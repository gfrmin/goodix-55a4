# Design: Linux driver for Goodix 27c6:55a4 fingerprint reader

**Date:** 2026-05-29
**Status:** Approved design, pre-implementation.

## Context

This laptop has a **Goodix `27c6:55a4`** USB fingerprint reader that does not work on
Linux. The reader has no official Linux driver, is not in upstream `libfprint`, and is
not covered by the proprietary `libfprint-2-tod1-goodix` TOD packages (which target
`550a` etc.). [linux-hardware.org](https://linux-hardware.org/?id=usb:27c6-55a4) reports
no working driver across 773 logged systems.

The protocol for the `55x4` family **has** been reverse-engineered by the
[goodix-fp-linux-dev](https://github.com/goodix-fp-linux-dev) community —
[`goodix-fp-dump`](https://github.com/goodix-fp-linux-dev/goodix-fp-dump) ships
`run_55a4.py` / `driver_55x4.py` that perform USB init, the GTLS (TLS-PSK) handshake,
config upload, FDT calibration, and **raw image capture**. So this project does not start
from a Windows-driver teardown; it builds on existing work to reach a usable,
fprintd-integrated driver.

The intended outcome: enroll a fingerprint and use it for login / `sudo`, with the
password retained as a fallback.

## Goal & non-goals

**Goal:** `27c6:55a4` usable for authentication via the standard `fprintd` + `pam_fprintd`
stack, built incrementally to a full fingerprint-login finish line.

**Non-goals (v0):** supporting other Goodix models; a general AI-assisted RE framework
(harvested opportunistically as `docs/recipe.md`, not built); replacing the password as
the sole auth factor.

## The core problem

The sensor returns a **raw capacitive image**, not a match decision. A working driver is
three stacked jobs:

1. **Capture** — USB + GTLS handshake → raw frames. *Mostly solved upstream for `55x4`.*
2. **Recognition** — image → accept/reject. *The genuinely open part.*
3. **Integration** — feed accept/reject into `fprintd` → PAM.

### The decisive fork (test this first)

libfprint has two driver kinds. An **`FpImageDevice`** driver only has to produce a clean
grayscale image; **libfprint's core then does minutiae extraction + matching** (bundled
NBIS / Bozorth3 — that is what the driver struct's `bz3_threshold` field configures), and
`fprintd` owns enrollment storage + PAM.
([libfprint Writing Drivers](https://fprint.freedesktop.org/libfprint-dev/pt03.html))

So recognition has two possible outcomes, and **v0's first real experiment is to find out
which one we're in:**

- **Optimistic** — our image is good enough that NBIS extracts usable minutiae →
  we write ~no matching code; libfprint + fprintd do the rest.
- **Fallback** — Goodix sensors are small-area and noisy (the reason the goodixtls effort
  used a custom **SIFT + CLAHE** matcher instead of NBIS). If NBIS chokes, we either
  (a) preprocess images until NBIS copes, or (b) implement custom matching.

This fork is why we prototype in Python first: the test ("does NBIS find minutiae in our
captures?") is cheap and decides how big v0 is.

## Architecture — Hybrid (option C)

Prototype + tune recognition in **Python** (fast loop, where AI-assisted tuning plugs in),
then **port the proven approach into the C libfprint driver** for the final PAM
integration. The port happens *after* the hard problem is solved, when the algorithm is
frozen.

Rejected alternatives:
- **Pure C / libfprint from day one** — forces the slow C/meson loop onto the part we
  iterate most (recognition).
- **Pure Python + custom PAM glue** — reinvents enrollment + PAM that fprintd gives free,
  and isn't upstreamable.

### Layer boundaries (mirror the upstream split)

The ecosystem already separates RE/capture (`goodix-fp-dump`, Python) from the driver
(`libfprint`, C). This repo is the **workspace** that orchestrates both via forked
submodules and holds the research + dataset that belong to neither upstream.

```
~/git/goodix-55a4/
├── docs/
│   ├── superpowers/specs/      this spec
│   ├── protocol/               confirmed packet field tables, golden init logs
│   └── recipe.md               generalizable "how to RE a reader" writeup (AI-RE seed)
├── captures/                   .pcapng + Lua dissectors            (gitignored)
├── research/                   Python: capture, NBIS test, preprocessing, matcher fallback
├── eval/                       held-out test set, FAR/FRR scoring
├── data/                       raw images + templates              (HARD gitignored)
├── vendor/
│   ├── goodix-fp-dump/         submodule → your fork (capture + protocol fixes)
│   └── libfprint/              submodule → your fork (the C FpImageDevice driver)
└── README / CLAUDE.md
```

Contributions flow **out** from the forks via PR when solid; nothing leaks upstream early.

## Milestones (incremental, each gates the next)

- **M0 — Prove capture.** Run `goodix-fp-dump` `run_55a4.py` against *this* unit; pull and
  visualize one raw frame. De-risks everything. *Read-only-ish: writes zero-PSK + reads
  frames; reversible; no PAM changes.*
- **M1 — Recognition fork test.** Capture several frames; run libfprint's minutiae
  extractor / NBIS on them. **Decide optimistic vs fallback.** Build preprocessing (CLAHE)
  as needed. Output: a Python tool that enrolls + matches your finger, with measured
  quality.
- **M2 — Tune + evaluate.** Build `eval/` held-out set; tune thresholds; report FAR/FRR.
  Gate: numbers sane enough to trust.
- **M3 — libfprint driver.** Implement `55a4` as an `FpImageDevice` in the `libfprint`
  fork (`FpiSsm` state machine + `FpiUsbTransfer`, frame-assembly helpers, supported-device
  table entry for `27c6:55a4`). `fprintd-enroll` / `fprintd-verify` work.
- **M4 — PAM.** Wire `pam_fprintd` into the relevant `/etc/pam.d` stacks as **`sufficient`**
  (password fallback intact). Fingerprint login / `sudo` works.
- **M5 — Robustness / upstream.** Stabilize, document, PR to `goodix-fp-linux-dev`.

## Security & threat model

- Match-on-host + known/zero PSK → raw prints live in host RAM and spoofing is easier than
  match-on-chip. **This is a convenience factor, not a security boundary.**
- PAM: fingerprint `sufficient`, **password always a working fallback**; never the sole
  factor. Not for full-disk or as the only login secret.
- **Biometric-data hygiene:** captures + templates are biometric data → `data/` and
  `captures/` hard-gitignored, never in any history; templates live where fprintd stores
  them (`/var/lib/fprint`, `0700`).
- **Measure before trusting:** no PAM changes until held-out FAR/FRR look sane.

## Verification

- **M0:** a saved raw image file you can open and see ridge-like structure.
- **M1–M2:** a script that, on a held-out set, accepts your enrolled finger and rejects
  others; FAR/FRR reported.
- **M3:** `fprintd-enroll` succeeds and `fprintd-verify` matches via the new driver.
- **M4:** `sudo` and the lock screen accept your fingerprint; the same actions still
  accept your password.

## Open questions

1. Does NBIS extract usable minutiae from `55a4` images, or is custom matching required?
   *(M1 answers this — highest priority.)*
2. Does `run_55a4.py` complete the handshake on this exact firmware revision, or need
   tweaks? *(M0.)*
3. C port target: contribute to `goodix-fp-linux-dev/libfprint`'s existing `55x4` work, or
   a clean driver? *(Decide at M3 after reading their tree.)*

## Sources

- libfprint Writing Drivers — https://fprint.freedesktop.org/libfprint-dev/pt03.html
- libfprint Image driver structures — https://fprint.freedesktop.org/libfprint-stable/libfprint-Image-driver-structures.html
- goodix-fp-dump — https://github.com/goodix-fp-linux-dev/goodix-fp-dump
- goodix-fp-linux-dev/libfprint — https://github.com/goodix-fp-linux-dev/libfprint
- Neodyme, "Reversing a Fingerprint Reader Protocol" — https://neodyme.io/en/blog/fingerprint_reversing/
- "Fixing my fingerprint reader on Linux by writing a driver for it" — https://infinytum.co/fixing-my-fingerprint-reader-on-linux-by-writing-a-driver-for-it/
- OpenRazer, Reverse Engineering USB Protocol — https://github.com/openrazer/openrazer/wiki/Reverse-Engineering-USB-Protocol
- linux-hardware.org 27c6:55a4 — https://linux-hardware.org/?id=usb:27c6-55a4
