from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from statistics import mean

import yaml

from . import logging_config
from .metrics import percentile

ROOT = Path(__file__).resolve().parents[1]


def read_window(path: Path, start: datetime, end: datetime) -> tuple[list[dict], int]:
    records, skipped = [], 0
    if not path.exists():
        return records, skipped
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                ts = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    raise ValueError("Missing timezone")
                if start <= ts <= end:
                    records.append(record)
            except (ValueError, TypeError, KeyError):
                skipped += 1
    return records, skipped


def aggregate(records: list[dict]) -> dict:
    received = [r for r in records if r.get("event") == "request_received"]
    responses = [r for r in records if r.get("event") == "response_sent"]
    failed = [r for r in records if r.get("event") == "request_failed"]
    tools = [r for r in responses + failed if r.get("tool_name") == "retrieval"
             and isinstance(r.get("tool_success"), bool)]

    def values(field):
        return [r[field] for r in responses if isinstance(r.get(field), (int, float))]

    latency, ttft, quality = values("latency_ms"), values("ttft_ms"), values("quality_score")
    return {
        "latency": {**{f"p{p}": percentile(latency, p) if latency else None for p in (50, 95, 99)},
                    "ttft_p95": percentile(ttft, 95) if ttft else None},
        "traffic": {"count": len(received)},
        "errors": {"error_rate_pct": len(failed) / len(received) * 100 if received else None,
                   "tool_success_rate_pct": sum(r["tool_success"] for r in tools) / len(tools) * 100 if tools else None,
                   "breakdown": dict(Counter(r.get("error_type", "Unknown") for r in failed))},
        "cost": {"total": sum(values("cost_usd"))},
        "tokens": {"input": sum(values("tokens_in")), "output": sum(values("tokens_out"))},
        "quality": {"mean": mean(quality) if quality else None},
    }


def chart(series: dict[str, list], threshold: float) -> str:
    colors = ["#2563eb", "#dc2626", "#059669", "#9333ea"]
    top = max([threshold, 0.001] + [v for values in series.values() for v in values if v is not None]) * 1.12
    def y(value):
        return 130 - value / top * 110
    result = [f'<svg viewBox="0 0 560 160" role="img" aria-label="Minute series and threshold">',
              f'<text x="4" y="14">{top:.3g}</text>',
              '<line x1="35" y1="130" x2="550" y2="130" stroke="#aaa"/>',
              f'<line x1="35" y1="{y(threshold):.2f}" x2="550" y2="{y(threshold):.2f}" stroke="#888" stroke-dasharray="5 4"/>',
              '<text x="35" y="153">-60 min</text><text x="500" y="153">Now</text>']
    for (name, values), color in zip(series.items(), colors):
        # Missing samples break the line rather than implying zero latency/quality.
        segment = []
        for index, value in enumerate(values + [None]):
            if value is None:
                if segment:
                    result.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(segment)}"/>')
                    segment = []
            else:
                x = 35 + index * 515 / max(1, len(values) - 1)
                segment.append(f"{x:.2f},{y(value):.2f}")
                result.append(f'<circle cx="{x:.2f}" cy="{y(value):.2f}" r="2" fill="{color}"/>')
    result.append('</svg><div class="legend">')
    for name, color in zip(series, colors):
        result.append(f'<span style="color:{color}">{escape(name)}</span> ')
    return "".join(result) + "</div>"


def render_dashboard() -> str:
    config = yaml.safe_load((ROOT / "config/dashboard.yaml").read_text(encoding="utf-8"))["dashboard"]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=config["time_range_minutes"])
    records, skipped = read_window(logging_config.LOG_PATH, start, end)
    summary = aggregate(records)
    buckets = [[] for _ in range(60)]
    for record in records:
        ts = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
        buckets[min(59, int((ts - start).total_seconds() // 60))].append(record)
    minutes = [aggregate(bucket) for bucket in buckets]
    series = {
        "latency": {k: [m["latency"][k] for m in minutes] for k in ("p50", "p95", "p99", "ttft_p95")},
        "traffic": {"requests/min": [m["traffic"]["count"] for m in minutes]},
        "errors": {k: [m["errors"][k] for m in minutes] for k in ("error_rate_pct", "tool_success_rate_pct")},
        "cost": {"cumulative USD": [sum(m["cost"]["total"] for m in minutes[:i + 1]) for i in range(60)],
                 "USD/min": [m["cost"]["total"] for m in minutes]},
        "tokens": {k: [sum(m["tokens"][k] for m in minutes[:i + 1]) for i in range(60)] for k in ("input", "output")},
        "quality": {"mean": [m["quality"]["mean"] for m in minutes]},
    }
    cards = []
    for panel in config["panels"]:
        key, threshold = panel["id"], panel["threshold"]
        stats = []
        for name, value in summary[key].items():
            formatted = "N/A" if value is None else (f"{value:.6g}" if isinstance(value, (int, float)) else json.dumps(value))
            stats.append(f"{name}: {formatted}")
        cards.append(f'<section><h2>{escape(panel["title"])}</h2><p class="stats">{escape(" | ".join(stats))}</p>'
                     f'<p>{escape(panel["unit"])} · 60 min · Threshold {escape(threshold["aggregation"])} '
                     f'{escape(threshold["operator"])} {threshold["value"]}</p>'
                     + chart(series[key], threshold["value"]) + '</section>')
    return f'''<!doctype html><html lang="vi"><meta charset="utf-8">
<meta http-equiv="refresh" content="{config['refresh_seconds']}"><title>Day 13 Dashboard</title>
<style>body{{font:15px system-ui;margin:24px;background:#f1f5f9;color:#172033}}h1{{margin-bottom:8px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}section{{background:white;padding:16px;border-radius:10px}}
h2{{font-size:18px;margin:0}}p{{font-size:13px}}.stats{{font-weight:600}}svg{{width:100%;font-size:11px}}
.legend{{font-size:12px}}.legend span{{margin-right:12px}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}</style>
<h1>{escape(config['title'])}</h1><p>{start:%Y-%m-%d %H:%M:%S} — {end:%Y-%m-%d %H:%M:%S} UTC · Refresh 30s · Source: data/logs.jsonl</p>
<p>{len(records)} records · {skipped} malformed lines skipped. {'Chưa có dữ liệu trong 60 phút gần nhất; hãy chạy load test.' if not records else ''}
Cost/token là mô phỏng; quality là heuristic. N/A = không có mẫu. Đường nét đứt = threshold.</p>
<main class="grid">{''.join(cards)}</main></html>'''
