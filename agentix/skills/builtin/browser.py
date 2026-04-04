"""
Browser skill — Playwright-powered headless browser tools.

Provides the agent with a persistent browser session for navigating the web,
scraping structured content, filling forms, and interacting with authenticated
portals such as LinkedIn.

Tools exposed:
  browser_navigate        — go to a URL, return page title + text
  browser_get_text        — extract visible text (optionally scoped to a CSS selector)
  browser_get_links       — list all anchor hrefs matching an optional selector
  browser_click           — click an element by CSS selector
  browser_fill            — fill an input field
  browser_scroll          — scroll the page (useful for infinite-scroll feeds)
  browser_screenshot      — capture a base64 PNG screenshot
  browser_wait            — wait for a CSS selector to appear (up to timeout_sec)
  browser_evaluate        — run arbitrary JavaScript in the page
  browser_close           — close the session (frees memory)

LinkedIn helpers (thin wrappers that navigate to well-known URLs):
  linkedin_get_profile    — fetch a person's LinkedIn profile by URL or name
  linkedin_search_people  — search People with filters
  linkedin_get_feed_posts — get recent posts/activity for a person
  linkedin_send_message   — open messaging and send a connection request or InMail

Setup:
  pip install playwright
  playwright install chromium

Headless mode is on by default. Set BROWSER_HEADLESS=false to watch it work
(useful for debugging LinkedIn auth flows).

Authentication:
  LinkedIn requires a logged-in session. Store cookies by running the agent
  once with BROWSER_HEADLESS=false, logging in manually, then:
    agentix browser save-cookies --output data/linkedin_cookies.json
  Point the skill at it with:
    skills:
      browser:
        cookies_file: data/linkedin_cookies.json
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from agentix.agent_runtime.tool_executor import tool

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Browser session (one per agent process, lazy-initialised)
# ---------------------------------------------------------------------------

_browser: Any = None
_page: Any = None
_playwright: Any = None


def _get_page(headless: bool = True, cookies_file: str | None = None) -> Any:
    global _browser, _page, _playwright
    if _page is not None:
        return _page
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        )
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    context = _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
    )
    # Load saved cookies if available
    _cookies_file = cookies_file or os.environ.get("BROWSER_COOKIES_FILE")
    if _cookies_file and Path(_cookies_file).exists():
        with open(_cookies_file) as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        log.info("Browser: loaded %d cookies from %s", len(cookies), _cookies_file)
    _page = context.new_page()
    return _page


def _headless() -> bool:
    return os.environ.get("BROWSER_HEADLESS", "true").lower() not in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Core browser tools
# ---------------------------------------------------------------------------

@tool(
    name="browser_navigate",
    description=(
        "Navigate the browser to a URL and return the page title and visible text. "
        "Use this to open any web page before calling other browser tools."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to navigate to"},
            "wait_for": {
                "type": "string",
                "description": "Optional CSS selector to wait for before returning",
            },
        },
        "required": ["url"],
    },
)
def browser_navigate(url: str, wait_for: str | None = None) -> dict:
    page = _get_page(headless=_headless())
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    if wait_for:
        page.wait_for_selector(wait_for, timeout=10_000)
    title = page.title()
    text = page.inner_text("body")[:8000]  # cap at 8k chars
    return {"url": page.url, "title": title, "text": text}


@tool(
    name="browser_get_text",
    description=(
        "Extract visible text from the current page, optionally scoped to a CSS selector. "
        "Useful for pulling structured sections (e.g. '.profile-section', '#about')."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector to scope extraction (default: body)",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default: 6000)",
            },
        },
        "required": [],
    },
)
def browser_get_text(selector: str = "body", max_chars: int = 6000) -> dict:
    page = _get_page(headless=_headless())
    try:
        text = page.inner_text(selector)[:max_chars]
    except Exception as e:
        text = f"[selector '{selector}' not found: {e}]"
    return {"selector": selector, "text": text}


@tool(
    name="browser_get_links",
    description="List all hyperlinks on the current page, optionally filtered by a CSS scope selector.",
    input_schema={
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "description": "CSS selector to limit link search (default: entire page)",
            },
        },
        "required": [],
    },
)
def browser_get_links(scope: str = "body") -> dict:
    page = _get_page(headless=_headless())
    links = page.eval_on_selector_all(
        f"{scope} a",
        "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))",
    )
    return {"links": links[:100]}  # cap at 100


@tool(
    name="browser_click",
    description="Click an element identified by a CSS selector.",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of element to click"},
            "wait_after_ms": {
                "type": "integer",
                "description": "Milliseconds to wait after click (default: 1000)",
            },
        },
        "required": ["selector"],
    },
)
def browser_click(selector: str, wait_after_ms: int = 1000) -> dict:
    page = _get_page(headless=_headless())
    page.click(selector)
    page.wait_for_timeout(wait_after_ms)
    return {"clicked": selector, "current_url": page.url}


@tool(
    name="browser_fill",
    description="Fill a text input or textarea with a value.",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector of input element"},
            "value": {"type": "string", "description": "Text to type into the input"},
        },
        "required": ["selector", "value"],
    },
)
def browser_fill(selector: str, value: str) -> dict:
    page = _get_page(headless=_headless())
    page.fill(selector, value)
    return {"filled": selector, "value": value}


@tool(
    name="browser_scroll",
    description=(
        "Scroll the page to load dynamic / infinite-scroll content. "
        "Call multiple times to load more items (e.g. LinkedIn feed posts)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["down", "up", "bottom", "top"],
                "description": "Scroll direction (default: down)",
            },
            "amount_px": {
                "type": "integer",
                "description": "Pixels to scroll (ignored for 'bottom'/'top', default: 1500)",
            },
        },
        "required": [],
    },
)
def browser_scroll(direction: str = "down", amount_px: int = 1500) -> dict:
    page = _get_page(headless=_headless())
    if direction == "bottom":
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    elif direction == "top":
        page.evaluate("window.scrollTo(0, 0)")
    elif direction == "up":
        page.evaluate(f"window.scrollBy(0, -{amount_px})")
    else:
        page.evaluate(f"window.scrollBy(0, {amount_px})")
    page.wait_for_timeout(800)
    return {"scrolled": direction, "amount_px": amount_px}


@tool(
    name="browser_screenshot",
    description="Capture a screenshot of the current page and return it as a base64-encoded PNG.",
    input_schema={
        "type": "object",
        "properties": {
            "full_page": {
                "type": "boolean",
                "description": "Capture the full scrollable page (default: false — visible area only)",
            },
        },
        "required": [],
    },
)
def browser_screenshot(full_page: bool = False) -> dict:
    page = _get_page(headless=_headless())
    png = page.screenshot(full_page=full_page)
    return {"image_base64": base64.b64encode(png).decode(), "format": "png"}


@tool(
    name="browser_wait",
    description="Wait for a CSS selector to appear on the page (useful after navigations or clicks).",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector to wait for"},
            "timeout_sec": {
                "type": "integer",
                "description": "Max seconds to wait (default: 10)",
            },
        },
        "required": ["selector"],
    },
)
def browser_wait(selector: str, timeout_sec: int = 10) -> dict:
    page = _get_page(headless=_headless())
    try:
        page.wait_for_selector(selector, timeout=timeout_sec * 1000)
        return {"found": True, "selector": selector}
    except Exception:
        return {"found": False, "selector": selector, "timed_out": True}


@tool(
    name="browser_evaluate",
    description=(
        "Execute JavaScript in the browser page and return the result. "
        "Use for scraping data that requires DOM traversal or custom extraction logic."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "script": {
                "type": "string",
                "description": "JavaScript expression to evaluate (must return a JSON-serialisable value)",
            },
        },
        "required": ["script"],
    },
)
def browser_evaluate(script: str) -> dict:
    page = _get_page(headless=_headless())
    result = page.evaluate(script)
    return {"result": result}


@tool(
    name="browser_close",
    description="Close the browser session and free resources. Call when finished with all browser tasks.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
def browser_close() -> dict:
    global _browser, _page, _playwright
    if _page:
        _page.close()
        _page = None
    if _browser:
        _browser.close()
        _browser = None
    if _playwright:
        _playwright.stop()
        _playwright = None
    return {"status": "browser closed"}


# ---------------------------------------------------------------------------
# LinkedIn helpers
# ---------------------------------------------------------------------------

@tool(
    name="linkedin_get_profile",
    description=(
        "Fetch a LinkedIn member's full profile: headline, about, experience, education, "
        "skills, and recent activity. Requires an authenticated LinkedIn session "
        "(set BROWSER_COOKIES_FILE to a saved-cookies JSON)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "profile_url": {
                "type": "string",
                "description": "Full LinkedIn profile URL, e.g. https://www.linkedin.com/in/johndoe/",
            },
        },
        "required": ["profile_url"],
    },
)
def linkedin_get_profile(profile_url: str) -> dict:
    page = _get_page(headless=_headless())

    # Navigate and wait for the profile main section
    page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)

    # Scroll to load lazy sections
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 1200)")
        page.wait_for_timeout(800)

    # Extract structured data via JS
    data = page.evaluate("""() => {
        const t = s => { const el = document.querySelector(s); return el ? el.innerText.trim() : ''; };
        const all = s => [...document.querySelectorAll(s)].map(e => e.innerText.trim()).filter(Boolean);
        return {
            name:       t('h1'),
            headline:   t('.text-body-medium.break-words'),
            location:   t('.text-body-small.inline.t-black--light.break-words'),
            about:      t('#about ~ div .full-width'),
            experience: all('.experience-item, [data-view-name="profile-component-entity"]').slice(0, 8),
            education:  all('.education__item').slice(0, 4),
            skills:     all('.skill-categories-section .t-bold').slice(0, 15),
        };
    }""")

    # Also grab recent activity URL
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    return {
        "profile_url": profile_url,
        "activity_url": activity_url,
        **data,
    }


@tool(
    name="linkedin_get_feed_posts",
    description=(
        "Retrieve a person's recent LinkedIn posts and activity (articles, shares, comments). "
        "Pass the profile URL; the tool navigates to their activity feed automatically."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "profile_url": {
                "type": "string",
                "description": "LinkedIn profile URL of the person",
            },
            "max_posts": {
                "type": "integer",
                "description": "Maximum number of posts to return (default: 10)",
            },
        },
        "required": ["profile_url"],
    },
)
def linkedin_get_feed_posts(profile_url: str, max_posts: int = 10) -> dict:
    page = _get_page(headless=_headless())
    activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
    page.goto(activity_url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)

    # Scroll to load posts
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 1500)")
        page.wait_for_timeout(900)

    posts = page.evaluate(f"""() => {{
        const items = [...document.querySelectorAll(
            '.occludable-update, [data-urn], .feed-shared-update-v2'
        )].slice(0, {max_posts});
        return items.map(el => ({{
            text: el.innerText.trim().slice(0, 800),
            timestamp: (el.querySelector('time') || {{}}).innerText || '',
        }}));
    }}""")

    return {"profile_url": profile_url, "posts": posts, "count": len(posts)}


@tool(
    name="linkedin_search_people",
    description=(
        "Search LinkedIn People with keyword + optional filters. "
        "Returns a list of matching profiles with name, headline, and profile URL."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "keywords": {"type": "string", "description": "Search query (name, title, company, etc.)"},
            "title_filter": {"type": "string", "description": "Filter by job title keyword"},
            "company_filter": {"type": "string", "description": "Filter by current company name"},
            "location_filter": {"type": "string", "description": "Filter by location"},
            "max_results": {
                "type": "integer",
                "description": "Max profiles to return (default: 10)",
            },
        },
        "required": ["keywords"],
    },
)
def linkedin_search_people(
    keywords: str,
    title_filter: str | None = None,
    company_filter: str | None = None,
    location_filter: str | None = None,
    max_results: int = 10,
) -> dict:
    page = _get_page(headless=_headless())

    # Build search URL
    import urllib.parse
    params: dict[str, str] = {"keywords": keywords, "origin": "GLOBAL_SEARCH_HEADER"}
    if title_filter:
        params["titleFilter"] = title_filter
    if company_filter:
        params["companyFilter"] = company_filter
    if location_filter:
        params["geoFilter"] = location_filter
    url = "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode(params)

    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2500)

    results = page.evaluate(f"""() => {{
        const cards = [...document.querySelectorAll(
            '.entity-result__item, .search-result__wrapper, li.reusable-search__result-container'
        )].slice(0, {max_results});
        return cards.map(card => ({{
            name:        (card.querySelector('.entity-result__title-text a, .actor-name') || {{}}).innerText?.trim() || '',
            headline:    (card.querySelector('.entity-result__primary-subtitle') || {{}}).innerText?.trim() || '',
            location:    (card.querySelector('.entity-result__secondary-subtitle') || {{}}).innerText?.trim() || '',
            profile_url: (card.querySelector('a.app-aware-link, a[href*="/in/"]') || {{}}).href || '',
        }}));
    }}""")

    return {"query": keywords, "results": [r for r in results if r.get("name")]}


@tool(
    name="linkedin_send_message",
    description=(
        "Open the LinkedIn messaging panel for a profile and send a message or "
        "connection request with a personalised note. "
        "Only works when authenticated (BROWSER_COOKIES_FILE must be set)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "profile_url": {
                "type": "string",
                "description": "Full LinkedIn profile URL of the recipient",
            },
            "message": {
                "type": "string",
                "description": "Message text to send (keep under 300 chars for connection notes)",
            },
            "action": {
                "type": "string",
                "enum": ["connect", "message"],
                "description": "'connect' sends a connection request with note; 'message' sends a direct InMail/message",
            },
        },
        "required": ["profile_url", "message", "action"],
    },
)
def linkedin_send_message(profile_url: str, message: str, action: str = "connect") -> dict:
    page = _get_page(headless=_headless())
    page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(2000)

    if action == "connect":
        # Click Connect button
        try:
            page.click('button[aria-label*="Connect"], button:has-text("Connect")', timeout=5000)
            page.wait_for_timeout(800)
            # Click "Add a note"
            try:
                page.click('button[aria-label="Add a note"]', timeout=3000)
                page.wait_for_timeout(500)
            except Exception:
                pass  # Some flows skip note step
            # Fill in the message
            page.fill('textarea[name="message"]', message[:300])
            page.wait_for_timeout(400)
            page.click('button[aria-label="Send now"], button:has-text("Send")', timeout=5000)
            return {"status": "connection_request_sent", "profile_url": profile_url}
        except Exception as e:
            return {"status": "error", "detail": str(e), "profile_url": profile_url}

    else:  # message / InMail
        try:
            page.click('button[aria-label*="Message"], button:has-text("Message")', timeout=5000)
            page.wait_for_timeout(1000)
            page.fill('.msg-form__contenteditable, [role="textbox"]', message)
            page.wait_for_timeout(400)
            page.click('button.msg-form__send-button, button[type="submit"]', timeout=5000)
            return {"status": "message_sent", "profile_url": profile_url}
        except Exception as e:
            return {"status": "error", "detail": str(e), "profile_url": profile_url}


# ---------------------------------------------------------------------------
# Skill metadata (read by SkillEngine)
# ---------------------------------------------------------------------------

SKILL_NAME = "browser"
SKILL_DESCRIPTION = "Playwright headless browser + LinkedIn automation tools"
SKILL_TOOLS = [
    "browser_navigate", "browser_get_text", "browser_get_links",
    "browser_click", "browser_fill", "browser_scroll",
    "browser_screenshot", "browser_wait", "browser_evaluate", "browser_close",
    "linkedin_get_profile", "linkedin_get_feed_posts",
    "linkedin_search_people", "linkedin_send_message",
]

SKILL_INSTRUCTIONS = """
## Browser & LinkedIn Skill

You have access to a real Chromium browser via Playwright. Use it to navigate websites,
extract content, and interact with LinkedIn.

### General browser usage
- Always call `browser_navigate` first before any other browser tool.
- After navigation, use `browser_get_text` to read page content.
- Use `browser_scroll` (direction: "down") 2-3 times on long pages / feeds to load more content.
- Call `browser_close` when you are completely finished with all browser tasks.

### LinkedIn usage
- `linkedin_get_profile` — fetches a person's full profile in one call.
- `linkedin_get_feed_posts` — retrieves recent posts for research.
- `linkedin_search_people` — find prospects matching a role/company/location.
- `linkedin_send_message` — sends a connection request or direct message.

### Authentication
LinkedIn requires a logged-in session stored in cookies. The cookies file path is
configured via `BROWSER_COOKIES_FILE`. If not set, unauthenticated browsing is used
(limited data).

### Rate limiting & ethics
- Add at least 1–2 seconds between consecutive LinkedIn page loads (the tools do this automatically).
- Never send more than 20 connection requests or messages per run.
- Personalise every message — generic messages hurt deliverability.
- Respect LinkedIn's Terms of Service.
""".strip()
