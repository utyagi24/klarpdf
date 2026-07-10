# Contributing to KlarPDF

Thanks for your interest in KlarPDF — a local, offline, native-Windows PDF viewer + page editor
(Python · PySide6 · PyMuPDF). This guide covers how to set up a dev environment, run the test suite,
and get a change reviewed and merged. Please also read [`CLAUDE.md`](CLAUDE.md) for the project
orientation and conventions, and [`PLAN.md`](PLAN.md) for the product spec and architecture.

## Licensing of contributions

KlarPDF is licensed **`AGPL-3.0-or-later`** (see the root `LICENSE` file). By contributing, you agree
that your contributions are licensed under the same `AGPL-3.0-or-later` terms. Do not contribute code
you are not entitled to license this way.

## Developer Certificate of Origin (DCO) — sign off every commit

This project uses the **Developer Certificate of Origin (DCO) 1.1** instead of a CLA. Every commit
must carry a `Signed-off-by` line certifying that you wrote the change (or otherwise have the right to
submit it under the project's licence). Add it automatically with the `-s` / `--signoff` flag:

```bash
git commit -s -m "fix: correct thumbnail sizing"
```

That appends a line using your `git` name and email, for example:

```
Signed-off-by: Jane Developer <jane@example.com>
```

Pull requests whose commits are not signed off cannot be merged. If you forgot, amend the last commit
with `git commit --amend -s` (or rebase to sign off a series) and force-push your branch.

<details>
<summary>Developer Certificate of Origin 1.1 (full text)</summary>

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

(See also <https://developercertificate.org/>.)

</details>

## Development environment

KlarPDF is developed **hybrid**: the cross-platform core (`model/`, `viewer/`, `organize/`) and the
headless test suite run in **WSL** (Ubuntu, Python 3.12), the GUI iterates via **WSLg**, and only
packaging + Windows shell-integration require **Windows**. See `PLAN.md` §Development environment for
the full picture.

### Set up the WSL dev venv and run the tests

```bash
# one-time: base Ubuntu python lacks ensurepip
sudo apt install -y python3.12-venv

python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt   # same pinned == versions as the ship lock, no hashes
invoke test                           # headless suite (offscreen Qt) — or run `pytest` directly
invoke --list                         # all build/release tasks: test · audit · lock · build · tag · publish
python launcher.py file.pdf           # run the GUI via WSLg
```

Notes:

- The dev venv installs from **`requirements-dev.txt`** (version-only, no hashes). The hashed,
  vendored, offline lock (`requirements-win.txt`) is the **Windows ship** artifact only —
  `pip install --require-hashes` fails on Linux by design, because the Windows lock pins `win_amd64`
  wheel hashes and pip resolves `manylinux` wheels on Linux. See `CLAUDE.md` §Gotchas and
  `DEPENDENCIES.md`.
- **Windows-only** work (building the installer, single-instance / focus / file-association
  validation) needs **python.org 3.12.x** and Inno Setup 6 on a real Windows machine — see
  `README.md` §Build the Windows installer and `RELEASE.md`.

### Run the test suite

`invoke test` (or `pytest`) runs the full headless suite; it must be green before you open a PR. CI
runs the same suite on every PR (`.github/workflows/test.yml`), plus a weekly dependency audit
(`audit.yml`).

## Branch, commit, and PR conventions

- **Always branch from an up-to-date `main`.** `git fetch origin && git switch -c <name> origin/main`.
  Use a prefixed branch name: `feat/…`, `fix/…`, `docs/…`.
- **One PR per logical unit.** Keep changes focused and reviewable.
- **Sign off every commit** (`git commit -s`, see the DCO section above).
- **Keep the tests green.** Run `invoke test` before pushing.
- **Match where things live.** Status → `PROGRESS.md`; design/spec → `PLAN.md`; conventions →
  `CLAUDE.md`; the README is the shop window (see `CLAUDE.md` §How we work).
- Open the PR against `main` and fill in the pull-request template (it restates the DCO sign-off and
  the test requirement).

## Reporting bugs and requesting features

Use the issue templates (**New issue** → choose *Bug report* or *Feature request*). Please include
your Windows version, the KlarPDF version, how you installed it, and clear reproduction steps.

For **security** issues, do **not** open a public issue — follow [`SECURITY.md`](SECURITY.md).

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By taking part you agree to
uphold it.
