# reel-queue

Instagram reel repost pipeline. Share a reel from the iPhone share sheet → it
queues → 1–3 get auto-posted each morning between 09:30–10:00 Pacific, captioned
`@originalaccount` for credit. Runs entirely on GitHub Actions, costs nothing.

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
