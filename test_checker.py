#!/usr/bin/env python3
"""Tests for checker.py detectors and state machine.

Synthetic fixtures mirror the exact patterns observed on the live pages
(Aug 2026). If real page snapshots exist in REAL_PAGES_DIR (not committed),
the detectors are validated against those too.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import checker
from checker import ON_SALE, NOT_ON_SALE, SHAPE_CHANGED

REAL_PAGES_DIR = os.environ.get("REAL_PAGES_DIR", "")

# --- Synthetic fixtures (patterns copied from live pages) -------------------

F1_NOT_ON_SALE = (
    '<html><script>window.x = {"tickets":[{"id":53519,"name":"Friday-Sunday",'
    '"closed":false,"dateStart":"2026-10-02","products":[],"priceCategories":[],'
    '"currency":null},{"id":53520,"name":"Saturday","products":[],'
    '"priceCategories":[]}]};</script>Buy Bahrain in Malaysia F1 Tickets</html>'
)

F1_ON_SALE = (
    '<html><script>window.x = {"tickets":[{"id":53519,"name":"Friday-Sunday",'
    '"products":[{"id":1080788,"name":"Main Grandstand","price":100}],'
    '"priceCategories":[{"id":1,"name":"Adult"}]}]};</script></html>'
)

F1_SHAPE_CHANGED = "<html><body>Welcome to a totally redesigned page</body></html>"

SEPANG_NOT_ON_SALE = (
    '<html><style>#html-body [data-pb-style=VF1W8E2]{display:flex}</style>'
    '<a href="/events/petronas-grand-prix-of-malaysia-2026-overview">'
    "PETRONAS GRAND PRIX OF MALAYSIA 2026</a>"
    '<a href="/events/malaysia-superbike-championship-overview.html">Superbike</a>'
    "Sepang International Circuit</html>"
)

# Two plausible shapes of the future F1 listing: named URL slug, or booking link.
SEPANG_ON_SALE_SLUG = SEPANG_NOT_ON_SALE.replace(
    "</html>",
    '<a href="/events/formula-1-bahrain-grand-prix-2026-overview">'
    "FORMULA 1 GULF AIR BAHRAIN GRAND PRIX IN MALAYSIA 2026</a></html>",
)
SEPANG_ON_SALE_TEXT_ONLY = SEPANG_NOT_ON_SALE.replace(
    "</html>",
    '<a href="https://tickets.sepangcircuit.com/booking/26F1MAIN?x=1">'
    "<span>Buy FORMULA 1 tickets</span></a></html>",
)

SEPANG_SHAPE_CHANGED = "<html><body>503 backend unavailable</body></html>"


class TestF1Detector(unittest.TestCase):
    def test_not_on_sale(self):
        status, detail = checker.check_f1_store(F1_NOT_ON_SALE)
        self.assertEqual(status, NOT_ON_SALE)

    def test_on_sale_products(self):
        status, detail = checker.check_f1_store(F1_ON_SALE)
        self.assertEqual(status, ON_SALE)
        self.assertIn("1 populated product arrays", detail)

    def test_on_sale_price_categories_only(self):
        html = F1_NOT_ON_SALE.replace(
            '"priceCategories":[],"currency"', '"priceCategories":[{"id":1}],"currency"'
        )
        status, _ = checker.check_f1_store(html)
        self.assertEqual(status, ON_SALE)

    def test_shape_changed(self):
        status, _ = checker.check_f1_store(F1_SHAPE_CHANGED)
        self.assertEqual(status, SHAPE_CHANGED)

    def test_event_name_mentions_do_not_trigger(self):
        # "bahrain" appears 36x on the real page — must NOT trigger this detector.
        html = F1_NOT_ON_SALE + " bahrain Bahrain BAHRAIN formula Formula"
        status, _ = checker.check_f1_store(html)
        self.assertEqual(status, NOT_ON_SALE)


class TestSepangDetector(unittest.TestCase):
    def test_not_on_sale_ignores_motogp_and_css_hashes(self):
        # 'Grand Prix of Malaysia' (MotoGP) and CSS hash 'VF1W8E2' present.
        status, detail = checker.check_sepang(SEPANG_NOT_ON_SALE)
        self.assertEqual(status, NOT_ON_SALE)

    def test_on_sale_via_href_slug(self):
        status, detail = checker.check_sepang(SEPANG_ON_SALE_SLUG)
        self.assertEqual(status, ON_SALE)
        self.assertIn("formula-1-bahrain", detail)

    def test_on_sale_via_anchor_text(self):
        status, detail = checker.check_sepang(SEPANG_ON_SALE_TEXT_ONLY)
        self.assertEqual(status, ON_SALE)

    def test_shape_changed(self):
        status, _ = checker.check_sepang(SEPANG_SHAPE_CHANGED)
        self.assertEqual(status, SHAPE_CHANGED)


@unittest.skipUnless(
    REAL_PAGES_DIR and os.path.isdir(REAL_PAGES_DIR), "real page snapshots not present"
)
class TestRealPages(unittest.TestCase):
    """Validation against real saved pages (local only, not committed)."""

    def read(self, name):
        with open(os.path.join(REAL_PAGES_DIR, name), encoding="utf-8",
                  errors="replace") as f:
            return f.read()

    def test_malaysia_page_not_on_sale(self):
        status, _ = checker.check_f1_store(self.read("f1_event.html"))
        self.assertEqual(status, NOT_ON_SALE)

    def test_singapore_page_on_sale(self):
        status, _ = checker.check_f1_store(self.read("f1_3301-singapore.html"))
        self.assertEqual(status, ON_SALE)

    def test_mexico_page_on_sale(self):
        status, _ = checker.check_f1_store(self.read("f1_4861-mexico.html"))
        self.assertEqual(status, ON_SALE)

    def test_sepang_ticketing_not_on_sale(self):
        status, _ = checker.check_sepang(self.read("sepang_ticketing.html"))
        self.assertEqual(status, NOT_ON_SALE)


class TestNotifyHeaders(unittest.TestCase):
    def test_non_latin1_title_does_not_crash(self):
        # Em dashes etc. in titles must be sanitized (HTTP headers are latin-1).
        sent = {}

        def fake_urlopen(req, timeout=15):
            # Reproduce http.client's strictness before "responding".
            for k, v in req.headers.items():
                v.encode("latin-1")
            sent["title"] = req.headers.get("Title")

            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        with mock.patch.object(checker, "NTFY_TOPIC", "test-topic"), \
             mock.patch.object(checker.urllib.request, "urlopen", fake_urlopen):
            checker.notify("Ticket page changed — f1_store", "détail ✓", click=None)
        self.assertIn("Ticket page changed", sent["title"])


    def test_notify_retries_on_transient_failure(self):
        calls = {"n": 0}

        def flaky_urlopen(req, timeout=15):
            calls["n"] += 1
            if calls["n"] < 3:
                raise checker.urllib.error.URLError("429 rate limited")

            class R:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        with mock.patch.object(checker, "NTFY_TOPIC", "test-topic"), \
             mock.patch.object(checker.time, "sleep"), \
             mock.patch.object(checker.urllib.request, "urlopen", flaky_urlopen):
            checker.notify("title", "message")
        self.assertEqual(calls["n"], 3)


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        checker.STATE_FILE = os.path.join(self.tmp.name, "state.json")
        self.notifications = []
        self.notify_patch = mock.patch.object(
            checker,
            "notify",
            side_effect=lambda title, message, **kw: self.notifications.append(
                (title, kw.get("priority", "default"))
            ),
        )
        self.notify_patch.start()

    def tearDown(self):
        self.notify_patch.stop()
        self.tmp.cleanup()

    def run_with_pages(self, f1_html=None, sepang_html=None, error=None):
        def fake_fetch(url, timeout=30):
            if error:
                raise error
            return f1_html if "formula1" in url else sepang_html

        with mock.patch.object(checker, "fetch", side_effect=fake_fetch):
            checker.main()
        with open(checker.STATE_FILE) as f:
            return json.load(f)

    def test_no_alert_when_nothing_changes(self):
        for _ in range(3):
            self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        self.assertEqual(self.notifications, [])

    def test_alert_fires_once_on_transition(self):
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)  # no re-alert
        on_sale = [n for n in self.notifications if "ON SALE" in n[0]]
        self.assertEqual(len(on_sale), 1)
        self.assertEqual(on_sale[0][1], "urgent")

    def test_on_sale_is_sticky_through_glitches(self):
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)  # glitch
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)  # back
        on_sale = [n for n in self.notifications if "ON SALE" in n[0]]
        self.assertEqual(len(on_sale), 1)

    def test_both_sources_alert_independently(self):
        self.run_with_pages(F1_ON_SALE, SEPANG_ON_SALE_SLUG)
        on_sale = [n for n in self.notifications if "ON SALE" in n[0]]
        self.assertEqual(len(on_sale), 2)

    def test_blind_alert_after_threshold_then_recovery(self):
        for _ in range(checker.FAIL_THRESHOLD):
            self.run_with_pages(error=RuntimeError("HTTP 403"))
        blind = [n for n in self.notifications if "BLIND" in n[0]]
        self.assertEqual(len(blind), 2)  # one per source, exactly once
        self.run_with_pages(error=RuntimeError("HTTP 403"))  # still failing
        self.assertEqual(len([n for n in self.notifications if "BLIND" in n[0]]), 2)
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)  # recovery
        recovered = [n for n in self.notifications if "recovered" in n[0]]
        self.assertEqual(len(recovered), 2)

    def test_shape_change_alerts_once(self):
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        changed = [n for n in self.notifications if "changed" in n[0]]
        self.assertEqual(len(changed), 1)

    def test_shape_change_then_on_sale_still_alerts(self):
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)
        on_sale = [n for n in self.notifications if "ON SALE" in n[0]]
        self.assertEqual(len(on_sale), 1)

    def test_flapping_shape_change_alerts_once_per_day(self):
        # Page oscillates (e.g. A/B-tested redesign): NOT <-> SHAPE_CHANGED.
        for _ in range(5):
            self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
            self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        changed = [n for n in self.notifications if "changed" in n[0]]
        self.assertEqual(len(changed), 1)

    def test_flapping_blind_recover_cycles_alert_once_per_day(self):
        for _ in range(3):  # fail past threshold, recover, repeat
            for _ in range(checker.FAIL_THRESHOLD):
                self.run_with_pages(error=RuntimeError("HTTP 403"))
            self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        blind = [n for n in self.notifications if "BLIND" in n[0]]
        recovered = [n for n in self.notifications if "recovered" in n[0]]
        self.assertEqual(len(blind), 2)  # one per source, once per day
        self.assertEqual(len(recovered), 2)

    def test_expired_cooldown_re_arms_alerts(self):
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        # Age the recorded alert timestamp past 24h, then flap again.
        with open(checker.STATE_FILE) as f:
            state = json.load(f)
        state["sources"]["f1_store"]["last_alerts"]["shape_changed"] = (
            "2020-01-01T00:00:00+00:00"
        )
        with open(checker.STATE_FILE, "w") as f:
            json.dump(state, f)
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        changed = [n for n in self.notifications if "changed" in n[0]]
        self.assertEqual(len(changed), 2)

    def test_on_sale_alert_is_never_cooldown_suppressed(self):
        # Even mid-flap, a real ON_SALE transition must always push.
        self.run_with_pages(F1_SHAPE_CHANGED, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        self.run_with_pages(F1_ON_SALE, SEPANG_NOT_ON_SALE)
        on_sale = [n for n in self.notifications if "ON SALE" in n[0]]
        self.assertEqual(len(on_sale), 1)

    def test_corrupt_state_file_recovers(self):
        with open(checker.STATE_FILE, "w") as f:
            f.write("{not json")
        state = self.run_with_pages(F1_NOT_ON_SALE, SEPANG_NOT_ON_SALE)
        self.assertIn("f1_store", state["sources"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
