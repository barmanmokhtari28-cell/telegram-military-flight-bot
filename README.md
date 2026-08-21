# telegram-military-flight-bot

OSINT flight tracker for Iran & the Middle East. Watches for US/allied
military aircraft movement (tankers, cargo, ISR, VIP, combat types) and
civilian traffic patterns, and posts alerts — including an escalation
score — to a Telegram channel. Runs on GitHub Actions every 10 minutes.

## What it does

- **Scans the whole region**, not just a small circle around one point —
  Iran, Iraq, the Gulf/Strait of Hormuz, the Levant/Israel, Saudi Arabia,
  and the Red Sea/Bab-el-Mandeb approach.
- **Posts an alert for each newly-seen military aircraft** (tanker, cargo,
  ISR/recon, VIP, or combat type, or a recognized military callsign),
  with an aircraft photo when available, type, altitude, speed, position,
  sector, and a live-tracking link.
- **Computes a 0–100 regional escalation index** each run, combining:
  - Volume spikes (military aircraft count vs. recent baseline)
  - Pattern combos (tanker + combat aircraft together, VIP/government
    aircraft airborne, ISR clustering, cargo/airlift surges)
  - Civilian anomaly detection (a drop in commercial traffic through the
    Hormuz/Gulf corridor versus baseline — a signal that airlines may be
    avoiding the area)
- **Posts a standalone alert whenever the escalation band changes**
  (e.g. LOW → ELEVATED, or HIGH → GUARDED), with the specific factors
  that drove the change — this is the escalation/de-escalation signal.
- **Posts a lightweight heartbeat** (traffic counts + current escalation
  index) at most once per hour when nothing new happened, instead of
  spamming the channel every 10 minutes.
- **No browser automation.** The old version used Playwright to
  screenshot a live radar map, but that map never reaches a "network
  idle" state (it's constantly polling), so those screenshots almost
  always timed out silently — meaning most alerts likely never posted.
  This version drops that entirely in favor of aircraft photos fetched
  by direct URL (Telegram fetches them server-side) plus a link to the
  live tracker.

## Setup

1. Add two repository secrets (Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`
2. The workflow (`.github/workflows/tracker.yml`) runs every 10 minutes
   automatically, or trigger it manually via "Run workflow" in the
   Actions tab.
3. State (dedupe list, traffic baselines, current escalation level) is
   kept in `state.json`, committed back to the repo by the workflow after
   each run. If you're migrating from the old version, your existing
   `seen_flights.json` will be picked up automatically on first run so
   you don't get a flood of re-announcements.

## Notes on the escalation score

The escalation index is a heuristic built from open ADS-B data — it is
**not** an intelligence assessment and can be wrong (aircraft can hide
their transponders, ADS-B coverage has gaps, and "spike vs baseline"
is only as good as the history the bot has accumulated). Treat it as a
prioritization signal for what to look at, not a conclusion.

## Data sources

- [airplanes.live](https://airplanes.live) — global military ADS-B feed
- [adsb.one](https://adsb.one) — regional point search
- [planespotters.net](https://www.planespotters.net) — aircraft photos
