"""Monitoring loop: sample → detect events → log JSONL (rotating) → hook."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from .collect import active_flags

DEFAULT_DB = None  # resolved lazily: /var/lib/pi-guard (root) or XDG state dir

EVENT_NAMES = {
    0: "UNDERVOLT",
    1: "FREQ_CAP",
    2: "THROTTLE",
    3: "SOFTTEMP_LIMIT",
}


def default_db() -> str:
    if os.geteuid() == 0:
        return "/var/lib/pi-guard/samples.jsonl"
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "pi-guard", "samples.jsonl")


class RotatingJsonl:
    """Append-only JSONL with size-based rotation (keeps one .1 generation)."""

    def __init__(self, path: str, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.path = path
        self.max_bytes = max_bytes
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, record: dict) -> None:
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                rotated = self.path + ".1"
                if os.path.exists(rotated):
                    os.unlink(rotated)
                os.replace(self.path, rotated)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as err:
            print(f"pi-guard: cannot write {self.path}: {err}", file=sys.stderr)

    def read_all(self) -> list:
        records = []
        for path in (self.path + ".1", self.path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            except OSError:
                continue
        return records


class Monitor:
    """Samples a source every `interval` seconds; detects threshold events.

    Events are printed to stderr (picked up by journald), recorded in the
    JSONL stream, and optionally dispatched to a user hook command.
    """

    def __init__(self, source, db: str, warn_temp: float = 70.0,
                 crit_temp: float = 80.0, hook: str | None = None,
                 max_bytes: int = 5 * 1024 * 1024) -> None:
        self.source = source
        self.store = RotatingJsonl(db, max_bytes=max_bytes)
        self.warn_temp = warn_temp
        self.crit_temp = crit_temp
        self.hook = hook
        self._prev_flags: set = set()
        self._prev_zone: str = "normal"   # normal | warn | crit
        self._stop = False

    def stop(self, *_args) -> None:
        self._stop = True

    def tick(self) -> dict:
        sample = self.source.sample()
        record = dict(sample)
        events = self._events_for(sample)
        if events:
            record["events"] = events
        self.store.append(record)
        for ev in events:
            line = f"pi-guard: {ev} temp={sample['temp_c']}C flags={','.join(sample['flags']) or 'none'}"
            print(line, file=sys.stderr, flush=True)
            if self.hook:
                self._run_hook(ev, sample)
        return record

    def _events_for(self, sample: dict) -> list:
        events = []
        flags = set(sample.get("flags", []))
        for flag in sorted(flags - self._prev_flags):
            events.append(EVENT_NAMES.get(_flag_bit(flag), flag.upper()))
        for flag in sorted(self._prev_flags - flags):
            events.append(f"RECOVERED:{flag}")
        self._prev_flags = flags

        temp = sample.get("temp_c")
        if temp is not None:
            zone = "crit" if temp >= self.crit_temp else ("warn" if temp >= self.warn_temp else "normal")
            if zone != self._prev_zone:
                events.append({"normal": "TEMP_NORMAL", "warn": "TEMP_WARN",
                               "crit": "TEMP_CRIT"}[zone])
                self._prev_zone = zone
        return events

    def _run_hook(self, event: str, sample: dict) -> None:
        """Run the user hook through the shell so it behaves as written.

        The hook is the user's own command line (see the README examples):
        running it via `sh -c` keeps quoting, redirects and $PIGUARD_*
        expansion working exactly as documented. A hook the shell itself
        rejects only prints a warning; it must not take the loop down.
        """
        try:
            if not self.hook or not self.hook.strip():
                return
            env = dict(os.environ)
            env["PIGUARD_EVENT"] = event
            env["PIGUARD_TEMP"] = str(sample.get("temp_c"))
            env["PIGUARD_FLAGS"] = ",".join(sample.get("flags", []))
            subprocess.run(self.hook, shell=True, env=env, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as err:
            print(f"pi-guard: hook failed: {err}", file=sys.stderr)

    def run(self, interval: float = 5.0, max_ticks: int | None = None) -> list:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        samples = []
        while not self._stop:
            samples.append(self.tick())
            if max_ticks is not None and len(samples) >= max_ticks:
                break
            # interruptible sleep
            end = time.monotonic() + interval
            while not self._stop and time.monotonic() < end:
                time.sleep(min(0.2, max(0.0, end - time.monotonic())))
        return samples


def _flag_bit(name: str) -> int:
    from .collect import THROTTLE_BITS

    for bit, n in THROTTLE_BITS:
        if n == name:
            return bit
    return 99
