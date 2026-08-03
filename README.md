# F1 Malaysia 2026 Ticket Tracker 🏁

Watches the official sale channels for the **Formula 1 Gulf Air Bahrain Grand
Prix in Malaysia 2026** (Sepang International Circuit, Oct 2–4, 2026 — F1's
return to Malaysia after nine years) and sends a phone push notification via
[ntfy.sh](https://ntfy.sh) the moment tickets go on sale.

Built because tickets were announced for "early August 2026" with no firm
date, and refreshing two websites for weeks is a job for a robot.

## How it works

```
GitHub Actions cron (every ~15 min)
        │
        ▼
   checker.py ──── fetches with a browser User-Agent ────┐
        │                                                │
        │      ┌──────────────────────────────┐  ┌───────────────────────┐
        │      │ tickets.formula1.com          │  │ sepangcircuit.com     │
        │      │ event page (Malaysia)         │  │ /ticketing + /events  │
        │      └──────────────────────────────┘  └───────────────────────┘
        ▼
  per-source detector → state machine (state.json, committed back to repo)
        │
        ▼  on state TRANSITION only
  ntfy.sh push → your phone (urgent priority, tap-to-open, store buttons)
```

## Detection logic (the interesting part)

Naive "did the page change?" diffing is useless — session tokens, cache-bust
hashes and analytics IDs change on every load. Instead, each source gets a
semantic detector validated against real on-sale and not-on-sale pages:

### Source 1: F1 official ticket store

The event page server-renders its ticket state as embedded JSON. Before the
sale, categories exist but are empty:

```json
{"id":53519,"name":"Friday-Sunday","closed":false,"products":[],"priceCategories":[]}
```

On-sale events (verified against Singapore and Mexico, which had 18 and 10
populated arrays respectively) contain `"products":[{...}]` with real
grandstand entries. The detector fires on `"products":[{` / `"priceCategories":[{`.

Keyword matching would not work here: "bahrain" already appears ~36 times on
the page (it's in the event name).

### Source 2: Sepang International Circuit

The circuit's Magento store server-renders its event tiles. On-sale events
carry links to their booking system (`tickets.sepangcircuit.com/booking/<CODE>`).
The detector fires when an href **or anchor text** matching `formula|bahrain`
appears on `/ticketing` or `/events-listing`.

Two traps this deliberately avoids:

- The "PETRONAS GRAND PRIX OF MALAYSIA" tile is **MotoGP**, not F1 — the F1
  race is named "Bahrain Grand Prix in Malaysia", so the pattern excludes it.
- Matching raw HTML would false-positive on CSS class hashes like
  `data-pb-style="VF1W8E2"` — hence hrefs and anchor text only.

## State machine and alert policy

Per-source states: `NOT_ON_SALE → ON_SALE | SHAPE_CHANGED | (fetch errors)`.

- Alerts fire on **transitions only** — no repeat spam every 15 minutes.
- `ON_SALE` is **sticky**: a glitchy page can't downgrade the state and
  re-arm a duplicate alert later.
- `SHAPE_CHANGED` fires if a page stops looking like itself (redesigns often
  accompany ticket launches — worth a manual look).
- After 4 consecutive fetch failures (~1 hour) you get a **"tracker is
  BLIND"** alert, and a "recovered" notice when checks work again. Silence
  is therefore never ambiguous: no news really is no news.
- Notification sends are retried 4× with backoff so a transient ntfy hiccup
  can't eat the one alert that matters.

`state.json` is committed back by the workflow after every run. That gives
persistent state across stateless runners, an audit trail of every
transition, and a liveness check: **if the newest `state:` commit is older
than ~30 minutes, the tracker has stopped running.**

## Notifications

Pushes go to a private ntfy.sh topic (stored only as the `NTFY_TOPIC`
GitHub Actions secret — for ntfy, the topic name *is* the password, so it is
never committed). The on-sale alert is `urgent` priority, opens the F1 store
on tap, and carries action buttons for both stores.

## Design notes / trade-offs

- Both sites return 403 to non-browser clients but accept plain HTTP
  requests with a Chrome User-Agent — no headless browser needed. Verified
  from GitHub's datacenter IPs too.
- Polling every 15 minutes is ~100 requests/day — indistinguishable from a
  human refreshing, and purchase is manual anyway (expect a launch-day
  queue regardless).
- GitHub's cron is best-effort (runs can be minutes late, occasionally
  skipped). Transition-based detection makes that harmless: the next run
  catches up.
- Scheduled workflows are auto-disabled after 60 days of repo inactivity;
  the per-run state commits keep the repo active as a side effect.
- Ticket2U (Sepang's authorized agent) is not watched: it's a Vue SPA with a
  token-gated API, and both watched sources will flip at effectively the
  same time.

## Local development

```bash
python3 test_checker.py   # 23 tests: detectors (incl. real-page fixtures),
                          # state machine, alert dedup, failure/recovery paths
python3 checker.py        # one live check; without NTFY_TOPIC set it only logs
```

Optional env vars: `NTFY_TOPIC`, `NTFY_SERVER` (default `https://ntfy.sh`),
`STATE_FILE` (default `state.json`), `FAIL_THRESHOLD` (default 4).

## Status

Live since 2026-08-03. Decommission after the race weekend (Oct 2–4, 2026) —
or earlier, the moment it has done its one job. 🏎️
