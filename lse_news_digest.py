#!/usr/bin/env python3
"""
LSE News Explorer digest.

Pulls every announcement from the London Stock Exchange news explorer
(https://www.londonstockexchange.com/news?tab=news-explorer) via the site's
internal component API, verifies nothing was missed against the server's own
totalElements count, and writes a one-line-per-announcement digest.

Usage:
    python3 lse_news_digest.py                # today's announcements
    python3 lse_news_digest.py --out digest.md --csv digest.csv
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def to_london(dt_str: str) -> datetime:
    """API datetimes are UTC; convert to UK local time."""
    return (datetime.fromisoformat(dt_str)
            .replace(tzinfo=timezone.utc).astimezone(LONDON))

API = "https://api.londonstockexchange.com/api/v1/components/refresh"
COMPONENT_ID = "block_content%3A431d02ac-09b8-40c9-aba6-04a72a4f2e49"
PAGE_PARAMS = "tab%3Dnews-explorer%26tabId%3D58734a12-d97c-40cb-8047-df76e660f23f"

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.londonstockexchange.com",
    "Referer": "https://www.londonstockexchange.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}

# Common RNS headline category codes -> plain English
CATEGORY = {
    "MSC": "Miscellaneous", "HOL": "Holding(s) in company", "TVR": "Total voting rights",
    "DSH": "Director/PDMR shareholding", "RES": "Results", "FR": "Final results",
    "IR": "Interim results", "TST": "Trading statement", "ACQ": "Acquisition",
    "DIS": "Disposal", "AGM": "AGM statement", "GMS": "General meeting statement",
    "CON": "Contract", "STR": "Strategy/company update", "POS": "Change of position",
    "BOA": "Board appointment/change", "DIV": "Dividend declaration", "NOR": "Notice of results",
    "NOA": "Notice of AGM", "IOD": "Issue of debt", "IOE": "Issue of equity",
    "ROI": "Result of issue", "ROA": "Result of AGM", "ROM": "Result of meeting",
    "SBB": "Share buyback", "POI": "Placing/open offer/issue", "TAB": "Takeover bid",
    "OFF": "Offer", "OUP": "Offer update", "RAG": "Regulatory application/grant",
    "PFU": "Portfolio update", "NAV": "Net asset value", "DRL": "Drilling/exploration",
    "SUS": "Suspension", "RST": "Restoration", "MER": "Merger", "CIR": "Circular",
    "PDI": "Price/dividend information", "EXN": "External news", "GEN": "General",
}


def fetch_page(page: int, retries: int = 4) -> dict:
    body = json.dumps({
        "path": "news",
        "parameters": PAGE_PARAMS,
        "components": [{
            "componentId": COMPONENT_ID,
            "parameters": f"page%3D{page}",
        }],
    }).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(API, data=body, headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            for c in data[0]["content"]:
                if c["name"] == "newsexplorersearch":
                    return c["value"]
            raise ValueError("newsexplorersearch block missing from response")
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            print(f"  page {page}: {e} — retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)


def pull_all() -> tuple[list[dict], int]:
    first = fetch_page(0)
    total = first["totalElements"]
    total_pages = first["totalPages"]
    items = {it["id"]: it for it in first["content"]}
    print(f"Server reports {total} announcements across {total_pages} pages.")

    for p in range(1, total_pages):
        v = fetch_page(p)
        for it in v["content"]:
            items[it["id"]] = it
        print(f"  page {p + 1}/{total_pages} — {len(items)} collected", end="\r")
        time.sleep(0.3)  # be polite
    print()

    # New announcements can land mid-pull and shift pagination; sweep page 0
    # again to catch anything that arrived, then verify count.
    v0 = fetch_page(0)
    for it in v0["content"]:
        items[it["id"]] = it
    final_total = v0["totalElements"]

    if len(items) < final_total:
        # a fresh item pushed something off the pages we saw; do a full second pass
        print("Count mismatch after first pass — re-sweeping all pages.")
        for p in range(v0["totalPages"]):
            v = fetch_page(p)
            for it in v["content"]:
                items[it["id"]] = it
            time.sleep(0.3)

    if len(items) >= final_total:
        print(f"Completeness check PASSED — {len(items)} collected vs server total {final_total}.")
    else:
        print(f"WARNING: {final_total - len(items)} short of server count.", file=sys.stderr)
    return sorted(items.values(), key=lambda x: x["datetime"], reverse=True), final_total


def one_liner(it: dict) -> str:
    t = to_london(it["datetime"]).strftime("%H:%M:%S")
    cat = CATEGORY.get(it.get("category") or "", it.get("category") or "—")
    company = it.get("companyname") or it.get("issuername") or "Unknown"
    return f"{t} | {company} | {cat} | {it['title']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="lse_digest.md")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--since", default=None, help="keep items from this UK time onwards, e.g. 07:00")
    ap.add_argument("--until", default=None, help="keep items up to this UK time, e.g. 12:00")
    args = ap.parse_args()

    items, total = pull_all()

    if args.since:
        today = datetime.now(LONDON).strftime("%Y-%m-%d")
        items = [it for it in items
                 if to_london(it["datetime"]).strftime("%Y-%m-%d") == today
                 and to_london(it["datetime"]).strftime("%H:%M") >= args.since]
    if args.until:
        items = [it for it in items if to_london(it["datetime"]).strftime("%H:%M") <= args.until]
    if not items:
        print("No announcements in the requested window yet.")
        sys.exit(0)

    window = f" in window {args.since or 'start'}-{args.until or 'now'}" if (args.since or args.until) else ""
    print(f"Collected {len(items)} announcements{window} (server total for today: {total}).")

    with open(args.out, "w") as f:
        f.write(f"# LSE announcements — {to_london(items[0]['datetime']).strftime('%Y-%m-%d')}\n\n")
        win = f" ({args.since or ''}-{args.until or 'latest'})" if (args.since or args.until) else ""
        f.write(f"{len(items)} announcements{win}. One line each: time (hh:mm:ss) | company | category | headline.\n\n")
        for it in items:
            f.write(f"- {one_liner(it)}\n")
    print(f"Wrote {args.out}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_uk", "datetime_utc", "company", "ticker", "category_code", "category",
                        "headline", "rns_number", "news_id", "last_price"])
            for it in items:
                w.writerow([
                    to_london(it["datetime"]).strftime("%Y-%m-%d %H:%M:%S"),
                    it["datetime"], it.get("companyname"), it.get("companycode"),
                    it.get("category"), CATEGORY.get(it.get("category") or "", ""),
                    it["title"], it.get("rnsnumber"), it["id"], it.get("lastprice"),
                ])
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
