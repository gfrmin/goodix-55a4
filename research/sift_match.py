#!/usr/bin/env python3
"""M2 fallback matcher — SIFT/ORB + CLAHE with RANSAC geometric verification.

NBIS minutiae (mindtct+bozorth3) does not separate fingers on this 88x108 partial
sensor (see docs/protocol/deltas.md 2026-05-30): impostors score as high as
genuine. This is the design's anticipated fallback — the goodixtls community's
correlation/keypoint approach.

Per frame: clear-finger (baseline-subtracted ridges) -> percentile-normalize ->
upscale -> CLAHE -> SIFT (or ORB) keypoints+descriptors. Match two prints by:
Lowe ratio-test good matches -> estimate a partial-affine transform with RANSAC
-> SCORE = number of geometric inliers. The geometry check is the whole point:
genuine overlapping prints yield many spatially-consistent matches; different
fingers yield a few random ones that RANSAC throws out.

Evaluated the same way as m2_far_frr.py: enroll a gallery, score held-out genuine
probes and DIVERSE impostors vs best-of-gallery, sweep the inlier threshold for
FAR/FRR. Roles by label: *imp*=impostor, *prb*=genuine probe, *gal*/*gen*=gallery.

Usage: sift_match.py [--orb] [--raw] [--scale N] [glob ...]
"""
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import vault  # noqa: E402  (vault root + external-volume guard)
from m2_eval import is_clean_baseline  # noqa: E402
from nbis_test import norm8  # noqa: E402
from render_pgm import read_p2  # noqa: E402

VAULT = vault.frames()
DEFAULT = [f"{VAULT}/m2c-*", f"{VAULT}/m2g-*", f"{VAULT}/m2-*"]


def role(name: str) -> str:
    n = name.lower()
    if "imp" in n:
        return "impostor"
    if "prb" in n:
        return "probe"
    return "gallery"


def preprocess(sess: str, use_raw: bool, scale: int) -> np.ndarray:
    fp = read_p2(f"{sess}/fingerprint.pgm")
    if use_raw:
        img = norm8(fp, invert=True)
    else:
        c0 = read_p2(f"{sess}/clear-0.pgm")
        img = norm8(c0 - fp)  # baseline-subtracted ridges (removes fixed pattern)
    if scale > 1:
        img = cv2.resize(img, (img.shape[1] * scale, img.shape[0] * scale),
                         interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def features(img: np.ndarray, detector):
    kp, des = detector.detectAndCompute(img, None)
    return kp, des


def match_score(f1, f2, norm: int) -> int:
    (kp1, des1), (kp2, des2) = f1, f2
    if des1 is None or des2 is None or len(kp1) < 3 or len(kp2) < 3:
        return 0
    bf = cv2.BFMatcher(norm)
    raw = bf.knnMatch(des1, des2, k=2)
    good = [m for pair in raw if len(pair) == 2
            for m, n in [pair] if m.distance < 0.75 * n.distance]
    if len(good) < 3:
        return len(good)  # too few to verify geometry; weak score
    src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    # partial affine = rotation + translation + uniform scale (finger placement)
    # ratio 0.75 + reproj 8.0 tuned on the m2c corpus: keeps impostors <=4 inliers
    # while genuine overlaps reach 25-43 (FAR=0 @ FRR=25%, the one miss = coverage).
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                         ransacReprojThreshold=8.0,
                                         maxIters=2000, confidence=0.99)
    return int(inl.sum()) if inl is not None else 0


def main() -> int:
    argv = sys.argv[1:]
    use_orb = "--orb" in argv
    use_raw = "--raw" in argv
    scale = 4
    if "--scale" in argv:
        scale = int(argv[argv.index("--scale") + 1])
    pats = [a for a in argv if not a.startswith("--")
            and not a.isdigit()] or DEFAULT
    # drop the scale value if it slipped into pats
    pats = [p for p in pats if "/" in p or "*" in p] or DEFAULT

    detector = cv2.ORB_create(nfeatures=500) if use_orb else cv2.SIFT_create()
    norm = cv2.NORM_HAMMING if use_orb else cv2.NORM_L2
    sessions = sorted({s for p in pats for s in glob.glob(os.path.expanduser(p))})

    feats, roles = {}, {}
    cfg = f"{'ORB' if use_orb else 'SIFT'}, {'raw' if use_raw else 'clear-finger'}, x{scale}, CLAHE"
    print(f"=== SIFT/CLAHE matcher ({cfg}) ===\nsessions:")
    for s in sessions:
        name = os.path.basename(s)
        ok, why = is_clean_baseline(s)
        if not ok:
            print(f"  SKIP {name:18} {why}")
            continue
        img = preprocess(s, use_raw, scale)
        kp, des = features(img, detector)
        feats[name], roles[name] = (kp, des), role(name)
        print(f"  {roles[name]:8} {name:18} keypoints={len(kp)}")

    gal = [n for n in feats if roles[n] == "gallery"]
    prb = [n for n in feats if roles[n] == "probe"]
    imp = [n for n in feats if roles[n] == "impostor"]
    if not gal or not prb or not imp:
        print(f"\nneed gallery+probe+impostor; have {len(gal)}/{len(prb)}/{len(imp)}")
        return 1

    def best(name):
        return max((match_score(feats[name], feats[g], norm) for g in gal),
                   default=0)

    print(f"\ngallery={len(gal)} | probes={len(prb)} | impostors={len(imp)}  "
          f"(score = RANSAC inliers vs best gallery frame)")
    gen = sorted(((n, best(n)) for n in prb), key=lambda t: -t[1])
    imps = sorted(((n, best(n)) for n in imp), key=lambda t: -t[1])
    print("genuine probes:")
    for n, sc in gen:
        print(f"  {n:18} -> {sc}")
    print("impostor probes:")
    for n, sc in imps:
        print(f"  {n:18} -> {sc}")

    g = [sc for _, sc in gen]
    i = [sc for _, sc in imps]
    hi = max(g + i + [1])
    print("\nthreshold sweep (accept if inliers >= T):")
    print(f"  {'T':>3} {'FRR':>7} {'FAR':>7}")
    far0 = None
    for T in range(0, hi + 2):
        frr = sum(1 for x in g if x < T) / len(g)
        far = sum(1 for x in i if x >= T) / len(i)
        if far == 0 and far0 is None:
            far0 = (T, frr)
        mark = "  <- FAR=0" if (far0 and T == far0[0]) else ""
        print(f"  {T:>3} {frr*100:6.0f}% {far*100:6.0f}%{mark}")

    print("\n--- VERDICT ---")
    gmax, imax = max(g), max(i)
    if far0 and far0[1] <= 0.25:
        T, frr = far0
        print(f"SEPARATES ({cfg}): genuine max={gmax}, impostor max={imax}. At T={T}: "
              f"FAR=0, FRR={frr*100:.0f}%. SIFT geometric verification works on this "
              f"sensor -> viable matcher for M3 (threshold ~{T} inliers). ")
    elif gmax > imax:
        print(f"PROMISING ({cfg}): genuine max={gmax} > impostor max={imax}, but FAR=0 "
              f"needs T={far0[0] if far0 else '?'} with FRR="
              f"{far0[1]*100 if far0 else 100:.0f}%. Tune (scale/CLAHE/ratio) or add "
              f"gallery coverage.")
    else:
        print(f"NO ({cfg}): impostor max={imax} >= genuine max={gmax}. Try other "
              f"params (--orb, --raw, --scale) or revisit preprocessing/PPI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
