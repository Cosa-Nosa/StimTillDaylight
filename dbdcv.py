"""
dbdcv.py — Computer vision layer.

Differs from OverStim's owcv.py in two key ways:
  1. Templates are auto-loaded by filename from data/templates/
     (no hardcoded coords dict — uses the event_registry instead).
  2. Match supports either full-frame search OR an optional region hint.

Capture, aspect-ratio handling, and grayscale matching follow OverStim's
pattern closely. The matching method returns (confidence, location) so callers
can draw debug overlays.
"""

import os
from math import gcd
from typing import Optional

import numpy as np
import cv2 as cv

try:
    import dxcam_cpp as _dxcam
except ImportError:
    try:
        import dxcam as _dxcam
    except ImportError:
        raise RuntimeError("Neither dxcam_cpp nor dxcam is installed. "
                           "Run: pip install dxcam-cpp  (or: pip install dxcam)")

from regions import get_region


resolutions_21_by_9 = (
    (2560, 1080), (3440, 1440), (3840, 1600), (5120, 2160),
    (5760, 2400), (7680, 3200), (8640, 3600), (10240, 4320),
)


def resolution_to_aspect_ratio_string(w: int, h: int):
    if (w, h) in resolutions_21_by_9:
        return "21:9"
    d = gcd(w, h)
    return f"{w // d}:{h // d}"


def resource_path(rel: str):
    return os.path.join(os.path.abspath("."), rel)


class ComputerVision:
    """
    Loads templates discovered by the event registry. Supports full-frame and
    region-restricted matching. Always normalizes captured frames to 1920x1080
    grayscale before matching.
    """

    def __init__(self, event_registry, print_detected_resolution=True):
        self.base_resolution = {"width": 1920, "height": 1080}
        self.base_aspect_ratio = self.base_resolution["width"] / self.base_resolution["height"]

        self.screen = _dxcam.create(max_buffer_len=1)

        # Detect monitor resolution
        first = self.screen.grab()
        detected = first.shape[:2]
        self.final_resolution = {"width": detected[1], "height": detected[0]}
        self.resolution_mismatch = self.final_resolution != self.base_resolution
        if print_detected_resolution:
            print(f"[CV] Detected monitor resolution: {self.final_resolution['width']}x{self.final_resolution['height']}")

        self.final_aspect_ratio = self.final_resolution["width"] / self.final_resolution["height"]
        self.aspect_ratio_mismatch = self.final_aspect_ratio != self.base_aspect_ratio
        if self.aspect_ratio_mismatch:
            print(f"[CV] Detected aspect ratio: "
                  f"{resolution_to_aspect_ratio_string(self.final_resolution['width'], self.final_resolution['height'])}")
            print(f"[CV] Set DBD's aspect ratio to 16:9 for accurate matching.")

        h_pad = abs(round((self.final_resolution["width"] - self.base_resolution["width"]) // 2))
        v_pad = abs(round((self.final_resolution["height"] - self.base_resolution["height"]) // 2))
        self.aspect_ratio_crop = np.ix_([v_pad, -v_pad], [h_pad, -h_pad])

        # ── Template + mask loading (auto-discovered via event_registry) ────
        self.event_registry = event_registry
        self.templates: dict[str, np.ndarray] = {}
        self.masks: dict[str, np.ndarray] = {}
        for name, spec in event_registry.items():
            if spec.template_path is None:
                # digit_state events own no template file — they reference others by name
                continue
            img = cv.imread(spec.template_path, cv.IMREAD_COLOR)
            if img is None:
                print(f"[CV] WARNING: failed to load template {spec.template_path}")
                continue
            self.templates[name] = cv.cvtColor(img, cv.COLOR_BGR2GRAY)

            # Optional mask: m_<name>.png in same dir
            mask_path = os.path.join(os.path.dirname(spec.template_path), f"m_{name}.png")
            if os.path.exists(mask_path):
                mask_img = cv.imread(mask_path, cv.IMREAD_COLOR)
                if mask_img is not None:
                    self.masks[name] = cv.cvtColor(mask_img, cv.COLOR_BGR2GRAY)
                    print(f"[CV] Loaded mask for '{name}'")

        print(f"[CV] Loaded {len(self.templates)} template(s)")

        self.frame: Optional[np.ndarray] = None

    # ── Capture lifecycle ───────────────────────────────────────────────────

    def start_capturing(self, target_fps=30):
        self.screen.start(target_fps=target_fps, video_mode=True)

    def stop_capturing(self):
        try:
            self.screen.stop()
        except Exception:
            pass

    def capture_frame(self):
        screenshot = self.screen.get_latest_frame()
        if screenshot is None:
            return
        if self.aspect_ratio_mismatch:
            screenshot = screenshot[self.aspect_ratio_crop]
        if self.resolution_mismatch:
            screenshot = cv.resize(screenshot, (self.base_resolution["width"], self.base_resolution["height"]))
        self.frame = cv.cvtColor(screenshot, cv.COLOR_BGR2GRAY)

    # ── Matching ────────────────────────────────────────────────────────────

    def _crop_to_region(self, region_name: Optional[str]) -> tuple[np.ndarray, int, int]:
        """Returns (cropped_frame, region_x_offset, region_y_offset)."""
        x, y, w, h = get_region(region_name)
        # Clamp to frame bounds
        x2 = min(x + w, self.frame.shape[1])
        y2 = min(y + h, self.frame.shape[0])
        return self.frame[y:y2, x:x2], x, y

    def match(self, name: str, region: Optional[str] = None) -> tuple[float, tuple[int, int]]:
        """
        Returns (best_confidence, (match_x, match_y)) where (x,y) is the
        top-left of the matched region in full-frame coordinates.

        Returns (0.0, (0, 0)) if no template / no frame / template larger than ROI.
        """
        if self.frame is None or name not in self.templates:
            return 0.0, (0, 0)

        tmpl = self.templates[name]
        roi, x_off, y_off = self._crop_to_region(region)

        if tmpl.shape[0] > roi.shape[0] or tmpl.shape[1] > roi.shape[1]:
            return 0.0, (0, 0)

        if name in self.masks:
            result = cv.matchTemplate(roi, tmpl, cv.TM_CCOEFF_NORMED, mask=self.masks[name])
        else:
            result = cv.matchTemplate(roi, tmpl, cv.TM_CCOEFF_NORMED)

        # NaN can appear with masked matching against uniform regions
        result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        _, max_val, _, max_loc = cv.minMaxLoc(result)
        return float(max_val), (max_loc[0] + x_off, max_loc[1] + y_off)

    def detect_single(self, name: str, region: Optional[str] = None,
                      threshold: float = 0.85) -> bool:
        conf, _ = self.match(name, region)
        return conf >= threshold

    def detect_multiple(self, name: str, region: Optional[str] = None,
                        threshold: float = 0.85) -> int:
        """Returns count of distinct matches above threshold within the region."""
        if self.frame is None or name not in self.templates:
            return 0
        tmpl = self.templates[name]
        roi, _, _ = self._crop_to_region(region)
        if tmpl.shape[0] > roi.shape[0] or tmpl.shape[1] > roi.shape[1]:
            return 0

        if name in self.masks:
            result = cv.matchTemplate(roi, tmpl, cv.TM_CCOEFF_NORMED, mask=self.masks[name])
        else:
            result = cv.matchTemplate(roi, tmpl, cv.TM_CCOEFF_NORMED)
        result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
        cv.threshold(result, threshold, 255, cv.THRESH_BINARY, result)
        contours, _ = cv.findContours(result.astype(np.uint8), cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE)
        return len(contours)
