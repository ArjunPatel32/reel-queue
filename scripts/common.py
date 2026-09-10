"""Shared helpers: config, the file-based queue, and git commits."""

import json
import os
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
READY = ROOT / "ready"
POSTED = ROOT / "posted"


def load_config():
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    """Queue order is oldest-first, which is what 'top of the queue' means."""
    return [read_item(p) for p in sorted(folder.glob("*.json"))]


def repo_slug():
    slug = os.environ.get("GITHUB_REPOSITORY")
    if slug:
        return slug
    url = run(["git", "remote", "get-url", "origin"])
    return url.rstrip("/").removesuffix(".git").split(":")[-1].split("/", 1)[-1]


def commit_and_push(message):
    """Commit any pending changes. Rebases before pushing so the ingest,
    retry and post workflows can't clobber each other's writes."""
    run(["git", "config", "user.name", "reel-queue-bot"], check=False)
    run(["git", "config", "user.email", "bot@users.noreply.github.com"], check=False)
    run(["git", "add", "-A", "queue", "ready", "posted"], check=False)

    status = run(["git", "status", "--porcelain", "queue", "ready", "posted"])
    if not status.strip():
        log("nothing to commit")
        return

    run(["git", "commit", "-m", message])
    for attempt in range(5):
        run(["git", "fetch", "origin"], check=False)
        rebase = subprocess.run(
            ["git", "rebase", "origin/" + current_branch()],
            capture_output=True,
            text=True,
        )
        if rebase.returncode != 0:
            run(["git", "rebase", "--abort"], check=False)
        push = subprocess.run(["git", "push"], capture_output=True, text=True)
        if push.returncode == 0:
            log(f"pushed: {message}")
            return
        log(f"push attempt {attempt + 1} failed, retrying")
    raise RuntimeError("could not push queue state after 5 attempts")


def current_branch():
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or "main"


def log(msg):
    print(msg, flush=True)


def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)
