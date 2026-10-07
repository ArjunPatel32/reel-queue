"""Turn a downloaded reel into the version that gets posted.

  1. Trim a fixed amount off the start/end (config, default 0).
  2. Find and cut the creator's outro - a "follow me" card, a logo slate, the
     TikTok end screen - so the clip ends on the actual content. Credit lives
     in the caption instead. Cutting real content is worse than leaving an
     outro in, so every rule here errs towards NOT cutting.
  3. Strip baked-in black bars, reframe to 1080x1920 (blurred fill for clips
     that aren't 9:16), polish colour/sharpness, level the loudness, and
     encode to Instagram's Reels spec.

Everything here is plain ffmpeg + tesseract on the GitHub runner. Each step
logs what it decided, so a bad cut is easy to trace back from the Actions log.
"""

import json
import math
import pathlib
import re
import shutil
import subprocess
import tempfile

from common import log, run

# On-screen words that mark an outro. Matched as whole words against OCR
# text from the tail, and only counted when they DON'T also appear mid-video
# (a watermark that's there the whole time isn't an end card).
OUTRO_WORDS = re.compile(
    r"\b(follow|subscribe|link in bio|more videos|part 2|tiktok|youtube|"
    r"thanks for watching)\b"
)
# Logos from other apps. Instagram won't recommend reels that carry them, so
# they get flagged (not removed - painting over a creator's watermark is
# worse than leaving it).
FOREIGN_MARKS = re.compile(r"\b(tiktok|capcut|youtube|snapchat)\b")

# Instagram's publishing API limits for reels.
MIN_SECONDS, MAX_SECONDS = 3, 900
MAX_BYTES = 300 * 1_000_000
# Below this the audio over a still frame counts as silence.
SILENT_DB = -45.0


class EditError(RuntimeError):
    """This reel can't be turned into something Instagram accepts. Retrying
    the download won't change that."""


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
    duration = float(data["format"]["duration"])
    try:
        video_duration = float(video.get("duration") or duration)
    except ValueError:
        video_duration = duration
    return {
        "duration": duration,
        "video_duration": min(video_duration, duration),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps if fps > 0 else 30.0,
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _ffmpeg_log(args):
    """Run an analysis-only ffmpeg pass and return its log output."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *args, "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stderr


def _analysis_crop(box):
    """Look only at the picture inside letterbox bars, so the bars (which
    never change) don't water down scene-change scores."""
    return "crop={}:{}:{}:{},".format(*box) if box else ""


def scene_cuts(path, threshold=0.3, box=None):
    """Timestamps of hard cuts. Run on a small copy of the frames for speed."""
    out = _ffmpeg_log([
        "-i", str(path), "-an",
        "-vf", f"{_analysis_crop(box)}scale=320:-2,select='gt(scene,{threshold})',showinfo",
    ])
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", out)]


def trailing_freeze(path, duration, box=None):
    """Start of a still frame that runs to the end of the clip, if any."""
    out = _ffmpeg_log([
        "-i", str(path), "-an",
        "-vf", f"{_analysis_crop(box)}scale=320:-2,freezedetect=n=0.001:d=0.7",
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


def mean_volume(path, start, end):
    """Mean loudness (dB) of [start, end], or None if there's no audio."""
    out = _ffmpeg_log([
        "-ss", f"{start:.3f}", "-t", f"{max(end - start, 0.1):.3f}", "-i", str(path),
        "-vn", "-af", "volumedetect",
    ])
    m = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", out)
    if not m:
        return None
    return -math.inf if m.group(1) == "-inf" else float(m.group(1))


def content_box(path, info, start, end):
    """The area of the KEPT part that isn't baked-in black bars, as
    (w, h, x, y), or None when there are no bars worth removing. Looks at
    every 10th frame, so one dark scene can't trick it into over-cropping."""
    out = _ffmpeg_log([
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path), "-an",
        "-vf", "select='not(mod(n,10))',cropdetect=limit=22:round=2:reset=0",
    ])
    boxes = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", out)
    if not boxes:
        return None
    W, H = info["width"], info["height"]
    w, h, x, y = (int(v) for v in boxes[-1])
    barred_w, barred_h = w < W * 0.94, h < H * 0.94
    if not (barred_w or barred_h):
        return None  # only compression noise at the edges
    # Bars run along one axis; the other must be (nearly) full size. A 2.39:1
    # movie clip letterboxed in 9:16 keeps under a quarter of the height, so
    # the barred axis may go as low as 15%.
    if (barred_w and barred_h) or min(w / W, h / H) < 0.15:
        return None
    # Real letterbox/pillarbox bars are the same size on both sides. A dark
    # sky or a black wall is one-sided, so don't crop those.
    if abs(y - (H - y - h)) > H * 0.04 or abs(x - (W - x - w)) > W * 0.04:
        return None
    return w, h, x, y


def ocr_at(path, t, workdir):
    """OCR one frame. Returns lowercase text, or '' if nothing readable.
    Reads it twice: as plain greyscale (dark-on-light, high-contrast cards)
    and with only near-white pixels kept, turned black on white - the usual
    white overlay text (captions, watermarks, end cards) that tesseract
    can't pick out of a busy background otherwise."""
    frame = workdir / f"ocr-{t:.2f}.png"
    white = workdir / f"ocr-{t:.2f}-white.png"
    try:
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=720:-2,format=gray", str(frame),
        ])
        run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(frame),
            "-vf", "lut=c0='if(gt(val,180),0,255)'", str(white),
        ])
        text = " ".join(
            run(["tesseract", str(img), "-", "--psm", "11"], check=False)
            for img in (frame, white)
        )
    except Exception:
        return ""
    return text.lower()


def outro_markers(text, author):
    found = set(OUTRO_WORDS.findall(text))
    handle = (author or "").lower()
    # The handle only counts written as @handle, so a short name can't match
    # ordinary words.
    if len(handle) >= 3 and re.search(r"@\s?" + re.escape(handle) + r"(?![a-z0-9_.])", text):
        found.add("@" + handle)
    return found


def loudness(path, start, end):
    """Measure the kept segment (EBU R128) for one linear gain. Returns
    the loudnorm measurement dict, or None for silence / no audio."""
    out = _ffmpeg_log([
        "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(path), "-vn",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=20:print_format=json",
    ])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        if float(data["input_i"]) < -70:  # digital silence - nothing to level
            return None
        float(data["input_tp"])
    except (KeyError, ValueError):
        return None
    return data


# ------------------------------------------------------------ outro finding


def find_outro(path, info, author, cfg, workdir, box=None):
    """Return (cut_at_seconds, reason, watermarks_seen_mid_video)."""
    v = cfg["video"]
    duration = info["video_duration"]
    tail_start = max(duration - float(v.get("outro_max_seconds", 8)), MIN_SECONDS)

    have_ocr = shutil.which("tesseract") is not None
    # Anything on screen in the body of the clip is a watermark or caption,
    # not an end card. Sample it widely so a bouncing/fading watermark that
    # OCR misses on some frames is still caught on others.
    baseline, mid_text = set(), ""
    if have_ocr:
        body_end = max(min(tail_start, duration - 3), duration * 0.3)
        for i in range(8):
            t = duration * 0.05 + (body_end - duration * 0.05) * i / 7
            text = ocr_at(path, t, workdir)
            mid_text += " " + text
            baseline |= outro_markers(text, author)
    watermarks = sorted(set(FOREIGN_MARKS.findall(mid_text)))

    if tail_start >= duration - 0.5:
        return None, "clip too short to have an outro", watermarks

    cuts = [c for c in scene_cuts(path, box=box) if tail_start <= c < duration - 0.3]
    bounds = cuts + [duration]

    def card_hits(a, b):
        """Outro markers on a segment, probing two frames inside it."""
        hits = set()
        for frac in (0.3, 0.75):
            hits |= outro_markers(ocr_at(path, a + (b - a) * frac, workdir), author)
        return hits - baseline

    # A hard cut in the tail whose next shot says "follow"/"@creator" etc. -
    # but only if what comes after it is card-like too, allowing at most
    # 1.5s of text-free tail (a logo sting). Otherwise the hit was a caption
    # in the middle of real content, not an end card.
    if have_ocr:
        for i, cut in enumerate(cuts):
            hits = card_hits(cut, bounds[i + 1])
            if not hits:
                continue
            later = zip(bounds[i + 1:-1], bounds[i + 2:])
            plain = sum(b - a for a, b in later if not card_hits(a, b))
            if plain <= 1.5:
                return cut, f"end card from {cut:.1f}s ({', '.join(sorted(hits))})", watermarks

    # A still frame on the end. Only an outro if nothing is being said or
    # played over it - a reaction image or a punchline still with audio is
    # content.
    freeze = trailing_freeze(path, duration, box)
    if freeze is not None and freeze >= tail_start and duration - freeze >= 1.0:
        lead_in = [c for c in cuts if freeze - 1.0 <= c <= freeze]
        cut = lead_in[0] if lead_in else freeze
        volume = mean_volume(path, cut, duration) if info["has_audio"] else None
        text_hits = card_hits(cut, duration) if have_ocr else set()
        if volume is None or volume <= SILENT_DB or text_hits:
            why = ", ".join(sorted(text_hits)) or "silent"
            return cut, f"still frame from {cut:.1f}s to the end ({why})", watermarks
        return None, f"still frame at the end kept - audio plays over it ({volume:.0f} dB)", watermarks

    note = "" if have_ocr else " (tesseract missing, text check skipped)"
    return None, "no outro found" + note, watermarks


# ----------------------------------------------------------------- encoding


def _even(expr):
    return f"trunc({expr}/2)*2"


def video_filter(info, cfg, box=None):
    v = cfg["video"]
    w, h = int(v["width"]), int(v["height"])
    crop = float(v.get("crop", 1.0))

    # Work out the picture's real size after bar removal + edge shave.
    src_w, src_h = (box[0], box[1]) if box else (info["width"], info["height"])
    pre = []
    if box:
        pre.append("crop={}:{}:{}:{}".format(*box))
    if crop < 1.0:
        pre.append(f"crop={_even(f'iw*{crop}')}:{_even(f'ih*{crop}')}")
    pre.append("setsar=1")
    pre = ",".join(pre)

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
    polish = ("," + ",".join(polish)) if polish else ""

    aspect = src_w / src_h
    if abs(aspect - w / h) / (w / h) < 0.04:
        # Already (near enough) 9:16 - fill the frame, no bars at all.
        return f"[0:v]{pre},{fill}{polish},format=yuv420p[v]"

    if v.get("background", "blur") == "blur":
        # Clip centred over a blurred, darkened copy of itself. The blur runs
        # on a 1/4-size copy because full-res blur is slow and looks the same;
        # the polish only touches the sharp foreground.
        sw, sh = w // 4, h // 4
        return (
            f"[0:v]{pre},split[a][b];"
            f"[a]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
            f"boxblur=12:3,eq=brightness=-0.10:saturation=0.9,scale={w}:{h},setsar=1[bg];"
            f"[b]{fit}{polish}[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,format=yuv420p[v]"
        )

    return (
        f"[0:v]{pre},{fit}{polish},"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p[v]"
    )


def output_fps(info):
    fps = round(info["fps"])
    return fps if 24 <= fps <= 60 else 30


def audio_filter(measured):
    """One fixed gain to -14 LUFS, capped so peaks stay under -1.5 dBTP.
    Truly linear (loudnorm's own 'linear' mode quietly falls back to dynamic
    gain-riding for most speech), so music never pumps."""
    if not measured:
        return "aresample=48000"
    gain = min(-14.0 - float(measured["input_i"]), -1.5 - float(measured["input_tp"]))
    gain = max(min(gain, 20.0), -30.0)
    return f"volume={gain:.2f}dB,aresample=48000"


def video_bitrate_cap(seconds):
    """Keep the file under Instagram's 300 MB whatever the length: at most
    15 Mbit/s, less for long clips (90% of the budget, minus audio)."""
    budget = 0.9 * MAX_BYTES * 8 / max(seconds, 1) - 128_000
    return int(max(min(15_000_000, budget), 1_000_000))


def render(src, dest, info, start, end, cfg, box=None, measured=None, crf_bump=0):
    v = cfg["video"]
    fps = output_fps(info)
    maxrate = video_bitrate_cap(end - start)
    # Whole milliseconds, rounded DOWN: ffmpeg keeps a frame whose time is
    # below start+t, so rounding up can let the first end-card frame through.
    seconds = math.floor((end - start) * 1000) / 1000
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", str(src),
    ]
    if not info["has_audio"]:
        # A reel with no audio track at all gets a silent one; Instagram is
        # happier with a stream present.
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    cmd += ["-filter_complex", video_filter(info, cfg, box), "-map", "[v]"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0"]
        if v.get("loudnorm", True):
            cmd += ["-af", audio_filter(measured)]
    else:
        cmd += ["-map", "1:a:0", "-shortest"]

    cmd += [
        "-c:v", "libx264", "-preset", "slow", "-crf", str(int(v.get("crf", 18)) + crf_bump),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-r", str(fps), "-g", str(fps * 2),
        "-maxrate", str(maxrate), "-bufsize", str(maxrate * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-use_editlist", "0",
        str(dest),
    ]
    run(cmd)


# --------------------------------------------------------------------- main


def make_post_video(src, dest, cfg, author):
    """Edit `src` into `dest`. Returns a dict describing what was done.
    Raises EditError when the reel can't be made postable at all."""
    v = cfg["video"]
    info = probe(src)
    duration = info["video_duration"]
    log(f"    source: {info['width']}x{info['height']} @ {info['fps']:.0f}fps, {duration:.1f}s"
        + ("" if info["has_audio"] else ", NO AUDIO TRACK"))
    if duration < MIN_SECONDS - 0.05:
        raise EditError(f"source is only {duration:.1f}s - Instagram needs at least {MIN_SECONDS}s")
    if duration > MAX_SECONDS + float(v.get("outro_max_seconds", 8)):
        raise EditError(f"source is {duration / 60:.0f} min - Instagram reels max out at 15 min")

    start = min(float(v.get("trim_start", 0)), duration)
    end = duration - float(v.get("trim_end", 0))

    remove_bars = v.get("remove_bars", True)
    outro_note, watermarks = "outro removal off", []
    if v.get("remove_outro", True):
        # Bars over the whole clip, only to focus the outro analysis; the
        # bars actually removed are measured on the kept part below.
        analysis_box = content_box(src, info, 0, duration) if remove_bars else None
        with tempfile.TemporaryDirectory() as tmp:
            cut, outro_note, watermarks = find_outro(
                src, info, author, cfg, pathlib.Path(tmp), analysis_box)
        if cut is not None:
            # Back off half a frame so the card's first frame can't sneak in.
            end = min(end, cut - 0.5 / info["fps"])
    log(f"    outro: {outro_note}")
    if watermarks:
        log(f"    warning: other-app watermark on screen ({', '.join(watermarks)}) - "
            "Instagram won't recommend this one")

    if end - start < MIN_SECONDS:
        # Never let trimming wreck a short clip; fall back to the full length.
        log(f"    trim would leave {end - start:.1f}s - keeping the whole clip instead")
        start, end = 0.0, duration
    end = min(end, start + MAX_SECONDS)

    box = content_box(src, info, start, end) if remove_bars else None
    if box:
        log(f"    removing black bars: keeping {box[0]}x{box[1]} of {info['width']}x{info['height']}")

    measured = None
    if info["has_audio"] and v.get("loudnorm", True):
        measured = loudness(src, start, end)

    render(src, dest, info, start, end, cfg, box, measured)
    if dest.stat().st_size > MAX_BYTES:
        log("    over 300 MB - re-encoding at lower quality")
        render(src, dest, info, start, end, cfg, box, measured, crf_bump=5)

    out = probe(dest)
    size = dest.stat().st_size
    log(f"    edited: {out['duration']:.1f}s, {size / 1_000_000:.1f} MB")
    if not MIN_SECONDS - 0.05 <= out["duration"] <= MAX_SECONDS + 1:
        raise EditError(f"edited clip is {out['duration']:.1f}s, outside Instagram's 3s-15min limit")
    if size > MAX_BYTES:
        raise EditError(f"edited clip is {size / 1_000_000:.0f} MB, over Instagram's 300 MB limit")

    return {
        "source_seconds": round(duration, 2),
        "kept": [round(start, 3), round(end, 3)],
        "outro": outro_note,
        "bars_removed": bool(box),
        "has_audio": info["has_audio"],
        "foreign_watermark": watermarks,
        "seconds": round(out["duration"], 2),
    }


def contact_sheet(original, edited, dest, kept):
    """A two-row strip of frames, original on top, edited below, sampled at the
    same moments (incl. the tail) - for eyeballing an edit without downloading
    the whole video."""
    o_info, e_info = probe(original), probe(edited)
    o = o_info["video_duration"] - 1.5 / o_info["fps"]
    e = e_info["video_duration"] - 1.5 / e_info["fps"]
    start, _ = kept
    o_times = [o * 0.1, o * 0.5, max(o - 3, 0), max(o - 1.5, 0), max(o, 0)]
    e_times = [min(max(t - start, 0), e) for t in o_times[:2]]
    e_times += [max(e - 3, 0), max(e - 1.5, 0), max(e, 0)]

    with tempfile.TemporaryDirectory() as tmp:
        frames = []
        for row, (path, times) in enumerate(((original, o_times), (edited, e_times))):
            for i, t in enumerate(times):
                f = pathlib.Path(tmp) / f"{row}-{i}.png"
                for attempt_t in (t, max(t - 0.5, 0), 0):
                    run([
                        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{attempt_t:.2f}",
                        "-i", str(path), "-frames:v", "1", "-vf",
                        "scale=270:480:force_original_aspect_ratio=decrease,"
                        "pad=270:480:(ow-iw)/2:(oh-ih)/2", str(f),
                    ], check=False)
                    if f.exists():
                        break
                frames.append(f)
        inputs = [arg for f in frames for arg in ("-i", str(f))]
        run([
            "ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
            "[0][1][2][3][4]hstack=5[top];[5][6][7][8][9]hstack=5[bot];"
            "[top][bot]vstack=2", "-q:v", "3", str(dest),
        ])
