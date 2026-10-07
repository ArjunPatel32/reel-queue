"""Shared helpers: config, the file-based queue, and git commits."""

import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
READY = ROOT / "ready"
POSTED = ROOT / "posted"
STATE = ROOT / "state"
TRACKED = ("queue", "ready", "posted", "state")
# Edited videos wait on this GitHub release until they're posted.
RELEASE_TAG = "media"


def load_config():
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_caption(cfg, author):
    caption = cfg["caption_template"].format(author=author)
    if (cfg.get("hashtags") or "").strip():
        caption += "\n\n" + cfg["hashtags"].strip()
    return caption


def run(cmd, check=True, capture=True):
    """Run a command, returning stdout. Raises on non-zero exit when check."""
    proc = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        out = (proc.stdout or "") + (proc.stderr or "")
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{out}")
    return (proc.stdout or "").strip()


def read_item(path):
    with open(path, encoding="utf-8") as f:
        item = json.load(f)
    item["_path"] = str(path)
    return item


def write_item(path, item):
    item = {k: v for k, v in item.items() if not k.startswith("_")}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(item, f, indent=2, ensure_ascii=False)
        f.write("\n")


def list_items(folder):
    """Queue order is oldest-first, which is what 'top of the queue' means.
    A file that isn't valid JSON (e.g. a typo from editing it by hand on
    GitHub) is skipped with a loud warning instead of crashing every run."""
    items = []
    for p in sorted(folder.glob("*.json")):
        try:
            items.append(read_item(p))
        except (ValueError, OSError) as exc:
            log(f"WARNING: skipping {folder.name}/{p.name} - not valid JSON ({exc})")
    return items


def shortcodes_in(*folders):
    """Shortcodes present in these folders: from each file's NAME
    (<timestamp>-<shortcode>.json), which works even if the file is broken,
    plus its `shortcode` field (which differs once a share link resolves)."""
    codes = set()
    for folder in folders:
        for p in folder.glob("*.json"):
            codes.add(p.stem.split("-", 1)[-1])
            try:
                codes.add(json.loads(p.read_text(encoding="utf-8"))["shortcode"])
            except (ValueError, OSError, KeyError, TypeError):
                pass
    return codes


def repo_slug():
    slug = os.environ.get("GITHUB_REPOSITORY")
    if slug:
        return slug
    url = run(["git", "remote", "get-url", "origin"])
    return url.rstrip("/").removesuffix(".git").split(":")[-1].split("/", 1)[-1]


def sync():
    """Throw away the checkout and match the latest pushed state. Only call
    this when everything local has already been pushed."""
    branch = current_branch()
    run(["git", "fetch", "origin", branch])
    run(["git", "reset", "--hard", f"origin/{branch}"])


def _rebase_in_progress():
    git_dir = ROOT / ".git"
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def _resolve_rebase_conflicts():
    """Settle conflicts while replaying our commit on top of newer work.
    Policy: for files both sides changed, our (newer) write wins; when one
    side deleted a file the other changed, the deletion wins - a deletion
    here always means the item moved on (queue -> ready -> posted) or the
    owner removed it on purpose. Returns True once the rebase is finished."""
    for _ in range(20):
        if not _rebase_in_progress():
            return True
        status = run(["git", "status", "--porcelain"], check=False)
        unmerged = [
            (line[:2], line[3:]) for line in status.splitlines()
            if line[:2] in ("UU", "AA", "UD", "DU", "DD", "AU", "UA")
        ]
        for code, path in unmerged:
            if code in ("UU", "AA", "AU", "UA"):
                # During a rebase "theirs" is the commit being replayed: ours.
                run(["git", "checkout", "--theirs", "--", path], check=False)
                run(["git", "add", "--", path], check=False)
            else:
                run(["git", "rm", "-q", "--", path], check=False)
        proc = subprocess.run(
            ["git", "-c", "core.editor=true", "rebase", "--continue"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 and _rebase_in_progress() and not unmerged:
            # Resolution left nothing to commit (upstream already had it).
            subprocess.run(["git", "rebase", "--skip"], capture_output=True, text=True)
    return not _rebase_in_progress()


def commit_and_push(message):
    """Commit any pending changes and push them, rebasing onto whatever the
    other workflows pushed meanwhile. Retries with backoff for a few minutes,
    because losing this write (e.g. a post's receipt) is worse than waiting."""
    run(["git", "config", "user.name", "reel-queue-bot"], check=False)
    run(["git", "config", "user.email", "bot@users.noreply.github.com"], check=False)
    # git add refuses the whole command if any path is missing.
    tracked = [d for d in TRACKED if (ROOT / d).exists()]
    run(["git", "add", "-A", "--", *tracked], check=False)

    status = run(["git", "status", "--porcelain", "--", *tracked])
    if not status.strip():
        log("nothing to commit")
        return

    run(["git", "commit", "-q", "-m", message])
    branch = current_branch()
    delays = (0, 5, 15, 30, 60, 90, 120)
    for attempt, delay in enumerate(delays, 1):
        time.sleep(delay)
        run(["git", "fetch", "origin", branch], check=False)
        rebase = subprocess.run(
            ["git", "rebase", f"origin/{branch}"], capture_output=True, text=True
        )
        if rebase.returncode != 0 and not _resolve_rebase_conflicts():
            run(["git", "rebase", "--abort"], check=False)
            log(f"  rebase failed: {(rebase.stdout + rebase.stderr).strip()[-300:]}")
        push = subprocess.run(["git", "push", "origin", f"HEAD:{branch}"],
                              capture_output=True, text=True)
        if push.returncode == 0:
            log(f"pushed: {message}")
            return
        log(f"push attempt {attempt} failed: {push.stderr.strip()[-200:]}")
    raise RuntimeError(f"could not push queue state after {len(delays)} attempts")


def current_branch():
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=False)
    return branch if branch and branch != "HEAD" else "main"


def log(msg):
    print(msg, flush=True)


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)
