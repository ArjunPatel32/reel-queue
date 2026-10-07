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
    QUEUE,
    READY,
    RELEASE_TAG,
    list_items,
    load_config,
    log,
    repo_slug,
    run,
    write_item,
)
from edit import make_post_video

MAX_ATTEMPTS = 40

HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# "credit: @x", "cr @x", "via @x", "🎥 @x", "video by @x" ... in the poster's
# caption. Repost pages usually name the real creator this way. A bare
# "by @x" is deliberately NOT matched - "music by @x" / "edit by @x" would
# credit the wrong person.
CREDIT = re.compile(
    r"(?:credits?|\bcr\b|\bvia|(?:video|reel|clip|content|filmed|made|created|posted) by"
    r"|🎥|📹|🎬|📸|©)"
    r"\s*(?:to)?\s*[:\-–—]?\s*@([A-Za-z0-9._]{1,30})",
    re.IGNORECASE,
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
    """A different account the poster credits in their caption, if any."""
    for m in CREDIT.finditer(description or ""):
        handle = m.group(1).rstrip(".")
        if HANDLE.match(handle) and handle.lower() != (poster or "").lower():
            return handle
    return None


def _yt_dlp(url, cookies, workdir, code):
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--write-info-json",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
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


def fetch(url, cookies, workdir, code):
    """One yt-dlp call for both the video and its metadata, so Instagram only
    sees one round of requests per reel. If a cookie-backed attempt fails
    (a flagged or expired burner session makes yt-dlp error out rather than
    fall back), try once more logged-out."""
    try:
        _yt_dlp(url, cookies, workdir, code)
    except RuntimeError:
        if not cookies:
            raise
        log("    failed with IG_COOKIES - retrying logged-out (the cookies may be dead)")
        _yt_dlp(url, None, workdir, code)

    info = json.loads((workdir / f"{code}.info.json").read_text(encoding="utf-8"))
    if info.get("_type") == "playlist":
        raise RuntimeError("this is a carousel post, not a single reel - skipping")
    videos = [p for p in workdir.glob(f"{code}.*") if p.suffix in (".mp4", ".mov", ".webm", ".mkv")]
    if not videos:
        raise RuntimeError("yt-dlp finished but no video file was written")
    raw = workdir / f"{code}.raw{videos[0].suffix}"
    videos[0].replace(raw)
    return raw, info


def fetch_and_edit(url, code, cfg, cookies, workdir):
    """Download + edit one reel. Returns (raw_path, final_path, meta, edit_info)
    where meta has `author` (who gets the credit) and `poster` (who posted it)."""
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
        run([
            "gh", "release", "create", tag,
            "--title", title,
            "--notes", "Video files staged here by the reel-queue workflows.",
        ])


def upload(path, asset, tag=RELEASE_TAG, title="Reel media"):
    ensure_release(tag, title)
    staged = path.parent / asset
    if staged != path:
        path.replace(staged)
    run(["gh", "release", "upload", tag, str(staged), "--clobber"])
    return f"https://github.com/{repo_slug()}/releases/download/{tag}/{asset}"


def process(item, cfg, cookies, workdir):
    url, code = item["url"], item["shortcode"]
    log(f"--- {code}")

    _, final, meta, edit_info = fetch_and_edit(url, code, cfg, cookies, workdir)
    asset_url = upload(final, f"{code}.mp4")

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
    name = pathlib.Path(item["_path"]).name
    write_item(READY / name, ready)
    pathlib.Path(item["_path"]).unlink()
    log(f"    ready -> {asset_url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="just this shortcode (ingest uses this)")
    args = ap.parse_args()

    cfg = load_config()
    cookies = write_cookies()
    if not cookies:
        log("no IG_COOKIES secret - trying logged-out downloads")

    pending = [i for i in list_items(QUEUE) if not i.get("gave_up")]
    if args.only:
        pending = [i for i in pending if i["shortcode"] == args.only]
    if not pending:
        log("nothing pending")
        return

    log(f"{len(pending)} pending")
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="reels-"))
    failures = 0

    for item in pending:
        path = pathlib.Path(item["_path"])
        try:
            process(item, cfg, cookies, workdir)
        except Exception as exc:  # keep going; a bad item must not block the rest
            failures += 1
            attempts = int(item.get("attempts", 0)) + 1
            item["attempts"] = attempts
            item["last_error"] = str(exc)[-500:]
            log(f"    failed (attempt {attempts}): {str(exc)[-400:]}")
            if attempts >= MAX_ATTEMPTS:
                item["gave_up"] = True
                log("    giving up on this one, leaving it in queue/ for you to look at")
            write_item(path, item)
            # A rate-limit or login wall will hit every other item the same
            # way from this IP - stop instead of hammering Instagram.
            if re.search(r"rate.?limit|429|login|not granting access", str(exc), re.I):
                log("    looks like Instagram is blocking this runner - stopping for now")
                break

    if failures:
        log(f"{failures} item(s) will be retried on the next run")


if __name__ == "__main__":
    main()
