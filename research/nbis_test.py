#!/usr/bin/env python3
"""M1 — the recognition fork test: does NBIS find usable minutiae in our frame?

Feeds our captured 88x108 frame (several preprocessings) through NBIS:
  raw 8-bit -> cwsq (WSQ) -> mindtct (-m1 -> .xyt minutiae) + nfiq (quality 1-5).
Counts minutiae and average minutia quality per variant, draws an overlay, and
prints a verdict (optimistic: NBIS finds usable minutiae -> libfprint matches for
us; fallback: it doesn't -> custom preprocessing/matching needed).

Usage: nbis_test.py <session_dir>
Outputs (PNG overlays, NBIS files) stay in the session dir (the vault).
"""
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
from render_pgm import read_p2  # noqa: E402

PPI = 500
UPSCALE = 4
PAD = 320  # WSQ/cwsq require >= 256x256; pad the small sensor frame into a canvas


def norm8(a: np.ndarray, invert: bool = False) -> np.ndarray:
    lo, hi = np.percentile(a, 2), np.percentile(a, 98)
    out = np.clip((a - lo) / (hi - lo + 1e-9), 0, 1)
    if invert:
        out = 1.0 - out
    return (out * 255).astype(np.uint8)


def clahe(a8: np.ndarray, tiles: int = 8, clip: float = 2.0) -> np.ndarray:
    """Contrast-Limited Adaptive Histogram Equalization (pure numpy).

    No skimage/cv2 in the venv, so this is a from-scratch CLAHE: per-tile clipped
    histogram-equalization LUTs, bilinearly interpolated between the four nearest
    tile centers per pixel (the standard CLAHE smoothing, no tile-boundary seams).
    Input/output are uint8. Local normalization is what the design called for on
    these small, low-contrast capacitive frames.
    """
    h, w = a8.shape
    ty = np.linspace(0, h, tiles + 1).astype(int)
    tx = np.linspace(0, w, tiles + 1).astype(int)
    maps = np.zeros((tiles, tiles, 256))  # one 256-entry LUT per tile
    for i in range(tiles):
        for j in range(tiles):
            blk = a8[ty[i]:ty[i + 1], tx[j]:tx[j + 1]]
            if blk.size == 0:
                maps[i, j] = np.arange(256)
                continue
            hist = np.bincount(blk.ravel(), minlength=256).astype(float)
            if clip > 0:  # clip tall bins, redistribute the excess uniformly
                limit = clip * blk.size / 256.0
                excess = np.clip(hist - limit, 0, None).sum()
                hist = np.minimum(hist, limit) + excess / 256.0
            cdf = np.cumsum(hist)
            maps[i, j] = (cdf - cdf.min()) / (cdf.max() - cdf.min() + 1e-9) * 255

    cy = (ty[:-1] + ty[1:]) / 2.0
    cx = (tx[:-1] + tx[1:]) / 2.0

    def brackets(coords, centers):
        idx = np.searchsorted(centers, coords)
        i1 = np.clip(idx, 0, len(centers) - 1)
        i0 = np.clip(idx - 1, 0, len(centers) - 1)
        denom = centers[i1] - centers[i0]
        wgt = np.where(denom > 0, (coords - centers[i0]) / (denom + 1e-9), 0.0)
        return i0, i1, np.clip(wgt, 0, 1)

    iy0, iy1, wy = brackets(np.arange(h), cy)
    ix0, ix1, wx = brackets(np.arange(w), cx)
    TY0, TY1 = iy0[:, None], iy1[:, None]
    TX0, TX1 = ix0[None, :], ix1[None, :]
    m00 = maps[TY0, TX0, a8]
    m01 = maps[TY0, TX1, a8]
    m10 = maps[TY1, TX0, a8]
    m11 = maps[TY1, TX1, a8]
    WY, WX = wy[:, None], wx[None, :]
    top = m00 * (1 - WX) + m01 * WX
    bot = m10 * (1 - WX) + m11 * WX
    return np.clip(top * (1 - WY) + bot * WY, 0, 255).astype(np.uint8)


def pad_to(a8: np.ndarray, size: int = PAD) -> np.ndarray:
    """Center the print in a size x size canvas filled with its median (a flat
    background yields few false minutiae). Returns padded image; record offset
    so overlays/coords map back if needed."""
    h, w = a8.shape
    canvas = np.full((size, size), int(np.median(a8)), dtype=np.uint8)
    oy, ox = (size - h) // 2, (size - w) // 2
    canvas[oy:oy + h, ox:ox + w] = a8
    return canvas


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def nbis_on(img8: np.ndarray, sess: str, tag: str) -> dict:
    img8 = pad_to(img8)
    h, w = img8.shape
    raw = f"{sess}/_{tag}.raw"
    img8.tofile(raw)
    # raw -> WSQ (cwsq writes <base>.wsq)
    rc, out = run(["cwsq", "2.25", "wsq", raw, "-raw_in", f"{w},{h},8,{PPI}"])
    wsq = f"{sess}/_{tag}.wsq"
    if rc != 0 or not os.path.exists(wsq):
        return {"tag": tag, "error": f"cwsq failed: {out.strip()[:200]}"}
    # mindtct -> <oroot>.xyt (x y theta quality)
    oroot = f"{sess}/_{tag}"
    rc, out = run(["mindtct", "-m1", wsq, oroot])
    xyt = f"{oroot}.xyt"
    minu = []
    if os.path.exists(xyt):
        for line in open(xyt):
            parts = line.split()
            if len(parts) >= 4:
                minu.append(tuple(map(int, parts[:4])))
    # nfiq overall quality (1 best .. 5 worst)
    rc2, nout = run(["nfiq", wsq])
    nfiq = nout.strip().split()[0] if nout.strip() else "?"
    quals = [m[3] for m in minu]
    return {
        "tag": tag, "n": len(minu),
        "q_mean": round(float(np.mean(quals)), 1) if quals else 0,
        "q_ge40": sum(1 for q in quals if q >= 40),
        "nfiq": nfiq, "minu": minu, "img": img8,
    }


def overlay(res: dict, sess: str) -> None:
    if "img" not in res:
        return
    a = res["img"]
    im = Image.fromarray(a, "L").convert("RGB").resize(
        (a.shape[1] * UPSCALE, a.shape[0] * UPSCALE), Image.NEAREST)
    d = ImageDraw.Draw(im)
    for x, y, _t, q in res["minu"]:
        cx, cy = x * UPSCALE, y * UPSCALE
        color = (0, 255, 0) if q >= 40 else (255, 160, 0)
        d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], outline=color, width=2)
    im.save(f"{sess}/minutiae_{res['tag']}.png")


def main() -> int:
    sess = sys.argv[1].rstrip("/")
    fpname = "fingerprint.pgm" if os.path.exists(
        f"{sess}/fingerprint.pgm") else "fingerprint-0.pgm"
    fp = read_p2(f"{sess}/{fpname}")
    c0 = read_p2(f"{sess}/clear-0.pgm")
    diff = c0 - fp  # baseline subtraction -> ridges

    variants = {
        "raw": norm8(fp),
        "raw_inv": norm8(fp, invert=True),
        "diff": norm8(diff),
        "diff_inv": norm8(diff, invert=True),
    }

    print(f"session: {sess}  ({fp.shape[0]}x{fp.shape[1]} px @ ~{PPI}ppi)\n")
    print(f"{'variant':10} {'minutiae':>9} {'q>=40':>6} {'q_mean':>7} {'nfiq':>5}")
    best = None
    for tag, img in variants.items():
        r = nbis_on(img, sess, tag)
        if "error" in r:
            print(f"{tag:10} ERROR: {r['error']}")
            continue
        print(f"{tag:10} {r['n']:>9} {r['q_ge40']:>6} {r['q_mean']:>7} {r['nfiq']:>5}")
        overlay(r, sess)
        if best is None or r["q_ge40"] > best["q_ge40"]:
            best = r

    print()
    if best and best["q_ge40"] >= 8:
        print(f"VERDICT: OPTIMISTIC — NBIS found {best['q_ge40']} good minutiae "
              f"(q>=40) on the '{best['tag']}' variant. libfprint's bundled matcher "
              f"is likely viable; v0 can lean on it.")
    elif best and best["n"] >= 6:
        print(f"VERDICT: MARGINAL — {best['n']} minutiae but only {best['q_ge40']} "
              f"strong on '{best['tag']}'. Needs better preprocessing/capture before "
              f"trusting NBIS; borderline.")
    else:
        print("VERDICT: FALLBACK — NBIS finds too few usable minutiae on this small "
              "noisy frame. Expect custom preprocessing (CLAHE) or SIFT-style matching.")
    print(f"overlays: {sess}/minutiae_*.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
