"""Download every pending reel, edit it, and park it in a GitHub Release.

Runs on ingest and again every few hours on a retry cron. Instagram blocks
datacenter IPs unpredictably, so failures here are expected and harmless -
the item stays in queue/ and gets retried until it works.

A successful item moves queue/ -> ready/ with the edited video attached as a
release asset.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import tempfile

from common import (
    POSTED,
    QUEUE,
    READY,
    RELEASE_TAG,
    list_items,
    load_config,
    log,
    repo_slug,
    run,
    shortcodes_in,
    write_item,
)
from edit import EditError, make_post_video
from enqueue import SHARE_LINK, resolve_share_link, shortcode_of

MAX_ATTEMPTS = 40
# Errors that mean "Instagram is blocking this runner" rather than "this reel
# is broken". Two in a row on different reels ends the run early.
BLOCKED = re.compile(r"rate.?limit|429|login|not granting access", re.I)

HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
MENTION = re.compile(r"@([A-Za-z0-9._]{1,30})")
# What makes an @mention a credit for the video itself ("credit: @x",
# "cr @x", "via @x", "©️ @x", "🎥 @x", "video by @x", "creds @x"...) ...
CREDIT_CONTEXT = re.compile(
    r"\b(?:credits?|creds?|cr|via|source|original)\b|©|🎥|📹|🎬|📽"
    r"|\b(?:video|reel|clip|content|filmed|made|created|posted|recorded)\s+by\b"
    r"|\bvideo\s*:",
    re.I,
)
# ... and what makes it a credit for something else ("music cr @x",
# "song credit: @x", "shop my outfit via @x", "edit by @x").
OTHER_CONTEXT = re.compile(
    r"\b(?:music|song|audio|sound|beat|track|instrumental|shop|outfit|wearing|"
    r"edit(?:ed|or|s|ing)?|makeup|hair|styl\w*|location|thumbnail|cover)\b|📍|🎵|🎶|🎧",
    re.I,
)


def write_cookies():
    """Optional logged-in session for yt-dlp. Use a burner account."""
    raw = os.environ.get("IG_COOKIES", "").strip()
    if not raw:
        return None
    path = pathlib.Path(tempfile.gettempdir()) / "ig_cookies.txt"
    path.write_text(raw + "\n", encoding="utf-8")
    return str(path)


def poster_of(info):
    """The account that posted the reel. yt-dlp puts the Instagram username
    in `channel`; `uploader_id` is the numeric account id and `uploader` is
    the display name, so neither of those can go in a caption."""
    handle = (info.get("channel") or "").strip().lstrip("@")
    return handle if HANDLE.match(handle) else None


def credited_in(description, poster):
    """A different account the poster credits for the video in their
    caption, if any. Each @mention is judged by the words just before it on
    the same line (since the previous mention), so "audio cr: @singer /
    🎥 cr: @creator" credits @creator, not @singer."""
    text = description or ""
    seg_start = 0
    for m in MENTION.finditer(text):
        before = text[seg_start:m.start()]
        seg_start = m.end()
        before = re.split(r"[\n|•]", before)[-1]
        handle = m.group(1).rstrip(".")
        if not HANDLE.match(handle) or handle.lower() == (poster or "").lower():
            continue
        if OTHER_CONTEXT.search(before):
            continue
        if CREDIT_CONTEXT.search(before):
            return handle
    return None


def _yt_dlp(url, cookies, workdir, code):
    """Run yt-dlp and return the path of the finished (merged) video."""
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--write-info-json",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--print", "after_move:filepath",
        "-o", str(workdir / f"{code}.%(ext)s"),
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in proc.stderr.splitlines():
        if line.startswith(("WARNING", "ERROR")):
            log(f"    yt-dlp: {line}")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-3:])
        raise RuntimeError(f"yt-dlp failed: {tail}")
    printed = [line for line in proc.stdout.splitlines() if line.strip()]
    if not printed or not pathlib.Path(printed[-1]).exists():
        raise RuntimeError("yt-dlp finished but didn't report a video file")
    return pathlib.Path(printed[-1])


def fetch(url, cookies, workdir, code):
    """One yt-dlp call for both the video and its metadata, so Instagram only
    sees one round of requests per reel. If a cookie-backed attempt fails
    (a flagged or expired burner session makes yt-dlp error out rather than
    fall back), try once more logged-out from a clean slate."""
    try:
        video = _yt_dlp(url, cookies, workdir, code)
    except RuntimeError:
        if not cookies:
            raise
        log("    failed with IG_COOKIES - retrying logged-out (the cookies may be dead)")
        for leftover in workdir.glob(f"{code}.*"):
            leftover.unlink()
        video = _yt_dlp(url, None, workdir, code)

    info = json.loads((workdir / f"{code}.info.json").read_text(encoding="utf-8"))
    if info.get("_type") == "playlist":
        raise EditError("this is a carousel post, not a single reel")
    raw = workdir / f"{code}.raw{video.suffix}"
    video.replace(raw)
    return raw, info


def fetch_and_edit(url, code, cfg, cookies, workdir):
    """Download + edit one reel. Returns (raw_path, final_path, meta, edit_info)
    where meta has `author` (who gets the credit) and `poster` (who posted it)."""
    workdir = pathlib.Path(tempfile.mkdtemp(prefix=f"{code}-", dir=workdir))
    raw, info = fetch(url, cookies, workdir, code)
    poster = poster_of(info)
    if not poster:
        raise RuntimeError(
            "could not find the poster's username in yt-dlp's metadata "
            f"(channel={info.get('channel')!r}, uploader={info.get('uploader')!r})"
        )
    original = credited_in(info.get("description"), poster)
    author = original or poster
    if original:
        log(f"    posted by @{poster}, who credits @{original} - crediting @{original}")
    else:
        log(f"    by @{poster}")

    final = workdir / f"{code}.final.mp4"
    edit_info = make_post_video(raw, final, cfg, author)
    meta = {"author": author, "poster": poster}
    return raw, final, meta, edit_info


def ensure_release(tag, title):
    existing = subprocess.run(["gh", "release", "view", tag], capture_output=True, text=True)
    if existing.returncode != 0:
        created = subprocess.run([
            "gh", "release", "create", tag,
            "--title", title,
            "--notes", "Video files staged here by the reel-queue workflows.",
        ], capture_output=True, text=True)
        # Two runs can race to create it; losing that race is fine.
        if created.returncode != 0 and "already exists" not in created.stderr:
            raise RuntimeError(f"could not create release {tag}: {created.stderr.strip()}")


def upload(path, asset, tag=RELEASE_TAG, title="Reel media"):
    ensure_release(tag, title)
    staged = path.parent / asset
    if staged != path:
        path.replace(staged)
    run(["gh", "release", "upload", tag, str(staged), "--clobber"])
    return f"https://github.com/{repo_slug()}/releases/download/{tag}/{asset}"


def resolve(item):
    """Items queued from an unresolved /share/ link get their real shortcode
    here, on a later attempt. Returns False if it still can't be resolved."""
    if not item.get("needs_resolve"):
        return True
    url = resolve_share_link(item["url"])
    code = shortcode_of(url or "")
    if not code:
        return False
    log(f"    share link resolved -> {code}")
    item.update({
        "shortcode": code,
        "url": f"https://www.instagram.com/reel/{code}/",
        "needs_resolve": False,
    })
    return True


def process(item, cfg, cookies, workdir):
    path = pathlib.Path(item["_path"])
    log(f"--- {item['shortcode']}")
    if not resolve(item):
        raise RuntimeError("couldn't resolve the instagram.com/share/ link yet (login wall?)")

    code = item["shortcode"]
    if code in shortcodes_in(READY, POSTED):
        # The same reel was shared twice; the first copy is already handled.
        log("    already in ready/ or posted/ - dropping this duplicate")
        path.unlink()
        return

    _, final, meta, edit_info = fetch_and_edit(item["url"], code, cfg, cookies, workdir)
    # Name the asset after the queue file, not the shortcode, so two queue
    # entries can never share (or overwrite) one video.
    asset_url = upload(final, f"{path.stem}.mp4")

    ready = dict(item)
    ready.pop("_path", None)
    ready.pop("caption", None)
    ready.update(meta)
    ready.update({
        "asset_url": asset_url,
        "duration": edit_info["seconds"],
        "edit": edit_info,
        "last_error": None,
    })
    if not edit_info["has_audio"]:
        # Usually a music reel whose licensed track didn't come through -
        # a muted repost isn't worth posting. Delete "skip" to post anyway.
        ready["skip"] = True
        ready["skip_reason"] = "no audio track in the download"
        log("    no audio - parked in ready/ with skip=true, it won't be posted")
    write_item(READY / path.name, ready)
    path.unlink()
    log(f"    ready -> {asset_url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="just this queue file stem (ingest uses this)")
    args = ap.parse_args()

    cfg = load_config()
    cookies = write_cookies()
    if not cookies:
        log("no IG_COOKIES secret - trying logged-out downloads")

    pending = [i for i in list_items(QUEUE) if not i.get("gave_up")]
    if args.only:
        pending = [i for i in pending if pathlib.Path(i["_path"]).stem == args.only]
    # Least-tried first, so one reel that keeps failing can't always go first
    # and stall the rest.
    pending.sort(key=lambda i: (int(i.get("attempts", 0)), pathlib.Path(i["_path"]).name))
    if not pending:
        log("nothing pending")
        return

    log(f"{len(pending)} pending")
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="reels-"))
    failures = blocked_streak = 0

    for item in pending:
        path = pathlib.Path(item["_path"])
        try:
            process(item, cfg, cookies, workdir)
            blocked_streak = 0
        except Exception as exc:  # keep going; a bad item must not block the rest
            failures += 1
            attempts = int(item.get("attempts", 0)) + 1
            item["attempts"] = attempts
            item["last_error"] = str(exc)[-500:]
            log(f"    failed (attempt {attempts}): {str(exc)[-400:]}")
            if isinstance(exc, EditError):
                # Deterministic - downloading it again won't change anything.
                item["gave_up"] = True
                log("    this reel can't be made postable - giving up on it")
            elif attempts >= MAX_ATTEMPTS:
                item["gave_up"] = True
                log("    giving up on this one, leaving it in queue/ for you to look at")
            write_item(path, item)
            # A rate-limit/login wall hits every reel the same way from this
            # IP. One such error can also just be a private or deleted reel,
            # so stop only after two in a row.
            blocked_streak = blocked_streak + 1 if BLOCKED.search(str(exc)) else 0
            if blocked_streak >= 2:
                log("    Instagram seems to be blocking this runner - stopping for now")
                break

    if failures:
        log(f"{failures} item(s) will be retried on the next run")


if __name__ == "__main__":
    main()
