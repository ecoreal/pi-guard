# pi-guard

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Deps](https://img.shields.io/badge/dependencies-zero-brightgreen)

**A thermal / throttle / under-voltage watchdog for the Raspberry Pi.**

The Pi degrades gracefully: when power dips or silicon heats up it silently
caps frequencies and throttles — your "slow" problem is usually a power or
cooling problem you never saw. `pi-guard` makes those events loud, logged and
actionable.

```console
$ pi-guard once
temp      : 54.2 C
arm clock : 1500 MHz
core volt : 0.8750 V
throttled : 0x0 (none)
source    : vcgencmd
model     : Raspberry Pi 4 Model B Rev 1.5

$ pi-guard run            # + journald events like:
pi-guard: THROTTLE temp=81.3C flags=throttled
pi-guard: TEMP_CRIT temp=86.2C flags=throttled,under-voltage
pi-guard: RECOVERED:under-voltage temp=79.1C flags=throttled

$ pi-guard report
Last sample   : 2026-10-28 09:14:03  79.4C  arm=1500MHz  flags=none
Samples       : 1720  (2026-10-27 09:14:03 → 2026-10-28 09:14:03)
Temperature   : min 42.1C  avg 55.6C  max 87.4C
Throttled     : 3.2% of samples
  throttled              3.2%
  under-voltage          0.9%
Events        : 12 total, latest:
  2026-10-28 04:02:11  THROTTLE
  2026-10-28 04:09:44  RECOVERED:throttled
```

## What it watches

- 🌡️ **Temperature** (vcgencmd `measure_temp`) — WARN/CRIT/normal zone events
- ⚡ **Under-voltage** (`get_throttled` bit 0) — the #1 cause of mysterious Pi slowness
- 🧊 **Throttling & frequency capping** (bits 1–2) — current *and* since-boot flags
- 🌡️ **Soft temperature limit** (bit 3, the 60°C "soft cap" on some models)
- 🕐 **ARM clock** and core voltage sampled alongside, GPU memory for the record

The `get_throttled` bitmask is fully decoded — run `pi-guard decode 0x20005`
for a per-bit explanation of any value.

## Install

On the Pi (recommended):

```bash
git clone <repo> && cd pi-guard
make test              # 43 tests — run anywhere, simulation included
sudo ./install.sh      # zipapp → /usr/local/bin + systemd service (10s interval)
```

No install at all:

```bash
make zipapp && scp dist/pi-guard pi@raspberrypi:
ssh pi@raspberrypi './pi-guard run --interval 10 &'
```

(`vcgencmd` comes from `libraspberrypi-bin`, preinstalled on Raspberry Pi OS.
The service adds the `video` group automatically.)

## Commands

| Command | Purpose |
| --- | --- |
| `run` | Continuous monitor — JSONL history + journald events + optional `--hook` |
| `once [--json]` | One sample, human or JSON |
| `report [--json]` | History summary: min/avg/max temps, % time throttled, event log |
| `decode <0x…>` | Explain any `get_throttled` bitmask, bit by bit |
| `selftest` | Verify every parser against known vcgencmd output — run it on-device first |

Useful flags for `run`: `--interval SEC`, `--warn-temp C`, `--crit-temp C`,
`--db PATH`, `--hook "ntfy-send pi-alert"`.

### Hooks

Every event runs your command with context in the environment:

```bash
pi-guard run --hook 'curl -s -d "$PIGUARD_EVENT @ $PIGUARD_TEMP C ($PIGUARD_FLAGS)" ntfy.sh/my-pi'
```

### History

Samples live in `/var/lib/pi-guard/samples.jsonl` (root) or
`~/.local/state/pi-guard/samples.jsonl` (user) — plain JSON lines, rotated at
5 MB with one kept generation. One line per sample, so `grep`, `jq` and
`sqlite3` all work directly on it.

## Works off-Pi too

No vcgencmd? `pi-guard` falls back to sysfs (temperature + CPU clock, no
throttle bits) so it still watches thermals on SBCs and laptops. And for
demos or CI on any x86 box:

```bash
make demo   # simulated Pi heats up, throttles, recovers — with live events
```

## Design notes

- **Zero dependencies** — Python 3.9+ stdlib only; installs as a single
  zipapp file.
- **Parsers are pure functions** over vcgencmd's exact output strings, with a
  built-in `selftest` that re-verifies them on your actual device.
- **The `--simulate` source** is deterministic (seeded), so tests are stable
  and the full pipeline (events → JSONL → report → hooks) is exercised
  end-to-end on non-Pi machines — 43 unit tests run anywhere.

## License

[MIT](LICENSE)
