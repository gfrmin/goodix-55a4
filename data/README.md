# data/ — biometric data (NEVER committed)

Raw captured fingerprint images and enrolled templates live here. **Everything in this
directory except this README is gitignored and must never enter git history** — it is
biometric data.

`research/` and `eval/` read from here locally. fprintd's own templates live in
`/var/lib/fprint` (`0700`), not here.

Before any `git add -A`, confirm nothing from `data/` is staged.
