"""
event_registry.py — Declarative event configuration.

Loads events.yaml and merges it with discovered template files in data/templates/.
The result is a list of EventSpec objects that drives detection, stat tracking,
and vibe output.

This module is the single source of truth for "what does each template do?"
Add a template by:
  1. Drop t_<name>.png into data/templates/
  2. (optional) Add a <name>: { ... } block to events.yaml
If no events.yaml entry exists, the template is loaded but does nothing —
which is fine for templates you only care about for debug visualization.
"""

import os
import glob
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError:
    raise RuntimeError(
        "PyYAML is required. Run: pip install pyyaml"
    )


# ── Event types ────────────────────────────────────────────────────────────────
#
# Each template's `type` field in events.yaml controls how the framework reacts
# when the template matches. Types:
#
#   lifecycle
#       Match-start / match-end markers. Use `action` to specify "start_match"
#       or "end_match". Drives stat tracking boundaries.
#
#   outcome
#       Match-result banner (escaped / sacrificed / killed). Latches
#       match_outcome and triggers an end_match if `triggers_end_match: true`.
#
#   counter
#       Rising-edge event that increments a stat counter and optionally fires
#       a timed vibe. Useful for notifications ("gen complete" toasts) and
#       discrete events (got unhooked).
#
#   continuous
#       Active-while-matching state. Optionally accumulates a duration timer
#       and/or maintains a continuous vibe trigger. Can optionally fire a
#       counter + timed vibe on the falling edge via `on_complete:`.
#
#   passive
#       Detected and exposed in tracker state but does not change stats or
#       vibes on its own. Useful for things you want available for debug
#       visualization or future hookup.
# ───────────────────────────────────────────────────────────────────────────────


@dataclass
class VibeSpec:
    trigger: str
    intensity: float = 0.0
    duration: float = 0.0  # 0 for continuous (toggle) vibes


@dataclass
class OnCompleteSpec:
    """For continuous events: what fires when the state ends."""
    stat_counter: Optional[str] = None
    vibe: Optional[VibeSpec] = None

@dataclass
class OnDecrementSpec:
    """For digit_state events: what fires when the state value decreases (N → less than N)."""
    stat_counter: Optional[str] = None
    counter_cap: Optional[int] = None
    vibe: Optional[VibeSpec] = None

@dataclass
class EventSpec:
    name: str
    type: str                            # lifecycle | outcome | counter | continuous | passive
    region: Optional[str] = None
    threshold: float = 0.85
    confirm_frames: int = 2
    debounce_s: float = 3.0

    # Type-specific fields
    action: Optional[str] = None         # for lifecycle: "start_match" | "end_match"
    outcome: Optional[str] = None        # for outcome: "escaped" | "sacrificed" | "killed"
    triggers_end_match: bool = False     # for outcome
    stat_counter: Optional[str] = None   # for counter
    counter_cap: Optional[int] = None    # for counter
    stat_timer: Optional[str] = None     # for continuous
    vibe: Optional[VibeSpec] = None
    on_complete: Optional[OnCompleteSpec] = None

    # digit_state-specific fields
    states: Optional[dict] = None              # raw YAML map: state_value (int) → template registry name (str)
    initial: Optional[int] = None              # starting state value when match begins; None = no state until first match
    on_decrement: Optional[OnDecrementSpec] = None

    # Filled at load time
    template_path: Optional[str] = None


# ── Loader ──────────────────────────────────────────────────────────────────────

def load_event_registry(events_yaml_path: str, templates_dir: str) -> dict[str, EventSpec]:
    """
    Loads events.yaml and merges with files found in templates_dir/.
    Returns a dict mapping event_name -> EventSpec.

    Templates with no YAML entry are still loaded but marked as 'passive'.
    YAML entries with no matching template file are logged and skipped.
    """
    # 1. Load YAML
    yaml_config = {}
    if os.path.exists(events_yaml_path):
        with open(events_yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        yaml_config = data.get("templates", {})
    else:
        print(f"[registry] No events.yaml at {events_yaml_path} — all templates load as passive")

    # 2. Discover template files (filename without t_ prefix and .png suffix = event name)
    discovered = {}
    pattern = os.path.join(templates_dir, "t_*.png")
    for path in sorted(glob.glob(pattern)):
        filename = os.path.basename(path)
        name = filename[2:-4]  # strip "t_" prefix and ".png" suffix
        discovered[name] = path

    # Also accept templates without t_ prefix for simplicity
    pattern_noprefix = os.path.join(templates_dir, "*.png")
    for path in sorted(glob.glob(pattern_noprefix)):
        filename = os.path.basename(path)
        if filename.startswith("t_") or filename.startswith("m_"):
            continue
        name = filename[:-4]
        if name not in discovered:
            discovered[name] = path

    # 3. Build EventSpec dict
    registry = {}

    for name, path in discovered.items():
        yaml_entry = yaml_config.get(name, {})
        spec = _build_spec(name, yaml_entry)
        spec.template_path = path
        registry[name] = spec

    # 3b. Register digit_state events from YAML (they have no template file of their own)
    for yaml_name, yaml_entry in yaml_config.items():
        if not isinstance(yaml_entry, dict):
            continue
        if yaml_entry.get("type") != "digit_state":
            continue
        if yaml_name in registry:
            print(f"[registry] WARNING: digit_state '{yaml_name}' collides with a discovered "
                  f"template file of the same name. Rename one to avoid conflict. Skipping.")
            continue

        spec = _build_spec(yaml_name, yaml_entry)

        if not spec.states:
            print(f"[registry] WARNING: digit_state '{yaml_name}' has no 'states:' map. Skipping.")
            continue
        missing = [t for t in spec.states.values() if t not in discovered]
        if missing:
            print(f"[registry] WARNING: digit_state '{yaml_name}' references missing sub-templates "
                  f"{missing} — add them to {templates_dir}/ as t_<name>.png. Skipping.")
            continue
        if spec.initial is not None and spec.initial not in spec.states:
            print(f"[registry] WARNING: digit_state '{yaml_name}' has initial={spec.initial} "
                  f"which is not in states {sorted(spec.states.keys())}. Clearing initial.")
            spec.initial = None

        registry[yaml_name] = spec

    # 4. Warn about YAML entries with no matching template
    # (digit_state events are expected to have no single template file — skip them)
    for name, entry in yaml_config.items():
        if isinstance(entry, dict) and entry.get("type") == "digit_state":
            continue
        if name not in discovered:
            print(f"[registry] WARNING: events.yaml has '{name}' but no template file at "
                  f"{os.path.join(templates_dir, 't_' + name + '.png')}")

    print(f"[registry] Loaded {len(registry)} event(s) "
          f"({sum(1 for s in registry.values() if s.type != 'passive')} active, "
          f"{sum(1 for s in registry.values() if s.type == 'passive')} passive)")

    return registry


def _build_spec(name: str, yaml_entry: dict) -> EventSpec:
    """Constructs an EventSpec from a YAML dict, applying type-specific defaults."""
    type_ = yaml_entry.get("type", "passive")

    spec = EventSpec(
        name=name,
        type=type_,
        region=yaml_entry.get("region"),
        threshold=float(yaml_entry.get("threshold", 0.85)),
        confirm_frames=int(yaml_entry.get("confirm_frames", 2)),
        debounce_s=float(yaml_entry.get("debounce_s", 3.0)),
        action=yaml_entry.get("action"),
        outcome=yaml_entry.get("outcome"),
        triggers_end_match=bool(yaml_entry.get("triggers_end_match", False)),
        stat_counter=yaml_entry.get("stat_counter"),
        counter_cap=yaml_entry.get("counter_cap"),
        stat_timer=yaml_entry.get("stat_timer"),
    )

    if "vibe" in yaml_entry:
        v = yaml_entry["vibe"]
        spec.vibe = VibeSpec(
            trigger=v.get("trigger", name),
            intensity=float(v.get("intensity", 0.0)),
            duration=float(v.get("duration", 0.0)),
        )

    if "on_complete" in yaml_entry:
        oc = yaml_entry["on_complete"]
        on_complete = OnCompleteSpec(stat_counter=oc.get("stat_counter"))
        if "vibe" in oc:
            ocv = oc["vibe"]
            on_complete.vibe = VibeSpec(
                trigger=ocv.get("trigger", f"{name}_complete"),
                intensity=float(ocv.get("intensity", 0.0)),
                duration=float(ocv.get("duration", 0.0)),
            )
        spec.on_complete = on_complete

    
    # digit_state fields
    if "states" in yaml_entry:
        raw_states = yaml_entry["states"] or {}
        spec.states = {int(k): str(v) for k, v in raw_states.items()}

    if "initial" in yaml_entry:
        init = yaml_entry["initial"]
        spec.initial = None if init is None else int(init)

    if "on_decrement" in yaml_entry:
        od = yaml_entry["on_decrement"]
        cap = od.get("counter_cap")
        spec.on_decrement = OnDecrementSpec(
            stat_counter=od.get("stat_counter"),
            counter_cap=None if cap is None else int(cap),
        )
        if "vibe" in od:
            odv = od["vibe"]
            spec.on_decrement.vibe = VibeSpec(
                trigger=odv.get("trigger", f"{name}_decrement"),
                intensity=float(odv.get("intensity", 0.0)),
                duration=float(odv.get("duration", 0.0)),
            )
    return spec
