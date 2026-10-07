"""Run one reel through download + editing WITHOUT queueing or posting it.

For trying out edit settings: the edited video and a before/after strip of
frames land on the `preview` release, and the links show up on the Actions
run's summary page.
"""

import argparse
import os
import pathlib
import tempfile

from common import build_caption, fail, load_config, log
from download import fetch_and_edit, upload, write_cookies
from edit import contact_sheet
from enqueue import SHARE_LINK, resolve_share_link, shortcode_of

TAG = "preview"


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
    raw, final, author, edit_info = fetch_and_edit(
        f"https://www.instagram.com/reel/{code}/", code, cfg, write_cookies(), workdir
    )

    sheet = workdir / f"{code}-compare.jpg"
    contact_sheet(raw, final, sheet, edit_info["kept"])

    video_url = upload(final, f"{code}.mp4", tag=TAG, title="Edit previews")
    sheet_url = upload(sheet, f"{code}-compare.jpg", tag=TAG, title="Edit previews")
    caption = build_caption(cfg, author)

    lines = [
        f"## Preview: {code}",
        f"- Original poster: **@{author}**",
        f"- Kept {edit_info['kept'][0]}s - {edit_info['kept'][1]}s "
        f"of {edit_info['source_seconds']}s ({edit_info['outro']})",
        f"- Caption: `{caption}`",
        f"- [Edited video]({video_url})",
        f"- [Before/after frames]({sheet_url}) (top: original, bottom: edited)",
    ]
    log("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
