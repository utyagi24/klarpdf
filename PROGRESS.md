# KlarPDF — Build Progress

Live status: the current release, the open milestones, the 1.0 list and the open follow-ups. A done
milestone's record is in its own file in `docs/milestones/`. Older release notes are in
`docs/releases.md`. Which file holds what: `CLAUDE.md` §How we work → *Where things live*.

**Status:** ✅ **v0.21.0 shipped** — **files that open, text boxes that keep their look, and
checks that keep the code clean** (M158–M169). Nothing here is a new feature, so `RELEASE.md` §3's
SemVer rule would make it a patch; the owner chose a **minor** for the amount of change since
v0.20.0. Release: <https://github.com/utyagi24/klarpdf/releases/tag/v0.21.0>.

**(1) Fixes in the core, so in the app and the bridge alike.** An encrypted file with no `/ID` in
its trailer could be saved into a copy that asked for a password nobody had set (**M161**). The key
of an RC4 or AES-128 file is made from its `/ID`, and MuPDF wrote a new one on every save. Every
route now writes a file that opens, keeping its encryption and restrictions. Text boxes: a box moved
in Microsoft Edge kept KlarPDF's tag and came back as a solid black box (**M163**); a box's text
saved 4.5 pt higher than KlarPDF showed it, and now saves where it shows (**M164**); and a box Edge
had shrunk lost its last word below the edge, as did a long line typed near the page edge and a word
wider than a fixed-width box (**M166**).

**(2) In the app.** Toggling the sidebar on a one-page document could hang the window, because each
fit showed or hid the scrollbar that set off the next fit (**M160**). Dragging a text box's handle
while its editor was open lost the drag (**M165**).

**(3) Dependencies.** In the bridge's lock, and so in the wheel on PyPI, which pins them exactly:
`mcp` 2.0.0 → 2.3.0, `pyjwt` 2.14.0 → 2.15.1 and `opentelemetry-api` 1.44.0 → 1.45.2 (**M158**,
**M162**). Since `mcp` 2.1 the SDK passes on only a `ToolError`'s text, so the bridge now raises one,
and an agent reads the same refusal text it did on v0.20.0. The weekly `audit` job audits every lock
again; it had stopped at the first lock with a finding (**M162**). The dev lock and the bridge's lock
now pin the same versions, and a test keeps them so (**M159**).

**(4) Checks on the code itself** (M167–M169). Ruff runs on every change as the required `lint`
check, and the project's written rules are checks now: no OS call outside `platform_integration.py`,
no `print` in `klarpdf/`, Python 3.11 syntax everywhere, and a reason on every suppression. A dead-code
test removed what only the tests used, among it a view rotation no release ever let a user reach.
No behaviour a user could reach changed; the owner hand-checked M169.

**1.0 is still deliberately not taken**: the clean-machine install and the flaky
`test_single_instance` remain (§Toward 1.0, below).

3,261 headless tests green on Windows (9 expected skips under PowerShell — see `RELEASE.md` §3).

**Verified at release (2026-11-09).** The draft's four checksums match, `install.py` and the `.mcpb`
both carry 0.21.0, and the tag's `audit` run is green. The owner smoke-tested the installer and ran
`RELEASE.md` §4 row 9 in Claude Desktop through the one-click `.mcpb`: 23 tools, `get_info`,
`redact_text` leaving its input byte-identical, and a refusal arriving as readable text on `mcp` 2.3.
**Row 9's config-file route (Option B) was not checked this time.** Row 8's stdio check had failed
since v0.20.0, because it expected 19 tools; it was fixed in the release PR and passed 13 of 13. The
PyPI upload succeeded. Also found: `SHA256SUMS` has Windows line endings, so `sha256sum -c` fails on
Linux and macOS, and v0.20.0's did too
([#446](https://github.com/utyagi24/klarpdf/issues/446)).

Older release notes: [`docs/releases.md`](docs/releases.md).

**Toward 1.0 (owner call, 2026-08-01).** The feature roadmap is complete, and **1.0 was deliberately
not taken here**: on a public repo it is a claim about readiness for *other people*, and what remains
open is almost entirely in that category. The gate, all small and all concrete:

- [ ] **Clean-machine install** — the one deferred M9 item: `klarpdf-setup-x64.exe` on Windows with
  **no Python and networking disabled**. It is the first thing a stranger does and has never been
  watched. (Win10 Home has no Sandbox → VirtualBox / spare machine / fresh local user.)
- [x] **The Donate link points at a Sponsors listing that does not exist** — and it *redirects*
  rather than 404s, so no test can catch it. A dead link inside the app. **Fixed** (owner,
  2026-09-24): the account now has a public Sponsors listing, so the app's link lands on it.
  Checked the same day: `hasSponsorsListing` is `true`, and `https://github.com/sponsors/utyagi24`
  answers 200 with no redirect. Nothing in the app changed (G6 Part 2).
- [ ] **One known flaky test** (`test_single_instance`) — down from two. **Re-measured
  2026-08-12 and the stakes were overstated:** both were only ever seen on *local Windows* runs,
  while the required `pytest` check runs on `ubuntu-latest` — and across 200 recorded `test.yml` runs
  neither has failed it (191 green; all 9 failures trace to the 2026-07-29 widget-leak episode, the
  2026-07-25 reading-bar PR, and the workflow's own first run). So this is a release-prep annoyance,
  not the merge blocker described. Split accordingly: **the save path's `os.replace` was fixed in
  M38.5** ([#239](https://github.com/utyagi24/klarpdf/pull/239)); **`test_single_instance` is
  left alone** — one failure, never reproduced, no fix plan, nothing actionable to write.
  **Correction, 2026-08-28: "one known flaky test" was wrong, and the word doing the damage was
  *known*.** Preparing the v0.18.0 release surfaced a second, unrecorded one — `test_atomic_replace.py`
  failing **4 runs in 20**, in two different tests, with the reported one moving between runs. It had
  presumably been failing at that rate for a while and was absorbed as noise, because a re-run cleared
  it. Fixed as **M130**; the inventory is honest again. The lesson is about the *counting*, not the
  bug: a flake that a re-run hides is not observed, so "we have one" only ever meant "we have written
  one down." `test_single_instance` remains the sole item here. **2026-09-19: a second one, seen on
  CI** — `test_incremental_save.py::test_saving_twice_from_one_model_does_not_stack_revisions`, once on
  the `windows` job (§Open follow-ups).
- [x] **Item E — background rendering** (`PLAN.md` §Deferred): 1–3 s of frozen UI per page per zoom
  on image-heavy documents. Its gate is already met; this is scheduling, not justification. **Scheduled as
  M152.2** (2026-09-19). Revised 2026-09-24: M152.2 now draws slow pages in pieces on the window's
  thread, not off it. The owner's call (2026-09-24): M152.2 is recorded as a potential fix for
  Item E. **M152.2 merged** ([#387](https://github.com/utyagi24/klarpdf/pull/387), 2026-09-24).
  **Done** (owner, 2026-09-24: *"I am happy with the fix and so lets use #389 to call Item E as
  done"*). M152 closes it: a slow page no longer freezes the window, because it is drawn in pieces
  on the window's own thread rather than off it, as E first proposed. Two pauses remain, accepted
  with it (`PLAN.md` §M152, *M152.2 as built*, *Its limits*). A page no bigger than the window is
  drawn whole the first time it is met: 0.6–1.7 s on the NADA cover. And a page's photos are
  unpacked in one step the first time at a new level of detail: 2.3 s on `IAS_CaseStudy.pdf`
  page 6. If either ever needs removing, the helper program from M152's plan is the fallback.
- **Code signing** stays deferred (needs a certificate) — it is the one gate item that may never be
  purchasable, so it is explicitly *not* a blocker for 1.0.

## Roadmap — "MCP / Agent Bridge" (**next**; version assigned at tag time)

Spec + architecture in `PLAN.md` §MCP / Agent Bridge roadmap. Same conventions: **one PR per
milestone**, tick the box here on merge. ⭐ marks the keystone (GUI-free, fully headless-testable).
A new MCP server surface (`klarpdf/mcp_bridge/` package) that reuses the GUI-free `klarpdf/model/` core **without PySide6**
and ships as a separate optional component — the `klarpdf-setup-x64.exe` audit surface is untouched.

**Scheduled 2026-08-12** after a premise review (the roadmap was written 2026-06-22 and sat while
R1–R6 shipped). The eight decisions it settled, and the premises that died with it, are in
`PLAN.md` §MCP / Agent Bridge roadmap → Revision note (2026-08-12) — **not restated here**. Two
status facts that belong here: the reserved **v0.11.0 is spent** (v0.12.0 → v0.17.1 shipped past it),
so M44 assigns a version at tag time; and the bridge goes **before** the three remaining 1.0 gate
items, which are independent of it.

Done, each with its record in its own file: [M38.5](docs/milestones/M38.md), [M39](docs/milestones/M39.md), [M40](docs/milestones/M40.md), [M41](docs/milestones/M41.md), [M42](docs/milestones/M42.md), [M43](docs/milestones/M43.md), [M43.1](docs/milestones/M43.md), [M43.2](docs/milestones/M43.md), [M43.3](docs/milestones/M43.md), [M44](docs/milestones/M44.md).

## Roadmap — document structure for agents (M138–M142, M145 and M148; M138–M141, M145 and M148 done, M142 deferred)

Design in `PLAN.md` §M138–M140 — **not restated here**. Same conventions: **one PR per milestone**,
tick the box here on merge. Scoped **2026-09-07** from a session comparing the bridge against a
competing multi-format document MCP server, and then against the owner's own three goals: structural information for agents when a PDF has no bookmarks; *enriching* PDFs by writing
that structure back as real bookmarks; and a Markdown rendering. The third is **not** scheduled —
it is carried in §Open follow-ups.

The session's finding is what set the order. On the owner's `WH-1000XM6.pdf` — 146 pages, **zero
bookmarks**, RC4-encrypted — the printed contents page is built from **591 named-destination link
annotations**, and those links carry title, target page **and** indent-derived level. A throwaway
script recovered **65 entries spanning pages 13–140** and wrote them with `set_toc`: encryption and
permissions intact, **+14.7 KB appended** to a 2.7 MB file. So structure does not have to be
inferred for a large class of documents — it is already in the file, and the bridge had no way to
read it. **M138 + M139 + agent judgement is the shippable feature**; M140 is the fallback for what
is left over.

**Amended 2026-09-10, after M139 shipped. The build order is `M141 → M140 → M142`** — decided by
the owner that day, and it is deliberately *not* numeric. Two findings moved it, one about value and
one about correctness.

**Value: "the fallback for what is left over" undersells M140.** The owner's question found the gap
— a document with only high-level bookmarks, whose caller wants second-level ones inserted, is
served by **nothing the bridge has**. `get_outline` gives the existing tree but not what is inside a
section; `get_links` does not help, because a document that already ships bookmarks usually has no
printed linked contents page; and `extract_text` returns plain strings with no weight, which is the
one signal separating an unnumbered subheading from body text set at the same size (measured:
Helvetica-Bold 10 pt against a 10 pt body reads identically through it). **Enrichment is the other
half of M139, not a leftover.**

**Correctness: M142 was scheduled ahead of a milestone it declares a dependency on.** Its own entry
says *"Depends on M140 + M141"* and `PLAN.md` §M142 says it *"depends on M140 and M141 landing
first"*, while it sat above M140 in this list. That sequence could not be executed as written, and
it was the one unambiguous error here.

**But M141 stays ahead of M140, and the first proposal to make the order numeric was wrong.** M140's
`in_table` flag needs table detection, and M141 is where that machinery lands together with the
decision M141 settles about how to call it (as rebuilt 2026-09-13: a drawn grid, or ruled and shaded
rows, else a decline — `PLAN.md` §M141). Building M140 first would mean making that call independently and probably differently. The
1→2 demotion of M140 encoded real engineering reasoning; only M142's placement did not. So M142
moves to the end and nothing else does. Argument and measurements in `PLAN.md` §M140 → *Ordering*.

**Amended 2026-09-17: the build order is `M141 → M140 → M145 → M142`.** The owner asked whether the
bridge should also list a document's charts and tables with their titles. Measured and decided the
same day: **no chart list**, because a PDF has no chart object and a prototype chart finder was
wrong on half the chart pages it was checked on. **Table titles get fixed first (M145)**, because
`get_tables` already titles every table but not reliably, and M142 prints what it returns. Both
arguments are in `PLAN.md` §M145.

**Amended 2026-09-18: M142 is deferred** (owner: *"I want to put off export to markdown for now"*).
The roadmap ends at M145. M142's design and its prototype stand unchanged, and when it is picked up
it still comes after M145, for the reason above.

Done, each with its record in its own file: [M138](docs/milestones/M138.md), [M138.1](docs/milestones/M138.md), [M138.2](docs/milestones/M138.md), [M138.3](docs/milestones/M138.md), [M138.4](docs/milestones/M138.md), [M138.5](docs/milestones/M138.md), [M139](docs/milestones/M139.md), [M139.1](docs/milestones/M139.md), [M141](docs/milestones/M141.md), [M140](docs/milestones/M140.md), [M145](docs/milestones/M145.md), [M148](docs/milestones/M148.md).

- [ ] **M142** **`extract_markdown`** — ***deferred by the owner, 2026-09-18; not scheduled.*** A Markdown rendering of a page range, built on M140 and M141
  with **no new dependency**. Prototyped before being scheduled: headings by weight/size, ruled
  tables exactly, and **multi-column reading order** all work; a partially-ruled financial table, a
  structured side panel, a drop cap and justified single-word lines are the known holes. **Binding
  rule: a table that cannot be reconstructed is emitted as-is with a note, never as a mangled grid**
  — the third application of *report the uncertainty, never paper over it*, after M140's `in_table`
  and M141's `continues_from`. A **separate tool**, not a `format` flag on `extract_text`, because
  the cost differs by two orders of magnitude (~930 pages/s against 6.9). Depends on M140 + M141,
  and on M145 (added 2026-09-17), so that it does not print a chart read as a table.
  Design, and why `pymupdf4llm` is rejected, in `PLAN.md` §M142.

## Roadmap — the open issues (M149–M152; planned 2026-09-19)

Grouping and order in `PLAN.md` §*The open issues, grouped* — **not restated here**. Every open
GitHub issue except [#352](https://github.com/utyagi24/klarpdf/issues/352) and
[#370](https://github.com/utyagi24/klarpdf/issues/370) (owner, 2026-09-19). Each milestone's
diagnosis and design happen in the session that builds it, and its open questions are settled with
the owner then. Same conventions: **one PR per milestone, or per part** where a milestone has parts;
tick the box here on merge. Build in number order.

Done, each with its record in its own file: [M149](docs/milestones/M149.md), [M150](docs/milestones/M150.md), [M150.1](docs/milestones/M150.md), [M150.2](docs/milestones/M150.md), [M151](docs/milestones/M151.md), [M152](docs/milestones/M152.md), [M152.1](docs/milestones/M152.md), [M152.2](docs/milestones/M152.md), [M153](docs/milestones/M153.md), [M154](docs/milestones/M154.md), [M155](docs/milestones/M155.md), [M156](docs/milestones/M156.md), [M160](docs/milestones/M160.md), [M161](docs/milestones/M161.md), [M163](docs/milestones/M163.md), [M164](docs/milestones/M164.md), [M165](docs/milestones/M165.md), [M166](docs/milestones/M166.md), [M175](docs/milestones/M175.md), [M176](docs/milestones/M176.md), [M177](docs/milestones/M177.md).

## Roadmap — linting, dead code and type checking (M167–M174; planned 2026-11-08, M174 added 2026-11-11)

**Planned 2026-11-08.** The owner answered the proposal's three questions the same day: no formatter
for now, mypy for types, and `lint` and `typecheck` as required status checks. The strategy, the
measurements, the rules for each surface, how the checks run in CI, and the decisions with their
reasons are in `PLAN.md` §*Linting, dead code and type checking*, and are **not restated here**.
Same conventions as the other roadmaps: one PR per milestone, or per part where a milestone has
parts, and the box here is ticked on merge. Build in number order, except that M172 needs only M167.

Done, each with its record in its own file: [M167](docs/milestones/M167.md), [M168](docs/milestones/M168.md), [M169](docs/milestones/M169.md), [M170](docs/milestones/M170.md), [M171](docs/milestones/M171.md), [M172](docs/milestones/M172.md), [M172.1](docs/milestones/M172.md), [M172.2](docs/milestones/M172.md), [M173](docs/milestones/M173.md), [M173.1](docs/milestones/M173.md), [M173.2](docs/milestones/M173.md), [M174](docs/milestones/M174.md), [M174.1](docs/milestones/M174.md), [M174.2](docs/milestones/M174.md), [M174.3](docs/milestones/M174.md).

## Roadmap — GUI feature tranche R1–R6 (planned; M45–M79)

Spec, per-milestone scope, and the binding **design budgets** (UI / lightness / honesty) in
`PLAN.md` §GUI feature roadmap. Owner-decided **2026-07-18** (23 features approved; radio-button
groups rejected → §Future enhancements); **R6 added 2026-07-22** from the macOS-Preview UI
comparison session (spec + the decided-against list in `PLAN.md` §GUI feature roadmap → R6). Same
conventions: **one PR per milestone**, tick here on
merge; ⭐ = keystone. **Zero new dependencies** across the tranche. Versions provisional
(v0.12.0 → v0.16.0 if the MCP bridge ships v0.11.0 first; assigned at tag time). (**R#** = release —
**G#** already belongs to the Public-Release Readiness milestones below.)

**R1 — "Navigate & Polish"** (prov. v0.12.0)

Done, each with its record in its own file: [M45](docs/milestones/M45.md), [M46](docs/milestones/M46.md), [M47](docs/milestones/M47.md), [M48](docs/milestones/M48.md), [M49](docs/milestones/M49.md).
- [x] **R1 polish — the Tools menu** ([#123](https://github.com/utyagi24/klarpdf/pull/123); owner-decided during the stack review): the tranche's one
  budgeted top-level menu (`PLAN.md` §Design budgets) lands with the tools it was reserved for —
  Select/Grab and the armed one-shots (Text Box · Highlight · Redact ×2 · Crop + Remove Crop) move
  out of View into **Tools**; **Rotate Left/Right moves to Edit** beside the other page operations
  (it is a real, saved edit — the View placement implied a view-only spin). Shortcuts and the
  toolbar are unchanged; R3's Markup/Draw and R4's Stamp land straight into Tools.
Done, each with its record in its own file: [M50](docs/milestones/M50.md).

**R2 — "Document Hygiene"**

Done, each with its record in its own file: [M51](docs/milestones/M51.md), [M52](docs/milestones/M52.md), [M53](docs/milestones/M53.md), [M54](docs/milestones/M54.md), [M55](docs/milestones/M55.md).

**R3 — "Markup Tools"**

Done, each with its record in its own file: [M56](docs/milestones/M56.md), [M57](docs/milestones/M57.md), [M58](docs/milestones/M58.md), [M59](docs/milestones/M59.md), [M59.5](docs/milestones/M59.md), [M59.6](docs/milestones/M59.md), [M59.7](docs/milestones/M59.md), [M59.8](docs/milestones/M59.md), [M59.9](docs/milestones/M59.md), [M59.10](docs/milestones/M59.md), [M59.11](docs/milestones/M59.md), [M59.12](docs/milestones/M59.md), [M59.13](docs/milestones/M59.md), [M60](docs/milestones/M60.md).

**R4 — "Stamp, Sign & Watermark"**

Done, each with its record in its own file: [M61](docs/milestones/M61.md), [M62](docs/milestones/M62.md), [M63](docs/milestones/M63.md), [M64](docs/milestones/M64.md).
- [ ] ~~**M65** Verify + release → tag~~ — **skipped by owner call (2026-07-20)**; R4's features
  ship with the next published release. Work continues at **R5 (M66)**.

**R5 — "Foreign Annotations & Form Fields"**

Done, each with its record in its own file: [M66](docs/milestones/M66.md), [M67](docs/milestones/M67.md), [M68](docs/milestones/M68.md), [M69](docs/milestones/M69.md), [M69.1](docs/milestones/M69.md), [M69.2](docs/milestones/M69.md), [M69.3](docs/milestones/M69.md), [M69.4](docs/milestones/M69.md), [M69.5](docs/milestones/M69.md), [M69.6](docs/milestones/M69.md), [M69.7](docs/milestones/M69.md), [M69.8](docs/milestones/M69.md), [M69.9](docs/milestones/M69.md), [M69.10](docs/milestones/M69.md), [M69.11](docs/milestones/M69.md), [M69.12](docs/milestones/M69.md), [M69.13](docs/milestones/M69.md), [M69.14](docs/milestones/M69.md), [M69.15](docs/milestones/M69.md), [M69.16](docs/milestones/M69.md), [M70](docs/milestones/M70.md).

**R6 — "Simplify & Read"** (shipped v0.16.1)

Done, each with its record in its own file: [M71](docs/milestones/M71.md), [M72](docs/milestones/M72.md), [M73](docs/milestones/M73.md), [M74](docs/milestones/M74.md), [M75](docs/milestones/M75.md), [M76](docs/milestones/M76.md), [M76.1](docs/milestones/M76.md), [M76.2](docs/milestones/M76.md).
- [x] **Dashed stroke style** (owner request while testing R6; extends M74's "line ends as style").
  `Line` / `Shape` / `InkStroke` gain a `dashed` bool: PyMuPDF bakes it as a PDF `/BS /D` border on
  line/square/circle/ink and reads it back on reopen, so the solid/dashed choice round-trips with no
  extra model state (the array is re-derived from the width at bake, scaled so a thick line dashes
  boldly). The style picker's **"Width" sub-menu becomes "Line Style"** — the three thicknesses plus
  a Solid/Dashed radio group (independent groups); `_drawn_pen` dashes the preview to match the bake;
  draw + restyle-in-place carry it like colour. **Trap fixed**: PyMuPDF silently writes no `/D` for
  *float* dash values, so `_dash_array` returns ints (the round-trip test catches it). Both renders
  verified to match (overlay + baked PDF). — *Windows (headless + offscreen GUI)* — 16 new tests,
  1178 green ([#169](https://github.com/utyagi24/klarpdf/pull/169))
Done, each with its record in its own file: [M78.1](docs/milestones/M78.md), [M75.1](docs/milestones/M75.md), [M63.1](docs/milestones/M63.md), [M63.2](docs/milestones/M63.md), [M77.1](docs/milestones/M77.md), [M79.1](docs/milestones/M79.md), [M79.2](docs/milestones/M79.md), [M79.3](docs/milestones/M79.md), [M71.1](docs/milestones/M71.md), [M77](docs/milestones/M77.md), [M78](docs/milestones/M78.md), [M78.2](docs/milestones/M78.md), [M78.3](docs/milestones/M78.md), [M78.4](docs/milestones/M78.md), [M78.5](docs/milestones/M78.md), [M78.6](docs/milestones/M78.md), [M78.7](docs/milestones/M78.md), [M78.8](docs/milestones/M78.md), [M79](docs/milestones/M79.md).

**Post-R6 (unreleased)**

Done, each with its record in its own file: [M80](docs/milestones/M80.md), [M81](docs/milestones/M81.md), [M81.1](docs/milestones/M81.md), [M81.2](docs/milestones/M81.md), [M81.3](docs/milestones/M81.md), [M82](docs/milestones/M82.md), [M82.1](docs/milestones/M82.md), [M82.2](docs/milestones/M82.md), [M83](docs/milestones/M83.md), [M83.1](docs/milestones/M83.md), [M83.2](docs/milestones/M83.md), [M84](docs/milestones/M84.md), [M84.1](docs/milestones/M84.md), [M84.2](docs/milestones/M84.md), [M85](docs/milestones/M85.md), [M85.1](docs/milestones/M85.md), [M85.2](docs/milestones/M85.md), [M86](docs/milestones/M86.md), [M86.1](docs/milestones/M86.md), [M86.2](docs/milestones/M86.md), [M87](docs/milestones/M87.md), [M87.1](docs/milestones/M87.md), [M87.3](docs/milestones/M87.md), [M87.2](docs/milestones/M87.md), [M88](docs/milestones/M88.md), [M88.5](docs/milestones/M88.md), [M88.6](docs/milestones/M88.md), [M89](docs/milestones/M89.md), [M89.4](docs/milestones/M89.md), [M89.5](docs/milestones/M89.md), [M89.6](docs/milestones/M89.md), [M90](docs/milestones/M90.md), [M90.1](docs/milestones/M90.md), [M90.2](docs/milestones/M90.md), [M90.3](docs/milestones/M90.md), [M90.4](docs/milestones/M90.md), [M91](docs/milestones/M91.md), [M91.1](docs/milestones/M91.md), [M91.2](docs/milestones/M91.md), [M91.3](docs/milestones/M91.md), [M91.4](docs/milestones/M91.md), [M92](docs/milestones/M92.md), [M92.1](docs/milestones/M92.md), [M92.2](docs/milestones/M92.md), [M92.3](docs/milestones/M92.md), [M92.4](docs/milestones/M92.md), [M92.5](docs/milestones/M92.md), [M92.6](docs/milestones/M92.md).
- **Corner-case document analysis** (`PLAN.md` §The corner-case document) — `IAS_CaseStudy.pdf`,
  owner-supplied: 75.6 MB, 18 pages of 1920×1080 pt, **no text layer**, 95 MB of embedded images.
  Opens in **11.19 s**, of which **10.0 s is `fz_run_display_list`** — decoding imagery, not our
  overhead. Corrects two planning assumptions: such documents are **decode-bound, so render cost is
  flat across scale** (M83 costs them ~3× memory but no extra time — an earlier estimate of ~3× slower
  was wrong), and **every distinct zoom re-decodes every visible page** (47–126 ms at the same zoom,
  1976–4831 ms at a new one), which is the reported zoom lag.
- **Deferred, with the condition to revisit** (`PLAN.md` §Deferred): **C** pixmap preview during the
  gesture — only if M86 + M87.1 leave zoom sluggish; **independent of E**, and E would make C *more*
  valuable, not redundant (owner correction). **D** quantised zoom ladder — argued against, recorded
  so it isn't re-proposed as a free win. **E** background rendering — the only item that answers "the
  app must not appear blocked on a heavy document"; all rendering is synchronous today (verified: zero
  threading in the codebase). Owner call was **F now, E only if still needed after M86 + M87.1** — and the
  corner-case measurement above **has met that gate**: 1–3 s per page per new zoom is irreducible
  decode work, so A/B/F reduce how often we pay it but only E stops the freeze. Now a scheduling
  question, not a justification one.

Done, each with its record in its own file: [M103](docs/milestones/M103.md), [M102](docs/milestones/M102.md), [M99](docs/milestones/M99.md), [M100](docs/milestones/M100.md), [M115.1](docs/milestones/M115.md).

**Scoped 2026-09-04** — a four-milestone sequence changing how the bridge is shipped, from the
owner's question *"I am thinking of changing how we ship our MCP and how the end user can install/
configure it easily."* Listed in **execution order** rather than newest-first, because each depends
on the one above it. Every decision, every rejection and every measurement behind them is in
`PLAN.md` §M133–M136 — **not restated here**. The headline: the install goes from nine commands to
`python install.py`, or to `uvx --from klarpdf klarpdf-mcp` for anyone who already has `uv`.

Done, each with its record in its own file: [M162](docs/milestones/M162.md), [M159](docs/milestones/M159.md), [M158](docs/milestones/M158.md), [M157](docs/milestones/M157.md), [M147](docs/milestones/M147.md), [M146](docs/milestones/M146.md), [M144](docs/milestones/M144.md), [M143.1](docs/milestones/M143.md), [M143](docs/milestones/M143.md), [M137](docs/milestones/M137.md), [M133](docs/milestones/M133-M136.md), [M134](docs/milestones/M133-M136.md), [M135](docs/milestones/M133-M136.md), [M136](docs/milestones/M133-M136.md), [M132](docs/milestones/M132.md), [M131](docs/milestones/M131.md), [M130](docs/milestones/M130.md), [M129](docs/milestones/M129.md), [M128](docs/milestones/M128.md), [M127](docs/milestones/M127.md), [M126](docs/milestones/M126.md), [M125](docs/milestones/M125.md), [M124](docs/milestones/M124.md), [M123](docs/milestones/M123.md), [M122](docs/milestones/M122.md), [M121](docs/milestones/M121.md), [M120](docs/milestones/M120.md), [M119](docs/milestones/M119.md), [M118](docs/milestones/M118.md), [M117](docs/milestones/M117.md), [M116](docs/milestones/M116.md), [M115](docs/milestones/M115.md), [M114](docs/milestones/M114.md), [M113](docs/milestones/M113.md).

- [ ] **M112** *(unplanned)* **The bridge can *edit* an annotation, not only add one** — owner-asked
  2026-08-21, correcting an earlier framing in the same session. The app handles other tools' marks
  **well**: M66 deletes a foreign annotation, M67 moves one with its appearance intact, M68 **adopts**
  one on double-click into an ordinary editable KlarPDF mark, M90.4 shows its note — after which
  recolouring, re-noting and merging all work. The gap is that the **bridge has none of it**: after
  M101 an agent can read a colleague's marks and add beside them, and that is all. M101 scoped this
  out correctly for that milestone, but an undated exclusion is how a decision becomes a permanent
  gap. **Why it matters:** M101's review loop assumes the person reviews *in KlarPDF*, and reviewers
  use whatever their employer installed — so a real round trip comes back carrying Acrobat or Edge
  marks, against which the bridge can only stack. Adoption is the principled answer and already
  exists. **Naming a mark is this milestone's hard part, and it is unsolved** — an earlier draft of
  this entry said `fingerprint()` already provided one and scheduled it into the M101 fix PR;
  measurement withdrew that. For our own marks the identifier **silently rebinds**: PyMuPDF names
  annotations by *position* (`fitz-A0`, `fitz-A1`, …) and our marks are re-created from descriptors
  on every save, so after one mark absorbs another, an id that meant one highlight resolves to a
  different one — an edit sent to it hits the wrong mark with no error. Whether foreign marks carry
  a better name is **unverified**: both corpus files checked turned out to have been annotated by
  this app under its old codename. Design in `PLAN.md` §M112 — *WSL*

Done, each with its record in its own file: [M110](docs/milestones/M110.md), [M111](docs/milestones/M111.md), [M109](docs/milestones/M109.md), [M108](docs/milestones/M108.md), [M107](docs/milestones/M107.md), [M106](docs/milestones/M106.md), [M105](docs/milestones/M105.md), [M101](docs/milestones/M101.md), [M98](docs/milestones/M98.md), [M97](docs/milestones/M97.md), [M96](docs/milestones/M96.md), [M93](docs/milestones/M93.md), [M94](docs/milestones/M94.md), [M95](docs/milestones/M95.md).

## Public-Release Readiness — go open-source under AGPL-3.0 (planned)

**The repo is public** as of **2026-07-17**, as an `AGPL-3.0-or-later` project — the flip (G8) is done.
**G1–G5, G7 and G8 are complete, and so is `G6 Part 2` (enrol in GitHub Sponsors, 2026-09-24).**
All that is left is to confirm the repository page shows its Sponsor button, and nothing depends
on it. Independent of the v0.11.0 MCP roadmap — this track landed first. Full
execution detail in `PLAN.md` §Public-release readiness (plan introduced in
[#83](https://github.com/utyagi24/pdfproj/pull/83)). **One PR per item**; tick the box on merge and
append the PR link. Steps were ordered — **G1 ran first, while the repo was still private**, and the
flip itself was a manual GitHub action, not a PR. The pre-public hygiene scan was clean (no secrets in
tree or history; `.gitignore` excludes build artifacts/wheels/`report.json`; CI uses `${{ secrets.* }}`).

Done, each with its record in its own file: [G1](docs/milestones/G1.md), [G2](docs/milestones/G2.md), [G3](docs/milestones/G3.md), [G4](docs/milestones/G4.md), [G5](docs/milestones/G5.md), [G5.1](docs/milestones/G5.md).
- [ ] **G6** Donations — repo + product — let users support the project. **Platform: GitHub Sponsors**
  (decision gate closed) — same host as the source link, so supporting the project introduces no
  third-party domain into the app or the repo. Open-source + donations is fully AGPL-compatible.
  - [x] **Part 1 — the code** — `.github/FUNDING.yml` (`github: utyagi24`); README **Support** section
    + Sponsor badge; **Help ▸ Donate…** (extends the G4 Help menu, grouped with *View Source* — the
    separator splits "opens a dialog" from "hands a URL to the browser") + an About-dialog link, both
    via `QDesktopServices.openUrl` on **user click only, so the offline / no-telemetry guarantee holds**
    (the app opens no socket itself). A test asserts `FUNDING.yml` and `ui/about.py` name the *same*
    account — see the trap below. — *WSL + WSLg* — [#107](https://github.com/utyagi24/klarpdf/pull/107)
  - [x] **Part 2 — enrol the account in GitHub Sponsors (manual; the actual gate)** — Stripe +
    identity + GitHub review, so it takes **days**, not minutes: start it early. Verify with
    `gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'` → currently **`false`**.
    **The trap:** `https://github.com/sponsors/utyagi24` does **not** 404 without a listing — GitHub
    silently **redirects it to the plain profile page**, so a dead Donate link is indistinguishable
    from a working one, in the app and in CI. Nothing automated can catch this; hence the one-time
    gate in `RELEASE.md` §3, to be checked before the first release that ships the menu item.
    **This is now the only thing standing between the project and a finished public-release track**
    (G1–G5, G7, G8 all done). Two consequences of that ordering, both live today:
    - the in-app **Help ▸ Donate…** and About link **already shipped** in v0.10.0, so they currently
      lead to a silent redirect to the plain profile — harmless, but wrong;
    - the repo **Sponsor button** (the check moved here from G8) needs the repo public — ✅ since
      2026-07-17 — *and* the listing. So the listing is now the sole remaining blocker for it.
    Verify both once enrolled: `hasSponsorsListing` → `true`, and the Sponsor button renders on the
    repo page. — *GitHub account*
    **Enrolled** (owner, 2026-09-24). `hasSponsorsListing` is now `true`, the listing is public, and
    `https://github.com/sponsors/utyagi24` answers 200 with no redirect, so the in-app link works.
    **The Sponsor button is not confirmed yet.** The repository page as GitHub served it on
    2026-09-24 had no Sponsor button, only the README's own links. GitHub may add the button after
    the page loads, so the owner confirms it on the page. If it is missing, it is the repository's
    *Sponsorships* setting (Settings ▸ General ▸ Features).
Done, each with its record in its own file: [G7](docs/milestones/G7.md), [G8](docs/milestones/G8.md).

## Open follow-ups (carried)

Carried items — none block work.

**Scope, since 2026-08-25:** this section holds what still needs a **decision** — deferred design
questions, rejections and their reasons, trade-offs measured but not settled. A known defect in the
released build or in the code on `main` that is unambiguous and readily reproducible goes to
[**GitHub Issues**](https://github.com/utyagi24/klarpdf/issues) instead, where it can be closed by
the PR that fixes it. See `CLAUDE.md` §How we work for the split and why. Items already carried here
were not migrated wholesale: each is listed because a decision is outstanding, which is what keeps
it on this side of the line.

- **Replace the CRLF `SHA256SUMS` on the published releases?** — no (owner, 2026-11-13). The 11
  from v0.12.0 on are immutable releases and cannot be changed, and the 18 older ones stay as they
  are too. Reasons in `PLAN.md` §M177.
- **Say what a bare `tuple`, `dict` or `list` holds?** — graduated to **M174** (owner, 2026-11-11:
  all 445, as a new milestone).
- **Move a text box up at the bottom of the page, as Edge does?** (M166, deferred by the owner
  2026-11-07). When a box gains lines at the bottom of the page, by narrowing it, typing or a
  bigger font, it now grows only to the page edge, and lines that still do not fit are cut off,
  in KlarPDF and in the file alike. Microsoft Edge moves the box up instead, so text is cut only
  once the box is as tall as the page. That would lose no text in any realistic case, but moves a
  box from where it was put, can cover what is above it, and would also move boxes in older files
  when they are redrawn. Spilling onto the next page was ruled out: an annotation belongs to one
  page, so the text would become two unrelated boxes. **The owner kept cutting off for now and will
  revisit if it proves unacceptable in use.** The work would go in `fitted_to_its_lines` (move the
  top up by the overflow, no higher than the page top), the editor's `reposition_editor` and the
  width handle's `_reflow_textbox_rect` (`PLAN.md` §M166).
- **Report MuPDF's new `/ID` upstream?** (M161, found 2026-11-02). MuPDF writes a fresh `/ID` on
  every save, even when `PDF_ENCRYPT_KEEP` keeps a key that was made from the file's missing one.
  So any program saving such a file through MuPDF writes a copy nobody can open. `pin_file_id`
  works around it here. **Open question:** report it to Artifex, and if they fix it, drop the pin
  or keep it, since it costs nothing. The pin has to stay until whatever PyMuPDF build we pin
  carries the fix. `PLAN.md` §M161.

- **Dead code and coverage** (found 2026-09-26) — graduated. The coverage gate was rejected and a
  static dead-code check chosen instead, with today's findings to triage, in
  [#409](https://github.com/utyagi24/klarpdf/issues/409). `PyPdfEngine` became M156
  ([#407](https://github.com/utyagi24/klarpdf/pull/407)). #409 is planned as M169 in
  §*Roadmap — linting, dead code and type checking* (2026-11-08).

- **With several shapes selected, the style buttons show one style, not the group's** (M154, found
  2026-09-24). They show the first shape's style after Ctrl+click, or the style they already held
  after a box selection. So the opacity slider can show 100% while both shapes are at 60%. A change
  still sets only that setting on each shape (M154). But choosing the value the slider already
  shows does nothing, because the slider reports only a move. **Open question:** should the
  buttons show that the shapes differ, and what should the slider show then? `PLAN.md` §M154.

- **Going back to a zoom already drawn redraws a slow page's pieces** (M152.2, found 2026-09-24).
  A page drawn in pieces is kept whole in the store only once every tile is drawn, which needs the
  page to fit across the window. So on the NADA cover, 375% back to 300% was instant before and now
  takes 3.3 s to sharpen, with the window responsive throughout. **Open question:** keep the
  pieces of the last size or two, for their memory (about 16 MB for the cover's window at 400%),
  or accept the redraw. `PLAN.md` §M152, *M152.2 as built*.

- **No test pins the 2 px margin around each piece** (M152.2, 2026-09-24). Without it, MuPDF
  smoothed one row of a shape's edge on the NADA cover differently where a piece's clip cut
  through it. A test page of slanted shapes, solid or see-through, did not show the flaw, so the
  margin is backed only by the measurement on the cover. **To do:** find a small page that shows
  it, and make it a test.

- **Whole-page redraws could keep a display list too** (M152.2, measured 2026-09-24). `Page.get_pixmap`
  reads the page on every call. On some corpus pages that reading is most of the cost: page 14 of
  `Patina-at-RiverSound-Brochure (reduced).pdf` took 217 ms to read and 190 ms to draw at 100%. M152.2 keeps a display list
  only for pages drawn in pieces. **Open question:** keep one for every page in the band, so a
  zoom or a resize redraws such pages in about half the time, for the memory the lists take, which
  PyMuPDF does not report.

- **A resize with a zoom you chose yourself (no fit on) does not keep the top line at the end of
  the document** (M151, found 2026-09-21). With a fit on, a resize keeps the line at the top of the
  window where it is (owner's rule, `PLAN.md` §M151). With no fit, the view stays wherever Qt
  leaves it. That keeps the top line everywhere except at the end of the document: there a taller
  window has to show more above, which pushes the line down, and making it shorter again does not
  bring the line back. Not reported by anyone. **Open question:** should the owner's rule cover a
  zoom you chose as well? The same code would do it (`PdfView.resizeEvent` returns early when no
  fit is on).

- **The WSLg window-minimum snap-back is platform-motivated code sitting in cross-platform code**
  (M149 / [#358](https://github.com/utyagi24/klarpdf/issues/358); raised **2026-09-20** by the owner,
  who asked how it honours `CLAUDE.md` §*Keep OS-specific code quarantined*). **Kept for now**, owner's
  call the same day (*"keep the workaround for now"*) — recorded so the argument is not re-run.

  The facts. `MainWindow.resizeEvent`'s snap-back contains **no OS branch** — nothing in
  `platform_integration.py`, no `sys.platform`, and the only mentions of WSLg are comments. But it can
  only ever *fire* where the window system ignores Qt's minimum-size hint: on Windows the window
  manager enforces the floor, so `resizeEvent` never sees a sub-floor size and the timer never starts.
  It is dead code on the platform that ships. So it is platform-motivated code in a platform-neutral
  shape, which is how OS-specific behaviour gets *past* that rule rather than through it. The issue's
  own note — *"This is a size set on `MainWindow`, not OS-specific code"* — was written about
  `setMinimumSize`, which genuinely is neutral, and does not cover the snap-back; it was carried over
  without being re-tested against the new code.

  **What is undecided:** whether the guard should move behind a named predicate in
  `platform_integration.py` (say `window_minimum_is_advisory()`), so the seam is visible and the dead
  Windows path is provable; or be dropped entirely, since WSL is a development environment and not a
  product surface (`CLAUDE.md` §*Two consumers share one core*, closing paragraph), leaving #358 fixed
  by `setMinimumSize` alone as it already is on Windows. The counter-argument for leaving it as a
  general invariant: *"a window must never end up below its floor"* is true on any platform, and a
  future one could behave like WSLg.

  **One cheap experiment is outstanding and would settle it:** measured 2026-09-20, Qt declares the
  minimum correctly on both paths — `xdg_toplevel.set_min_size(400, 300)` is sent under Wayland (seen
  in a protocol trace; the compositor replies `configure(0, 0)` and does not enforce it), and
  `WM_NORMAL_HINTS` carries `PMinSize = 400 x 300` under **xcb** (read back with
  `XGetWMNormalHints`). Nobody has yet dragged an edge with `QT_QPA_PLATFORM=xcb`. If XWayland's window
  manager honours the hint, the whole workaround becomes one environment variable in the dev docs. The
  caveat to weigh if xcb were adopted as the standing WSLg default: the Qt xcb and Wayland plugins
  differ in DPI handling and in the wheel/touchpad event stream, which is the most heavily tuned part
  of this app (M91.4's coast-mute, M92's scroll distances).

- **Several tables in one ruled region: solved where the region fails, open where it succeeds** (M141;
  raised 2026-09-13, half-closed 2026-09-16 by TC-039's fixes). A region that declines is now read in
  the parts its prose divides it into, which returned Apple p14's three notes, p10's and p20's two
  each, and two statement pages. What is left is the pages that read *successfully* as one table with
  a caption and its lead-in sentence inside a cell — the SpaceX prospectus p274 ("Note 8 — Financial
  Instruments" and its sentence in one cell) and LLY's proxy p56. M140's measurement (2026-09-17) found
  three more, each with a note's *heading* and its paragraph in one cell: NVIDIA's annual report p151
  (`Note 4 - Net Income Per Share`) and p167 (`Note 17 - Leases`), and Apple's 10-Q p13 (`Note 6 –
  Debt`). That now matters outside `get_tables`: `get_heading_candidates` with `tables: true` reports
  those headings as table text. Recovery never sees them, because it
  only runs on failures, and that is deliberate: the rule that would catch them ("a lone line crossing
  more than one column is prose") also matches a legitimate spanning group header, so applying it to
  tables that read would risk cutting one below its header. Decision owed: extend the split to
  successful reads behind a stricter signal, or treat it as the title problem the owner reads it as
  (2026-09-16) and let the caption machinery claim those lines.
  **That risk is now measured** (2026-09-19, M148, as the second fix #369 proposed). Applied to
  every band of every ruled page in the corpus, the rule changed what 214 pages return and **lost
  the only table on 49 of them**. Spanning headings, units lines and wrapped labels are lone lines
  across a gap too: Broadcom p84's `(In millions)`, `Total Reportable` over `Segments` in both
  SpaceX prospectuses, and a 54-row table on the SpaceX prospectus's p291. So the stricter signal is
  still owed. The same round added cases. On Cisco's 10-K p46, p48 and p74 and salesforce p19,
  the prose between two tables is split into rows by underlined run-in headings. And the SpaceX
  prospectus, a browser print, shows the shape at a table's edge: its page frames are full-width
  lines, so the biographies after p196's table stay in its last rows.
- **Columns from what the page draws, not only from whitespace** (M141, measured 2026-09-16; owner's
  proposal). Where a table draws segments, the narrowest mark a column and a wider one with a heading
  above it marks a group — so Cisco p32's `$ 17,252` is one cell rather than two, and AMZN p7 is five
  columns with two groups. Measured across the corpus: joining collinear pieces within `_SNAP` is what
  separates a dashed full-width rule (1.1 pt gaps) from real column gaps (3.7–5.4 pt), but adopting
  marks as the column source changes the shape of **35 of 36** ruled tables (every `$` joins its
  figure), and "text spilling past its mark means decline" would decline Cisco p61 — whose free-text
  first column carries an underline under "Years Ended" only — unless such a column is exempt. The
  narrow form, splitting a line at a word gap that sits on a drawn boundary, was prototyped and
  rejected: it splits ~20 legitimate spanning headers and prose lines to fix 9 glued cells (`(3,027) $
  132,420`), and AMZN p10's trailing `$` needs its column *divided*, not its line split. Sized as its
  own milestone: it rewrites most expectations and needs its own blackbox round.
- **Two TC-039 findings are the documents, not the reader — no change** (M141, 2026-09-16). AMZN's
  Q2 release p9 prints `%` only on the first row of each block (*North America*); the other rows print
  bare numbers, and the tool returns exactly that — confirmed on a render. The 2021 rental statement
  p1 prints *Amount* to the left of its amounts, sharing no x-range with them, so heading and figures
  fall in separate columns; putting them together would mean deciding that a heading belongs to the
  column beside it, which the page does not state. Recorded so a later round does not re-derive
  either; the tester has been told both (`TC-039-RESPONSE-fixed-and-deferred.md`).
- **A grid with no lines between its printed rows returns each column as one multi-line cell —
  documented, not split** (M141, TC-040, decided 2026-09-16). A Treasury TIPS index-ratio report
  (`CPI_20260911.pdf`, 14 pages) boxes a month of daily figures under one drawn row and a block of
  labels beside a block of values under another. The daily cells line up one for one on every page;
  the only short column is a matured security's, and it holds the first 15 dates. The label block
  does not: a wrapped name or a second additional-issue date adds a line to one cell only — 29 cells
  on 13 pages — and on p1 the line counts still match, so pairing lines by position is wrong there
  without any sign of it. The owner gave the tool's output to several AI agents with a one-line prompt
  and every one rebuilt the rows correctly, pairing by content. Splitting a drawn row wherever its
  lines align across cells was measured as the alternative: it changes 25 of 67 grid tables — 11 of
  the corpus's 53, NADA p5 and dhariwal p76 among them — and leaves 8 with rows it cannot split
  safely, forms included. So `klarpdf://docs/get_tables` says to pair such lines by what they say.
  Revisit if a caller that pairs by position turns up. TC-040 also found the tool description
  promising a check the reader cannot make — "no cell holding two separate pieces" cannot see two
  values printed as one run — so it now says "separately printed" and the known limits name the case;
  the fix itself stays with columns from drawn segments (above).
- **A small ruled table on a page with little other text is not found at all** (M141). PyMuPDF's
  row-ruled finder needs ten words lined up down the page before it locates a region, so a four-row
  table alone on a page reports no table (`tests/test_mcp_tables.py` uses twelve-row fixtures for this
  reason). Reading ruled blocks straight from the drawn edges would reach it, but that is new
  region-finding with failure modes of its own — a page's header and footer rules make a "block" too —
  so it waits for a document that needs it.
- **`find_tables` switches a process-wide PyMuPDF setting while it runs** (M141). It turns
  `set_small_glyph_heights` on for the duration, and the MCP SDK runs tool calls concurrently in worker
  threads, so a `search` or `redact_text` executing at that moment would measure glyph boxes tighter than
  usual. `get_tables` serialises itself (`tables._READ_LOCK`) but cannot serialise other tools;
  redaction re-reads what it wrote before reporting success, so a miss would surface as a failed call
  rather than a leak. Decision owed: serialise every tool call in `server.guarded` — one lock, changing
  nothing but concurrency — or accept the window.
- **Text under 1 pt: `get_tables` skips it, `extract_text` and `search` do not** (M141; the owner asked
  where such text comes from, 2026-09-13). A market report's page carries a regular-weight copy of a
  table's Total row at 0.1 pt, drawn just before the bold row a reader sees, so `extract_text` returns
  the figures twice and `search` finds a second hit of 0.56 × 0.12 pt. Whether the text tools should
  flag or skip it is a change to those tools; TC-033 separately notes `search`'s `invisible` flag does
  not cover text that is invisible by size. Decision owed.
- ~~**`title` misses a statement heading that shares a text block with the company name**~~ —
  **graduated 2026-09-17 into M145, and settled there** (2026-09-19): the statement's name comes back,
  with the company's name when the page prints the two in one block.
- **A table border drawn in white takes the title in as the first row** (M141; found 2026-09-17
  while measuring M145). On Tesla's Q2 2026 update p7, `get_tables` returns *Installed Annual
  Capacity* as the table's first row and `header`, with `title: null`. (Since M145 the `title` comes
  back too, read from that row as the table's banner; the row itself is still there.) A reader sees a title above the
  grey header band (title at y 135–157, band from y 162). But the file draws the table's border as
  **white** lines on the page's white background, and the top edge is at y 130, above the title. By
  the lines the page draws, the title is inside the table; by what a reader sees, it is not. The
  same kind of problem as the hidden text in [#352](https://github.com/utyagi24/klarpdf/issues/352):
  something a reader cannot see changes what is read. Decision owed: whether an edge drawn in the
  colour of what lies beneath it counts as a table edge. The check compares two colours the page
  states, but the background can be an image or a gradient, and the answer changes which regions
  every drawn grid finds. Not a title rule, so it stays out of M145.
  **A second case** (found 2026-09-19, during M148): Broadcom's 10-K p63 draws each line of the
  paragraphs under its purchase-price table on a white-filled rectangle. The reader takes those as
  shaded bands, so three paragraphs come back as the table's last rows.
- **A region whose table does not read without the band keeps its old reading** (M148,
  2026-09-19). M148 reads a region without a top or bottom band that is not the table's only when
  what remains gives a table; otherwise it reads the region as it did before. That protects declined
  tables whose text would otherwise leave the declined box (Cisco's annual report p57), but it
  also keeps one wrong reading. The IPO prospectus's p428 returns its lease table with a paragraph
  in row 0, because without that band the table declines as stacked. Decision owed: decline such a
  region over its whole located box, so no text leaves it, instead of returning the old reading. It
  is one page of the corpus (found in two copies of the same document).
- **A declined region can reach over a table that was returned** (noticed 2026-09-19, during M145).
  On the survey report's p16 (`Research Doc2_Market Research Report_6.25.19.pdf`), `get_tables`
  returns two grids starting at y 311 and declines a region as `stacked` that runs from y 191, over
  the infographic above them, down to y 694, across the first grid. Its box covers the paragraph
  between the infographic and the grids, and part of a table that was returned. Not diagnosed:
  whether the ruled pass should have seen the grids' claim first, and whether an unread region may
  overlap a returned table at all. Decision owed after the diagnosis.
- **Text with no usable character map comes back as control characters** (noticed 2026-09-19). GE's
  refrigerator guide sets whole lines in a font whose codes are its letters shifted by a fixed amount
  (`6\VWqPH` for `Système`), with a control character for each space. `extract_text`, `search` and
  `get_tables`' cells return it as it is, and nothing tells the caller it is unreadable; `title`
  declines such a line (M145). Decision owed: whether the text tools should flag text that no reader
  can read.
- **`table_pages` roughly doubles what `extract_text` costs** (M141, measured 2026-09-13). Counting a
  page's ruled lines and shaded bands took 1.0–1.6× as long as reading its text on the table corpus,
  at most 17 ms on any page — not the half that #348's docstring stated. It stays a small fraction of
  `get_tables`, which took 72× the text. Untried: `get_cdrawings()`, which skips building a Python
  object for every path and might give the same count for less. Decision owed only if a caller finds
  `extract_text` slow.
- **PR #348, the first M141 attempt, is superseded** by the rewrite and left open for the owner to
  close; its description and the tester's reports hold the history, and nothing in it is needed now.
- **Expectations about private documents live outside the repository** (M141).
  `tools/table_corpus_check.py` runs from a plan file, and the committed plan names public filings and
  reports only. The plan for the owner's statements, payslips and forms was written to the local test
  folder beside `check-row-words.py`.

- **`set_outline` is untested on four shapes TC-025 names** (2026-09-10). An outline **deeper than
  two levels** written by this tool — NVIDIA's 8-level outline read back correctly through `merge`
  in TC-024, but nothing has *written* more than two; `set_outline` on a real **encrypted** document
  (`WH-1000XM6.pdf`, `Cisco_FORM10-K.pdf`) and on a real **tagged** one; and **empty-string / very
  long titles**. The encryption and tagging promises are asserted at unit scale in
  `tests/test_mcp_set_outline.py` and an empty title is refused, so this is not an open defect — it
  is that the claims have not met a document of that shape. Needs a decision only in the sense of
  scheduling: fold into the next manual round, or write fixtures for the depth case, which is the
  one a unit test can actually cover.
- **An authored outline is pinned to the page numbers it was written against** (M139, 2026-09-10).
  `VirtualDocument._outline_override` holds `[level, title, page]` in *output* page numbers and is
  validated against the page count as it stands when it is set. Nothing re-points it afterwards the
  way `remap_toc` re-points a document's own outline, so a page moved or deleted between authoring
  and saving would leave the entries aimed at the old positions. **Not reachable today and not a
  defect**: the only writer is the bridge's `set_outline`, which authors and materialises in one
  call with no page edit in between, which is also why the override is left out of `subset()` and of
  the undo snapshot. The decision is owed by whoever builds the **GUI** outline editor this
  capability was deliberately placed in the core to enable — re-point the override on every page
  edit (a second `remap_toc` caller), or drop it and make the user re-author. Worth settling before
  that editor exists rather than during it.
- ~~**A `get_tables` tool on `find_tables()` plus a shape filter**~~ — **graduated 2026-09-09 into
  M141** (see the roadmap above; design in `PLAN.md` §M141). All three open questions were decided
  by the owner: the strategy is chosen per page and **not** reported (*"I see no value in disclosing
  the strategy unless we expect the calling agents to perform some post processing based on it"*);
  the parenthesis repair is a rule with the residue documented (*"we are not aiming to be 100%
  accurate… don't over compensate for the corner cases"*); and the title/continuation gap was
  rejected as a gap at all (*"we can't expect tables to be present as a whole on a single page"*) —
  it is in scope. The finding worth keeping out of the verification that preceded it: the first
  round's conclusions were reached by comparing PyMuPDF's table output against PyMuPDF's text output
  and reading **consistency as correctness**, and were wrong. Redone against hand-read values from
  the rendered page, `strategy="text"` matched **18 of 18** rows on the Assets & Liabilities
  statement. Nothing carried. *Superseded 2026-09-13 by the rewrite:* a page with neither a drawn
  grid nor ruled rows is declined rather than read with `strategy="text"`, and no parentheses are
  repaired, because cells are built from whole words — `PLAN.md` §M141.

- ~~**Should the bridge offer a Markdown rendering of a PDF, and if so through `pymupdf4llm` or our
  own code**~~ — **graduated 2026-09-09 into M142** (roadmap above; design in `PLAN.md` §M142).
  Decided *"I want markdown, lets do it"* after a prototype answered the capability question with
  evidence. Ours, not rented: the deciding measurement is that **importing `pymupdf4llm` changes
  plain PyMuPDF's results globally** — 24 data rows to 3 on an identical call — which in a
  one-process server means an import for Markdown degrading `get_tables` beside it. Nothing carried.

- ~~**Classifying headings in code, with no agent in the loop — and whether `pymupdf_layout` ever
  earns its place**~~ — **rejected by the owner 2026-09-09.** Raised 2026-09-07 as the third rung of
  M140's split: **1a** candidate extraction (ours, mechanical — M140), **1b** classification by the
  calling agent (no algorithm needed), **1c** classification *in code*. 1c existed for one reason
  only — **the GUI has no LLM in it**, so a "generate bookmarks" menu item in the viewer could not
  use the agent-in-the-loop form.

  **The owner's answer removes the premise:** *"No 1c needed. I expect users to use MCP bridge
  services to close the gap where app is unable to do it."* The app and the bridge share a core but
  are **not** expected to reach capability parity; where the viewer cannot do something, the answer
  is the bridge, not a second implementation in the core to serve a consumer that has no agent. That
  is a standing architectural position, recorded in `PLAN.md` §M138–M140, and it settles more than
  this item.

  **It closes the `pymupdf_layout` question outright.** 1c was the only remaining place a layout
  model could have earned its ~11 pins and ≈100 MB — every other use (heading detection, tables,
  the side panels in `kasaragodhr.pdf`) is either served by what we already pin or answerable by an
  agent reading a `render_page` image. Nothing carried. Two things are worth keeping so they are not
  re-derived: `pymupdf4llm` **1.28.x remains rejected** on its own separate grounds (`PLAN.md`
  §M138–M140), and the entry's original claim that *"an LLM is a better classifier than any
  typographic rule"* was later sharpened by the prospectus — bold-at-body-size is a strong, cheap
  signal that a size-based rule misses entirely — so a code-side classifier was less hopeless than
  this item implied. That does not revive it; it only means the rejection rests on the architecture,
  not on 1c being infeasible.

- ~~**`install.py --client claude-code` registers at the client's default scope — `local`**~~ —
  **closed 2026-09-09**, graduated into **M143** above: `--client-scope` is now mandatory with
  `--client`. Worth keeping the correction that changed the fix, so it is not re-derived: this was
  first written up here as an *inconsistency* between the printed hints (`--scope user`) and the
  automation, which makes "default to `user`" look obvious. It is not one — `install.py` has no
  scope policy to contradict, and deferring to each client's own default is coherent and uniform.
  The real objection is that a directory-scoped default assumes you invoked it from the directory
  you care about, and a wrapper run from a download directory breaks that assumption rather than
  disagreeing with anything. Hence neither default, and a required flag.

- **`install.py --client X` exits 0 when X is not installed** — noticed 2026-09-09, asked by the
  owner while reviewing M143. `configure_client` checks `shutil.which(argv[0])`; when the client is
  absent it prints `` `gemini` is not on your PATH — skipping, the command is printed below. ``,
  falls back to the printed commands, and **returns success**. The same soft landing covers a client
  that *is* installed but whose `mcp add` fails — an stderr excerpt, then the fallback, then exit 0.
  **The argument for it:** the install genuinely succeeded, registration is opt-in convenience, and
  a provisioning run should not fail because a client is not on that machine yet. **The argument
  against:** a script doing `install.py --client claude-code && echo configured` is told the wrong
  thing, and the one line saying otherwise scrolls past inside a wall of successful output. Options
  are (a) leave it and document the exit code, (b) a distinct non-zero exit for "installed but not
  registered" — which a caller must then learn, and which breaks anyone treating non-zero as fatal,
  or (c) a `--require-client` flag that opts into strictness. Not decided; nothing is silently
  wrong, since the message is printed and the manual commands follow it.
  `packaging/mcp/installer/install.py`.

- **The `.mcpb` carries `QUICKSTART.md` but deliberately drops `README.md`** — noticed 2026-09-05
  while verifying M134's staged bundle. `build_mcpb.py`'s payload copy passes
  `ignore_patterns("__pycache__", "*.pyc", "README.md")`, so the bridge README is excluded on
  purpose while `QUICKSTART.md` — added to `klarpdf/mcp_bridge/` by #320, after that ignore list was
  written — rides into every bundle at ~2.4 KB. **The fix is one string in a tuple**, which is
  exactly why it is here rather than done silently: it changes what a shipped artifact contains, and
  the question underneath it is whether the ignore list should name documents individually at all or
  filter `*.md`, which would also cover the next doc someone adds. Not decided. Harmless either way
  — nothing reads it at runtime and the bundle is 239 KiB.
  `packaging/mcp/mcpb/build_mcpb.py`.

- ~~**Four CI/verification defects across M133–M136, none of them found locally**~~ — **closed
  2026-09-06** by a rule in `CLAUDE.md` §How we work: *the thing that verifies the code needs
  verifying too — break it and watch it fail*. Raised by the owner after a third round of red checks,
  this time on the release PR: *"I thought we had tested all paths leading to a release. did we not
  run the failed tests locally first?"* The suite **was** run locally and **was** green before every
  push — that is the finding rather than the excuse. All four lived in a context a local run
  structurally excludes: Windows `CreateProcess` (`WinError 193` on `#!/bin/sh` fixtures), a macOS
  path with a space, a point in the release cycle where the version is not yet published, and a
  runbook line nobody had executed. The discipline of reverting a fix to watch the guard fail was
  applied to *product* code every time and not once to test fixtures, CI jobs or runbook prose,
  whose only evidence was "it passed" — which for a CI job proves that it passed, not that it tests
  what it claims. Nothing reached a user; each was caught by machinery already in the repo. Nothing
  carried.

- **Does an unreachable advisory in a published bridge pin earn a patch release?** — open since
  M158 (2026-10-02). v0.20.0 on PyPI and its `.mcpb` pin `pyjwt` 2.14.0, which
  GHSA-42vr-xj54-vc7v (Medium) flags. The bridge never loads `jwt`, so no user is exposed, and
  `main` is fixed. M157 bumped *before* a release so that a flagged pin would not be published. No
  rule yet says whether a flag found *after* the release is worth a patch of its own or can wait
  for the next minor. Owner's call. **M162 (2026-11-02) adds five more to the same question.**
  v0.20.0 also pins `mcp` 2.0.0 (four Medium, none reachable) and `opentelemetry-api` 1.44.0
  (GHSA-xcq7-p3pf-p88h, Medium), which is on the bridge's path but can only be triggered by the
  client that launched it (`PLAN.md` §M162). **Decided for these pins** (owner, 2026-11-02): no
  patch release of their own. They ship with the next release of the app and the bridge, which is
  due soon. The general rule is still open.
- ~~**Should the bridge stop the SDK logging a refused call's text?**~~ — decided 2026-11-02
  (owner), in M162: yes. The bridge runs the SDK at WARNING, so its stderr stays as empty as on
  2.0.0 (`PLAN.md` §M162).
- ~~**No audit scans the three platform-only packages in the bundle's `uv.lock`**~~ — decided
  2026-11-02 (owner): kept as the limit `PLAN.md` §M129 already accepted. Dependabot watches those
  pins in that file, and only `pywin32` and `colorama` can reach a desktop.
- ~~**The dev lock and the bridge lock disagree on `typing-inspection`**~~ — decided 2026-10-02:
  resynced, with a test that keeps them in step. Graduated to **M159**.
- **GitHub's dependency graph still lists `packaging/mcpb/uv.lock`, a file that no longer exists** —
  found 2026-09-17 after M144 merged. M133 (2026-09-05) moved the bundle to `packaging/mcp/mcpb/`,
  but the graph (`dependencyGraphManifests` in the GraphQL API) still reports the old path as a
  file on `main`. So Dependabot raises alerts against it that no commit can close, because there is
  no file left to change: M144's three advisories did exactly that, as #31–#33, which were
  dismissed by hand. **Until the entry goes, every future advisory against a bridge dependency will
  do the same**; `RELEASE.md` §2 says how to recognise and dismiss those alerts. Check whether the
  entry is still there:
  `gh api graphql -f query='{repository(owner:"utyagi24",name:"klarpdf"){dependencyGraphManifests(first:50){nodes{filename}}}}'`

  **Cause, found 2026-09-30: an orphaned Dependabot snapshot.** A `uv.lock` does not reach the
  graph by GitHub reading the file. Dependabot scans it in a "Dependency Graph" run (trigger
  `dynamic`, on each push to `main`) and submits a snapshot. GitHub keeps only the newest snapshot
  for each pair of **correlator** and **detector**, and the correlator is built from the directory:
  today's is `dependabot-uv-packaging-mcp-mcpb` for `/packaging/mcp/mcpb` (read from run
  36651550197's log). The snapshot from before M133 sits under the old directory's correlator,
  presumably `dependabot-uv-packaging-mcpb`. Nothing scans that directory any more, so nothing
  replaces it, and GitHub treats its `uv.lock` as current, frozen at whatever it held on
  2026-09-05. The root files are unaffected: they share one correlator, `dependabot-pip`, which
  every run refreshes.

  **The fix that does not work:** replacing it with an empty snapshot under the old correlator,
  through the dependency-submission API. GitHub refuses (HTTP 422, *"detector name 'dependabot' is
  reserved for internal services"*), so only Dependabot can write that key. Nothing was changed.
  **Waiting on GitHub Support** to drop the snapshot: that is the only fix that sticks. Until then,
  dismiss as *inaccurate*. Done for #31–#33 (M144), #35 and #39–#47 (M157, 2026-09-30), #49–#51
  (M158, 2026-10-02), #92–#98 (M162, 2026-11-04). M162's arrived two minutes *after* the merge
  that fixed the real files, not with the advisories, so check for new alerts once such a merge
  lands.
  Turning the dependency graph off and on stays the last resort, since it may discard the alert
  history. How to avoid a repeat is in `CLAUDE.md` §Gotchas.

- **The dev lock and the bridge lock pin different `typing-inspection` versions** (0.4.3 vs 0.4.4) —
  noticed 2026-09-16 while doing M144, and already true on `main` before it. It is the only package
  the two locks share at different versions. It is harmless as it stands, because CI's `bridge` job
  installs `requirements-mcp.txt` and so tests the bridge against the version users get. **The
  decision is whether the two locks should be kept in step for shared packages**, as M115 did for
  PyMuPDF through `requirements-core.in`. Doing that for one pydantic helper would mean a constraint
  file or a second recompile on every bump; leaving it means the headless suite runs the bridge on a
  version no user installs. It was left out of M144 so the security fix stayed a two-package diff.

- **The release version is restated in five files and only three of them are generated** —
  found 2026-09-06 while cutting v0.19.0, by `test_the_manifest_version_tracks_the_app_version`
  going red. `klarpdf/version.py` is the source; `packaging/mcp/mcpb/pyproject.toml` and
  `packaging/mcp/installer/install.py` are regenerated by a command each;
  `packaging/mcp/mcpb/uv.lock` records it too and is refreshed by `uv lock` — the one v0.19.0
  missed, found 2026-09-16 by M144 and now caught by a test; and
  `packaging/mcp/mcpb/manifest.json` is **hand-edited**, because `build_mcpb.py` sets its version
  inside `stage()` — which writes the staged copy under `dist/`, not the committed input.
  M136's runbook line said `--validate` covered the manifest. It does not, and that was corrected
  in the same commit. **The decision is whether to consolidate**, and it is not free either way: one
  `sync_version.py` would end the hand edit and the ordering it implies, but it also becomes a
  fourth generator to keep in step with `sync_pins.py` and `sync_installer.py`, all three doing
  nearly the same thing to different files. A cheaper shape, if consolidation is wanted: teach
  `build_mcpb.py` a `--sync-version` flag, since it already owns the manifest. What makes this
  low-urgency rather than ignorable: the test catches it every time, loudly, before a tag exists.
  `RELEASE.md` §3 step 1.

- **`test_saving_twice_from_one_model_does_not_stack_revisions` failed once in CI and has not been
  reproduced** — 2026-09-06, on [#328](https://github.com/utyagi24/klarpdf/pull/328), whose changes
  are confined to `packaging/mcp/installer/`, the workflows and docs and cannot reach the save path.
  The assertion is that two saves of the same edits produce files of equal length; it saw
  `2517 == 2518`. **What is measured so far:** locally the length is **always 2518** — 40 runs of the
  test, then 100+ direct save-pairs, under both `Asia/Kolkata` and `UTC`, with and without a second
  boundary between the two saves. So the anomaly is CI's **first** save at 2517, not the second.
  Test ordering was ruled out: `test_incremental_save.py` sorts before the new `test_installer.py`,
  so the latter's `monkeypatch` of `sys.platform`/`sys.version_info` (which patches the real module,
  restored per test) runs afterwards and cannot reach it.
  **The decision this needs** is whether the assertion is right. The test's own docstring accepts
  that the two files legitimately differ — an annotation carries a modification date and MuPDF
  writes a fresh trailer `/ID` — and asserts only that neither grows. If some field in that set can
  vary in *length* rather than only in content, equal-length is too strong a claim and the check
  should be "no third `%%EOF`, and the second is not larger". If nothing can, this is a real
  nondeterminism in the append path and belongs in an issue with a reproduction. Not decided, and
  deliberately not filed as a bug yet: the repo's rule is that an issue asserts a defect someone can
  reproduce, and nobody has. `tests/test_incremental_save.py:189`, `PLAN.md` §M116–M117.

- ~~**Nothing we ship has ever been tested on macOS**~~ — **closed 2026-09-05 by M136**, which adds an `installer` CI job running `install.py` for real on `macos-latest` alongside Ubuntu and Windows. The cheap middle the entry itself proposed — one macOS leg on the installer rather than the whole suite — is what shipped, because that is where the claim is actually made. Original entry:

- **Nothing we ship has ever been tested on macOS** — noticed 2026-09-04 while scoping M133–M136.
  There is **no macOS runner anywhere in `.github/workflows/`**: the bridge's coverage is `bridge`
  (Ubuntu 3.12), `bridge-windows`, and `bridge-pyver` (3.11/3.13/3.14 on Ubuntu). Yet
  `klarpdf/mcp_bridge/README.md` says the bridge "runs on macOS, Linux and Windows", the `.mcpb` manifest
  declares `darwin` in `compatibility.platforms`, and **M136's `install.py` will print a macOS
  install path** (`~/Library/Application Support/klarpdf-mcp`) with macOS-specific behaviour behind
  it — the `/bin/sh` exec-trick shebang that a space in that path forces, measured on Linux and
  merely *assumed* to behave the same on Darwin. **The decision is whether to buy the runner or
  narrow the claim**, and it is genuinely two-sided: a `macos-latest` leg is one more job on every
  PR for a platform nobody has reported a bug on, while an installer that names a path we have never
  executed is a claim about someone else's machine made without evidence — which is the exact thing
  `PLAN.md` §M133–M136's closing paragraph says not to do. A cheap middle exists: one macOS leg on
  the *installer's* integration test only, not the whole suite, so the claim is backed where it is
  actually made. Not decided. `.github/workflows/test.yml`.

- **The tool count is hand-maintained in four places and nothing checks it** — noticed 2026-08-31
  while rewriting `klarpdf/mcp_bridge/README.md`. "Nineteen tools" appears twice in that file, once in the
  root `README.md`, and once in the repo's GitHub **About** text; `tests/test_mcp_docs.py` pins the
  1,900-character description budget over the live server but asserts nothing about how many tools
  it registers. All four are correct today — verified against `create_server`, which yields 19 with
  writes on and 7 under `--read-only`. **The decision is whether pinning it is worth it**, and it
  cuts both ways: a test that fails the day tool 20 lands is exactly the reminder the four prose
  copies need, but it is also one more file to edit in the PR that adds a tool, and a test whose
  only job is to be updated teaches people to update it without reading it. A cheaper shape, if the
  count is the thing that matters: assert the *read-only* set is a strict subset of the full set and
  print both counts on failure, so the numbers stay discoverable without being pinned. Not decided.
  (The same drift already bit once in the other direction: `--read-only`'s own `--help` text said
  "six query tools" from before `get_annotations` was added until the same session fixed it.)

- **`klarpdf/mcp_bridge/README.md` is still shaped for someone who already knows the tools** — noticed
  2026-08-31 in the review that produced the visitor-first rewrite, and deliberately left out of that
  PR because the owner scoped it to the four missing subjects. Three things stayed: **no table of
  contents** on a 489-line file with eighteen headings; **no worked example** — the root `README.md`
  has its "What that looks like in practice" block, this file has not one call with its reply; and
  **lopsided proportions**, where redaction gets ~110 lines and `split`'s print-dialog range syntax
  (`"1-3"`, `"5-"`) gets half a table cell, which is a thing a caller has to get right on the first
  try. **The decision is how much more the file should carry before it wants splitting** — a
  reference this long is arguably two documents (a setup guide and a tool reference), and adding a
  TOC is the cheap move that also makes the length feel deliberate rather than accidental.

- **The bundle now ships a lock that no audit step examines** — created by **M129** and noticed
  2026-08-28 while checking the release gate. The `.mcpb` carries `packaging/mcp/mcpb/uv.lock`, and since
  M129 that file is what a Desktop install actually resolves from — 139 hashed packages. `pip-audit`
  runs against `requirements*.txt` only, and `audit.yml`'s `pull_request` **paths filter names
  `requirements*.txt` / `requirements*.in` and not `packaging/mcp/mcpb/uv.lock`**, so a change to the
  shipped lock triggers no audit and its contents are never scanned. Most of it is the audited set
  arriving by another route — the lock is generated from those pins, and a test holds the two in step
  — so the genuinely uncovered part is small: the platform-specific transitive packages a
  deliberately marker-free `requirements-mcp.txt` structurally cannot name (`colorama`, `pywin32`).
  **The decision is what to do about that residue**, and there are three shapes: teach the audit to
  read `uv.lock` (`uv export` to a requirements file, then the existing `pip-audit` step); add the
  lock to the paths filter so at least a *change* to it is gated; or accept it and say so, which is
  what `klarpdf/mcp_bridge/README.md` already does. Not obviously worth the machinery for two packages —
  which is exactly why it needs a call rather than a silent default. `.github/workflows/audit.yml`,
  `packaging/mcp/mcpb/uv.lock`.

- **The app and the bridge name a 1 pt different box for the same shape** — noticed 2026-08-26 while
  fixing [#292](https://github.com/utyagi24/klarpdf/issues/292) (M120), and *not* part of it. M120
  makes `parse_annotation` recover a shape's **authored** rect, which is what the app draws and
  edits; `get_annotations` reads the raw annotation `/Rect` for any non-quad type, so for a shape it
  reports the stored box — **1 pt larger on every side**, that being PyMuPDF's baked-in growth.
  Measured: a 6 pt rectangle authored at `[100, 100, 300, 200]` reports `[99, 99, 301, 201]`.
  **Neither is obviously wrong**, which is exactly why this is here rather than in an issue. The
  bridge's boxes are documented to feed `redact_regions`, and a box that covers the *drawn stroke*
  is arguably the right thing to clear; the app's is the geometry the user actually placed. So the
  question is what a reported box is *for* — the mark's footprint on the page, or the shape as
  authored — and it should be answered once, for every non-quad type, rather than patched for
  shapes. Text markup is unaffected: it is read from quads, which carry no such padding (§M101).
  `klarpdf/mcp_bridge/annotations.py:_describe`, `klarpdf/model/page_edits.py:parse_annotation`.

- ~~**The MCP no-socket/no-Qt invariant tests can't run on native Windows**~~ — **decided and fixed
  2026-08-27 as M122** ([#301](https://github.com/utyagi24/klarpdf/issues/301); see the milestone
  above, design in `PLAN.md` §M122). Carried for two days as three competing options; the choice was
  settled by asking what each one *costs on the platform that ships*. Pre-building the loop keeps the
  guard armed for every tool call on both platforms, where skipping on `win32` would have surrendered
  the coverage exactly where the app runs. Nothing carried. The sibling finding from the same run,
  `get_info` reporting a raw `PermissionError` for a directory, went to
  [#294](https://github.com/utyagi24/klarpdf/issues/294) and is fixed as M119.

- **Redaction's Poppler-gated tests skip locally on Windows — by design, and the design holds** —
  noticed 2026-08-27 while reconciling a skip count (7 on the owner's PowerShell run, 3 on the
  assistant's Git Bash run, same 2,391 collected). The difference is four tests gated on
  `shutil.which("pdftotext")`: two in `tests/test_mcp_redaction.py` plus one each in
  `tests/test_redaction.py` and `tests/test_search_redact.py`. They run under Git Bash only because
  Git for Windows ships a `pdftotext` at `/mingw64/bin`, which is not on PowerShell's `PATH`.
  **This is not a missing local configuration**, and the first draft of this entry wrongly implied it
  might be: `.github/workflows/test.yml` says so in its header — the Linux job installs
  `poppler-utils` *precisely* so the cross-engine check runs "instead of skipping the way it does on
  a stock Windows/WSL shell" — and a post-run assertion (step *Assert the Poppler cross-engine
  redaction test ran (did not skip)*, `test.yml:82`) **fails the build** if
  `test_redaction_leak_check_poppler_cross_engine` comes back skipped.
  So the enforcement point is CI, deliberately, and it is guarded against silently becoming a no-op.
  Poppler is a *second* engine: redaction always verifies with PyMuPDF, and `pdftotext` is the
  independent cross-check that the text is really gone (`klarpdf/mcp_bridge/redaction.py`), so a developer
  without it loses corroboration, not the check.
  ~~What is left, and it is much smaller than first written: the CI assertion names **one** of the
  four tests, so the other three could start skipping in CI without failing anything.~~ **Fixed
  2026-08-27 in the same PR as M122** — the step now matches the skip *reason* rather than a list of
  names, so it covers all four and any Poppler-gated test added later, plus a named canary so the
  scan cannot pass by there being nothing left to skip. Nothing carried; no decision outstanding
  about local `PATH`, which was never the problem.

- **`pipx install .` still resolves PyMuPDF to the newest release rather than ours** — the gap M115
  left open on purpose. That milestone pinned the *lock* (`requirements-mcp.txt`) to the app's
  `1.27.2.3` and added a test holding the two locks together, but `pyproject.toml` keeps a **floor**
  (raised to `>=1.27.2.3`), because an exact pin in package metadata conflicts with whatever a user
  co-installs. So a bridge user following the README's `pipx install .` gets *at least* our engine
  and possibly a newer one — which is the ordinary library convention, and still a way for the two
  surfaces to diverge in a user's hands. The options if it is ever worth closing: a compatible-release
  ceiling (`>=1.27.2.3,<1.28`), or telling README readers to install from the lock. Not decided —
  recorded so the argument starts from here. See `PLAN.md` §M115.

- ~~**An append re-writes every mark the file already carried, not just the new one**~~ —
  **scheduled and shipped 2026-08-25 as M117** (see the milestone above; design in `PLAN.md` §M117).
  It was
  carried for a day and stopped being a follow-up the moment it had a number. Two things moved it
  there, both from the owner's questions: the first framing could answer neither *what a sitting is*
  nor *whether the cost multiplies per mark added*, and re-measuring against **front-heavy editing**
  — heavy markup in one sitting, then repeated reopens adding a few more — showed a 30-page document
  going **239,494 → 932,836 B for six highlights worth 4,800**. The earlier "low priority, do it
  when someone is next in that code" was reasoned from a 9-mark example and did not survive the
  200-mark one. Nothing carried.

- **The two exports still pay M110's quadratic cleanup, so an object-heavy document takes minutes**
  — measured 2026-08-22 on `dhariwal_ipo.pdf` (572 pp, **48,877 objects**) after M110/M111, when the
  owner asked what the fix does *not* cover. Every Save path on that file is now ~1.9 s and every
  structural edit ~2.0 s, but **Export ▸ Flattened PDF takes 287 s and Export ▸ Reduced Size PDF
  248 s**, because M111 keeps `GARBAGE_DEDUP` in both — correctly, since `bake()` and
  `rewrite_images` create the duplicates themselves, but the design assumed the scan was affordable
  and on this graph it is not. The bridge's `flatten` tool has the same cost; every other bridge
  write goes through `materialize` and is fast. **What it buys, measured:** flatten at level 4 is
  8,851,615 B against 9,311,219 at level 2 — **250 s for 4.9%**. That is a poor trade for flatten,
  where nobody asked for a smaller file, and a harsh one even for Reduced-Size, where they did but
  get no progress bar and no way to cancel. The fix is the object-count guard that was **rejected
  for Save** (§M110) — and the asymmetry is the point: on a Save it would buy size nobody asked for,
  while here it withholds an optimisation whose cost the caller cannot see. So it needs disclosure
  rather than silence ("the duplicate scan was skipped on a very large document"), which makes it a
  milestone rather than a constant. **Workaround meanwhile:** any structural edit collapses the
  graph through `insert_pdf` — one page deleted takes that file from 48,877 objects to **2,178**,
  after which flatten costs 1.19 s. `klarpdf/model/export.py`.
- **The Flattened-PDF export drops the document's encryption in silence** — noticed 2026-08-21 while
  building M111, not a regression from it: `export_flattened_pdf` has never passed encryption
  keywords, so flattening an owner-password-restricted form yields an unrestricted copy. It is the
  export half of what TC-002 found in the *save* path (M93), and the same argument applies — an
  advisory restriction that disappears without a word is worse than one that was never there. Not
  folded into M111, whose scope is the cleanup options and the reported baseline, and not obviously
  a bug either: an export is a derived artifact and a locked-content copy may legitimately want its
  own protection settings. Needs a decision (carry through / drop with a warning / offer the
  choice), then a milestone. `klarpdf/model/export.py:export_flattened_pdf`.
- **`get_info` reports *displayed* page sizes while `clip` and `redact_regions` consume *unrotated*
  ones** — TC-008, 2026-08-18, severity low. `search`, `redact_regions` and (since M99.1) `clip` all
  work in the unrotated space; `page_sizes` alone reports post-rotation dimensions, and there is no
  `rotation` key anywhere in the response. **M99.1 made this more visible, not less**: before it,
  `get_info` and `clip` at least agreed with each other, so the fix turned a 2-v-2 split into 3-v-1.
  The consequence is that a natively-portrait page and a rotated-landscape one are *indistinguishable*
  — reproduced on a two-page fixture where both land in one `612x792` group and
  `clip=[0,0,700,400]` is refused on page 1 and renders on page 2. Dimensions swap for 90°/270° and
  not for 0°/180°, so the distinction is unrecoverable from the call. A sharper way to put it: **the
  rect `clip` validates against is currently obtainable only by deliberately triggering an error**,
  since the M99.1 message is the one place it is printed.
  **Why it stays low.** A caller who sources boxes from `search` — the documented path — never needs
  any of this, and M99.1's error names the rotation and the convention at the moment they trip. It
  bites only when *constructing* a clip from scratch ("crop the top-right quadrant"), which is how
  TC-008's own card clips were built, so it is not hypothetical.
  ~~**Not yet designed**, deliberately~~ — **closed by M107.1** (2026-08-20). It took the first of
  the three options this entry listed: `get_info` now reports each page's `rotation` and *groups* on
  it, so a native-portrait page and a rotated-landscape one are no longer one indistinguishable row,
  and `get_info`'s docstring names the convention the box-taking tools use. The entry above was left
  standing after the fix and was read as open by the M101 session two days later, which is the
  ordinary cost of a follow-up that outlives its defect. M101 confirmed the rest of the convention
  from the other end: annotation geometry is unrotated at every `/Rotate`, measured and pinned, so
  `get_annotations` → `redact_regions` needs no adjustment. Nothing pending. — *WSL*

- **The over-redaction guard covers the query-split case only, not a single term matching inside a
  longer word** — TC-007 addendum, 2026-08-16, severity medium. M98's `query_terms` warns when
  word-list mode silently splits a multi-word query; nothing warns when one term matches *inside*
  another word, which is the older half of the same hazard. Measured on the policy document:
  `redact_text "Male"` with `whole_words` omitted took three boxes on page 6, one of them inside
  `Female`, leaving the driver table reading `Fe` — with `residual_matches: 0` and no warning at
  all. There **is** a tell: `verified_text` lists `"male"` in lowercase beside `"Male"`, and a
  lowercase fragment under a capitalised query means the match landed inside a longer word. It is
  easy to miss and nothing says it out loud. The information needed is already computed — `search`
  returns `Female` as that hit's snippet, so the write path can see the enclosing word too. Closing
  it symmetrically ("your query was split" / "your term matched inside something else") is the
  natural shape. Not scheduled. — *WSL*

- **Does the residual check cover pages that were never redacted?** — TC-003's open question,
  **answered in part by M103**. The scans are scoped to the pages the call was asked to redact, and
  M103 added `residual_scope` plus a warning so the scope is stated rather than implied. What that
  does *not* settle is whether the two **advisory** scans should widen to the whole document, which
  was examined and **rejected** on 2026-08-18: a reply must not mix page-scoped and document-wide
  results, so consistency won over reach. Recorded here because the rejection is a decision, not an
  oversight — a later session finding the scoped `[]` surprising should read `PLAN.md` §M103 before
  re-opening it. No action pending. — *WSL*

- ~~**TC-007's two input-shape items and the annotation round trip**~~ — **scheduled 2026-08-16 as
  M99–M101** (see the milestones above; design in `PLAN.md` §Planned next). They stopped being
  carried follow-ups the moment they had numbers.

- ~~**TC-002 — three MCP-side defects still open**~~ — **closed 2026-08-15 by M94** (see the
  milestone above). From the second hands-on session (2026-08-13, report at
  `/mnt/c/Users/umesh/Downloads/pdfs/klarpdf-tests/TC-002-fill_form-ssa-3.md`): filling an SSA-3
  through `fill_form` worked, but the write degraded the document seven ways. Four were the app's
  save engine (**M93**, [#253](https://github.com/utyagi24/klarpdf/pull/253)); the three bridge-side
  ones — `get_info` misreporting encryption (ISSUE 5), `get_form_fields` hiding a checkbox's
  on-state and the field flags (ISSUE 6), and the stale XFA data island (ISSUE 3) — are M94, along
  with the overstated "lossless" wording in the tool docs. **What M94 deliberately did not do**
  carries on below. `klarpdf-tests/inspect_pdf.py` (beside the report) dumps the document-level
  properties a save can drop, and diffs two files; it takes `--password` or prompts.

- **An XFA form's `datasets` packet is still not written, by choice.** M94 reports the mismatch —
  `fill_form` returns `xfa: {present, dynamic, datasets_updated: false}` and a warning — because the
  owner chose reporting over resolving (2026-08-15). The two stronger answers remain available and
  neither is scheduled:
  - **Write the values into `datasets` too.** Highest fidelity: the file stops asserting two
    different things. The work is the AcroForm-name → XFA-node mapping, where a wrong write is
    worse than no write, and it needs a real dynamic form to test against.
  - **Drop `/XFA` on write.** The conventional fix (pdftk's `drop_xfa`) — the output degrades to a
    plain AcroForm every viewer agrees on. Safe for a static form; for a **dynamic** one it removes
    the only thing that renders it, so it would have to be conditional or opt-in.
  A synthetic dynamic-XFA fixture now exists (`tests/conftest.py:dynamic_xfa_pdf`), but a **real**
  dynamic form is still untested and remains the likeliest hard failure — TC-002's own assessment,
  unchanged.

- **A reorder still loses the accessibility structure tree** (M93). The unchanged-page-set route
  keeps it; the grafting route cannot, because a structure tree is a tree of references into page
  content and moving pages means **rewriting** it rather than copying it. So reorder / delete / merge
  remain lossless for *content* and lossy for *document structure* — stated in `PLAN.md` §Key design
  idea rather than rounded up to "lossless". Worth doing if tagged PDFs become a real workflow; the
  same pass would need to carry `/Perms` and the `/Names` tree. Not scheduled. — *WSL*

- **A GUI launched from WSL always opens on the primary monitor, whatever screen you typed the
  command on** — owner report 2026-08-22, diagnosed the same day; **WSLg only, and not a KlarPDF
  bug**. `MainWindow._place_window` deliberately opens on the screen under the cursor
  (`QGuiApplication.screenAt(QCursor.pos())`), which is why this works on Windows. Under WSLg the
  app runs on the **Wayland** platform, where two separate things defeat it: `QCursor.pos()` returns
  `(0, 0)` — Wayland gives a client no way to ask where the global pointer is — which lands outside
  every screen rect, so `screenAt` returns `None` and the code falls back to the primary screen; and
  Wayland's `xdg-shell` gives a client no way to position its own top-level window at all. Measured:
  a probe window asking for (3080, 200) on the external monitor landed at (0, 348) on the laptop
  under `wayland`, and at (3086, 227) on the **external monitor** under `xcb`. Qt sees both screens
  either way (`rdp-0` 2880×1800, `rdp-11` 2560×1440), so nothing is hidden — the placement is simply
  not the client's to make. **Workaround:** `QT_QPA_PLATFORM=xcb python launcher.py file.pdf` routes
  through XWayland, where both calls work; the caveat is that XWayland only learns the pointer
  position while the cursor is over an X client, and the launching terminal is a *Windows* window,
  so the position can be stale. Deliberately not fixed in the app: WSLg is the dev environment and
  the product ships native Windows. It becomes real work only if Linux desktop is ever a target, and
  then the answer is an explicit screen choice (a `--screen` flag / `KLARPDF_SCREEN`) rather than a
  cursor heuristic. `main_window.py:3082`.
- ~~**`_render_visible` is O(document length), not O(visible band)**~~ — **fixed 2026-07-28 as
  M87.3** (see the M87 entry above). It was worse than filed: a *third* walk, the annotation
  overlay re-deriving the band once per page, made the pass **quadratic** rather than linear —
  127 ms at 1000 pages against the ~6 ms/pass on 320 that the follow-up recorded. Nothing carried.

- ~~**On open, no thumbnail is marked at all**~~ — **fixed 2026-07-28 with M89.4** (owner-reported
  during the M86 verification pass: "reopening the document lands me at the last page but in the
  thumbnail bar the page is not selected"). `ThumbnailPanel.mark_open_page()` seeds the marker from
  the open path. The same fix uncovered a bigger one sitting underneath it — reopening had never
  restored the remembered page at all — see the M89.4 entry above. Nothing carried.

- **A search is still one uninterruptible pass over every page.** M78.7 made the pass ~400× cheaper
  and stopped typing from launching one per keystroke, but the shape is unchanged: `search()` walks
  the whole document synchronously on the UI thread and cannot be cancelled. On the 320-page
  `spaceX_prospectus.pdf` a one-letter query is **2.3 s** (4.1 s with Match case) of frozen UI —
  fine once debounced, since it only happens when the user genuinely pauses after one letter, but it
  grows with page count and it is now the whole remaining cost. The fix is to **chunk the scan**: a
  page-at-a-time generator driven from a zero-delay timer, painting hits as they arrive and
  abandoning the run when the query changes. That also buys incremental results and a live count on
  any document size. Deferred because it changes `search()` from a function that returns a count
  into something asynchronous, which every caller (find bar, results panel, Find-and-Redact dialog)
  would have to follow — its own milestone, not a bug-fix branch. A hit **cap** was considered and
  rejected: "72 097 matches, showing the first 1 000" is a different feature with a worse answer,
  and Find-and-Redact must see every hit to be trustworthy.

- ~~**Search is blind to the live edit model**~~ — **fixed 2026-07-24 (PR #190, Direction A)**. The
  three symptoms (a new text box unfindable until save+reopen, a moved one still matching its old
  spot, results clearing on any edit) shared one cause: `search()` scanned the raw source, decoupled
  from the model, and `search_for` surfaced FreeText / form-field text. Search now returns the page's
  printed content-stream text only (matching Preview/Edge — text boxes, foreign FreeText and field
  values excluded; highlights still findable; redaction/crop findable while unsaved), and results
  survive a content-only edit. Design + rationale in `PLAN.md` §Future enhancements.

- ~~**The Annotations panel reads each row's snippet with `page.get_textbox`**~~ — **fixed in M78.8**
  (wrong snippets *and* 15.7 s of per-edit lag at 200 highlights; `PageText` moved to
  `klarpdf/model/page_text.py` and shared). Nothing carried.

- **The thumbnail sidebar bakes the *whole document* on every edit.** `ThumbnailPanel._edited_render`
  calls `PyMuPDFEngine.render_output(vdoc)` — a full materialise of every page — so the panel can
  rasterise the handful of thumbnails actually on screen. On a 320-page document that is **~0.69s per
  edit** (measured at M69.4 on `spaceX_prospectus.pdf`, after the mark-rendering fixes took the rest
  of the edit cost from 10.7s to 0.89s); it is now the single largest remaining O(document) cost per
  edit, and it grows with page count on *every* edit, not just marks. The fix is a **per-page bake**:
  an engine entry point that materialises only the pages asked for, which the panel would call for
  its visible rows the same way `_render_visible_thumbs` already scopes rasterising. Deferred because
  it touches the shared `render_output` path that print and save also use, so it wants its own
  milestone and its own verification rather than riding a bug-fix branch. Not a blocker: 0.89s per
  edit on a 320-page document is responsive, and small documents are unaffected.

- **Upstream PyMuPDF bug: URI links with an unbalanced paren are dropped by `insert_pdf` /
  `insert_link`** (unescaped re-serialisation of the URI text; console shows "skipping bad link /
  annot item N"; seen in the wild in a novaPDF-produced file whose URI is `http://www.adobe.com)`).
  Worked around in `klarpdf/model/links_remap.py`: the materialise link pass re-adds any URI link
  `insert_pdf` dropped, with the text pre-escaped (round-trips correctly). Consider reporting
  upstream to PyMuPDF; if fixed there, the restore pass simply finds nothing missing.

- **A stale `vendor/wheels/` silently shadows the lock in `-Offline` builds.** Found while building
  v0.10.0: the local cache still held `pypdf-6.13.2` (the wheel the v0.9.4 security bump replaced), so
  `build.ps1 -Offline` failed with *"Could not find a version that satisfies pypdf==6.13.3"*. The repo
  was correct (`requirements-win.txt`, `vendor/wheels-sources.md` both say 6.13.3); `vendor/wheels/` is
  **gitignored**, so it drifts per-machine and never gets re-vendored by a `git pull`. CI is unaffected
  (it fetches fresh). Fix by re-running `build.ps1` **without** `-Offline` once, then re-running with
  it. Worth a guard in `build.ps1` that diffs the cache against the lock before an offline build.
- ~~**Flaky test: `test_incremental_save.py::test_saving_twice_from_one_model_does_not_stack_revisions`**~~
  — **diagnosed and fixed 2026-09-20**, on its second firing (the first was 2026-09-19 on
  [#367](https://github.com/utyagi24/klarpdf/pull/367); it fired again on the `pytest` job of
  [#380](https://github.com/utyagi24/klarpdf/pull/380), with the same 2,518 vs 2,517 bytes).

  **The cause is the document identifier in the trailer, not the save.** Every PDF carries a pair
  of them, and MuPDF draws a fresh second one on every save, writing it as hexadecimal or as a
  quoted string — whichever is shorter for the random bytes it drew. So two saves of the very same
  edits differ by a few bytes at random. Measured over 2,000 pairs: 32 of them, **1.6%**, differ in
  length, by −4 to +4 bytes and **in both directions**. About one run in 62. The earlier guess, a
  timestamp compressing differently, was wrong.

  `assert len(first) == len(second)` could therefore never have been made reliable. It is now the
  assertion that carries the meaning instead: both files are **byte-identical up to the start of
  the appended revision**, and both hold exactly two revisions. Stacking a revision moves that
  offset and adds a third `%%EOF`; a fresh identifier cannot touch either, because it is written
  after the offset. Held 2,000 of 2,000 pairs, the 32 with differing lengths included, and a
  deliberately stacked second save makes it fail. — [#381](https://github.com/utyagi24/klarpdf/pull/381)
- **Flaky test: `test_single_instance.py::test_handoff_opens_window_in_resident_instance`.** Failed
  once, passed on rerun (timing-sensitive Windows IPC: a race between the resident instance binding its
  socket and the forwarding launch connecting). **Could not reproduce** — 5 isolated runs + several
  full suites all green. **Stakes rose twice at G8** and this is now the most actionable follow-up:
  the repo is public, so a flake is a red X on a stranger's first CI run — *and* `pytest` is now a
  **required status check**, so a flake no longer merely looks bad, it **blocks the merge** until
  someone re-runs the job. (The bypass list is empty by design, so there is no override; the escape is
  re-running the check, or flipping `enforcement` to `disabled` and back.) Note the check runs on
  `ubuntu-latest` while the observed flake was on Windows. → **Reassessed 2026-08-12: not the most
  actionable follow-up, and deliberately left alone.** That parenthetical turns out to be the whole
  story — the required check has never failed on this in 200 recorded runs, so the merge-blocking
  risk is theoretical. With one unreproduced failure and no fix plan, there is nothing to write;
  reopen it if it ever fires on `ubuntu-latest`, which would be new information.
- **Dependency vuln: pypdf → 6.13.3** → ✅ fixed in **v0.9.4**: bumped `pypdf` 6.13.2 → 6.13.3
  (**GHSA-jm82-fx9c-mx94**, Moderate memory-DoS in the `pypdf` fallback edit engine), recompiled the
  locks + regenerated `vendor/wheels-sources.md`, and removed the audit-gate ignore.
- **Dependency vuln: pypdf → 6.15.0** → ✅ fixed in **v0.17.1**. Bumped `pypdf`
  6.14.2 → 6.15.0, clearing **two** Moderate advisories — **GHSA-fwg2-594c-jp42** (CVE-2026-71852,
  unusually large CID font width ranges) and **GHSA-fp3f-mc75-235c** (CVE-2026-71870, unusually large
  `/ToUnicode` streams). Both are CPU/memory-DoS on **parse**, reached through `PyPdfEngine`'s
  `PdfReader` in `klarpdf/model/edit_engine.py`, so a crafted PDF is the attack surface. The weekly `audit`
  job caught it first — the 2026-08-10 scheduled run went **red** (`pip-audit`: "Found 2 known
  vulnerabilities in 1 package"). Bumped via `RELEASE.md` §2 → §1: floor pin in `requirements.in`,
  `invoke lock --package pypdf==6.15.0`, `invoke vendor`; all three locks audit clean and the
  `audit` run on `main` went red → green. All four Dependabot alerts (#6–#9, two advisories × two
  manifests) auto-closed as `fixed` once the graph re-scanned.
- ~~**Dependabot security-update PRs are ON, but `RELEASE.md` §2 says they are OFF**~~ — **settled
  2026-08-11: turned off**, so the documented policy is now the true one. Worth keeping the story,
  because a *doc asserting a setting, with nothing checking it* is the failure mode. §2 records
  Dependabot as **detection-only** ("alerts are on … security-update PRs and version-update PRs are
  both disabled"), because Dependabot compiles on **Linux** and would write the wrong lock — but G8
  above had enabled `automated-security-fixes` a month earlier, and nothing reconciled the two. So
  Dependabot opened [#234](https://github.com/utyagi24/klarpdf/pull/234) for the pypdf 6.15.0 bump and
  `close-external-prs.yml` closed it automatically, Dependabot's `author_association` being neither
  OWNER, COLLABORATOR nor MEMBER. That auto-close was the **right outcome for the wrong reason** —
  nobody read the diff, and the diff dropped `colorama` from `requirements-dev.txt` (pytest's
  win32-only dep), precisely the wrong-platform compile §2 warns about. (Its pypdf *hashes* were in
  fact fine — pypdf ships one pure-Python wheel, so the hash hazard §2 names is real only for the
  native deps. `colorama` was the actual defect.) Closing it unmerged also made Dependabot stop
  offering 6.15.0, so the bump was done by hand as [#235](https://github.com/utyagi24/klarpdf/pull/235)
  regardless. The rejected alternative was keeping the setting on and exempting Dependabot in the
  closer — declined because it makes every future lock diff a Windows-recompile chore, and §2's
  reasoning had just been vindicated. §2 now carries the `gh api` verification line, so the next
  drift is one command away from being caught. Nothing carried.
- **Clean-machine install** — the one deferred M9 verification item: run `klarpdf-setup-x64.exe` on a
  Windows VM with **no Python and networking disabled** (Win10 Home has no Sandbox → VirtualBox /
  spare machine / fresh local user). Everything else in the Verification matrix is green.
- **CI action versions** → ✅ done in M15: `actions/checkout@v6`, `setup-python@v6`,
  `upload-artifact@v7`, `softprops/action-gh-release@v3` (all Node-24).
- **Code signing** — deferred Authenticode step (removes the SmartScreen prompt); needs a cert, so
  it stays deferred (still unsigned through v0.4.0); slots into `release.yml` before packaging
  (PLAN.md §Packaging §5). Carry to a future release once a cert is available.
- **App icon** → ✅ shipped in **M10** (v0.2.0).
- **Product features** → view/print/annotate/redact/round-trip/flatten-export all shipped (M0–M31.5).
  The next tranche is **scheduled** in §Next roadmap above: image import/export (v0.8.0, M35–M37);
  encrypted PDFs + GoTo-link remap (v0.9.0, M32/M33, re-scoped out of v0.7.0).
  Still **deferred beyond** the roadmap (PLAN.md §Future enhancements): new-field form designer,
  drop-to-open in the main view, re-encryption on save, cross-app annotation editing (M31 round-trip
  edits only KlarPDF's own author-tagged marks; foreign annotations are shown but not editable — a
  deliberate fidelity-safety boundary, see PLAN.md).

- ~~**Help ▸ Donate… points at a GitHub Sponsors listing that does not exist.**~~ — **closed
  2026-09-24**: the owner enrolled the account in GitHub Sponsors. `hasSponsorsListing` is `true`,
  the link answers 200 with no redirect, and the gate block is deleted from `RELEASE.md`.
  `gh api graphql -f query='{user(login:"utyagi24"){hasSponsorsListing}}'` returns **false**, and
  `/sponsors/utyagi24` **redirects to the plain profile** rather than 404ing — so the dead link is
  indistinguishable from a working one and no test can catch it. This is exactly the one-time gate
  in `RELEASE.md` §3, which was **not** satisfied before v0.12.0 shipped the menu item, nor before
  v0.14.0. Owner call at v0.14.0: **ship as-is, fix separately.** Clearing it is a GitHub account
  step (enable the Sponsors listing), after which the gate check returns true and the gate block can
  be deleted from `RELEASE.md`.

- **Flaky test: the save path's `os.replace` hits `[WinError 5] Access is denied`.** Seen twice
  while preparing v0.14.0, both times in `tests/test_external_change.py` (different tests each
  time: `test_save_no_external_change_does_not_prompt`, then `test_save_overwrite_proceeds`), and
  only in **full-suite** runs — the file passed 4/4 in isolation, and a clean full re-run was green
  both times. `_write_to` writes a temp file next to the target then `os.replace`s it, so a
  transient lock on the freshly written temp (real-time antivirus is the usual suspect on this
  machine) fails the rename; the resulting "Save failed" modal is what the conftest guard reports.
  Environmental rather than a code defect, but it is a **release-gate** annoyance and shares the
  "red X on a stranger's CI run" stakes with the single-instance flake above. Worth a bounded
  retry around the `os.replace` before declaring the save failed. → **Scheduled 2026-08-12 as M38.5**,
  the prerequisite PR before the MCP bridge. Note the framing above overstated the CI stakes: this
  was only ever seen on local Windows runs, and the required check runs on `ubuntu-latest` (see the
  1.0 gate entry). The retry is worth doing on its own merits — any user with real-time antivirus can
  hit the same transient lock and get a spurious "Save failed" modal.
  → **CLOSED 2026-08-12 by M38.5** ([#239](https://github.com/utyagi24/klarpdf/pull/239)):
  `klarpdf/util/atomic.py:atomic_replace` retries `PermissionError` (WinError 5 / 32) four times over ~0.75 s
  before giving up, and both write sites — `_write_to` (Save / Save As) and `_export_pdf` (every
  Export) — go through it. Only lock contention is retried; `FileNotFoundError` and a cross-device
  `OSError` still fail on the first attempt. Kept here rather than deleted because the *diagnosis* is
  the durable part: if a "Save failed" ever returns, it is no longer this.

- **`MarkupStyleButton.style()` shadows `QWidget.style()`** (it returns the `MarkupStyle`
  dataclass). Harmless in paint — Qt calls the C++ method — but any Python-level `button.style()`
  gets the wrong object, and it already cost one debugging detour in M59.13 (workaround:
  `QWidget.style(btn)`). Rename to `markup_style()` when that file is next touched.
