# Progress log

Running record of what's done and what's next, so we can pick this up cold.

**Status: repo live at https://github.com/ArjunPatel32/reel-queue. Code deployed,
still untested — no secrets set, no Shortcut built, nothing has ever run.**

---

## Decisions already made (don't re-litigate these)

- **Everything runs on GitHub Actions**, not Arjun's PC — his desktop is only on
  when gaming, he mostly uses a work computer, so a local scheduler was ruled out.
- **Zero budget.** No paid scraper API, no VPS, no object storage. Every piece
  had to have a free path, and does.
- **Repo must be public** — unlimited free Actions minutes. Private gets 2,000/mo
  and the sleeping post job would burn through it. Secrets stay encrypted either
  way.
- **The repo is the queue.** Files in `queue/` → `ready/` → `posted/`. No
  database, no server.
- **Video hosting = GitHub Releases** (tag `media`). Meta's servers need a public
  HTTPS URL to fetch the video from; a release asset on a public repo is one, free.
- **Download at share time, not post time.** Instagram blocks datacenter IPs, so
  downloads are flaky on Actions runners; a 4-hourly retry cron plus days of
  queue buffer absorbs it. The morning job only posts already-downloaded files.
- **No Meta App Review needed** — the app stays in Development mode, which has
  full access to your own accounts. Review is only for posting to other people's.
- **Token strategy:** long-lived user token → derive the *Page* token from it,
  which never expires. Avoids the 60-day refresh treadmill.
- **Downloader uses a burner IG account's cookies**, not the posting account —
  it logs in from datacenter IPs and could get flagged.

## Known risk, accepted

Reposting others' reels is against IG's ToS regardless of credit. Realistic
downside is reach-limiting or account action, not legal trouble. Arjun's call;
build on an account he'd be OK losing.

---

## Done

- [x] Whole architecture designed and agreed
- [x] `config.yml` — timezone, window, 1–3/day, caption template, encode settings
- [x] `scripts/common.py` — config loading, file queue, git commit w/ rebase-retry
- [x] `scripts/enqueue.py` — URL → `queue/` item, dedupes across all three folders
- [x] `scripts/download.py` — yt-dlp → ffmpeg → release upload → `ready/`, with
      per-item error capture so one bad reel can't block the rest
- [x] `scripts/post.py` — picks N, picks random times, sleeps, Graph API publish,
      `DRY_RUN=1` supported
- [x] `scripts/commit.py` — commit helper for the workflows
- [x] `.github/workflows/ingest.yml` — `repository_dispatch` from the Shortcut
- [x] `.github/workflows/retry.yml` — every 4h
- [x] `.github/workflows/post.yml` — daily cron at 11:45 UTC, sleeps to window
- [x] `SETUP.md` — full click-through: repo, IG Professional, Meta app, tokens,
      cookies, secrets, the iPhone Shortcut
- [x] `README.md` — how it works, daily use, troubleshooting
- [x] Timezone set to `America/Los_Angeles` (San Francisco); `post.yml` cron
      moved to 14:15 UTC = 07:15 PDT / 06:15 PST, ~2h ahead of the 09:30 window
- [x] git 2.55 + gh CLI 2.100 installed via winget; git identity configured
- [x] `gh auth login` as **ArjunPatel32**
- [x] Public repo created and pushed: https://github.com/ArjunPatel32/reel-queue
- [x] Actions default workflow permissions set to `write` via API (this is the
      "Read and write permissions" setting — already done, don't redo it)

## Not done — pick up here

**Milestone A — get the share button working (~20 min left)**

1. **[Arjun] Make a burner IG account** for the downloader cookies. Agreed: not
   his posting account (a personal-ish public account he cares about), not his
   main personal one either.
2. **[Arjun] Export cookies.txt** from that burner via the "Get cookies.txt
   LOCALLY" browser extension — SETUP.md step 5.
3. **[Claude] Set the `IG_COOKIES` secret** with `gh secret set`.
4. **[Arjun] Build the iPhone Shortcut** — SETUP.md step 7. Needs a fine-grained
   PAT scoped to just this repo with Contents: read+write.
5. **[together] Test ingest** — share a reel, watch the Actions run, confirm it
   reaches `ready/` with the right `author` field.

**Milestone B — make it actually post (~40 min)**

6. **[Arjun] IG account → Professional, create + link a Facebook Page** — step 2.
7. **[Arjun] Meta developer app**, keep it in Development mode — step 3.
8. **[Arjun] Mint tokens + get `IG_USER_ID`** — step 4. Fiddliest part.
9. **[Claude] Set `IG_USER_ID` and `IG_ACCESS_TOKEN` secrets.**
10. **[together] Dry run**, then the first real post.

## Open questions

- Does he want the ffmpeg cleanup at all, or straight reposts? Currently does a
  3% crop + re-encode + pad to 1080×1920. Easy to turn off (`crop: 1.0`).

## Things that have NOT been verified

- **No code has ever run.** There's no Python on the Windows machine, so nothing
  has even been syntax-checked — first execution will be on the GitHub runner.
  Expect to fix a couple of things on the first real run; that's normal.
- The exact yt-dlp metadata field holding the original username is unconfirmed.
  `download.py` tries `uploader_id`, `uploader`, `channel_id`, `channel` in
  order and errors if none look like a username. **Watch this on the first
  ingest** — the caption is the whole point.
- Meta's dashboard UI shifts around; SETUP.md step 3's exact menu names may have
  drifted since writing.

---

## Log

**2026-09-09** — Designed the system, settled the free/Actions-only approach,
wrote all code and docs. Nothing deployed.

**2026-09-10** — Timezone set to San Francisco. Installed git + gh, authed as
ArjunPatel32, created and pushed the public repo, set Actions write permissions.
Stopped at: needs a burner IG account + cookies, then the iPhone Shortcut.
