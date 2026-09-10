"""Write a shared reel URL into queue/ as a pending item.

Called by the ingest workflow with the URL your iPhone Shortcut sent.
This runs before any download is attempted so a shared reel is never lost,
even if Instagram blocks the download for the next several hours.
"""

import argparse
import datetime as dt
import re

from common import QUEUE, READY, POSTED, list_items, log, write_item

SHORTCODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def shortcode_of(url):
    m = SHORTCODE.search(url)
    return m.group(1) if m else None


def clean(url):
    """Strip the tracking junk Instagram's share button appends."""
    return url.split("?")[0].strip().rstrip("/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    url = clean(args.url)
    code = shortcode_of(url)
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


if __name__ == "__main__":
    main()
