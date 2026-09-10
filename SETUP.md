# Setup — one time, roughly 45 minutes

Nothing here costs money. Work through it in order; part 4 is the only slow bit.

> **Steps 1, 3 (permissions) and the git/gh install are already DONE** as of
> 2026-09-10 — see PROGRESS.md. Pick up at step 7 (the Shortcut), then decide
> about cookies (step 5) based on whether the cookieless download works.

---

## 1. The repo (5 min) — ✅ DONE

1. Make a **new public repo** on GitHub called `reel-queue`.
   Public matters: public repos get unlimited free Actions minutes, private ones
   get 2,000/month and the sleeping post job would eat most of that. Your secrets
   stay encrypted and invisible either way — the only thing anyone can see is a
   list of reels you queued.
2. Push this folder to it:

   ```powershell
   cd C:\Users\Arjun\reel-queue
   git init -b main
   git add -A
   git commit -m "reel queue"
   git remote add origin https://github.com/ArjunPatel32/reel-queue.git
   git push -u origin main
   ```

3. Repo **Settings → Actions → General → Workflow permissions** →
   select **Read and write permissions** → Save. The workflows commit the queue
   state back to the repo, and they can't without this.

---

## 2. Instagram account (5 min)

On the account you're posting to:

**Settings → Account type and tools → Switch to professional account → Creator.**

Then create a Facebook Page (free, nobody has to see it — it's just the object
Meta's API hangs the Instagram permissions off): facebook.com/pages/create.

Link them: **facebook.com → your new Page → Settings → Linked accounts →
Instagram → Connect account.** Confirm it says connected before moving on; almost
every "why doesn't the API see my account" problem traces back to this step.

---

## 3. Meta developer app (10 min)

1. Go to **developers.facebook.com** → *Get Started* → accept, verify your
   account. No card, no fee.
2. **My Apps → Create App**. Use case: **Other**. Type: **Business**. Name it
   anything.
3. In the app dashboard: **Add Product → Instagram → Set up**.
4. Leave the app in **Development mode** (the toggle at the top — it should say
   *In development*). This is what lets you skip App Review entirely. Review is
   only required to post to *other people's* accounts; on your own account a
   development-mode app has full access forever.
5. Note your **App ID** and **App Secret** from *App settings → Basic*.

---

## 4. Get a token that never expires (15 min)

Meta's default tokens expire in an hour. Page tokens derived from a long-lived
user token don't expire at all — that's what we want, so you never touch this
again.

**a. Short-lived user token.** App dashboard → **Tools → Graph API Explorer**.
Pick your app, token type **User**. Add these permissions:

```
instagram_basic
instagram_content_publish
pages_show_list
pages_read_engagement
business_management
```

Click **Generate Access Token**, log in, approve. Copy the token.

**b. Exchange it for a long-lived one.** Paste this in a browser, filling in
your values:

```
https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN
```

Copy `access_token` from the response.

**c. Get the permanent page token + your IG user id.**

```
https://graph.facebook.com/v21.0/me/accounts?access_token=LONG_TOKEN
```

Find your Page in the response. Its `access_token` field is your permanent
token — **that's `IG_ACCESS_TOKEN`**. Its `id` is the Page ID. Then:

```
https://graph.facebook.com/v21.0/PAGE_ID?fields=instagram_business_account&access_token=PERMANENT_TOKEN
```

The `instagram_business_account.id` in the response is **`IG_USER_ID`**.

---

## 5. Instagram cookies for the downloader (5 min)

yt-dlp needs a logged-in session to fetch reels. **Use a burner Instagram
account, not the one you're posting from** — it'll be logging in from a
datacenter IP, which Instagram sometimes flags.

1. Log into the burner in a browser.
2. Install the extension **"Get cookies.txt LOCALLY"** (Chrome/Firefox).
3. On instagram.com, click it → Export → you get a `cookies.txt`.
4. Open it in Notepad and copy the whole contents.

---

## 6. Add the secrets (2 min)

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add three:

| Name | Value |
|---|---|
| `IG_USER_ID` | from step 4c |
| `IG_ACCESS_TOKEN` | the permanent page token from step 4c |
| `IG_COOKIES` | the full contents of cookies.txt from step 5 |

---

## 7. The iPhone Shortcut (5 min)

First make a GitHub token for your phone: **github.com → Settings → Developer
settings → Personal access tokens → Fine-grained tokens → Generate new token.**
Repository access: *only* `reel-queue`. Permissions: **Contents → Read and
write**. Expiration: whatever you like (you'll have to redo this when it
lapses — 1 year is reasonable). Copy the token.

Now, in the **Shortcuts** app → **+** → add one action, **Get Contents of URL**:

- **URL:** `https://api.github.com/repos/ArjunPatel32/reel-queue/dispatches`
- **Method:** `POST`
- **Headers:**
  - `Authorization` → `Bearer YOUR_FINE_GRAINED_TOKEN`
  - `Accept` → `application/vnd.github+json`
- **Request Body:** `JSON`
  - `event_type` (Text) → `queue_reel`
  - `client_payload` (Dictionary) → one key inside it:
    - `url` (Text) → tap the field, then insert the **Shortcut Input** variable

Then the shortcut's settings (ⓘ icon):
- **Show in Share Sheet** → ON
- **Accepted types** → URLs only
- Name it **Queue Reel**

Test it: open any reel in Instagram → paper-plane / share icon → **Share to…** →
**Queue Reel**. Check your repo's **Actions** tab — an *Ingest reel* run should
appear within seconds.

---

## 8. Set your timezone and window

Edit `config.yml` — `timezone`, `window`, and `posts_per_day`. If you change
the window a lot, also nudge the cron in `.github/workflows/post.yml` so the job
still starts an hour or two ahead of it (that cron is in **UTC**).

---

## 9. Dry run before you let it post for real

Queue 2–3 reels from your phone, wait for the Actions runs to go green, then:

**Actions → Post reels → Run workflow → check *dry run* → Run.**

It'll print the number it picked for today, the random times, and exactly what
it would post — without touching Instagram. When that looks right, run it again
unchecked, or just leave it to the morning cron.
