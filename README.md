# H-1B slot watch (India)

Checks public, crowd-sourced slot trackers (VisaGrader) every ~5 minutes on
GitHub Actions and sends a phone push via ntfy when an H-1B interview slot is
reported. Slots on or before 20 Dec 2026 are sent as "URGENT - BOOK NOW".

It never logs in to usvisascheduling.com. Always confirm and book on the
official portal.

## Setup
1. Make this repository **public** (public repos get free unlimited Actions minutes).
2. Settings → Secrets and variables → Actions → New repository secret:
   `NTFY_TOPIC` = your ntfy topic name.
3. Actions tab → enable workflows → "H-1B slot watch" → Run workflow (test).
4. Phone: install the ntfy app and subscribe to the same topic.

To stop: Actions → H-1B slot watch → ⋯ → Disable workflow.
