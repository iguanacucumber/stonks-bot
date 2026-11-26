"""Point-in-time news cutoff, filing text selection, and LLM cache failures."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import llm, news  # noqa: E402


def main() -> None:
    # A filing accepted after the 16:00 ET signal close must wait one session.
    fake = {
        "filings": {
            "recent": {
                "form": ["8-K", "8-K", "10-Q"],
                "filingDate": ["2026-04-30", "2026-04-30", "2026-04-30"],
                "acceptanceDateTime": [
                    "2026-04-30T15:00:00.000Z",
                    "2026-04-30T20:05:00.000Z",
                    "2026-04-30T15:00:00.000Z",
                ],
                "items": ["2.02", "1.01", ""],
                "accessionNumber": ["before", "after", "ignored"],
                "primaryDocument": ["a.htm", "b.htm", "c.htm"],
            }
        }
    }
    old_submissions = news.submissions
    news.submissions = lambda cik: fake
    try:
        d = pd.Timestamp("2026-04-30")
        evs = news.events_for("1", "T", d, d, cutoff=d + pd.Timedelta(hours=16))
        assert [e["accession"] for e in evs] == ["before"], evs
        all_evs = news.events_for("1", "T", d, d)
        assert [e["accession"] for e in all_evs] == ["before", "after"], all_evs
    finally:
        news.submissions = old_submissions

    # filing_text used to unpack two-tuples into three names and crash.
    with tempfile.TemporaryDirectory() as tmp:
        old_cache = news.CACHE
        news.CACHE = Path(tmp)
        old_get, old_get_req = news._get_json, news.requests.get
        old_sleep = news.time.sleep
        try:
            news._get_json = lambda url, path: {
                "directory": {"item": [{"name": "doc.htm", "size": "200"}]}
            }

            class Response:
                status_code = 200
                text = "<html><body>Quarterly <b>results</b></body></html>"

            news.requests.get = lambda *args, **kwargs: Response()
            news.time.sleep = lambda seconds: None
            text = news.filing_text("123", "0001-26-000001", "doc.htm")
            assert "Quarterly results" in text, text
        finally:
            news.CACHE = old_cache
            news._get_json = old_get
            news.requests.get = old_get_req
            news.time.sleep = old_sleep

    # A failed LLM subprocess must be retried, not cached as the final answer.
    with tempfile.TemporaryDirectory() as tmp:
        old_llm_cache = llm.CACHE
        old_run = llm.subprocess.run
        llm.CACHE = Path(tmp)
        calls = []

        def fail(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("boom")

        llm.subprocess.run = fail
        try:
            first = llm.ask("hello", model="test-model")
            second = llm.ask("hello", model="test-model")
            assert first.startswith("ERROR ") and second.startswith("ERROR ")
            assert len(calls) == 4, f"failed call was cached: {len(calls)} attempts"
            assert list(Path(tmp).iterdir()) == [], "error response written to cache"
        finally:
            llm.CACHE = old_llm_cache
            llm.subprocess.run = old_run

    print("ok: filing cutoff, filing text, LLM failures are retried not cached")


if __name__ == "__main__":
    main()
