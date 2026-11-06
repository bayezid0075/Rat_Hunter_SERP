import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE_URL = (__ENV.BASE_URL || "http://localhost:8000").replace(/\/$/, "");
const API_KEY = __ENV.API_KEY || "";
const WORKLOAD = __ENV.WORKLOAD || "mixed";
const RATE = Number(__ENV.RATE || 10);
const DURATION = __ENV.DURATION || "30m";
const SEARCH_QUERY = __ENV.SEARCH_QUERY || "wireless headphones";
const EXTRACT_URL = __ENV.EXTRACT_URL || "https://example.com/";
const P95_MS = Number(__ENV.P95_MS || 20000);
const MAX_ERROR_RATE = Number(__ENV.MAX_ERROR_RATE || 0.05);

const cacheHits = new Counter("cache_hits");
const cacheMisses = new Counter("cache_misses");
const searchLatency = new Trend("search_latency", true);
const extractionLatency = new Trend("extraction_latency", true);
const workflowFailures = new Rate("workflow_failures");

export const options = {
  scenarios: {
    sustained: {
      executor: "constant-arrival-rate",
      rate: RATE,
      timeUnit: "1s",
      duration: DURATION,
      preAllocatedVUs: Math.max(10, RATE * 2),
      maxVUs: Number(__ENV.MAX_VUS || 200),
    },
  },
  thresholds: {
    http_req_failed: [`rate<${MAX_ERROR_RATE}`],
    http_req_duration: [`p(95)<${P95_MS}`],
    workflow_failures: [`rate<${MAX_ERROR_RATE}`],
  },
};

const headers = {
  "Content-Type": "application/json",
  "X-API-Key": API_KEY,
  "User-Agent": "universal-serp-k6/1.0",
};

function post(path, body, tags) {
  return http.post(`${BASE_URL}${path}`, JSON.stringify(body), { headers, tags });
}

function parse(response) {
  try {
    return JSON.parse(response.body);
  } catch {
    return null;
  }
}

function recordCache(payload) {
  if (payload?.metadata?.cached === true) {
    cacheHits.add(1);
  } else if (payload?.metadata) {
    cacheMisses.add(1);
  }
}

function search({ live = false, product = false } = {}) {
  const query = live ? `${SEARCH_QUERY} ${__VU}-${__ITER}` : SEARCH_QUERY;
  const body = {
    query,
    limit: Number(__ENV.LIMIT || 10),
    mode: __ENV.MODE || "quick",
    search_type: product ? "products" : "web",
    use_cache: !live,
    force_refresh: live,
    country: __ENV.COUNTRY || "US",
    language: __ENV.LANGUAGE || "en",
  };
  const path = product ? "/v1/products/search" : "/v1/search";
  const response = post(path, body, { workload: product ? "product_search" : "search" });
  const payload = parse(response);
  recordCache(payload);
  searchLatency.add(response.timings.duration);
  return check(response, {
    "search returned 200": (r) => r.status === 200,
    "search response is successful": () => payload?.success === true,
    "search returned results": () => Array.isArray(payload?.results),
  });
}

function asyncSearch() {
  const response = post(
    "/v1/search",
    {
      query: `${SEARCH_QUERY} ${__VU}-${__ITER}`,
      limit: Number(__ENV.LIMIT || 10),
      mode: __ENV.MODE || "quick",
      async: true,
      country: __ENV.COUNTRY || "US",
      language: __ENV.LANGUAGE || "en",
    },
    { workload: "async_search" },
  );
  const accepted = parse(response);
  const acceptedOk = check(response, {
    "async search returned 202": (r) => r.status === 202,
    "async search returned job id": () => Boolean(accepted?.job_id),
  });
  if (!acceptedOk || !accepted?.job_id) {
    return false;
  }
  for (let attempt = 0; attempt < Number(__ENV.POLL_ATTEMPTS || 30); attempt += 1) {
    sleep(Number(__ENV.POLL_INTERVAL || 0.5));
    const statusResponse = http.get(`${BASE_URL}/v1/jobs/${accepted.job_id}`, {
      headers,
      tags: { workload: "job_poll" },
    });
    const job = parse(statusResponse);
    if (job?.status === "succeeded") {
      return true;
    }
    if (job?.status === "failed") {
      return false;
    }
  }
  return false;
}

function extract({ fresh = false } = {}) {
  const body = {
    url: EXTRACT_URL,
    include_text: true,
    include_media: true,
    allow_browser: __ENV.ALLOW_BROWSER === "true",
    use_cache: !fresh,
    force_refresh: fresh,
  };
  const response = post("/v1/extract", body, { workload: "extraction" });
  const payload = parse(response);
  extractionLatency.add(response.timings.duration);
  return check(response, {
    "extraction returned 200": (r) => r.status === 200,
    "extraction response is successful": () => payload?.success === true,
    "extraction contains page data": () => Boolean(payload?.data?.page),
  });
}

export function setup() {
  if (!API_KEY) {
    throw new Error("API_KEY is required; create one with scripts/create_api_key.py");
  }
  const response = http.get(`${BASE_URL}/v1/health`, {
    headers: { "User-Agent": "universal-serp-k6/1.0" },
  });
  check(response, { "health check passed": (r) => r.status === 200 });
  return { startedAt: new Date().toISOString() };
}

export default function () {
  let ok = false;
  if (WORKLOAD === "search") {
    ok = search();
  } else if (WORKLOAD === "search-cache") {
    ok = search();
  } else if (WORKLOAD === "search-live") {
    ok = search({ live: true });
  } else if (WORKLOAD === "product") {
    ok = search({ product: true });
  } else if (WORKLOAD === "extract") {
    ok = extract();
  } else if (WORKLOAD === "extract-live") {
    ok = extract({ fresh: true });
  } else if (WORKLOAD === "async-search") {
    ok = asyncSearch();
  } else {
    const choice = __ITER % 4;
    if (choice === 0) {
      ok = search();
    } else if (choice === 1) {
      ok = search({ product: true });
    } else if (choice === 2) {
      ok = extract();
    } else {
      ok = search({ live: true });
    }
  }
  workflowFailures.add(!ok);
}
