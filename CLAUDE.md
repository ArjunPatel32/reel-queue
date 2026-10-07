# reel-queue

Instagram reel repost pipeline. Share a reel from the iPhone share sheet → it
queues → gets edited (outro cut, colour polish) → 1–3 a day get auto-posted
between 10:00–20:00 Pacific, oldest first, captioned
`🎥 @originalaccount` for credit. Runs entirely on GitHub Actions, costs nothing.

## Read this first

**Open `PROGRESS.md` before doing anything else.** It is the source of truth for
what's built, what's deployed, what's been decided (and why), and exactly where
to pick up. It's kept current — update it whenever a chunk of work finishes.

`SETUP.md` is the one-time setup walkthrough, with completed steps marked.
`README.md` explains the architecture and day-to-day use.

## Working agreements

- **Arjun works across many short sessions.** Update `PROGRESS.md` as work
  completes, not at the end — assume any session can stop abruptly.
- **Zero budget.** Every piece must have a free path. No paid APIs, no VPS.
- **Nothing may depend on Arjun's PC being on.** He mostly uses a work computer;
  the desktop is only on for gaming.
- **Never commit secrets.** This repo is public. Tokens and cookies live in
  GitHub Actions secrets only.
- Don't re-litigate decisions recorded in `PROGRESS.md` — read the reasoning
  there first.
- **No Python on Arjun's PC.** Code is verified on GitHub's runners: `check.yml`
  (actionlint + `scripts/selftest.py`) runs on every push, and the *Preview
  edit* workflow exercises a real reel without touching the queue. Add a
  selftest case for any behaviour you change.
