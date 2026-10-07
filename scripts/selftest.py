"""Offline checks - no Instagram, no Meta. Run by check.yml on every code
change, and safe to run by hand: python scripts/selftest.py

Covers URL parsing, credit extraction, the daily planner, queue safety
(duplicates, holds, broken files, git conflicts) and full edits of
synthetic clips: a "follow @creator" end card, a persistent watermark with
fast cuts, a reaction still with audio, a silent logo slate, letterboxes.
"""

import datetime as dt
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import common
from common import build_caption, list_items, load_config, shortcodes_in
from download import credited_in, poster_of
from enqueue import shortcode_of
import post

failures = []
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def sh(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


# ------------------------------------------------------------- parsing


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
    check("poster uses channel, not numeric id", poster_of(info) == "naomipq", poster_of(info))
    check("poster none without channel", poster_of({"uploader_id": "2815873", "uploader": "Name"}) is None)

    credits = {
        "so good 🔥 credit: @realcreator #funny": "realcreator",
        "🎥: @some.one": "some.one",
        "🎥 by @creator": "creator",
        "©️ @creator": "creator",
        "©️: @creator": "creator",
        "creds: @creator": "creator",
        "cred @creator": "creator",
        "Video: @creator": "creator",
        "Credits to @abc_def": "abc_def",
        "via @orig.": "orig",
        "Reel by @august_thegingercat": "august_thegingercat",
        "audio cr: @singer\n🎥 cr: @creator": "creator",
        "music by @dj_x": None,           # a musician, not the creator
        "song credit: @dj_artist": None,
        "music credits @dj_artist": None,
        "beat made by @producer": None,
        "Shop my outfit via @shop.ltk": None,
        "edit by @friend": None,          # bare "by" is not trusted
        "credit @poster": None,           # crediting yourself isn't a credit
        "great day with @friend 😂": None,
        "": None,
    }
    for text, want in credits.items():
        got = credited_in(text, "poster")
        check(f"credit in {text[:32]!r} -> {want}", got == want, got)


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


def test_scrub():
    post._TOKEN = "EAAsecret123"
    msg = post.scrub("Max retries exceeded with url: /v24.0/1?access_token=EAAsecret123 (Caused")
    check("token scrubbed from error text", "EAAsecret123" not in msg and "***" in msg, msg)
    post._TOKEN = ""


def test_audio_and_bitrate():
    from edit import audio_filter, video_bitrate_cap
    check("quiet speech: gain limited by peak", audio_filter(
        {"input_i": "-20", "input_tp": "-3"}) == "volume=1.50dB,aresample=48000")
    check("loud master: turned down", audio_filter(
        {"input_i": "-8", "input_tp": "0.5"}) == "volume=-6.00dB,aresample=48000")
    check("no measurement: untouched", audio_filter(None) == "aresample=48000")
    for secs in (30, 160, 600, 900):
        rate = video_bitrate_cap(secs)
        size = (rate + 128_000) * secs / 8
        check(f"bitrate cap keeps {secs}s under 300MB", size < 300_000_000, (rate, size))


# --------------------------------------------------------- queue safety


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_queue_files():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selftest-q-"))
    q, r, p = tmp / "queue", tmp / "ready", tmp / "posted"
    write_json(r / "20261001T100000-AAA.json", {"shortcode": "AAA"})
    (r / "20261001T110000-BBB.json").parent.mkdir(parents=True, exist_ok=True)
    (r / "20261001T110000-BBB.json").write_text('{"shortcode": "BBB" "skip": true}')  # typo
    write_json(q / "20261001T120000-share-xyz.json", {"shortcode": "CCC", "needs_resolve": False})
    items = list_items(r)
    check("broken JSON file is skipped, not fatal", [i["shortcode"] for i in items] == ["AAA"], items)
    codes = shortcodes_in(q, r, p)
    check("dedupe sees names AND resolved shortcodes, even in broken files",
          {"AAA", "BBB", "share-xyz", "CCC"} <= codes, codes)


def test_next_ready():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selftest-n-"))
    post.QUEUE, post.READY, post.POSTED = tmp / "queue", tmp / "ready", tmp / "posted"
    for d in (post.QUEUE, post.READY, post.POSTED):
        d.mkdir()
    tz = ZoneInfo("America/Los_Angeles")
    now = dt.datetime(2026, 10, 7, 12, 0, tzinfo=tz)

    write_json(post.POSTED / "20261001T090000-DUP.json", {"shortcode": "DUP"})
    write_json(post.READY / "20261001T100000-DUP.json", {"shortcode": "DUP", "asset_url": "x/DUP.mp4"})
    write_json(post.READY / "20261002T100000-NEW.json", {"shortcode": "NEW", "asset_url": "x/NEW.mp4"})
    item, why = post.next_ready(now)
    check("already-posted duplicate is retired, not posted",
          item and item["shortcode"] == "NEW" and not (post.READY / "20261001T100000-DUP.json").exists(),
          (item, why))

    older = (now - dt.timedelta(hours=2)).astimezone(dt.timezone.utc)
    write_json(post.QUEUE / "20261001T120000-OLD.json",
               {"shortcode": "OLD", "added_at": older.isoformat()})
    item, why = post.next_ready(now)
    check("holds the slot for an older reel still downloading", item is None and "OLD" in (why or ""),
          (item, why))

    stale = (now - dt.timedelta(days=3)).astimezone(dt.timezone.utc)
    write_json(post.QUEUE / "20261001T120000-OLD.json",
               {"shortcode": "OLD", "added_at": stale.isoformat()})
    item, why = post.next_ready(now)
    check("stops holding after a day", item and item["shortcode"] == "NEW", (item, why))
    post.QUEUE, post.READY, post.POSTED = common.QUEUE, common.READY, common.POSTED


def test_git_conflicts():
    """commit_and_push must survive the other workflows having pushed
    conflicting changes meanwhile: our edit wins a both-modified file, and
    a deletion wins over a modification."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selftest-git-"))
    remote, a, b = tmp / "remote.git", tmp / "a", tmp / "b"
    sh("git", "init", "-q", "--bare", "-b", "main", str(remote))
    sh("git", "clone", "-q", str(remote), str(a))
    for repo in (a,):
        sh("git", "config", "user.email", "t@t", cwd=repo)
        sh("git", "config", "user.name", "t", cwd=repo)
    write_json(a / "ready" / "X.json", {"v": 1})
    write_json(a / "ready" / "Y.json", {"v": 1})
    write_json(a / "state" / "plan.json", {"v": 1})
    sh("git", "add", "-A", cwd=a)
    sh("git", "commit", "-q", "-m", "base", cwd=a)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=a)
    sh("git", "clone", "-q", str(remote), str(b))
    sh("git", "config", "user.email", "t@t", cwd=b)
    sh("git", "config", "user.name", "t", cwd=b)

    # Someone else pushes: edits plan.json, deletes X, edits Y.
    write_json(a / "state" / "plan.json", {"v": "theirs"})
    (a / "ready" / "X.json").unlink()
    write_json(a / "ready" / "Y.json", {"v": "theirs"})
    sh("git", "add", "-A", cwd=a)
    sh("git", "commit", "-q", "-m", "other", cwd=a)
    sh("git", "push", "-q", "origin", "HEAD:main", cwd=a)

    # We (stale) edit plan.json, edit X, delete Y - then commit_and_push.
    write_json(b / "state" / "plan.json", {"v": "ours"})
    write_json(b / "ready" / "X.json", {"v": "ours"})
    (b / "ready" / "Y.json").unlink()
    old_root, old_cwd = common.ROOT, os.getcwd()
    common.ROOT = b
    os.chdir(b)
    try:
        common.commit_and_push("ours")
        pushed = True
    except Exception as exc:
        pushed = str(exc)
    finally:
        common.ROOT = old_root
        os.chdir(old_cwd)
    check("conflicting push still lands", pushed is True, pushed)
    sh("git", "pull", "-q", "origin", "main", cwd=a)
    plan = json.loads((a / "state" / "plan.json").read_text())
    check("both-modified file: our newer write wins", plan == {"v": "ours"}, plan)
    check("modify vs delete: deletion wins (X)", not (a / "ready" / "X.json").exists())
    check("modify vs delete: deletion wins (Y)", not (a / "ready" / "Y.json").exists())


# --------------------------------------------------------------- editing


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def text(t, size=90, y="(h-text_h)/2"):
    return (f"drawtext=fontfile={FONT}:text='{t}':fontsize={size}:fontcolor=white:"
            f"borderw=4:bordercolor=black:x=(w-text_w)/2:y={y}")


def clip_with_card(path):
    """8s of moving 16:9 test pattern with a tone, then a 3s card that says
    'Follow @testcreator' - all letterboxed inside a 9:16 frame with black
    bars, the way a lot of reposted landscape clips arrive."""
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8",
        "-f", "lavfi", "-i", f"color=c=0x203040:size=1280x720:rate=30:duration=3,{text('Follow @testcreator')}",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=11",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0,scale=1080:608,pad=1080:1920:0:656:black[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )


def clip_watermarked(path):
    """14s, 9:16, a 'TikTok @someuser' watermark on screen the whole time and
    fast cuts in the tail - nothing here is an outro."""
    parts = []
    for i, src in enumerate(("testsrc2", "smptebars", "rgbtestsrc", "testsrc2", "smptebars")):
        parts += ["-f", "lavfi", "-i", f"{src}=size=1080x1920:rate=30:duration={(6, 2, 2, 2, 2)[i]}"]
    ffmpeg(
        *parts, "-f", "lavfi", "-i", "sine=frequency=300:duration=14",
        "-filter_complex",
        "[0:v][1:v][2:v][3:v][4:v]concat=n=5:v=1:a=0," + text("TikTok @someuser", 70, "200") + "[v]",
        "-map", "[v]", "-map", "5:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )


def clip_still_ending(path, silent):
    """8s of motion then a 3s still frame. With audio over the still it's a
    reaction/punchline (keep); with silence it's a dead slate (cut)."""
    tone = ("sine=frequency=500:duration=8,apad=whole_dur=11" if silent
            else "sine=frequency=500:duration=11")
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=8",
        "-f", "lavfi", "-i", "smptebars=size=1080x1920:rate=30:duration=3",
        "-f", "lavfi", "-i", tone,
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )


def clip_cinemascope(path):
    """A 2.39:1 movie-style picture letterboxed into 9:16."""
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1080x452:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
        "-vf", "pad=1080:1920:0:734:black",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )


def test_edit(cfg):
    if not shutil.which("ffmpeg"):
        print("skip edit tests (no ffmpeg)")
        return
    from edit import contact_sheet, loudness, make_post_video, probe

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="selftest-"))

    src, out = tmp / "card.mp4", tmp / "card-out.mp4"
    clip_with_card(src)
    info = make_post_video(src, out, cfg, "testcreator")
    o = probe(out)
    check("output is 1080x1920", (o["width"], o["height"]) == (1080, 1920), o)
    check("end card cut (kept ~8s of 11s)", 7.5 <= o["duration"] <= 8.1, info)
    check("cut reason is the card's text", "follow" in info["outro"], info["outro"])
    check("card found by its scene cut through the letterbox", "end card" in info["outro"], info["outro"])
    check("output has audio", o["has_audio"])
    check("letterbox bars removed", info["bars_removed"], info)
    sheet = tmp / "sheet.jpg"
    contact_sheet(src, out, sheet, info["kept"])
    check("contact sheet written", sheet.exists() and sheet.stat().st_size > 10_000)
    keep = pathlib.Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    shutil.copy(sheet, keep / "selftest-sheet.jpg")

    src = tmp / "wm.mp4"
    clip_watermarked(src)
    info = make_post_video(src, tmp / "wm-out.mp4", cfg, "someuser")
    check("persistent watermark + fast tail cuts: nothing cut", info["kept"][1] >= 13.9, info)
    check("other-app watermark flagged", "tiktok" in info["foreign_watermark"], info)

    src = tmp / "reaction.mp4"
    clip_still_ending(src, silent=False)
    info = make_post_video(src, tmp / "reaction-out.mp4", cfg, "someone")
    check("still with audio over it is kept", info["kept"][1] >= 10.9, info)

    src = tmp / "slate.mp4"
    clip_still_ending(src, silent=True)
    info = make_post_video(src, tmp / "slate-out.mp4", cfg, "someone")
    check("silent still at the end is cut", 7.5 <= info["kept"][1] <= 8.1, info)

    src = tmp / "scope.mp4"
    clip_cinemascope(src)
    info = make_post_video(src, tmp / "scope-out.mp4", cfg, "someone")
    check("2.39:1 letterbox bars removed", info["bars_removed"], info)

    plain = tmp / "plain.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=9",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=9",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(plain),
    )
    info = make_post_video(plain, tmp / "plain-out.mp4", cfg, "testcreator")
    check("no-outro clip keeps full length", info["kept"][1] >= 8.9, info)
    check("full-frame clip not cropped", not info["bars_removed"], info)
    measured = loudness(plain, 0, 9)
    check("loudness measured", bool(measured and "input_i" in measured), measured)


if __name__ == "__main__":
    random.seed(7)
    cfg = load_config()
    test_urls()
    test_author()
    test_planner(cfg)
    test_caption(cfg)
    test_scrub()
    test_audio_and_bitrate()
    test_queue_files()
    test_next_ready()
    test_git_conflicts()
    test_edit(cfg)
    print(f"\n{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
