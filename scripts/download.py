"""Download every pending reel, re-encode it, and park it in a GitHub Release.

Runs on ingest and again every few hours on a retry cron. Instagram blocks
datacenter IPs unpredictably, so failures here are expected and harmless -
the item stays in queue/ and gets retried until it works.

A successful item moves queue/ -> ready/ with a public asset URL attached,
which is what Meta's servers fetch at publish time.
"""

import json
import os
import pathlib
import subprocess
import tempfile

from common import (
    QUEUE,
    READY,
    ROOT,
    list_items,
    load_config,
    log,
    repo_slug,
    run,
    write_item,
)

RELEASE_TAG = "media"
MAX_ATTEMPTS = 40


def write_cookies():
    """yt-dlp needs a logged-in session for most reels. Use a burner account."""
    raw = os.environ.get("IG_COOKIES", "").strip()
    if not raw:
        return None
    path = pathlib.Path(tempfile.gettempdir()) / "ig_cookies.txt"
    path.write_text(raw + "\n", encoding="utf-8")
    return str(path)


def probe(url, cookies):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--no-playlist"]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)
    return json.loads(run(cmd))


def author_of(info):
    for key in ("uploader_id", "uploader", "channel_id", "channel"):
        value = (info.get(key) or "").strip().lstrip("@")
        if value and " " not in value:
            return value
    return None


def fetch(url, cookies, dest):
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(dest),
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)
    run(cmd)


def reencode(src, dest, cfg):
    v = cfg["video"]
    crop, w, h = v["crop"], v["width"], v["height"]
    vf = (
        f"crop=iw*{crop}:ih*{crop},"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(v["crf"]),
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dest),
    ])


def ensure_release():
    existing = subprocess.run(
        ["gh", "release", "view", RELEASE_TAG], capture_output=True, text=True
    )
    if existing.returncode != 0:
        run([
            "gh", "release", "create", RELEASE_TAG,
            "--title", "Reel media",
            "--notes", "Video files staged here so Meta's servers can fetch them.",
        ])


def upload(path, shortcode):
    ensure_release()
    asset = f"{shortcode}.mp4"
    staged = path.parent / asset
    if staged != path:
        path.replace(staged)
    run(["gh", "release", "upload", RELEASE_TAG, str(staged), "--clobber"])
    return f"https://github.com/{repo_slug()}/releases/download/{RELEASE_TAG}/{asset}"


def process(item, cfg, cookies, workdir):
    url, code = item["url"], item["shortcode"]
    log(f"--- {code}")

    info = probe(url, cookies)
    author = author_of(info)
    if not author:
        raise RuntimeError("could not determine the original account's username")

    raw = workdir / f"{code}.raw.mp4"
    final = workdir / f"{code}.final.mp4"
    fetch(url, cookies, raw)
    reencode(raw, final, cfg)

    size_mb = final.stat().st_size / 1_000_000
    duration = float(info.get("duration") or 0)
    log(f"    @{author}, {duration:.0f}s, {size_mb:.1f} MB")
    if duration and not (3 <= duration <= 900):
        raise RuntimeError(f"duration {duration:.0f}s outside Instagram's 3s-15min limit")

    asset_url = upload(final, code)

    ready = dict(item)
    ready.pop("_path", None)
    ready.update({
        "author": author,
        "caption": cfg["caption_template"].format(author=author),
        "asset_url": asset_url,
        "duration": duration,
        "last_error": None,
    })
    name = pathlib.Path(item["_path"]).name
    write_item(READY / name, ready)
    pathlib.Path(item["_path"]).unlink()
    log(f"    ready -> {asset_url}")


def main():
    cfg = load_config()
    cookies = write_cookies()
    if not cookies:
        log("warning: no IG_COOKIES secret set, most reels will fail to download")

    pending = list_items(QUEUE)
    if not pending:
        log("queue is empty")
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
            item["last_error"] = str(exc)[:500]
            log(f"    failed (attempt {attempts}): {str(exc)[:200]}")
            if attempts >= MAX_ATTEMPTS:
                item["gave_up"] = True
                log("    giving up on this one, leaving it in queue/ for you to look at")
            write_item(path, item)

    if failures:
        log(f"{failures} item(s) will be retried on the next run")


if __name__ == "__main__":
    main()
