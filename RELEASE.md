# RELEASE.md — release & dependency-update runbook

The step-by-step **operational** guide for maintainers. The *spec / rationale* lives in
`PLAN.md` §Packaging, dependencies & installer and `DEPENDENCIES.md`; this file is the *how*.

Version is single-sourced in `version.py`; dependency versions are single-sourced in
`requirements.in` (compiled to the locks). Nothing changes automatically — every bump is an
explicit edit + a reviewable PR (see `CLAUDE.md` §How we work).

> **Each step here is also an `invoke` task** (`tasks.py` — run `invoke --list`). The tasks are thin
> wrappers that **echo the exact command** they run, so they're a convenience, not a second source of
> truth — this prose stays authoritative for *why* and for the platform/CI boundaries. Quick map:
> `invoke test` · `invoke audit` · `invoke lock --package <pkg==ver>` · `invoke vendor` ·
> `invoke build` · `invoke tag --version <v>` (pre-flights, then tags) · `invoke publish --version <v>`.
> Windows-only tasks (`lock`/`vendor`/`build`) fail fast off Windows.

---

## Start here — shipping a change

The sections below are organised by *operation*; this is how they **compose** for the two everyday
cases. A change reaches users only when a release is cut (the last step). A small fix can carry its
own version bump in the same PR; larger work can accumulate on `main` and ship under one later
release PR — either way the release mechanics are §3.

### A — bug fix or feature, **no** new dependency
1. Branch from `origin/main`; make the change.
2. **Test** — `invoke test` (headless suite green).
3. **Version + docs** (§3 steps 1–2) — bump `version.py` (patch = fix, minor = feature); update the
   `PROGRESS.md` / `CLAUDE.md` / **`README.md`** status paragraphs.
4. **PR** (change + bump together), review, **merge** to `main`.
5. **Release** (§3 steps 3–6) — `invoke tag --version X.Y.Z` → CI builds the draft → smoke-test →
   `invoke publish --version X.Y.Z`.

### B — bug fix or feature that **adds** a dependency
Same as A, but do the dependency change **first** (§1), in the same PR, so the lock diff reviews
alongside the code:
1. Branch from `origin/main`.
2. **Add + lock the dep** (§1) — edit the right `*.in`: runtime → `requirements.in`, test-only →
   `requirements-dev.in`, build-only → `requirements-build.in`. Then **on Windows** regenerate with
   `invoke lock` (recompiles the locks; unaffected ones produce no diff), plus `invoke vendor` for a
   **runtime/build** dep (refreshes the offline wheels + `vendor/wheels-sources.md`; a *test-only*
   dep needs no vendoring).
3. Make the code change that uses it; **`invoke test`**.
4. **Version + docs** as in A — and for a **runtime** dep, also add it to the `DEPENDENCIES.md`
   runtime table (it grows the shipped audit surface).
5. **PR** (the `*.in` + regenerated lock(s) + `wheels-sources.md` + code + bump), review, **merge**.
6. **Release** as in A step 5.

> A **Dependabot alert** is just case B with the target version already decided — see §2.

---

## 1. Change a dependency (pin → compile → vendor)

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
   pip-compile --generate-hashes -o requirements-win.txt requirements.in
   # dev lock (versions, no hashes; a Windows compile keeps the win32-only colorama)
   pip-compile -o requirements-dev.txt requirements-dev.in
   # build lock (hashed) — only when a build-only dep changed
   pip-compile --generate-hashes --allow-unsafe -o requirements-build-win.txt requirements-build.in
   ```
   `--require-hashes` isn't shareable across platforms, which is why the dev lock is version-only
   (see `DEPENDENCIES.md`).

   > **A plain re-compile does NOT upgrade anything.** `pip-compile` reuses the pins already in the
   > output file whenever they still satisfy the `.in` constraints — so re-running it against an
   > unchanged `.in` is a no-op, even when the pinned version is the one an advisory names. To move
   > a pin you must ask: `--upgrade-package <name>` (targeted, preferred) or `--upgrade` (every pin
   > — a much larger diff to review). This bit us on **PYSEC-2026-3447** (setuptools 82.0.1 → 83.0.0,
   > a *transitive* pin of PyInstaller): the first recompile silently reproduced 82.0.1.

3. **Re-vendor wheels + regenerate the sources record** — needed for the offline build /
   clean-machine test (the online CI build re-fetches on its own). `vendor/wheels-sources.md` is
   **generated, never hand-edited** — `vendor/gen-sources.py` writes it from a pip `--report` JSON:
   ```sh
   pip download -r requirements-win.txt --only-binary=:all: -d vendor/wheels
   pip install -r requirements-win.txt --require-hashes --ignore-installed --dry-run --report report.json
   py -3.12 vendor/gen-sources.py        # reads report.json -> writes vendor/wheels-sources.md
   ```
   Commit the regenerated `vendor/wheels-sources.md`; `report.json` is a gitignored throwaway. (The
   `.whl` payloads under `vendor/wheels/` are gitignored too — the `.md` is the committed record.)

4. **Review + test.** Open the lock diff as a PR (branch from `origin/main`); run the headless suite
   (`.\.venv\Scripts\python.exe -m pytest`). The diff is auditable — exact `==` plus per-wheel hashes.

5. **Ship it** if it should reach users — cut a release per §3.

> Caveat (`CLAUDE.md` §Gotchas): compile the **ship/build** locks on **Windows** (python.org 3.12.x).
> Compiling them on Linux yields manylinux hashes that `--require-hashes --no-index` rejects at build.

---

## 2. Respond to a Dependabot alert

Dependabot is **detection-only** here: **alerts** are on (repo setting), but **security-update PRs
and version-update PRs are both disabled**. Dependabot runs on Linux and would write wrong-platform
(manylinux) hashes into the `win_amd64` locks for native deps, so we never let it auto-edit them — it
tells you *what* and *how severe*, and you do the bump yourself.

> The alert (repo ▸ Security ▸ Dependabot) carries the advisory ID, **severity (CVSS)**, the
> vulnerable range, and the **first patched version** — that's the source of truth.

**Flow:**

1. **Read the alert** — note the package, severity, and the first patched version.

2. **Bump it via §1** — edit the `.in` floor to the patched version, recompile the affected lock(s)
   on Windows, re-vendor. (Pure-Python vs native doesn't change the steps; recompiling on Windows
   yields the correct `win_amd64` hashes either way.)

   **If the package is not in any `.in`** it is a *transitive* pin (e.g. `setuptools`, pulled in by
   PyInstaller and written under "considered to be unsafe"). There is no floor to edit — move it
   with `--upgrade-package <name>` on the lock that carries it, and check the resulting diff touches
   only that package. Re-vendoring is **ship-lock only**: `vendor/wheels-sources.md` is generated
   from `requirements-win.txt`, so a *build*-lock change leaves it unchanged (`build.ps1` fetches
   the build lock's wheels itself). A build-only package also means the **shipped exe is
   unaffected** — confirm with a quick search of `dist/klarpdf` before deciding a release is needed.

3. **Clean the audit gate** — if the advisory was being carried as track-only, remove its
   `--ignore-vuln <GHSA-id>` from `.github/workflows/audit.yml` **and** `tools/audit-deps.ps1`, and
   resolve the matching `PROGRESS.md` "Open follow-ups" entry.

4. **Cut a patch release** — §3 below. Pushing the bump to `main` auto-resolves the alert.

---

## 3. Cut a release

**Sequence:** version bump → docs → tag → CI draft → smoke → publish.

**Prereqs:** on an up-to-date `main`, working tree clean, headless suite green
(`.\.venv\Scripts\python.exe -m pytest` — offscreen; 1 expected skip = the Poppler `pdftotext`
cross-check, absent on Windows).

**One-time gate — the first release carrying Help ▸ Donate… (G6).** The menu item ships whether or not
the GitHub Sponsors listing exists, and a missing listing does **not** 404: `/sponsors/utyagi24`
redirects to the plain profile, so a dead Donate link looks *exactly* like a working one — no test can
tell them apart. Confirm the listing is live before the first release that includes it:
```sh
gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'   # must be true
```
Delete this gate once it has passed once.

1. **Version bump.** Edit `version.py` `__version__` (e.g. `0.9.3` → `0.9.4`). This single value
   feeds the PyInstaller exe metadata (`packaging/klarpdf.spec`), the Inno `AppVersion`
   (`packaging/installer.iss`), and the `v<version>` git tag. SemVer: **patch** = fixes / dependency
   bumps only; **minor** = features; **major** = breaking.

2. **Docs** (same PR as the bump):
   - `PROGRESS.md` — tick any item this release resolves; add the release line + link.
   - `CLAUDE.md` — update the **## Status** paragraph.
   - **`README.md` — update the `**Status: vX.Y.Z shipped**` line (with its one-line "New in …"
     summary, current release only — history lives in GitHub Releases), and the **Features**
     inventory if the release adds or changes a user-facing feature.**
     This is the *only* doc a visitor to the public repo reads, so a stale version here is the most
     visible drift there is. It went unnoticed at v0.9.5 and v0.9.6 (README still claimed v0.9.4).
   - `DEPENDENCIES.md` — update the **Locked** column if a dependency version changed.

   Open this as a normal PR (branch from `origin/main`), review, and **merge to `main`**.

   Quick check before opening the PR — these three must agree:
   ```sh
   grep -n '__version__' version.py
   grep -n 'Status' README.md CLAUDE.md | head -2
   ```

3. **Tag.** Annotated tag on the merged `main`, then push it — the push is what triggers the release
   workflow:
   ```sh
   git checkout main && git pull --ff-only
   git tag -a v0.9.4 -m "v0.9.4"
   git push origin v0.9.4
   ```

4. **CI draft.** The `v*` tag push runs `.github/workflows/release.yml` on a `windows-latest` runner.
   It executes `packaging/build.ps1` end-to-end — re-fetch + hash-verify the `win_amd64` wheels from
   `requirements-win.txt` → clean build venv (`--require-hashes --no-index`) → PyInstaller onedir +
   onefile → Inno Setup installer → `SHA256SUMS` — uploads the artifacts, and creates a **draft**
   GitHub Release (`draft: true`, auto-generated notes) attaching `klarpdf-setup-x64.exe`,
   `klarpdf-portable-x64.exe`, `SHA256SUMS`, and `vendor-wheels.zip` (the exact build inputs / AGPL
   corresponding-source pointer at that tag). **It does NOT auto-publish.**
   - The runner is *online*, so it re-fetches wheels — the CI build is not the offline build; the
     authoritative offline build + clean-machine install are validated locally (see Verification in
     `PLAN.md`). To build/upload artifacts **without** drafting a Release (e.g. a dry run), use the
     workflow's `workflow_dispatch` ("Run workflow") trigger instead of a tag.

5. **Smoke-test the draft before publishing:**
   - Headless suite green (above).
   - **One-time, for the first KlarPDF release:** *uninstall `pdfproj` first.* The rename minted a
     fresh Inno `AppId`, so `klarpdf-setup.exe` installs as a **new** app rather than upgrading
     `pdfproj` in place. Installing over the old app would leave its `pdfproj.Document` ProgID and its
     `.pdf` `OpenWithProgids` value orphaned — the *old* uninstaller is the only thing that removes
     them, and an in-place upgrade never runs it. Uninstalling first also clears the stale "pdfproj"
     entry from the `.pdf` **Open With** list.
   - Then **delete `%LOCALAPPDATA%\pdfproj` by hand.** pdfproj's uninstaller does *not* remove it: its
     `[UninstallDelete]` pointed at `{userappdata}` (Roaming), while Qt's `AppConfigLocation` resolves
     to `%LOCALAPPDATA%` on Windows — so it deleted a path that never existed. Fixed for KlarPDF
     (`{localappdata}`), but the old build can't retroactively clean up after itself.
   - **Close the app first — since v0.10.1 the installer enforces this.** KlarPDF holds a named mutex
     (`platform_integration.APP_MUTEX_NAME`) for its whole lifetime, and `installer.iss` names it in
     `AppMutex`, so **Setup and the uninstaller both refuse to run** while the app is open (silent runs
     exit **non-zero** and change nothing — `1` and `5` both observed, so test the code is non-zero,
     never that it equals a particular value). It **refuses rather than force-closes**: Restart Manager could shut
     the app down for us, but KlarPDF prompts on unsaved edits and a forced close would bypass that
     prompt — hence `CloseApplications=no`.

     What the guard prevents, observed at v0.10.0 and neither of them a packaging fault: the install
     directory survives (Inno cannot delete a running `.exe`, and a per-user install has no admin
     rights to queue a reboot-time delete — `PendingFileRenameOperations` stays empty), and
     `%LOCALAPPDATA%\klarpdf` **reappears** because `[UninstallDelete]` removes it and the still-live
     process then writes `view_state.json` on shutdown. Both clear with a manual `Remove-Item`.

     Two things the mutex does *not* cover: the **portable** exe (no installer at all), and the
     **pdfproj-era** uninstaller, which predates the mutex — close that app by hand before removing it.
   - Install/run the **onedir** `klarpdf-setup-x64.exe` (from the draft's assets, or a local build):
     launch, open a PDF, confirm single-instance + window focus.
   - The **onefile portable** (`klarpdf-portable-x64.exe`) may be blocked *locally* by a Windows
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

## See also
- **Dependency scanning:** `tools/audit-deps.ps1` (local, isolated `pip-audit`),
  `.github/workflows/audit.yml` (CI: weekly cron + on release tag + on lock-touching PRs). For the
  severity of any finding, read the GHSA or the **Security ▸ Dependabot alerts** entry.
- **Spec & rationale:** `PLAN.md` §Packaging, dependencies & installer; `DEPENDENCIES.md`.
- **Conventions:** `CLAUDE.md` §How we work (branch from `origin/main`; one PR per logical unit).
