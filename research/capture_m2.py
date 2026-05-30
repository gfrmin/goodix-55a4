#!/usr/bin/env python3
"""M2 capture with a quality gate, around the proven no-flash recipe.

Delegates init / firmware-guard / PSK / 88x108 capture to capture_55x4cfg (the
M0-working recipe: 5503 init + 55x4 image config + in-process TLS-PSK, never a
flash), then GATES the result so we never again bank a contaminated or
low-coverage frame:

  - clean baseline (finger OFF during clear-* frames)  -> clear-0 mean >= 2200
    (a finger held during calibration drags it down to ~1650)
  - real ridge signal                                  -> (clear-finger) std >= 80
  - substantial print (not a corner tap)               -> coverage >= 30%
    (clean genuine prints cover ~70-83% of the array; contaminated ~0-2%)

PASS -> keep the session under frames/<label>. FAIL -> move it to frames/rejects/
(so it won't match the m2g-* eval glob) and tell the operator to redo the press.

Usage: cd vendor/goodix-fp-dump && sudo PYTHONPATH=$PWD \
       <repo>/.venv/bin/python \
       <repo>/research/capture_m2.py <label>
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
import capture_55x4cfg as eng  # noqa: E402  init/guard/PSK/session_dir/run_driver
from render_pgm import read_p2  # noqa: E402

CLEAR_MEAN_MIN = 2200
DIFF_STD_MIN = 80
COV_MIN = 30.0   # percent of array meaningfully pressed
COV_THR = 200    # capacitive drop (clear-finger) that counts as "pressed"


def gate(sess: str) -> tuple[bool, str, list[str]]:
    fp = read_p2(f"{sess}/fingerprint.pgm")
    c0 = read_p2(f"{sess}/clear-0.pgm")
    diff = c0 - fp
    cmean, dstd = float(c0.mean()), float(diff.std())
    cov = float((diff > COV_THR).mean() * 100)
    problems = []
    if cmean < CLEAR_MEAN_MIN:
        problems.append(f"baseline contaminated (clear-0 mean={cmean:.0f}; keep "
                        f"the finger OFF until 'Waiting for finger...')")
    if dstd < DIFF_STD_MIN:
        problems.append(f"no ridge signal (clear-finger std={dstd:.0f})")
    if cov < COV_MIN:
        problems.append(f"coverage too low ({cov:.1f}%; press flatter / more centred)")
    return (not problems), (f"clear0_mean={cmean:.0f} diff_std={dstd:.0f} "
                            f"coverage={cov:.1f}%"), problems


def main() -> int:
    rc = eng.main()  # session dir = argv[1] label; chdir's into it; runs capture
    if rc != 0:
        return rc

    sess = os.getcwd()
    ok, stats, problems = gate(sess)
    print(f"[m2] QUALITY: {stats}", flush=True)
    if ok:
        print(f"[m2] PASS — kept {os.path.basename(sess)}", flush=True)
        return 0

    rej = os.path.join(eng.VAULT, "rejects")
    os.makedirs(rej, exist_ok=True)
    os.chdir(eng.VAULT)  # leave the dir we're about to move
    dest = os.path.join(rej, os.path.basename(sess))
    shutil.move(sess, dest)
    print(f"[m2] RETRY — rejected: {'; '.join(problems)}. Moved to rejects/. "
          f"Please redo this press.", flush=True)
    return 10


if __name__ == "__main__":
    try:
        rc = main()
    except Exception as exc:  # noqa: BLE001  (let SystemExit/KeyboardInterrupt through)
        import traceback
        print("[m2] EXCEPTION:\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)),
            flush=True)
        rc = 1
    sys.exit(rc)
