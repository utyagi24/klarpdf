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

> **Verify the two settings actually match this paragraph** — they drifted apart for a month once
> (see `PROGRESS.md` "Open follow-ups"), which is how Dependabot came to open #234 against this very
> policy:
> ```sh
> gh api repos/utyagi24/klarpdf/automated-security-fixes   # -> {"enabled": false}  (PR-writing: OFF)
> gh api -i repos/utyagi24/klarpdf/vulnerability-alerts    # -> HTTP 204            (alerts: ON)
> ```
> In the web UI these live at **Settings ▸ Advanced Security** (`/settings/security_analysis`) and are
> labelled **"Dependabot security updates"** / **"Dependabot alerts"** — the string
> `automated-security-fixes` is the REST name only and appears nowhere on the page.
>
> If security-update PRs are ever switched back on, note that `close-external-prs.yml` will close them
> on sight: Dependabot's `author_association` is neither OWNER, COLLABORATOR nor MEMBER. Exempt it
> there first, or the setting is just noise.

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
(`.\.venv\Scripts\python.exe -m pytest` — offscreen), **and the `audit` workflow green on `main`**:

> **Expect 7 skips on Windows, not 1.** This line used to say "1 expected skip = the Poppler
> `pdftotext` cross-check" and had drifted: there are **four** Poppler-gated tests now, not one, plus
> three platform-conditional ones. A releaser with the wrong baseline cannot tell a normal run from a
> broken environment, which is the only reason the number is written down at all. The seven are —
> **4** gated on `pdftotext` (`test_mcp_redaction.py` ×2, `test_redaction.py`, `test_search_redact.py`),
> **2** POSIX symlink-semantics tests, and **1** off-Windows mutex no-op. All seven **run in Linux CI**
> (which installs `poppler-utils`), and `test.yml` fails the build if anything skips there for a
> reason not on its allowlist. Conversely `tests/test_app_mutex.py`'s two Windows-kernel
> cases skip in CI and run **only here**, so this manual run is their sole coverage.
>
> **Seven is the PowerShell number. Under Git Bash you get three, and that is also correct** —
> measured 2026-08-27 on the same commit: `2394 passed, 7 skipped` from PowerShell, `3 skipped` from
> Git Bash. Git for Windows ships a `pdftotext` at `/mingw64/bin` that PowerShell's `PATH` does not
> carry, so the **four** Poppler-gated tests *run* there and only the three platform-conditional ones
> remain — the same three, with the same reasons, that CI's `windows` job reports. So the rule is
> **7 under PowerShell, 3 under Git Bash, and the difference is exactly the four Poppler tests**;
> anything else means a broken environment, investigate before tagging. The reconciliation behind
> this is in `PROGRESS.md` §Open follow-ups ("Redaction's Poppler-gated tests skip locally on
> Windows"); it had not reached this runbook, so a releaser in Git Bash was being told to go hunting
> for a fault that was not there.
```sh
gh run list --workflow=audit.yml --limit 3      # main must be green before you tag
```

**Not a gate any more — Help ▸ Donate… (G6) already shipped.** This block used to say "confirm the
Sponsors listing is live before the first release that includes it", and that release was **v0.10.0**.
The menu item ships whether or not the listing exists, and a missing listing does **not** 404:
`/sponsors/utyagi24` redirects to the plain profile, so a dead Donate link looks *exactly* like a
working one — no test can tell them apart. The listing is still absent (`hasSponsorsListing` →
**`false`**, re-checked 2026-08-27), so the gate never passed and every release since has sailed
straight through a block phrased as though it could stop one.

So it is recorded here as a **known dead link in the shipped app**, not a pre-release check to
satisfy. It is tracked as a 1.0 gate item in `PROGRESS.md` (G6 Part 2 — enrol the account in GitHub
Sponsors), which is where the decision lives; releases before 1.0 do not wait on it:
```sh
gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'   # false until G6 Part 2 lands
```
When it finally returns `true`, delete this block — its whole subject is gone at that point.

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
   - **The tag's `audit` run is green.** The `v*` push starts `audit.yml` alongside `release.yml`,
     but they are **separate workflows** — a red audit does **not** fail the build, block the draft,
     or stop `gh release edit --draft=false`. Nothing is in the way, so this has to be looked at on
     purpose. It was red at **v0.12.0, v0.13.0 and v0.14.0** before anyone noticed (setuptools
     PYSEC-2026-3447 in the build lock, fixed in #144).
     ```sh
     gh run list --workflow=audit.yml --limit 3           # the row for this tag must be success
     ```
     If it is red, triage before publishing: **which lock** (ship / dev / build), then **is the
     package shipped** (`find dist/klarpdf -iname '*<pkg>*'` — a build-only package is not in the
     artifact), then **is the vulnerable path reachable** for a frozen Windows app. That decides
     whether the release must be held or the advisory merely fixed on `main` afterwards.
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

## 4. The MCP bridge — the extra verification before a release that ships it

The bridge has **no version of its own**: it ships under the app's tag and reads `version.py`, so
"releasing the bridge" is §3 plus the checks below. Run them once, on the release that first carries
`mcp_bridge/`, and after that only when something in `mcp_bridge/`, `requirements-mcp.*` or
`packaging/mcpb/` has changed.

This is PLAN.md §MCP / Agent Bridge roadmap → Verification, turned into things you can actually do.
**Most of it is already automated** — the point of the table is to make the small remainder obvious
rather than to re-check what CI checks on every PR.

Since **M126** the remainder is two rows: **9** (install the `.mcpb` in Claude Desktop) and **10**
(the `uv.lock` question, which can only be answered while doing 9). Everything else is a CI check or
a single command.

| # | Matrix item | How it is checked | Where |
|---|---|---|---|
| 1 | Tool round-trips preserve OCR text / TOC / form fields | `tests/test_mcp_transforms.py` — the same invariants as `test_materialize.py`, same fixtures | **automated**, every PR |
| 2 | Redaction is leak-free, cross-engine | `tests/test_mcp_redaction.py` + `test_redaction.py::…poppler_cross_engine`; `test.yml` asserts the Poppler test **did not skip** | **automated**, every PR |
| 3 | No outbound connection, no listening port | `tests/test_mcp_no_qt.py` — the child runs every tool with `socket.connect`/`bind` poisoned | **automated**, every PR |
| 4 | No Qt on the server path | same file — a fresh interpreter, **every registered tool** exercised (pinned to the registry, so a new tool cannot escape the guard), then `PySide6`/`shiboken6`/`model.edit_commands` asserted absent. Has a negative control | **automated**, every PR |
| 5 | Source left byte-identical by every write tool | `tests/test_mcp_transforms.py`, parametrised over every write tool | **automated**, every PR |
| 6 | Cross-platform — **Linux** | CI runs the whole suite on `ubuntu-latest`; `tests/test_mcp_packaging.py` asserts the lock is unhashed and platform-marker-free | **automated**, every PR |
| 7 | Cross-platform — **Windows** | the `bridge-windows` job resolves `requirements-mcp.txt` on `windows-latest` and runs the bridge suite against it (M126) | **automated**, every PR that reaches the bridge |
| 8 | Lives with Claude Code | the `.mcp.json` at the repo root; `python tools/mcp_stdio_check.py` drives the console script over a real pipe with the SDK's own client (initialize → list → call → image → resource → the three refusals) | **one command**, either platform |
| 9 | Lives with Claude Desktop — config **and** one-click `.mcpb` | the steps below | **manual, Windows or macOS** |
| 10 | Does the host honour a `uv.lock`? | carried from M42 — see below | **open question** |

### 7 — Windows *(automated since M126 — nothing to do by hand)*

The point of this step was never the tests: CI had already run them. It was that
`pip install -r requirements-mcp.txt` **resolves at all** on Windows — "a lock that only resolves
on one platform" being the defect PLAN.md flags as expensive to discover late. The `bridge-windows`
job in `test.yml` now does exactly that on every PR that can reach the bridge, and runs the bridge
suite against the lock it just resolved.

Run it by hand only when you want the answer *without* pushing — e.g. while editing the lock. From
the Windows checkout, in a throwaway venv so the app's `.venv` is untouched:

```powershell
py -3.12 -m venv $env:TEMP\klarpdf-mcp-check
& $env:TEMP\klarpdf-mcp-check\Scripts\python.exe -m pip install -r requirements-mcp.txt
& $env:TEMP\klarpdf-mcp-check\Scripts\python.exe -m pytest tests\test_mcp_*.py
```

Expect green, with **one skip**: the Poppler cross-engine redaction check, because `pdftotext` is
not on a stock Windows PATH. That skip is exactly why `test.yml` installs `poppler-utils` and
asserts the test ran on Linux — between the two platforms it is never skipped everywhere.

### Handoff — where a Windows session picks this up (2026-08-27)

*Transient. Delete this block when M44 ticks; the numbered rows above and below are the durable
part.*

**State.** `main` is at the M126 merge. Eight of the ten rows are CI checks or a single command;
**rows 9 and 10 are what is left, plus the tag.** Both need a Claude Desktop install, so they are
one sitting, not two. Nothing here is blocked and nothing else in M44 is waiting on it.

**Verified in WSL on 2026-08-27**, so a Windows session need not redo it: full suite 2,401 passed /
2 expected skips; bridge suite 539 passed / 0 skips; `tools/mcp_stdio_check.py` 13/13; the bundle
builds at 199 KiB with its manifest and version in step. `bridge-windows` is green on `main` and is
now a **required check**, so row 7 needs nothing by hand.

**One-time, on the Windows box:** python.org **3.12.x** (the Store stub cannot build), **Node** (for
`npx @anthropic-ai/mcpb`, which the build shells out to), **[`uv`](https://docs.astral.sh/uv/)** on
PATH (the bundle's launcher — Desktop cannot install without it), and **Claude Desktop** itself.

**Then, in order:**

```powershell
git pull
py -3.12 packaging\mcpb\build_mcpb.py     # -> dist\klarpdf-<version>.mcpb  (stdlib only)
.\.venv\Scripts\Activate.ps1              # NOT the same as calling the venv's python.exe — see below
python -m pip install -e . --no-deps      # once per checkout; puts klarpdf-mcp on PATH
python tools\mcp_stdio_check.py           # row 8 here: expect 13 passed
```

**The two scripts want different interpreters, and the second one wants the venv *activated*, not
merely invoked.** `build_mcpb.py` is stdlib-only, so the base python.org `py -3.12` runs it.
`mcp_stdio_check.py` needs three separate things, and only activation supplies all three:

1. `mcp` and `pymupdf` on the import path — **only the repo `.venv` has them**, so `py -3.12` dies on
   `ModuleNotFoundError: No module named 'mcp'` before reaching a single check.
2. `klarpdf-mcp` **existing** — nothing in a fresh checkout provides it, hence the editable install.
   `--no-deps` leaves the pinned test venv exactly as the suite found it.
3. `klarpdf-mcp` **on `PATH`** — the script locates the server with `shutil.which`, and this is the
   step that catches people. Running `.\.venv\Scripts\python.exe tools\mcp_stdio_check.py` gets you
   (1) and (2) and still fails with `klarpdf-mcp is not on PATH`, because invoking a venv's
   interpreter directly does **not** put its `Scripts\` directory on `PATH` — only activation does.
   The console script is sitting right there beside the interpreter and the check cannot see it.

Verified 2026-08-27 on Windows: **13 passed, 0 failed**, against `klarpdf-mcp 0.17.1`, protocol
`2025-11-25`, 19 tools.

`dist\` is gitignored, so the bundle built in WSL did not travel — build it there. Then work row 9,
then row 10 **in the same sitting** (it can only be answered while installing), then §3 for the tag.

**Write back before closing:** tick **M44** in `PROGRESS.md` with what rows 9 and 10 actually
returned; put row 10's answer in the row 10 section below (and in `mcp_bridge/README.md` only if it
changed); delete this block.

### 9 — Claude Desktop

```bash
python packaging/mcpb/build_mcpb.py     # -> dist/klarpdf-<version>.mcpb
```

Needs Node (for `npx @anthropic-ai/mcpb`) and, on the *installing* machine,
[`uv`](https://docs.astral.sh/uv/) on PATH. Then:

1. Open the `.mcpb`. Claude Desktop should offer to install it.
2. Confirm the tool list matches `packaging/mcpb/manifest.json` — **19** as of v0.17.1. Count it
   from the manifest rather than from here: a test pins the manifest to what the server registers,
   so the manifest cannot go stale and this line can (it said 16 for three tools longer than it was
   true).
3. Call `get_info` on a real PDF, then `redact_text` on a throwaway copy, and check the reply
   carries `cross_engine_verified` (`true` only if Poppler is installed on that machine).
4. Confirm the input file is **byte-identical** afterwards. Hash it before and after rather than
   trusting the timestamp — the whole guarantee is that a write tool never touches its input:

   ```powershell
   $h = (Get-FileHash .\throwaway.pdf -Algorithm SHA256).Hash
   # ... run redact_text against it from Desktop, writing to a NEW out path ...
   if ((Get-FileHash .\throwaway.pdf -Algorithm SHA256).Hash -eq $h) { "unchanged" } else { "CHANGED" }
   ```

5. Also check the plain-config path (Option B in `mcp_bridge/README.md`), since it is the fallback
   for anyone without `uv`. It needs `klarpdf-mcp` on the PATH **Desktop** sees, which is not
   necessarily the one your shell sees — use `where klarpdf-mcp` and put the absolute path in
   `%APPDATA%\Claude\claude_desktop_config.json` if the bare name does not resolve.

**If Desktop refuses the bundle**, the likely causes in order: `uv` not on PATH (the manifest's
launcher is `uv run --directory ${__dirname}/server`), a Python outside `>=3.12,<3.13` (what the
generated `pyproject.toml` requires), or no network — Option A resolves from PyPI at install time
by design. Option B needs none of those and is the fallback worth reaching for.

> **"Python >=3.12,<3.13" can show unmet on a machine that has exactly that** — hit 2026-08-27 with
> python.org **3.12.10** installed and working. Desktop's install dialog flags the runtime while
> `python --version` in a shell answers `3.12.10`, which makes the warning look like a lie.
>
> The cause is Windows' **App Execution Aliases**. `%LOCALAPPDATA%\Microsoft\WindowsApps` sits
> **first** on the user `PATH` and holds `python.exe`, `python3.exe` and `pythonw.exe` as **0-byte
> reparse points**. A shell resolves the alias and gets the real interpreter; something that stats
> the file, or launches it without the alias machinery, sees a zero-byte file and no version. So the
> probe fails on a machine whose Python is fine — the same shape as M127's `npx`, where every way of
> *looking* for the tool succeeded and the one call that mattered could not start it.
>
> **It is a pre-flight check, not the launcher.** The bundle runs `uv run --directory …`, and `uv`
> finds interpreters through the registry (`HKCU\Software\Python\PythonCore\3.12\InstallPath`), not
> through that alias — verified the same day against the staged bundle: `Using CPython 3.12.10 at
> …\pythoncore-3.12-64\python.exe`, 32 packages installed, server imported. So the warning can be
> **installed straight past**; it says "may not work correctly" and in this case it does work.
>
> To clear it rather than ignore it, put the real interpreter ahead of the alias on the user `PATH`:
> ```powershell
> $real = "$env:LOCALAPPDATA\Python\pythoncore-3.12-64"
> $entries = [Environment]::GetEnvironmentVariable('Path','User') -split ';' |
>            Where-Object { $_ -and $_ -ne $real -and $_ -ne "$real\Scripts" }
> [Environment]::SetEnvironmentVariable('Path', (@($real,"$real\Scripts") + $entries) -join ';', 'User')
> ```
> Then **fully quit Claude Desktop** — from the tray, not just the window — since a Windows process
> reads `PATH` once at start. Confirm with `(Get-Item (Get-Command python).Source).Length`: ~105,000
> means the real binary, `0` means you are still on the alias. Disabling the aliases under
> *Settings ▸ Apps ▸ Advanced app settings ▸ App execution aliases* works equally well.

**A third-party client is a spot check, not a gate** — Codex CLI via `~/.codex/config.toml`, or Grok
Build. stdio is the universal denominator, so a failure there is a bug worth knowing about before
strangers find it, not a reason to hold the release.

### 10 — the `uv.lock` question, still open

PLAN.md asked M42 to test whether the host honours a `uv.lock` (and hash verification), because if
it does, most of the "the audited lock is not what the `.mcpb` installs" gap closes. **It could not
be answered**, and the reason changed the question: there is no `uv` server type (M42 measured
`mcpb` 2.1.2 accepting only `python | node | binary`), so there is no host-managed `uv` invocation
to honour a lock. What the bundle actually does is `uv run --directory server`, and whether *that*
consults a committed `uv.lock` can only be seen by installing the bundle.

While answering it, try: drop a `uv.lock` beside the bundle's `pyproject.toml`, install, and compare
the resolved versions against `requirements-mcp.txt`. If they match, commit the lock into the bundle
and say so in `mcp_bridge/README.md`. **Until then the README's statement stands as written** — the
`.mcpb` path installs online and is not covered by `pip-audit`. Do not soften that wording without
evidence.

**Concretely, in the same sitting as row 9.** Generate the lock from the bundle's own manifest, so
it describes exactly what the bundle declares:

```powershell
cd packaging\mcpb
uv lock                      # -> uv.lock beside the generated pyproject.toml
py -3.12 build_mcpb.py       # rebuild so the lock travels inside the bundle
```

Install that bundle, then read back what Desktop's copy actually resolved — the environment `uv`
built lives under the installed extension's `server` directory:

```powershell
uv run --directory "<installed extension>\server" python -m pip freeze
```

Compare that against `requirements-mcp.txt`. **Three outcomes, and each has a different consequence:**

- **They match, and a deliberately stale lock still wins** — i.e. edit one pin in `uv.lock`, rebuild,
  reinstall, and the *stale* version is what appears. That is the only proof the lock is being
  honoured rather than coincidence: the generated `pyproject.toml` already pins the whole transitive
  set with `==`, so a fresh resolve produces the same answer with or without a lock. **Do this
  second step** — without it a match proves nothing.
- **They match, but the doctored lock is ignored** — `uv` is resolving from the pins and the lock is
  inert. The README wording stands unchanged.
- **They differ** — the bundle is installing something the audit never saw, which is the case the
  README already describes. Record the drift; it is the strongest argument for Option B.

Whatever comes back, write the answer here and tick M44. A question carried since M42 should not
survive being answered.

### Release notes — the two things to say plainly

Whatever else the notes cover, these two are the ones a reader can be misled about:

- The bridge is a **separate, optional component**. `klarpdf-setup-x64.exe` is unchanged: same size,
  same hashed offline lock, same clean-machine install test. Nobody installing the app gets any of
  this.
- The `.mcpb` path **installs online**, and its dependencies are therefore not the ones `pip-audit`
  covers. Every other path this project ships is offline; this one is the deliberate exception.

---

## See also
- **Dependency scanning:** `tools/audit-deps.ps1` (local, isolated `pip-audit`),
  `.github/workflows/audit.yml` (CI: weekly cron + on release tag + on lock-touching PRs). For the
  severity of any finding, read the GHSA or the **Security ▸ Dependabot alerts** entry.
- **Spec & rationale:** `PLAN.md` §Packaging, dependencies & installer; `DEPENDENCIES.md`.
- **Conventions:** `CLAUDE.md` §How we work (branch from `origin/main`; one PR per logical unit).
