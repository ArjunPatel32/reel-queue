# reel-queue

Share a reel from Instagram → it's downloaded, cleaned up, and queued → 1–3
of them get posted to your account each day, oldest first, captioned with
credit to the original creator.

Runs entirely on GitHub Actions. Costs nothing. Doesn't need your PC on.

**New here or picking this back up? Read [PROGRESS.md](PROGRESS.md) first**, then
[SETUP.md](SETUP.md).

## How it works

```
iPhone share sheet → "Queue Reel" shortcut
      ↓  (GitHub API: run ingest.yml with the URL)
ingest.yml   → queue/<time>-<code>.json         saved first, so nothing is lost
      ↓  yt-dlp download → edit.py → video stored on the `media` release
ready/<time>-<code>.json                        edited, credited, waiting its turn
      ↓  post.yml, hourly, against today's plan in state/plan.json
posted/<time>-<code>.json                       receipt with the Instagram media id
```

## What the edit does

- Cuts the creator's **outro** — a "follow me"/"@creator" end card, a logo
  slate, the TikTok end screen, or a still frame held at the end (scene-cut
  detection + reading on-screen text in the last 8 s).
- Removes **black bars** baked into the video; clips that aren't 9:16 sit on a
  **blurred copy of themselves** instead of black bars.
- Mild **brightness / contrast / colour / sharpening** boost, and **volume
  levelling** so every reel plays at the same loudness.
- Optional fixed trim off the start/end.
- Encodes to Instagram's Reels spec.

Caption: `🎥 @creator`. If the person who posted the reel credits someone else
in their caption ("credit: @x", "via @x", "🎥 @x"…), that account gets the
credit instead.

All the knobs are in [config.yml](config.yml).

## The workflows

| Workflow | Trigger | Does |
|---|---|---|
| `ingest.yml` | your Shortcut | saves the URL, downloads + edits it |
| `retry.yml` | every 4 hours | retries anything still stuck in `queue/` |
| `post.yml` | hourly | plans the day once, posts whatever's due |
| `preview.yml` | you, by hand | edits one reel without queueing it — for trying settings |
| `check.yml` | code changes | lint + offline self-test |

## Daily use

Share a reel to **Queue Reel**. That's it.

- **What's waiting:** `queue/` (not downloaded yet) and `ready/` (edited,
  waiting its turn). **What went out:** `posted/`.
- **Today's plan:** `state/plan.json`.
- **Watch an edited reel before it posts:** open its file in `ready/` and tap
  the `asset_url` link.
- **Try different edit settings:** change `config.yml`, then **Actions →
  Preview edit → Run workflow**, paste a reel URL. The run's summary page
  links the edited video and a before/after strip of frames.
- **Don't want one to post:** delete its file from `ready/`, or add
  `"skip": true` to it.
- **Post something right now:** **Actions → Post reels → Run workflow →
  post_now**.

## Worth knowing

- **Reach.** Instagram stops recommending accounts that repost other people's
  content 10+ times in 30 days (it shows them to followers only), and
  replaces reposted copies with the original in Explore/Reels. Crediting,
  cropping and colour edits don't change that — Instagram calls them
  "low-effort". What does count: adding your own commentary, voiceover or
  on-screen context. For reels you really want people to see, Instagram's
  built-in **Repost** button is the approved route. This pipeline is fine for
  a page your followers enjoy; it won't go viral on the algorithm.
- Reels whose download has **no audio** (usually licensed music that didn't
  come through) are parked in `ready/` with `skip: true` rather than posted
  muted.
- Reels showing another app's **watermark** (TikTok etc.) are flagged in the
  logs — Instagram won't recommend those at all.

## When something breaks

- **A reel never leaves `queue/`** — open its JSON and read `last_error`. If
  it says login/rate-limit, see SETUP step 8 (cookies).
- **Posting fails** — `last_error` in the `ready/` JSON, or the failed *Post
  reels* run. Most often the token (redo SETUP step 5).
- **Nothing posts at all** — GitHub disables scheduled workflows on repos
  with no activity for 60 days; check the Actions tab for a "disabled"
  banner and re-enable. Also check for runs "awaiting approval".
