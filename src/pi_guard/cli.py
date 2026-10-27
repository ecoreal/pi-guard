"""CLI: pi-guard run | once | report | decode | selftest."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, collect, monitor, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pi-guard",
        description="Raspberry Pi thermal / throttle / under-voltage watchdog "
                    "with JSONL history and threshold hooks.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", required=True)

    s = sub.add_parser("run", help="monitor continuously (daemon; pair with systemd)")
    _add_sim_args(s)
    s.add_argument("--interval", type=float, default=5.0, metavar="SEC")
    s.add_argument("--db", default=None, help="JSONL history path")
    s.add_argument("--warn-temp", type=float, default=70.0, metavar="C")
    s.add_argument("--crit-temp", type=float, default=80.0, metavar="C")
    s.add_argument("--hook", default=None, metavar="CMD",
                   help="command run on every event (env: PIGUARD_EVENT, PIGUARD_TEMP, PIGUARD_FLAGS)")
    s.add_argument("--max-ticks", type=int, default=None, help=argparse.SUPPRESS)

    s = sub.add_parser("once", help="print one sample and exit")
    _add_sim_args(s)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("report", help="summarize recorded history")
    s.add_argument("--db", default=None)
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("decode", help="explain a get_throttled bitmask")
    s.add_argument("mask", help="e.g. 0x20000")

    s = sub.add_parser("selftest", help="verify parsers work on this machine")
    return p


def _add_sim_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--simulate", action="store_true",
                        help="use simulated data (demos/tests on non-Pi machines)")
    parser.add_argument("--scenario", choices=["healthy", "throttle"],
                        default="healthy", help="simulation scenario")
    parser.add_argument("--seed", type=int, default=42)


def _db_or_default(path: str | None) -> str:
    return path or monitor.default_db()


def cmd_run(args) -> int:
    source = collect.autodetect_source(simulate=args.simulate,
                                       scenario=args.scenario, seed=args.seed)
    mon = monitor.Monitor(source, db=_db_or_default(args.db),
                          warn_temp=args.warn_temp, crit_temp=args.crit_temp,
                          hook=args.hook)
    src = type(source).__name__
    print(f"pi-guard: monitoring with {src}, interval {args.interval}s, "
          f"warn {args.warn_temp}C, crit {args.crit_temp}C", file=sys.stderr, flush=True)
    mon.run(interval=args.interval, max_ticks=args.max_ticks)
    return 0


def cmd_once(args) -> int:
    source = collect.autodetect_source(simulate=args.simulate,
                                       scenario=args.scenario, seed=args.seed)
    sample = source.sample()
    if args.json:
        print(json.dumps(sample, indent=2))
        return 0
    flags = ",".join(sample["flags"]) or "none"
    print(f"temp      : {sample['temp_c']} C")
    print(f"arm clock : {sample['arm_mhz']} MHz")
    print(f"core volt : {sample['volt_core']} V")
    print(f"throttled : 0x{sample['throttled']:x} ({flags})")
    print(f"source    : {sample['source']}")
    if sample["model"]:
        print(f"model     : {sample['model']}")
    return 0


def cmd_report(args) -> int:
    store = monitor.RotatingJsonl(_db_or_default(args.db))
    summary, state = report.build_report(store)
    if args.json:
        print(report.render_json(summary, state))
    else:
        print(report.render_human(summary, state))
    return 0


def cmd_decode(args) -> int:
    raw = args.mask
    try:
        mask = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 16)
    except ValueError:
        print(f"pi-guard: not a hex bitmask: {raw}", file=sys.stderr)
        return 2
    print(f"throttled = {raw} ({mask:#010b}b)")
    for d in collect.decode_throttled(mask):
        mark = []
        if d["now"]:
            mark.append("ACTIVE NOW")
        if d["occurred"] and d["bit"] >= 16:
            mark.append("occurred since boot")
        status = " + ".join(mark) if mark else "-"
        print(f"  bit {d['bit']:2d}  {d['name']:26s} {status}")
    return 0


def cmd_selftest(_args) -> int:
    print("pi-guard selftest:")
    ok = collect.selftest()
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"run": cmd_run, "once": cmd_once, "report": cmd_report,
                "decode": cmd_decode, "selftest": cmd_selftest}
    try:
        return handlers[args.cmd](args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
