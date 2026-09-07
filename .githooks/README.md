# .githooks — structural PII guard

`pii_check.py` is a vendored copy of the guard from the author's `life-agent`
repo. It enforces an **allowlist of safe shapes**: tracked text may only contain
data shaped like obviously-synthetic values, and anything shaped like a real
personal path, email, or ID is rejected. That catches *novel* leaks, unlike a
denylist of known strings.

Enable it (once per clone):

    git config core.hooksPath .githooks

Then `pre-commit` scans staged blobs and `pre-push` scans every blob in every
pushed commit — not just the net diff — so nothing reaches a public remote via
an intermediate commit.

This repo runs the guard in `--shapes-only` mode: shapes plus the email
allowlist, with no private name denylist. A clean run therefore proves no
personal *paths/IDs/emails* leaked; it does not vet names.

Ad-hoc scan of the whole tree:

    python3 .githooks/pii_check.py --shapes-only

Config, both public by design:

- `pii-path-allow.txt` — path roots that may appear in tracked text. The real
  vault location is never listed; it lives in `$GOODIX_VAULT` or the gitignored
  `.vault-path`.
- `pii-allow.txt` — email domains that may appear (clone URLs, attribution).

A reviewed false positive can be marked with `PII-OK` on the line. Use sparingly.
