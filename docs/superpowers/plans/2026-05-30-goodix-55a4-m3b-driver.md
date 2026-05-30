# Goodix 55a4 Phase 3b-driver — clean libfprint driver (SIGFM on stock core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `fprintd-enroll` then `fprintd-verify` succeed against the `27c6:55a4` via a new libfprint driver that matches with SIGFM, on an **isolated build** (no `/usr` overwrite, no PAM), no firmware flashing, with sane held-out FAR/FRR.

**Architecture:** A custom `FpDevice` (NOT `FpImageDevice`, which is locked to the NBIS matching M2 disproved) on **stock/unpatched** libfprint core. It captures no-flash (ported from the proven Python `driver_55x4.run_driver` + lifted LGPL C TLS/USB plumbing), preprocesses (`clear−finger` → norm → ×4 → CLAHE), and calls vendored SIGFM directly from its own enroll/verify/identify vfuncs, storing the multi-frame template as an opaque `FPI_PRINT_RAW` blob in `FpPrint`.

**Tech Stack:** C (libfprint / GLib / GObject / `FpiSsm`), C++ (SIGFM / OpenCV 4 — SIFT + CLAHE), `meson`+`ninja`, OpenSSL TLS-PSK, libusb (via libfprint). Reference: proven Python in `research/` + `vendor/goodix-fp-dump`; LGPL C in `vendor/libfprint-55b4-validate`.

**Spec:** [`../specs/2026-05-30-goodix-55a4-m3-driver-design.md`](../specs/2026-05-30-goodix-55a4-m3-driver-design.md)
**Parent plan (roadmap this expands):** [`2026-05-30-goodix-55a4-m3-driver.md`](2026-05-30-goodix-55a4-m3-driver.md) — Phase 3b-driver section.

---

## Context — why this, and why now

M0–M2 are done: no-flash capture works (Python), and SIFT+CLAHE matching (SIGFM in C) separates our corpus with FAR=0. Phases 0, 3a, 3b-offline are done. **3a banked the C transport as validated** (TLS handshake succeeded on our unit; the literal C frame was deferred to 3b). The parent roadmap intentionally stopped short of bite-sized steps "until 3a confirms the runtime facts." We now have those facts pinned to **ground truth** (the proven Python `driver_55x4.run_driver` + the LGPL C that handshook in 3a), so this plan is exact.

**Load-bearing decisions (made, with rationale):**
- **Custom `FpDevice`, NOT `FpImageDevice`.** Stock `FpImageDevice` is locked to NBIS/bozorth (M2 disproved that). We own enroll/verify/identify and call SIGFM directly. (Inverse of TheWeirdDev's fork, which patched core to add `FPI_DEVICE_ALGO_SIGFM`. We do **not** patch core → upstreamable, loads in stock fprintd.)
- **TLS approach = reuse the proven C** (user-chosen, 2026-05-30). Lift the LGPL goodix protocol/USB/TLS plumbing — the exact code that handshook with our unit in 3a — rather than rewrite. A cleanup pass is deferred to any M5 upstream PR.
- **Fork pin:** `vendor/libfprint` = upstream libfprint pinned to the tag matching the installed system (`1.94.10`, per `pkg-config --modversion libfprint-2`), so the same stock `fprintd` drives our build and M4 system-install is low-friction.
- **Orientation = 88×108 throughout** (our Python/M2/offline-harness convention). We are not an `FpImageDevice`, so libfprint never sees an `FpImage`; orientation only needs to be self-consistent with our proven preprocess + SIGFM. Ignore the C-fork's 108×88 transpose.
- **Keep CLAHE** (M2 evidence) — SIGFM has none internally; we pre-CLAHE before `sigfm_extract`.

## Hard rules (from CLAUDE.md / memory — apply to every task)

- **No firmware flashing, ever.** The ported activate path is `run_driver`'s pre-frame steps only (nop/reset/read-id/read-OTP/TLS/upload-config) — **none of `write_firmware`, `mcu_erase_app`, `update_firmware`, `write_psk` is on this path.** A grep-gate asserts this.
- **The fingerprint pad is the power button → gentle taps only, never press hard.** Every capture/enroll/verify prompt must say so.
- **Biometric data only under `$GOODIX_VAULT/`** (vault). Enroll templates go to an isolated fprintd store rooted in the vault, never the repo. `vendor/` and build dirs stay gitignored.
- **No `/etc/pam.d` changes, no overwriting `/usr` libfprint.** Isolated prefix + private fprintd only. (System install + PAM = M4.)
- **User `g` is colorblind** — diagnostics must not rely on color.
- **Log firmware/handshake/protocol findings** in `docs/protocol/deltas.md`.
- LGPL attribution preserved on every lifted file.

## Ground-truth facts this plan is built on (verified 2026-05-30)

Source of truth = `vendor/goodix-fp-dump/driver_55x4.py` (`run_driver`, proven on our unit via `research/capture_55x4cfg.py`) + the LGPL C in `vendor/libfprint-55b4-validate/libfprint/drivers/goodixtls/` (which handshook in 3a).

- **No-flash command order** (`driver_55x4.py:117-206`), opcodes:
  1. `nop` `0x00` (in `init_device`)
  2. `reset(True,False,20)` `0xa2` payload `01 14`
  3. `read_sensor_register(0x0000,4)` `0x82` (chip id `0x00a1`)
  4. `read_otp()` `0xa6` (**read only**)
  5. **TLS-PSK handshake** (`request_tls_connection` `0xd0` + record proxy)
  6. `upload_config_mcu(DEVICE_CONFIG)` `0x90` (**256-byte** volatile config — ground truth; the C-fork's 239-byte array is the constant to discard)
  7. clear-0: `mcu_switch_to_fdt_mode(<26B mode>)` `0x36` → `mcu_get_image(b"\x01\x00", FLAGS_TLS_DATA=0xb2)` `0x20` → read **14260** bytes, drop last 4, decode
  8. clear-1: `fdt_mode` → `mcu_switch_to_idle_mode(20)` `0x70` → `read_sensor_register(0x0082,2)` `0x82` → `get_image`
  9. finger: `fdt_mode` → `switch_to_sleep_mode(0x6c)` `0x92` → `mcu_switch_to_fdt_down(<26B down>)` `0x32` (**blocks until finger**) → `get_image`
- **TLS PSK = 32 zero bytes** (`driver_55x4.PSK`). `preset_psk_read(0xbb020007)` must return flags `0xbb020007` and `PMK_HASH = 81b8ff49…50361` (device-side verification only — NOT the TLS key). The C-fork's TLS callback correctly returns zeros.
- **Image decode** (`tool.decode_image`, identical to C-fork `decode_frame`): per 6 raw bytes → 4 12-bit px: `((c0&0xf)<<8)|c1`, `(c3<<4)|(c0>>4)`, `((c5&0xf)<<8)|c2`, `(c4<<4)|(c5>>4)`. 14256 image bytes → 9504 px → **88 W × 108 H**.
- **Firmware gate (relaxed):** accept regex `GF32[0-9]{2}_RTSEC_APP_100[0-9]{2}` (our unit reports `GF32xx_RTSEC_APP_10062`, model digit flaky). `driver_5503.VALID_FIRMWARE`.
- **Constants:** `EP_IN 0x82`, `EP_OUT 0x01`, interface `0`, `PSK_FLAGS 0xbb020007`, 26-byte FDT mode payload `0d 01 80 12 80 12 80 98 80 82 80 12 80 a0 80 99 80 7f 80 12 80 9f 80 93 80 7e`, 26-byte FDT down payload `0c 01 80 b0 80 c4 80 ba 80 a6 80 b7 80 c7 80 c0 80 aa 80 b4 80 c4 80 ba 80 a6`, sleep mode arg `0x6c`.
- **SIGFM C-ABI** (`research/sigfm_c/sigfm.hpp`, `SfmPix=unsigned char`): `SigfmImgInfo* sigfm_extract(const SfmPix* pix,int w,int h)`, `int sigfm_match_score(frame, enrolled)` (`<0` err, `0` reject, `>0` count; genuine ≫ imp), `unsigned char* sigfm_serialize_binary(info,int* outlen)`, `SigfmImgInfo* sigfm_deserialize_binary(const unsigned char*,int)`, `int sigfm_keypoints_count(info)`, `sigfm_copy_info`, `sigfm_free_info`. **No internal CLAHE.** Offline result: impostors all 0 (FAR=0), genuine up to 34781; ship threshold ~5 to start.
- **Storage/glue API** (verified in `libfprint/fpi-print.h`, `fpi-device.h`, `fp-print.c`): `FPI_PRINT_RAW` + `fpi_print_set_type(print, FPI_PRINT_RAW)`; opaque blob via the `"fpi-data"` **GVariant** property (`g_object_set/get(print,"fpi-data",…)`) — RAW prints serialize `fpi-data` so fprintd persists it. Async vfuncs + completers: `fpi_device_get_{enroll,verify,identify}_data`, `fpi_device_enroll_{progress,complete}`, `fpi_device_verify_{report,complete}`, `fpi_device_identify_{report,complete}`, `fpi_device_action_is_cancelled`, `fpi_device_get_usb_device`. Precedent non-image drivers to copy patterns from: `drivers/upekts.c` (RAW + fpi-data), `drivers/synaptics/synaptics.c`, `drivers/goodixmoc/goodix.c`, `drivers/elanmoc/elanmoc.c`.

## File structure (branch `goodix55a4-m3` inside the `vendor/libfprint` submodule)

| File | Responsibility |
|---|---|
| `libfprint/drivers/goodix55a4/goodix55a4.c` | Custom `FpDevice` subclass: `id_table {0x27c6,0x55a4}`, class_init wiring, vfuncs `probe/open/close/enroll/verify/identify/cancel`. Owns the activate + capture SSMs (or delegates to transport). |
| `libfprint/drivers/goodix55a4/goodix55a4.h` | All constants (above), the `FpiDeviceGoodix55a4` instance struct, internal prototypes. |
| `libfprint/drivers/goodix55a4/transport.c/.h` | USB + no-flash init + TLS-PSK + config upload + FDT + encrypted image read + 12-bit decode → raw `guint16[9504]`. Lifted from LGPL `goodix_proto.c`/`goodix.c`/`goodixtls.c`, stripped of FpImageDevice bits; sequence follows `run_driver`. |
| `libfprint/drivers/goodix55a4/preprocess.cpp/.h` | `clear−finger` → percentile-norm → ×4 cubic → CLAHE(2.0,8×8) → 8-bit buffer. OpenCV C++ (same as the offline harness). |
| `libfprint/drivers/goodix55a4/template.c/.h` | Pack/unpack the multi-frame SIGFM gallery ↔ one `"fpi-data"` GVariant (`aay`). |
| `libfprint/drivers/goodix55a4/sigfm/` | Vendored `sigfm.cpp/.hpp/.h`, `img-info.hpp`, `binary.hpp` (from `research/sigfm_c/`, LGPL). |
| `libfprint/drivers/goodix55a4/meson.build` + edits to `libfprint/meson.build` | Build glue: register `goodix55a4` driver token; link `openssl` + `opencv4`; compile the C++ TUs. **No edits to any libfprint core file.** |

---

## Phase A — Workspace + build skeleton (no hardware, no matching)

### Task A.1: Set up `vendor/libfprint` forked submodule
**Files:** `.gitmodules`, `vendor/libfprint/` (submodule), follow `vendor/README.md`
- [ ] Read `vendor/README.md` for the fork/submodule convention; create the fork (gh) if absent. **(Outward-facing: creating a GitHub fork — confirm with the user first.)**
- [ ] Add submodule pinned to the tag matching the system: `git submodule add <fork-url> vendor/libfprint`, then in it `git fetch --tags && git checkout v1.94.10 -b goodix55a4-m3` (fallback: nearest 1.94.x tag; record which).
- [ ] **Verify:** `pkg-config --modversion libfprint-2` (1.94.10) and `git -C vendor/libfprint describe --tags` agree (or note the chosen-tag delta).
- [ ] **Commit** (superproject): add submodule + `.gitmodules`.

### Task A.2: Scaffold the driver dir with a minimal `FpDevice` that probes
**Files:** create `vendor/libfprint/libfprint/drivers/goodix55a4/{goodix55a4.c,goodix55a4.h}`
- [ ] `goodix55a4.h`: `G_DECLARE_FINAL_TYPE(FpiDeviceGoodix55a4, fpi_device_goodix55a4, FPI, DEVICE_GOODIX55A4, FpDevice)`, the `id_table` extern, the instance struct (empty for now).
- [ ] `goodix55a4.c`: `G_DEFINE_TYPE`, `static const FpIdEntry id_table[] = {{ .vid=0x27c6, .pid=0x55a4 }, { .vid=0 }};` `class_init` sets `dev_class->id="goodix55a4"`, `full_name`, `type=FP_DEVICE_TYPE_USB`, `id_table`, `scan_type=FP_SCAN_TYPE_PRESS`, `nr_enroll_stages=12`, wires `probe/open/close` to stubs that call `fpi_device_*_complete(dev, NULL)`. `probe` reports id/name.
- [ ] **Verify:** deferred to A.3 build gate.

### Task A.3: Wire meson + build to an isolated prefix (GATE: builds)
**Files:** create `…/goodix55a4/meson.build`; edit `vendor/libfprint/libfprint/meson.build`
- [ ] In `libfprint/meson.build`, add `'goodix55a4' : [ 'drivers/goodix55a4/goodix55a4.c' ]` to the driver map and an `if driver == 'goodix55a4'` block adding our sources (grow as files are added) + `dependency('openssl')`, `dependency('opencv4')`.
- [ ] Enable C++: ensure `project('libfprint', ['c','cpp'], …)` (add `'cpp'` if absent) so `.cpp` TUs compile.
- [ ] Configure: `meson setup builddir -Ddrivers=goodix55a4 -Ddoc=false -Dgtk-examples=false -Dintrospection=false -Dudev_rules=disabled -Dprefix="$PWD/_inst" -Dc_args=-Wno-deprecated-declarations` then `ninja -C builddir`.
- [ ] **Gate:** builds `libfprint-2.so` containing our driver; `ninja -C builddir` exits 0.
- [ ] **Commit:** scaffold + meson glue.

---

## Phase B — Transport (capture): lift LGPL plumbing, port `run_driver` order

### Task B.1: Vendor + strip the LGPL protocol/USB/TLS plumbing
**Files:** create `…/goodix55a4/transport.c/.h` from `goodix_proto.{c,h}` + `goodix.{c,h}` + `goodixtls.{c,h}` in `vendor/libfprint-55b4-validate/…/goodixtls/`
- [ ] Copy the **packet/protocol framing verbatim** (LGPL header intact): `goodix_encode_pack`/`encode_protocol`/`decode_pack`/`decode_protocol` + checksums (pack csum = XOR of header; protocol csum = `0xaa - sum` or `0x88` null) and the `0x40`-pad USB write.
- [ ] Copy the **USB send/recv** (`goodix_send_data`/`send_pack`/`send_protocol`, `goodix_receive_data*`, reassembly) and the **TLS-PSK server** (`goodix_tls_server_init`, `tls_server_config_ctx` — TLS1.2-only, `SSL_CTX_set_psk_server_callback` → 32 zero bytes, socketpair + serve thread) and the handshake SSM proxy. Use **our** EP/interface constants.
- [ ] Strip every `fpi_image_device_*` call and FpImageDevice coupling; expose the small C API of B.3/B.4 instead. Keep attribution + LGPL header on `transport.c`.
- [ ] **Verify:** compiles into the driver; `grep -rn fpi_image_device drivers/goodix55a4/` → none.

### Task B.2: Constants header (ground truth from `driver_55x4.py`)
**Files:** edit `…/goodix55a4/goodix55a4.h` (or a `transport.h` constants block)
- [ ] Add: `EP_IN 0x82`, `EP_OUT 0x01`, `IFACE 0`, `WIDTH 88`, `HEIGHT 108`, `RAW_FRAME 14256`, `IMG_READ_LEN 14260`, `PSK_FLAGS 0xbb020007`, `static const guint8 PSK[32]={0}`, `PMK_HASH[32]={0x81,0xb8,…,0x61}`, `DEVICE_CONFIG[256]={…}` (from `driver_55x4.py:30-38`), `FDT_MODE[26]`, `FDT_DOWN[26]` (from `:143-192`), `SLEEP 0x6c`, `FW_FAMILY_RE "GF32[0-9]{2}_RTSEC_APP_100[0-9]{2}"`.
- [ ] **Verify:** arrays byte-for-byte equal to Python (`diff` the C arrays vs the `driver_55x4` bytes for `DEVICE_CONFIG`, `FDT_MODE`, `FDT_DOWN`, `PMK_HASH`).

### Task B.3: Activate SSM (no-flash) + relaxed firmware gate
**Files:** `transport.c` (activate SSM), `goodix55a4.c` (`open` → run activate)
- [ ] **No-flash audit gate first:** `grep -rniE 'write_firmware|mcu_erase|update_firmware|preset_psk_write|flash' drivers/goodix55a4/ | grep -v '//'` → **must be empty.** If not, STOP.
- [ ] SSM states mirroring `run_driver` pre-frame: `NOP(0x00)` → `RESET(0xa2,01 14)` → `READ_CHIP_ID(0x82,0000,4)` → `READ_OTP(0xa6)` → `TLS_HANDSHAKE` → `UPLOAD_CONFIG(0x90,DEVICE_CONFIG)`. Firmware check uses `FW_FAMILY_RE` (g_regex), `mark_failed` only on a non-`GF32xx` string; `g_debug` the real firmware. `preset_psk_read` verifies flags+PMK_HASH.
- [ ] **Gate (hardware, no finger):** `open` completes; log shows the real firmware + "TLS handshake complete" (reproduces 3a). Record firmware string in deltas.md.

### Task B.4: Capture SSM (THE 0x20 FIX) + 12-bit decode (GATE: real frame)
**Files:** `transport.c` (capture SSM + `decode_frame`); interface `gboolean goodix55a4_capture(FpiDeviceGoodix55a4*, guint16 **clear0, guint16 **finger, GError**)`
- [ ] Capture SSM follows `run_driver:143-201` exactly: `FDT_MODE(0x36)` → `GET_IMAGE(0x20, payload 01 00, FLAGS_TLS_DATA 0xb2)` → read `IMG_READ_LEN`, drop last 4 → **clear-0**; `FDT_MODE` → `IDLE(0x70,20)` → `READ_REG(0x82,0082,2)` → `GET_IMAGE` → **clear-1**; `FDT_MODE` → `SLEEP(0x92,6c)` → **report/await finger** → `FDT_DOWN(0x32, FDT_DOWN bytes, blocks)` → `GET_IMAGE` → **finger**. (The 3a "0x20 timeout" ran the C-fork scan order; following `run_driver` is the fix.) `decode_frame`: 6→4 12-bit px, 88×108.
- [ ] **Tell the user: gentle tap only when "place finger" prints — never press (power button).**
- [ ] **Gate (hardware):** capture a finger frame; render `clear−finger` and confirm ridge structure visually matching a `research/capture_55x4cfg.py` frame. **This = the literal C frame 3a deferred.** Record in deltas.md; **commit** (code only, no frames).

---

## Phase C — Preprocess (parity with the proven Python)

### Task C.1: `preprocess.cpp` — clear−finger → norm8 → ×4 → CLAHE
**Files:** create `…/goodix55a4/preprocess.cpp/.h`; reference `research/sigfm_c/harness.cpp` `preprocess`/`norm8` and `research/sift_match.py`
- [ ] Interface: `guint8* goodix55a4_preprocess(const guint16* finger, const guint16* clear0, int w, int h, int* out_w, int* out_h)` → malloc'd 8-bit buffer (`out_w=4w, out_h=4h`).
- [ ] Body = harness `preprocess`: `diff = clear0 - finger` (CV_32S) → `norm8` (pct 2/98) → `resize ×4 INTER_CUBIC` → `createCLAHE(2.0,{8,8})->apply` → copy to buffer.
- [ ] **Test/Gate:** on a saved Phase-B frame, byte/near-parity vs `research/sift_match.py` output (write both, compare; PSNR ≥ 40 dB or exact). Commit.

---

## Phase D — Matcher binding (re-confirm offline result inside the build)

### Task D.1: Vendor SIGFM into the driver + meson
**Files:** copy `research/sigfm_c/{sigfm.cpp,sigfm.hpp,img-info.hpp,binary.hpp}` (+ a C-only `sigfm.h` exposing the `extern "C"` block) into `…/goodix55a4/sigfm/`; edit meson sources.
- [ ] Add the C++ TUs + `opencv4_dep` to the `goodix55a4` meson block; C side includes only `sigfm.h`.
- [ ] **Gate:** builds and links (OpenCV4 resolved).

### Task D.2: In-tree matcher smoke test
- [ ] Re-run the offline harness logic against two saved frames (genuine vs gallery, impostor) **through the driver's vendored copy**. **Gate:** genuine ≫ impostor (0).

---

## Phase E — Enroll (tiled multi-frame gallery → `fpi-data`)

### Task E.1: Template (de)serialization
**Files:** create `…/goodix55a4/template.c/.h`
- [ ] `GVariant* goodix55a4_gallery_to_variant(GPtrArray* blobs)` packs N `sigfm_serialize_binary` byte-arrays as `"aay"`; `GPtrArray* goodix55a4_gallery_from_variant(GVariant*)` reverses it. Store/fetch via `g_object_set/get(print,"fpi-data",…)` after `fpi_print_set_type(print, FPI_PRINT_RAW)`.
- [ ] **Test/Gate:** round-trip N blobs → variant → blobs; `sigfm_deserialize_binary` of each yields keypoints_count > 0. Commit.

### Task E.2: `enroll` vfunc (SSM, ~12 stages, retry-on-reject)
**Files:** `goodix55a4.c` (`enroll` + enroll SSM). Pattern from `upekts.c`/`elanmoc.c`.
- [ ] `fpi_device_get_enroll_data(dev,&print)`. Per stage: `goodix55a4_capture` → `preprocess` → `sigfm_extract` → **quality gate** (`sigfm_keypoints_count ≥ Kmin` + non-empty clear−finger signal). On pass: serialize → append to gallery → store updated variant on `print` → `fpi_device_enroll_progress(dev, ++done, print, NULL)`. On reject: `fpi_device_enroll_progress(dev, done, NULL, fpi_device_retry_new(FP_DEVICE_RETRY_GENERAL))` and re-capture (no increment).
- [ ] **Spoken tiling guidance** (center, tip, base, L/R edges, slight rolls) + **gentle taps**.
- [ ] Final stage: `fpi_print_set_type(print, FPI_PRINT_RAW)`, ensure `fpi-data` set, `fpi_device_enroll_complete(dev, g_object_ref(print), NULL)`.
- [ ] Honor `fpi_device_action_is_cancelled`; implement `cancel`.
- [ ] **Gate:** isolated `fprintd-enroll` completes and writes a template into the isolated store (under the vault). No frames/templates in the repo.

---

## Phase F — Verify / Identify

### Task F.1: `verify` vfunc
**Files:** `goodix55a4.c` (`verify`). Pattern from `synaptics.c`/`goodixmoc/goodix.c`.
- [ ] `fpi_device_get_verify_data(dev,&enrolled)`; `from_variant` → gallery of `SigfmImgInfo*`. Capture probe → preprocess → `sigfm_extract` → `best = max sigfm_match_score(probe, g_i)` → `result = best>=T ? FPI_MATCH_SUCCESS : FPI_MATCH_FAIL`. `fpi_device_verify_report(dev,result, result==SUCCESS?enrolled:NULL, NULL)` then `fpi_device_verify_complete(dev,NULL)`. `<0` score → `FPI_MATCH_ERROR` + GError.
- [ ] **Gate:** `fprintd-verify` matches the enrolled finger; an unenrolled finger → no match.

### Task F.2: `identify` vfunc
- [ ] `fpi_device_get_identify_data(dev,&prints)`; same scoring across all prints; report best via `fpi_device_identify_report(dev, match_or_NULL, scanned, NULL)` + `fpi_device_identify_complete`.
- [ ] **Gate:** identify picks the right enrolled finger among several; impostor → no match.

---

## Phase G — Integration, threshold calibration, M3-done gate

### Task G.1: Private fprintd end-to-end + held-out FAR/FRR
**Files:** `docs/protocol/deltas.md`, `project-status` memory
- [ ] Run a **private** fprintd against the isolated build (point `LD_LIBRARY_PATH` / fprintd's driver env at `builddir`; do **not** touch `/usr` or `/etc/pam.d`). Confirm fprintd lists `goodix55a4`.
- [ ] Enroll the real finger (tiled, gentle taps). Capture **held-out genuine probes** + a **diverse impostor set** (left-hand fingers, per the M2 method). Sweep `T`; pick the live threshold (start ~5 from 3b-offline; re-tune on live SIGFM scores).
- [ ] **M3-DONE GATE:** `fprintd-enroll` + `fprintd-verify`/`-identify` work end-to-end via the driver; **FAR=0 across the diverse impostors** and low FRR on held-out genuine probes. Password/PAM untouched.
- [ ] Record results + chosen `T` in `docs/protocol/deltas.md`; update `project-status` memory (M3 ✅, M4 = PAM/system-install next). Final commit (code + docs; **no biometric data**).

---

## Verification (how we know M3 is done)

1. `ninja -C vendor/libfprint/builddir` builds the driver (Phase A/B/D gates).
2. Phase-B hardware gate: the C driver captures a finger frame with real ridge structure (the literal C frame 3a deferred).
3. Phase C: preprocess output matches the proven Python pipeline (PSNR/near-exact).
4. Phase D: vendored SIGFM separates genuine vs impostor inside the build.
5. Phase G: a private `fprintd-enroll` then `fprintd-verify` succeed via `goodix55a4`, FAR=0 across diverse impostors and low FRR — **no `/usr`/PAM changes, no flashing, no biometric data in the repo.**

## Risks & mitigations
- **Mixed C/C++ + OpenCV in a libfprint driver** is unusual but supported (TheWeirdDev's fork links opencv4; we scope the dep to our driver). Mitigation: C core, C++ only for preprocess/sigfm, link via the `cpp` project language.
- **TLS thread + socketpair** (reused) is heavier than libfprint's async ideal. Accepted for M3 (proven code); flagged for an M5 MemoryBIO cleanup before any upstream PR.
- **SIGFM geometric filter stricter than our RANSAC → FRR more coverage-sensitive.** Mitigation: tiled enroll (~12 stages) + retry-on-reject + `Kmin`/threshold tuning in Phase G.
- **Fork-version drift** from system 1.94.10. Mitigation: pin the submodule to the matching tag.

## Self-review — spec coverage
- Custom `FpDevice` on stock core (not `FpImageDevice`/NBIS), core unmodified → File structure + Phase A/E/F. ✔
- Vendored SIGFM called from enroll/verify/identify; templates as `FpPrint` RAW blob → Phases D/E/F. ✔
- No-flash capture ported from proven Python (the 0x20 fix) → Phase B (ground-truth order). ✔
- CLAHE kept (M2) → Phase C + the offline harness. ✔
- Tiled multi-frame enroll, retry, gentle taps, ~12 stages → Phase E. ✔
- Threshold recalibrated on our hardware → Phase G. ✔
- Relaxed firmware gate; reconciled PSK (zero key)/orientation/config-size constants → ground-truth section + Phase B. ✔
- Isolated prefix, no `/usr`, no PAM, biometric data in vault only → hard rules, Phase A.3, Phase G. ✔
- LGPL attribution → Phase B.1, D.1. ✔
- M3-done gate (fprintd-enroll/verify + sane FAR/FRR) → Phase G. ✔
- Deferred to M4: PAM, system install. ✔ (non-goal, stated)
