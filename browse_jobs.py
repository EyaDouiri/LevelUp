"""Real-site browsing adapters used by the scout agent.

Two profiles:
  - stage.fr: server-rendered HTML, read directly with httpx (no key, no browser).
  - Welcome to the Jungle: JSON API first (lightweight); if it returns nothing,
    fall back to a headless Chromium browsing session when playwright is installed.

Everything is read-only browsing: each link is then verified individually by the
verifier agent before the analyst ranks it. Nothing here collects credentials.
"""
import re
from html import unescape
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LevelUpPersonalResearch")

_ANCHOR = re.compile(r"<\s*a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(html):
    return unescape(_TAG.sub(" ", html or "")).strip() or ""


def _clean_anchors(html, base, keep, limit):
    found = []
    for href, title in _ANCHOR.findall(str(html or "")):
        full = urljoin(base, href)
        host = urlsplit(full).hostname or ""
        if keep not in full or not host or any(host == d or host.endswith("." + d) for d in
                                               ("wikipedia.org", "youtube.com", "reddit.com", "facebook.com")):
            continue
        item = {"url": full, "title": _text(title)[:220]}
        if full not in {existing["url"] for existing in found}:
            found.append(item)
        if len(found) >= limit:
            break
    return found


def stage_fr_jobs(limit=15):
    """Parse the Stage.fr index page: every /job/ link is an individual offer."""
    url = "https://www.stage.fr/jobs"
    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=20,
                             follow_redirects=True)
        response.raise_for_status()
        html = response.text
    except httpx.HTTPError:
        return []
    return _clean_anchors(html, url, "/job/", limit)


def wttj_jobs(query, limit=15):
    """Welcome to the Jungle: try the JSON feed, then a Playwright session."""
    results = _wttj_json(query, limit)
    if results:
        return results
    return _wttj_playwright(query, limit)


def _wttj_json(query, limit):
    url = "https://www.welcometothejungle.com/api/v2/jobs"
    try:
        response = httpx.get(url, params={"query": query, "page": 1},
                             headers={"Accept": "application/json", "User-Agent": USER_AGENT},
                             timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    offers = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        org = item.get("organization") or {}
        org_slug = org.get("slug") if isinstance(org, dict) else ""
        slug = item.get("slug") or item.get("link") or ""
        if slug and org_slug:
            link = f"https://www.welcometothejungle.com/fr/companies/{org_slug}/jobs/{slug}"
        elif isinstance(item.get("link"), str) and item["link"].startswith("http"):
            link = item["link"]
        else:
            continue
        offers.append({"url": link, "title": item.get("name") or item.get("title") or "Offre a examiner"})
    return offers


def _wttj_playwright(query, limit):
    """Browse WTTJ like a human would: load, wait, collect the /jobs/ links."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    links = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            url = f"https://www.welcometothejungle.com/fr/jobs?query={query}&page=1"
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            seen = set()
            for anchor in page.locator("a[href*='/jobs/']").all():
                href = anchor.get_attribute("href") or ""
                title = anchor.inner_text().strip().splitlines()[:1]
                title = title[0] if title else "Offre a examiner"
                link = urljoin("https://www.welcometothejungle.com", href)
                if link in seen or not link.startswith("https://www.welcometothejungle.com/fr/companies/"):
                    continue
                seen.add(link)
                links.append({"url": link, "title": title[:220]})
                if len(links) >= limit:
                    break
            browser.close()
    except Exception:
        return []
    return links