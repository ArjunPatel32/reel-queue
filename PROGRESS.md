# Progress log

Running record of what's done and what's next, so we can pick this up cold.

**Status (2026-10-07, end of session): everything up to posting is BUILT AND
PROVEN on GitHub's servers — a real ingest run queued, downloaded (logged-out),
credited, edited and staged a reel; the poster planned the day and dry-ran
correctly; 81/81 self-tests pass. Waiting on Arjun for the iPhone Shortcut
(SETUP step 2) and the Meta setup (steps 3–6). Nothing posted yet; queue is
empty (the test reel was removed).**

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
  handing Meta the GitHub release URL. Verified: github.com/robots.txt has
  `Disallow: /*/download`, release links 302 to a signed URL, and they're
  served as application/octet-stream — three reasons Meta's fetcher would
  reject them. Resumable upload is **Facebook-Login-only**, so the Meta app
  must use the "API setup with Facebook login" setup, not Instagram login
  (an app can only have one). SETUP.md step 4 says this.
- **(2026-10-07) Shortcut uses workflow_dispatch** on ingest.yml with an
  **Actions-only** fine-grained PAT (can't touch code), not repository_dispatch
  with a Contents token. The old Contents PAT should be deleted.
- **(2026-10-07) Credit:** `channel` from yt-dlp = the poster. If the poster's
  caption credits someone else ("credit: @x", "via @x", "🎥 @x", "video by
  @x"), that account is credited instead. Bare "by @x" is NOT trusted (music
  by / edit by).
- **(2026-10-07) Crop off** (was 3%) — it cost picture and doesn't defeat
  Instagram's matching. Symmetric baked-in black bars ARE removed (cropdetect).
- **(2026-10-07) Reels with no audio** are parked in ready/ with skip=true
  (usually licensed music that didn't download) instead of posted muted.
- **(2026-10-07) GitHub Models is retired** (2026-07-30) — no free LLM in
  Actions. Moot for now since Arjun chose simple credit captions.

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

## Code review (2026-10-07)

An adversarial review workflow (4 reviewers + 4 skeptic verifiers) found 33
issues, 28 confirmed real; all 28 fixed in commit 4c1d3ef. The design rules
that came out of it — keep them when changing code:

- **Never put the IG token in a URL**; scrub it from any text that's logged
  or committed (`post.scrub`). The repo is public.
- **Sync before acting**: checkouts use `ref: github.ref_name` (branch tip),
  post.py `sync()`s before every post. Runs that waited in a concurrency
  queue otherwise act on the commit from when they were triggered.
- **Commit the container id before media_publish**; next attempt checks
  `status_code == PUBLISHED` before posting again. Delete the release asset
  only after the receipt is pushed.
- `commit_and_push` resolves rebase conflicts: newer write wins a
  both-modified file; deletion beats modification. Retries ~5 min.
- One bad reel must never block the queue: duplicates retired, 404 asset →
  skip, attempt caps, permanent edit errors (`EditError`) give up at once.
- A share must never be lost: committed before the slow install; unresolved
  /share/ links queued as `share-<id>` with `needs_resolve`.
- Outro cutting errs towards NOT cutting: needs text not seen in an 8-frame
  baseline, whole-word/@handle matches, and ≤1.5 s of text-free tail after;
  a trailing still is only cut when silent or carrying outro text.

## Research findings worth not re-deriving (2026-10-07)

- Graph API: v26.0 current; post.py uses **v24.0, valid until 2028-02-18**.
  v21.0 dies 2027-01-21. Calls to dead versions get silently upgraded.
- Reels spec: MP4, no edit lists, moov first, H.264/HEVC closed GOP 4:2:0,
  23–60 fps, AAC ≤48 kHz 128k, 3 s–15 min, ≤300 MB. edit.py matches.
- Publishing cap 50–100 posts/24 h (Meta's docs disagree), 400 containers/24 h.
- yt-dlp logged-out Instagram **requires curl-cffi** (browser impersonation;
  Instagram 429s plain HTTP/1.1). Extractor reworked 2026-06-28.
  Logged-out quality ceiling is ~1080x1920 VP9 @1.7 Mbps + 48 kbps audio.
- **Instagram originality policy:** accounts reposting others' content 10+
  times in 30 days aren't recommended (followers-only reach); reposts get
  replaced by the original in recommendations. Crop/colour/credit/subtitles
  are "low-effort" and don't count. Own commentary/voiceover/on-screen
  context does. Native Repost button (Aug 2025) has no API. Told Arjun.
- GitHub cron: widely 3–10 h late since 2026-08-26, sometimes drops runs; no
  fix from GitHub. Dispatch events start in seconds → cron-job.org is the
  free fix (SETUP step 9, optional).
- ubuntu-latest moves to 26.04 (ffmpeg 8, Python 3.14) Oct 19–Nov 19 2026 —
  workflows are pinned to ubuntu-24.04. Revisit after testing 26.04.
- apt install of ffmpeg+tesseract takes ~4.5 min per job (was 14 before the
  man-db fix). Acceptable for background jobs.
- Dev-mode Meta app posts: unconfirmed whether they're public. Check the first
  post from a logged-out browser. Going Live needs Business Verification.

## Not done — pick up here

**Milestone A — prove download + editing work on GitHub's servers**

1. [x] check.yml green — 81/81 self-tests (outro card, persistent watermark,
       reaction still, silent slate, letterboxes, git conflicts, duplicates…)
2. [x] Preview on a real public reel (DQwMwTLEvbn): **logged-out download
       works from a GitHub runner**, credit came out `@david_editor_`
       (correct), edit looked good (brighter/punchier, no outro on that one)
3. [x] **Full ingest run** (workflow_dispatch, exactly what the Shortcut
       does): queued in 35 s, downloaded + edited + staged in ~4 min. Test
       reel then removed from ready/ and the release.
3b. [x] Poster dry runs (post_now + planner) behave correctly.
3c. [ ] Test outro removal on a real reel that HAS an end card (Arjun can run
       Preview edit on one he knows has an outro)
4. [ ] Cookies only if downloads start failing (SETUP step 8)

**Milestone B — share button**

5. [ ] **[Arjun] New Actions-only PAT + build the Shortcut** — SETUP step 2.
       Delete the old Contents PAT.
6. [ ] Share a reel, confirm it reaches `ready/` with the right `author`.

**Milestone C — make it actually post (~40 min)**

7. [ ] **[Arjun]** IG → Professional + Page, Meta app (Facebook login
       setup!), tokens — SETUP steps 3–5.
8. [ ] **[Arjun]** Paste `IG_USER_ID` / `IG_ACCESS_TOKEN` into repo secrets
       (step 6) — directly in GitHub, not in chat.
9. [ ] Dry run, then `post_now`; check the post is visible logged-out.
10. [ ] Optional: cron-job.org trigger for punctual posting (step 9).

## Things that have NOT been verified

- The resumable-upload posting path has never run against Meta (needs tokens).
  Docs-verified, not live-verified. `share_to_feed` with resumable isn't in
  Meta's example — probably fine.
- Outro detection on real reels (only tested on a synthetic clip so far).
- Whether bot commits reset GitHub's 60-day scheduled-workflow auto-disable.
- The hourly cron: no scheduled run fired in the first ~2 h after switching
  (consistent with GitHub's current lag/drops). Check the Actions tab next
  session; if it's still sparse, do SETUP step 9 (cron-job.org).
- apt cache: saved as `apt-debs-ubuntu24-<image>-ffmpeg-tesseract-v1` (68 MB).
  Media setup now ~30 s–2 min instead of 4–20 min.

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
self-test workflows. Ran a 4-area research pass with independent verifiers
(findings above) and applied it. Live-tested: logged-out download + edit of a
real reel on a runner works. Ran an adversarial code review workflow.

Arjun asked how it works day to day (explained; plain-English version now at
the top of README.md) and said he'll do the setup "in a bit or later".
**Resume at: SETUP.md step 2 (iPhone Shortcut), then steps 3–6 (Meta).** Offer
to walk him through each screen. Also check the Actions tab: had the hourly
post cron started firing? If not, suggest SETUP step 9 (cron-job.org).
