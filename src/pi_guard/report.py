"""History report from the JSONL sample log."""

from __future__ import annotations

import json
import time

from .collect import decode_throttled


def _normalize_record(r) -> dict:
    """Type-whitelist one history record for the report core.

    read_all() only skips lines that are not valid JSON at all; a *valid*
    line with wrong-typed values — a bare scalar, or a hand edit / jq rewrite
    like ``"throttled": "0x4"``, ``"flags": null`` — would raise inside
    summarize/last_state and take the whole report down. Non-conforming
    values degrade to their empty equivalents; well-formed records pass
    through unchanged.
    """
    if not isinstance(r, dict):
        return {}
    fixed = dict(r)
    ts = fixed.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        fixed["ts"] = None
    throttled = fixed.get("throttled", 0)
    if isinstance(throttled, bool) or not isinstance(throttled, int):
        fixed["throttled"] = 0
    flags = fixed.get("flags", [])
    if not isinstance(flags, list):
        flags = []
    fixed["flags"] = [f for f in flags if isinstance(f, str)]
    events = fixed.get("events", [])
    if not isinstance(events, list):
        fixed["events"] = []
    return fixed


def summarize(records: list) -> dict:
    """Aggregate JSONL records into report statistics."""
    records = [_normalize_record(r) for r in records]
    temps = [r["temp_c"] for r in records if isinstance(r.get("temp_c"), (int, float))]
    throttled_samples = sum(1 for r in records if r.get("throttled", 0) & 0xF)
    per_flag: dict = {}
    for r in records:
        for flag in r.get("flags", []):
            per_flag[flag] = per_flag.get(flag, 0) + 1
    events = []
    for r in records:
        for ev in r.get("events", []):
            events.append({"ts": r.get("ts"), "event": ev})
    n = len(records)
    return {
        "samples": n,
        "first": records[0].get("ts") if records else None,
        "last": records[-1].get("ts") if records else None,
        "temp_min": min(temps) if temps else None,
        "temp_avg": round(sum(temps) / len(temps), 2) if temps else None,
        "temp_max": max(temps) if temps else None,
        "throttled_pct": round(100.0 * throttled_samples / n, 2) if n else 0.0,
        "flag_pct": {k: round(100.0 * v / n, 2) for k, v in per_flag.items()},
        "events": events[-20:],
        "total_events": len(events),
    }


def last_state(records: list) -> dict | None:
    """Latest sample + decoded throttle bits."""
    if not records:
        return None
    last = _normalize_record(records[-1])
    return {
        "sample": last,
        "throttle_bits": [d for d in decode_throttled(last.get("throttled", 0)) if d["occurred"]],
    }


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"


def render_human(summary: dict, state: dict | None) -> str:
    lines = []
    if state:
        s = state["sample"]
        lines.append(f"Last sample   : {_fmt_ts(s.get('ts'))}  "
                     f"{s.get('temp_c', '?')}C  arm={s.get('arm_mhz', '?')}MHz  "
                     f"flags={','.join(s.get('flags', [])) or 'none'}")
    if summary["samples"] == 0:
        lines.append("No history yet — pi-guard has not recorded any samples.")
        return "\n".join(lines)
    lines.append(f"Samples       : {summary['samples']}  "
                 f"({_fmt_ts(summary['first'])} → {_fmt_ts(summary['last'])})")
    lines.append(f"Temperature   : min {summary['temp_min']}C  "
                 f"avg {summary['temp_avg']}C  max {summary['temp_max']}C")
    lines.append(f"Throttled     : {summary['throttled_pct']}% of samples")
    if summary["flag_pct"]:
        for flag, pct in sorted(summary["flag_pct"].items(), key=lambda kv: -kv[1]):
            lines.append(f"  {flag:22s} {pct}%")
    lines.append(f"Events        : {summary['total_events']} total"
                 + (", latest:" if summary["events"] else ""))
    for ev in reversed(summary["events"][-8:]):
        lines.append(f"  {_fmt_ts(ev['ts'])}  {ev['event']}")
    return "\n".join(lines)


def build_report(store) -> tuple:
    records = store.read_all()
    return summarize(records), last_state(records)


def render_json(summary: dict, state: dict | None) -> str:
    return json.dumps({"summary": summary, "last_state": state},
                      indent=2, ensure_ascii=False, default=str)
