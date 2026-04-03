from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from html import unescape
import json
import os
import re
from urllib.parse import quote_plus, unquote, urlparse
from xml.etree import ElementTree

import httpx


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

QUERY_TRANSLATIONS_KO_EN = {
    "시장": "market",
    "동향": "trends",
    "최신": "latest",
    "뉴스": "news",
    "헤드라인": "headlines",
    "오늘": "today",
    "금리": "interest rate",
    "비즈니스": "business",
    "모델": "model",
    "네이버": "naver",
}

GENERIC_RESEARCH_TOKENS = {
    "paper",
    "research",
    "study",
    "journal",
    "article",
    "논문",
    "학술",
    "리서치",
}

COMMAND_PHRASES = (
    "인터넷 검색",
    "웹 검색",
    "검색 시도",
    "검색해줘",
    "검색 해줘",
    "찾아줘",
    "찾아 봐",
    "조회해줘",
    "검색은",
    "검색어는",
    "검색 키워드는",
    "search for",
    "search",
)

WEATHER_KEYWORDS = (
    "날씨",
    "기온",
    "온도",
    "강수",
    "풍속",
    "weather",
    "temperature",
    "forecast",
    "humidity",
)

SEARCH_MODES = {"auto", "anthropic", "custom"}

KOREAN_CITY_ALIASES = {
    "서울": "Seoul",
    "서울시": "Seoul",
    "성남": "Seongnam-si",
    "성남시": "Seongnam-si",
    "부산": "Busan",
    "부산시": "Busan",
    "대구": "Daegu",
    "대구시": "Daegu",
    "인천": "Incheon",
    "인천시": "Incheon",
    "대전": "Daejeon",
    "대전시": "Daejeon",
    "광주": "Gwangju",
    "광주시": "Gwangju",
    "울산": "Ulsan",
    "울산시": "Ulsan",
    "수원": "Suwon-si",
    "수원시": "Suwon-si",
    "고양": "Goyang-si",
    "고양시": "Goyang-si",
    "용인": "Yongin-si",
    "용인시": "Yongin-si",
    "창원": "Changwon-si",
    "창원시": "Changwon-si",
    "청주": "Cheongju-si",
    "청주시": "Cheongju-si",
    "전주": "Jeonju-si",
    "전주시": "Jeonju-si",
    "제주": "Jeju-si",
    "제주시": "Jeju-si",
    "세종": "Sejong",
}


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
        return " ".join(filtered[:12])
    return query.strip()


def _extract_intent_query(raw_query: str) -> str:
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r"\s+", " ", query).strip()

    # Prefer explicit "검색은 ...", "검색어는 ..." style payload if present.
    marker_patterns = [
        r"(?:검색(?:어| 키워드)?(?:는|:)\s*)(?P<q>.+)$",
        r"(?:\bsearch\b(?:\s+for)?\s*[:\-]?\s*)(?P<q>.+)$",
    ]
    for pattern in marker_patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            candidate = (match.group("q") or "").strip(" .!?\t\"'")
            if len(candidate) >= 2:
                query = candidate
                break

    # If multiple clauses exist, pick the most content-heavy clause.
    clauses = [clause.strip() for clause in re.split(r"[.!?]+", query) if clause.strip()]
    if len(clauses) > 1:
        scored = sorted(clauses, key=lambda item: len(_tokenize(item)), reverse=True)
        if scored:
            query = scored[0]

    lowered = query.lower()
    for phrase in COMMAND_PHRASES:
        if phrase not in lowered:
            continue
        if re.fullmatch(r"[A-Za-z\s\-]+", phrase):
            pattern = rf"(?<![A-Za-z]){re.escape(phrase)}(?![A-Za-z])"
        else:
            pattern = re.escape(phrase)
        query = re.sub(pattern, " ", query, flags=re.IGNORECASE)
        lowered = query.lower()

    query = re.sub(r"\b(해줘|해주세요|해 봐|해봐|시도해줘|부탁해)\b", " ", query)
    query = re.sub(r"\s+", " ", query).strip(" .!?\t\"'")

    if len(_tokenize(query)) >= 1 and len(query) >= 2:
        return query
    return raw_query.strip()


def _expand_query_candidates(query: str) -> list[str]:
    raw = query.strip()
    base = _normalize_query(query)
    candidates = [raw]
    if base and base != raw:
        candidates.append(base)

    translated = query
    for ko, en in QUERY_TRANSLATIONS_KO_EN.items():
        translated = translated.replace(ko, en)
    translated = _normalize_query(translated)
    if translated and translated not in {base, raw}:
        candidates.append(translated)

    lowered = query.lower()
    if _looks_like_news_query(query) and "news" not in lowered:
        candidates.append(_normalize_query(f"{query} news"))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped[:3]


def _extract_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _normalize_for_dedupe(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def _trust_score_for(url: str) -> float:
    domain = _extract_domain(url)
    if not domain:
        return 0.35
    score = 0.56
    if url.startswith("https://"):
        score += 0.1
    if "." in domain:
        score += 0.08
    if domain.endswith(".gov") or domain.endswith(".edu") or domain.endswith(".ac.kr"):
        score += 0.12
    if "news" in domain:
        score += 0.05
    if "arxiv.org" in domain or "doi.org" in domain:
        score += 0.08
    return round(min(score, 0.94), 4)


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


def _freshness_score(pub_date: str | None) -> float:
    if not pub_date:
        return 0.0
    try:
        from datetime import datetime, timezone

        published = parsedate_to_datetime(pub_date)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)

        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - published.astimezone(timezone.utc)).total_seconds()
            / 3600.0,
        )
        if age_hours <= 24:
            return 0.2
        if age_hours <= 72:
            return 0.14
        if age_hours <= 168:
            return 0.08
        return 0.03
    except Exception:
        return 0.0


def _build_doc(
    *,
    query: str,
    title: str,
    url: str,
    snippet: str,
    source: str,
    published_at: str | None = None,
) -> dict:
    trust = _trust_score_for(url)
    relevance = _query_relevance(query, title, snippet, url)
    freshness = _freshness_score(published_at)
    score = (trust * 0.44) + (relevance * 0.46) + freshness
    return {
        "title": title,
        "url": url,
        "snippet": snippet,
        "trust_score": round(trust, 4),
        "query_relevance": round(relevance, 4),
        "ranking_score": round(min(1.0, score), 4),
        "query_match": query[:120],
        "source": source,
        "published_at": published_at,
    }


def _looks_like_news_query(query: str) -> bool:
    lowered = query.lower()
    return any(token in query for token in ("뉴스", "헤드라인", "오늘", "속보", "기사")) or any(
        token in lowered for token in ("news", "headline", "today", "breaking")
    )


def _looks_like_deep_research_query(query: str) -> bool:
    lowered = query.lower()
    markers = [
        "논문",
        "학술",
        "peer review",
        "paper",
        "arxiv",
        "crossref",
        "doi",
        "systematic review",
        "research",
        "journal",
    ]
    return any(marker in lowered or marker in query for marker in markers)


def _looks_like_weather_query(query: str) -> bool:
    lowered = query.lower()
    return any(keyword in query or keyword in lowered for keyword in WEATHER_KEYWORDS)


def _extract_weather_location(query: str) -> str:
    text = query.strip()
    if not text:
        return ""
    patterns = [
        r"(?:현재|오늘|지금|실시간)?\s*(?P<loc>[가-힣A-Za-z0-9\s]+?)\s*날씨",
        r"weather\s+in\s+(?P<loc>[A-Za-z0-9\s]+)",
        r"(?P<loc>[가-힣A-Za-z0-9\s]+)\s*(?:기온|온도|강수|풍속)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            loc = (match.group("loc") or "").strip()
            loc = re.sub(r"\b(현재|오늘|지금|실시간)\b", " ", loc, flags=re.IGNORECASE)
            loc = re.sub(r"\s+", " ", loc).strip()
            if len(loc) >= 2:
                return re.sub(r"\s+", " ", loc)

    stripped = re.sub(
        r"(현재|오늘|지금|실시간|날씨|기온|온도|강수|풍속|weather|temperature|forecast)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped


def _is_korean_text(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


def _get_search_mode() -> str:
    mode = (os.getenv("AGENTGCS_SEARCH_MODE") or "auto").strip().lower()
    if mode not in SEARCH_MODES:
        return "auto"
    return mode


def _needs_fresh_web_data(query: str) -> bool:
    lowered = query.lower()
    freshness_tokens = [
        "최신",
        "오늘",
        "현재",
        "방금",
        "실시간",
        "뉴스",
        "속보",
        "latest",
        "today",
        "current",
        "real-time",
        "realtime",
        "breaking",
        "news",
    ]
    return any(token in query or token in lowered for token in freshness_tokens)


def _weather_location_candidates(location: str) -> list[str]:
    loc = (location or "").strip()
    if not loc:
        return []
    candidates: list[str] = [loc]

    if _is_korean_text(loc):
        if not loc.endswith(("시", "군", "구", "동", "읍", "면")):
            candidates.append(f"{loc}시")
        if loc.endswith("시"):
            candidates.append(loc[:-1])
        alias = KOREAN_CITY_ALIASES.get(loc)
        if alias:
            candidates.append(alias)
        if loc.endswith("시"):
            alias = KOREAN_CITY_ALIASES.get(loc[:-1])
            if alias:
                candidates.append(alias)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item.strip())
    return deduped


def _pick_best_geocode_result(results: list[dict]) -> dict | None:
    if not results:
        return None

    return sorted(results, key=_geocode_result_score, reverse=True)[0]


def _geocode_result_score(item: dict) -> float:
    score = 0.0
    population = item.get("population")
    if isinstance(population, (int, float)):
        score += min(1000.0, float(population) / 1000.0)
    feature = str(item.get("feature_code") or "")
    if feature == "PPLC":
        score += 700.0
    elif feature.startswith("PPLA"):
        score += 500.0
    elif feature.startswith("PPL"):
        score += 300.0
    elif feature.startswith("ADM"):
        score += 120.0
    if str(item.get("country_code") or "").upper() == "KR":
        score += 200.0
    name = str(item.get("name") or "")
    admin1 = str(item.get("admin1") or "")
    if "특별시" in name or "특별시" in admin1:
        score += 80.0
    return score


def _needs_naver_news(query: str) -> bool:
    lowered = query.lower()
    has_naver = ("네이버" in query) or ("naver" in lowered)
    return has_naver and _looks_like_news_query(query)


def _extract_explicit_urls(query: str) -> list[str]:
    urls = re.findall(r"https?://[^\s)>\]}]+", query)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = _normalize_for_dedupe(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(url)
    return deduped[:3]


def _extract_json_array(text: str) -> list[dict]:
    stripped = text.strip()
    candidates: list[str] = []
    if stripped.startswith("[") and stripped.endswith("]"):
        candidates.append(stripped)

    code_block = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, flags=re.DOTALL)
    if code_block:
        candidates.append(code_block.group(1))

    array_match = re.search(r"\[(?:.|\n|\r)*\]", text)
    if array_match:
        candidates.append(array_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except Exception:
            continue
    return []


async def _fetch_url_preview(query: str, url: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code >= 400 or not response.text:
            return None
        html = response.text
        title_match = re.search(r"<title[^>]*>(?P<title>.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        title = _clean_text(title_match.group("title")) if title_match else url
        meta_match = re.search(
            r"""<meta[^>]+name=['\"]description['\"][^>]+content=['\"](?P<desc>[^'\"]+)['\"]""",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _clean_text(meta_match.group("desc")) if meta_match else ""
        return _build_doc(query=query, title=title, url=str(response.url), snippet=snippet, source="direct_url")
    except Exception:
        return None


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
    seen: set[str] = set()
    for idx, anchor in enumerate(anchor_iter):
        attrs = anchor.group("attrs") or ""
        if "result-link" not in attrs:
            continue
        href_match = re.search(r"""href=['"](?P<href>[^'"]+)['"]""", attrs, flags=re.IGNORECASE)
        if not href_match:
            continue

        final_url = unescape(href_match.group("href"))
        if not final_url.startswith("http"):
            continue
        dedupe = _normalize_for_dedupe(final_url)
        if dedupe in seen:
            continue
        seen.add(dedupe)

        title = _clean_text(anchor.group("title"))
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
        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=final_url,
                snippet=snippet,
                source="duckduckgo_lite",
            )
        )
        if len(docs) >= max_results * 2:
            break
    return docs[: max_results * 2]


async def _search_google_web(query: str, max_results: int) -> list[dict]:
    params = {
        "q": query,
        "hl": "ko",
        "gl": "KR",
        "num": str(max(10, max_results * 2)),
        "pws": "0",
    }
    url = "https://www.google.com/search"
    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400 or not response.text:
        return []

    html = response.text
    pattern = re.compile(
        r"<a[^>]+href=['\"]/url\?q=(?P<link>https?://[^'\"&]+)[^'\"]*['\"][^>]*>(?P<title>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    docs: list[dict] = []
    seen: set[str] = set()

    for match in pattern.finditer(html):
        link = unquote(unescape(match.group("link"))).strip()
        if not link.startswith("http"):
            continue
        domain = _extract_domain(link)
        if not domain or domain.endswith("google.com"):
            continue

        title = _clean_text(match.group("title"))
        if not title:
            continue

        key = _normalize_for_dedupe(link)
        if key in seen:
            continue
        seen.add(key)

        segment = html[match.end() : min(len(html), match.end() + 1600)]
        snippet = ""
        for snippet_pattern in (
            r"<div[^>]+class=['\"][^'\"]*VwiC3b[^'\"]*['\"][^>]*>(?P<snippet>.*?)</div>",
            r"<span[^>]+class=['\"][^'\"]*aCOpRe[^'\"]*['\"][^>]*>(?P<snippet>.*?)</span>",
            r"<div[^>]+class=['\"][^'\"]*s3v9rd[^'\"]*['\"][^>]*>(?P<snippet>.*?)</div>",
        ):
            snippet_match = re.search(snippet_pattern, segment, flags=re.IGNORECASE | re.DOTALL)
            if snippet_match:
                snippet = _clean_text(snippet_match.group("snippet"))
                break

        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=snippet,
                source="google_web",
            )
        )
        if len(docs) >= max_results * 2:
            break

    return docs[: max_results * 2]


async def _search_naver_web(query: str, max_results: int) -> list[dict]:
    url = "https://search.naver.com/search.naver"
    params = {
        "where": "nexearch",
        "sm": "top_hty",
        "fbm": "1",
        "ie": "utf8",
        "query": query,
    }
    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400 or not response.text:
        return []

    html = response.text
    docs: list[dict] = []
    seen: set[str] = set()

    pattern = re.compile(
        r"<a[^>]+class=['\"][^'\"]*link_tit[^'\"]*['\"][^>]+href=['\"](?P<link>https?://[^'\"]+)['\"][^>]*>(?P<title>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        link = unescape(match.group("link")).strip()
        title = _clean_text(match.group("title"))
        if not link.startswith("http") or not title:
            continue
        key = _normalize_for_dedupe(link)
        if key in seen:
            continue
        seen.add(key)

        segment = html[match.end() : min(len(html), match.end() + 1200)]
        snippet = ""
        snippet_match = re.search(
            r"<div[^>]+class=['\"][^'\"]*dsc_txt[^'\"]*['\"][^>]*>(?P<snippet>.*?)</div>",
            segment,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if snippet_match:
            snippet = _clean_text(snippet_match.group("snippet"))

        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=snippet,
                source="naver_web",
            )
        )
        if len(docs) >= max_results * 2:
            break

    return docs[: max_results * 2]


async def _search_naver_news(query: str, max_results: int) -> list[dict]:
    url = "https://search.naver.com/search.naver"
    params = {
        "where": "news",
        "sm": "tab_opt",
        "query": query,
    }
    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400 or not response.text:
        return []

    html = response.text
    docs: list[dict] = []
    seen: set[str] = set()

    pattern = re.compile(
        r"""<a[^>]+href=['"](?P<link>https?://(?:n\.news\.naver\.com|news\.naver\.com)/[^'"]+)['"][^>]*>(?P<title>.*?)</a>""",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        link = unescape(match.group("link")).strip()
        title = _clean_text(match.group("title"))
        if (
            not link.startswith("http")
            or not title
            or title in {"네이버뉴스", "Naver"}
            or len(title) < 6
        ):
            continue

        key = _normalize_for_dedupe(link)
        if key in seen:
            continue
        seen.add(key)

        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet="Naver news search result",
                source="naver_news",
            )
        )
        if len(docs) >= max_results * 2:
            break

    return docs[: max_results * 2]


async def _search_naver_headlines(query: str, max_results: int) -> list[dict]:
    url = "https://news.naver.com/main/ranking/popularDay.naver?mid=etc&sid1=111"
    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400 or not response.text:
        return []

    link_pattern = re.compile(
        r"""<a[^>]+href=['"](?P<href>https?://n\.news\.naver\.com/(?:mnews/)?article/[^'"]+)['"][^>]*>(?P<title>.*?)</a>""",
        flags=re.IGNORECASE | re.DOTALL,
    )
    docs: list[dict] = []
    seen: set[str] = set()
    for match in link_pattern.finditer(response.text):
        href = unescape(match.group("href")).strip()
        title = _clean_text(match.group("title"))
        if not href or not title or len(title) < 8:
            continue
        normalized = _normalize_for_dedupe(href)
        if normalized in seen:
            continue
        seen.add(normalized)
        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=href,
                snippet="네이버 뉴스 랭킹(일간) 헤드라인",
                source="naver_ranking",
            )
        )
        if len(docs) >= max_results:
            break
    return docs


async def _search_google_news_rss(query: str, max_results: int) -> list[dict]:
    rss_url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    )
    headers = {
        "User-Agent": REQUEST_HEADERS["User-Agent"],
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=12.0, headers=headers, follow_redirects=True) as client:
        response = await client.get(rss_url)
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
        pub_date = item.findtext("pubDate", default=None)
        if not title or not link.startswith("http"):
            continue
        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=snippet,
                source="google_news_rss",
                published_at=pub_date,
            )
        )
        if len(docs) >= max_results * 2:
            break
    return docs[: max_results * 2]


async def _search_bing_rss(query: str, max_results: int) -> list[dict]:
    url = "https://www.bing.com/search"
    params = {"q": query, "format": "rss"}
    headers = {
        "User-Agent": REQUEST_HEADERS["User-Agent"],
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=12.0, headers=headers, follow_redirects=True) as client:
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
        pub_date = item.findtext("pubDate", default=None)
        if not title or not link.startswith("http"):
            continue
        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=snippet,
                source="bing_rss",
                published_at=pub_date,
            )
        )
        if len(docs) >= max_results * 2:
            break
    return docs[: max_results * 2]


def _weather_code_label(code: int | None) -> str:
    labels = {
        0: "맑음",
        1: "대체로 맑음",
        2: "부분적으로 흐림",
        3: "흐림",
        45: "안개",
        48: "서리안개",
        51: "가벼운 이슬비",
        53: "이슬비",
        55: "강한 이슬비",
        61: "약한 비",
        63: "비",
        65: "강한 비",
        71: "약한 눈",
        73: "눈",
        75: "강한 눈",
        80: "소나기",
        81: "강한 소나기",
        82: "매우 강한 소나기",
        95: "뇌우",
        96: "우박 동반 뇌우",
        99: "강한 우박 동반 뇌우",
    }
    if code is None:
        return "정보 없음"
    return labels.get(code, f"날씨코드 {code}")


async def _search_open_meteo_current_weather(query: str, max_results: int) -> list[dict]:
    if not _looks_like_weather_query(query):
        return []

    location = _extract_weather_location(query)
    if not location:
        return []

    geocode_url = "https://geocoding-api.open-meteo.com/v1/search"
    weather_url = "https://api.open-meteo.com/v1/forecast"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
            picked_geo: dict | None = None
            picked_geo_score = -1.0
            for location_candidate in _weather_location_candidates(location):
                geo_params = {
                    "name": location_candidate,
                    "count": 8,
                    "language": "ko",
                    "format": "json",
                }
                if _is_korean_text(location_candidate):
                    geo_params["countryCode"] = "KR"

                geo_res = await client.get(geocode_url, params=geo_params)
                if geo_res.status_code >= 400:
                    continue
                geo_data = geo_res.json()
                results = geo_data.get("results") if isinstance(geo_data, dict) else None
                if (not isinstance(results, list) or not results) and "countryCode" in geo_params:
                    fallback_params = dict(geo_params)
                    fallback_params.pop("countryCode", None)
                    geo_res = await client.get(geocode_url, params=fallback_params)
                    if geo_res.status_code < 400:
                        geo_data = geo_res.json()
                        results = geo_data.get("results") if isinstance(geo_data, dict) else None
                if not isinstance(results, list) or not results:
                    continue
                candidate_best = _pick_best_geocode_result(results)
                if candidate_best:
                    score = _geocode_result_score(candidate_best)
                    if score > picked_geo_score:
                        picked_geo = candidate_best
                        picked_geo_score = score

            if not picked_geo:
                return []

            lat = picked_geo.get("latitude")
            lon = picked_geo.get("longitude")
            if lat is None or lon is None:
                return []

            weather_res = await client.get(
                weather_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "timezone": "auto",
                },
            )
            if weather_res.status_code >= 400:
                return []
            weather_data = weather_res.json()
            current = weather_data.get("current") if isinstance(weather_data, dict) else None
            if not isinstance(current, dict):
                return []

            name = str(picked_geo.get("name") or location)
            admin1 = str(picked_geo.get("admin1") or "").strip()
            country = str(picked_geo.get("country") or "").strip()
            area = ", ".join(part for part in (name, admin1, country) if part)

            temp = current.get("temperature_2m")
            apparent = current.get("apparent_temperature")
            humidity = current.get("relative_humidity_2m")
            wind = current.get("wind_speed_10m")
            weather_code = current.get("weather_code")
            weather_desc = _weather_code_label(int(weather_code)) if isinstance(weather_code, (int, float)) else "정보 없음"
            observed_at = str(current.get("time") or "")

            title = f"{area} 현재 날씨: {weather_desc}"
            snippet_parts = [
                f"기온 {temp}°C" if temp is not None else None,
                f"체감 {apparent}°C" if apparent is not None else None,
                f"습도 {humidity}%" if humidity is not None else None,
                f"풍속 {wind}km/h" if wind is not None else None,
                f"관측시각 {observed_at}" if observed_at else None,
            ]
            snippet = " / ".join(part for part in snippet_parts if part)
            source_url = (
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m&timezone=auto"
            )
            docs = [
                _build_doc(
                    query=query,
                    title=title,
                    url=source_url,
                    snippet=snippet,
                    source="open_meteo_current",
                    published_at=None,
                )
            ]
            if max_results > 1:
                docs.append(
                    _build_doc(
                        query=query,
                        title=f"{area} 날씨 검색 (네이버)",
                        url=f"https://search.naver.com/search.naver?where=nexearch&query={quote_plus(area + ' 날씨')}",
                        snippet="지역 날씨 관련 포털 검색 결과",
                        source="naver_weather_search",
                        published_at=None,
                    )
                )
            return docs[:max_results]
    except Exception:
        return []


async def _search_arxiv_api(query: str, max_results: int) -> list[dict]:
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query=all:{quote_plus(query)}&start=0&max_results={max(1, max_results * 2)}"
    )
    headers = {
        "User-Agent": "AgentGCS/1.0 (+https://localhost; research crawler)",
        "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        response = await client.get(url)
    if response.status_code >= 400 or not response.text:
        return []

    try:
        root = ElementTree.fromstring(response.text)
    except Exception:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    docs: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns))
        summary = _clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
        published = entry.findtext("atom:published", default=None, namespaces=ns)
        link = ""
        for link_node in entry.findall("atom:link", ns):
            href = link_node.attrib.get("href", "")
            rel = link_node.attrib.get("rel", "")
            if rel == "alternate" and href:
                link = href
                break
            if href and not link:
                link = href
        if not title or not link:
            continue
        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=summary,
                source="academic_arxiv",
                published_at=published,
            )
        )
        if len(docs) >= max_results * 2:
            break
    return docs[: max_results * 2]


async def _search_crossref_api(query: str, max_results: int) -> list[dict]:
    url = "https://api.crossref.org/works"
    params = {"query": query, "rows": max(1, max_results * 2)}
    headers = {
        "User-Agent": "AgentGCS/1.0 (+https://localhost; research crawler)",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        response = await client.get(url, params=params)
    if response.status_code >= 400 or not response.text:
        return []

    try:
        payload = response.json()
    except Exception:
        return []
    message = payload.get("message") if isinstance(payload, dict) else None
    items = message.get("items") if isinstance(message, dict) else None
    if not isinstance(items, list):
        return []

    docs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title_arr = item.get("title")
        title = _clean_text(title_arr[0]) if isinstance(title_arr, list) and title_arr else ""
        if not title:
            continue
        lowered_title = title.lower().strip()
        if lowered_title in {"paper", "article", "editorial"} or len(title) < 8:
            continue
        doi = item.get("DOI")
        if isinstance(doi, str) and doi:
            link = f"https://doi.org/{doi}"
        else:
            link = ""
            link_items = item.get("link")
            if isinstance(link_items, list) and link_items:
                first = link_items[0]
                if isinstance(first, dict):
                    link = str(first.get("URL") or "")
        if not link:
            continue

        abstract = _clean_text(str(item.get("abstract") or ""))
        if not abstract:
            container = item.get("container-title")
            venue = container[0] if isinstance(container, list) and container else ""
            abstract = f"Academic record from {venue}" if venue else "Academic record"
        published_at = None
        created = item.get("created")
        if isinstance(created, dict):
            created_at = created.get("date-time")
            if isinstance(created_at, str):
                published_at = created_at

        docs.append(
            _build_doc(
                query=query,
                title=title,
                url=link,
                snippet=abstract,
                source="academic_crossref",
                published_at=published_at,
            )
        )
        if len(docs) >= max_results * 2:
            break
    return docs[: max_results * 2]


async def _search_via_claude_web_tool(query: str, max_results: int) -> list[dict]:
    """
    Attempt Claude native web-search tool when gateway supports it.
    Silently returns [] when unavailable.
    """
    base_url = os.getenv("ANTHROPIC_BASE_URL") or "https://claude.1000.school"
    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("SCHOOL_API_TOKEN")
    if not base_url or not auth_token:
        return []

    model = os.getenv("CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
    prompt = (
        "웹 검색 도구를 사용해 아래 질의의 최신/관련 결과를 찾고 JSON 배열만 반환하라. "
        "각 항목 키는 title,url,snippet,published_at 를 사용하라.\n"
        f"질의: {query}\n"
        f"최대 결과 수: {max_results}"
    )

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model,
        "max_tokens": 1400,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=35.0) as client:
            response = await client.post("/v1/messages", headers=headers, json=payload)
        if response.status_code >= 400 or not response.text:
            return []

        body = response.json()
        content = body.get("content") if isinstance(body, dict) else None
        text_chunks: list[str] = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    text_chunks.append(block["text"])
        raw_text = "\n".join(text_chunks).strip()
        if not raw_text:
            return []

        parsed_rows = _extract_json_array(raw_text)
        if not parsed_rows:
            return []

        docs: list[dict] = []
        for item in parsed_rows:
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            published_at = item.get("published_at")
            if not title or not url.startswith("http"):
                continue
            docs.append(
                _build_doc(
                    query=query,
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="claude_web_tool",
                    published_at=published_at if isinstance(published_at, str) else None,
                )
            )
            if len(docs) >= max_results:
                break
        return docs
    except Exception:
        return []


def _dedupe_and_rank(query: str, docs: list[dict], max_results: int) -> list[dict]:
    if not docs:
        return []
    unique: list[dict] = []
    seen: set[str] = set()
    for doc in docs:
        url = str(doc.get("url") or "")
        if not url:
            continue
        key = _normalize_for_dedupe(url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)

    for doc in unique:
        if "ranking_score" not in doc:
            title = str(doc.get("title") or "")
            snippet = str(doc.get("snippet") or "")
            relevance = _query_relevance(query, title, snippet, str(doc.get("url") or ""))
            trust = _trust_score_for(str(doc.get("url") or ""))
            doc["query_relevance"] = round(relevance, 4)
            doc["trust_score"] = round(trust, 4)
            doc["ranking_score"] = round((trust * 0.44) + (relevance * 0.46), 4)

    lowered_query = query.lower()
    prefer_naver = ("네이버" in query) or ("naver" in lowered_query)
    prefer_academic = _looks_like_deep_research_query(query)
    for doc in unique:
        bonus = 0.0
        domain = _extract_domain(str(doc.get("url") or ""))
        if prefer_naver and "naver.com" in domain:
            bonus += 0.14
        if prefer_academic and ("arxiv.org" in domain or "doi.org" in domain):
            bonus += 0.08
        if bonus > 0:
            base_score = float(doc.get("ranking_score", 0.0))
            doc["ranking_score"] = round(min(1.0, base_score + bonus), 4)

    relevant_only = [row for row in unique if float(row.get("query_relevance", 0.0)) >= 0.1]
    if not relevant_only:
        return []

    if prefer_academic:
        academic_only = []
        for row in relevant_only:
            source = str(row.get("source") or "")
            domain = _extract_domain(str(row.get("url") or ""))
            if source.startswith("academic_") or "arxiv.org" in domain or "doi.org" in domain:
                if _passes_deep_research_specificity(query, row):
                    academic_only.append(row)
        if academic_only:
            relevant_only = academic_only

    relevant_only.sort(key=lambda row: float(row.get("ranking_score", 0.0)), reverse=True)
    return relevant_only[:max_results]


def _can_fetch_url(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    blocked_domains = (
        "api.open-meteo.com",
        "geocoding-api.open-meteo.com",
    )
    domain = _extract_domain(url)
    if any(domain.endswith(blocked) for blocked in blocked_domains):
        return False
    return True


def _passes_deep_research_specificity(query: str, row: dict) -> bool:
    meaningful_tokens = [
        token for token in _tokenize(query) if token not in GENERIC_RESEARCH_TOKENS and len(token) >= 3
    ]
    if not meaningful_tokens:
        return float(row.get("query_relevance", 0.0)) >= 0.2

    haystack = (
        f"{row.get('title', '')} {row.get('snippet', '')} {row.get('url', '')}"
    ).lower()
    matched = sum(1 for token in meaningful_tokens if token in haystack)
    coverage = matched / max(1, len(meaningful_tokens))
    return coverage >= 0.2 or float(row.get("query_relevance", 0.0)) >= 0.3


async def _enrich_results_with_fetch(query: str, docs: list[dict], max_fetch: int = 3) -> list[dict]:
    if not docs or max_fetch <= 0:
        return docs

    candidates = [doc for doc in docs if _can_fetch_url(str(doc.get("url") or ""))]
    if not candidates:
        return docs

    targets = candidates[:max_fetch]
    tasks = [
        asyncio.create_task(_fetch_url_preview(query, str(doc.get("url") or "")))
        for doc in targets
    ]
    fetch_results = await asyncio.gather(*tasks, return_exceptions=True)

    fetched_by_url: dict[str, dict] = {}
    for result in fetch_results:
        if isinstance(result, Exception) or not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if url:
            fetched_by_url[_normalize_for_dedupe(url)] = result

    if not fetched_by_url:
        return docs

    enriched: list[dict] = []
    for doc in docs:
        current = dict(doc)
        doc_url = str(current.get("url") or "")
        key = _normalize_for_dedupe(doc_url)
        fetched = fetched_by_url.get(key)
        if fetched:
            fetched_title = str(fetched.get("title") or "").strip()
            fetched_snippet = str(fetched.get("snippet") or "").strip()
            if fetched_title and len(fetched_title) >= 8 and len(str(current.get("title") or "")) < 12:
                current["title"] = fetched_title
            if fetched_snippet:
                current["snippet"] = fetched_snippet
            current["source"] = f"{current.get('source', 'search')}+fetch"
            current["fetched"] = True
        enriched.append(current)

    reranked = _dedupe_and_rank(query, enriched, max_results=len(docs))
    return reranked or enriched


async def search_trusted_sources(query: str, max_results: int = 5) -> list[dict]:
    """
    Real-time multi-source search.
    - Main pipeline: Google + Naver
    - Deep research: arXiv + Crossref (+ web search context)
    """
    original_query = (query or "").strip()
    if not original_query:
        return []
    query = _extract_intent_query(original_query)

    query_candidates = _expand_query_candidates(query)
    explicit_urls = _extract_explicit_urls(query)
    is_news = _looks_like_news_query(query)
    is_deep_research = _looks_like_deep_research_query(query)
    search_mode = _get_search_mode()
    needs_fresh = _needs_fresh_web_data(query)

    if _looks_like_weather_query(query):
        weather_docs = await _search_open_meteo_current_weather(query, max_results=max_results)
        if weather_docs:
            for item in weather_docs:
                item["query_match"] = query[:120]
                item["pipeline"] = "weather_direct"
            return weather_docs

    tasks: list[asyncio.Task] = []

    for url in explicit_urls:
        tasks.append(asyncio.create_task(_fetch_url_preview(query, url)))

    # A안: Anthropic 공식 web-search tool 경로
    if search_mode in {"auto", "anthropic"}:
        # 최신성이 중요한 질의에서는 우선 시도
        claude_results_limit = max_results * (3 if needs_fresh else 2)
        tasks.append(
            asyncio.create_task(_search_via_claude_web_tool(query, max_results=claude_results_limit))
        )

    # B안: 커스텀 검색 API/크롤링 경로
    if search_mode in {"auto", "custom"}:
        for candidate in query_candidates:
            if is_deep_research:
                tasks.append(asyncio.create_task(_search_arxiv_api(candidate, max_results=max_results * 2)))
                tasks.append(asyncio.create_task(_search_crossref_api(candidate, max_results=max_results * 2)))
                tasks.append(
                    asyncio.create_task(
                        _search_google_web(f"{candidate} research paper arxiv", max_results=max_results * 2)
                    )
                )
                tasks.append(
                    asyncio.create_task(
                        _search_bing_rss(f"{candidate} site:arxiv.org OR doi.org", max_results=max_results * 2)
                    )
                )
            else:
                tasks.append(asyncio.create_task(_search_google_web(candidate, max_results=max_results * 2)))
                tasks.append(asyncio.create_task(_search_naver_web(candidate, max_results=max_results * 2)))
                tasks.append(asyncio.create_task(_search_bing_rss(candidate, max_results=max_results * 2)))
                tasks.append(
                    asyncio.create_task(_search_google_news_rss(candidate, max_results=max_results * 2))
                )

                if is_news:
                    tasks.append(asyncio.create_task(_search_naver_news(candidate, max_results=max_results * 2)))

    if search_mode in {"auto", "custom"} and _needs_naver_news(query):
        tasks.append(asyncio.create_task(_search_naver_headlines(query, max_results=max_results * 2)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged: list[dict] = []
    for result in results:
        if isinstance(result, Exception):
            continue
        if isinstance(result, dict):
            merged.append(result)
            continue
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    merged.append(item)

    ranked = _dedupe_and_rank(query, merged, max_results=max_results)

    if not ranked and search_mode in {"auto", "custom"} and is_news:
        # last resort for Korean headline queries
        fallback_naver = await _search_naver_headlines(query, max_results=max_results)
        ranked = _dedupe_and_rank(query, fallback_naver, max_results=max_results)

    # Search -> Fetch 분리: 상위 URL을 실제로 읽어서(snippet/title) 보강 후 재랭킹
    ranked = await _enrich_results_with_fetch(query, ranked, max_fetch=min(3, max_results))
    ranked = _dedupe_and_rank(query, ranked, max_results=max_results)

    for item in ranked:
        item["query_match"] = query[:120]
        item["pipeline"] = f"{search_mode}:search_fetch"
    return ranked
