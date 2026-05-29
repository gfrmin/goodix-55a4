# research/ — Python prototyping (Milestones 0–2)

Fast-iteration Python for the parts we don't yet understand: capture, the NBIS-vs-custom
recognition fork, preprocessing, and (if needed) a fallback matcher. Once an approach is
proven and measured (`eval/`), it gets ported into the C libfprint driver (`vendor/libfprint`).

## Environment

```sh
# system deps (Arch)
sudo pacman -S --needed usbutils wireshark-cli   # lsusb, tshark for captures
# meson ninja later, for the M3 libfprint build

# python env
cd ~/git/goodix-55a4
uv venv && source .venv/bin/activate
uv pip install pyusb pillow numpy opencv-python   # opencv for CLAHE/SIFT in the fallback path
```

Note: `usbmon` is needed for USB captures: `sudo modprobe usbmon`.

## Milestone 0 — prove capture (start here)

Goal: pull **one raw frame** off this specific `27c6:55a4` and look at it.

1. Set up `vendor/goodix-fp-dump` (see `vendor/README.md`).
2. Install its `requirements.txt` into the venv.
3. Run its `55a4` entry point (needs USB access — run as root or via a udev rule):
   ```sh
   cd vendor/goodix-fp-dump && sudo python run_55a4.py
   ```
4. Save the resulting frame into `../data/` (gitignored) and open it.

**Expected failure modes** (note them in `docs/protocol/`): handshake stalls on a
different firmware rev; wrong config/PSK; permissions. The upstream Discord is the place
to compare notes.

## Milestone 1 — the recognition fork test

The decisive experiment (see design spec). With a few captured frames:

- Feed an image to libfprint's minutiae extractor (NBIS) and check whether it finds usable
  minutiae. If yes → optimistic path (libfprint matches for us in M3).
- If no → preprocess (CLAHE, normalization) and retry; if still poor, prototype the
  custom SIFT-based matcher (the goodixtls approach) here.

Output of M1: a script that enrolls + matches *your* finger, plus notes on which path won.

## Milestone 2 — tune + evaluate

Move scoring into `eval/`. Don't proceed to PAM until FAR/FRR look sane.

## AI-assisted tooling (optional, opportunistic)

Where an LLM helps (analyzing capture diffs, proposing preprocessing params, drafting the
dissector), keep those helpers here. Point local models at the **`steel`** ollama server,
not localhost.
