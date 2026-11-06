"""Seed source metadata and optionally import documents into the internal index.

Usage:
    python -m scripts.seed
    python -m scripts.seed --index-file documents.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.database.session import Database
from app.models.indexed_document import IndexedDocument
from app.models.search_source import SearchSource
from sqlalchemy import select

DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "name": "google",
        "adapter_type": "google",
        "description": "Google web results collected through a controlled browser session.",
        "config": {"requires_browser": True},
    },
    {
        "name": "daraz",
        "adapter_type": "direct_website",
        "description": "Daraz product search.",
        "config": {"base_url": "https://www.daraz.com.bd"},
    },
    {
        "name": "pickaboo",
        "adapter_type": "direct_website",
        "description": "Pickaboo product search.",
        "config": {"base_url": "https://www.pickaboo.com"},
    },
    {
        "name": "startech",
        "adapter_type": "direct_website",
        "description": "Star Tech product search.",
        "config": {"base_url": "https://www.startech.com.bd"},
    },
    {
        "name": "gadgetandgear",
        "adapter_type": "direct_website",
        "description": "Gadget & Gear product search.",
        "config": {"base_url": "https://gadgetandgear.com"},
    },
    {
        "name": "internal_index",
        "adapter_type": "custom_index",
        "description": "Operator-managed internal document index.",
        "config": {},
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed source metadata and indexed documents")
    parser.add_argument(
        "--index-file",
        type=Path,
        default=None,
        help="JSON array of documents: url, title, content, optional canonical_url, language, page_type",
    )
    return parser.parse_args()


async def seed(args: argparse.Namespace) -> dict[str, int]:
    settings = get_settings()
    db = Database(settings)
    source_count = 0
    document_count = 0
    try:
        async with db.session_factory() as session:
            for item in DEFAULT_SOURCES:
                existing = (
                    await session.execute(select(SearchSource).where(SearchSource.name == item["name"]))
                ).scalar_one_or_none()
                if existing is None:
                    session.add(SearchSource(**item))
                else:
                    existing.adapter_type = item["adapter_type"]
                    existing.description = item["description"]
                    existing.config = item["config"]
                    existing.enabled = True
                source_count += 1

            if args.index_file:
                documents = json.loads(args.index_file.read_text(encoding="utf-8"))
                if not isinstance(documents, list):
                    raise ValueError("--index-file must contain a JSON array")
                for raw in documents:
                    if not isinstance(raw, dict) or not raw.get("url") or not raw.get("title"):
                        raise ValueError("each document requires url and title")
                    url = str(raw["url"])
                    existing_doc = (
                        await session.execute(select(IndexedDocument).where(IndexedDocument.url == url))
                    ).scalar_one_or_none()
                    domain = urlsplit(url).hostname or ""
                    values = {
                        "canonical_url": raw.get("canonical_url"),
                        "domain": domain,
                        "title": str(raw["title"]),
                        "content": str(raw.get("content", "")),
                        "language": str(raw.get("language", "en")),
                        "page_type": str(raw.get("page_type", "webpage")),
                        "doc_metadata": raw.get("metadata") or {},
                    }
                    if existing_doc is None:
                        session.add(IndexedDocument(url=url, **values))
                    else:
                        for key, value in values.items():
                            setattr(existing_doc, key, value)
                    document_count += 1

            await session.commit()
    finally:
        await db.dispose()
    return {"sources": source_count, "documents": document_count}


def main() -> None:
    print(json.dumps(asyncio.run(seed(parse_args())), indent=2))


if __name__ == "__main__":
    main()
