#!/usr/bin/env python3
"""Estimate the sensor's true PPI from ridge spacing in captured prints.

Adult fingerprint ridges have a well-known physical period (~0.40-0.50 mm,
central ~0.46 mm). Measure the dominant ridge period in PIXELS from each frame's
2D FFT, then PPI = period_px / wavelength_mm * 25.4. Averaging over many clean
right-index frames gives a robust number to check `nbis_test.PPI` against. That
constant is read live below rather than copied here: the first version of this
script hardcoded the superseded 500, so it reported MISCALIBRATED even after the
pipeline had been corrected to 600. Only the NBIS path depends on it (it is the
PPI written into the WSQ header that mindtct then scales minutiae by); the SIFT
and SIGFM paths are scale-free.

Method per frame: clear-finger ridge image -> central crop -> remove low-freq
trend -> Hann window -> 2D FFT power -> radial-average -> peak in the plausible
ridge band (period 4-20 px) -> period_px. Reports per-frame periods and the PPI
estimate for wavelength 0.40 / 0.46 / 0.50 mm.

Usage: measure_ppi.py [glob ...]   (default: clean right-index frames)
"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import vault  # noqa: E402  (vault root + external-volume guard)
from m2_eval import is_clean_baseline  # noqa: E402
from nbis_test import PPI, norm8  # noqa: E402
from render_pgm import read_p2  # noqa: E402

VAULT = vault.frames()
# right-index genuine frames carry the cleanest, fullest ridge field
DEFAULT = [f"{VAULT}/m2c-gal-*", f"{VAULT}/m2c-prb-*", f"{VAULT}/m2g-genidx-*"]
WAVELENGTHS_MM = (0.40, 0.46, 0.50)
PERIOD_MIN, PERIOD_MAX = 4.0, 20.0  # plausible ridge period in pixels


def ridge_period_px(sess: str) -> float | None:
    fp = read_p2(f"{sess}/fingerprint.pgm")
    c0 = read_p2(f"{sess}/clear-0.pgm")
    img = norm8(c0 - fp).astype(np.float64)
    h, w = img.shape
    n = min(h, w)
    img = img[(h - n) // 2:(h - n) // 2 + n, (w - n) // 2:(w - n) // 2 + n]
    # remove slow trend (gradient/DC) so only ridge-scale structure remains
    from numpy.fft import fft2, fftshift
    img -= img.mean()
    win = np.outer(np.hanning(n), np.hanning(n))
    P = np.abs(fftshift(fft2(img * win))) ** 2
    cy = cx = n // 2
    yy, xx = np.indices((n, n))
    r = np.hypot(yy - cy, xx - cx)
    rint = r.astype(int)
    radial = np.bincount(rint.ravel(), P.ravel()) / (np.bincount(rint.ravel()) + 1e-9)
    # plausible ridge band: radius k (cycles per crop) -> period = n / k
    k_lo = max(1, int(np.ceil(n / PERIOD_MAX)))
    k_hi = int(np.floor(n / PERIOD_MIN))
    band = radial[k_lo:k_hi + 1]
    if band.size == 0:
        return None
    k_star = k_lo + int(np.argmax(band))
    return n / k_star


def main() -> int:
    pats = sys.argv[1:] or DEFAULT
    sessions = sorted({s for p in pats for s in glob.glob(os.path.expanduser(p))})
    periods = []
    print("per-frame dominant ridge period:")
    for s in sessions:
        name = os.path.basename(s)
        if not is_clean_baseline(s)[0]:
            continue
        p = ridge_period_px(s)
        if p:
            periods.append(p)
            ppi = p / 0.46 * 25.4
            print(f"  {name:18} period={p:5.2f}px  -> {ppi:4.0f} PPI (@0.46mm)")
    if not periods:
        print("no usable frames.")
        return 1
    med = float(np.median(periods))
    print(f"\nmedian ridge period = {med:.2f} px  (n={len(periods)}, "
          f"std={np.std(periods):.2f})")
    print("implied sensor PPI (PPI = period_px / wavelength_mm * 25.4):")
    for wl in WAVELENGTHS_MM:
        print(f"  ridge wavelength {wl:.2f} mm -> {med / wl * 25.4:4.0f} PPI")
    central = med / 0.46 * 25.4
    # Judge against the band the measurement can actually distinguish rather than
    # a magic tolerance: ridge wavelength is only known to ~0.40-0.50 mm, so these
    # same frames are consistent with any PPI in [lo, hi]. A value inside that band
    # is not evidence of miscalibration and does not justify moving the constant.
    lo = med / max(WAVELENGTHS_MM) * 25.4
    hi = med / min(WAVELENGTHS_MM) * 25.4
    verdict = ("OK" if lo <= PPI <= hi
               else f"MISCALIBRATED, update nbis_test.PPI to ~{central:.0f}")
    print(f"\nbest estimate ~{central:.0f} PPI (central 0.46mm); frames are "
          f"consistent with {lo:.0f}-{hi:.0f} PPI.")
    print(f"nbis_test.PPI = {PPI} (delta {central - PPI:+.0f}) -> {verdict}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
