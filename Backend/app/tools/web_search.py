from __future__ import annotations

from dataclasses import dataclass


TRUSTED_DOMAINS = {
    "gartner.com",
    "mckinsey.com",
    "ieee.org",
    "nature.com",
    "wipo.int",
    "techcrunch.com",
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


async def search_trusted_sources(query: str, max_results: int = 5) -> list[dict]:
    """
    Lightweight mock search tool.
    Replace this with Tavily/SerpAPI/Brave Search in production.
    """
    seed_results = [
        SearchDocument(
            title="Volumetric Display Market Forecast 2025",
            url="https://www.gartner.com/insights/emerging-tech/volumetric-display",
            snippet="B2B education, medical simulation, and defense prototyping are rising demand clusters.",
            trust_score=0.91,
        ),
        SearchDocument(
            title="Human-Computer Interaction for 3D Immersive Displays",
            url="https://ieeexplore.ieee.org/document/1234567",
            snippet="Interactive 3D rendering engagement is highest with context-aware narrative overlays.",
            trust_score=0.94,
        ),
        SearchDocument(
            title="Synthetic Biology Creatures in Educational Media",
            url="https://www.nature.com/articles/s41586-025-00001",
            snippet="Gamified ecological storytelling increases retention in STEM outreach formats.",
            trust_score=0.88,
        ),
        SearchDocument(
            title="IP Strategy for Real-Time 3D Display Pipelines",
            url="https://www.wipo.int/edocs/pubdocs/en/wipo_pub_9999.pdf",
            snippet="Defensive publication and utility patent pairing is common for rendering engines.",
            trust_score=0.89,
        ),
        SearchDocument(
            title="Go-to-Market Patterns for Hardware + Content Bundles",
            url="https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/hardware-content-bundles",
            snippet="Pilot programs with institutions reduce CAC and accelerate trust in novel hardware.",
            trust_score=0.9,
        ),
    ]

    filtered = [
        item
        for item in seed_results
        if any(domain in item.url.lower() for domain in TRUSTED_DOMAINS)
    ]
    contextual = [
        {
            **doc.as_dict(),
            "query_match": query[:100],
        }
        for doc in filtered[:max_results]
    ]
    return contextual

