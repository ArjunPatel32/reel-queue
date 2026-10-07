# Setup — one time, about 45 minutes

Nothing here costs money. Steps 1 and 2 get the share button working (reels
start queueing and getting edited). Steps 3–6 connect Instagram so they
actually post.

> **Already done** (see PROGRESS.md): the repo, Actions permissions, git/gh.
> Downloading has been tested from GitHub's servers without any login, so the
> cookies step is now optional (step 8).

---

## 1. The repo — ✅ DONE

Public repo `ArjunPatel32/reel-queue`, Actions set to *Read and write
permissions*. Public matters: free unlimited Actions minutes. Secrets stay
encrypted either way.

---

## 2. The iPhone Shortcut (10 min)

### a. A token for your phone — Actions only

**github.com → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token.**

- Name: `reel-queue-shortcut-actions`
- Expiration: 1 year
- Repository access: **Only select repositories → `reel-queue`**
- Permissions → Repository permissions → **Actions → Read and write**.
  Nothing else.

Copy the token. **Also delete the old `reel-queue-shortcut` token** (the one
with *Contents* access, made 2026-09-10) — it could change the code, it was
pasted into a chat, and nothing uses it any more.

> Why Actions-only: this token lives on your phone. A *Contents* token could
> rewrite the scripts that hold your Instagram token. An *Actions* token
> can't read your Instagram token or change any code — but anyone holding it
> **can make the bot post any reel they choose to your account**. So if your
> phone is lost or the token leaks, revoke it straight away (same settings
> page) and make a new one.

### b. Build the Shortcut

**Shortcuts app → + → add the action *Get Contents of URL***:

- **URL:** `https://api.github.com/repos/ArjunPatel32/reel-queue/actions/workflows/ingest.yml/dispatches`
- Tap **Show More**:
- **Method:** `POST`
- **Headers** (tap *Add new header* twice):
  - `Authorization` → `Bearer YOUR_TOKEN`
  - `Accept` → `application/vnd.github+json`
- **Request Body:** `JSON`, then add two fields:
  - `ref` (Text) → `main`
  - `inputs` (Dictionary) → inside it, one field:
    - `url` (Text) → tap the value, then pick the **Shortcut Input** variable

Then tap the **ⓘ** (or the shortcut's name at the top) → **Details**:
- **Show in Share Sheet** → ON
- **Share Sheet Types** → turn everything off except **URLs** and **Text**
- Rename it **Queue Reel**

Optional: add a **Show Notification** action after it saying "Queued ✅", so
you know it went through.

### c. Test it

Open any reel in Instagram → paper-plane/share → **Share to…** (or the `…`
row) → **Queue Reel**. Within a minute, your repo's **Actions** tab shows an
*Ingest reel* run. About 5 minutes later a file appears in `ready/` — open it
and check `author` is the right account.

Never share this Shortcut with anyone — the token is inside it.

---

## 3. Instagram account → Professional (5 min)

On the account you're posting to: **Settings → Account type and tools →
Switch to professional account → Creator** (Business works too). The account
must be **public**.

Create a Facebook Page (free; nobody has to see it — it's the object Meta's
API hangs Instagram permissions off): **facebook.com/pages/create**.

Link them: **facebook.com → your Page → Settings → Linked accounts →
Instagram → Connect account**. Confirm it says connected before moving on —
almost every "the API can't see my account" problem traces back to this.

If the Page requires two-factor authentication, make sure your Facebook
account has 2FA on, or publishing will be refused.

---

## 4. Meta developer app (10 min)

1. Go to **developers.facebook.com/apps/creation** (sign in, accept the
   developer terms, verify if asked — no card, no fee).
2. **App details:** any name (e.g. `reel-queue`), your email → Next.
3. **Use cases:** pick **Manage messaging & content on Instagram** → Next.
4. **Business:** **I don't want to connect a business portfolio yet** → Next
   → through *Requirements* / *Overview* → **Go to dashboard**.
5. On the dashboard: **Customize the "Manage messaging & content on
   Instagram" use case** → in the left menu choose **API setup with Facebook
   login** → click **Add all required permissions**.

   ⚠️ Pick **Facebook login**, not *Instagram login*. An app can only have one
   of the two, and this project's upload method only works with Facebook
   login.
6. Leave the app in **Development** mode (top of the dashboard). For an app
   that only posts to your own account, Meta's App Review is "not required".
7. **App settings → Basic:** note the **App ID** and **App secret**.

The menu names above are from Meta's docs as of October 2026; Meta moves
things around, so look for the closest match if a label differs.

---

## 5. Get a token that never expires (15 min)

Meta's default tokens die in an hour. A *Page* token made from a long-lived
user token doesn't expire — that's the one we want.

**a. Short-lived user token.** **developers.facebook.com/tools/explorer** →
pick your app → **User Token** → add permissions:

```
instagram_basic
instagram_content_publish
pages_show_list
pages_read_engagement
business_management
```

**Generate Access Token** → log in → approve everything (select your Page and
your Instagram account when asked). Copy the token.

**b. Make it long-lived.** Paste into a browser with your values filled in:

```
https://graph.facebook.com/v24.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN
```

Copy `access_token` from the response.

**c. The permanent Page token + your IG user id.**

```
https://graph.facebook.com/v24.0/me/accounts?access_token=LONG_TOKEN
```

Find your Page. Its `access_token` is **`IG_ACCESS_TOKEN`**; its `id` is the
Page ID. Then:

```
https://graph.facebook.com/v24.0/PAGE_ID?fields=instagram_business_account&access_token=PAGE_TOKEN
```

`instagram_business_account.id` is **`IG_USER_ID`**.

**d. Check it really never expires:**

```
https://graph.facebook.com/v24.0/debug_token?input_token=PAGE_TOKEN&access_token=PAGE_TOKEN
```

Look for `"expires_at": 0` and `instagram_content_publish` in `scopes`.

Things that kill even a "permanent" token: changing your Facebook password,
losing your role on the Page, or removing the app. If that happens the
poster's daily token check fails and GitHub emails you — redo step 5.

---

## 6. Add the secrets (2 min)

Repo → **Settings → Secrets and variables → Actions → New repository
secret**:

| Name | Value |
|---|---|
| `IG_USER_ID` | from 5c |
| `IG_ACCESS_TOKEN` | the Page token from 5c |

Paste them straight into GitHub — don't send them in a chat.

---

## 7. First post

1. Share 1–2 reels with the Shortcut and wait for them to reach `ready/`.
2. **Actions → Post reels → Run workflow → tick *dry_run* → Run.** It prints
   today's plan and exactly what it would post, without touching Instagram.
3. **Run workflow → tick *post_now* → Run.** Posts the oldest ready reel
   immediately.
4. **Check the post from a logged-out browser** (or a friend's phone). Meta
   says data from Development-mode apps is visible only to the app's own
   users; nobody has confirmed whether that applies to Instagram posts. If
   it's hidden, tell Claude — the fix is switching the app to Live, which
   needs extra verification.

After that it runs itself: 1–3 reels a day between 10:00 and 20:00 Pacific
(2–4 a day while more than 10 are waiting), oldest first.

---

## 8. Optional — cookies, only if downloads start failing

Downloads currently work with no login. If `queue/` items start piling up
with errors like *login required* or *rate-limit*, add cookies from a
**burner** Instagram account (never the posting account):

1. Log into the burner in a **private/incognito** browser window.
2. Install **Get cookies.txt LOCALLY**, export cookies on instagram.com.
3. Close the private window without logging out.
4. Repo secret **`IG_COOKIES`** = the whole cookies.txt contents.

If the cookies die later, downloads automatically retry without them.

---

## 9. Optional — make posting times punctual

GitHub's hourly timer is currently running hours late and sometimes skips
runs. Posts still go out (each run catches up on anything overdue, spaced
30 minutes apart), just not exactly on time. To make it punctual, free:

1. Make a fine-grained token like step 2a (Actions: Read and write, only
   `reel-queue`), named `reel-queue-cron`.
2. **cron-job.org** → free account → **Create cronjob**:
   - URL: `https://api.github.com/repos/ArjunPatel32/reel-queue/actions/workflows/post.yml/dispatches`
   - Schedule: every hour at minute 41
   - Advanced → Request method **POST**, headers
     `Authorization: Bearer TOKEN`, `Accept: application/vnd.github+json`,
     body `{"ref":"main"}`
3. Give that token a short expiry (90 days) and set a calendar reminder —
   cron-job.org silently disables a job after 25 failures. Like the phone
   token, anyone holding it could trigger posts, so revoke it if your
   cron-job.org account is ever compromised.
