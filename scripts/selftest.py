"""Offline checks - no Instagram, no Meta, no network. Run by check.yml on
every code change, and safe to run by hand: python scripts/selftest.py

Covers URL parsing, credit extraction, the daily planner, and a full edit of
a synthetic clip that ends in a fake "follow @creator" card.
"""

import datetime as dt
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from common import build_caption, load_config
from download import author_of
from enqueue import shortcode_of
import post

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def test_urls():
    cases = {
        "https://www.instagram.com/reel/DQwMwTLEvbn/?igsh=MWx0ZGQ1": "DQwMwTLEvbn",
        "https://instagram.com/reels/ABC_def-123/": "ABC_def-123",
        "https://www.instagram.com/p/Cxyz123/": "Cxyz123",
        "https://www.instagram.com/someuser/reel/DQwMwTLEvbn/": "DQwMwTLEvbn",
        "check this out https://www.instagram.com/reel/DQwMwTLEvbn/ lol": "DQwMwTLEvbn",
        "https://www.instagram.com/share/reel/BAqZx81": None,
        "https://example.com/reel/nope": None,
    }
    for url, want in cases.items():
        check(f"shortcode {url[:50]}", shortcode_of(url) == want, f"got {shortcode_of(url)!r}")


def test_author():
    # Field layout taken from yt-dlp's Instagram extractor test cases.
    info = {"channel": "naomipq", "uploader_id": "2815873", "uploader": "B E A U T Y  F O R  A S H E S"}
    check("author uses channel, not numeric id", author_of(info) == "naomipq", author_of(info))
    check("author none without channel", author_of({"uploader_id": "2815873", "uploader": "Name"}) is None)
    check("author from channel_url", author_of({"channel_url": "https://www.instagram.com/foo.bar"}) == "foo.bar")


def test_planner(cfg):
    tz = ZoneInfo(cfg["timezone"])
    morning = dt.datetime(2026, 10, 7, 6, 0, tzinfo=tz)
    for waiting, want in ((0, (1, 3)), (10, (1, 3)), (11, (2, 4)), (40, (2, 4))):
        got = post.count_range(cfg, waiting)
        check(f"{waiting} waiting -> {want[0]}-{want[1]} a day", got == want, got)

    start = dt.datetime.combine(morning.date(), post.parse_hhmm(cfg["window"]["start"]), tz)
    end = dt.datetime.combine(morning.date(), post.parse_hhmm(cfg["window"]["end"]), tz)
    for count in (1, 2, 3, 4):
        for _ in range(100):
            times = post.pick_times(cfg, morning, count)
            gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
            inside = all(start <= t <= end for t in times)
            if len(times) != count or not inside or any(g < 1800 for g in gaps):
                check(f"{count} slots inside window, >=30 min apart", False, times)
                break
        else:
            check(f"{count} slots inside window, >=30 min apart", True)

    late = dt.datetime.combine(morning.date(), dt.time(23, 0), tz)
    check("planning after window still yields slots", len(post.pick_times(cfg, late, 2)) == 2)


def test_caption(cfg):
    cap = build_caption(cfg, "someone")
    check("caption credits the author", "@someone" in cap, cap)


def make_clip(path):
    """8s of moving 16:9 test pattern with a tone, then a 3s black card that
    says 'Follow @testcreator'."""
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    card = (
        f"drawtext=fontfile={font}:text='Follow @testcreator':fontsize=90:"
        "fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2"
    )
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8",
        "-f", "lavfi", "-i", f"color=c=black:size=1280x720:rate=30:duration=3,{card}",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=11",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ], check=True)


def test_edit(cfg):
    if not shutil.which("ffmpeg"):
        print("skip edit test (no ffmpeg)")
        return
    from edit import contact_sheet, make_post_video, probe

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selftest-"))
    src, out = tmp / "src.mp4", tmp / "out.mp4"
    make_clip(src)
    info = make_post_video(src, out, cfg, "testcreator")
    o = probe(out)
    check("output is 1080x1920", (o["width"], o["height"]) == (1080, 1920), o)
    check("outro card cut (kept ~8s of 11s)", 7.5 <= o["duration"] <= 8.6, info)
    check("outro reason names the card", "end card" in info["outro"] or "still" in info["outro"], info["outro"])
    check("output has audio", o["has_audio"])

    sheet = tmp / "sheet.jpg"
    contact_sheet(src, out, sheet, info["kept"])
    check("contact sheet written", sheet.exists() and sheet.stat().st_size > 10_000)
    keep = pathlib.Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    shutil.copy(sheet, keep / "selftest-sheet.jpg")

    # A clip with NO outro must come through at full length.
    plain = tmp / "plain.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=9",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=9",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(plain),
    ], check=True)
    info = make_post_video(plain, tmp / "plain-out.mp4", cfg, "testcreator")
    check("no-outro clip keeps full length", info["kept"][1] >= 8.9, info)


if __name__ == "__main__":
    random.seed(7)
    cfg = load_config()
    test_urls()
    test_author()
    test_planner(cfg)
    test_caption(cfg)
    test_edit(cfg)
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
