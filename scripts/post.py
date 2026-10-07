"""The poster. Runs every hour.

Once a day (the first run after local midnight) it plans the day: how many
reels to post and at what times, saved to state/plan.json. Every run then
posts whatever slots fall due before the next run, sleeping until each one,
always taking the oldest ready reel first.

Why hourly instead of one morning run: GitHub's cron is routinely HOURS late
(this repo's daily job fired 3-7h behind schedule for weeks). Hourly runs are
just as late individually, but there's always one along within the hour, so
the planned times hold to within about an hour no matter how late any single
run is.

Manual runs (Actions -> Post reels -> Run workflow):
  dry_run   - plan and walk through without touching Instagram or the repo
  post_now  - post the oldest ready reel right now, ignoring the plan
"""

import datetime as dt
import json
import os
import pathlib
import random
import tempfile
import time
from zoneinfo import ZoneInfo

import requests

from common import (
    POSTED,
    QUEUE,
    READY,
    STATE,
    build_caption,
    commit_and_push,
    fail,
    list_items,
    load_config,
    log,
    write_item,
)

GRAPH_VERSION = "v24.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
POST_NOW = os.environ.get("POST_NOW") == "1"
PLAN = STATE / "plan.json"

# Post anything due before the next hourly run gets here.
HORIZON = dt.timedelta(minutes=58)
# How often to retry a slot whose post failed (bad token, Meta outage...).
SLOT_ATTEMPTS = 3
# How often to retry one reel Instagram rejects before skipping it for good.
ITEM_ATTEMPTS = 3


# ------------------------------------------------------------------ planning


def parse_hhmm(value):
    hour, minute = (int(part) for part in str(value).split(":"))
    return dt.time(hour, minute)


def waiting_count():
    """Everything shared but not posted yet - what 'the queue' means to you."""
    pending = [i for i in list_items(QUEUE) if not i.get("gave_up")]
    ready = [i for i in list_items(READY) if not i.get("skip")]
    return len(pending) + len(ready)


def count_range(cfg, waiting):
    """(min, max) posts for today: the backlog range while more than
    `threshold` reels are waiting, the normal range otherwise."""
    backlog = cfg.get("backlog") or {}
    if backlog and waiting > int(backlog["threshold"]):
        return int(backlog["min"]), int(backlog["max"])
    return int(cfg["posts_per_day"]["min"]), int(cfg["posts_per_day"]["max"])


def pick_times(cfg, now, count):
    """One random time in each equal slice of the window, so posts are spread
    out rather than bunched. A margin inside each slice keeps neighbours at
    least ~half an hour apart."""
    tz = now.tzinfo
    start = dt.datetime.combine(now.date(), parse_hhmm(cfg["window"]["start"]), tz)
    end = dt.datetime.combine(now.date(), parse_hhmm(cfg["window"]["end"]), tz)
    if count <= 0:
        return []
    if now >= end:
        log("planning after the window closed (very late runner) - spacing posts from now")
        return [now + dt.timedelta(minutes=5 + 45 * i) for i in range(count)]

    start = max(start, now)
    slice_s = (end - start).total_seconds() / count
    margin = min(15 * 60, slice_s / 4)
    return [
        start + dt.timedelta(seconds=i * slice_s + random.uniform(margin, slice_s - margin))
        for i in range(count)
    ]


def load_plan():
    if PLAN.exists():
        return json.loads(PLAN.read_text(encoding="utf-8"))
    return None


def save_plan(plan):
    PLAN.parent.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


def todays_plan(cfg, now):
    plan = load_plan()
    today = now.date().isoformat()
    if plan and plan.get("date") == today:
        return plan, False

    if plan:
        missed = [s for s in plan["slots"] if s["status"] == "pending"]
        if missed:
            log(f"{len(missed)} slot(s) from {plan['date']} never ran - dropped")

    waiting = waiting_count()
    low, high = count_range(cfg, waiting)
    count = random.randint(low, high)
    log(f"{waiting} reel(s) waiting -> {low}-{high} today, picked {count}")
    plan = {
        "date": today,
        "slots": [
            {"at": t.isoformat(timespec="seconds"), "status": "pending", "attempts": 0}
            for t in pick_times(cfg, now, count)
        ],
    }
    log(f"plan for {today}: {count} post(s) at "
        + ", ".join(dt.datetime.fromisoformat(s["at"]).strftime("%H:%M") for s in plan["slots"]))
    return plan, True


# ---------------------------------------------------------------- Graph API


def _check(resp, what):
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    if resp.status_code >= 400 or "error" in body:
        raise RuntimeError(f"{what} failed ({resp.status_code}): {body}")
    return body


def create_container(ig_user_id, token, caption):
    """Resumable upload: we send Meta the file bytes directly, so it never has
    to fetch a URL (GitHub's release links redirect and are served as
    application/octet-stream, which Meta's fetcher is picky about)."""
    body = _check(requests.post(
        f"{GRAPH}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true",
            "access_token": token,
        },
        timeout=120,
    ), "container creation")
    return body["id"]


def upload_bytes(creation_id, token, path):
    size = path.stat().st_size
    with open(path, "rb") as f:
        body = _check(requests.post(
            f"{RUPLOAD}/{creation_id}",
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(size),
            },
            data=f,
            timeout=600,
        ), "video upload")
    if not body.get("success", True):
        raise RuntimeError(f"video upload failed: {body}")


def await_ready(creation_id, token, timeout_s=900):
    """Meta transcodes asynchronously; publishing early just errors."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = _check(requests.get(
            f"{GRAPH}/{creation_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=60,
        ), "status check")
        status = body.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise VideoRejected(f"Instagram rejected the video: {body.get('status')}")
        log(f"    processing ({status})...")
        time.sleep(15)
    raise RuntimeError("timed out waiting for Instagram to process the video")


def publish(ig_user_id, token, creation_id):
    body = _check(requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=120,
    ), "publish")
    return body["id"]


class VideoRejected(RuntimeError):
    """Instagram refused this particular video - retrying won't help."""


def download_asset(url):
    path = pathlib.Path(tempfile.mkdtemp(prefix="post-")) / "reel.mp4"
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    return path


def post_one(item, cfg, ig_user_id, token):
    caption = build_caption(cfg, item["author"])
    log(f"  posting {item['shortcode']} - caption {caption!r}")
    if DRY_RUN:
        log(f"    DRY RUN: would upload {item['asset_url']}")
        return "dry-run"
    video = download_asset(item["asset_url"])
    creation_id = create_container(ig_user_id, token, caption)
    upload_bytes(creation_id, token, video)
    await_ready(creation_id, token)
    media_id = publish(ig_user_id, token, creation_id)
    log(f"    published, media id {media_id}")
    return media_id


# --------------------------------------------------------------------- main


def next_ready():
    for item in list_items(READY):
        if not item.get("skip"):
            return item
    return None


def post_next(cfg, ig_user_id, token, tz):
    """Post the oldest ready reel. Returns True on success, False when there
    was nothing to post, raises on failure (after recording it on the item).
    The caller commits."""
    item = next_ready()
    if not item:
        log("  nothing ready to post")
        return False

    source = pathlib.Path(item["_path"])
    try:
        media_id = post_one(item, cfg, ig_user_id, token)
    except Exception as exc:
        log(f"    FAILED: {str(exc)[:400]}")
        if DRY_RUN:
            raise
        item["last_error"] = str(exc)[:500]
        item["post_attempts"] = int(item.get("post_attempts", 0)) + 1
        if isinstance(exc, VideoRejected) and item["post_attempts"] >= ITEM_ATTEMPTS:
            item["skip"] = True
            log("    Instagram keeps rejecting this one - skipping it from now on")
        write_item(source, item)
        raise

    if DRY_RUN:
        return True
    done = dict(item)
    done.pop("_path", None)
    done.update({
        "caption": build_caption(cfg, item["author"]),
        "ig_media_id": media_id,
        "posted_at": dt.datetime.now(tz).isoformat(timespec="seconds"),
    })
    write_item(POSTED / source.name, done)
    source.unlink()
    log(f"    moved to posted/ (@{item['author']})")
    return True


def main():
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])

    ig_user_id = os.environ.get("IG_USER_ID", "")
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not DRY_RUN and not (ig_user_id and token):
        if not ig_user_id and not token:
            # Not set up yet. Exit cleanly so an hourly job doesn't send a
            # failure email every hour until SETUP.md step 4 is done.
            log("IG_USER_ID / IG_ACCESS_TOKEN not set yet - nothing to do (see SETUP.md)")
            return
        fail("only one of IG_USER_ID / IG_ACCESS_TOKEN is set - add the other")

    if POST_NOW:
        log("post_now: posting the oldest ready reel immediately")
        try:
            post_next(cfg, ig_user_id, token, tz)
        finally:
            if not DRY_RUN:
                commit_and_push("post now")
        return

    now = dt.datetime.now(tz)
    plan, is_new = todays_plan(cfg, now)
    if is_new and not DRY_RUN:
        save_plan(plan)
        commit_and_push(f"plan {plan['date']}")

    horizon = now + HORIZON
    due = [
        s for s in plan["slots"]
        if s["status"] == "pending" and dt.datetime.fromisoformat(s["at"]) <= horizon
    ]
    if not due:
        upcoming = [s["at"][11:16] for s in plan["slots"] if s["status"] == "pending"]
        log(f"nothing due this hour (still to come today: {', '.join(upcoming) or 'none'})")
        return

    failed = False
    for slot in due:
        at = dt.datetime.fromisoformat(slot["at"])
        wait = (at - dt.datetime.now(tz)).total_seconds()
        if wait > 0 and not DRY_RUN:
            log(f"sleeping {wait / 60:.1f} min until {at.strftime('%H:%M:%S')}")
            time.sleep(wait)

        try:
            posted = post_next(cfg, ig_user_id, token, tz)
            slot["status"] = "posted" if posted else "empty"
        except Exception as exc:
            failed = True
            slot["attempts"] += 1
            slot["error"] = str(exc)[:300]
            if slot["attempts"] >= SLOT_ATTEMPTS:
                slot["status"] = "failed"
            # Leave it pending otherwise - the next hourly run retries it.

        if not DRY_RUN:
            save_plan(plan)
            commit_and_push(f"slot {slot['at'][11:16]} {slot['status']}")

    if failed:
        fail("one or more posts failed - see the log above")


if __name__ == "__main__":
    main()
