# RELEASE.md — release & dependency-update runbook

The step-by-step **operational** guide for maintainers. The *spec / rationale* lives in
`PLAN.md` §Packaging, dependencies & installer and `DEPENDENCIES.md`; this file is the *how*.

Version is single-sourced in `version.py`; dependency versions are single-sourced in
`requirements.in` (compiled to the locks). Nothing changes automatically — every bump is an
explicit edit + a reviewable PR (see `CLAUDE.md` §How we work).

---

## 1. Cut a release

**Sequence:** version bump → docs → tag → CI draft → smoke → publish.

**Prereqs:** on an up-to-date `main`, working tree clean, headless suite green
(`.\.venv\Scripts\python.exe -m pytest` — offscreen; 1 expected skip = the Poppler `pdftotext`
cross-check, absent on Windows).

1. **Version bump.** Edit `version.py` `__version__` (e.g. `0.9.3` → `0.9.4`). This single value
   feeds the PyInstaller exe metadata (`packaging/pdfproj.spec`), the Inno `AppVersion`
   (`packaging/installer.iss`), and the `v<version>` git tag. SemVer: **patch** = fixes / dependency
   bumps only; **minor** = features; **major** = breaking.

2. **Docs** (same PR as the bump):
   - `PROGRESS.md` — tick any item this release resolves; add the release line + link.
   - `CLAUDE.md` — update the **## Status** paragraph.
   - `DEPENDENCIES.md` — update the **Locked** column if a dependency version changed.

   Open this as a normal PR (branch from `origin/main`), review, and **merge to `main`**.

3. **Tag.** Annotated tag on the merged `main`, then push it — the push is what triggers the release
   workflow:
   ```sh
   git checkout main && git pull --ff-only
   git tag -a v0.9.4 -m "v0.9.4"
   git push origin v0.9.4
   ```

4. **CI draft.** The `v*` tag push runs `.github/workflows/release.yml` on a `windows-latest` runner.
   It executes `packaging/build.ps1` end-to-end — re-fetch + hash-verify the `win_amd64` wheels from
   `requirements.txt` → clean build venv (`--require-hashes --no-index`) → PyInstaller onedir +
   onefile → Inno Setup installer → `SHA256SUMS` — uploads the artifacts, and creates a **draft**
   GitHub Release (`draft: true`, auto-generated notes) attaching `pdfproj-setup.exe`,
   `pdfproj-portable.exe`, `SHA256SUMS`, and `vendor-wheels.zip` (the exact build inputs / AGPL
   corresponding-source pointer at that tag). **It does NOT auto-publish.**
   - The runner is *online*, so it re-fetches wheels — the CI build is not the offline build; the
     authoritative offline build + clean-machine install are validated locally (see Verification in
     `PLAN.md`). To build/upload artifacts **without** drafting a Release (e.g. a dry run), use the
     workflow's `workflow_dispatch` ("Run workflow") trigger instead of a tag.

5. **Smoke-test the draft before publishing:**
   - Headless suite green (above).
   - Install/run the **onedir** `pdfproj-setup.exe` (from the draft's assets, or a local build):
     launch, open a PDF, confirm single-instance + window focus.
   - The **onefile portable** (`pdfproj-portable.exe`) may be blocked *locally* by a Windows
     Application Control policy (a machine policy on unsigned single-file exes) — that is **not** a
     build defect; trust the CI artifact for the portable.
   - PyInstaller output is **version-repro, not bit-repro** (timestamps differ) — CI's `SHA256SUMS`
     will not match a local build's hashes; don't compare them.

6. **Publish.** Flip the reviewed draft to public:
   ```sh
   gh release edit v0.9.4 --draft=false
   ```

### Local build (optional)
`pwsh packaging/build.ps1` re-fetches wheels then builds; add `-Offline` to build strictly from the
existing `vendor/wheels` (proves the fully-offline path — populate it once online first). Requires
Inno Setup 6 installed (see `DEPENDENCIES.md`). Artifacts land in `dist/`.

---

## 2. Change a dependency (pin → compile → vendor)

`pip-compile` runs **here** — a manual, maintainer step, done *before* the pipeline — **not** inside
`build.ps1` or `release.yml`. Those only *consume* the committed lock (`pip download` +
`pip install --require-hashes --no-index`); they never regenerate it, so a rebuild can't silently
pull a different version. Run `pip-compile` (pinned pip-tools, see `DEPENDENCIES.md`) on **Windows**,
because the ship/build locks carry `win_amd64` hashes.

1. **Edit the floor pin** in the right `*.in` — the only file you hand-edit:
   - runtime dep (PySide6 / PyMuPDF / pypdf) → `requirements.in`
   - test-only dep → `requirements-dev.in`
   - build-only dep (PyInstaller) → `requirements-build.in`

2. **Re-compile the affected lock(s).** A runtime change propagates to **both** the ship and dev
   locks (the dev lock includes `-r requirements.in`):
   ```sh
   # ship lock (hashed, win_amd64) — Windows
   pip-compile --generate-hashes -o requirements.txt requirements.in
   # dev lock (versions, no hashes; a Windows compile keeps the win32-only colorama)
   pip-compile -o requirements-dev.txt requirements-dev.in
   # build lock (hashed) — only when a build-only dep changed
   pip-compile --generate-hashes --allow-unsafe -o requirements-build.txt requirements-build.in
   ```
   `--require-hashes` isn't shareable across platforms, which is why the dev lock is version-only
   (see `DEPENDENCIES.md`).

3. **Re-vendor wheels** — needed for the offline build / clean-machine test (the online CI build
   re-fetches on its own):
   ```sh
   pip download -r requirements.txt --only-binary=:all: -d vendor/wheels
   ```
   then update `vendor/wheels-sources.md` (version + sha256 + URL per wheel).

4. **Review + test.** Open the lock diff as a PR (branch from `origin/main`); run the headless suite
   (`.\.venv\Scripts\python.exe -m pytest`). The diff is auditable — exact `==` plus per-wheel hashes.

5. **Ship it** if it should reach users — cut a release per §1.

> Caveat (`CLAUDE.md` §Gotchas): compile the **ship/build** locks on **Windows** (python.org 3.12.x).
> Compiling them on Linux yields manylinux hashes that `--require-hashes --no-index` rejects at build.

---

## 3. Incorporate a Dependabot security update

Dependabot **alerts + security updates** (repo *settings*, under **Settings ▸ Code security**) open
a PR when a dependency has a known advisory. There is no `.github/dependabot.yml` — version-update
PRs are deliberately off, so the only Dependabot PRs you see are security-driven.

> The PR description is Dependabot's standard changelog/commits format and does **not** name the
> advisory. The **advisory + severity (CVSS) live in the linked alert** under
> **repo ▸ Security ▸ Dependabot alerts**, which is tied to the PR there.

**Flow:**

1. **Triage** — open the linked alert for severity + advisory ID; skim the PR changelog.

2. **Decide trust by package type** (this is the crux for our hashed/offline lock):
   - **Pure-Python dep** (e.g. `pypdf`, pip-tools deps): the wheel is `py3-none-any`, so Dependabot's
     `requirements.txt` hashes are platform-independent and **correct** → safe to take.
     *Watch:* Dependabot re-resolves on its Linux runner, so it can **drop a win32-only transitive**
     from `requirements-dev.txt` (e.g. `colorama`, pulled in by pytest only on Windows).
   - **Native dep** (`PyMuPDF`, `PySide6-Essentials`, `shiboken6`): Dependabot resolves on Linux, so
     its `requirements.txt` hashes may be the manylinux wheels, **not** our `win_amd64` set →
     `--require-hashes` would fail on Windows. **Do not take its lock** — bump `requirements.in` and
     regenerate the lock(s) on Windows per **§2**.

3. **Reconcile the dev lock** on Windows (§2 step 2) so any win32-only transitive Dependabot's Linux
   run dropped (e.g. `colorama`) returns.

4. **Re-vendor** (§2 step 3) — only needed for a strict `-Offline` local build / clean-machine test;
   the online CI release build re-fetches automatically.

5. **Clean the audit gate.** Once the dependency is patched the advisory no longer applies — remove
   its `--ignore-vuln <GHSA-id>` from `.github/workflows/audit.yml` **and** `tools/audit-deps.ps1`,
   and resolve the matching `PROGRESS.md` "Open follow-ups" entry. (Leaving the ignore would silently
   suppress that advisory if it ever recurred.)

6. **Cut a patch release** — §1 above.

**Fast path for a pure-Python bump** (the common case, e.g. `pypdf`): merge the Dependabot PR, then
do steps 3 + 5, then release. Merging to `main` auto-resolves the Dependabot alert.

---

## See also
- **Dependency scanning:** `tools/audit-deps.ps1` (local, isolated `pip-audit`),
  `.github/workflows/audit.yml` (CI: weekly cron + on release tag + on lock-touching PRs). For the
  severity of any finding, read the GHSA or the **Security ▸ Dependabot alerts** entry.
- **Spec & rationale:** `PLAN.md` §Packaging, dependencies & installer; `DEPENDENCIES.md`.
- **Conventions:** `CLAUDE.md` §How we work (branch from `origin/main`; one PR per logical unit).
