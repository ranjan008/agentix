"""
Built-in Skill: web-search
Provides web search and page fetch capabilities via httpx.
"""
from __future__ import annotations

import json
import urllib.parse

import httpx

INSTRUCTIONS = """
## Web Search Skill
You can search the web and fetch web pages using these tools:
- `web_search`: Search the web for information. Use specific queries for best results.
- `web_fetch`: Fetch and read the content of a specific URL.

Always cite sources when using web search results.
""".strip()


def _web_search(query: str, num_results: int = 5) -> dict:
    """
    Search the web using the DuckDuckGo Instant Answer API (no API key required).
    For richer results, replace with Google Custom Search or Brave Search API.
    """
    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        data = resp.json()
        results = []
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"],
                "url": data.get("AbstractURL", ""),
            })
        for item in data.get("RelatedTopics", [])[:num_results]:
            if isinstance(item, dict) and item.get("Text"):
                results.append({
                    "title": item.get("Text", "")[:80],
                    "snippet": item.get("Text", ""),
                    "url": item.get("FirstURL", ""),
                })
        return {"query": query, "results": results[:num_results]}
    except Exception as e:
        return {"query": query, "results": [], "error": str(e)}


def _web_fetch(url: str, max_chars: int = 4000) -> dict:
    """Fetch raw text content from a URL."""
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        # Strip HTML tags naively
        import re
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"url": url, "content": text[:max_chars], "truncated": len(text) > max_chars}
    except Exception as e:
        return {"url": url, "content": "", "error": str(e)}


TOOLS = {
    "web_search": _web_search,
    "web_fetch": _web_fetch,
}

TOOL_SCHEMAS = [
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns a list of results with titles, snippets, and URLs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "num_results": {"type": "integer", "description": "Number of results to return (default 5)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": "Fetch the text content of a web page at a given URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_chars": {"type": "integer", "description": "Maximum characters to return (default 4000)", "default": 4000},
            },
            "required": ["url"],
        },
    },
]
