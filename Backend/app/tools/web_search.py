from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from xml.etree import ElementTree

import httpx


TRUSTED_DOMAINS = {
    "wikipedia.org",
    "openai.com",
    "help.openai.com",
    "gartner.com",
    "mckinsey.com",
    "ieee.org",
    "nature.com",
    "wipo.int",
    "techcrunch.com",
    "arxiv.org",
    "stanford.edu",
    "mit.edu",
    "who.int",
    "oecd.org",
    "gov",
    "edu",
}

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

QUERY_STOPWORDS = {
    "search",
    "latest",
    "with",
    "source",
    "sources",
    "please",
    "show",
    "tell",
    "about",
    "what",
    "how",
    "the",
    "and",
    "for",
    "from",
    "that",
    "this",
    "검색",
    "최신",
    "출처",
    "인터넷",
    "웹",
    "결과",
    "알려줘",
    "해줘",
    "해주세요",
}


@dataclass(slots=True)
class SearchDocument:
    title: str
    url: str
    snippet: str
    trust_score: float

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "trust_score": self.trust_score,
        }


def _extract_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _resolve_duckduckgo_redirect(url: str) -> str:
    if url.startswith("//"):
        url = f"https:{url}"
    if "duckduckgo.com/l/?" not in url:
        return url
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        uddg = qs.get("uddg", [url])[0]
        return unquote(uddg)
    except Exception:
        return url


def _trust_score_for(url: str) -> float:
    domain = _extract_domain(url)
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 0.94
    if any(domain.endswith(known) for known in TRUSTED_DOMAINS):
        return 0.9
    if domain:
        return 0.65
    return 0.4


def _seed_results(query: str, max_results: int) -> list[dict]:
    seed_results: list[SearchDocument] = [
        SearchDocument(
            title="Live web search fallback: DuckDuckGo query link",
            url=f"https://duckduckgo.com/?q={quote_plus(query)}",
            snippet=(
                "실시간 검색 결과를 가져오지 못해 직접 검색 링크를 제공합니다. "
                "네트워크/차단 정책을 확인하세요."
            ),
            trust_score=0.52,
        ),
        SearchDocument(
            title="Live web search fallback: Wikipedia query link",
            url=f"https://en.wikipedia.org/w/index.php?search={quote_plus(query)}",
            snippet="Wikipedia 검색 링크를 통해 동일 질의를 바로 확인할 수 있습니다.",
            trust_score=0.5,
        ),
        SearchDocument(
            title="Search diagnostics",
            url="https://duckduckgo.com/duckduckgo-help-pages/results/",
            snippet=f"query={query[:120]} / mode=fallback",
            trust_score=0.45,
        ),
    ]
    return [
        {**doc.as_dict(), "query_match": query[:120], "source": "diagnostic_fallback"}
        for doc in seed_results[:max_results]
    ]


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", text or "")
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()) if len(token) >= 2]


def _normalize_query(query: str) -> str:
    tokens = _tokenize(query)
    filtered = [token for token in tokens if token not in QUERY_STOPWORDS]
    if len(filtered) >= 2:
        return " ".join(filtered[:10])
    return query.strip()


def _query_relevance(query: str, title: str, snippet: str, url: str) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0
    haystack = f"{title} {snippet} {url}".lower()
    matched = sum(1 for token in query_tokens if token in haystack)
    coverage = matched / max(1, len(query_tokens))
    strong_tokens = [token for token in query_tokens if len(token) >= 4]
    if strong_tokens:
        strong_matched = sum(1 for token in strong_tokens if token in haystack)
        strong_ratio = strong_matched / len(strong_tokens)
    else:
        strong_ratio = coverage
    phrase_bonus = 0.18 if query.strip().lower() in haystack else 0.0
    return min(1.0, (coverage * 0.62) + (strong_ratio * 0.38) + phrase_bonus)


async def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    q = quote_plus(query.strip())
    url = f"https://lite.duckduckgo.com/lite/?q={q}&kl=kr-ko"

    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code != 200 or not response.text:
        return []

    html = response.text
    anchor_iter = list(
        re.finditer(r"<a(?P<attrs>[^>]*)>(?P<title>.*?)</a>", html, flags=re.IGNORECASE | re.DOTALL)
    )

    docs: list[dict] = []
    for idx, anchor in enumerate(anchor_iter):
        attrs = anchor.group("attrs") or ""
        if "result-link" not in attrs:
            continue

        href_match = re.search(r"""href=['"](?P<href>[^'"]+)['"]""", attrs, flags=re.IGNORECASE)
        if not href_match:
            continue

        raw_href = unescape(href_match.group("href"))
        raw_title = anchor.group("title")
        title = _clean_text(raw_title)
        if not title:
            continue

        next_start = len(html)
        for following in anchor_iter[idx + 1 :]:
            if "result-link" in (following.group("attrs") or ""):
                next_start = following.start()
                break
        segment = html[anchor.end() : next_start]
        snippet_match = re.search(
            r"""<td[^>]*class=['"][^'"]*result-snippet[^'"]*['"][^>]*>(?P<snippet>.*?)</td>""",
            segment,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _clean_text(snippet_match.group("snippet")) if snippet_match else ""

        final_url = _resolve_duckduckgo_redirect(raw_href)
        if not final_url.startswith("http"):
            continue
        trust = _trust_score_for(final_url)
        relevance = _query_relevance(query, title, snippet, final_url)
        ranking_score = (trust * 0.58) + (relevance * 0.42)

        docs.append(
            {
                "title": title,
                "url": final_url,
                "snippet": snippet,
                "trust_score": trust,
                "query_relevance": round(relevance, 4),
                "ranking_score": round(ranking_score, 4),
                "query_match": query[:120],
                "source": "duckduckgo_lite",
            }
        )
        if len(docs) >= max_results * 2:
            break

    docs.sort(key=lambda item: float(item.get("ranking_score", 0.0)), reverse=True)
    return docs[:max_results]


async def _search_wikipedia(query: str, max_results: int) -> list[dict]:
    endpoint = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": max_results,
        "namespace": 0,
        "format": "json",
    }
    headers = {
        "User-Agent": "AgentGCS/1.0 (+https://localhost)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        response = await client.get(endpoint, params=params)
    if response.status_code >= 400 or not response.text:
        return []
    data = response.json()
    if not isinstance(data, list) or len(data) < 4:
        return []
    titles = data[1] if isinstance(data[1], list) else []
    snippets = data[2] if isinstance(data[2], list) else []
    urls = data[3] if isinstance(data[3], list) else []
    docs: list[dict] = []
    for idx, title in enumerate(titles):
        if not isinstance(title, str):
            continue
        url = urls[idx] if idx < len(urls) and isinstance(urls[idx], str) else ""
        snippet = snippets[idx] if idx < len(snippets) and isinstance(snippets[idx], str) else ""
        if not url:
            continue
        relevance = _query_relevance(query, title, snippet, url)
        docs.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "trust_score": 0.76,
                "query_relevance": round(relevance, 4),
                "ranking_score": round((0.76 * 0.58) + (relevance * 0.42), 4),
                "query_match": query[:120],
                "source": "wikipedia",
            }
        )
    docs.sort(key=lambda item: float(item.get("ranking_score", 0.0)), reverse=True)
    return docs[:max_results]


async def _search_bing_rss(query: str, max_results: int) -> list[dict]:
    url = "https://www.bing.com/search"
    params = {"q": query, "format": "rss"}
    headers = {
        "User-Agent": REQUEST_HEADERS["User-Agent"],
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }

    async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400 or not response.text:
        return []

    try:
        root = ElementTree.fromstring(response.text)
    except Exception:
        return []

    docs: list[dict] = []
    for item in root.findall("./channel/item"):
        title = _clean_text(item.findtext("title", default=""))
        link = (item.findtext("link", default="") or "").strip()
        snippet = _clean_text(item.findtext("description", default=""))
        if not title or not link.startswith("http"):
            continue
        trust = _trust_score_for(link)
        relevance = _query_relevance(query, title, snippet, link)
        docs.append(
            {
                "title": title,
                "url": link,
                "snippet": snippet,
                "trust_score": trust,
                "query_relevance": round(relevance, 4),
                "ranking_score": round((trust * 0.58) + (relevance * 0.42), 4),
                "query_match": query[:120],
                "source": "bing_rss",
            }
        )
        if len(docs) >= max_results * 2:
            break
    docs.sort(key=lambda item: float(item.get("ranking_score", 0.0)), reverse=True)
    return docs[:max_results]


async def search_trusted_sources(query: str, max_results: int = 5) -> list[dict]:
    """
    Lightweight web search tool with live DuckDuckGo fallback + trusted-domain scoring.
    """
    query = (query or "").strip()
    if not query:
        return _seed_results("empty-query", max_results)
    search_query = _normalize_query(query)

    def _set_query_match(rows: list[dict]) -> list[dict]:
        for row in rows:
            row["query_match"] = query[:120]
        return rows

    try:
        live = await _search_duckduckgo(search_query, max_results=max_results)
        if live and any(float(item.get("query_relevance", 0.0)) >= 0.3 for item in live):
            return _set_query_match(live)
    except Exception:
        live = []

    bing: list[dict] = []
    try:
        bing = await _search_bing_rss(search_query, max_results=max_results)
        if bing and any(float(item.get("query_relevance", 0.0)) >= 0.3 for item in bing):
            merged = [*live, *bing]
            merged.sort(key=lambda item: float(item.get("ranking_score", 0.0)), reverse=True)
            unique: list[dict] = []
            seen_urls: set[str] = set()
            for item in merged:
                url = str(item.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                unique.append(item)
                if len(unique) >= max_results:
                    break
            if unique:
                return _set_query_match(unique)
    except Exception:
        bing = []

    try:
        wiki = await _search_wikipedia(search_query, max_results=max_results)
        if wiki:
            merged = [*live, *bing, *wiki]
            merged.sort(key=lambda item: float(item.get("ranking_score", 0.0)), reverse=True)
            unique: list[dict] = []
            seen_urls: set[str] = set()
            for item in merged:
                url = str(item.get("url") or "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                unique.append(item)
                if len(unique) >= max_results:
                    break
            if unique:
                return _set_query_match(unique)
    except Exception:
        wiki = []

    if live and any(float(item.get("query_relevance", 0.0)) >= 0.25 for item in live):
        # relevance score가 낮더라도 실시간 결과를 우선 반환
        for item in live:
            item["source"] = "duckduckgo_low_relevance"
        return _set_query_match(live[:max_results])
    if bing and any(float(item.get("query_relevance", 0.0)) >= 0.25 for item in bing):
        for item in bing:
            item["source"] = "bing_low_relevance"
        return _set_query_match(bing[:max_results])

    seeded = _seed_results(query, max_results)
    for item in seeded:
        item["source"] = "diagnostic_fallback"
    return _set_query_match(seeded)
