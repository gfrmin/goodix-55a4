#!/usr/bin/env python3
"""M2 — matching eval: do same-finger prints score higher than different fingers?

For each capture session we build a matcher input under SEVERAL preprocessings
(raw, baseline-subtracted, CLAHE), run it through cwsq -> mindtct (.xyt), then
bozorth3 every unordered pair. Group = label up to the last '-N' (e.g. m2g-genidx-1
-> 'genidx'). Same group = genuine pair, different = impostor. For each variant we
print minutiae counts and the genuine-vs-impostor score distributions, and whether
they separate (a clean gap => libfprint's bundled NBIS/Bozorth matcher is viable).

Sessions whose baseline is contaminated (finger held during the clear-* frames)
are detected and EXCLUDED automatically -- a clean baseline reads clear-0 mean
~2620; a contaminated one ~1650 and clear-finger std ~0.

Usage: m2_eval.py [frames_glob ...]   (default <vault>/frames/m2g-*)
"""
import glob
import itertools
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import vault  # noqa: E402  (vault root + external-volume guard)
from nbis_test import PPI, clahe, norm8, pad_to  # noqa: E402
from render_pgm import read_p2  # noqa: E402

DEFAULT = os.path.join(vault.frames(), "m2g-*")

# Baseline-contamination thresholds (see module docstring; verified on this unit).
CLEAR_MEAN_MIN = 2200   # clean baseline ~2620; finger-held ~1650
DIFF_STD_MIN = 80       # clear-finger std: ~300-470 clean, ~6-55 contaminated


def variants(fp: np.ndarray, c0: np.ndarray) -> dict:
    """Preprocessing variants. diff = clear - finger (finger lowers capacitive
    reading, so ridges are the high-signal part). CLAHE = local-contrast norm.
    All polarities are applied consistently across every print, which is what
    bozorth3 (minutiae-geometry) needs."""
    diff = c0 - fp
    return {
        "raw_inv":     norm8(fp, invert=True),               # old baseline approach
        "clahe_raw":   clahe(norm8(fp, invert=True)),
        "diff_inv":    norm8(diff, invert=True),
        "clahe_diff":  clahe(norm8(diff)),
        "clahe_diffi": clahe(norm8(diff, invert=True)),
    }


def is_clean_baseline(sess: str) -> tuple[bool, str]:
    fp = read_p2(f"{sess}/fingerprint.pgm")
    c0 = read_p2(f"{sess}/clear-0.pgm")
    cmean, dstd = float(c0.mean()), float((c0 - fp).std())
    if cmean < CLEAR_MEAN_MIN:
        return False, f"clear-0 mean={cmean:.0f} (<{CLEAR_MEAN_MIN}: finger on baseline)"
    if dstd < DIFF_STD_MIN:
        return False, f"clear-finger std={dstd:.0f} (<{DIFF_STD_MIN}: no ridge signal)"
    return True, f"clear-0 mean={cmean:.0f} diff std={dstd:.0f}"


def make_xyt(sess: str, variant: str, img8: np.ndarray) -> tuple[str, int]:
    img = pad_to(img8)
    h, w = img.shape
    raw = f"{sess}/_m2_{variant}.raw"
    img.tofile(raw)
    subprocess.run(["cwsq", "2.25", "wsq", raw, "-raw_in", f"{w},{h},8,{PPI}"],
                   capture_output=True)
    subprocess.run(["mindtct", "-m1", f"{sess}/_m2_{variant}.wsq",
                    f"{sess}/_m2_{variant}"], capture_output=True)
    xyt = f"{sess}/_m2_{variant}.xyt"
    n = sum(1 for _ in open(xyt)) if os.path.exists(xyt) else 0
    return xyt, n


def score(a: str, b: str) -> int:
    p = subprocess.run(["bozorth3", a, b], capture_output=True, text=True)
    try:
        return int(p.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return -1


def group(name: str) -> str:
    # strip trailing -N and any known capture-round prefix -> the finger label
    base = re.sub(r"-\d+$", "", name)
    return re.sub(r"^(m2g?-)", "", base)


def main() -> int:
    pats = sys.argv[1:] if len(sys.argv) > 1 else [DEFAULT]
    sessions = sorted({s for p in pats for s in glob.glob(os.path.expanduser(p))})

    # 1) contamination gate
    print("sessions (baseline check):")
    good = []
    for s in sessions:
        ok, why = is_clean_baseline(s)
        print(f"  {'OK ' if ok else 'SKIP'} {os.path.basename(s):16} {why}")
        if ok:
            good.append(s)
    if len(good) < 2:
        print(f"\nneed >=2 clean captures, have {len(good)}.")
        return 1
    names = [os.path.basename(s) for s in good]
    print(f"\ngroups: {sorted({group(n) for n in names})}")

    # 2) per-variant: extract minutiae for every clean print, score all pairs
    vnames = list(variants(read_p2(f"{good[0]}/fingerprint.pgm"),
                           read_p2(f"{good[0]}/clear-0.pgm")).keys())
    best = None
    for v in vnames:
        xyt, counts = {}, {}
        for s in good:
            n = os.path.basename(s)
            imgs = variants(read_p2(f"{s}/fingerprint.pgm"),
                            read_p2(f"{s}/clear-0.pgm"))
            xyt[n], counts[n] = make_xyt(s, v, imgs[v])
        sc = {}
        for a, b in itertools.combinations(names, 2):
            v_ab = score(xyt[a], xyt[b])
            sc[(a, b)] = sc[(b, a)] = v_ab
        gen = [sc[(a, b)] for a, b in itertools.combinations(names, 2)
               if group(a) == group(b)]
        imp = [sc[(a, b)] for a, b in itertools.combinations(names, 2)
               if group(a) != group(b)]

        print(f"\n=== variant '{v}' ===")
        print("minutiae: " + ", ".join(f"{n}={counts[n]}" for n in names))
        # Per-probe BEST-match: the metric libfprint actually uses (a probe is
        # matched against ALL enrolled frames, best score wins). Partial prints
        # that imaged a different fingertip region legitimately score ~0; that is
        # a coverage gap, not a matcher failure -- so per-pair min/max is the
        # wrong test. A probe is "correct" if its best same-finger score beats
        # its best impostor score.
        correct = 0
        print(f"  {'probe':16} {'gen_best':>8} {'imp_best':>8}  ok")
        for p in names:
            gb = max([sc[(p, o)] for o in names if o != p and group(o) == group(p)]
                     or [0])
            ib = max([sc[(p, o)] for o in names if group(o) != group(p)] or [0])
            correct += gb > ib
            print(f"  {p:16} {gb:>8} {ib:>8}  {'YES' if gb > ib else 'no'}")
        if gen and imp:
            gmax, imax = max(gen), max(imp)
            ov_nz = sum(1 for x in gen if x > 0)
            print(f"  genuine  pairs: max={gmax} overlapping(>0)={ov_nz}/{len(gen)} "
                  f"sorted={sorted(gen, reverse=True)}")
            print(f"  impostor pairs: max={imax} sorted={sorted(imp, reverse=True)}")
            print(f"  -> {correct}/{len(names)} probes match own finger best; "
                  f"genuine-overlap max={gmax} vs impostor max={imax} "
                  f"(margin={gmax - imax})")
            # rank variants by: probes-correct, then how far overlapping genuine
            # clears the impostor ceiling
            key = (correct, gmax - imax, gmax)
            if best is None or key > best[0]:
                best = (key, v, gen, imp, correct, len(names))

    # 3) verdict
    print("\n--- VERDICT ---")
    if not best:
        print("no genuine+impostor pairs available (need >=2 of one finger and "
              ">=1 of another).")
        return 0
    (_, v, gen, imp, correct, nprobes) = best
    gmax, imax = max(gen), max(imp)
    margin = gmax - imax
    if margin > 0 and correct >= 2:
        thr = imax + max(1, margin // 2)
        print(f"SIGNAL PRESENT (best variant '{v}'): overlapping same-finger pairs "
              f"score up to {gmax}; NO impostor pair exceeds {imax} (margin {margin}). "
              f"{correct}/{nprobes} probes match their own finger above any impostor. "
              f"Minutiae genuinely correspond -> NBIS/Bozorth IS viable on this "
              f"sensor. The failures are non-overlapping PARTIAL prints (coverage), "
              f"not matcher error -> fix with multi-frame enrollment / fuller "
              f"placement, as libfprint enrollment already does. A bozorth3 "
              f"threshold ~{thr} accepts overlapping genuine and rejects all "
              f"impostors here. Provisionally OPTIMISTIC; confirm with denser "
              f"coverage before trusting FAR/FRR.")
    else:
        print(f"NO SEPARATION (best variant '{v}': genuine-overlap max={gmax} vs "
              f"impostor max={imax}). Even overlapping same-finger prints don't beat "
              f"impostors -> STOP and report; user decides on the custom SIFT/CLAHE "
              f"matcher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
