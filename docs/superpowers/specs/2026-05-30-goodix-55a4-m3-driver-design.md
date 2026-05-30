# Design: M3 — Goodix 27c6:55a4 libfprint driver (validate-then-clean)

**Date:** 2026-05-30
**Status:** Approved design, pre-implementation.
**Supersedes:** the M3 bullet in
[`2026-05-29-goodix-55a4-driver-design.md`](2026-05-29-goodix-55a4-driver-design.md)
(which assumed an `FpImageDevice` + bundled NBIS/`bz3_threshold` driver). M2 disproved that
assumption — see below.

## Context — why M3's architecture changed

The original spec's M3 plan was "implement `55a4` as an `FpImageDevice`; libfprint's bundled
NBIS/Bozorth3 matcher does recognition (`bz3_threshold`)." **M2 killed that path.** A proper
gallery FAR/FRR (17-frame gallery, held-out probes, 10 diverse impostors) showed NBIS
minutiae do **not** separate fingers on this 88×108 partial sensor (impostor bozorth3 max
≥ genuine max; EER ~40–50%). The anticipated fallback won instead: **SIFT + CLAHE + RANSAC
geometric verification** separates cleanly (genuine 25–43 inliers, impostors ≤4; FAR=0 at a
low threshold). See `docs/protocol/deltas.md` (2026-05-30) and the `project-status` memory.

Consequence for the driver: recognition needs a SIFT-class matcher, which **libfprint's stock
core does not provide**. M3 must carry that matcher. This spec is how.

## What the M3 research found (primary sources)

All verified by reading source via `gh api` on 2026-05-30. Key repos/branches:

1. **A C driver for our exact device already exists.**
   `TheWeirdDev/libfprint@55b4-experimental` →
   `libfprint/drivers/goodixtls/goodix55x4.{c,h}`. Its `id_table` literally lists
   `{.vid=0x27c6, .pid=0x55a4}` (with `0x55b4`). 108×88 image, `scan_type = PRESS`,
   `nr_enroll_stages = 10`. Constants to reconcile against our Python:
   `GOODIX_55X4_FIRMWARE_VERSION = "GF3268_RTSEC_APP_10041"`,
   `GOODIX_55X4_PSK_FLAGS = 0xbb020007`, `EP_IN = 0x82`, `EP_OUT = 0x01`.

2. **The matcher is solved, convergently with our M2 work.** The community's **SIGFM**
   (“Sigma Fingerprint Matcher”, `goodix-fp-linux-dev/sigfm`, vendored as `libfprint/sigfm/`)
   is the *same* recipe M2 independently reached: `cv::SIFT::create()` +
   `cv::BFMatcher` knn + Lowe ratio 0.75 + a geometric-consistency filter (`min_match=5`,
   `length_match=angle_match=0.05`), score where genuine ≫ impostor. It is a clean
   **LGPL-2.1+ C-ABI** library (`sigfm.h`):
   ```c
   SigfmImgInfo* sigfm_extract(const SigfmPix* pix, int width, int height);
   int           sigfm_match_score(SigfmImgInfo* frame, SigfmImgInfo* enrolled); // <0 err, 0 reject
   unsigned char* sigfm_serialize_binary(SigfmImgInfo* info, int* outlen);
   SigfmImgInfo*  sigfm_deserialize_binary(const unsigned char* bytes, int len);
   int           sigfm_keypoints_count(SigfmImgInfo* info);   // quality gate
   ```
   `SigfmImgInfo = { std::vector<cv::KeyPoint> keypoints; cv::Mat descriptors; }` — exactly the
   SIFT features our Python computes. **We vendor SIGFM; we do not write SIFT-in-C.**
   Caveat: the 55b4 build of `sigfm.cpp` **omits CLAHE**; M2 found CLAHE matters (later forks,
   e.g. `AbdulRehmanMehar`, re-added CLAHE clip 2.0–3.0).

3. **No firmware flashing — our hard constraint is satisfiable in C.** The entire goodixtls C
   driver has **no firmware-write code**: `goodix.c` only *reads* the version
   (`goodix_send/receive_firmware_version`); the activate SSM does a read-only version
   **check** (`strcmp`, `fpi_ssm_mark_failed` on mismatch) then uploads **volatile** MCU
   config (`goodix_send_upload_config_mcu(goodix_55x4_config, …)`). This is our no-flash path.
   The only snag: the version check is a strict `strcmp` against `GF3268_RTSEC_APP_10041`, but
   our unit reports a flaky/varying `GF32xx…_RTSEC_APP_10062` — so we relax the gate to a
   tolerant family match (as our Python `capture_55x4cfg.py` already does).

4. **TLS-PSK (“GTLS”) is already implemented in C** (`goodixtls.c`, OpenSSL, TLS 1.2,
   `SSL_CTX_set_psk_server_callback`), driven by an `FpiSsm` in `goodix.c` with
   `goodix_tls_read_image` for per-frame decrypted USB image reads — the same protocol
   generation as the 55x4 devices, reusable as reference.

5. **The fork integrates SIGFM by patching libfprint *core*** (a new `FPI_DEVICE_ALGO_SIGFM`
   in `fpi-image-device.c`/`fpi-print.c`/`fp-image.c`/`fp-print.c`), built on libfprint
   **1.94.6** (system is 1.94.10), last touched 2024-11, marked "unstable." Deps:
   `openssl`, `opencv4`, `doctest`, a C++ compiler, `meson`/`ninja`.
   A **stock-core alternative** exists: `buxel/libfprint` (and `AndyHazz/goodix53x5`,
   `berkekbgz/gdix51c0`) call SIGFM **from the driver's own enroll/verify**, leaving core
   untouched. `buxel/libfprint-27c6-5110` (a TLS-image sibling) even ships an
   `analysis/09-upstream-porting-plan.md` — our closest precedent for a clean driver.

## Strategy — validate, then clean

**3a — Validate (throwaway, hardware de-risk).** Build `TheWeirdDev@55b4-experimental`
(relaxed firmware gate) and run it against our 55a4 on the **no-flash** path. The only goal
is to confirm the **C TLS/USB capture works on *this* unit**; using a known-configured
reference implementation isolates "does the C protocol work on our hardware" from "did I port
it correctly." **Gate: one frame captured in C.** (If a build proves cheap I may peek at
enroll/verify, but capture is the gate.) If the 1.94.6 tree fights the build, we have learned
the real blockers cheaply, and our already-working **Python** capture remains the fallback
proof — then we proceed to 3b regardless.

**3b — Clean (the deliverable).** A driver on **current/stock libfprint** with vendored SIGFM
and our CLAHE pipeline. **Gate: `fprintd-enroll` + `fprintd-verify` work via the driver, with
sane held-out FAR/FRR.** No PAM (that is M4).

## The load-bearing architecture decision

The clean driver must run on **stock libfprint core** (unpatched) so it loads in the
installed `fprintd` and is upstreamable. That forces one choice:

> On stock core, an **`FpImageDevice` is locked to NBIS/Bozorth matching** — the base class
> owns `enroll`/`verify` and runs `fp_image_detect_minutiae` + bozorth3. A subclass cannot
> substitute SIFT.

Therefore the clean driver is a **custom `FpDevice`** (not `FpImageDevice`) that performs its
own capture + enroll + verify + identify and calls SIGFM directly, storing templates as an
opaque blob in the `FpPrint`. This is the deliberate inverse of TheWeirdDev's fork (which got
SIGFM by patching core). **We do not fork core.** Precedent: the driver-local-SIGFM forks in
finding #5.

## Stage 3b — component design

Each unit has one purpose and a defined interface; they are testable in isolation.

| Unit | Responsibility | Source / approach | Depends on |
|---|---|---|---|
| **Transport** | USB enumerate, no-flash init, PSK preset/read, MCU config upload, FDT calibration, encrypted image read | Port the proven Python (`driver_5503` init/PSK, `driver_55x4` config/FDT/88×108) into C, using LGPL `goodix.c`/`goodixtls.c` as the C reference (`FpiSsm`, `FpiUsbTransfer`, OpenSSL TLS-PSK) | libusb (via libfprint), OpenSSL |
| **Preprocess** | raw frame → matcher-ready 8-bit image | Our M2 pipeline: `clear−finger` baseline subtraction → percentile-normalize → **CLAHE** → uint8 buffer | numpy-equivalent in C; CLAHE via OpenCV or in-driver |
| **Matcher** | image → features; features × features → score; serialize | Vendored **`libsigfm`** (LGPL): `sigfm_extract` / `sigfm_match_score` / `sigfm_serialize_binary` / `sigfm_keypoints_count` | OpenCV4 |
| **libfprint glue** | be an `FpDevice` fprintd can drive | `FpDevice` subclass; vfuncs `probe/open/close/enroll/verify/identify/cancel(/suspend)`; capture SSM; `id_table {0x27c6,0x55a4}` | libfprint, GLib |
| **Template store** | enrolled finger ↔ on-disk template | enroll collects N tiled frames → array of `sigfm_serialize_binary` blobs → `FpPrint` (`fpi_print_set_type` RAW + GVariant); fprintd persists | GLib/GVariant |

### Data flow

- **Enroll** (`nr_enroll_stages ≈ 12–15`, default, adjustable): for each stage — capture →
  preprocess → `sigfm_extract` → quality-gate (`sigfm_keypoints_count` + our clean-baseline /
  coverage gate) → on pass, `report_enroll_progress` and append the serialized blob to the
  print's gallery; **retry on reject**. The result is a multi-frame `FpPrint` that *tiles* the
  fingertip (M2: a single touch covers ~¼ fingertip, so the template must span more than any
  one auth capture; the lone M2 FRR miss was an un-enrolled region).
- **Verify / identify**: capture probe → preprocess → `sigfm_extract` → `sigfm_match_score`
  vs every enrolled blob → accept if **best ≥ T**. `T` is **recalibrated on our hardware**
  (SIGFM's score scale ≠ our RANSAC inlier count; TheWeirdDev used 72, our inlier metric used
  ~5 — neither transfers blindly). Target: FAR=0 across our diverse impostor corpus, low FRR.

### Enrollment & matching policy (from M2)

Tiled enroll with **spoken coverage guidance** — center, tip, base, left/right edges, slight
rolls. **Power-button caveat holds: gentle taps only, never a hard press** (the pad is the
power button). Quality-gate and retry rather than accept a poor frame.

## Build / test / integration boundaries

- **Deps:** install `meson` + `ninja` (currently missing); `openssl`, `opencv4`, `doctest`
  present-or-installable; `gcc`/`clang` present.
- **Non-destructive:** build to an **isolated prefix** and run a private
  `fprintd`/`fprintd-enroll`/`fprintd-verify` against it for M3. **Do not touch the system
  `/usr` libfprint or `/etc/pam.d`** — system install + PAM are M4. Fully reversible.
- **Biometric hygiene:** test enrollments live in the isolated fprintd store under the vault
  (`$GOODIX_VAULT/`), **never** the repo; no captures/templates committed. Set up
  `vendor/libfprint` as a forked submodule per `vendor/README.md`; the clean driver + vendored
  `sigfm` live on a branch there.
- **Licensing:** SIGFM and the goodixtls driver are **LGPL-2.1+** — compatible with libfprint
  (LGPL-2.1+). Attribution preserved on any lifted code.

## Constants to reconcile (implementation checklist, not blockers)

- Firmware-version gate: relax `strcmp("GF3268_RTSEC_APP_10041")` → tolerant `GF32xx…` family
  match (our unit reports `…_10062`, model digit varies per read).
- `GOODIX_55X4_PSK_FLAGS = 0xbb020007` and the PSK bytes vs our Python `driver_5503`/
  `driver_55x4` values.
- Image orientation: their **108×88** vs our **88×108** — confirm width/height convention.
- Endpoints `EP_IN 0x82` / `EP_OUT 0x01` vs our capture.
- SIGFM score threshold: recalibrate on our corpus.

## Risks & gates

- **Old-fork build age (3a):** time-boxed; capture-only gate; fall back to the working Python
  capture proof and proceed to 3b if the fork won't build.
- **Stock-core custom `FpDevice`:** we own enroll/verify/storage (more C) — accepted cost of
  not forking core; mitigated by the `buxel` precedent.
- **CLAHE:** keep it (M2 evidence) even though SIGFM-55b4 dropped it; verify it helps on our
  hardware, not just our offline corpus.
- **Unstable upstream:** treat community code as reference, not gospel; re-validate every
  borrowed constant against our captures.

## Verification (M3 done criteria)

- **3a:** a frame captured off `27c6:55a4` by the *C* driver on the no-flash path.
- **3b:** `fprintd-enroll` succeeds and `fprintd-verify` matches your finger via the new
  driver, on the isolated build; held-out FAR/FRR sane (FAR=0 across diverse impostors, low
  FRR). Password unaffected; no `/etc/pam.d` changes.

## Non-goals (this milestone)

- PAM / login / `sudo` integration (M4).
- System-wide install over distro libfprint (M4 decision).
- Upstream PRs / other Goodix PIDs (M5).
- Driving FRR to a specific number — M2's denser-gallery work is a parallel track, folded in
  opportunistically via the tiled-enroll policy.

## Sources

- TheWeirdDev/libfprint @ `55b4-experimental` — `libfprint/drivers/goodixtls/goodix55x4.{c,h}`,
  `goodix.c`, `goodixtls.c`; core `fpi-image-device.c`, `fpi-print.c`, `fp-image.c`,
  `fp-print.c` (SIGFM core integration). https://github.com/TheWeirdDev/libfprint
- goodix-fp-linux-dev/sigfm — the SIGFM matcher (LGPL-2.1+). https://github.com/goodix-fp-linux-dev/sigfm
- goodix-fp-linux-dev/libfprint — branches `goodixtls`, `sigfm`, `0x00002a/libfprint-sigfm`.
  https://github.com/goodix-fp-linux-dev/libfprint
- buxel/libfprint-27c6-5110 — driver-local SIGFM + upstream-porting analysis (stock-core
  precedent). https://github.com/buxel/libfprint-27c6-5110
- d-k-bo, "Install experimental driver for the goodix 55b4 sensor" (practical recipe + flash
  warning). https://gist.github.com/d-k-bo/15e53eab53e2845e97746f5f8661b224
- libfprint Writing Drivers — https://fprint.freedesktop.org/libfprint-dev/pt03.html
- Our M2 work: `research/sift_match.py`, `research/nbis_test.py` (CLAHE/`norm8`),
  `research/measure_ppi.py` (~600 PPI), `research/capture_55x4cfg.py` (no-flash engine);
  `docs/protocol/deltas.md` (2026-05-30); `project-status` memory.
