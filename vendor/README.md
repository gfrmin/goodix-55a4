# vendor/ — forked upstreams

The two upstream projects are brought in here as **git submodules pointing at your own
forks**, so you can branch freely and open PRs upstream when ready. The trees themselves
are gitignored (see `.gitignore`); only this README is tracked.

## What goes here

| Submodule | Upstream | Role |
|---|---|---|
| `goodix-fp-dump` | https://github.com/goodix-fp-linux-dev/goodix-fp-dump | Capture / RE (Python). Has `run_55a4.py`, `driver_55x4.py`. Milestones 0–2. |
| `libfprint` | https://github.com/goodix-fp-linux-dev/libfprint | The C driver lands here as an `FpImageDevice`. Milestones 3–4. |

## Setup

1. **Fork both** on GitHub (`gh repo fork ...` or the web UI).
2. Add as submodules pointing at *your* forks:

```sh
cd ~/git/goodix-55a4
git submodule add git@github.com:<you>/goodix-fp-dump.git vendor/goodix-fp-dump
git submodule add git@github.com:<you>/libfprint.git       vendor/libfprint
git -C vendor/goodix-fp-dump submodule update --init --recursive   # it has a firmware submodule
```

3. Track upstream so you can pull their fixes:

```sh
git -C vendor/goodix-fp-dump remote add upstream https://github.com/goodix-fp-linux-dev/goodix-fp-dump.git
git -C vendor/libfprint       remote add upstream https://github.com/goodix-fp-linux-dev/libfprint.git
```

> If you just want to start fast without forking yet, clone the upstreams directly into
> these paths and convert to forked submodules later. Either way the trees stay
> gitignored.

## Contribution flow

Work on branches in your fork → open PR to `goodix-fp-linux-dev`. Keep this repo's
research/dataset out of those PRs.
