from __future__ import annotations

import asyncio
import html
import re
import warnings
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    from ddgs import DDGS  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    try:
        from duckduckgo_search import DDGS  # type: ignore[assignment]
        warnings.filterwarnings(
            "ignore",
            message=r"This package \(`duckduckgo_search`\) has been renamed to `ddgs`!.*",
            category=RuntimeWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*duckduckgo_search.*renamed to `ddgs`.*",
            category=RuntimeWarning,
        )
    except ImportError:  # pragma: no cover
        DDGS = None

QUERY_TRANSLATIONS = {
    "가천대": "gachon university",
    "가천대학교": "gachon university",
    "공지사항": "notice",
    "최신": "latest",
    "홈페이지": "homepage",
    "날씨": "weather",
    "성남": "seongnam",
    "서울": "seoul",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-z가-힣]{2,}", (text or "").lower())


def _relevance(query: str, title: str, snippet: str, url: str) -> float:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        return 0.0
    haystack = f"{title} {snippet} {url}".lower()
    hit = sum(1 for token in q_tokens if token in haystack)
    return min(1.0, hit / max(1, len(q_tokens)))


def _trust(url: str) -> float:
    if url.startswith("https://"):
        base = 0.72
    else:
        base = 0.6
    if any(url.endswith(suffix) for suffix in (".gov", ".edu", ".ac.kr")):
        base += 0.12
    if any(token in url for token in ("arxiv.org", "doi.org")):
        base += 0.08
    return round(min(0.95, base), 4)


def _normalize_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"(?:인터넷|웹)?\s*검색(?:\s*시도)?(?:해줘|해주세요|은|어는|키워드는)?[:\s]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    return text or query.strip()


def _translate_query(query: str) -> str:
    translated = query
    for ko, en in QUERY_TRANSLATIONS.items():
        translated = translated.replace(ko, en)
    translated = re.sub(r"\s+", " ", translated).strip()
    return translated


async def _ddgs_text(query: str, max_results: int) -> list[dict[str, Any]]:
    if DDGS is None:
        raise RuntimeError("duckduckgo-search package is not installed.")

    def _run() -> list[dict[str, Any]]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with DDGS() as ddgs:
                rows = list(ddgs.text(query, max_results=max_results))
        return rows

    return await asyncio.to_thread(_run)


def _to_doc(
    query: str,
    row: dict[str, Any],
    source: str,
    *,
    score_query: str | None = None,
) -> dict[str, Any] | None:
    title = str(row.get("title") or "").strip()
    url = str(row.get("href") or row.get("url") or "").strip()
    snippet = str(row.get("body") or row.get("snippet") or "").strip()
    if not title or not url.startswith("http"):
        return None
    relevance = _relevance(score_query or query, title, snippet, url)
    trust = _trust(url)
    score = (relevance * 0.56) + (trust * 0.44)
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "trust_score": trust,
        "query_relevance": round(relevance, 4),
        "ranking_score": round(min(1.0, score), 4),
        "query_match": query[:120],
        "source": source,
        "published_at": None,
    }


def _dedupe_rank(query: str, docs: list[dict[str, Any]], max_results: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for doc in docs:
        url = str(doc.get("url") or "")
        key = re.sub(r"^https?://(?:www\.)?", "", url).rstrip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(doc)

    filtered = [doc for doc in unique if float(doc.get("query_relevance", 0.0)) >= 0.02]
    if not filtered:
        return []
    ranked_pool = filtered
    ranked_pool.sort(key=lambda row: float(row.get("ranking_score", 0.0)), reverse=True)
    return ranked_pool[:max_results]


async def _bing_rss_text(query: str, max_results: int) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    params = {"q": query, "format": "rss"}
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get("https://www.bing.com/search", params=params, headers=headers)
        if resp.status_code >= 400 or not resp.text.strip():
            return []
        root = ET.fromstring(resp.text)
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        if not title or not link.startswith("http"):
            continue
        out.append(
            {
                "title": html.unescape(title),
                "href": link,
                "body": html.unescape(description),
            }
        )
        if len(out) >= max_results:
            break
    return out


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


async def search_trusted_sources(query: str, max_results: int = 5, intent: str = "auto") -> list[dict]:
    q = _normalize_query(query)
    if not q:
        return []
    max_results = max(1, min(max_results, 12))

    candidate_queries = [q]
    translated = _translate_query(q)
    if translated and translated != q:
        candidate_queries.append(translated)
    lowered = q.lower()
    is_weather_query = any(token in lowered for token in ("날씨", "weather", "forecast", "기온", "temperature"))
    if intent == "academic":
        candidate_queries.append(f"{q} research paper arxiv doi")
    elif intent == "general":
        candidate_queries.append(f"{translated or q} latest")
    else:
        if any(token in lowered for token in ("논문", "학술", "research", "paper", "doi", "arxiv")):
            candidate_queries.append(f"{q} research paper arxiv doi")
        elif is_weather_query:
            candidate_queries.append(f"{translated or q} weather forecast")
        else:
            candidate_queries.append(f"{translated or q} latest")

    docs: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidate_queries):
        try:
            rows = await _ddgs_text(candidate, max_results=max_results * 2)
        except Exception:
            continue
        source = "ddgs_text" if idx == 0 else "ddgs_text_expanded"
        for row in rows:
            if not isinstance(row, dict):
                continue
            parsed = _to_doc(q, row, source=source, score_query=candidate)
            if parsed:
                docs.append(parsed)

    # DDGS 결과가 비거나 지나치게 빈약하면 Bing RSS로 보완한다.
    if len(docs) < max(2, min(max_results, 3)):
        for candidate in candidate_queries[:2]:
            rows = await _bing_rss_text(candidate, max_results=max_results * 2)
            for row in rows:
                parsed = _to_doc(q, row, source="bing_rss", score_query=candidate)
                if parsed:
                    docs.append(parsed)

    ranked = _dedupe_rank(q, docs, max_results=max_results)
    for row in ranked:
        row["pipeline"] = "ddgs_bing_fallback"
        host = _host(str(row.get("url") or ""))
        if host:
            row["host"] = host
    return ranked


async def search_general_sources(query: str, max_results: int = 5) -> list[dict]:
    return await search_trusted_sources(query=query, max_results=max_results, intent="general")


async def search_academic_sources(query: str, max_results: int = 5) -> list[dict]:
    return await search_trusted_sources(query=query, max_results=max_results, intent="academic")
