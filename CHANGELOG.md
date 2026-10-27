# Changelog

## 1.0.1 — 2026-10-28

- Fixed: `make zipapp` failed on a fresh checkout because the `dist/`
  directory did not exist yet; it is now created on demand.
- Fixed: `pi-guard report` crashed with a KeyError on history records that
  predate the `ts` field; such records are now tolerated.
- Added: dedicated unit tests for the history report module (+11, 44 total).

## 1.0.0 — 2026-10-28

Initial release.

- Sources: vcgencmd (live Pi), sysfs fallback (any Linux), deterministic
  simulation with healthy/heat-throttle-recover scenarios.
- Full get_throttled bitmask decoder (all 8 bits, current vs since-boot).
- Monitor: interval sampling, WARN/CRIT temperature zones, per-flag
  THROTTLE/UNDERVOLT/FREQ_CAP events, RECOVERED transitions, rotating JSONL
  history (5 MB × 2 generations), user hooks with event env vars.
- Report: min/avg/max temperature, % time throttled per flag, event log.
- CLI: run, once, report, decode, selftest (--simulate / --scenario / --seed).
- systemd service (Restart=always, video group); zero-dep zipapp build.
- 33 unit/end-to-end tests, all runnable on non-Pi machines.
