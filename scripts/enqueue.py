"""Write a shared reel URL into queue/ as a pending item.

Called by the ingest workflow with the URL your iPhone Shortcut sent.
This runs before any download is attempted so a shared reel is never lost,
even if Instagram blocks the download for the next several hours.
"""

import argparse
import datetime as dt
import os
import pathlib
import re

import requests

from common import POSTED, QUEUE, READY, log, run, shortcodes_in, write_item

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
    """instagram.com/share/... links redirect to the real reel URL. Instagram
    answers browser user-agents with an HTML page instead of the redirect, so
    ask like curl does (same trick cobalt uses), then fall back to the page's
    canonical link."""
    try:
        resp = requests.get(
            url, allow_redirects=False, timeout=30, headers={"User-Agent": "curl/7.88.1"}
        )
        location = resp.headers.get("location", "")
        if shortcode_of(location):
            return location
        resp = requests.get(url, allow_redirects=True, timeout=30)
        for hop in [*resp.history, resp]:
            if shortcode_of(hop.url):
                return hop.url
        for pattern in (
            r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"',
            r'<meta[^>]+property="og:url"[^>]+content="([^"]+)"',
        ):
            m = re.search(pattern, resp.text)
            if m and shortcode_of(m.group(1)):
                return m.group(1)
    except requests.RequestException as exc:
        log(f"could not resolve share link: {exc}")
    return None


def shared_at():
    """When the share happened: the workflow run's creation time (GitHub
    stamps it at dispatch). Runs then spend minutes on setup in parallel, so
    'now' at this step could put two quick shares in the wrong order."""
    run_id, repo = os.environ.get("GITHUB_RUN_ID"), os.environ.get("GITHUB_REPOSITORY")
    if run_id and repo:
        try:
            created = run(["gh", "api", f"repos/{repo}/actions/runs/{run_id}", "--jq", ".created_at"])
            return dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (RuntimeError, ValueError) as exc:
            log(f"(couldn't read the run's start time, using now: {exc})")
    return dt.datetime.now(dt.timezone.utc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    # The share sheet sometimes hands over "caption text https://..." rather
    # than a bare URL.
    found = URL_IN_TEXT.search(args.url)
    raw = found.group(0).rstrip(").,") if found else args.url.strip()

    code, needs_resolve = shortcode_of(raw), False
    if not code and SHARE_LINK.search(raw):
        code = shortcode_of(resolve_share_link(raw) or "")
        if not code:
            # Instagram walled the lookup. Queue it anyway under the share id
            # and let the download step resolve it on a later attempt - a
            # shared reel must never be lost.
            tail = raw.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            share_id = re.sub(r"[^A-Za-z0-9_-]", "", tail)[:40]
            code, needs_resolve = f"share-{share_id or 'link'}", True
            log("share link didn't resolve yet - queueing it to resolve later")
    if not code:
        raise SystemExit(f"not an Instagram reel/post URL: {args.url!r}")

    # Matched on file names, so one hand-broken JSON file can't stop a share.
    if code in shortcodes_in(QUEUE, READY, POSTED):
        log(f"already have {code} - skipping")
        return

    when = shared_at()
    item = {
        "shortcode": code,
        "url": raw if needs_resolve else f"https://www.instagram.com/reel/{code}/",
        "added_at": when.isoformat(),
        "attempts": 0,
        "last_error": None,
    }
    if needs_resolve:
        item["needs_resolve"] = True
    name = f"{when.strftime('%Y%m%dT%H%M%S')}-{code}.json"
    write_item(QUEUE / name, item)
    log(f"queued {code} -> queue/{name}")

    # Tell the workflow which item to download straight away.
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"stem={pathlib.Path(name).stem}\n")


if __name__ == "__main__":
    main()
