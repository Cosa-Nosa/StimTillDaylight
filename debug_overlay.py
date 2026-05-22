"""
debug_overlay.py — Live template-matching visualizer.

Usage:
    python debug_overlay.py

Captures your screen, runs all loaded templates against the current frame,
and shows a window with green boxes around matches (red boxes around the
configured regions for each event). Use this to verify that a template you
just dropped into data/templates/ actually matches what you think it matches.

Controls (focus the preview window):
    Q / Esc         Quit
    Space           Pause / resume capture
    S               Save current frame to data/debug_<timestamp>.png
    +  /  -         Raise / lower confidence threshold (default 0.85)
    R               Toggle region overlays (red boxes)
    M               Toggle match-only mode (hide non-matching templates)
    ↑ / ↓           Cycle through templates (focus mode: show only one at a time)
    Esc             Exit focus mode
"""

import os
import sys
import time
import cv2 as cv
import numpy as np

from event_registry import load_event_registry
from regions import get_region
from dbdcv import ComputerVision


CONFIG_DEFAULTS = {
    "events_yaml": "events.yaml",
    "templates_dir": "data/templates",
    "preview_width": 1280,   # downsample for display (won't fit 1920 on small screens)
    "default_threshold": 0.85,
}


def color_for_confidence(conf: float, threshold: float) -> tuple[int, int, int]:
    """BGR color: green if above threshold, yellow if close, red if far."""
    if conf >= threshold:
        return (0, 255, 0)
    if conf >= threshold - 0.10:
        return (0, 255, 255)
    return (40, 40, 200)


def draw_match_box(img, name, x, y, w, h, conf, threshold):
    color = color_for_confidence(conf, threshold)
    cv.rectangle(img, (x, y), (x + w, y + h), color, 2)
    label = f"{name} {conf:.2f}"
    cv.putText(img, label, (x, max(y - 5, 12)),
               cv.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv.LINE_AA)


def draw_region_box(img, region_name):
    x, y, w, h = get_region(region_name)
    cv.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 3)
    cv.putText(img, f"region:{region_name}", (x + 6, y + 22),
               cv.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv.LINE_AA)


def main():
    registry = load_event_registry(CONFIG_DEFAULTS["events_yaml"], CONFIG_DEFAULTS["templates_dir"])
    if not registry:
        print("\nNo templates found. Drop t_<name>.png files into "
              f"{CONFIG_DEFAULTS['templates_dir']}/ and try again.\n")
        sys.exit(1)

    print(f"\nLoaded {len(registry)} template(s):")
    for name, spec in sorted(registry.items()):
        print(f"  - {name:<28} region={spec.region or 'full':<14} threshold={spec.threshold}")
    print()

    cv_obj = ComputerVision(registry, print_detected_resolution=True)
    cv_obj.start_capturing(target_fps=10)

    threshold = CONFIG_DEFAULTS["default_threshold"]
    paused = False
    show_regions = True
    match_only = True
    focus_idx = -1   # -1 means show all
    template_names = sorted(registry.keys())

    cv.namedWindow("DBDTracker — Debug Overlay", cv.WINDOW_NORMAL)

    print("Press H to print controls in the terminal.\n"
          "Controls: Q=quit | Space=pause | S=save | +/-=threshold | "
          "R=regions | M=match-only | up/down=focus next/prev | Esc=clear focus\n")

    try:
        while True:
            if not paused:
                cv_obj.capture_frame()

            frame = cv_obj.frame
            if frame is None:
                time.sleep(0.05)
                continue

            # BGR canvas to draw colored overlays on a grayscale frame
            canvas = cv.cvtColor(frame, cv.COLOR_GRAY2BGR)

            # Decide which events to draw
            if focus_idx >= 0:
                names_to_check = [template_names[focus_idx]]
            else:
                names_to_check = template_names

            # Match each
            best_text_lines = []
            for name in names_to_check:
                spec = registry[name]
                conf, (mx, my) = cv_obj.match(name, region=spec.region)

                hit = conf >= threshold
                if match_only and not hit and focus_idx < 0:
                    continue

                tmpl = cv_obj.templates.get(name)
                if tmpl is None:
                    continue
                h, w = tmpl.shape[:2]
                draw_match_box(canvas, name, mx, my, w, h, conf, threshold)

                if hit or focus_idx >= 0:
                    best_text_lines.append(f"{name:<28} {conf:.3f} {'HIT' if hit else ''}")

            # Status bar
            status = [
                f"threshold={threshold:.2f}",
                f"templates={len(template_names)}",
                f"focus={template_names[focus_idx] if focus_idx >= 0 else 'all'}",
                "PAUSED" if paused else "LIVE",
                "regions:ON" if show_regions else "regions:off",
                "match-only:ON" if match_only else "match-only:off",
            ]
            cv.rectangle(canvas, (0, 0), (1920, 28), (20, 20, 20), -1)
            cv.putText(canvas, " | ".join(status), (8, 19),
                       cv.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv.LINE_AA)

            # Side panel with hit list
            if best_text_lines:
                panel_h = 18 * (len(best_text_lines) + 1) + 10
                cv.rectangle(canvas, (0, 30), (520, 30 + panel_h), (20, 20, 20), -1)
                cv.putText(canvas, "Confidences:", (8, 48),
                           cv.FONT_HERSHEY_SIMPLEX, 0.5, (180, 220, 180), 1, cv.LINE_AA)
                for i, line in enumerate(best_text_lines):
                    cv.putText(canvas, line, (8, 68 + i * 18),
                               cv.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv.LINE_AA)

            # Region overlays
            if show_regions:
                seen_regions = set()
                for name in names_to_check:
                    spec = registry[name]
                    key = spec.region or "full"
                    if key not in seen_regions:
                        draw_region_box(canvas, spec.region)
                        seen_regions.add(key)

            # Downsample for display
            preview_w = CONFIG_DEFAULTS["preview_width"]
            scale = preview_w / 1920.0
            preview = cv.resize(canvas, (preview_w, int(1080 * scale)))
            cv.imshow("DBDTracker — Debug Overlay", preview)

            key = cv.waitKey(20) & 0xFF
            if key == ord('q') or key == 27 and focus_idx < 0:
                break
            elif key == 27:
                focus_idx = -1
            elif key == ord(' '):
                paused = not paused
            elif key == ord('s'):
                stamp = time.strftime("%Y%m%d_%H%M%S")
                out = os.path.join("data", f"debug_{stamp}.png")
                os.makedirs("data", exist_ok=True)
                cv.imwrite(out, frame)
                print(f"Saved {out}")
            elif key == ord('+') or key == ord('='):
                threshold = min(1.0, round(threshold + 0.02, 2))
            elif key in (ord('-'), ord('_')):
                threshold = max(0.0, round(threshold - 0.02, 2))
            elif key == ord('r'):
                show_regions = not show_regions
            elif key == ord('m'):
                match_only = not match_only
            elif key == 82 or key == ord('w'):  # up arrow (or 'w')
                focus_idx = (focus_idx + 1) % len(template_names) if focus_idx >= 0 else 0
            elif key == 84 or key == ord('s') and focus_idx >= 0:
                focus_idx = (focus_idx - 1) % len(template_names) if focus_idx >= 0 else len(template_names) - 1
            elif key == ord('h'):
                print(__doc__)

    finally:
        cv_obj.stop_capturing()
        cv.destroyAllWindows()


if __name__ == "__main__":
    main()
