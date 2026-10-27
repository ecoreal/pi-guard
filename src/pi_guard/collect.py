"""Health data sources and parsers.

Three interchangeable sources implement ``sample() -> dict``:

* :class:`VcGenCmdSource` — real Raspberry Pi data via ``vcgencmd``
* :class:`SysFsSource`    — degraded data from sysfs (any Linux)
* :class:`SimulatedSource`— deterministic fake data for tests/demos

Every parser is a pure function over vcgencmd's exact output strings.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import time

# ---------------------------------------------------------------- parsers ---


def parse_temp(out: str) -> float | None:
    """vcgencmd measure_temp → "temp='45.6'C" → 45.6"""
    for token in out.replace("'", " ").replace('"', " ").split():
        try:
            return float(token.lower().rstrip("c"))
        except ValueError:
            continue
    return None


def parse_volts(out: str) -> float | None:
    """vcgencmd measure_volts core → "volt=0.8750V" → 0.875"""
    if "=" not in out:
        return None
    try:
        return float(out.split("=", 1)[1].strip().rstrip("vV"))
    except ValueError:
        return None


def parse_throttled(out: str) -> int:
    """vcgencmd get_throttled → "throttled=0x0" → 0"""
    if "=" not in out:
        return 0
    token = out.split("=", 1)[1].strip()
    try:
        return int(token, 16)
    except ValueError:
        return 0


def parse_clock(out: str) -> int | None:
    """vcgencmd measure_clock arm → "frequency(48)=1000000000" → 1000 (MHz)"""
    if "=" not in out:
        return None
    try:
        return int(out.rsplit("=", 1)[1].strip()) // 1_000_000
    except ValueError:
        return None


def parse_mem(out: str) -> int | None:
    """vcgencmd get_mem gpu → "gpu=76M" → 76"""
    if "=" not in out:
        return None
    try:
        return int(out.rsplit("=", 1)[1].strip().rstrip("mM"))
    except ValueError:
        return None


# ------------------------------------------------------- throttle bitmask ---

THROTTLE_BITS = [
    (0, "under-voltage"),
    (1, "freq-capped"),
    (2, "throttled"),
    (3, "softtemp-limit"),
    (16, "under-voltage-occurred"),
    (17, "freq-capped-occurred"),
    (18, "throttled-occurred"),
    (19, "softtemp-limit-occurred"),
]

# bits 0–3 describe the *current* state
NOW_ACTIVE_BITS = {0, 1, 2, 3}


def decode_throttled(mask: int) -> list:
    """Bitmask → [{bit, name, now, occurred}] for every known flag."""
    out = []
    for bit, name in THROTTLE_BITS:
        active = bool(mask & (1 << bit))
        out.append({
            "bit": bit,
            "name": name,
            "now": active and bit in NOW_ACTIVE_BITS,
            "occurred": active,
        })
    return out


def active_flags(mask: int) -> list:
    return [d["name"] for d in decode_throttled(mask) if d["now"]]


# ---------------------------------------------------------------- sources ---


class VcGenCmdSource:
    """Live data on a Raspberry Pi. Requires vcgencmd (libraspberrypi-bin)."""

    def __init__(self, binary: str = "vcgencmd") -> None:
        self.binary = shutil.which(binary) or binary

    def _run(self, *args: str) -> str:
        try:
            res = subprocess.run([self.binary, *args], capture_output=True,
                                 text=True, timeout=10)
            return res.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def sample(self) -> dict:
        mask = parse_throttled(self._run("get_throttled"))
        return {
            "ts": time.time(),
            "temp_c": parse_temp(self._run("measure_temp")),
            "volt_core": parse_volts(self._run("measure_volts", "core")),
            "arm_mhz": parse_clock(self._run("measure_clock", "arm")),
            "gpu_mem_mb": parse_mem(self._run("get_mem", "gpu")),
            "throttled": mask,
            "flags": active_flags(mask),
            "source": "vcgencmd",
            "model": read_model(),
        }


class SysFsSource:
    """Degraded data on any Linux box (temperature + ARM clock only)."""

    def __init__(self, root: str = "/") -> None:
        self.root = root

    def _read(self, rel: str) -> str:
        try:
            with open(os.path.join(self.root, rel)) as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def sample(self) -> dict:
        raw_temp = self._read("sys/class/thermal/thermal_zone0/temp")
        raw_freq = self._read("sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        return {
            "ts": time.time(),
            "temp_c": int(raw_temp) / 1000 if raw_temp.isdigit() else None,
            "volt_core": None,
            "arm_mhz": int(raw_freq) // 1000 if raw_freq.isdigit() else None,
            "gpu_mem_mb": None,
            "throttled": 0,
            "flags": [],
            "source": "sysfs",
            "model": read_model(self.root),
        }


class SimulatedSource:
    """Deterministic fake data: a temperature random walk with a throttle
    scenario.  Used by tests and ``--simulate``."""

    def __init__(self, seed: int = 42, scenario: str = "healthy") -> None:
        self.rng = random.Random(seed)
        self.scenario = scenario
        self.temp = 52.0
        self._cooling = False
        self._tick = 0

    def sample(self) -> dict:
        self._tick += 1
        if self.scenario == "healthy":
            self.temp += self.rng.uniform(-0.8, 0.9)
            self.temp = max(40.0, min(72.0, self.temp))
            mask = 0
        else:  # "throttle": heat/cool cycles that cross every threshold
            if self._cooling:
                self.temp -= self.rng.uniform(1.5, 3.0)
                if self.temp < 76.0:
                    self._cooling = False
            else:
                self.temp += self.rng.uniform(0.4, 2.2)
                if self.temp >= 91.0:
                    self._cooling = True
            self.temp = max(40.0, min(92.0, self.temp))
            mask = 0
            if self.temp > 80:
                mask |= 1 << 2            # throttled now
            if self.temp > 85:
                mask |= 1 << 0            # under-voltage now
            if self.temp > 88:
                mask |= 1 << 19           # softtemp-limit occurred
        return {
            "ts": time.time(),
            "temp_c": round(self.temp, 1),
            "volt_core": round(self.rng.uniform(0.86, 0.89), 4),
            "arm_mhz": 1500 if not (mask & (1 << 2)) else 1000,
            "gpu_mem_mb": 76,
            "throttled": mask,
            "flags": active_flags(mask),
            "source": "simulated",
            "model": "Raspberry Pi 4 Model B Rev 1.5 (simulated)",
        }


def read_model(root: str = "/") -> str:
    try:
        with open(os.path.join(root, "proc/device-tree/model"), "rb") as fh:
            return fh.read().replace(b"\x00", b"").decode("utf-8", "replace").strip()
    except OSError:
        try:
            with open(os.path.join(root, "etc/pi-guard.model")) as fh:
                return fh.read().strip()
        except OSError:
            return ""


def autodetect_source(simulate: bool = False, scenario: str = "healthy", seed: int = 42):
    if simulate:
        return SimulatedSource(seed=seed, scenario=scenario)
    if shutil.which("vcgencmd"):
        return VcGenCmdSource()
    return SysFsSource()


# ---------------------------------------------------------------- selftest --

SELFTEST_CASES = [
    ("parse_temp", "temp='45.6'C", 45.6),
    ("parse_temp", "temp='85.0'C", 85.0),
    ("parse_volts", "volt=0.8750V", 0.875),
    ("parse_throttled", "throttled=0x0", 0),
    ("parse_throttled", "throttled=0x20000", 0x20000),
    ("parse_clock", "frequency(48)=1500299000", 1500),
    ("parse_mem", "gpu=76M", 76),
]


def selftest() -> bool:
    ok = True
    for name, raw, expected in SELFTEST_CASES:
        got = globals()[name](raw)
        status = "ok" if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  {name:16s} {raw!r:38s} → {got!r}  [{status}]")
    flags = decode_throttled(0x20005)
    got_now = sorted(f["name"] for f in flags if f["now"])
    want_now = ["throttled", "under-voltage"]
    ok = ok and got_now == want_now
    print(f"  decode_throttled 0x20005 now-active {got_now}  "
          f"[{'ok' if got_now == want_now else 'FAIL'}]")
    return ok
