# Goodix 55a4 libfprint Driver (M3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get the Goodix `27c6:55a4` reader working through `fprintd` (`fprintd-enroll` / `fprintd-verify`) on this machine, via a libfprint driver that matches with SIGFM (SIFT+CLAHE), with no firmware flashing and no PAM changes.

**Architecture:** *Validate-then-clean.* **Phase 3a** proves the existing community C capture path (`TheWeirdDev/libfprint@55b4-experimental`, which already lists `0x55a4`) works on *this* unit, no-flash — gate: one C-captured frame. **Phase 3b-offline** vendors the LGPL SIGFM matcher and proves it separates our existing M2 corpus in C (no hardware needed) — gate: FAR=0 at a usable threshold. **Phase 3b-driver** builds the real deliverable: a custom `FpDevice` on stock libfprint that captures, preprocesses (clear−finger + CLAHE), and calls SIGFM from its own enroll/verify — gate: `fprintd-enroll`/`fprintd-verify` work with sane FAR/FRR. Stock libfprint core stays unpatched (upstreamable); no `FpImageDevice` (that path is locked to NBIS, which M2 disproved).

**Tech Stack:** C (libfprint / GLib / GObject), C++ (SIGFM / OpenCV 4), `meson`+`ninja`, OpenSSL TLS-PSK, libusb (via libfprint), Python 3 (existing capture + eval reference in `research/`).

**Spec:** [`../specs/2026-05-30-goodix-55a4-m3-driver-design.md`](../specs/2026-05-30-goodix-55a4-m3-driver-design.md)

---

## Standing constraints (apply to every task)

- **No firmware flashing, ever.** Phase 3a includes an explicit pre-run audit that the activate path sends no firmware/OTP write. See [[no-flashing-goodix-55a4]].
- **The fingerprint pad is the power button.** Every capture step instructs **gentle taps only — never press hard.**
- **Biometric data only under `$GOODIX_VAULT/`**, never in the repo. Captured frames and enrolled templates stay in the vault; the throwaway fork clone is gitignored.
- **No `/etc/pam.d` changes, no overwriting system `/usr` libfprint.** Everything builds to an isolated prefix. (System install + PAM is M4.)
- **The user (`g`) is colorblind** — any rendered diagnostics must not depend on color to be read.
- **Log firmware/handshake findings** in `docs/protocol/deltas.md`.

## Why this plan stops at a 3b-driver *roadmap* (not bite-sized steps)

This is a deliberate consequence of "validate-then-clean," not a placeholder. The 3b driver's capture code is a C port of our Python protocol, and its exact shape (which constants are right, whether the C TLS/USB path even works on this unit, what the real firmware/orientation quirks are) is **what Phase 3a empirically determines**. Writing 40 "complete-code" C steps now would be fiction. So Phases 0, 3a, and 3b-offline are fully bite-sized and executable today; Phase 3b-driver is a locked-down file map + per-unit interface/test/gate to be expanded into its own bite-sized plan the moment 3a passes. Each phase produces working, testable output on its own.

---

## Phase 0 — Prerequisites

### Task 0.1: Install build tooling

**Files:** none (system packages)

- [ ] **Step 1: Check what's missing**

Run: `for t in meson ninja gcc g++ pkg-config; do printf "%-10s " $t; command -v $t || echo MISSING; done; pkg-config --exists opencv4 && echo "opencv4 OK" || echo "opencv4 MISSING"; pkg-config --modversion libfprint-2`
Expected: `meson` and `ninja` MISSING; `gcc`/`g++`/`pkg-config` present; note opencv4 status; libfprint-2 = `1.94.10`.

- [ ] **Step 2: Install meson, ninja, opencv, doctest + libfprint build deps**

These are system packages; if `sudo` prompts for a password, run it yourself in-session with the `!` prefix: `! sudo pacman -S --needed meson ninja opencv vtk hdf5 doctest gtk-doc gobject-introspection nss libgusb gusb pixman cairo`

Run (or `!`-run): `sudo pacman -S --needed meson ninja opencv doctest gtk-doc gobject-introspection nss libgusb pixman cairo`
Expected: all install or report "up to date". (`libfprint`/`glib2`/`openssl` are already present.)

- [ ] **Step 3: Verify**

Run: `meson --version && ninja --version && pkg-config --exists opencv4 && echo "opencv4 $(pkg-config --modversion opencv4)"`
Expected: meson ≥ 0.56, a ninja version, and an opencv4 4.x version printed.

- [ ] **Step 4: Commit (docs only — no system state in git)**

No commit; this task changes no tracked files. Proceed.

### Task 0.2: Gitignore the throwaway validation clone

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add ignore entries**

Append to `.gitignore`:
```
# Phase 3a throwaway fork clone (never committed; may contain captured frames)
/vendor/libfprint-55b4-validate/
# Phase 3b-offline vendored matcher build artifacts
/research/sigfm_c/builddir/
/research/sigfm_c/*.o
```

- [ ] **Step 2: Verify it's ignored**

Run: `git check-ignore -v vendor/libfprint-55b4-validate/x research/sigfm_c/builddir/x`
Expected: both paths print a matching `.gitignore` rule.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "Ignore M3 throwaway fork clone + sigfm_c build artifacts"
```

---

## Phase 3a — Validate the C capture on our hardware (gate: one captured frame)

Throwaway. Goal: prove the C TLS/USB capture works on *this* `27c6:55a4`, no-flash.

### Task 3a.1: Clone the fork (throwaway, gitignored)

**Files:**
- Create: `vendor/libfprint-55b4-validate/` (gitignored clone)

- [ ] **Step 1: Clone the exact branch**

Run: `git clone --depth 1 --branch 55b4-experimental https://github.com/TheWeirdDev/libfprint.git vendor/libfprint-55b4-validate`
Expected: clones into `vendor/libfprint-55b4-validate` on branch `55b4-experimental`.

- [ ] **Step 2: Confirm our device is in the table**

Run: `grep -n '0x55a4\|0x55b4' vendor/libfprint-55b4-validate/libfprint/drivers/goodixtls/goodix55x4.h`
Expected: shows `{.vid = 0x27c6, .pid = 0x55a4}` (and `0x55b4`).

### Task 3a.2: Audit the activate path for any firmware/OTP write (no-flash safety gate)

**Files:**
- Read: `vendor/libfprint-55b4-validate/libfprint/drivers/goodixtls/goodix55x4.c`, `.../goodix.c`

- [ ] **Step 1: Confirm no firmware/OTP/flash write exists in the driver**

Run:
```bash
cd vendor/libfprint-55b4-validate
grep -rniE 'flash|write_firmware|update_firmware|send_read_otp|send_write|mcu_download|otp_write' libfprint/drivers/goodixtls/ | grep -v '//' | grep -viE 'write_sensor_register|fprintf|fwrite|client_write|tls'
```
Expected: **no uncommented firmware/OTP-write call.** The only OTP line in `goodix55x4.c` activate path is the commented-out `// goodix_send_read_otp(...)`. `ACTIVATE_SET_MCU_CONFIG` uploads volatile `goodix_55x4_config` only. If any *uncommented* firmware-write appears, **STOP** and report — do not run against hardware.

- [ ] **Step 2: Note the activate sequence for the record**

Run: `grep -n 'ACTIVATE_' libfprint/drivers/goodixtls/goodix55x4.c | head`
Expected: states like `ACTIVATE_CHECK_FW_VER`, `ACTIVATE_SET_MCU_CONFIG`, etc. — read-check + config-upload + TLS, no write. Record in deltas.md (Task 3a.6).

### Task 3a.3: Relax the strict firmware-version gate

**Files:**
- Modify: `vendor/libfprint-55b4-validate/libfprint/drivers/goodixtls/goodix55x4.c` (the `strcmp` at ~line 109)

Our unit reports a flaky `GF32xx…_RTSEC_APP_10062` (model digit varies per read), not the hard-coded `GF3268_RTSEC_APP_10041`. The driver's `check_firmware_version` fails the SSM on exact mismatch. Relax it to the same app-family tolerance our Python uses.

- [ ] **Step 1: Show the current check**

Run: `grep -n 'strcmp(firmware, GOODIX_55X4_FIRMWARE_VERSION)' libfprint/drivers/goodixtls/goodix55x4.c`
Expected: one hit (the `if (strcmp(...)) { ... mark_failed ... }`).

- [ ] **Step 2: Relax to an app-family match**

Run:
```bash
sed -i 's/strcmp(firmware, GOODIX_55X4_FIRMWARE_VERSION)/!strstr(firmware, "_RTSEC_APP_")/' \
  libfprint/drivers/goodixtls/goodix55x4.c
grep -n '_RTSEC_APP_' libfprint/drivers/goodixtls/goodix55x4.c
```
Expected: the `if` now reads `if (!strstr(firmware, "_RTSEC_APP_")) {` — accepts any `GF32xx…_RTSEC_APP_…` while still rejecting a totally wrong string. (The driver also `g_print`s the firmware, so we'll see our unit's real value at runtime.)

### Task 3a.4: Build the fork to an isolated prefix

**Files:**
- Create: `vendor/libfprint-55b4-validate/builddir/` (build tree)

- [ ] **Step 1: Discover the driver token for `-Ddrivers`**

Run: `grep -rn '55x4\|goodixtls' meson.build libfprint/meson.build | head`
Expected: shows how the 55x4 driver is gated (a `drivers` token, likely `goodixtls`, or included in the default/`all` set). Use that token in Step 2 (`goodixtls` assumed below; fall back to `all` if setup errors that the driver is unknown).

- [ ] **Step 2: Configure (isolated prefix, minimal deps, no system install)**

Run:
```bash
meson setup builddir \
  -Ddrivers=goodixtls -Ddoc=false -Dgtk-examples=false \
  -Dintrospection=disabled -Dudev_rules=disabled \
  -Dprefix="$PWD/_inst" -Dc_args=-Wno-deprecated-declarations
```
Expected: configures successfully and lists `opencv4`, `openssl` as found dependencies. If it errors on an unknown driver token, retry with `-Ddrivers=all`. If it errors on a missing dep, install it (Phase 0) and re-run. If OpenSSL-3 deprecations are *errors* not warnings, the `-Dc_args` flag above suppresses them.

- [ ] **Step 3: Build the library + example tools**

Run: `ninja -C builddir`
Expected: builds `libfprint-2.so` and `examples/img-capture`. **If the 1.94.6 tree won't build against current toolchain** after reasonable fixes, that is a legitimate Phase-3a outcome: record the blockers in deltas.md, rely on our already-working Python capture proof (M0), and proceed to Phase 3b (the clean driver targets current libfprint anyway). Do not sink more than a short timebox here.

- [ ] **Step 4: Confirm the capture tool built**

Run: `ls -la builddir/examples/img-capture`
Expected: the executable exists.

### Task 3a.5: Capture one frame off our 55a4 (THE GATE)

**Files:**
- Output: a frame under `$GOODIX_VAULT/frames/55a4-cvalidate/` (vault, gitignored)

- [ ] **Step 1: Confirm the device is present**

Run: `lsusb -d 27c6:55a4`
Expected: `Bus … Device …: ID 27c6:55a4 Shenzhen Goodix …`.

- [ ] **Step 2: Read img-capture's usage**

Run: `sed -n '1,60p' examples/img-capture.c | grep -iE 'usage|argv|output|pgm|printf' | head`
Expected: shows how it takes the output path / how it saves the PGM (it uses libfprint to capture and writes an image). Note the exact invocation.

- [ ] **Step 3: Capture (gentle tap) using the isolated build**

This needs USB access → run as root, pointing the loader at our freshly built lib so it finds the goodix driver. **Tell the user: place fingertip on the reader with a GENTLE tap when prompted — do NOT press hard (it's the power button).** Output goes to the vault.

Run:
```bash
mkdir -p $GOODIX_VAULT/frames/55a4-cvalidate
sudo LD_LIBRARY_PATH="$PWD/builddir/libfprint" G_MESSAGES_DEBUG=all \
  ./builddir/examples/img-capture $GOODIX_VAULT/frames/55a4-cvalidate/cframe.pgm
```
Expected: debug log prints the device firmware string (our real `GF32xx…`), runs the no-flash activate (config upload + TLS handshake), reports finger, and writes a PGM. (If img-capture takes no path arg, it writes to its default filename in CWD — `cd` to the vault dir first, then run it.)

- [ ] **Step 4: Verify the frame has ridge structure (the gate)**

Run: `cd <repo> && .venv/bin/python research/render_pgm.py $GOODIX_VAULT/frames/55a4-cvalidate/cframe.pgm` (or open the PGM). 
Expected: a non-empty 108×88 (or 88×108) image with visible ridge-like structure. **This passing = Phase 3a gate met: the C transport works on this unit, no-flash.** If the image is all-zero / handshake stalls, capture the debug log and diagnose (PSK flags, config, FDT) in deltas.md before proceeding.

### Task 3a.6: Record the validation result

**Files:**
- Modify: `docs/protocol/deltas.md`

- [ ] **Step 1: Append a dated entry**

Add to `docs/protocol/deltas.md` a "M3a: C capture validated (no-flash)" entry recording: the real firmware string printed, that the activate path was audited flash-free, the build token/flags that worked (or the blockers if it didn't build), and the captured-frame outcome. Keep it factual.

- [ ] **Step 2: Commit (docs only)**

```bash
git add docs/protocol/deltas.md
git commit -m "M3a: validate C 55x4 capture on our unit (no-flash) — record outcome"
```

---

## Phase 3b-offline — Prove SIGFM separates our corpus in C (gate: FAR=0). No hardware.

Validates the matcher we'll ship, against the existing M2 vault corpus, before any driver wiring.

### Task 3b-off.1: Vendor the SIGFM matcher sources

**Files:**
- Create: `research/sigfm_c/sigfm.cpp`, `sigfm.h`, `sigfm.hpp`, `img-info.hpp`, `binary.hpp`

- [ ] **Step 1: Fetch the LGPL SIGFM sources from the fork**

Run:
```bash
mkdir -p research/sigfm_c
for f in sigfm.cpp sigfm.h sigfm.hpp img-info.hpp binary.hpp; do
  gh api -H "Accept: application/vnd.github.raw" \
    "repos/goodix-fp-linux-dev/libfprint/contents/libfprint/sigfm/$f?ref=0x00002a/libfprint-sigfm" \
    > "research/sigfm_c/$f"
done
ls -la research/sigfm_c/
head -5 research/sigfm_c/sigfm.h
```
Expected: five files present; `sigfm.h` shows the LGPL header and the `extern "C"` API.

- [ ] **Step 2: Confirm the C API symbols we'll call**

Run: `grep -nE 'sigfm_extract|sigfm_match_score|sigfm_serialize_binary|sigfm_keypoints_count' research/sigfm_c/sigfm.h`
Expected: all four declared.

- [ ] **Step 3: Commit the vendored matcher (LGPL, attribution intact)**

```bash
git add research/sigfm_c/sigfm.cpp research/sigfm_c/sigfm.h research/sigfm_c/sigfm.hpp research/sigfm_c/img-info.hpp research/sigfm_c/binary.hpp
git commit -m "Vendor SIGFM matcher (LGPL-2.1+) for offline C validation"
```

### Task 3b-off.2: Write a P2 PGM reader + preprocessing parity, as a test against the Python pipeline

**Files:**
- Create: `research/sigfm_c/harness.cpp`
- Reference: `research/sift_match.py` (`preprocess`), `research/nbis_test.py` (`norm8`), `research/render_pgm.py` (`read_p2`)

- [ ] **Step 1: Write the harness (PGM read → clear−finger → norm → ×4 → CLAHE → SIGFM → FAR/FRR sweep)**

Create `research/sigfm_c/harness.cpp`:
```cpp
// Offline validation: does vendored SIGFM separate our M2 corpus?
// Mirrors research/sift_match.py preprocessing, then uses sigfm_match_score.
#include "sigfm.h"
#include <opencv2/opencv.hpp>
#include <dirent.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <string>
#include <vector>

// Minimal P2 (ASCII) PGM reader; values may exceed 255 (our frames are ~12-bit).
static cv::Mat read_p2(const std::string& path) {
    FILE* f = fopen(path.c_str(), "r");
    if (!f) return {};
    char magic[3] = {0};
    if (fscanf(f, "%2s", magic) != 1 || std::string(magic) != "P2") { fclose(f); return {}; }
    int w, h, maxv;
    if (fscanf(f, "%d %d %d", &w, &h, &maxv) != 3) { fclose(f); return {}; }
    cv::Mat m(h, w, CV_32S);
    for (int i = 0; i < w * h; ++i) {
        int v = 0; if (fscanf(f, "%d", &v) != 1) { fclose(f); return {}; }
        m.at<int>(i / w, i % w) = v;
    }
    fclose(f);
    return m;
}

// percentile 2/98 normalize to uint8 (matches nbis_test.norm8)
static cv::Mat norm8(const cv::Mat& src32) {
    std::vector<int> vals; vals.reserve(src32.total());
    for (int i = 0; i < (int)src32.total(); ++i) vals.push_back(((int*)src32.data)[i]);
    std::sort(vals.begin(), vals.end());
    double lo = vals[(int)(0.02 * vals.size())], hi = vals[(int)(0.98 * vals.size())];
    cv::Mat out(src32.size(), CV_8U);
    for (int i = 0; i < (int)src32.total(); ++i) {
        double v = (((int*)src32.data)[i] - lo) / (hi - lo + 1e-9);
        v = std::min(1.0, std::max(0.0, v));
        out.data[i] = (unsigned char)(v * 255.0);
    }
    return out;
}

// clear-finger baseline-subtracted, normalized, x4, CLAHE — like sift_match.preprocess
static cv::Mat preprocess(const std::string& sess) {
    cv::Mat fp = read_p2(sess + "/fingerprint.pgm");
    cv::Mat c0 = read_p2(sess + "/clear-0.pgm");
    if (fp.empty() || c0.empty()) return {};
    cv::Mat diff = c0 - fp;            // CV_32S
    cv::Mat n = norm8(diff);
    cv::Mat up; cv::resize(n, up, {}, 4, 4, cv::INTER_CUBIC);
    auto clahe = cv::createCLAHE(2.0, {8, 8});
    cv::Mat out; clahe->apply(up, out);
    return out;
}

static std::string role(const std::string& name) {
    if (name.find("imp") != std::string::npos) return "impostor";
    if (name.find("prb") != std::string::npos) return "probe";
    return "gallery";
}

int main(int argc, char** argv) {
    std::string root = (argc > 1) ? argv[1]
        : std::string(getenv("HOME")) + "/yo/data/goodix-55a4/frames";
    // collect m2c-* session dirs
    std::vector<std::string> sessions;
    if (DIR* d = opendir(root.c_str())) {
        for (dirent* e; (e = readdir(d));) {
            std::string n = e->d_name;
            if (n.rfind("m2c-", 0) == 0) sessions.push_back(root + "/" + n);
        }
        closedir(d);
    }
    std::sort(sessions.begin(), sessions.end());
    std::map<std::string, SigfmImgInfo*> feats;
    std::map<std::string, std::string> roles;
    for (auto& s : sessions) {
        std::string name = s.substr(s.find_last_of('/') + 1);
        cv::Mat img = preprocess(s);
        if (img.empty()) { printf("SKIP %s (no frames)\n", name.c_str()); continue; }
        SigfmImgInfo* info = sigfm_extract(img.data, img.cols, img.rows);
        if (!info || sigfm_keypoints_count(info) == 0) { printf("SKIP %s (no keypoints)\n", name.c_str()); continue; }
        feats[name] = info; roles[name] = role(name);
        printf("%-9s %-18s kp=%d\n", roles[name].c_str(), name.c_str(), sigfm_keypoints_count(info));
    }
    std::vector<std::string> gal, prb, imp;
    for (auto& [n, _] : feats) (roles[n]=="gallery"?gal:roles[n]=="probe"?prb:imp).push_back(n);
    auto best = [&](const std::string& n) {
        int b = 0; for (auto& g : gal) b = std::max(b, sigfm_match_score(feats[n], feats[g])); return b;
    };
    std::vector<int> gscore, iscore;
    printf("\ngenuine probes:\n");  for (auto& n : prb) { int s=best(n); gscore.push_back(s); printf("  %-18s -> %d\n", n.c_str(), s); }
    printf("impostor probes:\n");   for (auto& n : imp) { int s=best(n); iscore.push_back(s); printf("  %-18s -> %d\n", n.c_str(), s); }
    int hi = 1; for (int s : gscore) hi = std::max(hi, s); for (int s : iscore) hi = std::max(hi, s);
    printf("\nthreshold sweep (accept if score >= T):\n   T    FRR    FAR\n");
    int far0 = -1;
    for (int T = 0; T <= hi + 1; ++T) {
        double frr = gscore.empty()?0: (double)std::count_if(gscore.begin(),gscore.end(),[&](int x){return x<T;})/gscore.size();
        double far = iscore.empty()?0: (double)std::count_if(iscore.begin(),iscore.end(),[&](int x){return x>=T;})/iscore.size();
        if (far == 0 && far0 < 0) far0 = T;
        printf("  %2d  %5.0f%% %5.0f%%%s\n", T, frr*100, far*100, (far0==T?"  <- FAR=0":""));
    }
    printf("\nVERDICT: %s (FAR=0 at T=%d). genuine_max=%d impostor_max=%d\n",
           (far0>=0 && !gscore.empty() && *std::max_element(gscore.begin(),gscore.end())>=far0) ? "SIGFM SEPARATES our corpus" : "REVISIT",
           far0, gscore.empty()?0:*std::max_element(gscore.begin(),gscore.end()),
           iscore.empty()?0:*std::max_element(iscore.begin(),iscore.end()));
    for (auto& [_, info] : feats) sigfm_free_info(info);
    return 0;
}
```

- [ ] **Step 2: Write the build script**

Create `research/sigfm_c/build.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
g++ -O2 -std=c++17 harness.cpp sigfm.cpp \
  $(pkg-config --cflags --libs opencv4) -o harness
echo "built ./harness"
```
Then: `chmod +x research/sigfm_c/build.sh`

### Task 3b-off.3: Build and run the matcher validation (gate)

- [ ] **Step 1: Build**

Run: `bash research/sigfm_c/build.sh`
Expected: compiles `harness` (links OpenCV4 + SIGFM). Fix any include path (`sigfm.hpp`/`img-info.hpp` are in the same dir) if needed.

- [ ] **Step 2: Run against the existing M2 corpus (no new captures)**

Run: `research/sigfm_c/harness`
Expected: lists gallery/probe/impostor sessions with keypoint counts, then a FAR/FRR sweep. **Gate: a threshold T with FAR=0 across the impostors and genuine_max ≥ T** (i.e. "SIGFM SEPARATES our corpus"). This confirms the *shippable* matcher + a starting threshold on our own data. If it doesn't separate, compare against `research/sift_match.py` (our RANSAC variant) and note the gap — SIGFM uses a length/angle geometric filter rather than RANSAC; a tuning step (CLAHE clip, ratio) would precede the driver.

- [ ] **Step 3: Record the threshold + commit the harness**

Append the FAR/FRR result and chosen starting threshold to `docs/protocol/deltas.md`. Then:
```bash
git add research/sigfm_c/harness.cpp research/sigfm_c/build.sh docs/protocol/deltas.md
git commit -m "M3b-offline: SIGFM separates our M2 corpus in C; record threshold"
```
(Do **not** commit the built `harness` binary or any vault frames.)

---

## Phase 3b-driver — the clean driver (ROADMAP; expand to bite-sized steps after 3a passes)

Locked-down structure below. Each unit lists its file, interface, how it's tested, and its gate. The bite-sized TDD/observation steps get written into a follow-on plan (`2026-..-goodix-55a4-m3b-driver.md`) once 3a confirms the C capture works and the runtime firmware/orientation/PSK facts are known — because the capture unit *is* a port informed by those facts.

### File structure (on a branch in `vendor/libfprint`, a forked submodule of current upstream libfprint)

| File | Responsibility |
|---|---|
| `vendor/libfprint/` | Forked submodule of **current** upstream libfprint (≈1.94.10, matching system), per `vendor/README.md`. Core stays **unmodified**. |
| `libfprint/drivers/goodix55a4/goodix55a4.c` | The custom `FpDevice` subclass: `id_table {0x27c6,0x55a4}`, vfuncs `probe/open/close/enroll/verify/identify/cancel`, capture SSM. |
| `libfprint/drivers/goodix55a4/goodix55a4.h` | Constants (verified in 3a): PSK bytes/flags, `goodix_55x4_config`, EPs, dims, relaxed firmware family. |
| `libfprint/drivers/goodix55a4/transport.c/.h` | USB + no-flash init + PSK + config upload + FDT + encrypted image read. Ported from Python `driver_5503`/`driver_55x4`; OpenSSL TLS-PSK lifted (LGPL) from `goodixtls.c`/`goodix.c`. |
| `libfprint/drivers/goodix55a4/preprocess.c/.h` | `clear−finger` → percentile-norm → CLAHE → 8-bit buffer (mirrors `research/sift_match.preprocess`). |
| `libfprint/drivers/goodix55a4/sigfm/` | Vendored SIGFM (from `research/sigfm_c/`), wired into the driver's meson build (OpenCV4 dep, scoped to this driver). |
| `libfprint/drivers/goodix55a4/meson.build` + driver registration | Build glue; register driver token so `-Ddrivers=goodix55a4` works; **no edits to libfprint core files**. |

### Units, interfaces, tests, gates

- [ ] **Unit A — Transport (capture).** Interface: `gboolean goodix55a4_capture_frame(FpiDeviceGoodix55a4*, guint8 **out, int *w, int *h, GError**)` driven by an `FpiSsm`. Port from Python; constants from 3a. **Test/gate:** the driver captures a frame whose render matches a 3a reference frame (same ridge structure). No-flash audit re-asserted (no firmware/OTP write states).
- [ ] **Unit B — Preprocess.** Interface: `cv`-free C producing the same 8-bit buffer as `research/sift_match.preprocess` (CLAHE via OpenCV C++ or a C CLAHE). **Test/gate:** byte/near-parity vs the Python output on a saved 3a frame (PSNR within tolerance).
- [ ] **Unit C — Matcher binding.** Reuse vendored `sigfm.{cpp,h}`. Interface already C-ABI (`sigfm_extract`/`match_score`/`serialize`). **Test/gate:** the Phase-3b-offline harness already proved this on our corpus; in-driver test re-runs extract+score on two saved frames and asserts genuine ≫ impostor.
- [ ] **Unit D — Enroll.** `nr_enroll_stages ≈ 12–15`; per stage capture→preprocess→`sigfm_extract`→quality-gate (`sigfm_keypoints_count` + clean-baseline/coverage)→append serialized blob; `fpi_device_enroll_progress`; retry-on-reject; tiled-coverage guidance (center/tip/base/edges/rolls), gentle taps. Store gallery in `FpPrint` (`fpi_print_set_type` RAW + GVariant array of blobs). **Test/gate:** `fprintd-enroll` (isolated fprintd) completes and writes a template to the isolated store.
- [ ] **Unit E — Verify/Identify.** Capture probe → `sigfm_extract` → `sigfm_match_score` vs each enrolled blob → accept if best ≥ T (from 3b-offline, re-tuned on live captures). **Test/gate:** `fprintd-verify` matches the enrolled finger; a different finger is rejected.
- [ ] **Unit F — Isolated integration + eval.** Run a private `fprintd` against the isolated libfprint build (env: `LD_LIBRARY_PATH`/`FP_*`; no `/usr`, no `/etc/pam.d`). **Gate (M3 done):** `fprintd-enroll` + `fprintd-verify` work end-to-end; FAR/FRR on a held-out live set sane (FAR=0 across diverse impostors, low FRR). Record in deltas.md; update `project-status` memory; password/PAM untouched.

### 3b constants to verify at expansion time (from 3a runtime + source)
Firmware family string actually seen; `GOODIX_55X4_PSK_FLAGS 0xbb020007` + PSK bytes vs our Python; `goodix_55x4_config` bytes; EPs `0x82`/`0x01`; **orientation 108×88 with the driver's transpose** vs our 88×108 (reconcile so preprocess/Python eval agree); live SIGFM threshold.

---

## Self-review — spec coverage

- Capture (no-flash, this unit) → Phase 3a (gate: frame). ✔
- NBIS-is-out / SIFT-class matcher needed → 3b-offline vendors SIGFM, proves separation. ✔
- Custom `FpDevice` on stock core (not `FpImageDevice`/NBIS) → 3b-driver Unit map, core unmodified. ✔
- SIGFM called from driver enroll/verify; templates as `FpPrint` raw blob gallery → Units C/D/E. ✔
- CLAHE kept (M2) → preprocess Unit B + offline harness. ✔
- Tiled multi-frame enroll, retry, gentle taps, ~12–15 stages → Unit D. ✔
- Threshold recalibrated on our hardware → 3b-offline + Unit E. ✔
- Relaxed firmware gate; reconcile PSK/orientation constants → Tasks 3a.3 + 3b constants list. ✔
- Isolated prefix, no `/usr`, no PAM, biometric data in vault only → Phase 0.2, 3a.5, Unit F, standing constraints. ✔
- LGPL attribution → Task 3b-off.1. ✔
- M3-done gate (fprintd-enroll/verify + sane FAR/FRR) → Unit F. ✔
- Deferred to M4: PAM, system install. ✔ (out of scope, stated)
