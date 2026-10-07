"""Run one reel through download + editing WITHOUT queueing or posting it.

For trying out edit settings: the edited video and a before/after strip of
frames land on the `preview` release, and the links show up on the Actions
run's summary page.
"""

import argparse
import json
import os
import pathlib
import subprocess
import tempfile

from common import build_caption, fail, load_config, log
from download import fetch_and_edit, upload, write_cookies
from edit import contact_sheet
from enqueue import SHARE_LINK, resolve_share_link, shortcode_of

TAG = "preview"
# Keep the newest previews (two files each); older ones are deleted so the
# release doesn't grow forever (a release holds at most 1000 files).
KEEP_FILES = 20


def prune_previews():
    proc = subprocess.run(
        ["gh", "release", "view", TAG, "--json", "assets"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return
    assets = sorted(json.loads(proc.stdout)["assets"], key=lambda a: a["createdAt"])
    for asset in assets[:-KEEP_FILES]:
        subprocess.run(["gh", "release", "delete-asset", TAG, asset["name"], "--yes"],
                       capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    args = ap.parse_args()

    url = args.url.strip()
    code = shortcode_of(url)
    if not code and SHARE_LINK.search(url):
        code = shortcode_of(resolve_share_link(url) or "")
    if not code:
        fail(f"not an Instagram reel/post URL: {url!r}")

    cfg = load_config()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="preview-"))
    log(f"--- {code} (preview)")
    raw, final, meta, edit_info = fetch_and_edit(
        f"https://www.instagram.com/reel/{code}/", code, cfg, write_cookies(), workdir
    )
    author = meta["author"]

    # The video goes up first; a failure drawing the frame strip mustn't
    # throw away a finished edit.
    staged = final.parent / f"{code}.mp4"
    video_url = upload(final, staged.name, tag=TAG, title="Edit previews")
    sheet = workdir / f"{code}-compare.jpg"
    try:
        contact_sheet(raw, staged, sheet, edit_info["kept"])
        sheet_url = upload(sheet, sheet.name, tag=TAG, title="Edit previews")
    except Exception as exc:
        log(f"(couldn't draw the before/after strip: {str(exc)[-200:]})")
        sheet_url = None
    prune_previews()
    caption = build_caption(cfg, author)

    credit = f"**@{author}**"
    if meta["poster"] != author:
        credit += f" (posted by @{meta['poster']}, who credits @{author})"
    lines = [
        f"## Preview: {code}",
        f"- Credit: {credit}",
        f"- Kept {edit_info['kept'][0]}s - {edit_info['kept'][1]}s "
        f"of {edit_info['source_seconds']}s ({edit_info['outro']})",
        f"- Black bars removed: {'yes' if edit_info['bars_removed'] else 'no'}",
        f"- Caption: `{caption}`",
        f"- [Edited video]({video_url})",
    ]
    if sheet_url:
        lines.append(f"- [Before/after frames]({sheet_url}) (top: original, bottom: edited)")
    if not edit_info["has_audio"]:
        lines.append("- ⚠️ No audio track - the queue would park this one instead of posting it")
    if edit_info["foreign_watermark"]:
        lines.append(f"- ⚠️ Other-app watermark on screen ({', '.join(edit_info['foreign_watermark'])}) "
                     "- Instagram won't recommend it")
    log("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
