"""Turn a downloaded reel into the version that gets posted.

  1. Trim a fixed amount off the start/end (config, default 0).
  2. Find and cut the creator's outro - a "follow me" card, a logo slate, the
     TikTok end screen - so the clip ends on the actual content. Credit lives
     in the caption instead.
  3. Shave the edges, reframe to 1080x1920, polish colour/sharpness, even out
     the loudness, and encode to Instagram's preferred format.

Everything here is plain ffmpeg + tesseract on the GitHub runner. Each step
logs what it decided, so a bad cut is easy to trace back from the Actions log.
"""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

from common import log, run

# On-screen words that mark an outro. Matched against OCR text from the tail
# of the clip, and only counted when they DON'T also appear mid-video (a
# watermark that's there the whole time isn't an end card).
OUTRO_WORDS = (
    "follow", "subscribe", "link in bio", "for more", "more videos",
    "part 2", "tiktok", "youtube", "thanks for watching",
)

# Instagram's publishing API limits for reels.
MIN_SECONDS, MAX_SECONDS = 3, 900
MAX_BYTES = 300 * 1_000_000


# --------------------------------------------------------------- inspection


def probe(path):
    out = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, den = (video.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _ffmpeg_log(args):
    """Run an analysis-only ffmpeg pass and return its log output."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *args, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stderr


def scene_cuts(path, threshold=0.3):
    """Timestamps of hard cuts. Run on a small copy of the frames for speed."""
    out = _ffmpeg_log([
        "-i", str(path), "-an",
        "-vf", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
    ])
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", out)]


def trailing_freeze(path, duration):
    """Start of a still frame that runs to the end of the clip, if any."""
    out = _ffmpeg_log([
        "-i", str(path), "-an", "-vf", "scale=320:-2,freezedetect=n=0.003:d=0.7",
    ])
    starts = [float(t) for t in re.findall(r"freeze_start: ([\d.]+)", out)]
    ends = [float(t) for t in re.findall(r"freeze_end: ([\d.]+)", out)]
    if not starts:
        return None
    last = starts[-1]
    # A freeze still running at EOF either never gets an end, or ends at EOF.
    later_ends = [e for e in ends if e > last]
    if not later_ends or later_ends[0] >= duration - 0.3:
        return last
    return None


def ocr_at(path, t, workdir):
    """OCR one frame. Returns lowercase text, or '' if nothing readable."""
    frame = workdir / f"ocr-{t:.2f}.png"
    try:
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=720:-2,format=gray", str(frame),
        ])
        text = run(["tesseract", str(frame), "-", "--psm", "11"], check=False)
    except Exception:
        return ""
    return text.lower()


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def outro_markers(text, author):
    found = {w for w in OUTRO_WORDS if w in text}
    handle = _squash(author or "")
    if len(handle) >= 4 and handle in _squash(text):
        found.add("@" + author)
    return found


# ------------------------------------------------------------ outro finding


def find_outro(path, info, author, cfg, workdir):
    """Return (cut_at_seconds, reason) or (None, reason)."""
    v = cfg["video"]
    duration = info["duration"]
    tail_start = max(duration - float(v.get("outro_max_seconds", 8)), MIN_SECONDS)
    if tail_start >= duration - 0.5:
        return None, "clip too short to have an outro"

    have_ocr = shutil.which("tesseract") is not None
    # Anything already on screen mid-video is a watermark or caption, not an
    # end card - sample a few points and ignore those words later.
    baseline = set()
    if have_ocr:
        for frac in (0.25, 0.5, 0.75):
            t = duration * frac
            if t < tail_start:
                baseline |= outro_markers(ocr_at(path, t, workdir), author)

    cuts = [c for c in scene_cuts(path) if tail_start <= c < duration - 0.3]
    freeze = trailing_freeze(path, duration)

    # A hard cut in the tail whose next shot says "follow"/"@creator" etc.
    if have_ocr:
        for i, cut in enumerate(cuts):
            seg_end = cuts[i + 1] if i + 1 < len(cuts) else duration
            probe_t = min(cut + 0.4, (cut + seg_end) / 2)
            hits = outro_markers(ocr_at(path, probe_t, workdir), author) - baseline
            if hits:
                return cut, f"end card at {cut:.1f}s ({', '.join(sorted(hits))})"

    # A still frame sitting on the end - a logo slate or a held last frame.
    # Cutting a still costs nothing even when it isn't an outro.
    if freeze is not None and freeze >= tail_start and duration - freeze >= 1.0:
        # If a scene cut lands just before the still, the card starts there.
        lead_in = [c for c in cuts if freeze - 1.0 <= c <= freeze]
        cut = lead_in[0] if lead_in else freeze
        return cut, f"still frame from {cut:.1f}s to the end"

    note = "" if have_ocr else " (tesseract missing, text check skipped)"
    return None, "no outro found" + note


# ----------------------------------------------------------------- encoding


def _even(expr):
    return f"trunc({expr}/2)*2"


def video_filter(info, cfg):
    v = cfg["video"]
    w, h = int(v["width"]), int(v["height"])
    crop = float(v.get("crop", 1.0))

    pre = f"crop={_even(f'iw*{crop}')}:{_even(f'ih*{crop}')},setsar=1"
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
    fit = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease"
        f":force_divisible_by=2:flags=lanczos"
    )

    polish = []
    b, c, s = float(v.get("brightness", 0)), float(v.get("contrast", 1)), float(v.get("saturation", 1))
    if (b, c, s) != (0.0, 1.0, 1.0):
        polish.append(f"eq=brightness={b}:contrast={c}:saturation={s}")
    if float(v.get("sharpen", 0)) > 0:
        polish.append(f"unsharp=5:5:{float(v['sharpen'])}:5:5:0")
    polish.append("format=yuv420p")
    polish = ",".join(polish)

    aspect = info["width"] / info["height"]
    if abs(aspect - w / h) / (w / h) < 0.04:
        # Already (near enough) 9:16 - fill the frame, no bars at all.
        return f"[0:v]{pre},{fill},{polish}[v]"

    if v.get("background", "blur") == "blur":
        # Clip centred over a blurred, darkened copy of itself. The blur runs
        # on a 1/4-size copy because full-res blur is slow and looks the same.
        sw, sh = w // 4, h // 4
        return (
            f"[0:v]{pre},split[a][b];"
            f"[a]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"boxblur=12:3,eq=brightness=-0.08,scale={w}:{h}[bg];"
            f"[b]{fit}[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,{polish}[v]"
        )

    return (
        f"[0:v]{pre},{fit},"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,{polish}[v]"
    )


def output_fps(info):
    fps = round(info["fps"])
    return fps if 24 <= fps <= 60 else 30


def render(src, dest, info, start, end, cfg):
    v = cfg["video"]
    fps = output_fps(info)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src),
    ]
    if not info["has_audio"]:
        # Give silent clips a silent track - a missing audio stream is a
        # common reason Instagram rejects an upload.
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    cmd += ["-filter_complex", video_filter(info, cfg), "-map", "[v]"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0"]
        if v.get("loudnorm", True):
            cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
    else:
        cmd += ["-map", "1:a:0", "-shortest"]

    cmd += [
        "-c:v", "libx264", "-preset", "slow", "-crf", str(v.get("crf", 18)),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps * 2),
        "-maxrate", "15M", "-bufsize", "30M",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-use_editlist", "0",
        str(dest),
    ]
    run(cmd)


# --------------------------------------------------------------------- main


def make_post_video(src, dest, cfg, author):
    """Edit `src` into `dest`. Returns a dict describing what was done."""
    v = cfg["video"]
    info = probe(src)
    duration = info["duration"]
    log(f"    source: {info['width']}x{info['height']} @ {info['fps']:.0f}fps, {duration:.1f}s")

    start = min(float(v.get("trim_start", 0)), duration)
    end = duration - float(v.get("trim_end", 0))

    outro_note = "outro removal off"
    if v.get("remove_outro", True):
        with tempfile.TemporaryDirectory() as tmp:
            cut, outro_note = find_outro(src, info, author, cfg, pathlib.Path(tmp))
        if cut is not None:
            end = min(end, cut)
    log(f"    outro: {outro_note}")

    if end - start < MIN_SECONDS:
        # Never let trimming wreck a short clip; fall back to the full length.
        log(f"    trim would leave {end - start:.1f}s - keeping the whole clip instead")
        start, end = 0.0, duration

    render(src, dest, info, start, end, cfg)

    out = probe(dest)
    size = dest.stat().st_size
    log(f"    edited: {out['duration']:.1f}s, {size / 1_000_000:.1f} MB")
    if not MIN_SECONDS <= out["duration"] <= MAX_SECONDS:
        raise RuntimeError(f"edited clip is {out['duration']:.0f}s, outside Instagram's 3s-15min limit")
    if size > MAX_BYTES:
        raise RuntimeError(f"edited clip is {size / 1_000_000:.0f} MB, over Instagram's 300 MB limit")

    return {
        "source_seconds": round(duration, 2),
        "kept": [round(start, 2), round(end, 2)],
        "outro": outro_note,
        "seconds": round(out["duration"], 2),
    }


def contact_sheet(original, edited, dest, kept):
    """A two-row strip of frames, original on top, edited below, sampled at the
    same moments (incl. the tail) - for eyeballing an edit without downloading
    the whole video."""
    o, e = probe(original)["duration"], probe(edited)["duration"]
    start, end = kept
    o_times = [o * 0.1, o * 0.5, max(o - 3, 0), max(o - 1.5, 0), max(o - 0.2, 0)]
    e_times = [min(max(t - start, 0), e - 0.1) for t in o_times[:2]]
    e_times += [max(e - 3, 0), max(e - 1.5, 0), max(e - 0.2, 0)]

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for row, (path, times) in enumerate(((original, o_times), (edited, e_times))):
            for i, t in enumerate(times):
                f = pathlib.Path(tmp) / f"{row}-{i}.png"
                run([
                    "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                    "-frames:v", "1", "-vf",
                    "scale=270:480:force_original_aspect_ratio=decrease,"
                    "pad=270:480:(ow-iw)/2:(oh-ih)/2", str(f),
                ])
                frames.append(f)
        inputs = [arg for f in frames for arg in ("-i", str(f))]
        run([
            "ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
            "[0][1][2][3][4]hstack=5[top];[5][6][7][8][9]hstack=5[bot];"
            "[top][bot]vstack=2", "-q:v", "3", str(dest),
        ])
