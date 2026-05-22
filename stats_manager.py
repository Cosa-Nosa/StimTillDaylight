"""
stats_manager.py — Per-match stat accumulator.

Driven by the event registry — no hardcoded stat names. Counters and timers
are stored in a single dict, keyed by whatever stat name the YAML specifies.

The consumer (dbd_tracker.py) calls update_from_tracker() once per frame.
end_match() returns a flat dict suitable for csv_logger.write().
"""

import time
import datetime
from typing import Optional


class StatsManager:
    """
    Counters: integer dict, optionally capped by EventSpec.counter_cap
    Timers:   float dict in seconds, accumulated while state[X] is True
    """

    def __init__(self, event_registry):
        self.event_registry = event_registry
        self._reset_state()

    def _reset_state(self):
        self.in_match = False
        self.match_start_ts: Optional[float] = None
        self.match_end_ts: Optional[float] = None
        self.outcome: Optional[str] = None
        self.survivor: str = "Unknown"

        self.counters: dict[str, int] = {}
        self.timers: dict[str, float] = {}
        # Open timer tracking: stat_name -> when it became active
        self._timer_started_at: dict[str, float] = {}

    # ── Match lifecycle ───────────────────────────────────────────────────

    def start_match(self, survivor_name: str = "Unknown"):
        self._reset_state()
        self.in_match = True
        self.match_start_ts = time.time()
        self.survivor = survivor_name
        print(f"[Stats] Match started for {survivor_name}")

    def end_match(self, outcome: Optional[str] = None) -> Optional[dict]:
        if not self.in_match:
            return None

        # Close any open timers
        now = time.time()
        for stat_name, started_at in list(self._timer_started_at.items()):
            elapsed = now - started_at
            self.timers[stat_name] = round(self.timers.get(stat_name, 0.0) + elapsed, 1)
            del self._timer_started_at[stat_name]

        self.match_end_ts = now
        if outcome:
            self.outcome = outcome

        record = self._build_record()
        self.in_match = False
        print(f"[Stats] Match ended: outcome={record.get('match_outcome')}, "
              f"duration={record.get('match_duration_s')}s")
        return record

    def _build_record(self) -> dict:
        record = {
            "match_start_iso":  datetime.datetime.fromtimestamp(self.match_start_ts).isoformat(timespec="seconds"),
            "match_duration_s": round((self.match_end_ts - self.match_start_ts), 1),
            "match_outcome":    self.outcome or "unknown",
            "survivor":         self.survivor,
        }
        record.update(self.counters)
        record.update({k: round(v, 1) for k, v in self.timers.items()})
        return record

    # ── Per-frame update ──────────────────────────────────────────────────

    def update_from_tracker(self, tracker):
        """
        Reads the DBDStateTracker's rising/falling/state flags and applies them
        to counters and timers per each EventSpec's metadata.
        """
        if not self.in_match:
            return

        for name, spec in self.event_registry.items():
            # Counter events fire on rising edge
            if spec.type == "counter" and tracker.rising[name]:
                if spec.stat_counter:
                    self._increment_counter(spec.stat_counter, spec.counter_cap)

            # Continuous events accumulate time + fire on_complete on falling edge
            if spec.type == "continuous":
                if spec.stat_timer:
                    self._update_timer(spec.stat_timer, tracker.state[name])
                if spec.on_complete and tracker.falling[name]:
                    oc = spec.on_complete
                    if oc.stat_counter:
                        self._increment_counter(oc.stat_counter)
            
            # digit_state events: fire on_decrement counter on decrement transitions
            if spec.type == "digit_state":
                if (tracker.digit_state_decremented[name]
                        and spec.on_decrement
                        and spec.on_decrement.stat_counter):
                    self._increment_counter(
                        spec.on_decrement.stat_counter,
                        spec.on_decrement.counter_cap,
                    )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _increment_counter(self, stat_name: str, cap: Optional[int] = None):
        current = self.counters.get(stat_name, 0) + 1
        if cap is not None:
            current = min(current, cap)
        self.counters[stat_name] = current
        print(f"[Stats] {stat_name} = {current}")

    def _update_timer(self, stat_name: str, is_active: bool):
        now = time.time()
        already_started = stat_name in self._timer_started_at

        if is_active and not already_started:
            self._timer_started_at[stat_name] = now
        elif not is_active and already_started:
            elapsed = now - self._timer_started_at[stat_name]
            self.timers[stat_name] = round(self.timers.get(stat_name, 0.0) + elapsed, 1)
            del self._timer_started_at[stat_name]

    def match_duration_s(self) -> float:
        if self.match_start_ts is None:
            return 0.0
        end = self.match_end_ts if self.match_end_ts else time.time()
        return round(end - self.match_start_ts, 1)
