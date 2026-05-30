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
  `/home/<user>/...` and `/root/...` DO work. Print to stdout / read the bg task output file;
  don't have a sudo'd script write logs into `/tmp`.
- `os.path.expanduser("~")` under `sudo` = `/root`. Hardcode `/home/<user>/...` for the vault.
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
