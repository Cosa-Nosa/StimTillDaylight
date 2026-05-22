"""
dbdstate.py — Game state tracker.

Refactored to be registry-driven: instead of hardcoded coords + detection logic
per event, this module iterates the event registry every frame, runs detection
for each entry, applies debounce + confirmation, and exposes:

  - state[name]:        current True/False for every event
  - rising[name]:       True on the frame this event went False→True (one-shot)
  - falling[name]:      True on the frame this event went True→False (one-shot)
  - outcome:            "escaped" | "sacrificed" | "killed" | None (latched)
  - is_in_match:        derived from match_start / match_end events

Stats accumulation and vibe output are handled by the consumer (dbd_tracker.py),
which reads these flags and applies the rules in each EventSpec.
"""

import time
from typing import Optional

from dbdcv import ComputerVision


class DBDStateTracker:
    def __init__(self, event_registry):
        self.event_registry = event_registry
        self.dbdcv = ComputerVision(event_registry)

        # Per-event runtime state
        self.state: dict[str, bool] = {name: False for name in event_registry}
        self.rising: dict[str, bool] = {name: False for name in event_registry}
        self.falling: dict[str, bool] = {name: False for name in event_registry}
        self.confidence: dict[str, float] = {name: 0.0 for name in event_registry}
        self.match_loc: dict[str, tuple[int, int]] = {name: (0, 0) for name in event_registry}

        # Confirmation counters per event
        self._confirm: dict[str, int] = {name: 0 for name in event_registry}
        # Last-fire timestamp per event (for debounce on rising events)
        self._last_fired: dict[str, float] = {name: 0.0 for name in event_registry}

        # ── digit_state-specific runtime state ─────────────────────────
        digit_names = [n for n, s in event_registry.items() if s.type == "digit_state"]
        # Current confirmed state value (initialized from spec.initial)
        self.digit_state: dict[str, Optional[int]] = {
            n: event_registry[n].initial for n in digit_names
        }
        # State value just before the most recent transition
        self.digit_state_previous: dict[str, Optional[int]] = {n: None for n in digit_names}
        # One-frame flag: True on the frame a transition is accepted
        self.digit_state_changed: dict[str, bool] = {n: False for n in digit_names}
        # One-frame flag: True only on N → less-than-N transitions
        self.digit_state_decremented: dict[str, bool] = {n: False for n in digit_names}
        # Candidate tracking for the confirmation window
        self._digit_state_candidate: dict[str, Optional[int]] = {n: None for n in digit_names}
        self._digit_state_candidate_count: dict[str, int] = {n: 0 for n in digit_names}

        # Latched outcome
        self.outcome: Optional[str] = None
        self.is_in_match: bool = False
        self.current_time: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start_capturing(self, fps):
        self.dbdcv.start_capturing(target_fps=fps)

    def stop_capturing(self):
        self.dbdcv.stop_capturing()

    def reset_for_new_match(self):
        for name in self.event_registry:
            self.state[name] = False
            self.rising[name] = False
            self.falling[name] = False
            self._confirm[name] = 0
        self.outcome = None
        self.is_in_match = True

    def reset_for_match_end(self):
        # Don't clear outcome — that's read by the consumer right after this
        for name in self.event_registry:
            self.state[name] = False
            self._confirm[name] = 0
        self.is_in_match = False

    # ── Per-frame update ───────────────────────────────────────────────────
def refresh(self):
        self.dbdcv.capture_frame()
        if self.dbdcv.frame is None:
            return
        self.current_time = time.time()

        # Clear rising/falling — they're one-frame flags
        for name in self.event_registry:
            self.rising[name] = False
            self.falling[name] = False
        # Clear digit_state transition flags — also one-frame
        for name in self.digit_state_changed:
            self.digit_state_changed[name] = False
            self.digit_state_decremented[name] = False

        # Run detection for every registered event
        for name, spec in self.event_registry.items():
            if spec.type == "digit_state":
                self._refresh_digit_state(name, spec)
                continue
            conf, loc = self.dbdcv.match(name, region=spec.region)
            self.confidence[name] = conf
            self.match_loc[name] = loc

            detected_now = conf >= spec.threshold

            # Update confirmation counter
            if detected_now:
                self._confirm[name] = min(self._confirm[name] + 1, spec.confirm_frames * 2)
            else:
                self._confirm[name] = 0

            # Confirmed state
            confirmed_active = self._confirm[name] >= spec.confirm_frames

            prev_state = self.state[name]
            if confirmed_active and not prev_state:
                # Check debounce for rising events
                if self.current_time - self._last_fired[name] >= spec.debounce_s:
                    self.state[name] = True
                    self.rising[name] = True
                    self._last_fired[name] = self.current_time
            elif not confirmed_active and prev_state:
                # Falling edge — no debounce check
                self.state[name] = False
                self.falling[name] = True

            # Latch outcome
            if spec.type == "outcome" and self.rising[name] and self.outcome is None:
                self.outcome = spec.outcome

            # Lifecycle markers
            if spec.type == "lifecycle" and self.rising[name]:
                if spec.action == "start_match":
                    self.is_in_match = True
                elif spec.action == "end_match":
                    self.is_in_match = False


def _refresh_digit_state(self, name: str, spec):
        """
        Match every sub-template in spec.states, pick the highest-confidence winner,
        gate transitions with confirm_frames + debounce_s.

        Sets:
          self.confidence[name]               = best confidence across sub-templates
          self.digit_state[name]              = confirmed state (unchanged if no transition)
          self.digit_state_previous[name]     = prior value at the moment of transition
          self.digit_state_changed[name]      = True on the frame a transition is accepted
          self.digit_state_decremented[name]  = True on the frame state went N → less-than-N
        """
        best_state: Optional[int] = None
        best_conf: float = 0.0
        for state_value, sub_template in spec.states.items():
            conf, _loc = self.dbdcv.match(sub_template, region=spec.region)
            if conf >= spec.threshold and conf > best_conf:
                best_state = state_value
                best_conf = conf

        self.confidence[name] = best_conf

        # Confirmation: candidate must persist for `confirm_frames` consecutive frames
        if best_state == self._digit_state_candidate[name]:
            self._digit_state_candidate_count[name] += 1
        else:
            self._digit_state_candidate[name] = best_state
            self._digit_state_candidate_count[name] = 1

        # Accept a transition only when:
        #   - we have a confirmed non-None candidate
        #   - it differs from the current state
        #   - debounce window has elapsed since the last transition
        if (best_state is not None
                and self._digit_state_candidate_count[name] >= spec.confirm_frames
                and best_state != self.digit_state[name]
                and self.current_time - self._last_fired[name] >= spec.debounce_s):
            prev = self.digit_state[name]
            self.digit_state_previous[name] = prev
            self.digit_state[name] = best_state
            self.digit_state_changed[name] = True
            if prev is not None and best_state < prev:
                self.digit_state_decremented[name] = True
            self._last_fired[name] = self.current_time

def reset_for_new_match(self):
        for name in self.event_registry:
            self.state[name] = False
            self.rising[name] = False
            self.falling[name] = False
            self._confirm[name] = 0
        # Reset digit_state runtime to per-event initial values
        for name, spec in self.event_registry.items():
            if spec.type == "digit_state":
                self.digit_state[name] = spec.initial
                self.digit_state_previous[name] = None
                self._digit_state_candidate[name] = None
                self._digit_state_candidate_count[name] = 0
        self.outcome = None
        self.is_in_match = True