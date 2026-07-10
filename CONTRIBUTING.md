# Contributing to KlarPDF

KlarPDF is a local, offline, native-Windows PDF viewer + page editor (Python · PySide6 · PyMuPDF).
It is open source under `AGPL-3.0-or-later`. It is **not open to pull requests**.

## How this project accepts contributions

**Issues — open to everyone.** Bug reports, security reports, and feature requests are welcome and
wanted. Filing one is the most useful thing you can do; see
[Reporting bugs and requesting features](#reporting-bugs-and-requesting-features).

**Pull requests — the maintainer and invited collaborators only.** PRs from anyone else are closed
automatically by
[`.github/workflows/close-external-prs.yml`](.github/workflows/close-external-prs.yml). This is not a
judgement on your change. Review is the scarce resource here, and the roadmap is deliberately narrow
(see [`PLAN.md`](PLAN.md)). If a feature request is accepted, a collaborator implements it — an
unsolicited PR implementing it will still be closed, however good it is.

**Forking is welcome.** The AGPL guarantees your right to fork, modify, and publish your own version.
Nothing here is meant to discourage that.

## Licensing of contributions

KlarPDF is licensed **`AGPL-3.0-or-later`** (see the root `LICENSE`). Contributions are
*inbound = outbound*: by contributing you license your contribution under those same terms. You keep
your copyright — the project asks for no assignment. Do not contribute code you are not entitled to
license this way.

> **For collaborators.** Because contributions are *licensed*, not assigned, a merged contribution
> makes its author a copyright holder in the combined work — which forecloses relicensing the project
> under any other terms without their consent. See `PLAN.md` §Public-release readiness: if the
> commercial-relicensing option is ever to be kept, a relicensing grant must be settled **before** a
> collaborator's first merge, not after.

## Provenance — the Developer Certificate of Origin (DCO) 1.1

This project uses the **DCO 1.1** rather than a CLA. It is a statement about *provenance*: that you
wrote the change, or otherwise have the right to submit it under the project's licence.

**You accept it by submitting a change.** By opening a pull request, or having a commit merged, you
certify the three clauses reproduced below. There is no `Signed-off-by` requirement and no sign-off
check — with pull requests restricted to a handful of invited collaborators, a per-commit
certification would be ceremony rather than information.

Note the DCO grants the project no rights beyond the `AGPL-3.0-or-later` under which your contribution
is already licensed. It certifies that you *may* contribute; it does not transfer anything.

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

## Development environment (collaborators, and anyone working in a fork)

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

## Branch, commit, and PR conventions (collaborators)

- **Always branch from an up-to-date `main`.** `git fetch origin && git switch -c <name> origin/main`.
  Use a prefixed branch name: `feat/…`, `fix/…`, `docs/…`.
- **One PR per logical unit.** Keep changes focused and reviewable.
- **Keep the tests green.** Run `invoke test` before pushing.
- **Match where things live.** Status → `PROGRESS.md`; design/spec → `PLAN.md`; conventions →
  `CLAUDE.md`; the README is the shop window (see `CLAUDE.md` §How we work).
- Open the PR against `main` and fill in the pull-request template.

## Reporting bugs and requesting features

Use the issue templates (**New issue** → choose *Bug report* or *Feature request*). Please include
your Windows version, the KlarPDF version, how you installed it, and clear reproduction steps.

For **security** issues, do **not** open a public issue — follow [`SECURITY.md`](SECURITY.md).

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By taking part you agree to
uphold it.
