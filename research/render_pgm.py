#!/usr/bin/env python3
"""Render Goodix P2 PGM frames (12-bit, 80x64) to viewable PNGs.

tool.write_pgm emits ASCII "P2\\n{H} {W}\\n4095" with the raster laid out in
80 rows x 64 cols. We autoscale to 8-bit and upscale for visibility, and also
produce a (fingerprint - clear baseline) difference, which usually reveals ridge
structure on these small, low-contrast capacitive sensors.

Usage: render_pgm.py <session_dir>
Outputs <session_dir>/*.png (kept in the vault, never the repo).
"""
import sys

import numpy as np
from PIL import Image

UPSCALE = 6


def read_p2(path: str) -> np.ndarray:
    # tool.write_pgm emits header "P2\n{H} {W}\n{maxval}" but lays the raster out
    # in H-cols rows... in practice: rows = toks[2], cols = toks[1].
    with open(path) as f:
        toks = f.read().split()
    assert toks[0] == "P2", toks[0]
    cols, rows = int(toks[1]), int(toks[2])
    vals = list(map(int, toks[4:]))
    a = np.array(vals[: rows * cols], dtype=np.float64).reshape(rows, cols)
    return a


def _shape(a):
    return a.shape[1], a.shape[0]  # (W, H) for PIL resize


def autoscale(a: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    if hi <= lo:
        hi = a.max()
        lo = a.min()
    out = np.clip((a - lo) / (hi - lo + 1e-9), 0, 1) * 255
    return out.astype(np.uint8)


def save(a8: np.ndarray, path: str) -> None:
    h, w = a8.shape
    Image.fromarray(a8, "L").resize((w * UPSCALE, h * UPSCALE),
                                    Image.NEAREST).save(path)


def stats(name: str, a: np.ndarray) -> str:
    return (f"{name}: min={a.min():.0f} max={a.max():.0f} mean={a.mean():.1f} "
            f"std={a.std():.1f} p1={np.percentile(a,1):.0f} p99={np.percentile(a,99):.0f}")


def main() -> int:
    import os
    sess = sys.argv[1].rstrip("/")
    fpname = "fingerprint.pgm" if os.path.exists(
        f"{sess}/fingerprint.pgm") else "fingerprint-0.pgm"
    fp = read_p2(f"{sess}/{fpname}")
    c0 = read_p2(f"{sess}/clear-0.pgm")

    print(stats("fingerprint", fp))
    print(stats("clear-0    ", c0))

    save(autoscale(fp), f"{sess}/fingerprint.png")
    save(autoscale(c0), f"{sess}/clear-0.png")

    diff = c0 - fp  # finger presses lower the capacitive reading; baseline - finger
    print(stats("clear-finger", diff))
    save(autoscale(diff), f"{sess}/fingerprint_minus_clear.png")

    print(f"wrote PNGs to {sess}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
