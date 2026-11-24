"""Point-in-time company news from SEC 8-K filings and macro headlines from GDELT."""

from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import universe

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "news"
CACHE.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "stonks-bot research contact@example.com"}

# Items that usually move a stock. 7.01/8.01/9.01 are routine disclosure.
MATERIAL = {
    "1.01",
    "1.02",
    "2.01",
    "2.02",
    "2.03",
    "2.04",
    "2.05",
    "2.06",
    "4.01",
    "5.01",
    "5.02",
}

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _get_json(url: str, path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    for attempt in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            if r.status_code == 200:
                path.write_text(r.text)
                return r.json()
            if r.status_code in (403, 429):
                time.sleep(2 + attempt * 3)
                continue
            return None
        except Exception:
            time.sleep(1 + attempt * 2)
    return None


def cik_map() -> dict[str, str]:
    path = CACHE / "company_tickers.json"
    data = _get_json(SEC_TICKERS_URL, path) or {}
    out = {}
    for row in data.values():
        out[str(row["ticker"]).upper()] = str(row["cik_str"])
    # prefer the current constituents table (it knows renamed tickers too)
    try:
        meta = universe.current_meta()
        for t, c in zip(meta["ticker"], meta["cik"]):
            if pd.notna(c):
                out[str(t).upper()] = str(int(c))
    except Exception:
        pass
    return out


def submissions(cik: str) -> dict | None:
    path = CACHE / f"sub_{cik}.json"
    return _get_json(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", path)


def events_for(
    cik: str, ticker: str, start: pd.Timestamp, end: pd.Timestamp,
    cutoff: pd.Timestamp | None = None,
) -> list[dict]:
    data = submissions(cik)
    if not data:
        return []
    r = data.get("filings", {}).get("recent", {})
    if not r:
        return []
    out = []
    for i, form in enumerate(r.get("form", [])):
        if form != "8-K":
            continue
        accepted = r.get("acceptanceDateTime", [None] * len(r["form"]))[i]
        filed = r.get("filingDate", [None] * len(r["form"]))[i]
        if not filed:
            continue
        d = pd.Timestamp(str(filed))
        if d < start.normalize() or d > end.normalize():
            continue
        items = [
            x.strip()
            for x in str(r.get("items", [""] * len(r["form"]))[i] or "").split(",")
            if x.strip()
        ]
        if not (set(items) & MATERIAL):
            continue
        out.append(
            {
                "ticker": ticker,
                "cik": cik,
                "filed": str(d.date()),
                "accepted": str(accepted or ""),
                "items": items,
                "accession": r.get("accessionNumber", [""] * len(r["form"]))[i],
                "primary_doc": r.get("primaryDocument", [""] * len(r["form"]))[i],
            }
        )
    out.sort(key=lambda x: (x["filed"], x["accepted"]))
    return out


def _strip_html(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text)


def filing_text(cik: str, accession: str, primary_doc: str) -> str:
    if not accession:
        return ""
    path = CACHE / f"txt_{accession.replace('-', '')}.txt"
    if path.exists():
        return path.read_text()[:3000]
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
    idx = (
        _get_json(
            base + "/index.json", CACHE / f"idx_{accession.replace('-', '')}.json"
        )
        or {}
    )
    items = idx.get("directory", {}).get("item", [])
    candidates = []
    for it in items:
        name = str(it.get("name", ""))
        if name.lower().endswith((".htm", ".html", ".txt")):
            size = int(it.get("size", 0) or 0)
            candidates.append((size, name))
    if not candidates and primary_doc:
        candidates = [(0, primary_doc)]
    # prefer exhibits (press release) and the primary document
    candidates.sort(key=lambda x: -x[0])
    text = ""
    for _, _, name in candidates[:2]:
        try:
            r = requests.get(f"{base}/{name}", headers=UA, timeout=40)
            if r.status_code != 200:
                continue
            t = _strip_html(r.text)
            if t[:200].upper().startswith("XML") or "IDEA: XBRL DOCUMENT" in t[:300]:
                continue
            if len(t) > len(text):
                text = t
        except Exception:
            continue
        time.sleep(0.12)
    text = text[:3000]
    path.write_text(text)
    time.sleep(0.12)
    return text


def attach_text(events: list[dict]) -> list[dict]:
    for e in events:
        e["text"] = filing_text(e["cik"], e["accession"], e["primary_doc"])
    return events


def macro_digest(month_start: pd.Timestamp, month_end: pd.Timestamp) -> list[dict]:
    key = f"gdelt_{month_start:%Y%m}"
    path = CACHE / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    params = {
        "query": '(Federal Reserve OR inflation OR tariff OR recession OR "stock market") sourcelang:english',
        "mode": "artlist",
        "maxrecords": 50,
        "format": "json",
        "startdatetime": month_start.strftime("%Y%m%d000000"),
        "enddatetime": month_end.strftime("%Y%m%d235959"),
    }
    for attempt in range(2):
        try:
            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=45,
            )
            if r.status_code == 200 and r.text.strip().startswith("{"):
                arts = r.json().get("articles", [])
                out = [
                    {
                        "title": a.get("title", ""),
                        "date": a.get("seendate", ""),
                        "domain": a.get("domain", ""),
                    }
                    for a in arts
                ]
                path.write_text(json.dumps(out))
                return out
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    return []
