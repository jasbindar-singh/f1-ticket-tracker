# F1 Malaysia 2026 Ticket Tracker

Watches the official sale channels for the **Formula 1 Gulf Air Bahrain Grand
Prix in Malaysia 2026** (Sepang, Oct 2–4) and sends a phone push via
[ntfy.sh](https://ntfy.sh) the moment tickets go on sale.

## Sources watched

| Source | Detection |
|---|---|
| [F1 official store event page](https://tickets.formula1.com/en/f1-83069-bahrain-in-malaysia) | Server-embedded ticket JSON: `"products":[]` → `"products":[{...}]` |
| [sepangcircuit.com/ticketing](https://www.sepangcircuit.com/ticketing) + events listing | A new event/booking link matching `formula\|bahrain` appearing |

## How it works

- GitHub Actions cron runs `checker.py` every ~15 minutes.
- Alerts fire on **state transitions only** (no repeat spam); `state.json`
  is committed back to the repo as persistent state + audit trail.
- If fetches fail ~1 hour straight (e.g. bot-blocking), you get a
  "tracker is BLIND" push so silence can't be mistaken for "no tickets yet".
- Page redesigns that break the detector trigger a "page changed" push.

## Notifications

Pushes go to the ntfy topic in the `NTFY_TOPIC` repo secret. The on-sale
alert is `urgent` priority, opens the F1 store on tap, and has action
buttons for both stores. Subscribe to the same topic in the ntfy app.

## Local dev

```bash
python3 test_checker.py   # unit tests (21 scenarios)
python3 checker.py        # one dry-run check (no NTFY_TOPIC = log only)
```
