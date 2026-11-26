"""Static HTML/Markdown rendering. No server, JavaScript or external assets."""

from __future__ import annotations

from html import escape
from typing import Any, Iterable

from .collect import BookSnapshot, DashboardData, Finding


def _e(value: Any) -> str:
    return escape("" if value is None else str(value), quote=True)


def _md(value: Any) -> str:
    return str("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def _money(value: float | None, currency: str = "") -> str:
    if value is None:
        return "—"
    suffix = f" {_e(currency)}" if currency else ""
    return f"{value:,.2f}{suffix}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def _weight(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _bool(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "no")


def _status_class(status: str) -> str:
    value = status.lower()
    if value in {"ok", "pass", "healthy"}:
        return "ok"
    if value in {"warn", "warning", "risk_off", "degraded", "unknown", "wait", "info"}:
        return "warn" if value != "info" else "info"
    return "fail"


def _findings(data: DashboardData) -> list[Finding]:
    return sorted(
        data.findings,
        key=lambda f: (
            {"FAIL": 0, "WARN": 1, "INFO": 2, "OK": 3}.get(f.level, 4),
            f.code,
        ),
    )


def _gate_text(book: BookSnapshot) -> str:
    return book.gate.text or "unknown"


def _book_summary_rows(data: DashboardData) -> str:
    rows = []
    for b in data.books:
        rows.append(
            "<tr>"
            f"<td><strong>{_e(b.book)}</strong><br><span class='muted'>{_e(b.market)}</span></td>"
            f"<td><span class='badge {_status_class(b.status)}'>{_e(b.status)}</span></td>"
            f"<td>{_money(b.equity, b.currency)}</td>"
            f"<td>{_money(b.equity_eur, 'EUR')}</td>"
            f"<td>{_pct(b.drawdown)}</td>"
            f"<td>{_weight(b.gross)}</td>"
            f"<td><span class='badge {_status_class('ok' if b.gate.passed else 'wait')}'>"
            f"{_e(_gate_text(b))}</span></td>"
            f"<td>{_e(b.reconcile)}</td>"
            f"<td>{_e((b.last_run or {}).get('session') or b.last_session or '—')}</td>"
            f"<td>{_e(b.next_session or '—')}</td>"
            f"<td>{_bool(b.data_fresh)}</td>"
            "</tr>"
        )
    return (
        "".join(rows)
        if rows
        else "<tr><td colspan='11' class='muted'>No live books found.</td></tr>"
    )


def _book_detail(b: BookSnapshot) -> str:
    target_rows = []
    position_by_ticker = {p["ticker"]: p for p in b.positions}
    for ticker in sorted(set(b.targets) | set(position_by_ticker)):
        target = b.targets.get(ticker)
        pos = position_by_ticker.get(ticker, {})
        value = pos.get("value", 0.0) or 0.0
        current = value / b.equity if b.equity else None
        target_rows.append(
            "<tr>"
            f"<td>{_e(ticker)}</td><td>{_weight(target)}</td>"
            f"<td>{_weight(current)}</td><td>{_money(value, b.currency)}</td>"
            f"<td>{_e(pos.get('qty', '—'))}</td><td>{_money(pos.get('avg_cost'), b.currency)}</td>"
            f"<td>{_money(pos.get('last_price'), b.currency)}</td>"
            "</tr>"
        )
    if not target_rows:
        target_rows.append(
            "<tr><td colspan='7' class='muted'>No positions or targets recorded.</td></tr>"
        )
    orders = "".join(
        "<tr>"
        f"<td>{_e(o.get('ticker'))}</td><td>{_e(o.get('side'))}</td>"
        f"<td>{_e(o.get('qty'))}</td><td>{_e(o.get('ref_price'))}</td>"
        f"<td>{_e(o.get('status'))}</td>"
        "</tr>"
        for o in b.open_orders
    )
    if not orders:
        orders = "<tr><td colspan='5' class='muted'>No open orders.</td></tr>"
    warnings = (
        "".join(f"<li>{_e(w)}</li>" for w in b.warnings)
        or "<li class='muted'>None.</li>"
    )
    return (
        "<section class='card'>"
        f"<h2>{_e(b.book)} / {_e(b.market)} "
        f"<span class='badge {_status_class(b.status)}'>{_e(b.status)}</span></h2>"
        f"<p class='muted'>state: {_e(b.state_file or '—')} · updated: {_e(b.updated_at or '—')} "
        f"· ledger rows: {b.ledger_rows} · FX: {_e(b.fx_rate)} ({_e(b.fx_date or '—')})</p>"
        "<div class='grid2'>"
        "<div><h3>Targets vs positions</h3><table><thead><tr>"
        "<th>ticker</th><th>target</th><th>current</th><th>value</th><th>qty</th><th>avg</th><th>last</th>"
        f"</tr></thead><tbody>{''.join(target_rows)}</tbody></table></div>"
        "<div><h3>Open orders</h3><table><thead><tr><th>ticker</th><th>side</th><th>qty</th>"
        f"<th>ref</th><th>status</th></tr></thead><tbody>{orders}</tbody></table>"
        f"<h3>Warnings</h3><ul>{warnings}</ul></div>"
        "</div></section>"
    )


def _eval_html(data: DashboardData) -> str:
    ev = data.eval
    baseline = ""
    if ev.baseline:
        baseline = (
            "<table><thead><tr><th>market</th><th>book</th><th>native</th>"
            "<th>terminal-FX</th><th>EUR ledger (real-life)</th></tr></thead><tbody>"
            + "".join(
                "<tr>"
                f"<td>{_e(r.get('market'))}</td><td>{_e(r.get('book'))}</td>"
                f"<td>{_e(r.get('native'))}x</td><td>{_e(r.get('terminal_fx'))}x</td>"
                f"<td><strong>{_e(r.get('eur_ledger'))}x</strong></td>"
                "</tr>"
                for r in ev.baseline
            )
            + "</tbody></table>"
        )
    else:
        baseline = "<p class='muted'>No baseline rows available.</p>"
    dq = ev.data_quality
    dq_text = "not available"
    if dq:
        counts = dq.get("warning_counts") or {}
        dq_text = (
            f"{'PASS' if dq.get('ok') else 'ATTENTION'} · "
            f"{len(dq.get('hard_failures') or [])} hard failures · "
            f"{sum(counts.values()) if counts else len(dq.get('warnings') or [])} warnings"
        )
    surv = ev.survivorship
    surv_text = "not available"
    if surv:
        facts = surv.get("facts") or {}
        us = surv.get("us_missing") or {}
        surv_text = (
            f"TECH static {facts.get('n_static', '?')} names; "
            f"{facts.get('n_in_sp500_at_start', '?')} were S&P members at start; "
            f"US missing {us.get('missing', '?')}/{us.get('unique_members', '?')} "
            f"({(us.get('missing_share') or 0) * 100:.1f}%)."
        )
    return (
        "<section class='card'><h2>Research evidence</h2>"
        "<h3>Baseline after-tax multiples</h3>"
        f"{baseline}"
        "<div class='grid2'><div><h3>Data quality</h3>"
        f"<p>{_e(dq_text)}</p></div>"
        "<div><h3>Survivorship</h3>"
        f"<p>{_e(surv_text)}</p><p class='muted'>Trial registry rows: {_e(ev.trials if ev.trials is not None else 'n/a')}</p>"
        "</div></div>"
        "<p class='muted'>The EUR ledger is the real-life column. Terminal-FX is a legacy upper bound, "
        "not a real-life result. Eval warnings are listed above.</p></section>"
    )


def render_html(data: DashboardData, title: str = "stonks-bot dashboard") -> str:
    """Render one self-contained HTML page; every dynamic value is escaped."""
    findings = _findings(data)
    finding_rows = (
        "".join(
            "<tr>"
            f"<td><span class='badge {_status_class(f.level)}'>{_e(f.level)}</span></td>"
            f"<td>{_e(f.code)}</td><td>{_e(f.message)}</td><td>{_e(f.source)}</td>"
            "</tr>"
            for f in findings
        )
        or "<tr><td colspan='4' class='muted'>No findings.</td></tr>"
    )
    banner = (
        "<div class='banner'>SYNTHETIC DEMO — this page is illustrative, not live state or a forward result.</div>"
        if data.synthetic
        else ""
    )
    book_rows = _book_summary_rows(data)
    detail = "".join(_book_detail(b) for b in data.books)
    eval_html = _eval_html(data)
    overall = data.overall
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
:root {{ color-scheme: light; --fg:#17202a; --muted:#667085; --line:#d9e0ea; --bg:#f6f8fb; --card:#fff; --ok:#16794b; --warn:#9a6700; --fail:#b42318; --accent:#2457a6; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
main {{ max-width:1280px; margin:0 auto; padding:28px 20px 48px; }}
h1 {{ margin:0 0 4px; font-size:28px; letter-spacing:-.02em; }}
h2 {{ margin:0 0 14px; font-size:20px; }}
h3 {{ margin:18px 0 8px; font-size:15px; }}
p {{ margin:6px 0; }}
.muted {{ color:var(--muted); }}
.banner {{ background:#fff3cd; border:1px solid #e6c76a; border-radius:8px; padding:10px 14px; margin:14px 0; font-weight:600; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:700; white-space:nowrap; }}
.badge.ok {{ background:#e6f4ec; color:var(--ok); }}
.badge.warn {{ background:#fff4d5; color:var(--warn); }}
.badge.fail {{ background:#fde8e7; color:var(--fail); }}
.badge.info {{ background:#eaf1fb; color:var(--accent); }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; margin:18px 0; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin:14px 0; overflow:auto; }}
.metric {{ font-size:22px; font-weight:750; }}
.grid2 {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:18px; }}
table {{ width:100%; border-collapse:collapse; margin:8px 0; }}
th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:6px 7px; vertical-align:top; }}
th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.03em; }}
td {{ font-variant-numeric:tabular-nums; }}
tr:last-child td {{ border-bottom:0; }}
code {{ background:#eef2f7; padding:1px 4px; border-radius:4px; }}
footer {{ margin-top:26px; color:var(--muted); font-size:12px; }}
@media (max-width:700px) {{ h1 {{ font-size:23px; }} .grid2 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body><main>
{banner}
<h1>{_e(title)}</h1>
<p class="muted">Generated {_e(data.generated_at)} · mode {_e(data.mode)} · <span class="badge {_status_class(overall)}">{_e(overall)}</span></p>
<section class="cards">
<div class="card"><div class="muted">Session</div><div class="metric">{_e(data.session or "—")}</div></div>
<div class="card"><div class="muted">Next session</div><div class="metric">{_e(data.next_session or "—")}</div></div>
<div class="card"><div class="muted">Books</div><div class="metric">{len(data.books)}</div></div>
<div class="card"><div class="muted">Warnings / failures</div><div class="metric">{len(data.warnings)} / {len(data.failures)}</div></div>
</section>
<section class="card"><h2>Books, risk, data and gate</h2>
<table><thead><tr><th>book / market</th><th>status</th><th>equity</th><th>equity EUR</th><th>drawdown</th><th>gross</th><th>forward gate</th><th>reconcile</th><th>last run</th><th>next</th><th>data fresh</th></tr></thead>
<tbody>{book_rows}</tbody></table></section>
{detail}
<section class="card"><h2>Findings and warnings</h2>
<table><thead><tr><th>level</th><th>code</th><th>message</th><th>source</th></tr></thead>
<tbody>{finding_rows}</tbody></table></section>
{eval_html}
<footer>Read-only static page generated by <code>scripts/dashboard.py</code>. No server, external assets, secrets or order execution. Sources: {_e(", ".join(data.sources))}</footer>
</main></body></html>
"""


def render_markdown(data: DashboardData, title: str = "stonks-bot dashboard") -> str:
    """Render the same evidence as Markdown, with pipe/newline escaping."""
    lines = [
        f"# {title}",
        "",
    ]
    if data.synthetic:
        lines += [
            "> **SYNTHETIC DEMO — illustrative only, not live state or a forward result.**",
            "",
        ]
    lines += [
        f"Generated: {_md(data.generated_at)}  ",
        f"Mode: {_md(data.mode)}  ",
        f"Overall: **{data.overall}**  ",
        f"Session: {_md(data.session or '—')}  ",
        f"Next session: {_md(data.next_session or '—')}",
        "",
        "## Books, risk, data and gate",
        "",
        "| book | market | status | equity | equity EUR | drawdown | gross | forward gate | reconcile | last run | next | data fresh |",
        "|---|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for b in data.books:
        lines.append(
            "| "
            + " | ".join(
                _md(x)
                for x in (
                    b.book,
                    b.market,
                    b.status,
                    _money(b.equity, b.currency),
                    _money(b.equity_eur, "EUR"),
                    _pct(b.drawdown),
                    _weight(b.gross),
                    _gate_text(b),
                    b.reconcile,
                    (b.last_run or {}).get("session") or b.last_session or "—",
                    b.next_session or "—",
                    _bool(b.data_fresh),
                )
            )
            + " |"
        )
    if not data.books:
        lines.append("| — | — | — | — | — | — | — | — | — | — | — | — |")
    lines += ["", "## Book details", ""]
    for b in data.books:
        lines += [
            f"### {_md(b.book)} / {_md(b.market)}",
            "",
            f"- Status: **{_md(b.status)}**",
            f"- State: `{_md(b.state_file or '—')}` · updated {_md(b.updated_at or '—')} · ledger rows {b.ledger_rows}",
            f"- Risk: drawdown {_pct(b.drawdown)} · risk_off **{b.risk_off}** · reason {_md(b.risk_reason or '—')}",
            f"- FX: {_md(b.fx_rate)} ({_md(b.fx_date or '—')}) · data fresh: {_bool(b.data_fresh)} · last bar {_md(b.last_bar or '—')}",
            f"- Forward gate: {_md(_gate_text(b))} · reconcile: {_md(b.reconcile)}",
            "",
        ]
        if b.targets or b.positions:
            lines += [
                "| ticker | target | current | value | qty | avg | last |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
            pos = {p["ticker"]: p for p in b.positions}
            for ticker in sorted(set(b.targets) | set(pos)):
                p = pos.get(ticker, {})
                value = p.get("value", 0.0) or 0.0
                current = value / b.equity if b.equity else None
                lines.append(
                    "| "
                    + " | ".join(
                        _md(x)
                        for x in (
                            ticker,
                            _weight(b.targets.get(ticker)),
                            _weight(current),
                            _money(value, b.currency),
                            p.get("qty", "—"),
                            _money(p.get("avg_cost"), b.currency),
                            _money(p.get("last_price"), b.currency),
                        )
                    )
                    + " |"
                )
            lines.append("")
        if b.open_orders:
            lines += [
                "| ticker | side | qty | ref | status |",
                "|---|---|---:|---:|---|",
            ]
            for o in b.open_orders:
                lines.append(
                    "| "
                    + " | ".join(
                        _md(o.get(k))
                        for k in ("ticker", "side", "qty", "ref_price", "status")
                    )
                    + " |"
                )
            lines.append("")
        if b.warnings:
            lines += [f"- WARN: {_md(w)}" for w in b.warnings]
            lines.append("")
    lines += ["## Findings and warnings", ""]
    if data.findings:
        lines += [
            "| level | code | message | source |",
            "|---|---|---|---|",
        ]
        for f in _findings(data):
            lines.append(
                f"| {_md(f.level)} | {_md(f.code)} | {_md(f.message)} | {_md(f.source)} |"
            )
    else:
        lines.append("No findings.")
    ev = data.eval
    lines += ["", "## Research evidence", "", "### Baseline after-tax multiples", ""]
    if ev.baseline:
        lines += [
            "| market | book | native | terminal-FX | EUR ledger (real-life) |",
            "|---|---|---:|---:|---:|",
        ]
        for r in ev.baseline:
            lines.append(
                f"| {_md(r.get('market'))} | {_md(r.get('book'))} | "
                f"{_md(r.get('native'))}x | {_md(r.get('terminal_fx'))}x | "
                f"**{_md(r.get('eur_ledger'))}x** |"
            )
    else:
        lines.append("No baseline rows available.")
    dq = ev.data_quality or {}
    counts = dq.get("warning_counts") or {}
    lines += [
        "",
        f"- Data quality: **{'PASS' if dq.get('ok') else 'ATTENTION' if dq else 'n/a'}** · "
        f"{len(dq.get('hard_failures') or [])} hard failures · "
        f"{sum(counts.values()) if counts else len(dq.get('warnings') or [])} warnings",
    ]
    facts = (ev.survivorship or {}).get("facts") or {}
    us = (ev.survivorship or {}).get("us_missing") or {}
    lines.append(
        f"- Survivorship: TECH static {facts.get('n_static', '?')} names; "
        f"US missing {us.get('missing', '?')}/{us.get('unique_members', '?')} "
        f"({(us.get('missing_share') or 0) * 100:.1f}%)."
    )
    lines.append(
        f"- Trial registry rows: {ev.trials if ev.trials is not None else 'n/a'}"
    )
    lines += [
        "- The EUR ledger is the real-life column; terminal-FX is a legacy upper bound.",
        "",
        f"_Read-only static report. Sources: {_md(', '.join(data.sources))}_",
        "",
    ]
    return "\n".join(lines)
