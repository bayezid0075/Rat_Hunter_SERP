"""Schema.org microdata and pragmatic RDFa extraction.

The output is shaped like JSON-LD (``@type``, nested ``offers`` etc.) so the
same structured-data normalizers can be reused regardless of syntax.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

from app.extractors.base import clean_text


def _add(item: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    if key in item:
        existing = item[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            item[key] = [existing, value]
    else:
        item[key] = value


def _value_of(tag: Tag) -> Any:
    for attr in ("content", "datetime", "resource", "href", "src"):
        value = tag.get(attr)
        if value:
            return clean_text(str(value))
    text = clean_text(tag.get_text(" ", strip=True))
    return text


def _nearest_scope(tag: Tag) -> Tag | None:
    parent = tag.parent
    while isinstance(parent, Tag):
        if parent.has_attr("itemscope"):
            return parent
        parent = parent.parent
    return None


def _parse_microdata_scope(scope: Tag) -> dict[str, Any]:
    item: dict[str, Any] = {}
    item_type = scope.get("itemtype")
    if item_type:
        item["@type"] = str(item_type).split("/")[-1]
    item_id = scope.get("itemid")
    if item_id:
        item["@id"] = str(item_id)
    for element in scope.find_all(attrs={"itemprop": True}):  # type: ignore[call-overload]
        if not isinstance(element, Tag):
            continue
        if _nearest_scope(element) is not scope:
            continue
        properties = str(element.get("itemprop", "")).split()
        if not properties:
            continue
        value: Any
        if element.has_attr("itemscope"):
            value = _parse_microdata_scope(element)
        else:
            value = _value_of(element)
        for prop in properties:
            _add(item, prop, value)
    return item


def extract_microdata(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for scope in soup.find_all(attrs={"itemscope": True}):  # type: ignore[call-overload]
        if not isinstance(scope, Tag):
            continue
        if _nearest_scope(scope) is not None:
            continue
        item = _parse_microdata_scope(scope)
        if len(item) > 1:
            items.append(item)
    return items


def _nearest_typeof(tag: Tag) -> Tag | None:
    parent = tag.parent
    while isinstance(parent, Tag):
        if parent.has_attr("typeof"):
            return parent
        parent = parent.parent
    return None


def _rdfa_type(tag: Tag) -> str | None:
    raw = tag.get("typeof")
    if not raw:
        return None
    first = str(raw).split()[0]
    return first.split(":")[-1].split("/")[-1]


def extract_rdfa(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for scope in soup.find_all(attrs={"typeof": True}):  # type: ignore[call-overload]
        if not isinstance(scope, Tag):
            continue
        if _nearest_typeof(scope) is not None:
            continue
        item: dict[str, Any] = {}
        item_type = _rdfa_type(scope)
        if item_type:
            item["@type"] = item_type
        about = scope.get("about") or scope.get("resource")
        if about:
            item["@id"] = str(about)
        for element in scope.find_all(attrs={"property": True}):  # type: ignore[call-overload]
            if not isinstance(element, Tag):
                continue
            if _nearest_typeof(element) is not scope:
                continue
            props = str(element.get("property", "")).split()
            if element.has_attr("typeof"):
                value: Any = _parse_rdfa_scope(element)
            else:
                value = _value_of(element)
            for prop in props:
                _add(item, prop.split(":")[-1], value)
        if len(item) > 1:
            items.append(item)
    return items


def _parse_rdfa_scope(scope: Tag) -> dict[str, Any]:
    item: dict[str, Any] = {}
    item_type = _rdfa_type(scope)
    if item_type:
        item["@type"] = item_type
    for element in scope.find_all(attrs={"property": True}):  # type: ignore[call-overload]
        if not isinstance(element, Tag):
            continue
        if _nearest_typeof(element) is not scope:
            continue
        props = str(element.get("property", "")).split()
        value: Any = _parse_rdfa_scope(element) if element.has_attr("typeof") else _value_of(element)
        for prop in props:
            _add(item, prop.split(":")[-1], value)
    return item


def extract_microdata_and_rdfa(
    soup: BeautifulSoup,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return extract_microdata(soup), extract_rdfa(soup)
