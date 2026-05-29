#!/usr/bin/env python3
"""M2 — matching eval: do same-finger prints score higher than different fingers?

For each m2-* capture: raw frame -> norm8(invert) -> pad -> cwsq(WSQ) -> mindtct
(.xyt). Then bozorth3 every unordered pair. Group = label up to the last '-N'
(e.g. m2-genA-1 -> 'genA'). Same group = genuine pair, different = impostor.
Prints minutiae counts, the genuine vs impostor score distributions, and whether
they separate (a clean gap => libfprint's bundled matcher is trustworthy).

Usage: m2_eval.py [frames_glob]   (default $GOODIX_VAULT/frames/m2-*)
"""
import glob
import itertools
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from nbis_test import PPI, norm8, pad_to  # noqa: E402
from render_pgm import read_p2  # noqa: E402

DEFAULT = os.path.expanduser("$GOODIX_VAULT/frames/m2-*")


def make_xyt(sess: str) -> tuple[str, int]:
    fp = read_p2(f"{sess}/fingerprint.pgm")
    img = pad_to(norm8(fp, invert=True))
    h, w = img.shape
    raw = f"{sess}/_m2.raw"
    img.tofile(raw)
    subprocess.run(["cwsq", "2.25", "wsq", raw, "-raw_in", f"{w},{h},8,{PPI}"],
                   capture_output=True)
    subprocess.run(["mindtct", "-m1", f"{sess}/_m2.wsq", f"{sess}/_m2"],
                   capture_output=True)
    xyt = f"{sess}/_m2.xyt"
    n = sum(1 for _ in open(xyt)) if os.path.exists(xyt) else 0
    return xyt, n


def score(a: str, b: str) -> int:
    p = subprocess.run(["bozorth3", a, b], capture_output=True, text=True)
    try:
        return int(p.stdout.strip().split()[0])
    except (ValueError, IndexError):
        return -1


def group(name: str) -> str:
    return re.sub(r"-\d+$", "", name).replace("m2-", "")


def main() -> int:
    pat = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    sessions = sorted(glob.glob(pat))
    if len(sessions) < 2:
        print(f"need >=2 captures, found {len(sessions)} at {pat}")
        return 1

    xyt, counts = {}, {}
    print("minutiae per print:")
    for s in sessions:
        name = os.path.basename(s)
        xyt[name], counts[name] = make_xyt(s)
        print(f"  {name:14} group={group(name):6} minutiae={counts[name]}")

    gen, imp = [], []
    print("\npairwise bozorth3 scores (higher = more similar):")
    for a, b in itertools.combinations(sorted(xyt), 2):
        sc = score(xyt[a], xyt[b])
        kind = "GENUINE " if group(a) == group(b) else "impostor"
        (gen if group(a) == group(b) else imp).append(sc)
        print(f"  {kind}  {a:14} vs {b:14} : {sc}")

    print("\n--- summary ---")
    if gen:
        print(f"genuine  (same finger): n={len(gen)} min={min(gen)} "
              f"max={max(gen)} scores={sorted(gen, reverse=True)}")
    if imp:
        print(f"impostor (diff finger): n={len(imp)} min={min(imp)} "
              f"max={max(imp)} scores={sorted(imp, reverse=True)}")
    if gen and imp:
        gap = min(gen) - max(imp)
        if gap > 0:
            print(f"\nVERDICT: SEPARATES — every genuine pair ({min(gen)}) scores above "
                  f"every impostor pair ({max(imp)}); gap={gap}. A threshold in "
                  f"({max(imp)}, {min(gen)}] cleanly distinguishes you. NBIS/Bozorth "
                  f"matching is viable -> proceed toward fprintd/PAM.")
        else:
            print(f"\nVERDICT: OVERLAP — genuine min={min(gen)} <= impostor max={max(imp)}. "
                  f"Need better/more captures or preprocessing before trusting matching.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
