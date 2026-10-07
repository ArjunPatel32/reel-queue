"""Write a shared reel URL into queue/ as a pending item.

Called by the ingest workflow with the URL your iPhone Shortcut sent.
This runs before any download is attempted so a shared reel is never lost,
even if Instagram blocks the download for the next several hours.
"""

import argparse
import datetime as dt
import os
import re

import requests

from common import QUEUE, READY, POSTED, list_items, log, write_item

# instagram.com/reel/CODE, /reels/CODE, /p/CODE, /tv/CODE, and the newer
# instagram.com/<username>/reel/CODE form.
SHORTCODE = re.compile(
    r"instagram\.com/(?:(?!share/)[^/?#]+/)?(?:reels?|p|tv)/([A-Za-z0-9_-]+)"
)
SHARE_LINK = re.compile(r"instagram\.com/share/")
URL_IN_TEXT = re.compile(r"https?://\S+")


def shortcode_of(url):
    m = SHORTCODE.search(url)
    return m.group(1) if m else None


def resolve_share_link(url):
    """instagram.com/share/... links are redirects to the real reel URL."""
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"},
        )
        for hop in [*resp.history, resp]:
            if shortcode_of(hop.url):
                return hop.url
            location = hop.headers.get("location", "")
            if shortcode_of(location):
                return location
    except requests.RequestException as exc:
        log(f"could not resolve share link: {exc}")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    # The share sheet sometimes hands over "caption text https://..." rather
    # than a bare URL.
    found = URL_IN_TEXT.search(args.url)
    raw = found.group(0) if found else args.url.strip()

    code = shortcode_of(raw)
    if not code and SHARE_LINK.search(raw):
        resolved = resolve_share_link(raw)
        code = shortcode_of(resolved or "")
    if not code:
        raise SystemExit(f"not an Instagram reel/post URL: {args.url!r}")

    for folder in (QUEUE, READY, POSTED):
        for existing in list_items(folder):
            if existing.get("shortcode") == code:
                log(f"already have {code} in {folder.name}/ - skipping")
                return

    now = dt.datetime.now(dt.timezone.utc)
    item = {
        "shortcode": code,
        "url": f"https://www.instagram.com/reel/{code}/",
        "added_at": now.isoformat(),
        "attempts": 0,
        "last_error": None,
    }
    name = f"{now.strftime('%Y%m%dT%H%M%S')}-{code}.json"
    write_item(QUEUE / name, item)
    log(f"queued {code} -> queue/{name}")

    # Tell the workflow which reel to download straight away.
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"shortcode={code}\n")


if __name__ == "__main__":
    main()
