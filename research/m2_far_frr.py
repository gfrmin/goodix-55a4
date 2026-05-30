#!/usr/bin/env python3
"""M2 reliability — gallery-based FAR/FRR with a bozorth3 threshold sweep.

This is how a real fingerprint system is evaluated (and how libfprint matches):
a finger is enrolled as a GALLERY of frames; a verification probe is scored
against EVERY gallery frame and the BEST (max) score decides. We then sweep the
decision threshold T and report:
  FRR(T) = fraction of genuine probes whose best gallery score < T   (locked out)
  FAR(T) = fraction of impostor probes whose best gallery score >= T (false accept)

Roles come from the session label:
  *imp*           -> impostor probe        (right-middle legacy impmid/impB too)
  *prb*           -> genuine probe (held out, measures FRR)
  *gal* / *gen*   -> enrollment gallery     (right-index legacy genidx/genA too)
Contaminated sessions (finger held during the clear-* baseline) are auto-skipped.

Matcher = the proven winner: clear-finger -> CLAHE -> invert -> pad -> cwsq ->
mindtct -> bozorth3 (variant 'clahe_diffi' in m2_eval).

Usage: m2_far_frr.py [glob ...]
       (default: all m2c-*, m2g-*, m2-* under the vault)
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from m2_eval import is_clean_baseline, make_xyt, score, variants  # noqa: E402
from render_pgm import read_p2  # noqa: E402

VARIANT = "clahe_diffi"
VAULT = os.path.expanduser("$GOODIX_VAULT/frames")
DEFAULT = [f"{VAULT}/m2c-*", f"{VAULT}/m2g-*", f"{VAULT}/m2-*"]


def role(name: str) -> str:
    n = name.lower()
    if "imp" in n:
        return "impostor"
    if "prb" in n:
        return "probe"
    if "gal" in n or "gen" in n:
        return "gallery"
    return "gallery"  # default: treat unknown genuine-looking labels as gallery


def best_vs_gallery(probe_xyt: str, gallery_xyt: list[str]) -> int:
    return max((score(probe_xyt, g) for g in gallery_xyt), default=-1)


def main() -> int:
    pats = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT
    sessions = sorted({s for p in pats for s in glob.glob(os.path.expanduser(p))})

    roles, xyt = {}, {}
    print("sessions:")
    for s in sessions:
        name = os.path.basename(s)
        ok, why = is_clean_baseline(s)
        if not ok:
            print(f"  SKIP {name:18} {why}")
            continue
        imgs = variants(read_p2(f"{s}/fingerprint.pgm"), read_p2(f"{s}/clear-0.pgm"))
        x, n = make_xyt(s, VARIANT, imgs[VARIANT])
        r = role(name)
        roles[name], xyt[name] = r, x
        print(f"  {r:8} {name:18} minutiae={n}")

    gallery = [xyt[n] for n in xyt if roles[n] == "gallery"]
    probes = [n for n in xyt if roles[n] == "probe"]
    impostors = [n for n in xyt if roles[n] == "impostor"]
    if not gallery or not probes or not impostors:
        print(f"\nneed gallery+probe+impostor; have gallery={len(gallery)} "
              f"probe={len(probes)} impostor={len(impostors)}.")
        print("(label genuine held-out probes with 'prb', impostors with 'imp'.)")
        return 1

    print(f"\ngallery={len(gallery)} frames | genuine probes={len(probes)} | "
          f"impostor probes={len(impostors)}  (variant '{VARIANT}', best-of-gallery)")

    print("\ngenuine probes (best gallery score):")
    gen = []
    for p in sorted(probes):
        sc = best_vs_gallery(xyt[p], gallery)
        gen.append(sc)
        print(f"  {p:18} -> {sc}")
    print("impostor probes (best gallery score):")
    imp = []
    for p in sorted(impostors):
        sc = best_vs_gallery(xyt[p], gallery)
        imp.append(sc)
        print(f"  {p:18} -> {sc}")

    # threshold sweep
    hi = max(gen + imp + [1])
    print(f"\nthreshold sweep (accept if best gallery score >= T):")
    print(f"  {'T':>3} {'FRR':>7} {'FAR':>7}   (FRR=genuine locked out, FAR=impostor accepted)")
    far0 = None
    rows = []
    for T in range(0, hi + 2):
        frr = sum(1 for g in gen if g < T) / len(gen)
        far = sum(1 for i in imp if i >= T) / len(imp)
        rows.append((T, frr, far))
        if far == 0 and far0 is None:
            far0 = (T, frr)
    for T, frr, far in rows:
        mark = "  <- FAR=0" if (far0 and T == far0[0]) else ""
        print(f"  {T:>3} {frr*100:6.0f}% {far*100:6.0f}%{mark}")

    print("\n--- VERDICT ---")
    gmin, gmax = min(gen), max(gen)
    imax = max(imp)
    if far0:
        T, frr = far0
        print(f"At threshold T={T}: FAR=0 (no impostor of {len(imp)} reaches {T}; "
              f"impostor max={imax}) and FRR={frr*100:.0f}% "
              f"({sum(1 for g in gen if g < T)}/{len(gen)} genuine probes locked out). "
              f"Genuine probe scores={sorted(gen, reverse=True)}.")
        if frr <= 0.10:
            print(f"RELIABLE: a bz3_threshold ~{T} gives zero false-accepts here and "
                  f"FRR<=10% -> NBIS/Bozorth + multi-frame gallery is viable; set the "
                  f"M3 driver's bz3_threshold near {T} (default 40 is far too high for "
                  f"this partial sensor). Confirm on more data over time.")
        else:
            print(f"PARTIAL: zero false-accepts achievable, but FRR={frr*100:.0f}% is "
                  f"high -> the held-out probes hit fingertip regions the gallery "
                  f"doesn't cover. Improve gallery coverage (more tiled enroll frames) "
                  f"or accept a lower T with some FAR risk. Genuine min={gmin}.")
    else:
        print(f"NO CLEAN THRESHOLD: some impostor scores as high as genuine "
              f"(impostor max={imax}, genuine min={gmin}). More/better gallery "
              f"coverage or preprocessing needed before trusting matching.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
