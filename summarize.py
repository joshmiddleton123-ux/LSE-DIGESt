#!/usr/bin/env python3
"""
Enrich LSE announcements with full text and an AI one-line summary.

For each announcement from the LSE news explorer:
  1. find its full text on Investegate (which republishes every RNS),
  2. extract the announcement body and any document links in it,
  3. download linked documents (PDF or HTML) and extract their text,
  4. ask Claude for a one-sentence summary of the substance.

Summaries are cached per announcement id in digests/.cache/ so hourly runs
only pay for new items. If ANTHROPIC_API_KEY is not set, the digest is
produced without summaries.

Usage:
    python3 summarize.py --since 07:00 --out digests/latest.md --csv digests/latest.csv
"""

import argparse
import csv
import html as htmllib
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from lse_news_digest import pull_all, to_london, one_liner, CATEGORY, LONDON

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("SUMMARY_MODEL", "claude-haiku-4-5")
MAX_DOC_LINKS = 2          # documents to follow per announcement
MAX_DOC_CHARS = 30_000     # per linked document
MAX_BODY_CHARS = 20_000    # of the announcement itself
JUNK_LINK_HOSTS = ("rns.com", "lseg.com", "twitter.", "x.com", "facebook.", "linkedin.",
                   "investegate", "uknewswire", "tradingview", "stockomendation",
                   "uksharepickinggame", "google.", "youtube.", "instagram.")


def get(url, timeout=30, binary=False):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "ignore")


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (s or "").lower())).strip("-")


def strip_tags(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", htmllib.unescape(h)).strip()


# ---------------------------------------------------------------- investegate

def investegate_index(today_str, max_pages=40):
    """Map (ticker, headline-slug) and (company-slug, headline-slug) -> article url
    for all of today's announcements."""
    idx = {}
    for page in range(1, max_pages + 1):
        try:
            h = get(f"https://www.investegate.co.uk/?page={page}")
        except Exception as e:
            print(f"  investegate page {page}: {e}", file=sys.stderr)
            break
        links = re.findall(r'href="(https://www\.investegate\.co\.uk/announcement/[^"]+)"', h)
        if not links:
            break
        for url in links:
            parts = url.rstrip("/").split("/")
            if len(parts) < 7:
                continue
            company_ticker, headline_slug = parts[-3], parts[-2]
            m = re.match(r"(.*?)---?([a-z0-9.]+)$", company_ticker)
            if m:
                company_slug, ticker = m.group(1), m.group(2)
            else:
                company_slug, ticker = company_ticker, ""
            idx.setdefault((ticker, headline_slug), url)
            idx.setdefault((company_slug, headline_slug), url)
        # stop when the page no longer contains today's date
        if today_str not in h and page > 1:
            break
        time.sleep(0.4)
    return idx


def find_article(it, idx):
    tick = (it.get("companycode") or "").lower().replace(".", "")
    hslug = slugify(it.get("title"))
    cslug = slugify(it.get("companyname"))
    for key in [(tick, hslug), (cslug, hslug)]:
        if key in idx:
            return idx[key]
    # fuzzy: same ticker, headline slug prefix
    for (a, b), url in idx.items():
        if a == tick and (b.startswith(hslug[:25]) or hslug.startswith(b[:25])):
            return url
    return None


def extract_article(url):
    """Return (body_text, [doc links]) from an Investegate announcement page."""
    h = get(url)
    text = strip_tags(h)
    # Page layout: [site chrome] [Investegate's own AI summary] "Disclaimer*"
    # [actual RNS body] [footer]. Take the RNS body.
    i = text.find("Disclaimer*")
    if i > -1:
        text = text[i + len("Disclaimer*"):]
    else:
        # no AI summary block; body follows the release header
        i = text.find("Released ")
        if i > -1:
            text = text[i:]
    for end_marker in ["View more announcements", "This information is provided by RNS, the news service"]:
        j = text.find(end_marker)
        if j > -1:
            text = text[:j]
    links = []
    for l in re.findall(r'href="(https?://[^"]+)"', h):
        l = htmllib.unescape(l)
        # unwrap avanan-style safe links
        um = re.search(r"https?://url\.avanan\.click/v2/___(.+?)___", l)
        if um:
            l = um.group(1).replace("http:/", "http://").replace("https:/", "https://") \
                 .replace("http:///", "http://").replace("https:///", "https://")
        if any(j in l.lower() for j in JUNK_LINK_HOSTS):
            continue
        if l not in links:
            links.append(l)
    return text[:MAX_BODY_CHARS], links[:MAX_DOC_LINKS]


def fetch_document(url):
    """Download a linked document (PDF or HTML) and return extracted text."""
    try:
        data = get(url, timeout=45, binary=True)
    except Exception as e:
        return f"[link could not be fetched: {e}]"
    if data[:4] == b"%PDF":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = [p.extract_text() or "" for p in reader.pages[:60]]
            return re.sub(r"\s+", " ", " ".join(pages))[:MAX_DOC_CHARS]
        except Exception as e:
            return f"[pdf could not be parsed: {e}]"
    return strip_tags(data.decode("utf-8", "ignore"))[:MAX_DOC_CHARS]


# ------------------------------------------------------------------ anthropic

def claude_summary(it, body, docs):
    doc_block = ""
    for u, t in docs:
        doc_block += f"\n\nLINKED DOCUMENT ({u}):\n{t}"
    prompt = (
        "You are summarising a UK RNS regulatory announcement for a morning markets digest.\n"
        f"Company: {it.get('companyname')}  Category: {CATEGORY.get(it.get('category') or '', it.get('category'))}\n"
        f"Headline: {it.get('title')}\n\n"
        f"ANNOUNCEMENT TEXT:\n{body}{doc_block}\n\n"
        "Respond with ONLY a JSON object, no markdown fences, with exactly two keys:\n"
        '  "summary": two to three sentences (60-90 words) covering the material substance - key figures, '
        "amounts, names, percentages, dates, context, and any linked-document contents that matter. "
        "No preamble, no 'the company announced', just the substance.\n"
        '  "take": one sentence (max 30 words) on what this likely means for retail investors '
        "and how the market is likely to read it - positive, negative or non-event, and why. "
        "Plain observation, not advice. For pure boilerplate (voting rights, holdings forms) "
        'say something like "Routine disclosure; no read-through."'
    )
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({
            "model": MODEL,
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        }).encode(),
        headers={"Content-Type": "application/json",
                 "x-api-key": ANTHROPIC_KEY,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
            text = " ".join(b.get("text", "") for b in out.get("content", [])).strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
            try:
                d = json.loads(text)
                return {"summary": str(d.get("summary", "")).strip(),
                        "take": str(d.get("take", "")).strip()}
            except Exception:
                return {"summary": text[:300], "take": ""}
        except Exception as e:
            if attempt == 2:
                return {"summary": f"[summary failed: {e}]", "take": ""}
            time.sleep(3 * (attempt + 1))


# ----------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="07:00")
    ap.add_argument("--out", default="digests/latest.md")
    ap.add_argument("--csv", default="digests/latest.csv")
    ap.add_argument("--limit", type=int, default=0, help="only process first N (testing)")
    args = ap.parse_args()

    items, total = pull_all()
    today = datetime.now(LONDON).strftime("%Y-%m-%d")
    items = [it for it in items
             if to_london(it["datetime"]).strftime("%Y-%m-%d") == today
             and to_london(it["datetime"]).strftime("%H:%M") >= args.since]
    if args.limit:
        items = items[:args.limit]
    if not items:
        print("No announcements in window yet.")
        return

    cache_path = Path(f"digests/.cache/summaries-{today}.json")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    todo = [it for it in items if str(it["id"]) not in cache]
    print(f"{len(items)} announcements in window; {len(todo)} need summarising "
          f"({len(cache)} cached). API key {'present' if ANTHROPIC_KEY else 'MISSING - headline only'}.")

    if todo and ANTHROPIC_KEY:
        print("Building Investegate index…")
        idx = investegate_index(datetime.now(LONDON).strftime("%d %b").lstrip("0"))
        print(f"  indexed {len(idx)} keys")
        for n, it in enumerate(todo, 1):
            iid = str(it["id"])
            url = find_article(it, idx)
            entry = {"url": url, "summary": None}
            if url:
                try:
                    body, links = extract_article(url)
                    docs = [(l, fetch_document(l)) for l in links]
                    res = claude_summary(it, body, docs)
                    entry["summary"] = res["summary"]
                    entry["take"] = res["take"]
                    entry["links"] = links
                except Exception as e:
                    entry["summary"] = f"[enrichment failed: {e}]"
                cache[iid] = entry
            # not found on Investegate yet: leave uncached so the next
            # hourly run retries it (republication can lag a little)
            if n % 10 == 0 or n == len(todo):
                cache_path.write_text(json.dumps(cache))
                print(f"  {n}/{len(todo)} summarised", end="\r")
            time.sleep(0.4)
        print()
        cache_path.write_text(json.dumps(cache))

    # ---- write outputs
    with open(args.out, "w") as f:
        f.write(f"# LSE announcements — {today}\n\n")
        f.write(f"{len(items)} announcements ({args.since}-latest). "
                "One line each plus AI summary where available.\n\n")
        for it in items:
            f.write(f"- {one_liner(it)}\n")
            c = cache.get(str(it["id"])) or {}
            if c.get("summary"):
                f.write(f"  - {c['summary']}\n")
            if c.get("take"):
                f.write(f"  - Retail take: {c['take']}\n")

    with open(args.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_uk", "datetime_utc", "company", "ticker", "category_code",
                    "category", "headline", "rns_number", "news_id", "last_price",
                    "pct_change", "summary", "retail_take", "source_url"])
        for it in items:
            c = cache.get(str(it["id"])) or {}
            w.writerow([
                to_london(it["datetime"]).strftime("%Y-%m-%d %H:%M:%S"),
                it["datetime"], it.get("companyname"), it.get("companycode"),
                it.get("category"), CATEGORY.get(it.get("category") or "", ""),
                it["title"], it.get("rnsnumber"), it["id"], it.get("lastprice"),
                it.get("percentualchange"), c.get("summary") or "",
                c.get("take") or "", c.get("url") or "",
            ])
    print(f"Wrote {args.out} and {args.csv}")


if __name__ == "__main__":
    main()
