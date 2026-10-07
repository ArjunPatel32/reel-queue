# Progress log

Running record of what's done and what's next, so we can pick this up cold.

**Status (2026-10-07): code rewritten and pushed; first real test runs in
progress. Still no Shortcut, no Meta secrets, nothing posted.**

---

## Decisions already made (don't re-litigate these)

- **Everything runs on GitHub Actions**, not Arjun's PC — his desktop is only on
  when gaming, he mostly uses a work computer, so a local scheduler was ruled out.
- **Zero budget.** No paid scraper API, no VPS, no object storage. Every piece
  had to have a free path, and does.
- **Repo must be public** — unlimited free Actions minutes. Secrets stay
  encrypted either way.
- **The repo is the queue.** Files in `queue/` → `ready/` → `posted/`, plus
  `state/plan.json` for today's posting plan. No database, no server.
- **Video storage = GitHub Releases** (tag `media`; edit previews on tag
  `preview`).
- **Download at share time, not post time.** Instagram blocks datacenter IPs,
  so downloads are flaky on Actions runners; a 4-hourly retry cron plus days of
  queue buffer absorbs it. The poster only posts already-downloaded files.
- **No Meta App Review needed** — the app stays in Development mode.
- **Cookies: try cookieless FIRST**, burner account only if needed, never the
  posting account.
- **(2026-10-07) Posting rules, from Arjun:** strict queue, oldest first.
  Random 1–3 a day (avg 2). While MORE than 10 are waiting (queue/ + ready/),
  2–4 a day until it's back to 10. Config: `posts_per_day`, `backlog`.
- **(2026-10-07) Editing, from Arjun:** trim start/end, brighten/enhance
  colour and quality, and make sure the creator's outro/"tribute" card at the
  end is cut. Credit goes in the caption instead. "We can experiment" — use
  the Preview edit workflow to try settings.
- **(2026-10-07) Caption:** simple credit only — `🎥 @{author}`, plus an
  optional fixed `hashtags` line (empty for now; Arjun hasn't picked any).
- **(2026-10-07) Poster runs hourly**, not once a day: GitHub's cron fired the
  old 14:15 UTC daily job at 17:15–21:30 UTC every day (3–7 h late) for weeks.
  An hourly job against a daily plan keeps posts within ~1 h of plan. Posts are
  spread across a 10:00–20:00 Pacific window (`window` in config.yml).
- **(2026-10-07) Resumable upload** to Meta (we send the bytes) instead of
  handing Meta the GitHub release URL, which redirects and is served as
  application/octet-stream.

## Known risk, accepted

Reposting others' reels is against IG's ToS regardless of credit. Realistic
downside is reach-limiting or account action, not legal trouble. Arjun's call;
build on an account he'd be OK losing.

---

## Bugs found and fixed on 2026-10-07

Found by reading the Actions history — none of the old code had ever worked:

1. **`ingest.yml` and `retry.yml` were invalid YAML** (an unquoted
   `"ingest: queue reel"` in a `run:` line). GitHub refused to load them, so the
   Shortcut could never have worked and the retry cron never ran. Now caught by
   `check.yml` (actionlint).
2. **Credit would have been a number.** yt-dlp's Instagram `uploader_id` is the
   numeric account id; the @handle is `channel` (verified in yt-dlp's
   extractor source). Captions would have read `@2815873`.
3. **yt-dlp needs curl-cffi** for logged-out Instagram — the logged-out
   GraphQL path only runs when browser impersonation is available.
4. **Ingest's concurrency group would drop reels**: GitHub keeps one pending
   run per group and cancels the rest, so sharing 3 reels quickly lost one.
5. **Daily cron 3–7 h late** (see decisions).
6. `instagram.com/<user>/reel/CODE` and `/share/` links weren't recognised.

## Done

- [x] Architecture, config, scripts, workflows, SETUP/README (2026-09-09)
- [x] git + gh installed, authed as ArjunPatel32, public repo pushed, Actions
      write permission set, fine-grained PAT `reel-queue-shortcut` created
      (value NOT stored here — repo is public; rotate it, it was pasted in chat)
- [x] (2026-10-07) All six bugs above fixed and pushed (commit 9265db6)
- [x] (2026-10-07) `scripts/edit.py`: trim, outro detection (scene cuts in the
      last 8 s + OCR for "follow"/"@creator"/etc. that isn't also on screen
      mid-video + trailing still frame), crop 3%, 9:16 fill or blur-background,
      eq brightness/contrast/saturation, unsharp, loudnorm, x264 crf 18
- [x] (2026-10-07) `post.py` rewritten: hourly, daily plan in state/plan.json,
      backlog mode, resumable upload, dry_run + post_now buttons, doesn't fail
      (no hourly emails) while secrets are unset
- [x] (2026-10-07) `preview.yml` — edit one reel without queueing it; links on
      the run summary, incl. a before/after frame strip
- [x] (2026-10-07) `check.yml` + `scripts/selftest.py` — actionlint + offline
      tests incl. a synthetic clip with a fake "Follow @testcreator" end card

## Not done — pick up here

**Milestone A — prove download + editing work on GitHub's servers**

1. [ ] check.yml green (first run in progress at time of writing)
2. [ ] Preview run on a real public reel (DQwMwTLEvbn) — does a logged-out
       download work from a runner? Is `channel` the right handle? Does the
       edit look good? (in progress at time of writing)
3. [ ] If logged-out download fails: burner account cookies → `IG_COOKIES`.

**Milestone B — share button**

4. [ ] **[Arjun] Build the iPhone Shortcut** — SETUP.md step 7.
5. [ ] Share a reel, confirm it reaches `ready/` with the right `author`.

**Milestone C — make it actually post (~40 min)**

6. [ ] **[Arjun]** IG → Professional, Meta app, tokens (SETUP.md steps 2–4).
7. [ ] **[Claude]** Set `IG_USER_ID` and `IG_ACCESS_TOKEN` secrets.
8. [ ] Dry run, then `post_now` for the first real post.

## Things that have NOT been verified

- The resumable-upload posting path has never run against Meta (needs tokens).
- Graph API version bumped to v24.0 in post.py — confirm against the research
  results / Meta's changelog.
- Outro detection on real reels (only tested on a synthetic clip so far).

---

## Log

**2026-09-09** — Designed the system, settled the free/Actions-only approach,
wrote all code and docs. Nothing deployed.

**2026-09-10** — Timezone set to San Francisco. Installed git + gh, authed as
ArjunPatel32, created and pushed the public repo, set Actions write permissions,
created the phone's PAT. Stopped before building the Shortcut.

**2026-10-07** — Arjun asked again for the full thing (share → edit → credit →
post). Found the workflows had never run (YAML bug), the credit field was
wrong, and the daily cron was hours late. Arjun set the posting rules and
editing goals (see decisions). Rewrote editing + posting, added preview and
self-test workflows, pushed, started live tests.
