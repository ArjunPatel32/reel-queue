# Progress log

Running record of what's done and what's next, so we can pick this up cold.

**Status: code written, nothing deployed or tested yet.**

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

## Not done — pick up here

1. **[Arjun] Create the public GitHub repo and push** — SETUP.md step 1,
   including the *Read and write permissions* setting for Actions.
2. **[Arjun] Convert the IG account to Professional + create/link an FB Page** —
   step 2.
3. **[Arjun] Meta developer app** — step 3.
4. **[Arjun] Mint the tokens, get `IG_USER_ID`** — step 4. Fiddliest part; ask
   Claude to walk through it live if the responses look wrong.
5. **[Arjun] Burner account cookies** — step 5.
6. **[Arjun] Add the three repo secrets** — step 6.
7. **[Arjun] Build the iPhone Shortcut** — step 7.
8. **[together] Set the real timezone in `config.yml`** — currently defaults to
   `America/New_York`, never confirmed with Arjun. Also re-check the UTC cron in
   `post.yml` matches whatever timezone/window ends up being used.
9. **[together] End-to-end test** — queue 2–3 reels, confirm ingest goes green
   and items reach `ready/`, then run *Post reels* with dry run checked.
10. **[together] First real post.**

## Open questions

- **What timezone is Arjun in?** Never asked. `config.yml` and the `post.yml`
  cron both assume US Eastern.
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
wrote all code and docs. Nothing deployed. Next session starts at "Not done" #1.
