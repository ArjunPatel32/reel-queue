"""Download every pending reel, edit it, and park it in a GitHub Release.

Runs on ingest and again every few hours on a retry cron. Instagram blocks
datacenter IPs unpredictably, so failures here are expected and harmless -
the item stays in queue/ and gets retried until it works.

A successful item moves queue/ -> ready/ with a public asset URL attached.
"""

import argparse
import json
import os
import pathlib
import subprocess
import tempfile

from common import (
    QUEUE,
    READY,
    list_items,
    load_config,
    log,
    repo_slug,
    run,
    write_item,
)
from edit import make_post_video

RELEASE_TAG = "media"
MAX_ATTEMPTS = 40


def write_cookies():
    """Optional logged-in session for yt-dlp. Use a burner account."""
    raw = os.environ.get("IG_COOKIES", "").strip()
    if not raw:
        return None
    path = pathlib.Path(tempfile.gettempdir()) / "ig_cookies.txt"
    path.write_text(raw + "\n", encoding="utf-8")
    return str(path)


def author_of(info):
    """The original poster's @handle. yt-dlp puts the Instagram username in
    `channel`; `uploader_id` is the numeric account id and `uploader` is the
    display name, so neither of those can go in a caption."""
    handle = (info.get("channel") or "").strip().lstrip("@")
    if handle and " " not in handle:
        return handle
    for key in ("channel_url", "uploader_url"):
        url = (info.get(key) or "").rstrip("/")
        if "instagram.com/" in url:
            tail = url.rsplit("/", 1)[-1]
            if tail and not tail.isdigit():
                return tail
    return None


def fetch(url, cookies, workdir, code):
    """One yt-dlp call for both the video and its metadata, so Instagram only
    sees one round of requests per reel."""
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--write-info-json",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(workdir / f"{code}.%(ext)s"),
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)
    run(cmd)
    info = json.loads((workdir / f"{code}.info.json").read_text(encoding="utf-8"))
    videos = [p for p in workdir.glob(f"{code}.*") if p.suffix in (".mp4", ".mov", ".webm", ".mkv")]
    if not videos:
        raise RuntimeError("yt-dlp finished but no video file was written")
    raw = workdir / f"{code}.raw{videos[0].suffix}"
    videos[0].replace(raw)
    return raw, info


def fetch_and_edit(url, code, cfg, cookies, workdir):
    """Download + edit one reel. Returns (raw_path, final_path, author, edit_info)."""
    raw, info = fetch(url, cookies, workdir, code)
    author = author_of(info)
    if not author:
        raise RuntimeError(
            "could not find the original account's username in yt-dlp's metadata "
            f"(channel={info.get('channel')!r}, uploader={info.get('uploader')!r})"
        )
    log(f"    by @{author}")
    final = workdir / f"{code}.final.mp4"
    edit_info = make_post_video(raw, final, cfg, author)
    return raw, final, author, edit_info


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

    _, final, author, edit_info = fetch_and_edit(url, code, cfg, cookies, workdir)
    asset_url = upload(final, f"{code}.mp4")

    ready = dict(item)
    ready.pop("_path", None)
    ready.pop("caption", None)
    ready.update({
        "author": author,
        "asset_url": asset_url,
        "duration": edit_info["seconds"],
        "edit": edit_info,
        "last_error": None,
    })
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

    if failures:
        log(f"{failures} item(s) will be retried on the next run")


if __name__ == "__main__":
    main()
