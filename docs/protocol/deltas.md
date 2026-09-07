# Protocol / firmware deltas vs upstream `goodix-fp-dump` `driver_55x4.py`

Deltas confirmed on **this** `27c6:55a4` unit (Arch, user `g`). Record only what differs
from upstream or what we confirm on this hardware.

---

## 2026-05-29 — Device ships different app firmware than the capture target

**How found:** read-only probe (`research/probe_m0.py`) — USB init + `nop` +
`firmware_version()` + `preset_psk_read()`. No TLS, no image capture, no flash.

**Observed:**

| Field | This unit | `driver_55x4.py` expects |
|---|---|---|
| App firmware | `GF3208_RTSEC_APP_10062` | `TARGET_FIRMWARE = GF3268_RTSEC_APP_10041` |
| Valid PSK (zero-PSK) | `False` | written on demand |
| Bus/addr | bus 1 addr 9 | — |
| Endpoints | IN `0x82`, OUT `0x01`, Bulk, vendor-specific iface 0 | matches |
| Chip ID read | not yet read (pre-TLS only) | `read_sensor_register(0x0000,4)` in `run_driver` |

Note the model digits: this unit reports **GF32`08`** rev `100`62``; the bundled target
firmware is **GF32`68`** rev `100`41``. Both match upstream's permissive
`VALID_FIRMWARE = GF32[0-9]{2}_RTSEC_APP_100[0-9]{2}` pattern, so upstream's control flow
treats our `GF3208` app as "a valid-but-wrong app → erase and reflash to the GF3268 target."

**Consequence for the capture path (`driver_55x4.main`):**

Because our firmware != `TARGET_FIRMWARE` but matches `VALID_FIRMWARE`, `main()`'s loop would:
1. `erase_firmware(device)` — **wipe the current `GF3208` app**; device falls back to IAP
   (`MILAN_RTSEC_IAP_10027`).
2. Re-init; now firmware == IAP → `write_psk()` (zero-PSK white-box) → `update_firmware()`
   which flashes the bundled `firmware/55x4/GF3268_RTSEC_APP_10041.bin`.
3. Re-init; firmware == TARGET → `run_driver()` → calibration frames + finger capture.

So on this unit the M0 "capture" is **not** the reversible "write zero-PSK + read frames"
the design spec assumed — it is a **firmware erase + reflash from `GF3208_RTSEC_APP_10062`
to `GF3268_RTSEC_APP_10041`**. The erase→IAP→reflash is the documented upstream recovery
path (recoverable via IAP), but it is device-modifying and crosses model digits
(`3208`→`3268`), so it is **not being run without explicit user approval.**

**Open question:** is flashing the `GF3268` app onto a unit that reports `GF3208` correct,
or does this unit need a different/native firmware? Upstream `goodix-fp-dump` only ships the
`GF3268_RTSEC_APP_10041.bin` for `55x4`. Needs confirmation (upstream Discord / issues)
before flashing. Status: **superseded — see no-flash 5503 path below; reflash NOT pursued.**

---

## 2026-05-29 — No-flash 5503 path: TLS+config OK, but image fetch returns a short frame

**Decision taken:** do NOT flash (user constraint). Drive the device with `driver_5503`
primitives (its `WORKING_FIRMWARE` matches this unit) via `research/capture_m0.py`, pointed
at PID `0x55a4`. The wrapper only calls `init_device`/`write_psk`/`run_driver` and refuses
unless firmware matches `WORKING_FIRMWARE` — no erase/flash path reachable.

**What worked (this is real progress):**
- `firmware_version()`, `get_iap_version()` → `MILAN_RTSEC_IAP_10027`.
- Zero-PSK white-box written (`preset_psk_write`) → `check_psk()` now True.
- `reset`, `read_otp`, `pov_image_check`, **`request_tls_connection` + GTLS handshake**,
  and **`upload_config_mcu` (over TLS)** all succeeded. So the TLS-PSK control plane works.
- `set_drv_state`, `mcu_get_pov_image`, `mcu_switch_to_fdt_mode` ran without error.

**Where it fails:** the first calibration image fetch:
```
clear_0_image = tool.decode_image(tls_server.stdout.read(7684)[:-4])   # driver_5503.py:165
  -> IndexError: index out of range   (tool.py:43, chunk[5])
```
`read(7684)` from the `openssl s_server -quiet` stdout returned fewer bytes than the
expected 7684-byte plaintext frame (80×64 → 5120 px, 12-bit packed = 7680 + 4). So the
device's TLS-encrypted image either didn't arrive in full, didn't decrypt to a full frame,
or the device returned a short/error packet to `mcu_get_image` under the 5503 image sequence.

**Corroborating oddity — unstable firmware string read:** across runs `firmware_version()`
returned **`GF3208_RTSEC_APP_10062`** (probe + run 1) then **`GF3258_RTSEC_APP_10062`**
(run 2) — a one-char difference (`32`**`0`**`8` vs `32`**`5`**`8`). Both match
`WORKING_FIRMWARE = GF32[0,5]8_RTSEC_APP_10062`. A flaky/garbled USB read could be the same
root cause as the short image read (USB transfer framing/timing).

**Candidate causes (unconfirmed — to investigate, NOT yet guessed-at with fixes):**
1. **Modern OpenSSL (3.6.2).** `s_server -nocert -psk … -quiet` may negotiate/buffer
   differently than when this driver was written (TLS1.3 vs TLS1.2 PSK, SECLEVEL). Control
   messages are short and worked; a full image record may expose a cipher/seclevel/framing
   issue. Possible probe: force `-tls1_2 -cipher '…@SECLEVEL=0'`, capture s_server stderr
   separately (currently merged into the image stream via `stderr=STDOUT`).
2. **Device returned a short/error packet** to `mcu_get_image` — the 5503 `DEVICE_CONFIG` /
   FDT calibration constants may not be exactly right for this `55a4`-PID unit even though
   the firmware label matches. Probe: log `len(device.mcu_get_image(...))` before feeding it
   to `s_server`.
3. **Timing/fragmentation.** Upstream `driver_5503.run_driver` sprinkles `stdout.flush()` +
   `sleep(0.1)` around the finger read — suggests a known fragile read; the clear-frame reads
   have no such guard.

Status: **paused at M0 image-fetch, awaiting decision on which diagnostic/tweak to pursue.**
No flash performed. PSK is now set on the device (expected, accepted by threat model).

---

## 2026-05-29 (cont.) — Diagnosis: it's the OpenSSL ↔ image-decrypt step, not the device

Two separate causes were found and the first was a red herring:

**(a) Orphaned `openssl s_server` (resolved).** My first failed run was SIGKILLed; its
`finally: tls_server.terminate()` never ran, so an `openssl s_server` stayed bound to
`:4433`. Every later run's fresh `s_server` then failed `BIO_bind: Address already in use`,
so the decryptor produced nothing → short/empty frame → `IndexError`. In the
`stderr=STDOUT` runs, that bind-error text WAS the "garbled frame". Fix added to
`research/capture_m0.py`: `clear_stale_tls_server()` pkills stray `openssl s_server` before
each run. Always clean up `:4433` between attempts.

**(b) Real blocker — TLS application-data decrypt fails on a clean port.** With `:4433`
free, the instrumented diagnostic (`research/diag_m0.py`) showed:
- `mcu_get_image` raw reply `len=7758`, head `00204a1e00000000` **`17 03 03 1e 40`** … —
  i.e. a 9-byte goodix header then a well-formed **TLS 1.2 Application Data record**
  (type `0x17`, version `0x0303`, length `0x1e40` = 7744). `raw[9:]` correctly starts at
  the TLS record. **So the sensor returns a complete, correctly-framed encrypted image —
  the USB/protocol/`[9:]` offset are all fine on this unit.**
- `openssl s_server` completes the relayed handshake and accepts config, but on being fed
  the image record it **closes the TLS session** (observed live: client socket `CLOSE-WAIT`,
  server `FIN-WAIT-2`) and writes **zero** plaintext to stdout → driver's
  `read(7684)` blocks forever (server process still alive ⇒ no EOF ⇒ hang).

Interpretation: modern **OpenSSL 3.6.2 defaults to TLS 1.3**, while goodix GTLS is
**TLS 1.2 + PSK**. The handshake/control path limps through, but the image application-data
record isn't decrypted by the negotiated session → s_server tears down. This is the known
"goodix-fp-dump on new OpenSSL" class of failure.

**Proposed fix (NOT yet applied — gated on user):** force the relay to TLS 1.2 with a
legacy PSK cipher and relaxed security level, e.g. spawn
`openssl s_server -nocert -psk <hex> -port 4433 -quiet -tls1_2 -cipher 'PSK-AES128-GCM-SHA256:PSK-AES256-GCM-SHA384:@SECLEVEL=0'`
(exact cipher TBD by what the sensor offers). Implement as an override in
`research/capture_m0.py` (don't edit the vendored driver yet). Alternative: replace the
`openssl` relay with a Python TLS-PSK implementation (sslpsk / a hand-rolled record layer).

**Tooling notes for future runs (cost me time):**
- Under `sudo`, the script could NOT open a log file under `/tmp` or `~/.claude`
  (`PermissionError`) — the sandbox restricts sudo writes there. Root writes under
  absolute paths under the invoking user's home and `/root/...` DO work. Print to stdout / read the bg task output file;
  don't have a sudo'd script write logs into `/tmp`.
- `os.path.expanduser("~")` under `sudo` = `/root`. Derive paths from `__file__` / `SUDO_USER` (see research/vault.py) for the vault.
- Kill BOTH the python and its `openssl s_server` child between attempts; pkilling only the
  python orphans the server on `:4433`.

Status: superseded — see cipher-fix attempt below.

---

## 2026-05-29 (cont.) — Cipher pin did NOT fix it; OpenSSL-relay path is the real wall

Applied the TLS1.2/PSK-cipher fix to the vendored `driver_5503.py` run_driver
(`-tls1_2 -cipher 'PSK-AES128-CBC-SHA256:@SECLEVEL=0'`) and also tested the broad
`-cipher 'PSK:@SECLEVEL=0'` via `research/diag_m0.py`. Confirmed via `pgrep` that openssl
actually receives the flags.

**Result:** still fails. With the broad PSK cipher, the diagnostic shows:
- sensor image reply `len=7758`, a well-formed TLS 1.2 record at `[9:]` (`17 03 03 1e 40` =
  appdata, len 7744). Sensor/protocol fine.
- `openssl s_server` produces **zero decrypted bytes** for the image, with **empty stderr**
  (no alert, no error) and the process stays alive. In the capture run the server instead
  went `FIN-WAIT-2` (closed the session). Either way: no plaintext, no diagnostic.

So this is **not** a cipher-selection problem. The legacy openssl-relay handshake (written
for OpenSSL 1.1.x) does not establish a usable TLS-PSK session against **OpenSSL 3.6.2** for
the bulk image record, and gives no error to work from. (Control commands like
`upload_config_mcu` go over plain USB, not TLS, so they succeed regardless and are not
evidence the TLS session works.)

**Environment options checked:**
- No `openssl-1.1` in Arch repos (only AUR `lib32-`/`mingw-` variants; no native CLI).
- Python 3.14 `ssl` HAS `set_psk_*_callback`, **but** it's backed by the same OpenSSL 3.6.2
  → likely the same wall (would need a MemoryBIO in-process server to rule out s_server
  quirks / the relay's `recv(1024)` truncation).

**Decision needed (paused, no flash, device healthy):** how to obtain the decrypted image.
- **A. Pure-Python GTLS/crypto** (à la `wrapless.py`). Robust, no OpenSSL dependency, and is
  what the eventual C libfprint driver (M3) needs anyway. BUT `wrapless` implements 53x5's
  *custom* GTLS (0xFF01–0xFF04 MCU handshake); 5503 appears to use *real* TLS-1.2-PSK
  (`request_tls_connection`, standard `17 03 03` records), so this is a real
  implementation effort, not a copy.
- **B. Legacy OpenSSL 1.1** (build from AUR/source) for the relay. Likely a quick win
  (tooling targeted 1.1.x), but adds a legacy dependency.
- **C. Deep-dive the OpenSSL-3 relay** (handshake record sizes / `recv(1024)` truncation /
  in-process MemoryBIO PSK server) to find and patch the specific incompatibility.

Reusable fix already in place: kill the `:4433` holder with `fuser -k 4433/tcp`, NEVER
`pkill -f "openssl s_server"` (it matches our own shell command line and suicides the job).

---

## 2026-05-29 (cont.) — SOLVED the TLS; new blocker: sensor returns all-zero images

**Big win:** replaced the openssl relay with an **in-process Python `ssl` TLS-PSK server
over MemoryBIO** (`research/capture_inproc.py`, `SharedTLS`). Handshake completes
(`cipher=PSK-AES128-CBC-SHA256`), and the device's image record **decrypts cleanly** (no MAC
error → correct key). This is the reusable crypto path (and the model for the M3 C driver).

**New blocker:** every decrypted frame is **all zero**. Proven with `research/diag_inproc.py`:
- ciphertext reply `len=7758` (real, non-zero encrypted bytes),
- decrypted plaintext `len=7684`, **all 0x00** (nonzero=0). No MAC failure → decryption is
  correct → the **sensor genuinely sent a blank frame**.
- True for `clear-0`/`clear-1` (no-finger calibration frames) AND `fingerprint-0`. So it is
  **not** a finger-placement issue.
- The `mcu_switch_to_fdt_down(..., reply=True)` "wait for finger" returned **immediately**
  (didn't gate on a finger), reinforcing that the imaging/FDT subsystem isn't producing data.

**Hypothesis:** this unit has the **55a4 USB PID** (whose `driver_55x4` programs an **88×108**
array) but runs **5503-family firmware** (`GF3258_RTSEC_APP_10062`, an **80×64** array). We
captured with the **5503** `DEVICE_CONFIG`/FDT/`mcu_get_image` sequence (to avoid flashing).
The TLS layer is generic so it works, but the **5503 image-acquisition config likely
mis-programs this unit's sensor array → blank reads**.

**Next experiments (all no-flash):**
1. Run the **55x4 image-acquisition sequence** (driver_55x4's `DEVICE_CONFIG`, FDT byte
   strings, 88×108 dims, 14260-byte reads) over the existing no-flash + in-process-TLS
   connection — i.e. call `driver_55x4.run_driver`-equivalent steps, never its `main()`
   (which flashes). Tests whether the 55a4 array needs the 55x4 config.
2. If neither stock config works: capture a **Windows USB trace** of the vendor driver to get
   the real init/config (golden sequence) — bigger effort.
3. Check goodix-fp-dump issues/Discord for "55a4 blank/zero image" reports.

Status: superseded — SOLVED below.

---

## 2026-05-29 (cont.) — M0 SOLVED: real fingerprint captured (no flash)

The blank-image hypothesis was correct. This unit is **55a4-PID hardware (88×108 array)
running 5503 firmware (GF3258)**. The winning recipe (`research/capture_55x4cfg.py`):

- **init + PSK** via `driver_5503` helpers, against PID `0x55a4` (no flash; firmware-guarded).
- **image acquisition** via **`driver_55x4.run_driver`** — its `DEVICE_CONFIG`, FDT byte
  strings, and **88×108** dimensions / 14260-byte reads — NOT the 5503 image config.
- **decryption** via the **in-process Python TLS-PSK server** (`capture_inproc.SharedTLS`,
  MemoryBIO, `PSK-AES128-CBC-SHA256`), monkeypatched into `driver_55x4`'s `socket.socket` /
  `subprocess.Popen`. No external `openssl`.
- **NEVER** calls `driver_55x4.main` (that flashes). No firmware was ever written.

Results (`$GOODIX_VAULT/frames/55x4cfg-2026-05-29/`):
- `clear-0`/`clear-1` (88×108): real baseline, mean ≈ 2612, max ≈ 3561 (of 4095).
- `fingerprint` (88×108): mean ≈ 2038, std ≈ 584.
- **`clear − fingerprint`** (baseline subtraction) shows **clear ridge structure** — a
  visibly real fingerprint. Finger covered ~upper-left of the array; a flatter/centred
  press would fill more of the 88×108 frame.

Notes:
- The 55x4 FDT-down DOES gate on a real finger (unlike the 5503 sequence, which returned
  instantly) — so it blocks at "Waiting for finger..." until contact.
- Baseline subtraction (clear − finger) is the right preprocessing; raw frames have row
  striping + gradient.

Next: M1 — feed this frame to NBIS (`mindtct`) to decide optimistic vs fallback recognition.

---

## 2026-05-29 (cont.) — M1 result: leaning OPTIMISTIC (NBIS finds real minutiae)

Installed NBIS (AUR `nbis`: `mindtct`, `bozorth3`, `cwsq`, `nfiq`). `research/nbis_test.py`
pipeline: normalize 8-bit → **pad to 320×320** (cwsq/WSQ require ≥256×256) → `cwsq` → WSQ →
`mindtct -m1` (.xyt) + `nfiq`. Ran 4 variants on capture #1 (88×108 partial print):

| variant   | minutiae | q≥40 | nfiq(1=best) |
|-----------|---------:|-----:|-------------:|
| raw       | 19 | 9  | 3 |
| raw_inv   | 16 | 11 | 3 |
| diff      | 13 | 2  | 2 |
| diff_inv  | 13 | 2  | 1 |

Overlay (`minutiae_raw_inv.png`) confirms minutiae fall **on the ridge field, not in the
padding** — genuine detections. So **NBIS does extract usable minutiae from our frames** →
the optimistic path (libfprint's bundled NBIS/Bozorth matcher) is plausible; we likely do
NOT need a custom SIFT matcher for v0.

Caveats (why "leaning", not certain): counts are modest (9–11 strong) because the print was
**partial** (finger covered ~part of the 88×108 array) and raw frames have **row-stripe
noise**. `diff` (baseline-subtracted) is cleaner (nfiq 1–2) but its ridged area is smaller →
fewer minutiae. A fuller, centred capture + baseline subtraction should raise counts.

The real proof is M2: capture several prints of the same finger + impostors, run `bozorth3`,
and check genuine-vs-impostor score separation (FAR/FRR). Until those separate cleanly,
treat optimistic as provisional.

---

## 2026-05-29 (cont.) — M2 first pass: matching does NOT separate yet

Captured 3 genuine (right index, `m2-genA-1..3`) + 2 impostor (right middle, `m2-impB-1..2`),
ran `research/m2_eval.py` (raw → norm8+invert → pad320 → cwsq → `mindtct -m1` → `bozorth3`
all pairs). Minutiae per print 12–24. Scores (higher=more similar):
- genuine (same finger): 9, 5, 4, 0
- impostor (diff finger): 15, 8, 6, 3, 0, 0  ← overlaps; impostor max (15) > genuine max (9)

**VERDICT: OVERLAP / no separation.** All scores are at the noise floor (a confident
bozorth3 match is ~40+). So naive raw→NBIS matching is not trustworthy yet. This tempers
the M1 optimism: minutiae exist, but they don't correspond between captures.

Likely causes (fixable, in priority order):
1. **Contaminated baselines (operator error):** captures were taken with the finger HELD
   continuously, so it was also present during the `clear-*` baseline frames → baseline
   subtraction ≈ 0. Correct protocol: finger OFF during clear frames, ON only at
   "Waiting for finger...". Then use `clear − finger` clean ridges (M1 showed NFIQ 1–2).
2. **Small, position-varying partial prints:** 88×108 with the finger landing differently
   each time → little minutiae overlap between genuine pairs. Need fuller, consistent
   placement (and possibly capture-time alignment / more samples).
3. **Minimal preprocessing:** only norm8+invert. The design anticipated CLAHE/local
   normalization for these noisy small sensors — not yet applied.

Next: recapture with the correct finger-off-baseline protocol, add CLAHE preprocessing,
capture more genuine samples with fuller coverage, then re-run `m2_eval.py`. If genuine
still doesn't clear impostor, escalate to the custom-matching fallback (SIFT/CLAHE) per the
design. Tools/scripts (`m2_eval.py`, `nbis_test.py`) are reusable as-is.

---

## 2026-05-29 (cont.) — M2 second pass: SIGNAL FOUND (clean baselines + CLAHE-on-diff)

Captured a clean round with a quality gate (`research/capture_m2.py`, wrapping the no-flash
recipe): 5 genuine (right index, `m2g-genidx-1..5`, varied placement) + 3 impostor (right
middle, `m2g-impmid-1..3`). Gate enforces, per capture: clean baseline (`clear-0` mean
≥2200; finger OFF during calibration), real ridge signal (`clear−finger` std ≥80), and
coverage ≥30%. All 8 passed (coverage 55–96%). Added a pure-numpy **CLAHE** to
`nbis_test.py` and made `m2_eval.py` test 5 preprocessing variants + report the
biometrically-correct **per-probe best-match** metric (a probe is matched against ALL other
same-finger frames, best score wins — what libfprint enrollment does).

**Result — variant `clahe_diffi` (CLAHE on `clear−finger`, inverted):**
- genuine overlapping pairs score up to **20** (e.g. genidx-1↔2 = 20, genidx-1↔5 = 13);
- **no impostor pair exceeds 6** → margin **14**; a bozorth3 threshold ~7–13 separates.
- 4/8 probes match their own finger above any impostor. The 4 misses are **non-overlapping
  partial prints** (genidx-3 low / genidx-4 side imaged regions the centred cluster never
  touched; the 3 middle-finger presses didn't overlap *each other* either → score 0). That
  is a **coverage gap, not matcher failure** — minutiae DO correspond when prints share a
  region.

**Key diagnostic (why pass 1 failed, pass 2 worked):**
- **raw** variants now show a *negative* margin (impostor 11–23 > genuine 8): the sensor's
  fixed-pattern/structural noise is identical across every capture, so matching raw frames
  inflates IMPOSTOR scores. **Baseline subtraction (`clear − finger`) removes the fixed
  pattern** → only `*diff*` variants separate. Pass 1 used raw frames AND contaminated
  baselines → guaranteed noise-floor. Pass 2's clean-baseline protocol + CLAHE-on-diff is
  the fix. (clahe_diff margin=9, clahe_diffi margin=14; both diff-based variants win.)

**VERDICT: provisionally OPTIMISTIC.** NBIS/Bozorth IS viable on this sensor — overlapping
same-finger prints clear all impostors by ~3×. Recognition is NOT the wall; partial-print
**coverage** is. Path to a trustworthy FAR/FRR: enroll multiple frames per finger (libfprint
already does this) and/or guide fuller placement; preprocessing = `clear−finger` → CLAHE →
invert → pad → cwsq → mindtct → bozorth3, threshold ~10. Did NOT build a custom SIFT/CLAHE
matcher (not needed on this evidence). Stopped here per user's "stop and report" choice.

Reusable: `research/capture_m2.py` (gated capture), `nbis_test.clahe`, `m2_eval.py`
(per-probe best-match verdict + auto contamination gate). Data: `$GOODIX_VAULT/frames/m2g-*`.

---

## 2026-05-30 — M2 THIRD pass (proper FAR/FRR): NBIS/Bozorth does NOT separate — fallback

The optimism above was a **small-sample artifact**. Ran a real biometric eval
(`research/m2_far_frr.py`): a tiled enrollment **gallery** (10 deliberately-tiled right-index
touches + pooled prior clean right-index = **17 gallery frames**), **4 held-out genuine
probes** (natural placement), and **10 diverse impostors** (left index/middle/ring ×2 + prior
right-middle). All 20 new captures passed the quality gate (coverage 46–96%). Matcher =
the "winning" `clahe_diffi` pipeline, best-of-gallery (what libfprint does).

**Result — genuine and impostor distributions OVERLAP badly:**
- genuine probes (best vs 17-frame gallery): **12, 10, 0, 0**
- impostor probes (best vs same gallery): **22, 20, 10, 10, 9, 7, 6, 5, 5, 0**
- **impostor max (22, left-ring; 20, right-middle) > genuine max (12).** No usable
  threshold: FAR hits 0 only at T=23, where FRR=100%. EER ≈ 40–50% — essentially random.

**Ruled out (so this is robust, not a tuning miss):**
- *Spurious/border minutiae from CLAHE+padding?* Filtered minutiae by mindtct quality
  (q≥0…50): the false impostor matches use HIGH-quality minutiae; filtering kills genuine
  too. No quality threshold separates (always FRR=100% at FAR=0). Not noise.
- *Small-N luck?* This is the opposite — going from 3 to 10 diverse impostors is what
  exposed the overlap the first round missed.
- Padding offset / rotation: bozorth3 is translation/rotation invariant; not the cause.

**Conclusion:** minutiae matching (NBIS `mindtct`+`bozorth3`) is **insufficient on this small
88×108 partial sensor** — too few reliable corresponding minutiae per partial; chance
alignments between *different* fingers score as high as true matches. This is exactly the
reason CLAUDE.md flagged ("Goodix sensors are small/noisy, which is why goodixtls went to a
custom SIFT+CLAHE matcher"). The premature M1/M2 optimism is now corrected by a proper
FAR/FRR test — **the libfprint bundled-matcher (`bz3_threshold`) shortcut is OFF the table.**

**Path forward = the design's documented fallback:** a **correlation / keypoint matcher
(SIFT/ORB + CLAHE)** instead of minutiae — the goodixtls community approach for these
sensors. We're well-positioned: capture pipeline is rock-solid (20/20 clean), and we now
have a **labelled benchmark set** (`m2c-*` gallery/probe/impostor + pooled `m2g-*`/`m2-*`,
31 clean frames) and a correct FAR/FRR harness (`m2_far_frr.py`) to develop and score it
against — no more presses needed to iterate the matcher. (Possible secondary factor to
check during that work: the assumed **500 PPI** in the pipeline — if the true sensor
resolution differs, minutiae geometry tolerances are miscalibrated; but SIFT/correlation is
the indicated direction regardless.) Stopped here per the user's "stop and report" choice.

Reusable from this pass: `research/m2_far_frr.py` (gallery/probe/impostor FAR-FRR sweep,
role-by-label, auto contamination skip). The `m2c-*` set is the dev/benchmark corpus.

---

## 2026-05-30 (cont.) — M2 fallback WORKS: SIFT + CLAHE + RANSAC separates (FAR=0)

Built the design's fallback matcher (`research/sift_match.py`): per frame `clear−finger`
→ percentile-normalize → ×4 upscale → **cv2 CLAHE** → **SIFT** keypoints/descriptors; match
two prints by Lowe ratio-test (0.75) good matches → **`estimateAffinePartial2D` RANSAC**
(reproj 8.0) → **score = geometric inlier count**. Evaluated on the SAME corpus/harness as the
failed NBIS run (17-frame gallery, 4 held-out genuine probes, 10 diverse impostors,
best-of-gallery).

**Result — clean separation (vs NBIS's overlap):**
- genuine probes (inliers vs gallery): **43, 25, 5, 3**
- impostor probes: **4, 4, 4, 4, 4, 3, 3, 2, 2, 2** — every impostor ≤4.
- At **T=5 inliers: FAR=0, FRR=25%.** Genuine overlaps reach 25–43; impostors pinned ≤4
  because random keypoint matches don't survive a single global affine (RANSAC kills them).
  Contrast NBIS bozorth3 on the same data: impostor max 22 > genuine max 12 (≈random).

The lone FRR miss (`m2c-prb-1`, 3 inliers) scores ≤3 against **every** gallery frame — a true
enrollment-coverage gap (that natural touch hit a region/rotation the 10 tiled frames didn't
cover), not a discrimination failure. Top-k gallery fusion doesn't help (lifts impostors
proportionally). So FRR is reducible via denser enrollment + libfprint's retry-on-reject;
discrimination (FAR) is already solid.

Tuning (grid on m2c): ratio **0.75** + reproj **8.0** is the sweet spot — looser ratio
(0.8–0.85) raises genuine inliers but lifts impostors to 8–16 (worse FAR). Scale ×4 and ×6
equivalent. ORB untested (SIFT sufficient).

**VERDICT: SIFT/CLAHE/RANSAC is the viable matcher for this sensor.** Recognition path is
unblocked. M3 implication: the libfprint C driver should NOT use the bundled NBIS/bozorth
image-matcher; it needs this SIFT-inlier matcher (libfprint has no built-in SIFT → either a
non-image custom `FpDevice` that does SIFT matching, or vendor the matcher). Next: improve FRR
(denser gallery / score model), confirm sensor **PPI**, then design M3 around a custom matcher.

Deps: added `opencv-python-headless` (4.13) to the venv. Tool: `research/sift_match.py`
(`--orb`, `--raw`, `--scale N`). Corpus unchanged (`m2c-*` + pooled `m2g-*`/`m2-*`).

---

## 2026-05-30 (cont.) — Sensor PPI measured: ~600 (not 500); patch ≈ ¼ fingertip

Measured the true resolution from ridge spacing (`research/measure_ppi.py`): averaged,
zero-padded 2D-FFT radial spectrum over 19 clean right-index frames → dominant ridge period
**≈ 11.4 px**. With adult ridge wavelength 0.40–0.50 mm (central 0.46): **PPI ≈ 577–721, best
~600–627** — clearly NOT the 500 first assumed. Updated `research/nbis_test.py` `PPI=600`
with the derivation in a comment.

**Physical consequence (the useful part):** at ~600 PPI the 88×108 array images only
**≈ 3.6 × 4.4 mm** — roughly a *quarter* of a fingertip (~15×20 mm). This is the root reason
partial-overlap/coverage dominates: two touches must land within a few mm to share ridges.
It quantifies why enrollment must tile the finger across many frames and why a probe landing
on an un-enrolled quarter scores ~0 (the FRR misses).

**Did the wrong PPI sink NBIS? No — checked.** Re-ran the NBIS FAR/FRR at `PPI=600`:
**identical** scores (impostor max 22 > genuine max 12). In this pipeline PPI is only WSQ-
header metadata — it doesn't resample the pixels `mindtct`/`bozorth3` see (we upscale/pad by
fixed factors), so it can't change the matching outcome. The "NBIS insufficient" verdict is
robust. SIFT is scale-invariant and never needed PPI. So PPI is now correctly documented but
changes no result; its value is the enrollment-strategy insight above.

---

## 2026-05-30 (cont.) — Probe-failure diagnosis: it's placement/region, not mis-recognition

Inspected the two genuine probes that scored low (ridge-image montage `_diag_probes.png`,
vault-only). The SIFT discrimination is strong (the 2 clean probes scored 43/25 vs impostor
≤4); the misses are explainable and fixable, NOT the matcher confusing the user with someone
else:
- **`m2c-prb-1`** (3 inliers, the only fail at T=5): a clean, high-coverage press (70%, 171
  keypoints) — but of the **core** (concentric/whorl-centre ridges), a region the tiled
  gallery didn't enrol. Good press, uncovered region → scored ~0 vs every gallery frame.
- **`m2c-prb-3`** (5, scraped through): **partial contact** — left ~⅓ of the frame is dark
  (finger not flat). A sub-optimal press.
- The two PASSes (`prb-2`,`prb-4`) are full-contact **flat-pad** presses (parallel ridges,
  92–96% coverage) → matched emphatically.

So FRR here is dominated by placement/coverage, and n=4 is far too small to quantify it
anyway (1/4 ≈ no information). **M3 enrollment-design implications:** (a) the enrol gallery
must explicitly cover the **core**; (b) the flat pad matches most reliably; (c) login should
allow **retry-on-reject** (per-session FRR ≪ per-touch FRR). Discrimination (FAR) is the hard
part and it's solved; FRR is a tuning/UX matter for the driver. **M2 concluded → M3.**

---

## 2026-05-30 — M3a/M3b-offline progress (build env + SIGFM in C)

**M3 strategy = validate-then-clean** (spec `docs/superpowers/specs/2026-05-30-...-m3-driver-design.md`,
plan `docs/superpowers/plans/2026-05-30-...-m3-driver.md`).

**Build env (Arch, 2026):** the community fork `TheWeirdDev/libfprint@55b4-experimental`
(libfprint 1.94.6) **builds cleanly on the current toolchain** after two fixes: install
`glib2-devel` (modern Arch split `glib-mkenums`/`glib-genmarshal` out of `glib2`), and pass
`-Dc_args=-Wno-deprecated-declarations` (OpenSSL-3 legacy calls). Configure with
`-Ddrivers=goodixtls55x4 -Dintrospection=false -Dudev_rules=disabled -Ddoc=false
-Dgtk-examples=false -Dprefix=<local>`. All 102 targets link (libfprint-2.so, libsigfm.a,
examples/img-capture). `27c6:55a4` is in `goodix55x4.h` id_table. Activate path audited
**flash-free** (states READ_AND_NOP→ENABLE_CHIP→NOP→CHECK_FW_VER→CHECK_PSK→RESET→
SET_MCU_IDLE→SET_MCU_CONFIG; the only OTP call is commented out; config upload is volatile).
Relaxed the strict firmware `strcmp(GF3268_RTSEC_APP_10041)` → `!strstr("_RTSEC_APP_")` to
tolerate our unit's varying `GF32xx…_10062`.

**SIGFM validated in C on our M2 corpus** (`research/sigfm_c/`: vendored LGPL `sigfm.{cpp,hpp}`
+ `harness.cpp`; preprocess = clear−finger→norm→×4→CLAHE, like `sift_match.py`; SIGFM does
SIFT+Lowe0.75+min_match5+angle-filter, **no internal CLAHE**). Result over the 10-gallery /
4-probe / 6-impostor `m2c-*` set:
- **All 6 impostors → score 0. FAR = 0.** (Stricter than our RANSAC, which gave impostors ≤4.)
- Genuine: `prb-2`=34781, `prb-4`=9861, **`prb-1`=0, `prb-3`=0**. So at any T≥1: FAR=0, FRR=50%.
- The two genuine zeros are EXACTLY the M2-diagnosed coverage misses (`prb-1` core/whorl,
  `prb-3` partial contact). SIGFM's geometric filter zeroes partial-overlap probes rather than
  scoring them low → **FRR is more coverage-sensitive than RANSAC.**
- **Shippable threshold ≈5** (community default; huge margin: genuine 9861/34781 vs impostor 0).
- **3b levers for FRR:** tiled enroll that covers the core + retry-on-reject (M2 lesson), and
  possibly a slightly relaxed `min_match` for this tiny 88×108 sensor. Discrimination is solved.

**3a hardware run (C `img-capture` on our 55a4, no-flash):** the **transport is validated**.
Log shows: firmware `GF3268_RTSEC_APP_10062` accepted (relaxed gate), volatile config uploaded
(no flash), PSK accepted, **TLS handshake completed (`HANDSHAKE DONE`)**. Then the **empty
(no-finger) scan failed**: `SCAN_EMPTY` state 0 ran command `0x20`, the device replied with a
`0xd0` TLS-data packet the non-TLS proto parser flagged "Invalid protocol command: 0xd0", and
`0x20` timed out (`failed to scan: Command timed out: 0x20`). This is **before** the finger
stage — pressing is irrelevant to this failure.
- **Diagnosis:** the post-handshake image-read / FDT scan sequence (and possibly the
  `goodix_55x4_config` bytes) in the 55b4 fork doesn't match our 5503-firmware 55a4 unit. This
  is the exact area our Python `driver_55x4`/`driver_5503` tuned (the known flaky-FDT gating).
- **Conclusion:** the hard, highest-risk part (USB + no-flash + PSK + GTLS in C on this unit)
  is **proven working**. The literal "C-captured frame" is blocked only by the scan-read
  command sequence, which 3b rewrites by porting our working Python. Combined with (a) the
  hardware is already known to capture (Python, M0/M2) and (b) SIGFM proven in C (above), the
  3a de-risk goal is substantially met. Not rabbit-holing into the throwaway fork's flaky FDT
  path; carry the scan/config reconciliation into 3b (spec already lists these constants).

## 2026-05-30 — M3 Phase B: C transport reads encrypted frames (0x20 SOLVED)

The 3a `0x20 → 0xd0 → timeout` is **resolved** in the clean driver
(`vendor/libfprint` @ `goodix55a4-m3`, commit `feec9a6`). The `0xd0` reply to
`GET_IMAGE` was the device saying *"(re)establish TLS"* — it did not accept the
session for data. Three fixes, in order of impact, were all required (3a had
reached none of them):

1. **Scan order.** Every `GET_IMAGE (0x20)` must be preceded by `FDT_MODE
   (0x36)`, exactly as `driver_55x4.run_driver`. The 55b4 fork issued `0x20`
   from `SCAN_EMPTY`/`CALIBRATE` *before* any FDT switch — that alone times out.
2. **TLS server = in-process OpenSSL MemoryBIO**, not the fork's socketpair +
   background-`SSL_accept`-thread relay. With `@SECLEVEL=0` (required on OpenSSL
   3.6 for the legacy PSK-CBC-SHA256 suite) and the 32-zero-byte PSK. This is
   exactly what `research/capture_inproc.py` documents: the socket-relay style
   "produces no usable session on OpenSSL 3.6."
3. **Do NOT send `TLS_SUCCESSFULLY_ESTABLISHED (0xd4)`.** The proven Python path
   (`tool.connect_device`) never sends it; the fork does. Sending `0xd4`
   immediately after the handshake makes *this* device drop the session and
   reply `0xd0` to subsequent image reads. **This was the last and decisive
   fix.** (Plus a ~20 ms post-handshake settle, matching Python's `sleep(0.01)`
   "Important otherwise an USBTimeout".)

Constant note: the device-side `goodix_55x4_config` (239 B in the fork) was a
red herring — the **256-byte** `DEVICE_CONFIG` from `driver_55x4.py` is ground
truth and pairs with the Python FDT-mode/FDT-down payloads.

**On hardware (this 55a4):** full no-flash activate (`0x00/0xa8/0xe4/0xa2/0x82/
0xa6`), TLS handshake, `UPLOAD_CONFIG (0x90)`, then **`clear-0` and `clear-1`
frames read and TLS-decrypted** (two `Got TLS data msg`, 88×108, 12-bit). The
finger frame uses the identical read path after `FDT_DOWN (0x32)`; capturing it
just needs the sensor to register a sustained gentle touch (a quick tap is below
the FDT threshold). No flashing; only reads + volatile config upload + TLS.

**UPDATE (gate passed):** finger frame captured in C off this 55a4 — FDT-down registered a
sustained touch, finger frame read+decrypted (state 17), `CAP_NUM_STATES completed
successfully`. clear−finger **std 348.9**, **94.9% coverage** (M2 gates: std≥80, cov≥30%);
rendered (norm→×4→CLAHE) shows clean ridge/valley structure. **Phase B done.**

## 2026-05-31 — M3 Phases C–G: full driver, enroll+verify end-to-end

Phases C–F committed on the fork branch (`c7c7e26` preprocess, `c7b824c` SIGFM,
`4cc05e5` enroll, `a5fff5f` verify/identify, `23a85ca` set_type fix + replay
hook). The whole driver builds into stock libfprint 1.94.10 and every offline
gate passes:
- **C preprocess** matches the Python pipeline within max|Δ|=3/255.
- **D SIGFM** on a real C-captured frame: 286 keypoints, self-match 76M.
- **E template** aay-gallery round-trips through serialized bytes and re-matches.
- **G end-to-end:** `examples/enroll` (12 stages) writes a 1.85 MB 12-template
  gallery to `test-storage.variant`; `examples/verify` loads it and our verify
  vfunc reports **MATCH (score 76248176 ≥ 10)** through the real
  `fp_device_verify` flow. enroll→store→load→verify→match→report all work.

**Test aid:** `GOODIX55A4_REPLAY_DIR` env loads `clear-0.pgm`/`fingerprint.pgm`
from a dir instead of capturing (deterministic enroll/verify; reused the Phase-B
frame so the above needed zero touches).

**FAR/FRR:** discrimination already proven offline (3b-offline harness: 6
impostors all score 0 → FAR=0; genuine cross-captures match). The verify vfunc
uses the same `sigfm_match_score`, so impostor rejection holds by construction.

**Open (live hardware):** a LIVE verify capture matching the enrolled template,
and a live impostor for an on-device FAR number. Both are blocked only by
finicky FDT touch-registration in background runs — the capture path itself is
identical to the Phase-B capture that succeeded (handshake + both clear frames
decrypt every time; it just times out waiting for the physical finger). A
single sustained hold worked in Phase B; relevant to watch for M4 (PAM).

---

## 2026-06-24 — Live cross-capture: matcher OK, enroll does not tile (FDT_UP fix)

**How found:** first live genuine-verify attempts through the real driver
(`examples/enroll` + `examples/verify` on the isolated build). Three live
verifies all returned `best score 0`. Debugged offline against ground truth.

**Findings:**

- **Matcher is NOT the problem.** Captured two distinct live presses of the same
  right-index region (`img-capture` + `GOODIX55A4_DUMP_DIR`). Scored them with the
  *proven* M2 pipeline (`research/sift_match.py`: clear−finger → norm → ×4 → CLAHE
  → SIFT + RANSAC):
  - self-match A vs A = 444 inliers (sanity OK)
  - **genuine A vs B (cross-capture, same finger) = 2 inliers** → effectively no match.
  So two freehand presses of the *same* spot do not match even in ground truth.
- **Physical overlap measured:** zero-mean NCC of the two clear−finger frames peaks
  at **0.47** at a shift of dx=−2, **dy=−12** px (88×108 frame). They overlap, but
  the ~12 px offset on a ¼-fingertip sensor leaves too few corresponding SIFT
  minutiae. Consistent with M2's FRR≈25% even with a real gallery: single-region
  enroll vs one probe is unreliable by nature.
- **Enroll does not actually tile (driver bug).** Timing of a live 12-stage enroll:
  stage 1's `FDT_DOWN` (0x32) blocked ~26 s (waiting for the press), but stages
  2–12 each completed in <1 s. `FDT_DOWN` returns *immediately if a finger is still
  present*, so all 12 stages captured the same held region → the gallery covers one
  spot, not the fingertip. No verify press can reliably overlap a one-region gallery.

**Fix (identified, not yet implemented):** between enroll stages, wait for the
finger to *lift* before arming the next `FDT_DOWN`. The transport already exposes
the primitive: `goodix_send_mcu_switch_to_fdt_up` /
`goodix_send_mcu_switch_to_fdt_up_no_reply` (`GOODIX_CMD_MCU_SWITCH_TO_FDT_UP`
0x34, the inverse of 0x32). Sequence per stage: capture → FDT_MODE → FDT_UP
(blocks until lift) → next stage's FDT_DOWN. Then a real tiled enroll (~12
lift-and-tap, deliberately spread) builds a multi-region gallery so a verify press
overlaps some frame (~75%/press per M2; libfprint verify retries cover the rest).
Note: 0x34 is NOT on the proven `run_driver` path (driver_55x4 captures once), so
its payload/timing is a small unknown to validate on first use.

**Not a regression in:** capture (live frames have std ~5960, 392–444 SIFT
keypoints), TLS, preprocess, or SIGFM extract — all confirmed working live.
