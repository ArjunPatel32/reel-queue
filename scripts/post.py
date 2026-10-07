"""The poster. Runs every hour.

Once a day (the first run after local midnight) it plans the day: how many
reels to post and at what times, saved to state/plan.json. Every run then
posts whatever slots fall due before the next run, sleeping until each one,
always taking the oldest ready reel first.

Why hourly instead of one morning run: GitHub's cron is routinely HOURS late
(this repo's daily job fired 3-7h behind schedule for weeks). Hourly runs are
just as late individually, but there's usually one along within the hour, and
a run that comes late catches up on anything overdue.

Safety rules this file is built around:
  - Before every post it re-syncs with the latest pushed state, so a reel the
    owner deleted/skipped meanwhile, or one another run already posted, is
    never posted (again).
  - The container id is committed BEFORE publishing. If a run dies or the
    publish call times out, the next attempt asks Meta whether that container
    already went live instead of posting a second copy.
  - The release asset is deleted only after the receipt is pushed.
  - The access token travels in a header and is scrubbed from any error text
    that gets logged or committed (this repo is public).

Manual runs (Actions -> Post reels -> Run workflow):
  dry_run   - plan and walk through without touching Instagram or the repo
  post_now  - post the oldest ready reel right now, ignoring the plan
"""

import datetime as dt
import json
import os
import pathlib
import random
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

import requests

from common import (
    POSTED,
    QUEUE,
    READY,
    RELEASE_TAG,
    STATE,
    build_caption,
    commit_and_push,
    fail,
    list_items,
    load_config,
    log,
    shortcodes_in,
    sync,
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
# Never post two reels closer together than this within one run.
MIN_GAP = dt.timedelta(minutes=30)
# How often to retry a slot whose post failed (bad token, Meta outage...).
SLOT_ATTEMPTS = 3
# Skip a reel after Instagram rejects the video this many times...
MAX_REJECTIONS = 3
# ...or after this many failed attempts of any kind, so one bad reel can
# never block the whole queue.
MAX_POST_ATTEMPTS = 10
# Hold a slot (rather than skip ahead) while an OLDER shared reel is still
# downloading, but only for this long - strict oldest-first, within reason.
HOLD_FOR_OLDER = dt.timedelta(hours=24)


# ------------------------------------------------------------------ secrets

_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")


def scrub(text):
    """Error text with the access token blanked out - safe to log or commit."""
    text = str(text)
    return text.replace(_TOKEN, "***") if _TOKEN else text


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
        try:
            return json.loads(PLAN.read_text(encoding="utf-8"))
        except ValueError:
            log("WARNING: state/plan.json is not valid JSON - making a fresh plan")
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
        missed = [s for s in plan.get("slots", []) if s["status"] == "pending"]
        if missed:
            log(f"{len(missed)} slot(s) from {plan.get('date')} never ran - dropped")

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

_session = requests.Session()
_session.headers["Authorization"] = f"Bearer {_TOKEN}"


class VideoRejected(RuntimeError):
    """Instagram refused this particular video - retrying won't help much."""


class PermanentItemError(RuntimeError):
    """This reel can never be posted as it stands (e.g. its video is gone)."""


def _check(resp, what):
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:300]}
    if resp.status_code >= 400 or "error" in body:
        raise RuntimeError(scrub(f"{what} failed ({resp.status_code}): {body}"))
    return body


def graph_get(path, **params):
    try:
        resp = _session.get(f"{GRAPH}/{path}", params=params, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(scrub(f"GET {path}: {exc}")) from None
    return _check(resp, f"GET {path}")


def graph_post(path, **data):
    try:
        resp = _session.post(f"{GRAPH}/{path}", data=data, timeout=120)
    except requests.RequestException as exc:
        raise RuntimeError(scrub(f"POST {path}: {exc}")) from None
    return _check(resp, f"POST {path}")


def check_token(ig_user_id):
    """Cheap daily canary: fails loudly (an Actions failure email) the day the
    token stops working, instead of on the next post attempt."""
    body = graph_get(ig_user_id, fields="username")
    log(f"token OK - posting as @{body.get('username')}")


def create_container(ig_user_id, caption):
    """Resumable upload: we send Meta the file bytes directly, so it never has
    to fetch a URL (GitHub's release links are blocked by robots.txt, redirect,
    and are served as application/octet-stream)."""
    body = graph_post(
        f"{ig_user_id}/media",
        media_type="REELS",
        upload_type="resumable",
        caption=caption,
        share_to_feed="true",
    )
    return body["id"]


def upload_bytes(creation_id, path):
    size = path.stat().st_size
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"{RUPLOAD}/{creation_id}",
                headers={
                    "Authorization": f"OAuth {_TOKEN}",
                    "offset": "0",
                    "file_size": str(size),
                    "Content-Type": "application/octet-stream",
                },
                data=f,
                timeout=600,
            )
    except requests.RequestException as exc:
        raise RuntimeError(scrub(f"video upload: {exc}")) from None
    body = _check(resp, "video upload")
    # Meta's documented failure body is {"debug_info": {...}} with no
    # "success" key at all, so only an explicit success counts.
    if body.get("success") is not True or "debug_info" in body:
        raise RuntimeError(scrub(f"video upload failed: {body}"))


def container_status(creation_id):
    body = graph_get(creation_id, fields="status_code,status")
    return body.get("status_code"), body.get("status")


def await_ready(creation_id, timeout_s=900):
    """Meta transcodes asynchronously; publishing early just errors."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, detail = container_status(creation_id)
        if status in ("FINISHED", "PUBLISHED"):
            return status
        if status in ("ERROR", "EXPIRED"):
            raise VideoRejected(f"Instagram rejected the video: {status} {detail}")
        log(f"    processing ({status})...")
        time.sleep(15)
    raise RuntimeError("timed out waiting for Instagram to process the video")


def copyright_check(creation_id):
    """Meta's own copyright scan of the container - logged, not acted on."""
    try:
        status = graph_get(creation_id, fields="copyright_check_status").get(
            "copyright_check_status")
    except RuntimeError:
        return None
    if status:
        log(f"    copyright check: {status}")
    return status


def publish(ig_user_id, creation_id):
    """Publish, and settle ambiguity: if the call errors or times out, ask
    the container whether it went live anyway before calling it a failure."""
    try:
        return graph_post(f"{ig_user_id}/media_publish", creation_id=creation_id)["id"]
    except RuntimeError as exc:
        time.sleep(10)
        try:
            status, _ = container_status(creation_id)
        except RuntimeError:
            status = None
        if status == "PUBLISHED":
            log("    publish call errored, but the container is PUBLISHED - counting it")
            return None
        raise exc


def download_asset(url):
    path = pathlib.Path(tempfile.mkdtemp(prefix="post-")) / "reel.mp4"
    with requests.get(url, stream=True, timeout=300) as resp:
        if resp.status_code == 404:
            raise PermanentItemError(f"the edited video is gone from the release ({url})")
        resp.raise_for_status()
        with open(path, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
    return path


# --------------------------------------------------------------- the queue


def next_ready(now):
    """The reel to post next, or (None, reason). Strict oldest-first: if an
    older reel is still waiting for its download (and was shared within the
    last day), hold the slot for it instead of skipping ahead."""
    ready = [i for i in list_items(READY) if not i.get("skip")]
    posted = shortcodes_in(POSTED)
    for item in list(ready):
        if item["shortcode"] in posted:
            # Shared twice: one copy already went out.
            log(f"  {item['shortcode']} was already posted - retiring the duplicate")
            if not DRY_RUN:
                retire(item, "duplicate of an already-posted reel")
            ready.remove(item)
    if not ready:
        return None, "nothing ready to post"

    head = ready[0]
    head_name = pathlib.Path(head["_path"]).name
    for pending in list_items(QUEUE):
        if pending.get("gave_up") or pathlib.Path(pending["_path"]).name > head_name:
            continue
        try:
            added = dt.datetime.fromisoformat(pending["added_at"])
        except (KeyError, ValueError):
            continue
        if now - added < HOLD_FOR_OLDER:
            return None, (f"holding for older reel {pending['shortcode']} "
                          "(still downloading)")
    return head, None


def retire(item, reason):
    """Move a ready item out of the queue without posting it."""
    source = pathlib.Path(item["_path"])
    done = dict(item)
    done.pop("_path", None)
    done.update({"skipped": reason, "ig_media_id": None})
    write_item(POSTED / source.name, done)
    source.unlink()


def delete_asset(item):
    """Free the release slot once a reel is posted (a release holds at most
    1000 files) and stop hosting someone else's video publicly."""
    asset = item["asset_url"].rsplit("/", 1)[-1]
    proc = subprocess.run(
        ["gh", "release", "delete-asset", RELEASE_TAG, asset, "--yes"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        log(f"    (couldn't delete release asset {asset}: {proc.stderr.strip()[:200]})")


def post_one(item, cfg, ig_user_id):
    """Publish one reel. Returns the media id (None if it's known to be live
    but the id couldn't be read back)."""
    caption = build_caption(cfg, item["author"])
    log(f"  posting {item['shortcode']} - caption {caption!r}")
    if DRY_RUN:
        log(f"    DRY RUN: would upload {item['asset_url']}")
        return "dry-run"

    source = pathlib.Path(item["_path"])
    creation_id = item.get("creation_id")
    if creation_id:
        # A previous attempt got this far. Find out what became of it. If
        # Meta can't be asked right now, fail and try later rather than risk
        # posting a second copy.
        status, _ = container_status(creation_id)
        log(f"    earlier container {creation_id} is {status}")
        if status == "PUBLISHED":
            return None
        if status in ("FINISHED", "IN_PROGRESS"):
            await_ready(creation_id)
            return publish(ig_user_id, creation_id)
        creation_id = None  # expired/errored - start over

    video = download_asset(item["asset_url"])
    creation_id = create_container(ig_user_id, caption)
    upload_bytes(creation_id, video)
    await_ready(creation_id)
    item["copyright_check"] = copyright_check(creation_id)

    # Point of no return: record the container before publishing, so a crash
    # or an ambiguous error can be reconciled instead of double-posted.
    item["creation_id"] = creation_id
    write_item(source, item)
    commit_and_push(f"publishing {item['shortcode']}")

    media_id = publish(ig_user_id, creation_id)
    log(f"    published, media id {media_id}")
    return media_id


def post_next(cfg, ig_user_id, tz):
    """Post the oldest ready reel. Returns "posted", "empty" or "held".
    Raises on failure after recording it on the item. Syncs first and
    commits everything it changes."""
    if not DRY_RUN:
        sync()
    now = dt.datetime.now(tz)
    item, why_not = next_ready(now)
    if not item:
        log(f"  {why_not}")
        if not DRY_RUN:
            commit_and_push("retire duplicates")
        return "held" if why_not.startswith("holding") else "empty"

    source = pathlib.Path(item["_path"])
    try:
        media_id = post_one(item, cfg, ig_user_id)
    except Exception as exc:
        message = scrub(exc)
        log(f"    FAILED: {message[:400]}")
        if DRY_RUN:
            raise
        if source.exists():
            item = {**json.loads(source.read_text(encoding="utf-8")), "_path": str(source)}
        item["last_error"] = message[:500]
        item["post_attempts"] = int(item.get("post_attempts", 0)) + 1
        if isinstance(exc, VideoRejected):
            item["rejections"] = int(item.get("rejections", 0)) + 1
            item.pop("creation_id", None)
        reason = None
        if isinstance(exc, PermanentItemError):
            reason = str(exc)
        elif int(item.get("rejections", 0)) >= MAX_REJECTIONS:
            reason = f"Instagram rejected the video {item['rejections']} times"
        elif item["post_attempts"] >= MAX_POST_ATTEMPTS:
            reason = f"failed {item['post_attempts']} times"
        if reason:
            item["skip"], item["skip_reason"] = True, reason
            log(f"    skipping this reel from now on: {reason}")
        write_item(source, item)
        commit_and_push(f"post failed - {item['shortcode']}")
        raise RuntimeError(message) from None

    if DRY_RUN:
        return "posted"
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
    commit_and_push(f"posted {item['shortcode']} (@{item['author']})")
    delete_asset(item)  # only once the receipt is safely pushed
    return "posted"


# --------------------------------------------------------------------- main


def main():
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])

    ig_user_id = os.environ.get("IG_USER_ID", "")
    if not DRY_RUN and not (ig_user_id and _TOKEN):
        if not ig_user_id and not _TOKEN:
            # Not set up yet. Exit cleanly so an hourly job doesn't send a
            # failure email every hour until SETUP.md step 5 is done.
            log("IG_USER_ID / IG_ACCESS_TOKEN not set yet - nothing to do (see SETUP.md)")
            return
        fail("only one of IG_USER_ID / IG_ACCESS_TOKEN is set - add the other")

    if not DRY_RUN:
        sync()

    if POST_NOW:
        log("post_now: posting the oldest ready reel immediately")
        try:
            post_next(cfg, ig_user_id, tz)
        except RuntimeError as exc:
            fail(str(exc)[:300])
        return

    now = dt.datetime.now(tz)
    plan, is_new = todays_plan(cfg, now)
    if is_new and not DRY_RUN:
        check_token(ig_user_id)
        save_plan(plan)
        commit_and_push(f"plan {plan['date']}")

    horizon = now + HORIZON
    due = [
        s["at"] for s in plan["slots"]
        if s["status"] == "pending" and dt.datetime.fromisoformat(s["at"]) <= horizon
    ]
    if not due:
        upcoming = [s["at"][11:16] for s in plan["slots"] if s["status"] == "pending"]
        log(f"nothing due this hour (still to come today: {', '.join(upcoming) or 'none'})")
        return

    failed = False
    last_post = None
    for slot_at in due:
        at = dt.datetime.fromisoformat(slot_at)
        # When cron runs were dropped and several slots are overdue, don't
        # fire them back to back - keep them MIN_GAP apart.
        if last_post is not None:
            at = max(at, last_post + MIN_GAP)
        wait = (at - dt.datetime.now(tz)).total_seconds()
        if wait > 0 and not DRY_RUN:
            log(f"sleeping {wait / 60:.1f} min until {at.strftime('%H:%M:%S')}")
            time.sleep(wait)

        # post_next re-syncs, so re-read the slot from the fresh plan: another
        # run may have handled it while this one slept.
        try:
            outcome = post_next(cfg, ig_user_id, tz)
            error = None
        except Exception as exc:
            outcome, error = "error", scrub(exc)
            failed = True

        plan = load_plan() or plan
        slot = next((s for s in plan["slots"] if s["at"] == slot_at), None)
        if slot is None or slot["status"] != "pending":
            continue
        if outcome == "posted":
            slot["status"] = "posted"
            last_post = dt.datetime.now(tz)
        elif outcome == "empty":
            slot["status"] = "empty"
        elif outcome == "error":
            slot["attempts"] += 1
            slot["error"] = error[:300]
            if slot["attempts"] >= SLOT_ATTEMPTS:
                slot["status"] = "failed"
            # Otherwise leave it pending - the next hourly run retries it.
        # "held": leave it pending for the older reel to finish downloading.

        if not DRY_RUN:
            save_plan(plan)
            commit_and_push(f"slot {slot_at[11:16]} {slot['status']}")

    if failed:
        fail("one or more posts failed - see the log above")


if __name__ == "__main__":
    main()
