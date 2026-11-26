"""Constrained LLM overlay: direction + confidence, or 'none'."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "llm"
CACHE.mkdir(parents=True, exist_ok=True)
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
HORIZON_DAYS = 63
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _clean(text: str) -> str:
    return ANSI.sub("", text)


def _extract_json(text: str):
    text = _clean(text).strip()
    for start, open_ch in enumerate(["[", "{"]):
        i = text.find(open_ch)
        if i < 0:
            continue
        close_ch = "]" if open_ch == "[" else "}"
        depth = 0
        for j in range(i, len(text)):
            if text[j] == open_ch:
                depth += 1
            elif text[j] == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i : j + 1])
                    except Exception:
                        break
    return None


def ask(prompt: str, model: str = DEFAULT_MODEL, timeout: int = 240) -> str:
    key = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()[:24]
    path = CACHE / f"{key}.json"
    if path.exists():
        try:
            cached = json.loads(path.read_text())["text"]
            # Never let a failed call poison the cache: retry it next run.
            if cached.strip() and not cached.startswith("ERROR "):
                return cached
        except Exception:
            pass
    cmd = ["opencode", "run", "--format", "json", "--archived", "-m", model, prompt]
    last = ""
    ok = False
    for attempt in range(2):
        ok = False
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = _clean(p.stdout)
            texts = []
            for line in out.splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                part = ev.get("part", {})
                if part.get("type") == "text" and part.get("text"):
                    texts.append(part["text"])
            last = "\n".join(texts) if texts else out
            ok = True
        except Exception as e:
            last = f"ERROR {e}"
        if ok and last.strip():
            break
    if not ok or not last.strip():
        return last
    path.write_text(
        json.dumps({"prompt": prompt, "model": model, "text": last}, indent=1)
    )
    return last


def build_prompt(
    date: pd.Timestamp, items: list[dict], macro: list[dict], placebo: bool = False
) -> str:
    lines = [
        f"You are a portfolio analyst. Today is {date:%Y-%m-%d}.",
        "Use only information dated on or before today. Do not use knowledge of later events.",
        "",
        "For each stock below, say whether it will outperform (up) or underperform (down) the S&P 500",
        f"over the next {HORIZON_DAYS} trading days, and your confidence.",
        'If the evidence does not support a clear 3-month view, answer "none". Most names should be "none".',
        "confidence is the probability your stated direction is right, between 0.50 and 0.95.",
        "",
        "Return ONLY a JSON array with one object per ticker and exactly these keys:",
        '{"ticker":"...","direction":"up"|"down"|"none","confidence":0.0,"reason":"<=25 words"}',
        "",
    ]
    if not placebo and macro:
        lines.append("Macro headlines this month:")
        for m in macro[:12]:
            lines.append(f"- {m.get('title', '')[:150]}")
        lines.append("")
    for it in items:
        lines.append(f"## {it['ticker']} ({it.get('sector', 'Unknown')})")
        lines.append(
            f"20d return {it.get('ret20', float('nan')):.1%}, 60d return {it.get('ret60', float('nan')):.1%}, "
            f"annualized vol {it.get('vol', float('nan')):.2f}"
        )
        if not placebo:
            evs = it.get("events", [])
            if evs:
                lines.append("Recent material filings:")
                for e in evs[-3:]:
                    txt = (e.get("text") or "")[:700]
                    lines.append(f"- {e['filed']} items {','.join(e['items'])}: {txt}")
            else:
                lines.append("No material filings since the last review.")
        lines.append("")
    lines.append("JSON:")
    return "\n".join(lines)


def judge_batch(
    items: list[dict],
    date: pd.Timestamp,
    macro: list[dict],
    model: str = DEFAULT_MODEL,
    placebo: bool = False,
) -> list[dict]:
    prompt = build_prompt(date, items, macro, placebo=placebo)
    raw = ask(prompt, model=model)
    obj = _extract_json(raw)
    if isinstance(obj, dict):
        obj = [obj]
    if not isinstance(obj, list):
        return []
    want = {it["ticker"] for it in items}
    out = []
    for row in obj:
        if not isinstance(row, dict):
            continue
        t = str(row.get("ticker", "")).upper()
        if t not in want:
            continue
        direction = str(row.get("direction", "none")).lower()
        if direction not in ("up", "down", "none"):
            direction = "none"
        try:
            conf = float(row.get("confidence", 0.5))
        except Exception:
            conf = 0.5
        conf = max(0.5, min(0.95, conf))
        out.append(
            {
                "ticker": t,
                "direction": direction,
                "confidence": conf if direction != "none" else 0.0,
                "reason": str(row.get("reason", ""))[:200],
                "asof": str(date.date()),
                "model": model,
                "placebo": placebo,
            }
        )
    if out:
        with (RESULTS / "llm_calls.jsonl").open("a") as f:
            for row in out:
                f.write(json.dumps(row | {"prompt": prompt[:4000]}) + "\n")
    return out


# ---------- judgments + overlay ----------


def load_judgments(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "ticker",
                "direction",
                "confidence",
                "asof",
                "model",
                "placebo",
                "reason",
            ]
        )
    df = pd.read_json(path, lines=True)
    df["asof"] = pd.to_datetime(df["asof"])
    if "placebo" in df.columns and "model" in df.columns:
        df = df.drop_duplicates(["ticker", "asof", "placebo", "model"], keep="last")
    df["expiry"] = df["asof"] + pd.Timedelta(days=90)
    return df


def make_overlay(
    judgments: pd.DataFrame, max_active: float = 0.20, allow_add: bool = True
):
    j = judgments
    j = j[j["direction"] != "none"]

    def active(ticker: str, date: pd.Timestamp):
        sub = j[(j["ticker"] == ticker) & (j["asof"] <= date) & (j["expiry"] >= date)]
        if sub.empty:
            return None
        return sub.sort_values("asof").iloc[-1]

    def overlay(date: pd.Timestamp, base: dict[str, float], mkt) -> dict[str, float]:
        adj = {}
        for t, w in base.items():
            row = active(t, date)
            if row is None:
                adj[t] = w
                continue
            c = float(row["confidence"])
            if row["direction"] == "down" and c >= 0.60:
                adj[t] = 0.0
            elif row["direction"] == "down":
                adj[t] = w * (1 - 0.5 * c)
            else:
                adj[t] = w * (1 + 0.5 * c)
        if allow_add and base and len(adj) < 25:
            from .strategy import eligible

            df = eligible(mkt, date)
            adds = []
            for t in df.index:
                if t in adj or len(adds) >= 2:
                    continue
                row = active(t, date)
                if (
                    row is not None
                    and row["direction"] == "up"
                    and float(row["confidence"]) >= 0.70
                ):
                    adds.append(t)
            if adds:
                need = 0.03 * len(adds)
                scale = max(0.0, 1.0 - need / max(sum(adj.values()), 1e-9))
                for t in adj:
                    adj[t] *= scale
                for t in adds:
                    adj[t] = 0.03
        # cap total deviation from the math portfolio
        dev = sum(abs(adj.get(t, 0.0) - base.get(t, 0.0)) for t in set(adj) | set(base))
        if dev > max_active and dev > 0:
            k = max_active / dev
            adj = {
                t: base.get(t, 0.0) + k * (adj.get(t, 0.0) - base.get(t, 0.0))
                for t in set(adj) | set(base)
            }
        adj = {t: max(0.0, w) for t, w in adj.items() if w > 1e-4}
        return adj

    return overlay


@dataclass
class Calibration:
    n: int
    abstain_rate: float
    up_rate: float
    brier: float
    brier_base: float
    hit_21: float
    hit_63: float


def calibrate(
    judgments: pd.DataFrame, mkt, horizon_days: int = 63
) -> tuple[Calibration, pd.DataFrame]:
    rows = []
    for _, r in judgments.iterrows():
        d = pd.Timestamp(r["asof"])
        if d not in mkt.px.index or r["ticker"] not in mkt.px.columns:
            continue
        start = mkt.px.at[d, r["ticker"]]
        if pd.isna(start):
            continue
        fwd = mkt.px[r["ticker"]].shift(-horizon_days) / start - 1
        spyf = mkt.spy.shift(-horizon_days) / mkt.spy.loc[d] - 1
        idx = mkt.px.index.get_loc(d)
        j = min(idx + horizon_days, len(mkt.px) - 1)
        end_d = mkt.px.index[j]
        if end_d <= d or end_d >= mkt.px.index[-1]:
            continue
        fwd = mkt.px.at[end_d, r["ticker"]] / start - 1
        spyf = mkt.spy.at[end_d] / mkt.spy.loc[d] - 1
        if pd.isna(fwd) or pd.isna(spyf):
            continue
        excess = fwd - spyf
        if r["direction"] == "none":
            rows.append({**r, "excess": excess, "correct": None})
        else:
            correct = (excess > 0) if r["direction"] == "up" else (excess < 0)
            rows.append({**r, "excess": excess, "correct": bool(correct)})
    df = pd.DataFrame(rows)
    if df.empty:
        return Calibration(0, 0, 0, 0, 0, 0, 0), df
    called = df[df["direction"] != "none"]
    if called.empty:
        return Calibration(len(df), 1.0, 0, 0, 0, 0, 0), df
    y = called["correct"].astype(int)
    p = called["confidence"].astype(float)  # P(stated direction correct)
    brier = float(((p - y) ** 2).mean())
    base = float(y.mean())
    brier_base = float(((base - y) ** 2).mean())
    return Calibration(
        n=len(df),
        abstain_rate=float((df["direction"] == "none").mean()),
        up_rate=float((called["direction"] == "up").mean()),
        brier=brier,
        brier_base=brier_base,
        hit_21=float(called["correct"].mean()),
        hit_63=float(called["correct"].mean()),
    ), df
