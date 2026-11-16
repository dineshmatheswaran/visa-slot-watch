#!/usr/bin/env python3
"""
US visa H-1B interview slot watcher (India) - public data only.

Polls public, crowd-sourced slot pages (VisaGrader) every few minutes and
sends an instant phone push via ntfy.sh when an H-1B INTERVIEW slot
is reported. Slots on or before URGENT_BEFORE are flagged as urgent
(max priority, alarm-style notification).

It NEVER logs in to usvisascheduling.com, so it cannot get your account
locked. Booking is still done by you on the official portal.

No extra packages needed (Python 3 standard library only).

Setup:
  1. (nothing to install)
  2. Install the free "ntfy" app on your phone and subscribe to the topic
     name you put in NTFY_TOPIC below (pick something long and random).
  3. python3 visa_slot_watch.py
     (leave it running; keep the computer awake)
"""

import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime

import ssl
import urllib.error
import urllib.request
from html.parser import HTMLParser

# ---------------- settings ----------------
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or "h1b-slots-bfba83541a"  # a GitHub secret overrides this
URGENT_BEFORE = date(2026, 12, 20)          # slots on/before this = URGENT
POLL_SECONDS = 120                          # check every 2 minutes
VISA_TYPES = ("H-1B", "H1B")
APPOINTMENT_TYPE = "interview"              # ignore Dropbox

CITY_PAGES = {
    "Mumbai":    "https://visagrader.com/us-visa-time-slots-availability/india-ind/mumbai-P139",
    "Chennai":   "https://visagrader.com/us-visa-time-slots-availability/india-ind/chennai-P48",
    "New Delhi": "https://visagrader.com/us-visa-time-slots-availability/india-ind/new-delhi-P147",
}
INDEX_PAGE = "https://visagrader.com/us-visa-time-slots-availability/india-ind"
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_slots.json")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# ------------------------------------------


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


class FetchError(Exception):
    pass


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx()) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(str(e))


def http_post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx()) as r:
            r.read()
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(str(e))


def _ssl_ctx():
    ctx = ssl.create_default_context()
    try:
        import certifi  # optional; helps python.org installs on macOS
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


class _TableParser(HTMLParser):
    """Collects tables as lists of rows; each row is (is_header, [cell text])."""
    def __init__(self):
        super().__init__()
        self.tables, self.links = [], []
        self._row = self._cell = None
        self._is_th = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row, self._is_th = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            if tag == "th":
                self._is_th = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self.tables:
                self.tables[-1].append((self._is_th, self._row))
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_html(html):
    p = _TableParser()
    p.feed(html)
    return p


def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f)


def discover_city_pages():
    """Find Hyderabad / Kolkata (and any others) from the India index page."""
    try:
        html = http_get(INDEX_PAGE)
    except FetchError as e:
        log(f"index page failed: {e}")
        return
    for href in parse_html(html).links:
        m = re.search(r"/us-visa-time-slots-availability/india-ind/([a-z-]+)-P\d+", href)
        if m:
            name = m.group(1).replace("-", " ").title()
            url = href if href.startswith("http") else "https://visagrader.com" + href
            CITY_PAGES.setdefault(name, url)


def parse_date(text):
    text = text.strip()
    candidates = [text, text.split(",")[0].strip()]
    for fmt in ("%m/%d/%Y", "%d %b, %Y", "%d %b %Y", "%Y-%m-%d"):
        for t in candidates:
            try:
                return datetime.strptime(t, fmt).date()
            except ValueError:
                continue
    return None


def fetch_rows(city, url):
    """Return list of dicts from the 'Recent slot activity' table."""
    parsed = parse_html(http_get(url))
    rows = []
    for table in parsed.tables:
        headers = []
        for is_th, cells in table:
            if is_th:
                headers = [c.lower() for c in cells]
        if not any("visa type" in h for h in headers):
            continue
        for is_th, cells in table:
            if is_th or len(cells) < len(headers):
                continue
            row = dict(zip(headers, cells))
            rows.append({
                "city": city,
                "appt": row.get("appointment type", ""),
                "visa": row.get("visa type", ""),
                "date_text": row.get("availability date", ""),
                "slots": row.get("slots", ""),
                "reported": row.get("reported", ""),
            })
    return rows


def matches(row):
    if APPOINTMENT_TYPE not in row["appt"].lower():
        return False
    if not any(v.lower() in row["visa"].lower() for v in VISA_TYPES):
        return False
    d = parse_date(row["date_text"])
    return d is not None and d >= date.today()


def notify(row):
    d = parse_date(row["date_text"])
    urgent = d <= URGENT_BEFORE
    title = ("URGENT - BOOK NOW: " if urgent else "") + f"H-1B slot {row['city']} {d:%d %b}"
    body = (f"{row['city']} | {row['visa']} interview | {d:%d %b %Y} | "
            f"{row['slots']} slot(s) | reported {row['reported']}\n"
            "Crowd-sourced - confirm on usvisascheduling.com. Only 1 free reschedule.")
    try:
        http_post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            body.encode("utf-8"),
            {
                "Title": title,
                "Priority": "max" if urgent else "default",
                "Tags": "rotating_light" if urgent else "calendar",
                "Click": "https://www.usvisascheduling.com/",
            },
        )
        log(f"NOTIFIED: {title}")
    except FetchError as e:
        log(f"notify failed: {e}")


def check_once(seen):
    for city, url in CITY_PAGES.items():
        try:
            for row in fetch_rows(city, url):
                if not matches(row):
                    continue
                key = hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()
                if key not in seen:
                    notify(row)
                    seen.add(key)
                    save_seen(seen)
        except FetchError as e:
            log(f"{city}: fetch failed ({e})")
        except Exception as e:
            log(f"{city}: could not read page ({e})")
        time.sleep(random.uniform(2, 5))


def main():
    if "--once" in sys.argv:          # used by GitHub Actions
        discover_city_pages()
        check_once(load_seen())
        log("single check done")
        return
    if "CHANGE-ME" in NTFY_TOPIC:
        sys.exit("Edit NTFY_TOPIC in the script first (and subscribe to it in the ntfy app).")
    discover_city_pages()
    log(f"Watching: {', '.join(CITY_PAGES)} every {POLL_SECONDS}s")
    seen = load_seen()
    while True:
        try:
            check_once(seen)
        except Exception as e:          # never let one bad page stop the watcher
            log(f"unexpected error: {e}")
        time.sleep(POLL_SECONDS + random.uniform(0, 30))

if __name__ == "__main__":
    main()
