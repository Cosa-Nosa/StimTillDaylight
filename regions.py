"""
regions.py — Named region bounding boxes at 1920x1080.

A region is an optional hint for template matching that restricts the search
area. It speeds up matching and reduces false positives, but is not required —
templates without a region search the entire frame.

Each region is (x, y, w, h). Negative values are not used.

Add new regions here as needed. Names declared here can be referenced by name
in events.yaml under the `region:` key.
"""

REGIONS = {
    # Full frame — the default if a template doesn't specify a region
    "full":          (0,    0,   1920, 1080),

    # Top band
    "top":           (0,    0,   1920, 200),
    "top_left":      (0,    0,    400, 200),
    "top_center":    (560,  0,    800, 200),
    "top_right":     (1520, 0,    400, 200),

    # Center
    "center":        (560,  290,  800, 500),
    "center_top":    (560,  100,  800, 350),
    "center_bottom": (560,  600,  800, 350),

    # Bottom band — where most of DBD's HUD lives
    "bottom":        (0,    750,  1920, 330),
    "bottom_left":   (0,    750,  600,  330),
    "bottom_center": (660,  750,  600,  330),
    "bottom_right":  (1320, 750,  600,  330),

    # Side bands
    "left":          (0,    100,  400,  900),
    "right":         (1520, 100,  400,  900),
}


def get_region(name_or_none) -> tuple:
    """Returns the (x,y,w,h) for a named region, or the 'full' frame if None/unknown."""
    if name_or_none is None:
        return REGIONS["full"]
    if name_or_none not in REGIONS:
        print(f"[regions] Unknown region '{name_or_none}', falling back to full frame")
        return REGIONS["full"]
    return REGIONS[name_or_none]
