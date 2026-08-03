#!/usr/bin/env python3
"""Malaysia F1 2026 (Sepang) ticket availability tracker.

Watches the two official sale channels and sends an ntfy.sh push when
tickets go on sale:

  1. F1 official store event page (tickets.formula1.com) — the page embeds
     ticket-category JSON server-side. Not on sale: every "products" array
     is empty. On sale: "products":[{...}] / "priceCategories":[{...}]
     become populated (verified against on-sale events Singapore/Mexico).
  2. sepangcircuit.com ticketing + events listing — on-sale events carry
     links whose href/anchor text matches formula|bahrain (verified: zero
     matches today; MotoGP on-sale pages link tickets.sepangcircuit.com/booking/).

Alerts fire on state *transitions* only. Persistent fetch failures raise a
"tracker is blind" alert so silence is never mistaken for "no tickets".
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

F1_URL = "https://tickets.formula1.com/en/f1-83069-bahrain-in-malaysia"
SEPANG_URLS = [
    "https://www.sepangcircuit.com/ticketing",
    "https://www.sepangcircuit.com/events-listing",
]
SEPANG_LINK = "https://www.sepangcircuit.com/ticketing"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
# 4 consecutive failures at 15-min cadence ≈ 1 hour blind.
FAIL_THRESHOLD = int(os.environ.get("FAIL_THRESHOLD", "4"))

ON_SALE = "ON_SALE"
NOT_ON_SALE = "NOT_ON_SALE"
SHAPE_CHANGED = "SHAPE_CHANGED"


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}")


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read().decode("utf-8", errors="replace")


def check_f1_store(html):
    """Detect sale state from the embedded ticket-category JSON."""
    populated_products = len(re.findall(r'"products":\[\{', html))
    populated_prices = len(re.findall(r'"priceCategories":\[\{', html))
    any_products_key = len(re.findall(r'"products":', html))

    if populated_products or populated_prices:
        return ON_SALE, (
            f"{populated_products} populated product arrays, "
            f"{populated_prices} populated price categories"
        )
    if any_products_key == 0:
        return SHAPE_CHANGED, "embedded ticket JSON missing — page structure changed"
    return NOT_ON_SALE, f"{any_products_key} product arrays, all empty"


F1_WORD = re.compile(r"formula|bahrain", re.I)
ANCHOR = re.compile(r'<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.I | re.S)


def check_sepang(html):
    """Detect an F1 event appearing on Sepang's site.

    Matches only hrefs and anchor text — never raw HTML — because CSS class
    hashes (e.g. data-pb-style="VF1W8E2") contain 'F1'-like substrings.
    """
    hits = set()
    for m in ANCHOR.finditer(html):
        href, text = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        if F1_WORD.search(href) or F1_WORD.search(text):
            hits.add(href.strip() or text.strip()[:80])
    if hits:
        return ON_SALE, "F1 event links found: " + ", ".join(sorted(hits)[:5])
    if "sepang" not in html.lower():
        return SHAPE_CHANGED, "page no longer looks like a Sepang page"
    return NOT_ON_SALE, "no formula/bahrain links present"


def notify(title, message, priority="default", click=None, actions=None, tags=None):
    """Send an ntfy push. Without NTFY_TOPIC set, log only (dry run)."""
    if not NTFY_TOPIC:
        log(f"DRY-RUN notify: [{title}] {message}")
        return
    # HTTP headers are latin-1 only; the message body stays full UTF-8.
    def h(value):
        return value.encode("latin-1", "replace").decode("latin-1")

    headers = {"Title": h(title), "Priority": priority}
    if click:
        headers["Click"] = h(click)
    if actions:
        headers["Actions"] = h("; ".join(actions))
    if tags:
        headers["Tags"] = h(tags)
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode(),
        headers=headers,
        method="POST",
    )
    # Retry: losing the one alert that matters to a transient ntfy hiccup or
    # rate-limit (429) would defeat the whole tracker.
    for attempt, delay in enumerate((0, 5, 15, 30), start=1):
        time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log(f"notified ({resp.status}): [{title}] {message}")
                return
        except (urllib.error.URLError, OSError) as e:
            log(f"ERROR sending notification (attempt {attempt}): {e}")
    log(f"GIVING UP on notification: [{title}] {message}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"sources": {}}


def save_state(state):
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def on_sale_alert(source_name, detail, url):
    notify(
        title=f"F1 MALAYSIA TICKETS ON SALE — {source_name}",
        message=f"Go buy now! Detected: {detail}",
        priority="urgent",
        click=url,
        actions=[
            f"view, F1 Store, {F1_URL}",
            f"view, Sepang, {SEPANG_LINK}",
        ],
        tags="rotating_light,checkered_flag",
    )


def run_source(state, name, checker, url_label):
    """Fetch + detect for one source, update its state, alert on transitions."""
    src = state["sources"].setdefault(
        name, {"status": NOT_ON_SALE, "fails": 0, "alerted_blind": False}
    )
    try:
        if name == "f1_store":
            status, detail = checker(fetch(F1_URL))
        else:
            statuses = [checker(fetch(u)) for u in SEPANG_URLS]
            # ON_SALE on any page wins; then SHAPE_CHANGED; else NOT_ON_SALE.
            status, detail = max(
                statuses,
                key=lambda s: {ON_SALE: 2, SHAPE_CHANGED: 1, NOT_ON_SALE: 0}[s[0]],
            )
    except Exception as e:
        src["fails"] += 1
        log(f"{name}: fetch/check FAILED ({src['fails']}x): {e}")
        if src["fails"] >= FAIL_THRESHOLD and not src["alerted_blind"]:
            notify(
                title=f"Tracker is BLIND on {name}",
                message=(
                    f"{src['fails']} consecutive failures (last: {e}). "
                    f"The site may be blocking us — check manually."
                ),
                priority="high",
                click=url_label,
                tags="warning",
            )
            src["alerted_blind"] = True
        return

    if src["fails"]:
        log(f"{name}: recovered after {src['fails']} failures")
        if src["alerted_blind"]:
            notify(
                title=f"Tracker recovered on {name}",
                message="Checks are working again.",
                priority="low",
                tags="white_check_mark",
            )
    src["fails"] = 0
    src["alerted_blind"] = False

    prev = src["status"]
    log(f"{name}: {status} ({detail})")

    if status == prev:
        return
    # ON_SALE is sticky: never downgrade (a glitch page must not re-arm alerts).
    if prev == ON_SALE:
        log(f"{name}: ignoring downgrade {prev} -> {status}")
        return
    src["status"] = status
    if status == ON_SALE:
        on_sale_alert(name, detail, url_label)
    elif status == SHAPE_CHANGED:
        notify(
            title=f"Ticket page changed — {name}",
            message=(
                f"Detector no longer recognizes the page ({detail}). "
                f"Tickets may be launching — check manually."
            ),
            priority="high",
            click=url_label,
            tags="eyes",
        )
    elif status == NOT_ON_SALE and prev == SHAPE_CHANGED:
        log(f"{name}: page shape back to normal")


def main():
    state = load_state()
    run_source(state, "f1_store", check_f1_store, F1_URL)
    run_source(state, "sepang", check_sepang, SEPANG_LINK)
    save_state(state)
    log("run complete")


if __name__ == "__main__":
    sys.exit(main())
