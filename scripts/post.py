"""The daily poster.

Picks a random number of reels for today, picks random times inside your
window, sleeps until each one, and publishes the oldest items in the queue.

The workflow deliberately starts well before the window because GitHub's cron
is routinely 5-20 minutes late; the sleeping happens here, so the actual post
times stay accurate regardless of when the runner picks the job up.

Set DRY_RUN=1 to walk the whole thing without touching Instagram.
"""

import datetime as dt
import os
import pathlib
import random
import time
from zoneinfo import ZoneInfo

import requests

from common import (
    POSTED,
    READY,
    commit_and_push,
    fail,
    list_items,
    load_config,
    log,
    write_item,
)

GRAPH = "https://graph.facebook.com/v21.0"
DRY_RUN = os.environ.get("DRY_RUN") == "1"


def parse_hhmm(value):
    hour, minute = (int(part) for part in str(value).split(":"))
    return dt.time(hour, minute)


def todays_slots(cfg, tz, count):
    """Pick `count` random times inside today's window, at least 4 minutes
    apart so a slow upload can't push one post past the next."""
    now = dt.datetime.now(tz)
    start = dt.datetime.combine(now.date(), parse_hhmm(cfg["window"]["start"]), tz)
    end = dt.datetime.combine(now.date(), parse_hhmm(cfg["window"]["end"]), tz)

    if now >= end:
        log("window already passed (late runner) - posting immediately, spaced 4 min")
        return [now + dt.timedelta(minutes=4 * i) for i in range(count)]

    earliest = max(start, now)
    span = (end - earliest).total_seconds()
    picks = sorted(random.uniform(0, span) for _ in range(count))

    slots, previous = [], None
    for offset in picks:
        moment = earliest + dt.timedelta(seconds=offset)
        if previous and (moment - previous).total_seconds() < 240:
            moment = previous + dt.timedelta(minutes=4)
        slots.append(moment)
        previous = moment
    return slots


def create_container(ig_user_id, token, video_url, caption):
    resp = requests.post(
        f"{GRAPH}/{ig_user_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": token,
        },
        timeout=120,
    )
    body = resp.json()
    if "id" not in body:
        raise RuntimeError(f"container creation failed: {body}")
    return body["id"]


def await_ready(creation_id, token, timeout_s=900):
    """Meta transcodes asynchronously; publishing early just errors."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH}/{creation_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=60,
        ).json()
        status = resp.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"Instagram rejected the video: {resp.get('status')}")
        log(f"    container {status or resp}...")
        time.sleep(15)
    raise RuntimeError("timed out waiting for Instagram to process the video")


def publish(ig_user_id, token, creation_id):
    resp = requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": creation_id, "access_token": token},
        timeout=120,
    ).json()
    if "id" not in resp:
        raise RuntimeError(f"publish failed: {resp}")
    return resp["id"]


def post_one(item, ig_user_id, token):
    log(f"  posting {item['shortcode']} - credit @{item['author']}")
    if DRY_RUN:
        log(f"    DRY RUN: would post {item['asset_url']} / {item['caption']!r}")
        return "dry-run"
    creation_id = create_container(ig_user_id, token, item["asset_url"], item["caption"])
    await_ready(creation_id, token)
    media_id = publish(ig_user_id, token, creation_id)
    log(f"    published, media id {media_id}")
    return media_id


def main():
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])

    ig_user_id = os.environ.get("IG_USER_ID", "")
    token = os.environ.get("IG_ACCESS_TOKEN", "")
    if not DRY_RUN and not (ig_user_id and token):
        fail("IG_USER_ID / IG_ACCESS_TOKEN secrets are not set (or use DRY_RUN=1)")

    ready = list_items(READY)
    if not ready:
        log("nothing ready to post today")
        return

    wanted = random.randint(cfg["posts_per_day"]["min"], cfg["posts_per_day"]["max"])
    count = min(wanted, len(ready))
    if count < wanted:
        log(f"wanted {wanted} today but only {len(ready)} ready - posting {count}")

    slots = todays_slots(cfg, tz, count)
    batch = ready[:count]
    log(f"today: {count} reel(s) at " + ", ".join(s.strftime("%H:%M:%S") for s in slots))

    for item, slot in zip(batch, slots):
        wait = (slot - dt.datetime.now(tz)).total_seconds()
        if wait > 0:
            log(f"sleeping {wait / 60:.1f} min until {slot.strftime('%H:%M:%S')}")
            time.sleep(wait)

        source = pathlib.Path(item["_path"])
        try:
            media_id = post_one(item, ig_user_id, token)
        except Exception as exc:
            log(f"    FAILED: {str(exc)[:300]}")
            item["last_error"] = str(exc)[:500]
            item["post_attempts"] = int(item.get("post_attempts", 0)) + 1
            write_item(source, item)
            commit_and_push(f"post failed: {item['shortcode']}")
            continue

        done = dict(item)
        done.pop("_path", None)
        done.update({
            "ig_media_id": media_id,
            "posted_at": dt.datetime.now(tz).isoformat(),
        })
        write_item(POSTED / source.name, done)
        source.unlink()
        commit_and_push(f"posted {item['shortcode']} (@{item['author']})")

    log("done for today")


if __name__ == "__main__":
    main()
