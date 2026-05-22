# Template Calibration Workflow

This is how you go from "I want to track a new event" to "the tracker is reliably detecting it." It's a tight loop: make a template, drop it in, verify with the debug overlay, tune if needed.

---

## One-time setup

```
pip install -r requirements.txt
```

Install [Intiface Central](https://intiface.com/central/) (skip if you only want CSV stats — set `USING_INTIFACE = no` in `config.ini`).

Set DBD's video settings:
- **Resolution: 1920×1080** (other resolutions work but require resizing per frame; 1080p is fastest)
- **Aspect ratio: 16:9**
- **Display mode: Borderless Windowed** (true exclusive fullscreen sometimes gives `dxcam` black frames)

---

## The loop, per template

### Step 1: Take a screenshot

Get into the in-game state where the HUD element you want to track is visible. Press your screenshot key (Windows: `Win+Shift+S`). Crop the screenshot to just the unique visual element of the HUD piece.

**What "unique" means:**
- The element appears the same way every time it shows up
- The element does NOT appear when the state isn't active
- Avoid timer numbers, progress bars filling, or text that changes

Examples:
- ✅ Good: the static icon part of "M1 to Heal" prompt
- ❌ Bad: the progress bar (changes constantly)
- ✅ Good: the chase music icon (assuming you cropped it at a non-pulsing frame)
- ❌ Bad: the entire match scoreboard (varies by player count, scores, etc.)

### Step 2: Save the template

```
data/templates/t_<event_name>.png
```

Naming: lowercase, underscores, snake_case. Examples:
```
t_match_start.png
t_notif_gen_complete.png
t_chase_indicator.png
t_action_heal_self.png
```

The `t_` prefix is optional but recommended (matches OverStim's convention).

### Step 3: Run the debug overlay

```
python debug_overlay.py
```

This opens a live preview of your screen with colored boxes around every template match. Switch to DBD and reproduce the in-game state.

**You're looking for:**
- A **green box** around your HUD element → working
- A **yellow box** → close but below threshold; adjust threshold or improve the template
- A **red box in the wrong place** → false positive; tighten the region

**Useful keys in the overlay:**
- `+` / `-` — change threshold live (default 0.85)
- `R` — toggle region boxes (shows where each event is allowed to match)
- `M` — match-only mode (hide non-matching templates)
- `↑` / `↓` — focus a single template (cycle)
- `S` — save current frame as PNG for inspection
- `Q` / `Esc` — quit

### Step 4: Add the event to events.yaml

If you just want it loaded for debug viewing, no YAML entry is needed.

If you want it to do something (count, time, vibrate), add a block. Minimum required is `type`:

```yaml
templates:
  notif_gen_complete:
    type: counter
    stat_counter: gens_completed
    counter_cap: 5
    region: bottom_left
    vibe:
      trigger: gen_complete
      intensity: 0.5
      duration: 6.0
```

The schema is documented at the top of `events.yaml`. Five types exist:

| `type:`      | What it does                                                                 |
| ------------ | ---------------------------------------------------------------------------- |
| `lifecycle`  | `match_start` / `match_end` — boundary markers for stat tracking             |
| `outcome`    | Latches the match result (escaped / sacrificed / killed)                     |
| `counter`    | Rising-edge: increments a stat counter, optionally fires a timed vibe        |
| `continuous` | Active-while-matching: accumulates a timer, optionally maintains a toggle vibe; `on_complete:` fires a counter + timed vibe on the falling edge |
| `passive`    | Loaded for debug viewing only — no stat or vibe side effects                 |

### Step 5: Tune if needed

Common adjustments to the YAML block:

**Threshold** (`threshold: 0.85`)
- Too many false positives → raise toward 0.9
- Missing real matches → lower toward 0.78
- Animated UI elements usually want 0.78–0.82

**Region** (`region: bottom_left`)
- Use a named region from `regions.py`. Tighter = fewer false positives.
- Omit for full-frame search (slower, more false positives, but most flexible).

**Confirm frames** (`confirm_frames: 2`)
- Number of consecutive positive frames before the event fires
- Increase to 3-4 if you see one-frame spurious matches
- Decrease to 1 if events are fleeting (sub-100ms)

**Debounce** (`debounce_s: 3.0`)
- Minimum seconds between consecutive fires of the same event
- For frequent events like skill checks, lower to 1.0
- For rare events like gen complete, raise to 10.0

### Step 6: Run the main tracker

```
python dbd_tracker.py
```

Click **Start**. Play a match. Click **Stop** when done (or close the window — it'll flush any in-progress match).

`dbd_stats.csv` gets a row per match. New stat columns are added automatically when new events are added to YAML — old rows just have empty cells for new columns.

---

## Multiple variants for one event

If a HUD element has variants (different colors, lighting, animation frames), drop multiple PNGs with numeric suffixes:

```
t_chase_indicator.png
t_chase_indicator_2.png
t_chase_indicator_3.png
```

Currently the framework treats these as separate events — to merge them, you'd need a small extension to `dbdcv.py` (loop over variants in `match()`). I left this as a future enhancement since it's only needed if you find single templates aren't reliable.

---

## Troubleshooting

**Debug overlay shows red boxes everywhere on bright UI**
→ Threshold too low. Press `+` to raise it.

**A template matches at low confidence but is the right spot**
→ Template is probably too large/has too much background. Re-crop tighter around the unique visual.

**A template matches in the wrong location**
→ Add a `region:` to the YAML entry to restrict where it can match. See `regions.py` for the full list, or add a new one.

**`dxcam` returns black frames**
→ DBD is in exclusive fullscreen. Switch to borderless windowed mode.

**`buttplug-py` connect fails**
→ Make sure Intiface Central is running and the server is started (green play button in its UI). Default address is `ws://127.0.0.1:12345`.

**`pynput.HotKey.parse` error on startup**
→ Check `EMERGENCY_STOP_KEY_COMBO` format in `config.ini`. Use angle brackets: `<ctrl>+<shift>+<f10>`.

**Stats are wrong by 1 (off-by-one counters)**
→ Lower `debounce_s` if the event fires multiple times in quick succession. Raise it if the event was double-counted from a brief flicker.

**Timers seem off**
→ Check `confirm_frames` — if it's 3 and your capture rate is 10fps, you lose ~0.3s at the start of every state.

---

## Useful template ideas (DBD-specific)

These are starting points. Each requires its own template PNG.

| Event              | Where to look in HUD                          | Best `type`      |
| ------------------ | --------------------------------------------- | ---------------- |
| `match_start`      | Loading screen → "READY" indicator            | lifecycle        |
| `match_end`        | Top "TRIAL OVER" / "DEAD" banner              | lifecycle        |
| `result_escaped`   | Center "ESCAPED" banner                       | outcome          |
| `result_sacrificed`| Center "SACRIFICED" banner                    | outcome          |
| `result_killed`    | Center "KILLED" banner                        | outcome          |
| `self_healthy`     | Bottom-left portrait — healthy aura           | continuous       |
| `self_injured`     | Bottom-left portrait — bloodied aura          | continuous       |
| `self_dying`       | Bottom-left portrait — dying state            | continuous       |
| `self_hooked`      | Bottom-left portrait — hook icon              | continuous (+on_complete: times_hooked) |
| `bleeding_out`     | Center screen — dying timer                   | passive (special-cased in dbd_tracker.py for global override) |
| `action_repair`    | Bottom-center — "M1 to Repair" prompt         | continuous       |
| `action_heal_self` | Bottom-center — "M1 to Heal Yourself" prompt  | continuous (+on_complete) |
| `action_heal_other`| Bottom-center — "M1 to Heal Survivor" prompt  | continuous (+on_complete) |
| `action_unhook`    | Bottom-center — "M1 to Unhook" prompt         | continuous       |
| `action_cleanse`   | Bottom-center — "M1 to Cleanse" prompt        | continuous (+on_complete: totems_cleansed) |
| `chase_indicator`  | Above center — chase music HUD cue            | continuous       |
| `skill_check`      | Center — skill check ring                     | counter          |
| `notif_gen_complete`| Bottom-left log — "Generator repaired"       | counter (+vibe)  |
| `notif_unhook`     | Bottom-left log — "<Survivor> was unhooked"   | counter          |
| `notif_hook`       | Bottom-left log — "<Survivor> was hooked"     | counter          |

---

## Recommended order to build templates

1. **`match_start` and `match_end`** — without these, stats don't accumulate at all. The rest of the templates can be loaded as passive (no YAML entry) but nothing useful happens until match boundaries are detected.

2. **`result_escaped` / `result_sacrificed` / `result_killed`** — these give you the `match_outcome` column in CSV and let escape vibe fire.

3. **`self_hooked`** — high-value: drives `times_hooked` counter and the `got_unhooked` rising vibe.

4. **`action_repair`** — drives `time_on_gen_s`, one of the headline stats you asked about.

5. **`chase_indicator`** — drives `time_in_chase_s`, another headline stat.

6. **`action_heal_self` / `action_heal_other`** — drive `heals_completed_self` / `heals_completed_other`.

7. **`notif_gen_complete`** — drives the `gens_completed` counter (more reliable than counting your own repair sessions).

Everything else is nice-to-have. The first 6 give you the original ask (heals / gens / chase time per match).
