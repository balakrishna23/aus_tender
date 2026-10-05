#!/usr/bin/env python3
"""Build the operations dashboard: dashboard/index.html (open in a browser).

Reads the run reports (logs/runs), activity.db, releases.db and outreach_agent.db and
writes one self-contained page: no server, no external files. run_spiders.py and the
outreach agent rebuild it after every run.

  python dashboard.py            # rebuild and print the path
  python dashboard.py --open     # rebuild and open it
"""
import argparse
import html
import json
import sqlite3
import sys
import webbrowser
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from project_paths import PROJECT_DIR, database_directory

OUT_DIR = PROJECT_DIR / 'dashboard'
SPIDER_NAMES = {
    'aus_contract': 'Contract notices', 'aus_tender': 'AusTender tenders', 'planned_procurement': 'Planned procurements',
    'standing_offer': 'Standing offers', 'buy_ict': 'BuyICT', 'act_tender': 'ACT tenders', 'nsw_tender': 'NSW buy.nsw',
}
SOURCE_NAMES = {'austender_atm': 'AusTender', 'buyict': 'BuyICT', 'act': 'ACT', 'nsw': 'NSW', 'austender_plan': 'Planned'}
SHEET_NAMES = {'releases': 'New Releases', 'tenders': 'Tenders (ATM)', 'buyict': 'BuyICT', 'nsw': 'NSW',
               'act': 'ACT', 'planned': 'Planned Procurements', 'contracts': 'Contract Notices',
               'standing_offers': 'Standing Offers', 'outreach': 'Outreach'}


def e(value):
    return html.escape('' if value is None else str(value))


def query(db, sql, params=()):
    path = database_directory() / f'{db}.db'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        except sqlite3.OperationalError:
            return []


# ── Data ──────────────────────────────────────────────────────────────────────
def run_reports(limit=40):
    """Newest-last list of {'at': datetime, 'success': bool, 'results': [...], 'feed': str}."""
    reports = []
    for path in sorted((PROJECT_DIR / 'logs' / 'runs').glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            at = datetime.strptime(path.stem, '%Y%m%d-%H%M%S')
        except (ValueError, OSError):
            continue
        if len(data.get('results', [])) >= 5:  # full runs only; single-spider test runs would skew history
            reports.append(dict(data, at=at))
    return reports[-limit:]


def collect():
    reports = run_reports()
    releases = query('releases', 'SELECT * FROM releases ORDER BY first_seen_at DESC, published_on DESC')
    sends = query('outreach_agent', 'SELECT * FROM sends ORDER BY sent_at DESC')
    events = query('outreach_agent', 'SELECT * FROM events ORDER BY at DESC')
    state = {r['key']: r['value'] for r in query('outreach_agent', 'SELECT key, value FROM state')}
    shares = query('activity', 'SELECT dataset, spreadsheet_title, spreadsheet_url, total_rows, status, shared_at '
                               'FROM shares WHERE id IN (SELECT MAX(id) FROM shares GROUP BY dataset)')
    access = query('activity', "SELECT email, role, COUNT(*) AS sheets FROM access WHERE removed_at IS NULL "
                               "GROUP BY email, role ORDER BY role")
    return dict(reports=reports, releases=releases, sends=sends, events=events, state=state,
                shares=shares, access=access)


# ── Charts (inline SVG, single scale, hover tooltips via data-tip) ────────────
def nice_max(value):
    if value <= 5:
        return 5
    for step in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if value <= step:
            return step
    return int(value * 1.1)


def bar_chart(points, height=170, unit='', label_every=1):
    """points: [(x label, value, tooltip)]. Vertical bars, 4px rounded tops, 2px gaps."""
    if not points:
        return '<p class="empty">No data yet.</p>'
    width, left, bottom, top = 640, 34, 26, 10
    plot_w, plot_h = width - left - 8, height - bottom - top
    top_value = nice_max(max(v for _, v, _ in points))
    slot = plot_w / len(points)
    bar_w = max(2.0, min(28.0, slot - 2))
    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="bar chart">']
    for i in range(5):  # recessive grid + y ticks
        value = top_value * i / 4
        y = top + plot_h - plot_h * i / 4
        parts.append(f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="{"axis" if i == 0 else "grid"}"/>')
        parts.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="tick" text-anchor="end">{value:g}</text>')
    for n, (label, value, tip) in enumerate(points):
        x = left + slot * n + (slot - bar_w) / 2
        h = plot_h * value / top_value if top_value else 0
        y = top + plot_h - h
        r = min(4.0, bar_w / 2, h)
        if h > 0:
            path = (f'M{x:.1f},{top + plot_h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} '
                    f'H{x + bar_w - r:.1f} Q{x + bar_w:.1f},{y:.1f} {x + bar_w:.1f},{y + r:.1f} V{top + plot_h:.1f} Z')
            parts.append(f'<path d="{path}" class="bar"/>')
        # Hit target: the full column, larger than the mark.
        parts.append(f'<rect x="{left + slot * n:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" class="hit" '
                     f'data-tip="{e(tip)}"/>')
        if n % label_every == 0 or n == len(points) - 1:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 8}" class="tick" text-anchor="middle">{e(label)}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def sparkline(values, tips):
    """Tiny bars for one scraper's new records per run."""
    if not values:
        return ''
    width, height = 120, 28
    top = max(values) or 1
    slot = width / len(values)
    bars = []
    for n, (v, tip) in enumerate(zip(values, tips)):
        h = max(1.5, (height - 2) * v / top) if v else 1.5
        cls = 'spark' if v else 'spark zero'
        bars.append(f'<rect x="{slot * n + 0.5:.1f}" y="{height - h:.1f}" width="{max(1.5, slot - 1.5):.1f}" '
                    f'height="{h:.1f}" rx="1" class="{cls}" data-tip="{e(tip)}"/>')
    return f'<svg viewBox="0 0 {width} {height}" class="sparkline" aria-hidden="true">{"".join(bars)}</svg>'


# ── Page sections ─────────────────────────────────────────────────────────────
def neutral_pill(text):
    return f'<span class="pill"><span aria-hidden="true">○</span> {text}</span>'


def day_labels(moments):
    """Show a date under a bar only on the first run of each day."""
    seen, labels = set(), []
    for m in moments:
        key = m.strftime('%d %b')
        labels.append('' if key in seen else key)
        seen.add(key)
    return labels


def status_pill(ok, text_ok='OK', text_bad='Failed'):
    return (f'<span class="pill good"><span aria-hidden="true">●</span> {text_ok}</span>' if ok else
            f'<span class="pill bad"><span aria-hidden="true">▲</span> {text_bad}</span>')


def ago(when):
    if not when:
        return 'never'
    delta = datetime.now() - when
    if delta < timedelta(minutes=2):
        return 'just now'
    if delta < timedelta(hours=1):
        return f'{int(delta.total_seconds() // 60)} min ago'
    if delta < timedelta(days=1):
        return f'{int(delta.total_seconds() // 3600)} h ago'
    return f'{delta.days} d ago'


def parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace('T', ' ')[:19])
    except ValueError:
        return None


def pipeline(d):
    last = d['reports'][-1] if d['reports'] else None
    sent_total = sum(1 for s in d['sends'] if s['status'] in ('sent', 'replied'))
    open_releases = sum(1 for r in d['releases'] if r.get('status') == 'open')
    last_share = max((parse_time(s['shared_at']) for s in d['shares'] if s.get('shared_at')), default=None)
    last_send = parse_time(d['sends'][0]['sent_at']) if d['sends'] else None
    sheets_ok = all(s['status'] == 'ok' for s in d['shares']) if d['shares'] else False
    stages = [
        ('Scrapers', f"{sum(1 for r in last['results'] if r['status'] == 'OK') if last else 0} of "
                     f"{len(last['results']) if last else 0} OK",
         ago(last['at']) if last else 'never', bool(last and last.get('success'))),
        ('Releases feed', f'{len(d["releases"])} tenders, {open_releases} open',
         ago(parse_time(d['releases'][0]['first_seen_at'])) if d['releases'] else 'never', bool(d['releases'])),
        ('Google Sheets', f'{len(d["shares"])} sheets refreshed', ago(last_share), sheets_ok),
        ('Email agent', f'{sent_total} emails sent', ago(last_send), True),
    ]
    cells = []
    for n, (name, value, when, ok) in enumerate(stages):
        cells.append(f'<div class="stage"><div class="stage-top"><span class="stage-name">{name}</span>'
                     f'{status_pill(ok)}</div><div class="stage-value">{e(value)}</div>'
                     f'<div class="muted">last activity {e(when)}</div></div>')
        if n < len(stages) - 1:
            cells.append('<div class="arrow" aria-hidden="true">→</div>')
    return f'<section><h2>Pipeline</h2><div class="flow">{"".join(cells)}</div></section>'


def scrapers(d):
    reports = d['reports']
    if not reports:
        return '<section><h2>Scraper agents</h2><p class="empty">No runs yet.</p></section>'
    last = {r['spider']: r for r in reports[-1]['results']}
    rows = []
    for spider, name in SPIDER_NAMES.items():
        history = [(rep['at'], next((r for r in rep['results'] if r['spider'] == spider), None)) for rep in reports[-20:]]
        history = [(at, r) for at, r in history if r]
        news = [r.get('new', 0) or 0 for _, r in history]
        tips = [f"{at:%d %b %H:%M}: {r.get('new', 0)} new, {r.get('updated', 0)} updated" for at, r in history]
        r = last.get(spider)
        if not r:
            continue
        ok = r['status'] == 'OK'
        rows.append(
            f'<tr><td><b>{name}</b><div class="muted mono">{spider}</div></td><td>{status_pill(ok, "OK", e(r["status"]))}</td>'
            f'<td class="num">{r.get("new", 0)}</td><td class="num">{r.get("updated", 0)}</td>'
            f'<td class="num">{(r.get("unchanged", 0) or 0) + (r.get("skipped_final", 0) or 0)}</td>'
            f'<td class="num">{r.get("after", 0):,}</td><td class="num">{r.get("duration_seconds", 0):.0f}s</td>'
            f'<td>{sparkline(news, tips)}</td></tr>')
    totals = [(rep['at'], sum(r.get('new', 0) or 0 for r in rep['results']),
               sum(r.get('duration_seconds', 0) or 0 for r in rep['results'])) for rep in reports]
    labels = day_labels([at for at, _, _ in totals])
    chart = bar_chart([(label, new, f'{at:%d %b %H:%M}: {new} new records, run took {dur / 60:.0f} min')
                       for label, (at, new, dur) in zip(labels, totals)])
    return (f'<section><h2>Scraper agents</h2><p class="muted">Latest run {reports[-1]["at"]:%d %b %Y %H:%M} '
            f'(this PC\'s time). "Checked" = already-stored records confirmed unchanged or final.</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Status</th><th class="num">New</th>'
            f'<th class="num">Updated</th><th class="num">Checked</th><th class="num">Total stored</th>'
            f'<th class="num">Time</th><th>New per run</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<h3>New records per full run</h3>{chart}</section>')


def feed(d):
    releases = d['releases']
    days = Counter((r.get('first_seen_at') or '')[:10] for r in releases if r.get('first_seen_at'))
    start = min((date.fromisoformat(k) for k in days), default=date.today())
    span = [start + timedelta(days=i) for i in range((date.today() - start).days + 1)]
    chart = bar_chart([(d_.strftime('%d %b'), days.get(d_.isoformat(), 0),
                        f'{d_:%a %d %b}: {days.get(d_.isoformat(), 0)} new releases') for d_ in span],
                      label_every=max(1, len(span) // 8))
    by_source = Counter(SOURCE_NAMES.get(r.get('source'), r.get('source')) for r in releases)
    chips = ''.join(f'<span class="chip">{e(k)} <b>{v}</b></span>' for k, v in by_source.most_common())
    rows = ''.join(
        f'<tr><td>{e((r.get("first_seen_at") or "")[:16])}</td><td>{e(SOURCE_NAMES.get(r.get("source"), r.get("source")))}</td>'
        f'<td class="mono">{e(r.get("source_id"))}</td><td class="wrap">{e(r.get("title"))}</td><td>{e(r.get("buyer"))}</td>'
        f'<td>{e(r.get("closes_on"))}</td><td>{status_pill(True, "Open") if r.get("status") == "open" else neutral_pill(e(r.get("status")))}</td></tr>'
        for r in releases[:15])
    return (f'<section><h2>Releases feed</h2><div class="chips">{chips}</div>'
            f'<h3>New releases per day</h3>{chart}<h3>Latest releases</h3><div class="table-wrap"><table><thead><tr>'
            f'<th>First seen</th><th>Source</th><th>Reference</th><th>Title</th><th>Buyer</th><th>Closes</th><th>Status</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div></section>')


def agent(d):
    import os
    sends, events = d['sends'], d['events']
    mode = os.environ.get('OUTREACH_MODE', 'test').strip().lower() or 'test'
    today = date.today().isoformat()
    sent = [s for s in sends if s['status'] in ('sent', 'replied')]
    writers = Counter(s.get('writer') for s in sent)
    kinds = Counter(ev['kind'] for ev in events)
    tiles = [('Emails sent', len(sent)), ('Sent today', sum(1 for s in sent if (s['sent_at'] or '') >= today)),
             ('Written by AI', writers.get('ai', 0)), ('Template fallback', writers.get('template', 0)),
             ('Replies', kinds.get('reply', 0)), ('Unsubscribes', kinds.get('unsubscribe', 0)),
             ('Bounces', kinds.get('bounce', 0))]
    tile_html = ''.join(f'<div class="tile"><div class="tile-value">{v}</div><div class="muted">{k}</div></div>'
                        for k, v in tiles)
    first = d['state'].get('first_send_day')
    warm = (f'Warm-up: day {(date.today() - date.fromisoformat(first)).days + 1} of 14 (5 emails/day)'
            if first and (date.today() - date.fromisoformat(first)).days < 14 else 'Full daily cap')
    badge = ('<span class="pill warn"><span aria-hidden="true">■</span> TEST MODE: every email goes to the test inbox</span>'
             if mode != 'live' else '<span class="pill good"><span aria-hidden="true">●</span> LIVE</span>')
    rows = ''.join(
        f'<tr><td>{e((s["sent_at"] or "")[:16])}</td><td class="wrap">{e(s["intended_to"])}</td><td>{e(s["sent_to"])}</td>'
        f'<td class="wrap">{e(s["subject"])}</td><td>{e(s.get("writer"))}</td>'
        f'<td>{status_pill(s["status"] in ("sent", "replied"), e(s["status"]), e(s["status"]))}</td></tr>'
        for s in sends[:20])
    evrows = ''.join(f'<tr><td>{e(ev["at"][:16])}</td><td>{e(ev["kind"])}</td><td class="wrap">{e(ev["detail"])}</td></tr>'
                     for ev in events[:10]) or '<tr><td colspan="3" class="muted">No replies, unsubscribes or bounces yet.</td></tr>'
    return (f'<section><h2>Email agent</h2><div class="row-gap">{badge}<span class="muted">{e(warm)}</span></div>'
            f'<div class="tiles">{tile_html}</div><h3>Emails</h3><div class="table-wrap"><table><thead><tr>'
            f'<th>Sent</th><th>Meant for</th><th>Delivered to</th><th>Subject</th><th>Writer</th><th>Status</th>'
            f'</tr></thead><tbody>{rows or "<tr><td colspan=6 class=muted>None yet.</td></tr>"}</tbody></table></div>'
            f'<h3>Inbox activity</h3><div class="table-wrap"><table><thead><tr><th>When</th><th>Type</th><th>Detail</th>'
            f'</tr></thead><tbody>{evrows}</tbody></table></div></section>')


def sheets(d):
    rows = ''.join(
        f'<tr><td><a href="{e(s["spreadsheet_url"])}" target="_blank" rel="noopener">{e(SHEET_NAMES.get(s["dataset"], s["dataset"]))}</a></td>'
        f'<td class="num">{(s["total_rows"] or 0):,}</td><td>{e((s["shared_at"] or "")[:16])}</td>'
        f'<td>{status_pill(s["status"] == "ok")}</td></tr>' for s in sorted(d['shares'], key=lambda s: s['dataset']))
    people = ''.join(f'<span class="chip">{e(a["email"])} <b>{e(a["role"])}</b></span>' for a in d['access'])
    return (f'<section><h2>Google Sheets</h2><div class="table-wrap"><table><thead><tr><th>Sheet</th>'
            f'<th class="num">Rows</th><th>Last refresh</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<h3>Who can open the sheets</h3><div class="chips">{people}</div></section>')


STYLE = """
/* Layout: one column of sections; summary (pipeline) first, detail below. */
:root {
  --bg: #f6f6f3; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --series: #2a78d6; --series-soft: #cde2fb;
  --good: #0ca30c; --good-ink: #006300; --bad: #d03b3b; --warn: #fab219; --warn-ink: #7a5300;
  --font: system-ui, -apple-system, "Segoe UI", sans-serif; --mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
  --axis: #383835; --border: rgba(255,255,255,0.10); --series: #3987e5; --series-soft: #184f95;
  --good-ink: #0ca30c; --warn-ink: #fab219; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
  --axis: #383835; --border: rgba(255,255,255,0.10); --series: #3987e5; --series-soft: #184f95;
  --good-ink: #0ca30c; --warn-ink: #fab219; color-scheme: dark; }
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--ink); font: 14px/1.5 var(--font); margin: 0; }
.wrap-page { max-width: 1120px; margin: 0 auto; padding-inline: 16px; padding-block: 20px 48px; display: grid; gap: 18px; }
header h1 { font-size: 22px; margin: 0; text-wrap: balance; }
header p { margin: 4px 0 0; color: var(--ink-2); }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; min-width: 0; }
h2 { font-size: 16px; margin: 0 0 10px; }
h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--ink-2); margin: 16px 0 6px; }
.muted { color: var(--muted); font-size: 12px; }
.mono { font-family: var(--mono); font-size: 12px; }
.flow { display: flex; gap: 8px; align-items: stretch; flex-wrap: wrap; }
.stage { flex: 1 1 180px; border: 1px solid var(--border); border-radius: 8px; padding: 12px; display: grid; gap: 4px; min-width: 0; }
.stage-top { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
.stage-name { font-weight: 600; }
.stage-value { font-size: 18px; font-weight: 600; }
.arrow { align-self: center; color: var(--muted); font-size: 18px; }
.pill { display: inline-flex; gap: 5px; align-items: center; font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 99px; border: 1px solid var(--border); white-space: nowrap; }
.pill.good { color: var(--good-ink); } .pill.good span { color: var(--good); }
.pill.bad { color: var(--bad); }
.pill.warn { color: var(--warn-ink); } .pill.warn span { color: var(--warn); }
.row-gap { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
.tile { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.tile-value { font-size: 24px; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { border: 1px solid var(--border); border-radius: 99px; padding: 2px 10px; font-size: 12px; color: var(--ink-2); }
.chip b { color: var(--ink); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--axis); white-space: nowrap; }
td { padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
td.wrap { min-width: 180px; max-width: 360px; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
a { color: var(--series); }
.chart { width: 100%; height: auto; display: block; }
.chart .grid { stroke: var(--grid); stroke-width: 1; }
.chart .axis { stroke: var(--axis); stroke-width: 1; }
.chart .tick { fill: var(--muted); font-size: 10px; font-family: var(--font); }
.chart .bar { fill: var(--series); }
.chart .hit { fill: transparent; cursor: default; }
.chart .hit:hover { fill: var(--series-soft); fill-opacity: .35; }
.sparkline { width: 120px; height: 28px; display: block; }
.spark { fill: var(--series); } .spark.zero { fill: var(--axis); }
.empty { color: var(--muted); }
#tip { position: fixed; pointer-events: none; background: var(--ink); color: var(--surface); font-size: 12px; padding: 4px 8px; border-radius: 6px; max-width: 260px; z-index: 10; }
footer { color: var(--muted); font-size: 12px; text-align: center; }
"""

SCRIPT = """
(function () {
  var tip = document.getElementById('tip');
  document.addEventListener('mousemove', function (ev) {
    var t = ev.target.closest && ev.target.closest('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.getAttribute('data-tip');
    tip.hidden = false;
    var x = Math.min(ev.clientX + 12, window.innerWidth - tip.offsetWidth - 8);
    tip.style.left = x + 'px';
    tip.style.top = (ev.clientY + 14) + 'px';
  });
})();
"""


def page(d, standalone=True):
    body = (f'<div class="wrap-page"><header><h1>Tender Feed Operations</h1>'
            f'<p>Scrapers, releases feed, Google Sheets and the email agent. Generated {datetime.now():%d %b %Y %H:%M} '
            f'(this PC\'s time). Hover the bars for details.</p></header>'
            f'{pipeline(d)}{scrapers(d)}{feed(d)}{agent(d)}{sheets(d)}'
            f'<footer>Illoura Services · built by dashboard.py from the project databases</footer></div>'
            f'<div id="tip" hidden></div><script>{SCRIPT}</script>')
    head = f'<title>Tender Feed Operations</title><style>{STYLE}</style>'
    if standalone:
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">{head}</head><body>{body}</body></html>')
    return head + body  # artifact skeleton supplies doctype, head and body


def build():
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / '.env')
    data = collect()
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / 'index.html'
    path.write_text(page(data), encoding='utf-8')
    (OUT_DIR / 'artifact.html').write_text(page(data, standalone=False), encoding='utf-8')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--open', action='store_true', help='Open the dashboard in the browser')
    args = parser.parse_args(argv)
    path = build()
    print(path)
    if args.open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == '__main__':
    sys.exit(main())
