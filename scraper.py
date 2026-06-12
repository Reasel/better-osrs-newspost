#!/usr/bin/env python3
"""Better OSRS Newspost — build a mobile-friendly mirror of the latest OSRS
newsposts and notify (via ntfy) when a brand-new post appears.

Usage:
    python scraper.py            # same as "build"
    python scraper.py build      # scrape feed, render site/, mark new posts
    python scraper.py notify     # send any pending ntfy notification

The two-step split lets CI deploy the fresh site *before* the notification
fires, so the link in the push points at an already-live page.
"""

import calendar
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config (most can be overridden with environment variables)
# ---------------------------------------------------------------------------
FEED_URL = "https://secure.runescape.com/m=news/latest_news.rss?oldschool=1"
NUM_POSTS = int(os.environ.get("NUM_POSTS", "3"))
SITE_DIR = os.environ.get("SITE_DIR", "site")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
NOTIFY_FILE = os.environ.get("NOTIFY_FILE", ".notify.json")
CONTENT_CLASS = "news-article-content"
USER_AGENT = "Mozilla/5.0 (BetterOSRSNewspost; personal-use newspost mirror)"

NTFY_BASE = os.environ.get("NTFY_BASE", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "OSRSNewsPost")
# Base URL of the deployed mirror; used as the notification's "read" link.
SITE_URL = os.environ.get("SITE_URL", "").strip()
NOTIFY_ON_FIRST_RUN = os.environ.get("NOTIFY_ON_FIRST_RUN", "").lower() in (
    "1",
    "true",
    "yes",
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# ---------------------------------------------------------------------------
# Feed + article scraping
# ---------------------------------------------------------------------------
def fetch(url):
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def get_entries():
    """Return the NUM_POSTS newest feed entries as plain dicts."""
    raw = fetch(FEED_URL).content
    feed = feedparser.parse(raw)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Failed to parse feed: {feed.bozo_exception!r}")

    entries = []
    for e in feed.entries[:NUM_POSTS]:
        thumb = ""
        for enc in e.get("enclosures", []):
            if enc.get("href"):
                thumb = enc["href"]
                break

        date_str = ""
        if e.get("published_parsed"):
            ts = calendar.timegm(e.published_parsed)
            date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%A, %d %B %Y"
            )

        entries.append(
            {
                "title": e.get("title", "Untitled"),
                "link": e.get("link", ""),
                "guid": e.get("id") or e.get("link", ""),
                "date": date_str,
                "category": e.get("category", ""),
                "thumb": thumb,
                "summary": (e.get("summary", "") or "").strip(),
            }
        )
    if not entries:
        raise RuntimeError("Feed returned no entries.")
    return entries


_URL_ATTRS = ("href", "src", "poster")


def _absolutize_srcset(value, base_url):
    parts = []
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        bits = piece.split(None, 1)
        bits[0] = urljoin(base_url, bits[0])
        parts.append(" ".join(bits))
    return ", ".join(parts)


def extract_article_html(page_html, base_url):
    """Pull the article body out of a newspost page, rewrite every URL to
    absolute, and harden outbound links. Returns inner HTML or None."""
    soup = BeautifulSoup(page_html, "html.parser")
    container = soup.find("div", class_=CONTENT_CLASS)
    if container is None:
        anchor = soup.find(id="article-top")
        container = anchor.parent if anchor is not None else None
    if container is None:
        return None

    # Drop scripts/styles and the empty #article-top anchor div.
    for bad in container.find_all(["script", "style", "noscript"]):
        bad.decompose()
    anchor = container.find(id="article-top")
    if anchor is not None:
        anchor.decompose()

    # Rewrite relative URLs to absolute.
    for el in container.find_all(True):
        for attr in _URL_ATTRS:
            if el.has_attr(attr):
                el[attr] = urljoin(base_url, el[attr])
        if el.has_attr("srcset"):
            el["srcset"] = _absolutize_srcset(el["srcset"], base_url)
        # Strip inline event handlers (onclick, onload, ...).
        for attr in [a for a in el.attrs if a.lower().startswith("on")]:
            del el[attr]

    # Open all source links in a new tab.
    for a in container.find_all("a"):
        a["target"] = "_blank"
        a["rel"] = "noopener noreferrer"

    return container.decode_contents().strip()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1a1410">
<title>{{TITLE}} — OSRS News</title>
<style>
  :root {
    --bg: #15110d;
    --card: #1f1a14;
    --ink: #ece4d6;
    --muted: #a89b85;
    --gold: #f0a92b;
    --line: #352c20;
    --link: #ffc857;
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 18px;
    line-height: 1.65;
  }
  .wrap { max-width: 720px; margin: 0 auto; padding: 0 18px env(safe-area-inset-bottom); }
  header.site {
    position: sticky; top: 0; z-index: 5;
    background: rgba(21,17,13,0.96);
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line);
  }
  .brand {
    font-weight: 700; letter-spacing: 0.3px; color: var(--gold);
    padding: 14px 0 10px; font-size: 15px; text-transform: uppercase;
  }
  nav.posts { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 10px; scrollbar-width: none; }
  nav.posts::-webkit-scrollbar { display: none; }
  nav.posts a {
    flex: 0 0 auto; max-width: 60vw; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-size: 13px; text-decoration: none; color: var(--muted);
    border: 1px solid var(--line); border-radius: 999px; padding: 6px 12px;
  }
  nav.posts a.active { color: #15110d; background: var(--gold); border-color: var(--gold); font-weight: 700; }
  article { padding: 22px 0 8px; }
  .meta { color: var(--muted); font-size: 14px; margin-bottom: 4px; }
  .meta .cat { color: var(--gold); font-weight: 700; }
  h1 { font-size: 27px; line-height: 1.25; margin: 6px 0 14px; }
  .hero { width: 100%; height: auto; border-radius: 10px; margin: 4px 0 8px; display: block; }
  .content { font-size: 18px; }
  .content p { margin: 1.1em 0; }
  .content a { color: var(--link); }
  .content img { max-width: 100%; height: auto; border-radius: 8px; display: block; margin: 1em auto; }
  .content h2, .content h3, .content .osrs-subtitle, .content .osrs-subheading {
    color: var(--gold); line-height: 1.3; margin: 1.4em 0 0.5em;
  }
  .content ul, .content ol { padding-left: 1.3em; }
  .content table { width: 100%; border-collapse: collapse; display: block; overflow-x: auto; }
  .content td, .content th { border: 1px solid var(--line); padding: 6px 9px; }
  .content iframe { max-width: 100%; }
  .original {
    display: inline-block; margin: 10px 0 4px; padding: 11px 18px;
    background: var(--gold); color: #15110d; font-weight: 700;
    text-decoration: none; border-radius: 10px;
  }
  .pager { display: flex; justify-content: space-between; gap: 10px; margin: 26px 0 8px; }
  .pager a {
    flex: 1; text-align: center; text-decoration: none; color: var(--ink);
    border: 1px solid var(--line); border-radius: 10px; padding: 12px; font-size: 14px;
  }
  .pager a.disabled { opacity: 0.3; pointer-events: none; }
  footer { color: var(--muted); font-size: 12px; border-top: 1px solid var(--line); margin-top: 24px; padding: 16px 0 28px; }
  footer a { color: var(--muted); }
</style>
</head>
<body>
<header class="site">
  <div class="wrap">
    <div class="brand">Better OSRS Newspost</div>
    <nav class="posts">{{NAV}}</nav>
  </div>
</header>
<main class="wrap">
  <article>
    <div class="meta">{{CATEGORY}}{{DATE}}</div>
    <h1>{{TITLE}}</h1>
    {{HERO}}
    <div class="content">{{CONTENT}}</div>
    <a class="original" href="{{LINK}}" target="_blank" rel="noopener noreferrer">Read on RuneScape.com →</a>
    <div class="pager">{{PAGER}}</div>
  </article>
</main>
<footer class="wrap">
  <p>Mobile-friendly mirror of the latest Old School RuneScape news, rebuilt every few hours.
  All newspost content is &copy; Jagex Ltd; this is a personal-use reformatter that links back to the
  <a href="{{LINK}}" target="_blank" rel="noopener noreferrer">original post</a>.</p>
  <p>Last updated {{UPDATED}}.</p>
</footer>
</body>
</html>
"""


def filename_for(index):
    return "index.html" if index == 0 else f"post-{index + 1}.html"


def build_nav(posts, current):
    items = []
    for i, p in enumerate(posts):
        cls = " class=\"active\"" if i == current else ""
        label = "Latest" if i == 0 else escape(short(p["title"]))
        items.append(f'<a href="{filename_for(i)}"{cls}>{label}</a>')
    return "".join(items)


def build_pager(posts, current):
    newer = f'<a href="{filename_for(current - 1)}">← Newer</a>' if current > 0 else '<a class="disabled">← Newer</a>'
    older = (
        f'<a href="{filename_for(current + 1)}">Older →</a>'
        if current < len(posts) - 1
        else '<a class="disabled">Older →</a>'
    )
    return newer + older


def escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def short(text, length=28):
    return text if len(text) <= length else text[: length - 1] + "…"


def render_page(post, content_html, posts, index, updated):
    cat = f'<span class="cat">{escape(post["category"])}</span> · ' if post["category"] else ""
    date = escape(post["date"]) if post["date"] else ""
    hero = (
        f'<img class="hero" src="{escape(post["thumb"])}" alt="" loading="lazy">'
        if post["thumb"]
        else ""
    )
    repl = {
        "{{TITLE}}": escape(post["title"]),
        "{{NAV}}": build_nav(posts, index),
        "{{CATEGORY}}": cat,
        "{{DATE}}": date,
        "{{HERO}}": hero,
        "{{CONTENT}}": content_html,
        "{{LINK}}": escape(post["link"]),
        "{{PAGER}}": build_pager(posts, index),
        "{{UPDATED}}": updated,
    }
    html = PAGE_TEMPLATE
    for key, val in repl.items():
        html = html.replace(key, val)
    return html


# ---------------------------------------------------------------------------
# State + notifications
# ---------------------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(post):
    state = {
        "last_guid": post["guid"],
        "last_title": post["title"],
        "last_link": post["link"],
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def write_notify_marker(post):
    payload = {"title": post["title"], "post_link": post["link"]}
    with open(NOTIFY_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def cmd_notify():
    if not os.path.exists(NOTIFY_FILE):
        print("No pending notification.")
        return
    with open(NOTIFY_FILE, encoding="utf-8") as f:
        payload = json.load(f)

    title = payload.get("title", "New OSRS Newspost")
    post_link = payload.get("post_link", "")
    read_url = SITE_URL or post_link

    body = f"{title}\n\nRead: {read_url}"
    if post_link and post_link != read_url:
        body += f"\nSource: {post_link}"

    headers = {
        "Title": "New OSRS Newspost",
        "Tags": "newspaper",
        "Priority": "default",
    }
    if read_url or post_link:
        headers["Click"] = read_url or post_link

    url = f"{NTFY_BASE}/{NTFY_TOPIC}"
    resp = SESSION.post(url, data=body.encode("utf-8"), headers=headers, timeout=30)
    resp.raise_for_status()
    print(f"Sent ntfy to {url}: {title}")
    os.remove(NOTIFY_FILE)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def cmd_build():
    posts = get_entries()
    print(f"Fetched {len(posts)} posts. Newest: {posts[0]['title']!r}")

    os.makedirs(SITE_DIR, exist_ok=True)
    updated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    for i, post in enumerate(posts):
        content = extract_article_html(fetch(post["link"]).text, post["link"])
        if not content:
            content = (
                f'<p>Could not extract this post. '
                f'<a href="{escape(post["link"])}" target="_blank" rel="noopener noreferrer">'
                f"Read it on RuneScape.com</a>.</p>"
            )
            print(f"  ! content extraction failed for {post['link']}")
        html = render_page(post, content, posts, i, updated)
        path = os.path.join(SITE_DIR, filename_for(i))
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  wrote {path}  ({post['title']})")

    # New-post detection.
    state = load_state()
    newest = posts[0]
    if state is None:
        is_new = NOTIFY_ON_FIRST_RUN
        print("First run — no prior state." + (" Notifying." if is_new else " Skipping notification."))
    else:
        is_new = newest["guid"] != state.get("last_guid")
        print(f"New post detected: {is_new}")

    if is_new:
        write_notify_marker(newest)
        print(f"  queued notification: {newest['title']}")
    elif os.path.exists(NOTIFY_FILE):
        os.remove(NOTIFY_FILE)

    save_state(newest)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        cmd_build()
    elif cmd == "notify":
        cmd_notify()
    else:
        print(f"Unknown command: {cmd!r}. Use 'build' or 'notify'.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
