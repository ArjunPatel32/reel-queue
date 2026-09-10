# reel-queue

Share a reel from Instagram → it lands in a queue → 1–3 of them get posted to
your account each morning at a random time, captioned with credit to the
original poster.

Runs entirely on GitHub Actions. Costs nothing. Doesn't need your PC on.

**New here or picking this back up? Read [PROGRESS.md](PROGRESS.md) first**, then
[SETUP.md](SETUP.md).

## How it works

```
iPhone share sheet
      ↓  (Shortcut → GitHub repository_dispatch)
ingest.yml  →  queue/<time>-<code>.json     ← the reel URL, saved immediately
      ↓  yt-dlp + ffmpeg + upload to a GitHub Release
ready/<time>-<code>.json                    ← has a public video URL attached
      ↓  post.yml, every morning
posted/<time>-<code>.json                   ← receipt, with the IG media id
```

Downloading happens **at share time**, not at post time. Instagram blocks
datacenter IPs unpredictably, so `retry.yml` re-attempts every 4 hours until it
succeeds. Because you queue reels days before they're posted, a download has
plenty of slack to fail and recover. The morning job only publishes files that
are already downloaded, so it never depends on Instagram cooperating.

## The three workflows

| Workflow | Trigger | Does |
|---|---|---|
| `ingest.yml` | your Shortcut | saves the URL, tries to download it |
| `retry.yml` | every 4 hours | retries anything still stuck in `queue/` |
| `post.yml` | daily cron | picks 1–3, sleeps to random times, publishes |

`post.yml` starts about two hours before your window because GitHub's cron
routinely fires 5–20 minutes late; the script sleeps until the real randomized
times, so the actual posts land where you asked.

## Daily use

Share a reel to the **Queue Reel** shortcut. That's it.

To check on things: the repo's **Actions** tab for runs, the `queue/` folder for
what's waiting, `ready/` for what's downloaded and eligible, `posted/` for what
went out.

## Knobs

All in `config.yml`: timezone, the posting window, min/max posts per day, the
caption template, and the crop/encode settings.

## When something breaks

- **A reel never leaves `queue/`** — open its JSON and read `last_error`. Usually
  expired cookies; re-export them (SETUP step 5) and update the `IG_COOKIES`
  secret.
- **Posting fails** — check `last_error` in the `ready/` JSON. Most often the
  access token, or the account got switched off Professional.
- **Nothing posts at all** — GitHub disables scheduled workflows on repos with
  no activity for 60 days. Push any commit to re-enable.
