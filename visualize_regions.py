"""Visualize named regions from regions.py.

Run from the project root:
    python visualize_regions.py

This opens a 1920x1080 canvas with every named region drawn and labeled.
"""

import cv2 as cv
import numpy as np

from regions import REGIONS


def main():
    width, height = 1920, 1080
    canvas = np.zeros((height, width, 3), dtype=np.uint8)

    for name, (x, y, w, h) in REGIONS.items():
        cv.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 0), 3)
        cv.putText(
            canvas,
            name,
            (x + 8, y + 24),
            cv.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv.LINE_AA,
        )

    window_name = "DBDTracker Region Visualizer"
    cv.namedWindow(window_name, cv.WINDOW_NORMAL)
    cv.resizeWindow(window_name, 1280, 720)
    cv.imshow(window_name, canvas)
    print("Press any key to exit.")
    cv.waitKey(0)
    cv.destroyAllWindows()


if __name__ == "__main__":
    main()
