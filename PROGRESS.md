# KlarPDF — Build Progress

Live status of the build (milestone detail in `PLAN.md` §Execution). **One PR per milestone** — when
it merges, check the box here in the same PR and append the PR link.

> **This file is the single source of truth for live status** — shipped versions, per-release notes,
> release links, milestone ticks, and open follow-ups. `PLAN.md` (design/spec) and `CLAUDE.md`
> (conventions) **link here, they don't restate it** — see CLAUDE.md §How we work → "Where things live".

**Status:** ✅ **v0.17.1 shipped** — a **security patch plus the last M92 fix**; both are fixes, so
this stays a patch under `RELEASE.md` §3's SemVer rule.

**(1) `pypdf` 6.14.2 → 6.15.0**, clearing two Moderate advisories — **GHSA-fwg2-594c-jp42**
(CVE-2026-71852, oversized CID font width ranges) and **GHSA-fp3f-mc75-235c** (CVE-2026-71870,
oversized `/ToUnicode` streams). Both are CPU/memory exhaustion **on parse**, reached through
`PyPdfEngine`'s `PdfReader`, so a crafted PDF is the attack surface rather than a local misuse. The
weekly `audit` job found it, not a person: the 2026-08-10 scheduled run went red. The bump was made
by hand on Windows per `RELEASE.md` §2 rather than taken from Dependabot's PR, which had recompiled
on Linux and dropped `colorama` from the dev lock — see Open follow-ups for the full account,
including the settled Dependabot-policy contradiction it exposed.
[#235](https://github.com/utyagi24/klarpdf/pull/235) · [#236](https://github.com/utyagi24/klarpdf/pull/236)

**(2) M92.6 — the Pages sidebar rolls continuously** ([#233](https://github.com/utyagi24/klarpdf/pull/233)),
which landed after the v0.17.0 tag and so ships here. A detent threw the strip a whole viewport
(**698 px, 2.76 thumbnails**) because Qt's one-item `singleStep` was multiplied by the Windows
lines-to-scroll default and then clamped to `pageStep`; it now moves a third of a row pitch,
scaled by the measured pitch so it holds across sidebar widths. Detail in the M92.6 entry below.

1672 headless tests green (3 expected skips).

**v0.17.0** — **scrolling that behaves**, delivering **M91** (whitespace
fidelity, glyph legibility, reading position) and **M92** (mouse-wheel scrolling). The wheel moves a
**defined distance** — `wheelScrollLines × 32 px × zoom` — instead of Qt's `viewportHeight / 20`,
which on the owner's full-height window threw the page **183 px, a fifth of a page, ten lines of body
text per click**; the new rule is window-independent and zoom-scaled, measured at **87 px at Fit
Page** (M92.1). Each step is **eased over 200 ms**, chosen with the wheel in hand against a live
toggle, behind **View ▸ Smooth Scrolling** (M92.2). Using it then surfaced three more defects, each
measured before it was fixed: the coast-mute was **indefinitely renewable**, so the wheel could stay
dead for as long as the reader kept scrolling (M92.3); **prefetch was being paid on the scroll's
critical path**, which is the whole of the image-page stall — visible-page rendering costs 0 ms at
every zoom (M92.4); and the glide **restarted its curve against a clamped target**, so arriving at
page 1 sped up twice while stopping and then crawled the last 23 px for 240 ms (M92.5). M91 brought
the **page counter**, `Space`/`PgUp`/`PgDn` paging from anywhere including the sidebar, leading-space
fidelity in text boxes, and a Rotate glyph that no longer reads as Undo.
1650 headless tests green (3 expected skips — two Poppler `pdftotext` cross-checks, one off-Windows
mutex guard).

**Toward 1.0 (owner call, 2026-08-01).** The feature roadmap is complete, and **1.0 was deliberately
not taken here**: on a public repo it is a claim about readiness for *other people*, and what remains
open is almost entirely in that category. The gate, all small and all concrete:

- [ ] **Clean-machine install** — the one deferred M9 item: `klarpdf-setup-x64.exe` on Windows with
  **no Python and networking disabled**. It is the first thing a stranger does and has never been
  watched. (Win10 Home has no Sandbox → VirtualBox / spare machine / fresh local user.)
- [ ] **The Donate link points at a Sponsors listing that does not exist** — and it *redirects*
  rather than 404s, so no test can catch it. A dead link inside the app.
- [ ] **One known flaky test** (`test_single_instance`) — down from two. **Re-measured
  2026-08-12 and the stakes were overstated:** both were only ever seen on *local Windows* runs,
  while the required `pytest` check runs on `ubuntu-latest` — and across 200 recorded `test.yml` runs
  neither has failed it (191 green; all 9 failures trace to the 2026-07-29 widget-leak episode, the
  2026-07-25 reading-bar PR, and the workflow's own first run). So this is a release-prep annoyance,
  not the merge blocker described. Split accordingly: **the save path's `os.replace` was fixed in
  M38.5** ([#239](https://github.com/utyagi24/klarpdf/pull/239)); **`test_single_instance` is
  left alone** — one failure, never reproduced, no fix plan, nothing actionable to write.
- [ ] **Item E — background rendering** (`PLAN.md` §Deferred): 1–3 s of frozen UI per page per zoom
  on image-heavy documents. Its gate is already met; this is scheduling, not justification.
- **Code signing** stays deferred (needs a certificate) — it is the one gate item that may never be
  purchasable, so it is explicitly *not* a blocker for 1.0.

**v0.16.2** — a reading-bar legibility fix, following the Preview model.
Undo/Redo and rotate-left/right were four mirrored curved-arrow glyphs that read as two
near-identical pairs at toolbar size, so the resting **reading bar** now drops **Undo/Redo** and the
**second rotate direction**, leaving a single Rotate button as its only curved arrow — exactly what
Preview's own toolbar carries. Nothing is lost: undo/redo keep **Ctrl+Z / Ctrl+Y** and the Edit
menu, and Rotate Right keeps **Ctrl+R**, **Edit ▸ Rotate Right**, and the sidebar right-click menu
([#194](https://github.com/utyagi24/klarpdf/pull/194)). Release:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.16.2>. 1273 headless tests green (1 expected
skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.16.1 "Simplify & Read"** — the GUI tranche's **R6 (M71–M79)**, a
Preview-inspired simplification built on one idea: *the app at rest is a viewer; the markup kit is
chrome you summon on demand.* The single ~29-slot toolbar splits into **two tiers** — a resting
**reading bar** (Sidebar · Save · Undo/Redo · the zoom cluster · Rotate · a **Markup** toggle · Find)
and a **markup bar** the toggle reveals, its visibility remembered app-wide like the sidebar
([#159](https://github.com/utyagi24/klarpdf/pull/159)). The kit gains Preview's ergonomics: **one
gesture-detecting Redact** — press-on-text vs press-on-margin picks the gesture
([#160](https://github.com/utyagi24/klarpdf/pull/160), [#189](https://github.com/utyagi24/klarpdf/pull/189));
**sticky arming** so Highlight/Underline/Strike/Pen mark passage after passage
([#161](https://github.com/utyagi24/klarpdf/pull/161)); **arrowheads as line style** — Arrow folds
into Line with none/start/end/**both** ends, plus a **dashed** stroke option
([#162](https://github.com/utyagi24/klarpdf/pull/162), [#169](https://github.com/utyagi24/klarpdf/pull/169));
and a right-click **markup context menu** to recolour or add/remove layers in place
([#164](https://github.com/utyagi24/klarpdf/pull/164), [#167](https://github.com/utyagi24/klarpdf/pull/167), [#168](https://github.com/utyagi24/klarpdf/pull/168)).
Reading gains **Match case** + **Whole words** on the find bar
([#163](https://github.com/utyagi24/klarpdf/pull/163), [#171](https://github.com/utyagi24/klarpdf/pull/171)),
an **Annotations sidebar tab** listing every text markup — click to jump, present only when the doc
has marks ([#165](https://github.com/utyagi24/klarpdf/pull/165), [#174](https://github.com/utyagi24/klarpdf/pull/174)) —
and three view-only **view modes**: Full Screen, Slideshow, Two-Page
([#166](https://github.com/utyagi24/klarpdf/pull/166), [#170](https://github.com/utyagi24/klarpdf/pull/170)).
A late owner-testing pass (**M78.2–M78.6**) added **arrow-key nudge** and **text-box width reflow** to
object editing, an icon-polish set, **HUS arming swatches**, and split the shared style button into
Line Styling · Colors · Opacity (an exact-% slider)
([#180](https://github.com/utyagi24/klarpdf/pull/180)–[#184](https://github.com/utyagi24/klarpdf/pull/184)),
while **M79.1–.3** stripped the sidebar's title bar and made its optional tabs appear only on demand
([#175](https://github.com/utyagi24/klarpdf/pull/175), [#177](https://github.com/utyagi24/klarpdf/pull/177), [#178](https://github.com/utyagi24/klarpdf/pull/178)).
Two search fixes closed the tranche: find-as-you-type no longer hangs a large document
([#186](https://github.com/utyagi24/klarpdf/pull/186)) and **search matches the page's printed text
only** ([#190](https://github.com/utyagi24/klarpdf/pull/190)). A pre-release audit then flagged
**pypdf 6.13.3** carrying four newly-disclosed crafted-PDF DoS advisories (CVE-2026-59935/36/37/38),
so this release also bumps **pypdf → 6.14.2** ([#192](https://github.com/utyagi24/klarpdf/pull/192));
the intervening **v0.16.0** tag was cut but **never published** — skipped so the fix ships in the
first public build. Release:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.16.1>. 1273 headless tests green
(1 expected skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.15.0 "Stamp, Sign & Watermark"** — delivers **R4 (M61–M64)** and **R5
(M66–M69.16)** together: M65's release cut was skipped by owner call (2026-07-20) so R4 would ship
alongside R5 rather than under an unpublished tag. A **unified content-draw engine**
(`model/content_marks.py`) underlies stamps, signatures and watermarks — two descriptors, `Stamp`
(text + optional frame) and `ImageStamp` (a placed raster), that bake into the page's **content
stream** at save; built **vector** so stamp text stays searchable and crisp at any zoom and
arbitrary rotation comes free. A watermark isn't a third type, just either descriptor with
`under=True` applied across a page range. **Placement** rides the existing object tools — drag,
move, corner-resize, z-order, delete — rather than a second system
([#146](https://github.com/utyagi24/klarpdf/pull/146), [#147](https://github.com/utyagi24/klarpdf/pull/147)).
**Image stamp / signature**: "make white background transparent" keys a phone photo of a signature
so it stops blanking out whatever it covers, with a **recent-signatures** list for two-click reuse
([#148](https://github.com/utyagi24/klarpdf/pull/148)). **Tools ▸ Find and Redact…** finds every
occurrence of a search term, reviews hits in the search panel (checkable, click to jump), and
redacts the checked ones as one undo step — text-layer only, image-only pages named rather than
silently reporting zero matches ([#149](https://github.com/utyagi24/klarpdf/pull/149)).
R5 adds a **foreign-annotation** layer for marks another PDF tool wrote: infrastructure + **delete**
([#150](https://github.com/utyagi24/klarpdf/pull/150)), **move** with the appearance stream
preserved byte-for-byte ([#151](https://github.com/utyagi24/klarpdf/pull/151)), and
**adopt-on-edit** — double-click a foreign mark of a modeled type (highlight / underline /
strikeout / ink / line / square / circle / FreeText) to make it an ordinary editable KlarPDF mark,
with a degrade warning that fires only when something would actually be lost
([#152](https://github.com/utyagi24/klarpdf/pull/152)). **Form-field creation** — Tools ▸ Add Form
Field ▸ Text / Checkbox / Dropdown — places an ordinary AcroForm field, so inline fill, lossless
save, print and flatten all work on it by construction, no new code path
([#153](https://github.com/utyagi24/klarpdf/pull/153)). A sixteen-item polish pass (**M69.1–M69.16**)
followed from owner testing across the whole R4/R5 surface — a rotated stamp's mirror-image bug,
watermark interaction + live-thumbnail fixes, merging the stamp and watermark UI into one feature
(owner call: *"given the similarity…"*), large-document mark performance, whole-page marks visible
by default, an angle slider, a mark-dialog geometry warning, a recent-signature crash, signature-drag
lag, a backwards opacity slider, and — the last three — making a created form field behave as an
ordinary object: selected on placement, and grabbed **press-to-move / double-click-to-type** like
every other text box instead of by hunting for its border
([#154](https://github.com/utyagi24/klarpdf/pull/154), [#155](https://github.com/utyagi24/klarpdf/pull/155)).
Release: <https://github.com/utyagi24/klarpdf/releases/tag/v0.15.0>. 1068 headless tests green
(1 expected skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.14.0 "Markup Tools"** — the GUI tranche's **R3 (M56–M60)**, and because the **v0.13.0 tag was
cut but never published** (owner call), this release also delivered **R2 "Document Hygiene"
(M51–M54)** to users: extract / insert-blank / duplicate pages, **Reduced Size** export, document
**Properties + metadata** editing (both stores), and **AES-256** password protection. R3 itself is
the markup kit: **underline & strikeout** on Highlight's text-quad path, a **pen** plus **lines /
arrows / rectangles / ellipses**, a shared **colour · width · opacity · fill** picker with curated
per-verb text-markup palettes, and full **object editing** — marquee and Ctrl-click multi-select,
move, **resize** (single + group, about the bounding box), **z-order** (Bring to Front / Send to
Back, which is both paint *and* hit order), and group **copy / cut / paste** that preserves the
arrangement. Everything bakes into the saved PDF and reopens editable. Four fixes came out of owner
testing and shipped in the same tranche: re-marking text now **merges** into the existing mark
instead of stacking a second layer ([#139](https://github.com/utyagi24/klarpdf/pull/139)), mark
paint order in the preview now follows the model's z-order rather than the mark's *type* — so a
filled shape hides a text box exactly as it does in the saved file
([#140](https://github.com/utyagi24/klarpdf/pull/140)), group copy/paste reversed an earlier
deferral ([#141](https://github.com/utyagi24/klarpdf/pull/141)), and the toolbar's dropdown arrows
share one position ([#142](https://github.com/utyagi24/klarpdf/pull/142)). Release:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.14.0>. 737 headless tests green (1 expected
skip — the Poppler `pdftotext` cross-check, absent on Windows).

**v0.12.0 "Navigate & Polish"** — the GUI tranche's **R1 (M45–M50)**. **Outline sidebar**: a document with bookmarks gets a Pages | Outline switcher (no
TOC → no tab and no tab bar, owner rule) showing the **live** `remapped_toc()` tree — follows
edits, tracks scroll, click-to-jump — plus **Go to Page…** (Ctrl+G)
([#117](https://github.com/utyagi24/klarpdf/pull/117)). **Context menus everywhere**, hit-test
routed — selection / internal link / **external link (Copy Link Address)** / annotation / bare
page / sidebar ([#118](https://github.com/utyagi24/klarpdf/pull/118)). **Search-all results
panel** — List All shows page + context-snippet rows, click-to-jump; the surface M64 reuses
([#119](https://github.com/utyagi24/klarpdf/pull/119)). **Crop pages** — `crop_override` rides the
PageRef like rotation; page/selected/all scopes; *hidden, not removed*; Remove Crop restores the
full MediaBox even for pre-cropped files ([#120](https://github.com/utyagi24/klarpdf/pull/120)).
**Night reading mode** — view-only inversion; file/print/export stay true-colour
([#121](https://github.com/utyagi24/klarpdf/pull/121)). **The Tools menu** — modes out of View,
Rotate into Edit beside the page ops ([#123](https://github.com/utyagi24/klarpdf/pull/123)).
Review-testing fixes folded in: toolbar text tools **apply to a live selection** (Preview-style);
the find bar **revives its kept query** on reopen; the sidebar keeps its width bounds with the
switcher mounted; two offscreen-suite deadlock classes fixed (stale-watcher zombie prompts; a
conftest guard that fails loudly on any unexpected modal); and a **save-fidelity fix** — URI links
PyMuPDF's `insert_pdf` silently drops (unbalanced-paren URIs, seen in the wild) are restored at
materialise ([#122](https://github.com/utyagi24/klarpdf/pull/122)). **v0.11.0 stays reserved for
the MCP / Agent Bridge** (owner decision, PR #116) — hence R1 = v0.12.0. 485 headless tests green.
*(That reservation was never used and is now spent — the bridge is scheduled with its version
assigned at tag time; see the MCP / Agent Bridge roadmap section below. Left as written because it
is the record of why R1 skipped v0.11.0.)*

**v0.10.1** — a patch fixing the Windows shell integration v0.10.0 got wrong.
**The app icon is now a tile.** The brand mark is a portrait page, so it spanned only **59%** of the
square canvas Windows gives an icon (24×24 for the taskbar) — against 82–100% for every other app on a
typical machine — and read as *tiny*. `ui/icons/klarpdf.svg` is a gradient rounded square that spans
100%. **`.pdf` files get their own icon**: the ProgID `DefaultIcon` pointed at `klarpdf.exe,0`, so
every PDF on disk wore the *application's* icon; a new `klarpdf-doc.ico` (from the brand's
`pdf-file-icon.svg`, drawn for this and never wired up) now shows a page. The free-standing mark
survives in the About dialog. And **Setup and the uninstaller now refuse to run while KlarPDF is
open** — the app holds a named mutex Inno watches (`AppMutex`). Without it, uninstalling a running app
left the install directory behind (Windows won't delete a running `.exe`) and *recreated*
`%LOCALAPPDATA%\klarpdf`, because the dying process rewrites `view_state.json` on shutdown. It refuses
rather than force-closes: KlarPDF prompts on unsaved edits, and Restart Manager would bypass that
prompt.

**v0.10.0** — **"KlarPDF"**, the rebrand + open-source release. The app formerly
built as `pdfproj` is now **KlarPDF** (*klar* = "clear"): new name, new mark and toolbar glyph set, a
root **AGPL-3.0-or-later `LICENSE`** + `THIRD_PARTY_LICENSES`, and a **Help menu** — About (version,
licence, no-warranty notice, a link to the source at *this exact tag*) and Open-Source Licenses (the
bundled texts, offline). Community-health files and a governance policy landed too: issues are open to
everyone, pull requests are restricted to the maintainer and invited collaborators. Windows-facing
consequences of the rename: a **fresh Inno `AppId`**, so `klarpdf-setup.exe` installs as a *new* app —
**uninstall `pdfproj` first** (`RELEASE.md`) — a `KlarPDF.Document` ProgID, `%LOCALAPPDATA%\klarpdf`
for settings, and the exes finally carry **version metadata** (`ProductName`/`FileVersion`), which the
spec had never set. Milestones **M0–M38 complete** (v0.1.0 = M0–M9,
v0.2.0 = M10–M15, v0.3.0 = M16–M19, v0.4.0 = M20–M22, v0.5.0 = M23–M26, v0.6.0 = M27–M30,
v0.7.0 = M31 + M31.5 + M34, v0.8.0 = M35–M37, v0.9.0 = M32 + M33 + M38). Releases:
<https://github.com/utyagi24/klarpdf/releases/tag/v0.12.0> ·
<https://github.com/utyagi24/klarpdf/releases/tag/v0.10.1> ·
<https://github.com/utyagi24/klarpdf/releases/tag/v0.10.0> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.6> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.5> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.4> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.3> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.2> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.1> ·
<https://github.com/utyagi24/pdfproj/releases/tag/v0.9.0>. **v0.9.6** is a bug-fix patch: the **Pages
sidebar thumbnails no longer flicker** at the window height where the bottom edge meets the last
thumbnail — the thumbnail is sized off a scrollbar-invariant width, so the vertical scrollbar toggling
on/off can no longer drive a resize→scrollbar→resize loop
([#87](https://github.com/utyagi24/pdfproj/pull/87)); and **a second PDF opened from Explorer now comes
to the front** (previously only the first did) — the forwarding launch hands its foreground right to the
resident instance via `AllowSetForegroundWindow`, which Windows otherwise denies a background process
([#88](https://github.com/utyagi24/pdfproj/pull/88)). **v0.9.5** is a viewer-polish patch: the page
opens **centred** and stays centred, and **Fit Width / Fit Page are sticky on resize**
([#80](https://github.com/utyagi24/pdfproj/pull/80)); the **Pages sidebar** gets a narrower default
with a centred single column whose thumbnails **scale with the sidebar width** (Preview-style, capped);
a **rotated page that's wider than the view is centred** on fit; and the **Highlight** and
**Redact-Text** tools now **preview the armed selection in their final colour** (highlight colour /
redaction black) while you drag ([#81](https://github.com/utyagi24/pdfproj/pull/81)). **v0.9.4** is a dependency security patch:
bump **pypdf 6.13.2 → 6.13.3**, clearing GHSA-jm82-fx9c-mx94 (Moderate memory-DoS in the `pypdf`
fallback edit engine; no functional change). **v0.9.3** is an open-behavior patch
([#66](https://github.com/utyagi24/pdfproj/pull/66)): a new window opens **on the monitor under the
cursor** (where you double-clicked in Explorer) instead of always the primary; the **open-from-Explorer
flicker is gone** — `activate_window` now raises the window with a `SetWindowPos` z-order nudge instead
of toggling the `WindowStaysOnTopHint` flag (changing a window flag recreates the native window on
Windows → a visible flash every raise); and the **Pages sidebar is hidden by default**, remembered
app-wide, for a clean fast open. **v0.9.2** was a load-time / UX patch:
the open render/zoom **flicker is gone** (the page renders once at Fit Page, at the final geometry, instead of
being re-sized/re-zoomed after the window is already visible — [#63](https://github.com/utyagi24/pdfproj/pull/63)),
and the **Pages sidebar renders thumbnails lazily** (only the pages scrolled into view, not every
page up front), so large documents open far faster — a 320-page doc went ~1010 ms → ~150 ms
([#64](https://github.com/utyagi24/pdfproj/pull/64)). **v0.9.1** was a UX patch: a document window
opens at the **full screen height, centred horizontally, at Fit Page**
([#61](https://github.com/utyagi24/pdfproj/pull/61)). v0.9.0 "Encrypted & Links" adds
**encrypted / password-protected PDFs** (prompt + `authenticate` on open, then the source is held
decrypted so the output stays unencrypted) and **internal links** — `links_remap` rebuilds GoTo
**and** named-destination links at materialize so reorder/delete/Save keeps them working, and the
viewer makes them **clickable** (click → jump to the target page; `viewer/links.py`). **v0.8.1** was
a bug-fix patch: double-click open from a case-sensitive `\\wsl.localhost\` / UNC folder works for
every file ([#55](https://github.com/utyagi24/pdfproj/pull/55)). v0.8.0 "Images" adds **image import**
(drag a local PNG/JPEG/… from Explorer onto the Pages sidebar → it inserts as a page, converted via
PyMuPDF `convert_to_pdf`) and **image export** (`File ▸ Export ▸ Image…`; selected page(s) → PNG/JPEG
at a chosen DPI, edits-aware off `render_output`), plus UI polish (clearer multi-page selection,
vertical-centred fitting page, centred text-box text). v0.7.0 "Round-trip & Export" adds
**annotation round-trip editing** (reopen a saved doc → move / edit / remove our author-tagged
highlights & text boxes; the page render strips our baked marks so the editable overlay is the single
source of truth, and text selection reads that stripped page) and a flatten **Export → PDF**
(`File ▸ Export`; bakes annotations + form widgets into page content via `Document.bake()`,
text-preserving — a locked counterpart to the round-trip). v0.6.0 "Rich Text & Live Preview" adds
**styled text boxes**, **live thumbnails**, and **dynamic theme icons**. v0.5.0 "File Safety & Output"
adds **Revert to Saved**, an **external-change warning**, and **edits-aware printing**. v0.4.0
"Annotate & Redact" adds text **highlight** + **text boxes** and **true destructive redaction**.
**R2 "Document Hygiene" (M51–M54) is merged and tagged `v0.13.0` — no published release** (owner
call, 2026-07-19): the owner validated the merged build directly and skipped M55's release cut;
R2's features first *ship* with the next published release. **Next:** two planned roadmaps —
**v0.11.0 "MCP / Agent Bridge"** (M39–M44; `PLAN.md` §MCP / Agent Bridge roadmap; the version
number stays reserved for it) and the **GUI feature tranche's remaining releases R3–R5**
(M56–M70; `PLAN.md` §GUI feature roadmap — next tranche milestone is **M56**; sequencing vs the
bridge stays the owner's call). Other deferred items live in `PLAN.md` §Future enhancements.
**Open follow-ups** (carried items) are at the bottom.

- [x] **M0** Scaffold + WSL dev venv — *step 1 (WSL); WSL* — [#4](https://github.com/utyagi24/pdfproj/pull/4)
- [x] **M1** Correctness core: `model/` + headless tests green ⭐ — *steps 5, 7; WSL* — [#5](https://github.com/utyagi24/pdfproj/pull/5)
- [x] **M2** Viewer: render / scroll / zoom / rotate / thumbnails — *step 3; WSLg* — [#6](https://github.com/utyagi24/pdfproj/pull/6)
- [x] **M3** Selection + search — *step 4; WSLg* — [#7](https://github.com/utyagi24/pdfproj/pull/7)
- [x] **M4** Editing loop: cross-window cut/copy/paste + undo/redo + Save/Save As + close-prompt — *steps 6, 8; WSLg* — [#8](https://github.com/utyagi24/pdfproj/pull/8)
- [x] **M5** Single-instance launcher logic — *step 2; WSL (validate on Windows)* — [#9](https://github.com/utyagi24/pdfproj/pull/9)
- [x] **M6** Windows ship lock: python.org + hashed `win_amd64` wheels — *step 1 (Win); Windows* — [#11](https://github.com/utyagi24/pdfproj/pull/11)
- [x] **M7** Windows validation: instance / focus + GUI fidelity (Open-With → M8/M9) — *step 2; Windows* — [#12](https://github.com/utyagi24/pdfproj/pull/12)
- [x] **M8** Freeze + installer → `pdfproj-setup.exe` + portable + CI — *step 9; Windows* — [#14](https://github.com/utyagi24/pdfproj/pull/14)
- [x] **M9** Verify + release: matrix green + CI build + **v0.1.0** tagged & released — *Verification §; Windows* — [release](https://github.com/utyagi24/pdfproj/releases/tag/v0.1.0)

⭐ M1 is the keystone — most correctness risk, GUI-free, fully testable in WSL/CI.

## Releases — v0.2.0 ✅ → v0.3.0 ✅ → v0.4.0 ✅ → v0.5.0 ✅ → v0.6.0 ✅ → v0.7.0 ✅ → v0.8.0 ✅ → v0.9.0 ✅

Spec + architecture in `PLAN.md` (§Shipped roadmap for v0.2–v0.4, §Next roadmap for v0.5–v0.9). Same
conventions: **one PR per milestone**, tick the box here on merge. ⭐ marks a keystone (most risk,
GUI-free core, fully headless-testable).

**v0.2.0 ✅ — "Polish, Print & Forms"** (shipped)

- [x] **M10** Icons — app `.ico` + toolbar icons (undo/redo, zoom, cut/copy/paste) — *WSLg + Windows (frozen-exe icon)* — [#18](https://github.com/utyagi24/pdfproj/pull/18) (frozen-exe icon validated at M15)
- [x] **M11** Zoom UX — live magnification % indicator + Actual-Size / 100% reset (Ctrl+0) + presets — *WSLg* — [#19](https://github.com/utyagi24/pdfproj/pull/19)
- [x] **M12** Printing — `QtPrintSupport` system print dialog; PyMuPDF render at printer DPI — *WSL logic; Windows print validation* — [#21](https://github.com/utyagi24/pdfproj/pull/21) (physical-printer dialog = manual check)
- [x] **M13** Recent documents — MRU list + dynamic File ▸ Open Recent submenu — *WSL* — [#22](https://github.com/utyagi24/pdfproj/pull/22)
- [x] **M14** ⭐ Page-edit layer + form filling (fill existing AcroForm fields) — *WSL (model+tests) + WSLg* — [#24](https://github.com/utyagi24/pdfproj/pull/24) (model foundation) + [#25](https://github.com/utyagi24/pdfproj/pull/25) (inline fill)
- [x] **M15** Verify + release → tag **v0.2.0** (CI Node-24 action bumps folded in; code signing still deferred) — *Windows* — [#26](https://github.com/utyagi24/pdfproj/pull/26)

**v0.3.0 ✅ — "Interaction & Drag-and-Drop"** (shipped)

- [x] **M16** Drag visuals — page-thumbnail drag pixmap (+ "N pages" badge) + custom drop-insertion marker — *WSLg* — [#28](https://github.com/utyagi24/pdfproj/pull/28)
- [x] **M17** Explorer file drop — drag a `.pdf` from Explorer onto the Pages panel → insert at the drop slot — *WSL (logic) + WSLg* — [#29](https://github.com/utyagi24/pdfproj/pull/29)
- [x] **M18** Grab / Select mode — hand/pan vs text-selection toggle (default Select), toolbar + View menu — *WSLg* — [#30](https://github.com/utyagi24/pdfproj/pull/30)
- [x] **M19** Verify + release → tag **v0.3.0** — *Windows* — [#31](https://github.com/utyagi24/pdfproj/pull/31)

**v0.4.0 ✅ — "Annotate & Redact"** (keystone release, shipped)

- [x] **M20** ⭐ Annotations — text highlight + text-box (free-text) on the M14 layer — *WSL + WSLg* — [#32](https://github.com/utyagi24/pdfproj/pull/32) (per-page model) + [#33](https://github.com/utyagi24/pdfproj/pull/33) (viewer highlight/text-box interaction)
- [x] **M21** ⭐ Redaction — true destructive `apply_redactions` + leak verification (`fitz` + Poppler `pdftotext` cross-engine). Two entry points, one multi-rect `Redaction` descriptor: **Redact Region** (one-shot rubber-band, for images/logos) + **Redact Selection** (text-flow, one continuous bar per line). A redacted **Save is a point of no return** (confirm → write clean → reload from clean file → clear undo: secret gone from disk *and* RAM). Bundled text-box UX polish (one-shot armed inserts; drag-to-move; double-click re-edit; auto-grow W+H; clamp to page). Forward-compat hooks for future round-trip + font/size/colour picker (`TextBox.fontname`; pdfproj author-tag on baked annots). Annotate/redact tools unified as **one-shot armed** gestures (Text Box click; Highlight/Redact-Text drag-over-text — continuous bar per line; Redact-Block drag-rect), grouped together. Cross-window page drag/paste **carries per-page edits** (annotations + redactions + rotation). — *WSL (model+verify) + WSLg* — [#34](https://github.com/utyagi24/pdfproj/pull/34)
- [x] **M22** Verify + release → tag **v0.4.0** (version bump + docs; 232 headless tests green) — *Windows* — [#35](https://github.com/utyagi24/pdfproj/pull/35)

**v0.5.0 — "File Safety & Output"** (planned)

- [x] **M23** Revert / Reopen — discard all edits + reload from disk (reuse `reload_from_file` + clear undo, dirty-confirm) — *WSL + WSLg* — [#37](https://github.com/utyagi24/pdfproj/pull/37)
- [x] **M24** External-change warning — file-changed-on-disk detection (`QFileSystemWatcher` + `(mtime, size)` signature) → Reload / Keep prompt (+ Overwrite / Reload / Cancel before an overwriting Save) — *WSL (logic) + Windows* — [#38](https://github.com/utyagi24/pdfproj/pull/38)
- [x] **M25** Edits-aware printing — Print renders the same edits-applied output a Save would write (page order, rotation, form values, highlights, text boxes, redactions), so a not-yet-saved redaction no longer prints the original. Preview / "Save as PDF" / scale modes were dropped (the native dialog can't host them; a rasterised PDF is worse than Save As) — the page→image render is kept as the engine for the planned **image export** (M36). — *WSL logic; Windows print validation* — [#39](https://github.com/utyagi24/pdfproj/pull/39)
- [x] **M26** Verify + release → tag **v0.5.0** — *Windows* — [#40](https://github.com/utyagi24/pdfproj/pull/40)

**v0.6.0 ✅ — "Rich Text & Live Preview"** (shipped)

- [x] **M27** ⭐ Styled text boxes — font family/size/colour + box fill + box outline (on/off, black), via a formatting bar on the inline editor. **B/I/U + coloured outline descoped** (owner call — base-14 bold/italic variant names don't render on PyMuPDF's FreeText appearance path; they'd force the heavier richtext path). Simple `add_freetext_annot` (`text_color`/`fill_color`/`border_width`), text stays in `/Contents`. — *WSL (model+tests) + WSLg* — [#41](https://github.com/utyagi24/pdfproj/pull/41) (model) + [#42](https://github.com/utyagi24/pdfproj/pull/42) (viewer)
- [x] **M28** Live thumbnails — thumbnails reflect the page's edited state (annotations/redactions/fills), rendered from the shared `render_output` bake (only when the doc has edits; clean docs keep the fast source render) — *WSLg* — [#43](https://github.com/utyagi24/pdfproj/pull/43)
- [x] **M29** Dynamic theme icons — runtime OS light↔dark re-tint. Verify revealed it never fired: `changeEvent` matched only `ApplicationPaletteChange`, but Qt delivers `PaletteChange`; now handles both, so the toolbar glyphs re-tint live (app icon is theme-agnostic) — *WSLg + Windows* — [#44](https://github.com/utyagi24/pdfproj/pull/44)
- [x] **M30** Verify + release → tag **v0.6.0** (version bump + docs; 285 headless tests green) — *Windows* — [#45](https://github.com/utyagi24/pdfproj/pull/45)

**v0.7.0 ✅ — "Round-trip & Export"** (shipped)

- [x] **M31** ⭐ Annotation round-trip editing — reopen → move/edit/remove our author-tagged annotations (strip-then-re-add at materialize); page render + text selection read the stripped page so the editable overlay is authoritative (no double-draw / stale-position select) — *WSL (model+tests) + WSLg* — [#46](https://github.com/utyagi24/pdfproj/pull/46)
- [x] **M31.5** Export → PDF (flatten) — new **Export** action (`File ▸ Export`); bake annotations + form widgets into page content (PyMuPDF `Document.bake()`, text-preserving — locks the marks, the opposite of M31's round-trip). Extensible Export path (`model/export.py`); M36 adds an image format. — *WSL (model+tests) + WSLg* — [#48](https://github.com/utyagi24/pdfproj/pull/48)
- [x] **M34** Verify + release → tag **v0.7.0** (version bump + docs + re-scope; 317 headless tests green) — *Windows* — [#49](https://github.com/utyagi24/pdfproj/pull/49)

> Re-scope (owner, 2026-06-20): encrypted-PDF (M32) + internal-link remap (M33) moved **out of
> v0.7.0** to a new **v0.9.0**, so the image work (v0.8.0) ships next.

**v0.8.0 ✅ — "Images"** (shipped)

- [x] **M35** Image import — drag a local image (jpg/png/…) from Explorer onto the Pages sidebar → insert as a new page (reuse M17 drop + PyMuPDF `convert_to_pdf`) — *WSL (logic) + WSLg* — [#50](https://github.com/utyagi24/pdfproj/pull/50)
- [x] **M36** Image export — **extend the M31.5 Export feature** to images: selected page(s) → PNG/JPEG at a chosen DPI (reuse M25 `render_output` + `_page_image`; edits-aware) — *WSL (render) + WSLg* — [#51](https://github.com/utyagi24/pdfproj/pull/51)
- [x] **M37** Verify + release → tag **v0.8.0** (version bump + docs; 341 headless tests green) — *Windows* — [#54](https://github.com/utyagi24/pdfproj/pull/54)

> Pre-release polish (owner, 2026-06-20): clearer multi-page selection in the sidebar + vertically
> centred fitting page ([#52](https://github.com/utyagi24/pdfproj/pull/52)) and vertically centred
> text-box text ([#53](https://github.com/utyagi24/pdfproj/pull/53)).

**v0.9.0 ✅ — "Encrypted & Links"** (shipped; re-scoped out of v0.7.0)

- [x] **M32** Encrypted / password PDFs — detect `needs_pass`, prompt, `authenticate` on open (then store the source decrypted in memory; output stays unencrypted) — *WSL + WSLg* — [#57](https://github.com/utyagi24/pdfproj/pull/57)
- [x] **M33** Internal link remap **+ navigation** — `links_remap` rebuilds GoTo **and** named-destination links at materialize (reorder/delete/Save keeps them working; named dests baked to GoTo — insert_pdf drops them entirely), **and** the viewer makes internal links clickable (click → jump to target page; pointing-hand on hover, `viewer/links.py`) — *WSL (model+tests) + WSLg* — [#58](https://github.com/utyagi24/pdfproj/pull/58)
- [x] **M38** Verify + release → tag **v0.9.0** (version bump + docs; 369 headless tests green) — *Windows* — [#59](https://github.com/utyagi24/pdfproj/pull/59)

## Roadmap — "MCP / Agent Bridge" (**next**; version assigned at tag time)

Spec + architecture in `PLAN.md` §MCP / Agent Bridge roadmap. Same conventions: **one PR per
milestone**, tick the box here on merge. ⭐ marks the keystone (GUI-free, fully headless-testable).
A new MCP server surface (`mcp_bridge/` package) that reuses the GUI-free `model/` core **without PySide6**
and ships as a separate optional component — the `klarpdf-setup-x64.exe` audit surface is untouched.

**Scheduled 2026-08-12** after a premise review (the roadmap was written 2026-06-22 and sat while
R1–R6 shipped). The eight decisions it settled, and the premises that died with it, are in
`PLAN.md` §MCP / Agent Bridge roadmap → Revision note (2026-08-12) — **not restated here**. Two
status facts that belong here: the reserved **v0.11.0 is spent** (v0.12.0 → v0.17.1 shipped past it),
so M44 assigns a version at tag time; and the bridge goes **before** the three remaining 1.0 gate
items, which are independent of it.

- [x] **M38.5** *(prerequisite)* Bounded retry around the save path's `os.replace` — clears the
  flake recorded in Open follow-ups and fixes the real user-facing case behind it (a transient lock
  on the freshly written temp, antivirus the usual suspect, surfacing as a spurious "Save failed").
  `test_single_instance` is **deliberately not** in scope: no reproduction and no fix plan, and
  neither flake has ever failed the required `ubuntu-latest` check in 200 recorded runs — *WSL*
  ([#239](https://github.com/utyagi24/klarpdf/pull/239)) — `util/atomic.py:atomic_replace` retries
  `PermissionError` four times over ~0.75 s; both write sites (Save and every Export) now use it
- [x] **M39** ⭐ MCP scaffold + read-only core — `mcp_bridge/` stdio server on the official **`mcp` 2.x** SDK
  (`MCPServer`); headless query/metadata tools (`get_info`, `get_outline`, `search`, `extract_text`,
  `render_page`, `get_form_fields`), `search` reusing `model/page_text.py`; a **test asserting** no
  PySide6 on the server path; headless tests — *WSL*
  ([#240](https://github.com/utyagi24/klarpdf/pull/240)). Three deviations from the roadmap, each
  recorded next to the design it changes in `PLAN.md` §MCP / Agent Bridge roadmap: the package is
  **`mcp_bridge/`, not `mcp/`** (a local `mcp/` shadows the SDK it is built on — measured);
  **encrypted input landed here rather than at M41**, because opening an encrypted source had to be
  handled anyway; and the SDK joined **`requirements-dev.in`** so CI can run the new tests at all,
  with M42 still owning the separate `requirements-mcp.{in,txt}` a bridge user installs.
- [x] **M40** Transform tools — `split` / `merge` / `reorder` / `delete_pages` / `rotate` /
  `fill_form` / `flatten` / `export_images` to an explicit out path (never overwrites source;
  lossless OCR/TOC/forms); headless tests — *WSL*
  ([#241](https://github.com/utyagi24/klarpdf/pull/241)). Losslessness is asserted with
  `test_materialize.py`'s own invariants on the same fixtures. Two additions to the safety model,
  recorded in `PLAN.md` §Safety model: an **existing output** is refused unless `overwrite=true`,
  and writes go to a **sibling temp + rename** so a failure leaves no half-written PDF, through
  M38.5's `atomic_replace` — the two write paths do not diverge on the antivirus race.
- [x] **M41** Redaction + encrypted — `redact_regions` / `redact_text` (destructive + cross-engine
  leak verify) and encrypted-input (`password`) tools; headless leak assertion — *WSL*
  ([#242](https://github.com/utyagi24/klarpdf/pull/242)). A failed verification **deletes the
  output and raises**, so a returned path always names a file that was re-read. Verification counts
  occurrences rather than testing presence — a presence check destroyed a correct output when the
  redacted word was a substring of surviving text (redacting "Smith" out of "Smith and
  Smithsonian"); the rule and its two-way exactness are in `PLAN.md` §Safety model.
- [x] **M42** Dependency lock + packaging — `requirements-mcp.{in,txt}`, **cross-platform and
  unhashed** (a hashed `win_amd64` lock would make the bridge accidentally Windows-only); GUI lock
  untouched; fourth `pip-audit` step in `audit.yml` + `tools/audit-deps.ps1`; `klarpdf-mcp` entry
  point; `.mcp.json` + Claude Desktop config docs; **`.mcpb` bundle** with `==` pins in its
  `pyproject.toml`, a README note that this path installs **online** — *WSL*
  ([#243](https://github.com/utyagi24/klarpdf/pull/243)). **`server.type = "uv"` does not exist** —
  `mcpb` 2.1.2 accepts only `python | node | binary`, so the manifest declares `python` while its
  command *is* `uv`, preserving every property the decision was made for and adding one
  prerequisite (`uv` on PATH). The correction and its evidence are in `PLAN.md` §Dependencies &
  packaging. Bundle measured at 95 KiB / 25 files, no vendored env, no PySide6.
  - **Carried to M44 (Windows/macOS):** whether the host honours a `uv.lock`, and the
    Desktop/one-click install itself. Both need Claude Desktop, which WSL does not have. The
    audit-scope gap (the `.mcpb` resolves online, so `pip-audit` covers the pip/pipx path only)
    stays open and is stated in `mcp_bridge/README.md` rather than glossed.
- [x] **M43** Hardening + docs — path allowlist, return-size caps, `--read-only` opt-out flag
  (writes are on by default), error handling; README usage + example agent workflows — *WSL*
  ([#244](https://github.com/utyagi24/klarpdf/pull/244)). `--read-only` **withholds** the write
  tools rather than refusing them when called; `--allow-root` is unrestricted by default (a stdio
  server already runs with the user's own file access — the reasoning is in `PLAN.md` §Safety
  model); caps truncate text and hits with the real total, but *error* on an oversized render,
  because half an image is not a partial answer.
- [x] **M43.1** *(unplanned)* Three defects the first hands-on session with a real client found,
  none of which any test had caught, all sharing a cause — the suite exercises the code from inside
  a working checkout, which is the one place they cannot happen
  ([#246](https://github.com/utyagi24/klarpdf/pull/246)) — *WSL*
  - **No "extract pages" tool.** Asked to pull pages out, the agent shelled out to `pdfunite`.
    `extract_pages` now wraps `export_selected_pages` (M51), which existed unexposed.
  - **`python -m mcp_bridge` only works with the repo as CWD**, so the checked-in `.mcp.json` and
    both READMEs failed the moment a client was pointed at a folder of PDFs. `klarpdf-mcp` is the
    documented command everywhere now.
  - **`pipx install .` installed a script with no dependencies** — `pyproject.toml` had no
    `dependencies`, so the built metadata carried zero `Requires-Dist`. Floors added; a test now
    builds the metadata and asserts them. Detail in `PLAN.md` §Tool surface.
  - **…and that test then caught the same disease it was written for.** It calls
    `setuptools.build_meta` directly, on purpose, so the metadata check needs no network — but
    skipping build isolation skips the step that would have installed the backend, and
    `[build-system] requires` is honoured *only* for an isolated build. Python 3.12's `ensurepip`
    no longer ships setuptools, so CI failed with `ModuleNotFoundError` from the day the test
    landed while every dev machine passed on a leftover install. `setuptools` is now a direct
    entry in `requirements-dev.in`, which needs `pip-compile --allow-unsafe` (pip-tools omits it
    otherwise) — the compile command in the file's header changed to match.
- [x] **M43.2** *(unplanned)* A phrase redaction that leaked, and the verification that certified
  it — found by M44's own verification pass, test case TC-001
  ([#248](https://github.com/utyagi24/klarpdf/pull/248)) — *WSL*
  - **`redact_text "regular expression"` with `whole_words: true` removed 2 of 5 occurrences and
    reported success**, leaving a fully legible `regular expression.` in the output. Root cause is
    older than the bridge and shipped in the viewer: `PageText.is_whole_word` was purely geometric,
    and MuPDF splits `get_text("words")` on whitespace, so `expression.` is one word whose box
    includes the period — a hit covering just the letters read as *inside a longer word*. Every
    whole-word match at a sentence end has been dropped by the find bar since M64. Fixed in
    [#247](https://github.com/utyagi24/klarpdf/pull/247).
  - **A case-sensitive phrase lost anything that wrapped a line.** MuPDF returns a wrapped phrase as
    one box per line fragment; the filter compared each fragment against the whole term. Also #247.
  - **The verification could not fail on a matching bug.** It was box-scoped — it proved the regions
    it chose had lost their text, deriving its budget from those same regions, so an occurrence the
    matcher never found widened the allowance by exactly the amount it leaked (measured: budget
    `regular ≤ 1`, `expression ≤ 3`; residue exactly 1 and 3). `redact_text` now also proves the
    *query* is gone, twice: re-running its own search, and a textual scan of each engine's
    extracted text. The second is the load-bearing one — a matcher cannot see an occurrence it
    failed to redact, so a check that reuses it inherits its blind spots. `residual_matches` reports
    the verified count.
- [x] **M43.3** *(unplanned)* A wrapped match counted as two — found by the owner while checking
  M43.2's output ([#249](https://github.com/utyagi24/klarpdf/pull/249) viewer,
  [#250](https://github.com/utyagi24/klarpdf/pull/250) bridge) — *WSL*
  - Searching `regular expression` reported **7 matches for 5 occurrences**: MuPDF returns a match
    spanning a line break as one rect per line, and both the find bar and `search` appended a hit
    per rect. The bar read "4 of 7", Next stepped through one occurrence twice, and a row's snippet
    showed only the half of the phrase that fitted on the first line. Pre-existing since M64 and
    masked until M43.2 — the second fragment usually ended in punctuation and was being dropped by
    the whole-word bug, so the miscount sat behind a worse defect.
  - A hit is now an **occurrence** carrying one box per line it occupies. `PageText.group_matches`
    folds a term's boxes by accumulating the text under them until it spells the term; a run that
    does not spell it is emitted box by box, because an ungrouped box costs a miscount while a
    missing one costs a leak. `search` returns `boxes`, `redact_text` reports `matches` and
    `boxes_redacted`, and `redact_regions` accepts `boxes` so a hit goes back in whole.
- [ ] **M44** Verify + release → tag (version at tag time) — tool round-trips + leak verify +
  no-network/no-port + no-Qt assertion + cross-platform + runs from Code/Desktop — *Windows*
  **Runbook written 2026-08-12: `RELEASE.md` §4.** Of the ten matrix items, **six are automated and
  green on every PR** (round-trips, cross-engine leak verify, no-socket, no-Qt, source
  byte-identical, Linux). Three need a machine WSL does not have — the Windows lock resolve, Claude
  Desktop config + one-click `.mcpb` — and the tenth (does the host honour a `uv.lock`) is the open
  question M42 could not answer. Then the tag, which is an owner action.

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

- [x] **M45** ⭐ Outline sidebar (no TOC → no tab; live `remapped_toc`; scroll tracking) + Go to Page (Ctrl+G). The sidebar becomes a Pages | Outline switcher **only** for a TOC'd document (dock title "Sidebar"; the View-menu/toolbar toggle is renamed "Sidebar" — one stable label for both document kinds); TOC-less docs keep the bare Pages panel. Bundled fix: a reload-in-place now resyncs the file watcher, and a **closed** window can no longer raise the "file changed on disk" prompt (a lingering hidden window's stale watcher + a stray activation event = an unanswerable modal — it deadlocked the offscreen suite, and the pre-existing save-cancel path could trigger it too). — *WSL + WSLg* — [#117](https://github.com/utyagi24/klarpdf/pull/117)
- [x] **M46** Context menus everywhere — selection / link / empty-page / sidebar, hit-test routed.
  `PdfView.contextMenuEvent` delegates to a MainWindow-built menu by hit state: our annotation →
  Remove (the pre-M46 menu, now routed); live selection → Copy / **Highlight Selection** / **Redact
  Selection** (apply-now, vs the toolbar's armed one-shots); internal link → Go to Page N; **external
  link → Copy Link Address** (URI links stay non-clickable — clipboard only, offline guarantee
  intact); bare page → the routed View-menu QActions (fits · rotate · Go to Page). Sidebar menu adds
  Rotate Left/Right (extract joins at M51; paste-object at M59). — *WSLg* — [#118](https://github.com/utyagi24/klarpdf/pull/118)
- [x] **M47** Search-all results panel (page + snippet, click-to-jump; M64 reuses it). The FindBar
  gains a **List All** toggle → a hit-list band under the bar ("p. N   …snippet…"; hidden until
  asked — no dead chrome). Snippets are the hit's text line windowed ±4 words with ellipses; click
  a row → that hit becomes current and is revealed; the panel follows the query as typed, tracks
  next/prev, and empties with the overlay on a structural edit. — *WSLg* — [#119](https://github.com/utyagi24/klarpdf/pull/119)
- [x] **M48** Crop pages — `crop_override` on PageRef; page/selected/all scopes; "hidden, not
  removed" wording; reset offered. Rides the PageRef exactly like `rotation_override` (absolute
  content-frame rect; snapshots for undo; follows reorder **and** cross-window copy/paste);
  materialised via `set_cropbox`; live in the viewer (crop-aware geometry/overlay mapping + clip
  render + baked thumbnails). Armed **Crop Pages** drag → scope prompt (This/Selected/All) with the
  honesty wording; **Remove Crop** restores the full MediaBox — *including a crop the file arrived
  with* (pre-cropped sources also now display by their CropBox, fixing their layout). Odd/even
  book-scan crops stay deferred. — *WSL + WSLg* — [#120](https://github.com/utyagi24/klarpdf/pull/120)
- [x] **M49** Night reading mode (view-only pixmap invert). **View ▸ Night Reading Mode**
  (checkable, remembered app-wide): the page render inverts and the pre-render placeholder goes
  black (no bright flash); the file, print/export renders, and thumbnails keep true colours;
  independent of the followed OS theme. — *WSLg* — [#121](https://github.com/utyagi24/klarpdf/pull/121)
- [x] **R1 polish — the Tools menu** ([#123](https://github.com/utyagi24/klarpdf/pull/123); owner-decided during the stack review): the tranche's one
  budgeted top-level menu (`PLAN.md` §Design budgets) lands with the tools it was reserved for —
  Select/Grab and the armed one-shots (Text Box · Highlight · Redact ×2 · Crop + Remove Crop) move
  out of View into **Tools**; **Rotate Left/Right moves to Edit** beside the other page operations
  (it is a real, saved edit — the View placement implied a view-only spin). Shortcuts and the
  toolbar are unchanged; R3's Markup/Draw and R4's Stamp land straight into Tools.
- [x] **M50** Verify + release → tag **v0.12.0** (version bump + docs; 485 headless tests green on the
  merged main; local onedir build + smoke; CI draft → published) — *Windows* —
  [release](https://github.com/utyagi24/klarpdf/releases/tag/v0.12.0)

**R2 — "Document Hygiene"**

- [x] **M51** Extract selected pages → PDF + Insert blank / duplicate page — *Windows (headless + offscreen GUI)* — [#125](https://github.com/utyagi24/klarpdf/pull/125)
- [x] **M52** Reduce file size — Export ▸ Reduced Size PDF…; true-value presets + custom dpi/quality knobs; actual before→after — *Windows (headless + offscreen GUI)* — [#126](https://github.com/utyagi24/klarpdf/pull/126)
- [x] **M53** Properties + metadata (view · edit · remove; Info dict **and** XMP both) — *Windows (headless + offscreen GUI)* — [#127](https://github.com/utyagi24/klarpdf/pull/127)
- [x] **M54** ⭐ Document encryption — set/change/remove/carry-through, AES-256; optional advisory restriction flags — *Windows (headless + offscreen GUI)* — [#128](https://github.com/utyagi24/klarpdf/pull/128)
- [x] **M55** Verify + ~~release~~ tag — verify done (full headless suite green on merged main;
  owner validated the changes directly); **release cut skipped (owner call, 2026-07-19)** — main
  tagged **`v0.13.0`** only, so the version marks the R2 state without a published release. The
  CI draft the `v*` tag produces stays unpublished; R2's features first ship with the next
  published release. — *Windows*

**R3 — "Markup Tools"**

- [x] **M56** Underline & strikeout (Highlight's quad path; round-trip; Markup ▾ split-button) — *Windows (headless + offscreen GUI)* — [#130](https://github.com/utyagi24/klarpdf/pull/130)
- [x] **M57** ⭐ Pen & shapes model — ink/line+arrows/rect/ellipse descriptors, apply + read-back — *Windows (headless)* — [#131](https://github.com/utyagi24/klarpdf/pull/131) (shows **Closed**: merging #130 with `--delete-branch` removed this PR's *base* branch, which closes it irrecoverably; the commits reached `main` via #132, and the diff/review history is intact)
- [x] **M58** Pen & shapes tools — draw/move/delete, Shift-constrain, Draw ▾ split-button — *Windows (offscreen GUI)* — [#132](https://github.com/utyagi24/klarpdf/pull/132)
- [x] **M59** Copy / paste objects — object clipboard, cross-window, focus-routed Ctrl+C/X/V — *Windows (offscreen GUI)* — [#133](https://github.com/utyagi24/klarpdf/pull/133)
- [x] **M59.5** Markup colour · width · fill — shared sticky `MarkupStyle` + toolbar swatch button for underline/strikeout + pen & shapes — *Windows (headless + offscreen GUI)* — [#134](https://github.com/utyagi24/klarpdf/pull/134)
- [x] **M59.6** Multi-object selection — Objects mode: marquee + Ctrl-click; group restyle / move / delete (one undo each) — *Windows (offscreen GUI)* — [#135](https://github.com/utyagi24/klarpdf/pull/135)
- [x] **M59.7** Object resize — selection handles; single + group bounding-box resize (reusable placement component) — *Windows (headless + offscreen GUI)* — [#136](https://github.com/utyagi24/klarpdf/pull/136)
- [x] **M59.8** Object z-order — Bring to Front / Send to Back for a mark or group (paint + hit order) — *Windows (headless + offscreen GUI)* — [#137](https://github.com/utyagi24/klarpdf/pull/137)
- [x] **M59.9** Polish & fidelity — curated markup colour palettes (Markup ▾) · object opacity (`/CA`) · redaction preview z-order fix · edits keep your scroll place — *Windows (headless + offscreen GUI)* — [#138](https://github.com/utyagi24/klarpdf/pull/138)
- [x] **M59.10** Markup merge — re-marking text folds into the existing mark instead of stacking: same colour absorbs/extends, a different colour recolours what it covers and splits what it doesn't; one Remove, one undo step — *Windows (headless + offscreen GUI)* — [#139](https://github.com/utyagi24/klarpdf/pull/139)
- [x] **M59.11** Preview z-order fidelity — mark paint order follows the page's annotation tuple (not the mark's *type*), so a filled shape hides a text box's text as it does in the saved file, and the M59.8 z-order verbs restack the preview across types — *Windows (headless + offscreen GUI)* — [#140](https://github.com/utyagi24/klarpdf/pull/140)
- [x] **M59.12** Group copy / cut / paste — a multi-selection copies, cuts and pastes as a unit, keeping its arrangement (reverses M59.6's deferral, owner call); one undo step; labels count the set — *Windows (offscreen GUI)* — [#141](https://github.com/utyagi24/klarpdf/pull/141)
- [x] **M59.13** Dropdown-arrow placement — the Markup ▾ / Draw ▾ / style-swatch arrows all sit vertically centred with room from the icon, instead of Qt's two different per-popup-mode positions (one mid-height, one bottom-corner) — *Windows (offscreen GUI)* — [#142](https://github.com/utyagi24/klarpdf/pull/142)
- [x] **M60** Verify + release → **v0.14.0** tagged & published — *Windows*

**R4 — "Stamp, Sign & Watermark"**

- [x] **M61** ⭐ Unified content-draw engine (Way 2: presets = prefilled custom stamps; baked at save).
  `model/content_marks.py`: two descriptors — **`Stamp`** (text + optional rounded frame) and
  **`ImageStamp`** (a placed raster) — that ride the PageRef exactly like an annotation, but bake
  into the page's **content stream** at materialise instead of staying annotations. A **watermark is
  not a third type**: it is either of those with `under=True` (`overlay=False`, so the page's text
  sits on top), applied to every page in a range — the range is the UI's loop, not model state.
  Presets are **prefilled `Stamp`s**, so a placed preset is editable like a hand-made one and there
  is no second code path. Built **vector** (a throwaway one-page PDF placed via `show_pdf_page`)
  rather than the planned high-DPI pixmap: crisp at any zoom, stamp text stays searchable, and
  arbitrary rotation comes free — see `PLAN.md` §R4 "M61 as built". Because a content mark leaves
  nothing author-tagged to read back, a save that writes one is a **point of no return** like a
  redaction (`has_content_marks()` → confirm, write, reload from the clean file, or the next save
  would bake a second copy); the confirm now names which of the two it is committing. Move / resize
  / copy come from the existing `translate_mark` / `scale_mark` primitives. Print, export and live
  thumbnails inherit it via `render_output`. — *WSL (model+tests)* — 31 new tests, 767 green
- [x] **M62** Stamp & watermark UI — placement + dialogs + page-range apply. **There is no second
  placement system**: a content mark is a free-placed rect, so it joins `_OBJECT_TYPES` and inherits
  hit-testing, selection, move, corner-resize, z-order and delete from the M58/M59 object tools —
  which *is* the milestone's "drag rect, move, corner-resize until save", built by reuse. A new
  one-shot **`ArmedTool.STAMP`** shares the draw-gesture path (drag the box; Shift squares it).
  Two flows over the one engine: a **stamp / signature** is composed then *placed*, a **watermark**
  covers whole pages so it applies at once, sized to **each page's own** box. `ui/stamp_dialog.py`
  (text · colour · angle · opacity · frame · page range; presets prefill and stay editable) +
  `util/page_range.py` (`"1-3, 7, 12-"`, shared with M64's scope). Both dialogs state the bake
  boundary in the dialog. Toolbar: one new slot, the **Stamp ▾** split-button (three new icons).
  Live preview renders through the *same generator that bakes at save*; an `under=True` watermark is
  drawn with **multiply** compositing, since Qt cannot paint beneath the page pixmap — the page's
  text darkens through it exactly as in the saved file. — *Windows (headless + offscreen GUI)* —
  46 new tests, 812 green
- [x] **M63** Image stamp / signature — the sign-and-return workflow, on M62's placement UI.
  **"Make white background transparent"** (`white_to_alpha` + threshold) keys the paper out of a
  **phone photo** of a signature, which otherwise arrives as ink on an opaque white rectangle that
  blanks out whatever it covers; a transparent PNG still works through its own alpha, and existing
  alpha is **intersected, never replaced**, so keying can't resurrect pixels the author removed.
  Keying runs at C speed (MuPDF greyscale + one `bytes.translate`) because a 12-megapixel input is
  realistic. `ui/signature_dialog.py` previews through the *same generator that bakes at save*, so
  the threshold is judged on the real result. **Recent signatures store paths only** — KlarPDF keeps
  no copy of a signature image, and deleting the file is the revocation mechanism; the list hangs
  off Stamp ▾ (hidden until non-empty), making the second use **two clicks**, no dialog. Documented
  as ink-equivalent, **not** a cryptographic signature, in the dialog itself. — *Windows (headless +
  offscreen GUI)* — 21 new tests, 833 green
- [x] **M64** Search & redact — **Tools ▸ Find and Redact…**: mark-all → review → redact-checked.
  The dialog drives the **real** `SearchController`, so hits highlight on the page while they are
  reviewed in **M47's results panel, now checkable** — a doubtful row can be clicked to jump to it
  before deciding. Hits arrive **ticked but prunable** (the user asked for all of them, then
  prunes), and **Match case** / **Whole words only** exclude the classic false positive wholesale:
  MuPDF's `search_for` is always case-insensitive and always matches inside words, so both are
  filters over its hits — case compares the text under the box, whole-word is a *geometric* test
  that the touched words don't extend past the hit. **Nothing here is destructive**: checked hits
  become ordinary `Redaction` descriptors (one per page, all in one macro → one undo step) that the
  existing confirmed Save applies, so the app keeps exactly one destructive path. Honesty stated in
  the dialog: text-layer only, **image-only pages are detected and named**, form-field values are
  out of reach, and a box's width hints the removed string's length. — *Windows (headless +
  offscreen GUI, incl. the cross-engine Poppler leak check)* — 23 new tests, 856 green
- [ ] ~~**M65** Verify + release → tag~~ — **skipped by owner call (2026-07-20)**; R4's features
  ship with the next published release. Work continues at **R5 (M66)**.

**R5 — "Foreign Annotations & Form Fields"**

- [x] **M66** ⭐ Foreign-annot infra + delete — `model/foreign_annots.py`, the shared machinery M67
  and M68 consume. **Identity is the hard part**: an annotation's `xref` is renumbered by
  `insert_pdf`, so a descriptor holding one would target the wrong annotation at materialise;
  identity is instead the `/NM` name when the writing tool set one, else a hash of type + rect
  (rounded — a PDF float round-trip is not bit-exact) + contents, with identical twins resolved
  **positionally within the page**. `/NM` must be read from the **object dictionary** —
  `annot.info["name"]` is always empty, a trap that silently disables the preferred path (pinned by
  a test). First verb: **delete**, a `ForeignDeletion` riding the PageRef, applied to the
  materialised copy — so undo restores it, the shared source is never touched, and it works for
  **every** annotation type because it removes rather than rewrites. Fidelity is asserted on the
  surviving annotations' dictionaries **and appearance-stream bytes** (indirect references
  normalised — removing an object necessarily renumbers the rest). Viewer: hit-test + outline +
  right-click **Delete** / **Copy Comment Text**; a pending deletion is dropped from a per-*ordered-
  page* render copy, since a foreign mark lives in the page's own pixmap and no overlay can hide it.
  — *Windows (headless + offscreen GUI)* — 30 new tests, 886 green
- [x] **M67** Move foreign marks — drag any foreign annotation; a `ForeignMove` rides the PageRef
  and translates it at materialise. **The appearance stream is preserved verbatim** — a rich callout
  box moves with zero degradation because nothing re-renders it (asserted byte-for-byte). Not
  `Annot.set_rect`: on the quad-based text-markup types that **silently returns `False`** and leaves
  the rect alone, so a move built on it would fail invisibly on every highlight / underline /
  strikeout. Instead every geometry key in the annotation's dictionary (`/Rect`, `/QuadPoints`,
  `/Vertices`, `/L`, `/CL`, `/InkList`) is translated in place — all of them, or a highlight whose
  rect moved but whose quads didn't gets snapped back by any viewer that regenerates appearances.
  Deltas convert fitz's y-down to PDF's y-up. Fingerprints are **resolved once up front**, because a
  move changes the rect a hash fingerprint is derived from; deletion wins over a move for the same
  mark. Moves **combine rather than stack**, so one descriptor per mark always holds the original
  fingerprint; the viewer reports moved rects so hit-testing follows the mark you can see. — *Windows
  (headless + offscreen GUI)* — 24 new tests, 910 green
- [x] **M68** Adopt-on-edit — double-click a foreign mark of a **modeled type** (highlight ·
  underline · strikeout · ink · line · square · circle · FreeText) → it becomes an ordinary editable
  KlarPDF mark. The mechanism is **entirely M66's**: a `ForeignDeletion` of the original plus the
  parsed descriptor, one macro — so at materialise the original is stripped and ours is re-added
  author-tagged, and from then on it round-trips like a mark we drew. Parsing reuses
  `page_edits.parse_annotation` (extracted from `read_klarpdf_annotations`), so an adopted mark and a
  round-tripped one **cannot drift apart**. Unmodeled types (sticky note, stamp…) stay delete/move
  only and say so. **The degrade warning fires exactly when something would be lost** — rich text,
  a real callout, a reply thread, a dashed border, transparency on a type whose descriptor has no
  opacity field, a non-base-14 font. Getting "exactly" right was the work: `/RD` and `/CL` are
  written *routinely* by PyMuPDF itself, so naive key-presence warned on marks losing nothing, which
  is how a warning stops being read — a callout is now detected by `/IT /FreeTextCallout`, and `/RD`
  is ignored. A pending M67 move is folded into the adopted mark so it doesn't snap back. — *Windows
  (headless + offscreen GUI)* — 36 new tests, 946 green
- [x] **M69** Form-field creation — **Tools ▸ Add Form Field ▸ Text · Checkbox · Dropdown**: compose
  in a small properties dialog (type · name · default · choices), then drag the box with M62's
  placement gesture. `model/form_fields.py`'s `NewField` rides the PageRef and materialises via
  `page.add_widget`. **The output is not a KlarPDF construct** — it is an ordinary AcroForm field, so
  inline filling, lossless value save, edits-aware print and flatten all work on it *by construction*
  (each asserted by running the existing path over a created field, not new code). Creation runs
  **before** the fill pass, so a value typed into a field made in the same session persists; and
  `read_form_fields` reports placed-but-unsaved fields, so the form overlay tints one the moment it
  is drawn. The dialog requires a name (AcroForm keys values by name) and *warns without blocking*
  on a collision. **Radio groups stay rejected** (owner, 2026-07-18) — pinned by a test. — *Windows
  (headless + offscreen GUI)* — 29 new tests, 975 green
- [x] **M69.1** R4 stamp polish — four owner-reported items from the M61–M69 test pass, all in the
  stamp surface. **(1) A rotated mark baked as its own mirror image**: `show_pdf_page`'s `rotate` is
  clockwise-positive while `Stamp.angle`, the dialog spinner and the viewer preview are all
  counter-clockwise, so a −45° stamp tilted one way on the page and the other in the thumbnail —
  which renders the bake, and therefore the saved file. Every watermark shipped so far had its
  documented "bottom-left to top-right" diagonal backwards. **(2) A stamp had no Copy/Cut/z-order on
  its right-click menu**, though it selected, moved, resized and Ctrl+C/X/V'd fine: the menu carried
  a hand-written type list that predated the R4 content marks. It now reads
  `viewer.annotations.OBJECT_TYPES`, and the Remove verb defers to `mark_noun` instead of falling
  back to "Remove annotation". **(3) Stamp lettering could only be sized by resizing the box**, which
  auto-fit turns into a fight with the padding (a 260×100pt box fits "APPROVED" at 44.5pt on *width*,
  leaving 39pt of vertical slack you can only close by making the box narrower). The dialog now has a
  **Size** field — "Fit to box" (the unchanged default) or a point size — and a pinned size makes the
  box hug the text via `content_marks.natural_size`, so a **click** places it and there is no padding
  to fight. A resize carries the pinned size along (smaller axis governs) so the hug survives.
  **(4) The composed stamp/watermark style is now sticky across sessions** through `Settings`
  prefs — text, colour, size, angle, opacity, frame. The **page range is deliberately not
  remembered**: it is the one field where a stale value is destructive.
  **(5) A pinned size now means the size that is drawn**, which the first cut of (3) did not
  deliver. `render_mark_document` built the artwork at the *rect* size, so `show_pdf_page` — which
  fits rotated artwork into its rect — silently rescaled it: **120pt at −45° baked at 40pt**, in a
  box 89pt wider than A4 that could not be centred, with the artwork sitting diagonally inside a
  rect shaped for horizontal text (the reported "resizing distorts the stamp"). Artwork size and
  rect are now separate concerns — `art_size` / `placement_size` / `art_scale` / `art_target_rect`
  — so a pinned mark is placed at its **rotated extent** and baked at scale 1, is never enlarged to
  fill a roomier rect (still shrunk rather than allowed to spill), and `size_for_page` reduces a
  size too large for the paper to the largest that fits so it can be centred. A resize re-derives
  the box from the new size instead of stretching it, so the hug is exact at every step. The
  viewer's preview reads the same `art_scale` the bake applies, so the two cannot drift.
  — *Windows (headless + offscreen GUI)* — 45 new tests, 1020 green
- [x] **M69.2** Watermark interaction + live-thumbnail fixes — two owner-reported defects, neither
  watermark-specific underneath. **(1) Text selection stopped working on a watermarked page**: a
  full-page mark was an ordinary object hit target, so a press *anywhere* grabbed it before text
  selection got a look in (the armed markup tools were unaffected, which is exactly how it presented
  — "highlight and underline work, but selecting text does not"). A mark that blankets the page is
  no longer an interaction target for move / select / marquee; it stays reachable by right-click,
  which offers **Remove watermark** and drops the object verbs that would do nothing to it.
  **(2) Thumbnails did not update**: `populate()` runs on every edit and reset *every* row to a blank
  grey placeholder, re-rendering only the rows in the viewport — so one edit emptied the sidebar and
  most rows stayed empty until scrolled to. Rows now carry their previous render (a structural edit
  carries nothing, since row N is then a different page). Compounding it, a range apply called
  `_note_edit_on` per page, scrolling the view — and the sidebar — to the **last** page of the range;
  it now follows the first. — *Windows (headless + offscreen GUI)* — 11 new tests, 1031 green (1029 after the M69.3 merge folded two dialogs into one)
- [x] **M69.3** ⭐ Stamp and watermark merged into one feature — owner call: *"given the similarity
  I am not seeing much value offering them as two separate features"*. They were never two: the model
  has only `Stamp` / `ImageStamp` and a watermark is one with `under=True`. Of the seven axes on
  which the two dialogs differed, **six were defaults** and exactly one was structural — **how the
  mark is placed**. So `ui/mark_dialog.py` replaces both dialogs with one carrying a **Place**
  control ("Where I drag it" / "Over the whole page") that rewrites the style fields visibly and
  hides Size + Frame for a page-covering mark; `Tools ▸ Stamp / Watermark…` replaces the two menu
  entries. Presets became **one list of words** (`MARK_PRESETS`) prefilling text + colour only —
  ending the collision where "Draft" and "Confidential" sat in *both* lists meaning different
  things. Rationale and the Way-2 argument in `PLAN.md` §R4. Done before R4's first release, while
  it was still free. — *Windows (headless + offscreen GUI)* — 1029 green
- [x] **M69.4** Large-document mark performance — owner-reported: watermarking all pages of a
  **320-page** document left the app sluggish *afterwards*, not just during. Measured on
  `spaceX_prospectus.pdf`: the apply took **10.6s**, and **every subsequent edit cost 10.7s**. Two
  causes, both O(document) where they should be O(what's on screen). **(1) The overlay rasterised
  every content mark in the document on every repaint** — 320 marks to display about two. Content
  marks are the one overlay built by rendering a real PDF rather than from cheap Qt items, and the
  page pixmaps had been lazy since M25 while the overlay never caught up; they now share the view's
  prefetch band (`content_band`) and paint incrementally as pages scroll in (incrementally, not by
  `repaint`, which would drop the object selection on every scroll tick). **(2) The auto-fit search
  was uncached** — `_fit_fontsize` runs a 14-step binary search, each step opening a throwaway PDF
  and embedding a font, so one repaint ran ~4,500 of them to compute the *same* answer 320 times.
  Memoised on its scalar inputs, which fully determine the result. **Apply 10.6s → 0.93s; a later
  edit 10.7s → 0.89s; `view.reload()` 8.8s → 0.16s.** The remaining per-edit cost is the thumbnail
  panel's whole-document `render_output` bake (0.69s of the 0.89s) — see Open follow-ups.
  — *Windows (headless + offscreen GUI)* — 5 new tests, 1034 green
- [x] **M69.5** Whole-page marks: visible by default, and they stop moving the reader — two more
  owner-reported items. **(1) "Behind the page content" produced nothing.** Reported as *"does not
  update the thumbnails, does not save with the document"* — but the mark **was** saved: on
  `spaceX_prospectus.pdf` the watermark's text is in the saved page's text layer and is simply
  **invisible**, because `under=True` puts it beneath *everything the page draws* and most real
  PDFs paint an opaque full-page background. The on-screen preview hid the problem by compositing
  with **multiply on top**, which shows regardless — so the preview and the file genuinely disagreed
  (the M62 code comment claimed they were equivalent; that only holds for a page with a transparent
  background). Whole-page marks now default to **over** the content, which at watermark opacity is
  what a watermark should look like anyway — visible, with the page's own text fully legible
  through it. `under` is unchanged as a capability and still offered, with a tooltip that says when
  it will disappear. Making `under` itself honest is in Open follow-ups. **(2) The current page
  jumped** to the first or last page when marking the whole document. `_note_edit_on` exists to
  follow a mark to the page it landed on; a **range** mark did not land anywhere in particular, so
  it is no longer called for one — marking every page changes nothing about where the reader is.
  — *Windows (headless + offscreen GUI)* — 4 new tests, 1038 green
- [x] **M69.6** "Behind the page content" removed from the UI — owner call: *"I don't see any value
  for under given that we have opacity control in place already."* M69.5 had defaulted it off and
  warned in a tooltip; this drops the control. `Stamp.under` stays an **engine** capability (the M61
  "one engine, over or under" design and its tests are untouched) — the UI simply stops offering a
  control whose ordinary outcome is *nothing appears*, which is worse than dead chrome. The
  previously-recorded fix (bake `under` as an over-content `/BM /Multiply` draw so the file matches
  the multiply-composited preview) was **rejected**: it would not restore the one thing true
  under-print uniquely gives — page images *covering* the mark — and it means hand-built
  `/ExtGState` PDF code in the **save path**, adding exactly the cross-renderer variability that
  §M61's "no cross-renderer calibration" owner call exists to avoid. A pre-M69.6 settings file
  carrying `"under": true` is ignored rather than resurrecting the mode. — *Windows (headless +
  offscreen GUI)* — 1038 green
- [x] **M69.7** Two use cases, two controls — owner call: *"There are basically two use cases…
  so we don't need the third option of dragging to stamp."* Dragging a rectangle was only ever a way
  of **sizing** a text stamp; once a point size is on the dialog it is a second answer to a question
  already answered, and the worse one — a dragged box sets the size only indirectly, through the
  padding auto-fit leaves (which is what M69.1 was reported about). `Kind` is now *Stamp (click to
  place)* or *Watermark (whole page)*; the size field drops its "Fit to box" position and defaults to
  36pt; a stamp is centred on the **press point**, not the middle of a stray drag, and draws no
  rubber band to advertise a box it will not take. **Signature/image placement and M69 field
  creation keep the drag** — neither has a font size, so the box is genuinely how you size them, and
  `fontsize=0` stays the engine's auto-fit sentinel under them. — *Windows (headless + offscreen
  GUI)* — 5 new tests, 1042 green
- [x] **M69.8** Angle slider — owner request. The angle keeps its spin box and gains a slider
  beside it: two views of **one** value (the slider to find a tilt by eye, the spin box to say one
  exactly), synced without either driving the other in a loop. Ticks at the quarter turns plus a 3°
  snap to them, because 0° and ±45° are most of the angles anyone wants and exactly the ones a free
  drag over a −180…180 range is least likely to land on; the snap is short enough that a deliberate
  38° still sticks. Degrees are whole now, so the two views cannot disagree. — *Windows (headless +
  offscreen GUI)* — 5 new tests, 1047 green
- [x] **M69.9** Angle sign corrected + one shape for every numeric row — two owner items.
  **(1) The angle sign was backwards.** `Stamp.angle` was clockwise-positive — `-45` gave the
  north-east diagonal — while the field's own docstring said counter-clockwise and the watermark
  default was written `-45` with the comment "bottom-left to top-right". The owner asked the obvious
  question (*"shouldn't north-east be +45?"*); it should, so the **descriptor** was corrected rather
  than the docs bent to fit it. `+45` is now north-east, `-45` south-east. That cancelled the
  negation `apply_content_marks` had carried since M69.1 and added one in the preview (Qt's
  `setRotation` is clockwise-positive in a y-down scene). Free to fix: R4 has never shipped.
  **(2) Angle and Opacity are one shape.** They had drifted into two layouts — a slider stacked over
  a read-only label, and a slider beside an arrow-spinner — which is what made the dialog read as
  cluttered. Both are now `_SliderField`: a slider plus a typable, **spinner-free** value box, built
  once so the rows are identical by construction rather than by care. The box is a spin box with its
  buttons hidden, not a line edit, which keeps range clamping and number parsing for free.
  — *Windows (headless + offscreen GUI)* — 1047 green
- [x] **M69.10** Mark dialog geometry warning — owner-reported: switching Kind logged
  `QWindowsWindow::setGeometry: Unable to set geometry … Resulting geometry: …` on every switch. Qt
  was promising Windows a minimum **48px shorter** than the layout then needed. Cause: the wrapped
  bake note under the form. A word-wrapped `QLabel`'s height is a function of its **width**, which
  `minimumSizeHint` does not consult — so a dialog narrow enough for the note to re-wrap taller
  advertised a minimum it could not actually live in, and showing/hiding the Size + Frame rows made
  Qt ask for a geometry the platform then had to override. Fixed by pinning the note's minimum
  height to what it needs at the dialog's narrowest allowed width, giving the dialog that width
  floor, and resizing deliberately on a Kind switch instead of leaving the window manager to infer
  it. Cosmetic (a console warning, nothing misrendered) but it was the layout genuinely fighting
  itself. Nothing to do with the document being edited — dialog geometry only.
  — *Windows (headless + offscreen GUI)* — 3 new tests, 1050 green
- [x] **M69.11** Crash: picking a recent signature from the dropdown — owner-reported. The handler
  called `_rebuild_signature_menu`, which does `menu.clear()` and therefore **destroys the submenu's
  `QAction` objects — including the one whose `triggered` signal was still being delivered**. That is
  undefined behaviour: a hard crash on Windows, surfacing under PySide as *"Internal C++ object
  (QAction) already deleted"*. `Clear List` carried the identical hazard, being an action in the menu
  its own handler empties. The rebuild is now deferred by a zero-delay timer so the signal unwinds
  before anything is deleted. Reproduced first (triggering the *oldest* entry, which reorders the
  list and so forces a real rebuild), and both regression tests fail on the pre-fix code with that
  exact RuntimeError. — *Windows (headless + offscreen GUI)* — 3 new tests, 1053 green
- [x] **M69.12** Signatures made dragging other objects lag — owner-reported, worse with each
  signature added, *"even if added to other pages"*. A content mark is the one overlay built by
  **rendering a real PDF**, and it was re-rasterised on **every repaint** — and a drag repaints. So
  the cost of dragging anything scaled with how many signatures were in view, which had nothing to
  do with what was being dragged. Measured: **~98ms per transparent signature per repaint**, linear
  (4 signatures = 392ms *per repaint*). Two fixes. **(1) The rasterised artwork is cached**, keyed
  on `(mark, on-screen width)`; the descriptors are frozen dataclasses, so a moved or restyled mark
  is a different key and can never be served the stale image of its previous self — no explicit
  invalidation. Bounded LRU, so a document of distinct marks costs memory like one. **(2)
  `_drop_white`'s alpha intersection** ran a Python `zip`/`min` **per pixel** whenever the image
  *already had* transparency — the exact "full transparency" case — making a transparent PNG
  **4.6× slower** than an opaque one (110ms vs 24ms) in a module whose docstring warns that a Python
  per-pixel loop "would stall the UI for seconds". Now `map(min, …)`, which runs the loop inside
  CPython (~1.6×); MuPDF has no per-pixel alpha-intersect and numpy is not a dependency, so this
  stays the floor — the cache is what makes it stop mattering. **Repaint after the first: 98ms per
  signature → 0.0–0.7ms regardless of count.** — *Windows (headless + offscreen GUI)* — 3 new tests,
  1056 green
- [x] **M69.13** Signature removal slider ran backwards — owner-reported: *"transparency increases
  if I keep the slider towards zero and decreases as I drag it right."* It did. The slider exposed
  `ImageStamp.white_threshold` **raw**, and a threshold is a luminance *cutoff*, so lowering it
  removes more: measured on a grey ramp, far-left removed **129/256** pixels and far-right **4/256**.
  It also had **no label and no tooltip** — a bare slider under a checkbox, which reads as "how
  much", the one thing it was not. Now labelled **"Remove"** and inverted, so right removes more;
  the mapping to the cutoff (`(100 - strength) / 100`) lives at the dialog edge so
  `ImageStamp` keeps the plain threshold semantics the renderer wants. The default is unchanged —
  strength 15 is the old 0.85 — and the reach is the same (1–50 spans the old 0.99–0.50), so this
  inverts the control without quietly re-tuning it. — *Windows (headless + offscreen GUI)* — 2 new
  tests, 1058 green
- [x] **M69.14** A created form field is an ordinary object — owner-reported: fields could not be
  moved, even before saving. The **model had always been ready**: `PLACEABLE_TYPES` lists `NewField`,
  `translate_mark` and `scale_mark` both handle it, and its `bounding_rect` docstring says it exists
  *"so the viewer's shared hit-test / outline helpers work on it unchanged"*. But the viewer's
  `OBJECT_TYPES` tuple was never told, so a field was invisible to select / move / resize / marquee —
  the **third** time that hand-maintained list has gone stale behind a new descriptor (stamps at
  M69.1, watermarks at M69.2). A field is drawn by the *form* overlay rather than the annotation
  overlay, which is what let it go unnoticed. `mark_noun` also gained "form field", so the menu no
  longer offers "Remove newfield". **Note the deliberate mode split**, now pinned by a test: in
  **Objects** mode a click moves a field; in **Select** mode the form overlay claims it so you can
  type into a field you just created (M69's feature). — *Windows (headless + offscreen GUI)* —
  6 new tests, 1064 green
- [x] **M69.15** A freshly placed mark is selected — owner-reported: a form field could not be
  selected right after creating it (workaround: switch mode and marquee around it). **Placement
  committed with nothing selected**, so the next click went to the *form* overlay to be filled —
  which, to someone who had just drawn the box, looked like the field could not be selected at all.
  Paste has selected-after-add since M59.7 for exactly this reason (*"the add reloads the view,
  which clears any selection"*); placement never did. Now it does, for fields and content marks
  alike. Second half: a press on an **already-selected** object now leads the Select-mode priority
  list, so a field can be dragged without switching to Objects mode — gated on *selected*, so a
  click on an unselected field still means "type into it" (M69's in-session fill, pinned by a test).
  **A note on the diagnosis**: the first attempt looked like it did not work, because the test drove
  `finish_draw()` directly — the view disarms on mouse *release*, so the tool stayed armed and ate
  the next press. The owner re-testing is what caught that; the tests now go through real press /
  move / release events. — *Windows (headless + offscreen GUI)* — 3 new tests, 1067 green
- [x] **M69.16** A created field grabs like a text box — owner-reported: *"if I am in text select
  mode, clicking over it takes me into text entry mode… I have to click precisely on the edge, which
  is hit and a miss most of the times."* M69.15 had gated the Select-mode grab on the field being
  *already selected*, which left no way to select it by clicking in the first place — so the border
  was the only handle. A field **you created this session** now follows the contract a text box has
  had since M20: **press to move, double-click to type into it**. A **document's own** form fields
  are untouched — single-click still fills them, which is what filling in a form requires. The
  distinction is who owns the thing under the cursor, and it lives in one predicate
  (`PdfView._grabs_before_form`). — *Windows (headless + offscreen GUI)* — 2 new tests, 1068 green
- [x] **M70** Verify + release → tag **v0.15.0** (version bump + docs; 1068 headless tests green on
  the merged main; audit green; CI draft → published) — *Windows* —
  [release](https://github.com/utyagi24/klarpdf/releases/tag/v0.15.0)

**R6 — "Simplify & Read"** (shipped v0.16.1)

- [x] **M71** Two-tier toolbar — the single ~29-slot bar is now two tiers, the R6 budget revision
  made real: at rest the app shows only the **reading bar** (Sidebar · Save · Undo/Redo · the zoom
  cluster unchanged · Rotate L/R · a **Markup** toggle · Find); the **markup bar** the toggle
  summons carries the whole kit (Select/Grab/Objects · Text Box · Markup ▾ · Draw ▾ · style swatch ·
  Stamp ▾ · Redact ×2 until M72 merges them). Open/Print and the page-op buttons left the toolbar —
  the File/Edit menus and the M46 context menus carry every removed verb, with **no Pages-panel
  action strip** (owner call); whether Open returns beside Save is the one-line call at the M71
  review. Visibility is remembered app-wide exactly like the sidebar — only an *explicit* toggle
  persists. The arm/visibility interplay is honest both ways: arming a kit tool from the Tools menu
  **summons** the hidden bar (the armed state must be visible on the lit button), and dismissing the
  bar **disarms** a kit tool (an invisible armed state is a trap) while leaving the menu-only CROP
  arm and the Select/Grab/Objects base mode alone (Grab is a reading tool). View ▸ Markup Toolbar
  (Ctrl+Shift+M); new `markup` icon. — *Windows (headless + offscreen GUI)* — 12 new tests, 1082
  green ([#159](https://github.com/utyagi24/klarpdf/pull/159))
- [x] **M72** One Redact tool — the markup bar's two Redact slots are **one armed tool** with
  Preview-style gesture detect: `ArmedTool.REDACT` resolves **at press** on the existing text-hit
  path (`TextSelection.has_word_at`, exact containment — a margin press must mean *block*, never
  nearest-snap) — press-on-word → the text-flow redaction, press-elsewhere → the rubber-band block,
  and the resolution swaps in the concrete tool so the armed tint / release / one-shot disarm are
  exactly the explicit tools'. A press off any page stays armed **and unresolved**; a no-commit
  click restores the combined arm (the resolved gesture can't lock in); a rotated view resolves to
  block (text selection is disabled there). The slot's button lights for the whole redact family
  and a click on the lit button always disarms; a live selection applies immediately (the M46
  contract). Tools ▸ Redact Text (Ctrl+Shift+R) / Redact Block were menu verbs at M72; **revised
  2026-07-24 (PR #189)** to a single Tools-menu Redact (now carrying Ctrl+Shift+R) + Find and Redact,
  matching the bar — the concrete text/block tools stay, gesture-resolved (see PLAN §GUI roadmap
  M72). — *Windows (headless + offscreen GUI)* — 14 new tests, 1096 green
  ([#160](https://github.com/utyagi24/klarpdf/pull/160))
- [x] **M73** Sticky markup arming — Highlight / Underline / Strike Out / Pen **stay armed across
  gestures** (Preview's repeat-use behaviour): a new `ArmedTool.sticky` property names the quartet,
  and the view's two release paths (drag-over-text apply, draw commit) keep the arm instead of
  one-shot disarming — passage after passage, stroke after stroke, on one arm. Three exits (owner),
  all riding pre-existing paths: the lit button again (`_arm_tool`'s toggle) · Esc · arming any
  other tool; mode switches and dismissing the markup bar (M71) exit too. Placement and destructive
  tools stay **one-shot** — including the M72 combined slot's resolved text gesture — because a
  stuck destructive mode is a trap. The armed state stays visible throughout (lit button + M71's
  summon-on-arm), and the quartet's tooltips say so. — *Windows (headless + offscreen GUI)* — 13
  new tests, 1109 green ([#161](https://github.com/utyagi24/klarpdf/pull/161))
- [x] **M74** ⭐ Arrow ends as style — Preview treats arrowheads as *line style*, and it is right:
  `MarkupStyle.line_ends` joins the M59.5 picker as an **Arrowheads** submenu (None · Start · End ·
  **Both**), lines-only by the applicability-follows-the-model rule; **Arrow leaves Draw ▾**
  (`ArmedTool.ARROW` removed, four tools remain, the Tools menu drops the entry) — one Line tool
  draws every variant, and **Both is new capability**. WYSIWYG holds: the live preview draws the
  style's heads from the first drag pixel (`_line_path` shared with the overlay); `restyle_mark`
  takes `line_ends` (`None` = keep) so a selected line's ends change in place like colour — one
  undo step — and `from_mark` loads a selected line's ends into the picker. **Zero file-format
  change**: materialise already wrote `set_line_ends` per boolean and the parser read both back
  (M57's model was built for this), so pre-R6 arrows reopen editable + unchanged (pinned) and a
  both-ended line bakes as an `/LE` OpenArrow pair (asserted on the saved file). — *Windows
  (headless + offscreen GUI)* — 12 new tests, 1121 green
  ([#162](https://github.com/utyagi24/klarpdf/pull/162))
- [x] **M75** Find bar match options — **Match case** + **Whole words** on the interactive FindBar:
  M64's existing `SearchController.search` filters, surfaced at last, with the M64 dialog's labels
  and both off by default (exactly the pre-M75 behaviour). A toggle **re-runs the live query in
  place** — highlights, the "N of M" label and a visible List All panel follow without retyping;
  next/prev/goto operate on the filtered set by construction; the bar's kept-query revive inherits
  the kept toggles; the Find-and-Redact dialog keeps its own independent checkboxes (pinned). —
  *Windows (headless + offscreen GUI)* — 7 new tests, 1128 green
  ([#163](https://github.com/utyagi24/klarpdf/pull/163))
- [x] **M76** Markup context menu — right-click on marked text offers Preview's change set, scoped
  to the clicked mark's words: the curated **highlight colours** (recolour in place through the
  M59.10 merge — trim/absorb, never stacking — or lay a highlight under a clicked underline/
  strikeout) + **No Highlight** (Preview's slashed swatch) + **Underline** / **Strike Out**
  toggles (add in the sticky Markup ▾ line colour, or remove). Removal is the merge's new inverse
  — `remove_markup` trims covered same-type marks by exactly the span (a middle cut splits, full
  coverage drops), so a wider underline keeps its tail beyond the clicked words; `marks_over` is
  the shared tick-state query. One `SetAnnotationsCommand` per action = **one undo step**;
  identical repaints and no-layer removals are no-ops. "Remove <noun>" still closes the menu. —
  *Windows (headless + offscreen GUI)* — 10 new tests, 1138 green
  ([#164](https://github.com/utyagi24/klarpdf/pull/164))
- [x] **M76.1** Markup context menu reshaped to **Preview's swatch rows** (owner feedback from the
  M78 test pass: the M76 layout offered *two* removal wordings at once — "No Highlight" beside
  "Remove highlight"). Now three sections — Highlight · Underline · Strike Out — each a header over
  one **horizontal row of colour dots** (`SwatchRowAction`) ending in the standard **slashed
  no-colour dot**: a colour recolours/lays the layer through the merge, the slashed dot removes it
  (verb on hover — the owner asked for better than the word "None", and a glyph with "Remove
  highlight" as its tooltip beats any label), a ring marks each layer's current state (radio
  semantics). The rows are the complete change set, so the trailing "Remove <noun>" entry is gone —
  **exactly one removal path per layer** — and the line layers gain direct colour choice their
  toggles never had. Offscreen render inspected (layout/rings/slash correct; header tofu is the
  Windows offscreen font stack, confirmed by a control grab). — *Windows (headless + offscreen
  GUI)* — 12 tests rewritten incl. the one-path regression, 1162 green
  ([#167](https://github.com/utyagi24/klarpdf/pull/167))
- [x] **M76.2** HUS colours **always visible** in the Markup ▾ menu + the armed-highlight preview
  colour (two owner reports from the same test pass). (1) Picking a colour then arming was *two*
  menu trips (colours hid in submenus): the `SwatchRowAction` gains a state-setting mode
  (`include_remove=False, close_on_pick=False`) and the dropdown now carries the highlight + line
  colours as **always-visible dot rows that don't close the menu on a pick** — one menu visit, and
  no click at all when the ring already sits on your colour (`set_active` moves it in place); the
  old `_add_color_submenu` is gone. (2) An armed Highlight previewed a fixed yellow that only
  "converted" on release — it now reads the sticky colour the window keeps on the view
  (`PdfView.highlight_preview_color`, seeded at init + synced on change), so the chosen colour shows
  from the first pixel of drag (underline/strikeout keep the selection blue by design). — *Windows
  (headless + offscreen GUI)* — tests updated (menu rows + wired-preview case), 1162 green
  ([#168](https://github.com/utyagi24/klarpdf/pull/168))
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
- [x] **M78.1** View-mode navigation fixes (three owner reports from the R6 test pass). (1) **A
  slideshow step is a row, not a page index**: in Two-Page view pages 1|2 share a row, so
  `goto_page(current + 1)` scrolled to the offset already on screen and `_update_current` snapped
  back to the row's first page — click, Right and Down looked dead while the backward keys (which
  land on the previous row) worked. `PdfView.step_slide` now moves `_layout_rows()` entries and
  keeps the projected row (`_slide_row`) as the mode's own position instead of re-deriving it from
  the scroll offset; Home/End jump to the ends. (2) **The wheel steps whole slides too** — free
  scrolling a one-page-per-screen mode could come to rest straddling two pages, and from a straddle
  the page under the viewport centre isn't the page being read, so the next click stepped from the
  wrong one (the reported "jumps and comes back / click twice"); one detent = one slide, hi-res
  deltas accumulated to a detent, and `step_slide` pins `set_current_page` to the row it projected
  so a clamped scroll near the end can't drift it. (3) **A coasting wheel can no longer undo a
  click** — the reported "clicked eight times and the first slide never moved, worse each time I
  flick to the end and back". A flywheel wheel keeps emitting for seconds after the hand leaves it,
  and those events walked the deck back under the reader; a click/key step now parks the wheel
  until it has actually gone quiet (250 ms, `event.timestamp()`, falling back to our own clock so
  an unstamped platform fails *open*). Reproduced end-to-end on the owner's brochure — click → page
  2 → coast → page 1 before, holds at page 2 after. A step onto the row already showing costs
  nothing now, so a burst piling into either end of the deck renders nothing at all. (4) A
  **double-click** steps a slide: impatient clicking makes every second press a double-click, which
  the press handler never saw. (5) **F5 during a slideshow is a no-op** instead of a leave-and-
  re-enter blink, and from Full Screen it switches the projection on **in place**. (6) **F11 exits
  Full Screen**, not just Esc: a menu action's shortcut is live only while its menu bar is
  *visible*, which the mode hides — the window now carries the Full Screen + Slideshow actions
  itself, and F11 toggles chrome-free reading whichever mode is up (during a slideshow it leaves
  that too). — *Windows (headless + offscreen GUI)* — 9 new tests (each verified red without the
  fix), 1187 green ([#170](https://github.com/utyagi24/klarpdf/pull/170))
- [x] **M75.1** Find bar: **Whole words** decides *what the query is*, and the hit verbs go dead
  without hits (two owner reports from the R6 test pass). (1) The toggle was only M64's
  boundary filter — a multi-word query was a phrase either way, so ticking it on "electric heater"
  changed nothing visible. It now also splits the query: **off**, the query is a list of words and
  any of them matches on its own (every *electric*, every *heater*, still inside longer words);
  **on**, it is one unit — the phrase, and only as whole words. A single-word query is untouched in
  both states, so nothing M75 shipped changes meaning. `search()` runs `search_for` per term and
  re-orders a multi-term page into reading order (next/prev must walk the page as it is read), with
  duplicate boxes collapsed and the case filter comparing against the term that found the hit. The
  Find-and-Redact dialog drives the same controller, so its identically-labelled toggle gains the
  same meaning. (2) **Previous / Next / List All are disabled while the search has no hits** —
  dead verbs, not clickable no-ops (the M77 rule) — and the results panel goes with them, an empty
  band saying nothing the "No results" label doesn't; it returns still listing when the query
  matches again. — *Windows (headless + offscreen GUI)* — 4 new tests, 1191 green
  ([#171](https://github.com/utyagi24/klarpdf/pull/171))
- [x] **M63.1** Signature transparency is **remembered per image** (owner report: "Signature/Image
  does not remember the transparency setting last used"). How much paper to drop out of a scan is a
  property of that scan, not of the day, so the settings ride beside the recent list keyed by path
  (`Settings.signature_settings`) — *beside*, never inside it, so "paths, never pixels" still
  describes the list itself, and the tuning is pruned with its entry (a file deleted to revoke a
  signature leaves nothing behind). Choosing a known image in the dialog restores its checkbox +
  slider; an image with no memory — anything just browsed to — keeps whatever is on the controls,
  which is the last-used setting, since the dialog opens on the most recent entry (so a *re-scan*
  starts tuned). The **Recent Signatures menu** gains the most from it: with no dialog it had
  nowhere to re-tick "make white background transparent", so a photo signature came back with its
  paper on — it now places exactly as last time, which is what that path claims to be. — *Windows
  (headless + offscreen GUI)* — 7 new tests, 1197 green
  ([#172](https://github.com/utyagi24/klarpdf/pull/172))
- [x] **M63.2** The recent submenu is **"Recent Signatures / Images"** (owner: the old "Recent
  Signatures" assumed every insert is a signature). It is named for the command that fills it —
  Signature / Image… — because the list holds whatever that placed, seals and logos included, and a
  list named for one of its uses reads as a filter. *Not* "Recent Inserts", the alternative
  considered: **Insert already means pages** in this app (Edit ▸ Insert Pages from File… / Insert
  Blank Page), so it would name the one thing the list never holds. Internal names and the stored
  `recent_signatures` key are unchanged — renaming the key would silently drop every existing
  list. — *Windows (headless + offscreen GUI)* — 1 new test, 1198 green
  ([#173](https://github.com/utyagi24/klarpdf/pull/173))
- [x] **M77.1** The Annotations tab lists **text markups only** — highlights, underlines,
  strike-outs and notes (owner: it was listing drawn lines and shapes too). A markup is a
  *passage*: the row's snippet reads back what you marked, and a list of them is a reading of the
  document's margin. A pen stroke, line, shape, text box, stamp or form field is a placed
  **object** — no passage to read back (its row said "p. 3 · line"), visible where it sits, and
  arranged through the Objects mode that exists for exactly that. `is_listed` /
  `is_listed_foreign` in `organize/annotations_panel.py` are the single definition, shared with
  the tab's existence check (`_doc_has_listed_marks`), so a document of drawings alone gets **no
  tab** rather than a tab over an empty list. Foreign markups list on the same terms — including
  Squiggly (a wavy underline we cannot draw but can list) and sticky notes, which is "notes"
  arriving from another tool ahead of our own. — *Windows (headless + offscreen GUI)* — 2 new
  tests + 4 rewritten, 1199 green ([#174](https://github.com/utyagi24/klarpdf/pull/174))
- [x] **M79.1** Sidebar: **no title bar, Pages by default, the rest on demand** (three owner calls
  from the R6 test pass). (1) The dock's title strip is gone — a label reading "Sidebar" over the
  sidebar is chrome about chrome, and its ✕ was a third way to do what the toolbar button and
  View ▸ Sidebar already do, the only one leaving no lit button behind to say how to get it back
  (an empty, zero-height title widget; the window title survives for screen readers). (2) **Pages
  alone by default**: Outline and Annotations no longer mount by themselves, so the panel is the
  same shape on every document. (3) The sidebar toolbar button becomes a **split button** whose ▾
  carries a checkable entry per optional tab, remembered app-wide (`sidebar_tabs`), so the choice
  follows the reader across documents and launches. A tab shows when it is **asked for *and*
  applies** (M45/M77's rules intact), asking for one **opens a hidden sidebar** (as arming a markup
  tool summons the markup bar), and the ▾ lists only tabs this document could show — dropping the
  arrow entirely when it could show none, so a tick never produces nothing. — *Windows (headless +
  offscreen GUI)* — 9 new tests + 2 rewritten (the toggle now rides as a widget), 1208 green
  ([#175](https://github.com/utyagi24/klarpdf/pull/175))
- [x] **M79.2** The dropped ▾ really goes (owner: on a document with no outline and no marks the
  arrow was still drawn, and clicking it did nothing). M79.1 dropped the *menu*; a QToolButton draws
  the split section from its **popup mode**, so the button kept its 14 px arrow over an empty menu —
  a dead click, and exactly the greyed-out chrome the sidebar work was removing. The mode now flips
  with the menu. Making the *width* follow took a re-polish as well: neither of the two caches
  between the mode and the geometry notices `setPopupMode` (a bare property write) — QStyleSheetStyle
  holds the rule sizing `::menu-button` until the widget is re-polished, QToolButton holds its own
  sizeHint until a menu is attached or detached — so a returning arrow was drawn squeezed over the
  icon. Verified at the pixel: 31 px plain, 44 px split, across open · first mark · undo · redo. —
  *Windows (headless + offscreen GUI)* — 1 new test + 1 strengthened (it asserted `menu() is None`,
  which passed while the arrow was still painted), 1209 green
  ([#177](https://github.com/utyagi24/klarpdf/pull/177))
- [x] **M79.3** A new mark **offers** the Annotations tab; it no longer mounts one (owner: "don't
  add the Annotations tab automatically as soon as I add annotations — just the dropdown option").
  M77 had the tab track edits live *including its own existence*, so marking up a page pushed a
  panel into the sidebar mid-stroke. The existence rule is still M77's — asked for **and**
  something to list — but "asked for" is now read from *this window* rather than from the app-wide
  preference, which is exactly where it went wrong: a preference carried in from another document
  is not a request for a tab on this one. So a new mark only makes the ▾ entry offerable, while a
  window already carrying the tab keeps tracking live — deleting the last mark **through** the tab
  folds the empty panel away (owner, second report: an empty tab is the dead chrome this tranche
  removes) and undoing that deletion brings the panel back with the mark, rather than leaving the
  reader a restored annotation and no list. Putting the tab away by hand is different from it
  folding on empty: once unticked it stays away, and a later mark only offers it again. Each tick
  now mirrors **what the sidebar is showing** rather than the stored preference — the two part
  company the moment a mark makes the
  entry offerable on a document whose tab is not mounted, and a tick drawn from the preference would
  sit checked over an absent tab, one click from doing the opposite of what it says. The preference
  still decides what mounts at open, so the ask still follows the reader across documents. The ▾
  entries lost their "Tab" suffix in the same pass (owner) — each is named for the tab it produces,
  matching that tab's own label; "Tab" was our vocabulary for an entry that already sits under the
  sidebar button with a tick beside it. — *Windows (headless + offscreen GUI)* — 6 new tests +
  9 rewritten (they pinned the summon-on-mark behaviour this replaces), 1214 green
  ([#178](https://github.com/utyagi24/klarpdf/pull/178))
- [x] **M71.1** New icon for the **Markup toggle** — a page with a pen, chosen by the owner from six
  candidates rendered at real toolbar size (16/20/24 px, lit and unlit, beside their neighbours).
  The old pencil-in-a-circle failed twice over: the circle dominated at 20 px so the pencil inside
  read as a blob, and a bare pencil **is the Pen tool's icon**, two slots away on the bar this
  button opens. The button summons the whole kit, so it must depict none of its tools — the page is
  what makes it "mark up this document" rather than one more instrument, and it also rules out the
  obvious chisel-marker glyph, which is already Highlight's. One file (`ui/icons/markup.svg`);
  the toolbar and View menu both resolve it by name and the tint follows the theme. — *Windows
  (headless + offscreen GUI)* — 1208 green ([#176](https://github.com/utyagi24/klarpdf/pull/176))
- [x] **M77** Annotations sidebar tab — a third tab beside Pages | Outline listing **every mark
  in the document** as "p. N · type · snippet" rows: ours from the PageRef descriptors (text
  markups read their covered page text as the snippet; boxes/stamps/fields their own), foreign
  through a provider seam (the overlay's live `foreign_annotations` — deletions dropped, moves
  applied), so `organize/annotations_panel.py` depends only on the model + a callable. **The tab
  exists only while the document has marks** (inapplicable chrome is invisible): `_doc_has_marks`
  short-circuits on our marks and scans foreign *presence* once per source page (sources are
  immutable in-session; cache cleared on `_reset_to_file`). Tracks edits/undo live **including its
  own existence** — the first mark summons it, undoing the last dismisses it, with remounts keeping
  the active tab by label. Click = the M47 pattern: jump + real object selection for free-placed
  marks, the outline for foreign, plain jump for text-anchored/page-wide. — *Windows (headless +
  offscreen GUI)* — 11 new tests, 1149 green
  ([#165](https://github.com/utyagi24/klarpdf/pull/165))
- [x] **M78** View modes — the reading modes Preview offers, all **view-only** (the M49 principle;
  nothing is undoable because nothing is an edit): **Full Screen** (F11, checkable — menu bar, both
  toolbars, sidebar, find bar step aside; F11/Esc restores exactly the chrome that was up, and
  programmatic hides never rewrite the remembered sidebar/markup prefs) · **Slideshow** (F5 —
  chrome-free + one page per screen at Fit Page; click / Right/Down/Space/PgDn advance, Left/Up/
  PgUp back, clamped; selection, forms, links and menus inert; Esc exits and the prior zoom/fit
  returns exactly — Esc reaches MainWindow because the view leaves it unconsumed when nothing is
  armed) · **Two-Page View** (facing pairs 1|2, 3|4 … in the ordinary window: `_build_scene` lays
  out by row, `page_and_local_at` disambiguates within a row by x, and Fit Width/Page frame the
  whole spread via `_fit_dims`; session-only, like rotation). Surfaced in the View menu + the
  bare-page right-click menu as the same QActions. — *Windows (headless + offscreen GUI)* — 11 new
  tests, 1160 green ([#166](https://github.com/utyagi24/klarpdf/pull/166))
- [x] **M78.2** Nudge objects with arrow keys — arrow-move the object selection (1 pt / Shift 10 pt, page-clamped); a held key coalesces to one undo, taps stay separate — *Windows (headless)* — 10 new tests ([#180](https://github.com/utyagi24/klarpdf/pull/180))
- [x] **M78.3** Resize text-box width — a lone box's right-edge handle reflows the text (left pinned, height auto-fits); group resize leaves text boxes unstretched; the fold survives save+reopen (`auto_width` inferred) — *Windows (headless + offscreen GUI)* — 9 new tests ([#181](https://github.com/utyagi24/klarpdf/pull/181))
- [x] **M78.4** Icon polish — new Grab (filled outline hand, separated fingers) / Text Box (T in a box) / Pen (pencil on a baseline) glyphs, chosen from rendered candidates; verified light + dark, re-tint intact — *Windows (offscreen render)* — `pen` added to the icon test roster + 3 non-blank/QtSvg-safe checks ([#182](https://github.com/utyagi24/klarpdf/pull/182))
- [x] **M78.5** Highlight/Underline/Strike arming swatches — Markup ▾ becomes three colour rows; a pick sets the verb's colour **and** arms it (marking a live selection at once, and moving the split-button face); underline vs strike colours now independent — *Windows (headless + offscreen GUI)* — 4 new/rewritten tests ([#183](https://github.com/utyagi24/klarpdf/pull/183))
- [x] **M78.6** Split the markup style button → three markup-bar buttons over one shared `MarkupStyle`: Line Styling (thickness · dash · arrowheads) · Colors (Border + Fill rows + custom + No Fill) · Opacity (a slider showing/accepting an exact %); selecting an object loads its style into all three — *Windows (headless + offscreen GUI)* — new/updated tests across 7 suites ([#184](https://github.com/utyagi24/klarpdf/pull/184))
- [x] **M78.7** Find-as-you-type stops hanging on a large document (owner report: live search on
  `spaceX_prospectus.pdf` is *"very slow and unresponsive… with match case on my app actually hanged
  and I had to kill it"*). Two independent faults, plus a correctness bug the first was hiding.
  **(1) Every hit re-scanned its whole page.** The snippet walked the page's word list three times
  per hit, and Match case read the text under each hit with `page.get_textbox`, which re-extracts
  the page's text on *every call* (~31 ms measured). A live search's first keystroke is a one-letter
  query, which on this 320-page file has **72 097 hits** — so a single keystroke cost ~4.4 s for
  snippets and **~26 minutes** for Match case. That is the hang: not a deadlock, arithmetic.
  `_PageText` now indexes a page once into per-line word and char bands, so a lookup scans one line
  instead of one page, and the char index (Match case only) is built lazily. **(2) A full-document
  scan ran per keystroke** — typing a five-letter word ran five scans, the most expensive of them
  first. The find bar now debounces (`SEARCH_DEBOUNCE_MS`, 250 ms); Enter, an option toggle and
  Ctrl+F flush or bypass the wait, and closing the bar drops a pending scan. **(3) Match case was
  also *wrong*.** `get_textbox` answers "what is under this box?" by clipping, so it swept in
  whatever else shared the box's band — with ordinary single-spaced text the line above comes too,
  and a hit reading `'Cla\nSPX'` failed `!= "SPX"` and was discarded. Reading the box by char
  centres is both the faster answer and the right one: on the same file it recovers 4 of 72 hits for
  "SpaceX", 3 of 82 for "Starlink", and 84 of 2598 for "the". Measured end to end, one full-document
  search: `"s"` + Match case **1574 s → 4.1 s**, `"space"` + Match case **16.9 s → 1.4 s** — and the
  debounce means typing that word now pays it once instead of five times. Remaining O(document) cost
  is in Open follow-ups. — *Windows (headless + offscreen GUI)* — 8 new tests, 1256 green
- [x] **M78.8** Annotations rows say what was actually highlighted, and cost one page read each
  (the follow-up M78.7 opened; owner call: fix it before the release rather than ship it). Same
  `page.get_textbox` call, same two faults, found in `AnnotationsPanel._covered_text`.
  **(1) The snippets were wrong** — and this is why it went ahead of the remaining search work:
  `get_textbox` reads a box by *clipping*, so it returns whatever else shares the box's band. Each
  bar of a text-anchored mark is exactly one word's bbox, yet on a two-column page of
  `spaceX_prospectus.pdf` **567 of 700** single-word highlights read back as something else —
  "Following" as "Following and Class B", pulled from the next column. Across 5567 real word boxes
  the clip read was exact **89.6%** of the time; the indexed read is exact on every one (the 5
  nominal misses are a leading thin space that whitespace normalisation strips anyway). The snippet
  *is* the row's value — "a highlight row reads back the passage you highlighted" — so those rows
  were simply false, silently, and looked plausible. **(2) It re-extracted the whole page per bar**,
  and `populate()` re-runs after **every edit**, so this was per-edit lag that grew as the reader
  marked up: **0.79 s at 10 highlights, 3.46 s at 50, 15.73 s at 200** → now **0.032 s / 0.064 s /
  0.150 s** (25×–105×). Marks sharing a page now share one index, held for the rebuild only — an
  index describes a page as it was, and the next rebuild is called precisely because something
  changed. **`_PageText` moved to `model/page_text.py` as `PageText`** to be shared: it is pure
  PyMuPDF text geometry with no Qt and no viewer or panel state, and the panel's own contract is to
  depend only on the model plus the provider seam, so it could not import from `viewer/`. Duplicating
  it was rejected — two copies of a routine that has already been subtly wrong once. — *Windows
  (headless + offscreen GUI)* — 5 new tests, 1261 green
- [x] **M79** Verify + release → tagged **v0.16.1** (v0.16.0 tagged but never published — see the
  Status note) — 1273 headless tests green — *Windows*

**Post-R6 (unreleased)**

- [x] **M80** **Ctrl+wheel zoom**, anchored on the pointer (owner-reported 2026-07-27: "many
  applications support Ctrl+scroll for zooming; ours does not"). It wasn't inert — a Ctrl-modified
  wheel fell through to `QAbstractScrollArea` and **scrolled**, so the reader asking for zoom got
  motion. `wheelEvent` now intercepts it and zooms with the **content under the cursor held fixed**
  (every other zoom entry point holds the viewport centre — there is no pointer behind a menu item);
  the centre anchor generalised into `_anchor_at(view_pos=None)` / `_restore_anchor(anchor, view_pos)`
  plus a `set_zoom(..., anchor_pos=…)` argument, so the existing paths are byte-for-byte unchanged in
  behaviour. The factor is **continuous** (`_ZOOM_STEP ** (delta / _WHEEL_NOTCH)`): one detent is
  exactly one Ctrl+± step, and a precision touchpad's fractional deltas zoom smoothly rather than
  being swallowed as sub-detent noise. The event is accepted even at the zoom limits, so the gesture
  can never degrade back into the scroll it replaced; the **slideshow** deliberately keeps stepping
  slides whatever the modifier (its contract is one page per screen at Fit Page — M78). View-only:
  no model, file or dependency change. — *Windows (headless + offscreen GUI)* — 6 new tests (5 of
  them verified red before the fix), full suite green
  - **Also recorded: the input-conventions audit** the same report asked for — seven further gaps,
    each *measured* against a running window rather than read off the source (`Home`/`End`/`Ctrl+Home`
    /`Ctrl+End` dead · `Space`/`Shift+Space` dead · `Shift+wheel` scrolls vertically, not
    horizontally · no momentary hand-pan · no `Ctrl+A` · `Ctrl+=` unbound because Qt's `ZoomIn` is
    `Ctrl++` · pinch-zoom unconsumed). Listed in `PLAN.md` §M80 → **now scheduled as M81** below,
    all but the hand-pan (dropped).
- [x] **M81** **Notes: the model, the round-trip, and the data loss it cures.** Two things in one
  milestone because they are the same code. (1) ⚠️ **Live data loss in v0.16.2** — adopting a
  *commented* foreign highlight silently destroyed the comment: `parse_annotation` never read
  `/Contents` for HUS marks and `degradations()` never checked it, so M68's "empty means adoption is
  lossless" contract was broken in two clicks on any Acrobat/Preview/Edge-reviewed PDF. (2) The
  **note model** the owner specified — a note attached to exactly one Highlight/Underline/Strikeout,
  stored as that annotation's `/Contents`, which is precisely what Edge and Acrobat already write.
  Adding `note` to the three dataclasses **cured the data loss outright** rather than papering over
  it with a warning, which is why it led the tranche. The note being a **field of its host** rather
  than an object is what makes the owner's rules 2 and 5 ("removing the mark removes its note", "a
  later mark over the same text neither moves nor copies it") hold with no code at all: there is no
  second object, no parent pointer and no referential integrity to keep. **Three lines of behaviour
  change, six of guard** — the cost was in finding the two places that quietly dropped text, not in
  writing them. Headless; the interface is M90. Spec in `PLAN.md` §M81.
  — *Windows (headless)* — 22 new tests (20 of them verified red before the fix), 1302 green
  - [x] **M81.1** `note: str = ""` on the HUS dataclasses; baked via `set_info(content=…)`; parsed
    back in `parse_annotation`. Verified on the pinned PyMuPDF (1.27.2.3): round-trips beside our
    `/T` tag and stays stable across save→reopen→save; an **empty note writes no `/Contents` key at
    all** (`set_info` tests the string's truth), so an unnoted highlight's bytes are exactly what
    they were before M81; and note text reaches neither `search_for` nor `get_text()`, so Find stays
    body-text-only with **no change** to the PR #190 search filter
  - [x] **M81.2** Merge preserves notes — `merge_markup` rebuilt an absorbed mark from bars+colour
    only, so a note *was* silently destroyed by highlighting adjacent text, with the user having
    deleted nothing. Owner call taken: **keep and join**, in document order, a blank line apart. The
    different-colour *trim* path one line above already preserved them (`dataclasses.replace`), and
    is now pinned by a test — it is the near-identical neighbour of the path that did not
  - [x] **M81.3** Adoption carries the comment across, and `degradations()` stops lying. The five
    kinds that can now hold a comment (HUS + FreeText, whose `/Contents` *is* its text) lose nothing
    and say nothing; the **four drawn kinds** — ink, line, square, circle — have no field for one,
    so there the loss is real and is now reported as "its comment". That is what makes *empty means
    adoption is lossless* true for the first time rather than true-for-some-types
- [x] **M82** Foreign text markup was draggable — **and it stole the press from text selection**
  (owner-reported on the Edge file). Our own HUS marks are deliberately undraggable (their quads
  describe *text*); the foreign path had no type gate. Worse, `begin_foreign_move` runs **before text
  selection** in the default SELECT mode, so **dragging across an Edge-highlighted passage dragged the
  highlight instead of selecting the text** — the reader could not select or copy the very words a
  reviewer had marked for their attention. Same symptom `covers_page()` was written to fix for
  watermarks, never generalised; this generalises it. And because a `ForeignMove` is applied at
  materialise, the displacement was becoming **permanent in the saved file** — a file-modifying
  action a reader could trigger by accident while merely trying to read. Spec in `PLAN.md` §M82.
  — *Windows (headless + offscreen GUI)* — 16 new tests (6 of them verified red before the fix),
  1318 green
  - [x] **M82.1** Gate the foreign hit-test/move on free-placed types; sticky notes, stamps and
    drawings stay draggable, delete stays available for every type. One rule, in the model:
    `TEXT_MARKUP_KINDS` + `is_free_placed()` (`model/foreign_annots.py`) — Highlight, Underline,
    StrikeOut and **Squiggly**, which rides along as text markup even though it is not adoptable.
    The viewer's `foreign_annotation_at` grew a `free_placed_only` flag that the *drag* caller
    alone passes, so delete (M66) and double-click adopt (M68) keep seeing every type. The filter
    runs **inside** the hit-test loop rather than on its result: a sticky note lying under an Edge
    highlight is still the mark the press meant, where filtering afterwards would have let the
    undraggable mark on top shield it
  - [x] **M82.2** Regression: press-drag across a foreign highlight selects the text under it,
    driven through `PdfView`'s real press/move/release handlers — the *ordering* is the bug, so a
    test that called the overlay directly would not have reproduced it. Pinned to the same words
    the identical drag selects on unmarked text, plus Ctrl+C, plus a zero-drag click no longer
    outlining the reviewer's mark
- [x] **M83** The annotations tuple is heterogeneous, and only four of five hit-tests knew it
  (owner-reported from a console traceback, filed as "expose any unknown gap"). Spec in
  `PLAN.md` §M83. — *Windows (headless + offscreen GUI)* — 23 new tests (5 of them verified red
  before the fix, reproducing the reported traceback verbatim), 1341 green
  - [x] **M83.1** `annotation_at` raised `AttributeError` on `ForeignDeletion`/`ForeignMove`, which
    carry no geometry. **Not a crash** — Qt swallows exceptions from Python overrides of its
    virtuals, so the **context menu silently never appeared**; every right-click in the page view was
    dead once a foreign annotation had been deleted, moved or adopted. Worth recording *when* it
    bites: the tuple is walked reversed, so a click that lands on a mark above the bookkeeping entry
    returns before reaching it. The dead menu is therefore worst on **bare page** — which is most of
    the page, and is what "every right-click is dead" actually described
  - [x] **M83.2** Convention replaced with a chokepoint — `rects_of()` / `is_geometric()` in
    `model/page_edits.py`. `rects_of` is **total**: it never raises, and a descriptor declaring no
    geometry yields `()`, so the hit-tests skip it by iterating zero times rather than by each site
    remembering to guard. That is the direction that fails safe, and it is duck-typed rather than a
    type list, so the *next* non-geometric descriptor is handled without anyone registering it —
    there is a test that invents one. `mark_bounds()` was rewritten as the union of `rects_of`, so
    the outline/handle geometry and the hit-test geometry now have a single source instead of two
    that happened to agree
- [x] **M84** Highlights rendered dull — **the preview alpha-blended what the file multiplies**
  (owner-reported: "our highlight color appear very dull compared to Edge, can we revisit our
  palette?"). **Investigated: the palette is fine and is unchanged.** `setAlpha(110)` washed
  colours toward the white page — measured saturation **2.3×–2.4× lower** than it should be, and
  black text under a highlight washed to olive, so the mark *reduced* legibility. Meanwhile PyMuPDF
  writes our saved highlights with `/BM /Multiply`, so **our viewer showed them duller than the file
  we had just written**. The idiom already existed in the same module (`_MultiplyPixmapItem`, whose
  docstring argues this very point) — highlights simply never got it, which is why both multiply
  items now live in **`viewer/blend.py`**: two overlays need them, and burying the principle beside
  one caller is how it went five milestones unnoticed. Spec in `PLAN.md` §M84.
  — *Windows (offscreen GUI, pixel-measured)* — 21 new tests (16 of them verified red before the
  fix), 1362 green
  - [x] **M84.1** Multiply-blend the committed highlight (a `MultiplyRectItem` sibling), at **full
    alpha** — multiply supplies the translucency, and an alpha on top of it would wash the colour a
    second time. Measured against a running window: yellow over paper goes (255, 240, 156) →
    **(255, 219, 26)**, and black text under it (110, 95, 11) → **(0, 0, 0)**, matching the
    investigation's numbers exactly. The strongest test renders the **saved PDF** with PyMuPDF at
    the same scale and compares pixel to pixel — the claim is fidelity, not taste
  - [x] **M84.2** Same for the live drag-over-text preview, or arming looks pale and the mark jumps
    vivid on release. This turned up a **third** path: the un-wired fallback colour lived in the
    armed-colour table as a fourth `QColor` at alpha 120, so it would have stayed pale while the
    wired case went vivid. The existing M76.2 test caught it; the table no longer carries a
    highlight entry at all, and the whole tool — wired or not — resolves in one place. Redact and
    the plain selection stay source-over: a selection indicator is not a mark
- [x] **M85** Current-page tracking is wrong for short pages (owner-reported on `IAS_CaseStudy.pdf`:
  "I clicked on slide 1 thumbnail and it resulted in showing both Slide 1 and 2 as selected… then as
  I made the window wider, the current slide changed to 4 and then to 5 without me clicking").
  **Reproduced headlessly**, both symptoms, on a synthetic 16:9 deck in a tall window. One root
  cause: `_update_current` picks the page under the **viewport centre**, which breaks once a page is
  shorter than half the viewport — a 16:9 slide at fit-width was 403 px tall in a 966 px viewport,
  so the centre landed 1.2 pages down. Ordinary A4 documents never reach it. Spec in `PLAN.md` §M85.
  — *Windows (headless + offscreen GUI)* — 9 new tests (8 of them verified red before the fix,
  reproducing the report's numbers verbatim), 1369 green
  - [x] **M85.1** Track the current page by **largest visible area** — the intersection of each page
    with the viewport, not a point test. Fixes both symptoms alone. Ties go to the **earlier** page,
    so a landed short page stays current over the equally-visible one below it and a facing spread
    resolves to its left-hand page; the tie is held by a 1 px² epsilon, without which the two areas
    (computed from different scene coordinates) can differ by an ulp and hand the later page a
    random win
  - [x] **M85.2** Keep the thumbnail's current row and selection in step when the *view* drives the
    change — but leave **multi-row** selections alone, so a page-op selection survives scrolling.
    The mechanism is the selection flag: under `ExtendedSelection` the plain `setCurrentRow()`
    applies `SelectCurrent`, so a lone click left the clicked row wearing the 2 px selection border
    while the view-driven current row took the 3 px ring — the two marked thumbnails the report
    described, grabbed offscreen before and after. A single-row selection now moves with the current
    row (`ClearAndSelect`); a multi-row or empty one is left exactly as the reader left it
    (`NoUpdate`, which also stops a scroll from quietly *adding* rows to a staged selection)
- [x] **M86** The two cheap zoom fixes (out of profiling M80). M80 did not make a zoom step slower —
  it made steps arrive **10–60× more often**, exposing costs that were always there. These two were
  meant to ride M80's PR; **#197 merged without them**, so `main` carried the un-coalesced wheel
  until now. Spec in `PLAN.md` §M86. — *Windows (offscreen GUI, measured before/after on a 60-page
  document at 1200×900)* — 12 new tests (7 of them verified red against a build with both mechanisms
  neutralised), 1381 green
  - [x] **M86.1** Collapse the 3 redundant `_render_visible()` passes per zoom to 1 (**A**), via a
    `_hold_render()` block that defers rasterising until the whole geometry change has landed.
    Pre-existing waste, so it is applied at every geometry change, not just zoom — **measured 3→1
    per zoom, 2→1 for fit / rotate / two-page toggle / reload / reopen / resize**, nested blocks
    collapsing into the outermost (a layout switch rebuilds, then re-fits, which zooms).
    **The spec's cost claim was wrong in both directions, and the second measurement is the one
    that matters.** The extra passes are *not* "two-thirds of the most expensive work": they never
    rasterise anything. Across five regimes the **cache-miss count is identical** with and without
    the fix (165/165, 160/160, 126/126, 165/165, 367/367), because all three passes of one
    `set_zoom` run at the *same* zoom value — the first populates the cache, the rest hit it. That
    holds even at 8 s of rasterising per sweep, so cache pressure never converts them into real
    work. But the first correction then **under-sold it by measuring a 60-page document**:
    `_render_visible` walks **every page in the document** twice (`_visible_range`, then the
    drop-offscreen loop), so one pass costs **O(document length)** no matter how few pages are on
    screen. Measured over a 40-step zoom sweep, median of 5:

    | document | rasterising before → after | per geometry change |
    | --- | --- | --- |
    | 60 pages, moderate zoom | 718 → 679 ms (−5%) | 1.0 ms |
    | 60 pages, high zoom | 8004 → 7997 ms (−0.1%) | 0.2 ms |
    | 60 pages, A1-size | 1649 → 1632 ms (−1%) | 0.4 ms |
    | **320 pages, moderate zoom** | **1550 → 933 ms (−40%)** | **15.4 ms** |
    | **320 pages, zoomed out** | **1284 → 683 ms (−47%)** | **15.0 ms** |

    So the win **scales with page count, not pixel work** — ~15 ms per zoom step, about one frame,
    on exactly the long documents where zoom already feels worst, and nothing measurable on short
    ones. Wall time on the 320-page sweep: 2186 → 1552 ms (−29%)
  - [x] **M86.2** Coalesce the wheel gesture — accumulate deltas, apply once per frame (**B**). A
    **throttle, not a debounce**: the timer is started by the first event of a frame and left to
    run, because restarting it per event would hold the zoom back for as long as the gesture
    continued and a sustained touchpad zoom would show nothing until the fingers stopped. Exact
    rather than approximate — the factor is `_ZOOM_STEP ** (delta / _WHEEL_NOTCH)`, so multiplying
    the per-event factors and exponentiating the summed delta are the same number. Measured on a
    40-delta touchpad gesture: **75 → 9 render passes and 289 ms → 103 ms of rasterising** when the
    events are paced 6 ms apart (75 → 1 pass, 331 ms → 0.2 ms when they arrive inside one frame,
    which is the ceiling rather than the everyday figure). A 10-detent notched flick paced 25 ms
    apart coalesces little **by design** — events arriving slower than a frame have nothing to
    merge — and still drops 21 → 7 passes, which is M86.1 doing the work
- [ ] **M87** Render-resource discipline — what the app *keeps*. **Sized against post-M88 numbers**,
  since the DPI correction makes every page ~5.4× heavier. Spec in `PLAN.md` §M87.
  **Premise check done 2026-07-28 before building** (numbers + method in `PLAN.md` §M87). Three of
  the milestone's four assumptions held, one was wrong, and one reorders the work:
  **M87.2 goes first — it is a live defect, not preparation for M88.** One Ctrl+wheel sweep to max
  zoom on an ordinary 60-page Letter document takes the process from **127 MB to 4431 MB** of
  working set on `main` today, with the cache pinned at exactly 48 entries the whole way. Separately,
  every byte figure in the plan was **~27–33% low**: `QPixmap` is **32 bpp**, not the 24 bpp the
  tables assumed (the ~5.4× ratio is geometric and unaffected). Also ruled out, so it stays ruled
  out: **there is no leak on close** — `closeEvent` never clears `_cache`, but destroying the view
  releases it all (2124 MB → 90.6 MB)
  - [x] **M87.1** Adaptive prefetch (**F**) — `_PREFETCH = 2` was a fixed constant, fine at
    1.85 MB/page and harmful at 264 MB/page. **Premise measured and understated**: at zoom ≥ 2 the
    visible band is 2 pages and the render band 6, so **67% of rendered bytes are prefetch** —
    237 MB visible against **473 MB prefetched** at 8×, and 57% waste even at 1.0×. The band is now
    bought with a **byte allowance** (`_PREFETCH_BYTES`, 48 MB per direction) rather than a page
    count, scaled by the **heaviest page in view**. — *Windows (offscreen GUI)* — 8 new tests,
    1415 green
    - **The curve.** 48 MB is ~26 Letter pages at 100%, so ordinary reading never notices it and
      the fixed cap of 2 is still what binds; the band falls to 1 page once a page passes 48 MB and
      to 0 past 96 MB. Post-M88 that is: 100% on the 1.75× panel (10.07 MB) → 2, 200% (40.27 MB) →
      1, 500% (~264 MB) → 0. It is **page size**, not zoom, that drives it — an A0 sheet at 100% is
      ~32 MB and already down to 1
    - **Measured A/B, same process, same run** — the fixed band forced back on for the control.
      40 zoom steps to 8.0, then 20 page-steps of scrolling:

      | phase | fixed band (`main`) | **adaptive (M87.1)** |
      | --- | --- | --- |
      | zoom sweep | 180 pages / **6081.7 MB** / 6.38 s | 132 pages / **2450.7 MB** / **2.74 s** |
      | scrolling at 8× | 3 pages / 372.3 MB / 0.43 s | 3 pages / 372.3 MB / 0.43 s |

      **60% fewer bytes rasterised and 57% less time** in the sweep; the scroll phase is
      byte-for-byte identical, which is the control that shows the visible band is not starved —
      the pages a reader actually arrives at are rendered either way
    - **What the trade costs**: at high zoom a page now rasterises as it comes into view rather than
      ahead of it. That is the intent (prefetching a 124 MB page the reader is several scrolls away
      from is the waste being removed), and normal-zoom reading is untouched
    - **The cache sweep barely moves** (1183.7 → 1174.0 MB) because M87.2's ceiling was already
      binding there — the store fills to its budget either way, only with more useful pages. Where
      it shows is what a *window* holds: a deactivated window drops to 242.3 MB from 490.8, and five
      windows open together to **336.7 MB from 585.1**
  - [x] **M87.3** A render pass costs the **band**, not the document — the carried M86.1 follow-up,
    assigned to M87 by the premise check because it is the same question asked about the walk rather
    than the cache. `_visible_range()` becomes a **binary search** over a y-sorted index of page
    tops, and the drop-offscreen pass reads a **tracked set** of the pages actually holding a pixmap
    instead of asking all N. — *Windows (offscreen GUI)* — 16 new tests, 1431 green
    - **There was a third walk, and it made the pass quadratic.** The follow-up filed two (the range
      scan and the drop loop) and measured ~6 ms/pass on 320 pages. Found while verifying the fix:
      `AnnotationOverlay._paint_visible_content` looped over **every page in the document** asking
      `_content_band_contains`, which re-derived the band — a viewport-to-scene map and a full page
      scan — **once per page**. O(N) pages × O(N) scan, so the lazy pass that exists to avoid
      O(document) work was itself the worst offender. Both it and `repaint()` now derive the band
      once
    - **Measured against `main`, same machine, 80 scrolled passes per point:**

      | pages | `main` | **M87.3** | |
      | --- | --- | --- | --- |
      | 20 | 1.89 ms | 1.66 ms | |
      | 60 | 2.89 ms | 1.97 ms | |
      | 320 | 15.36 ms | **2.07 ms** | 7.4× |
      | 1000 | **127.37 ms** | **2.64 ms** | 48× |

      `main`'s curve is the quadratic — 320 → 1000 pages is 3.1× the document and 8.3× the time. The
      fixed pass is flat in everything but the band
    - **The differential test is the substance.** A search rewrite is only as good as its agreement
      with the scan it replaced, so the old linear scan is kept as an oracle and cross-checked at
      every scroll position, three zooms, both page layouts and five page-size regimes — including
      facing pages that **share a y** (the case a plain `bisect` gets wrong) and a page taller than
      the viewport whose top sits far above it
  - [x] **M87.2** Cache: entry count → **global byte ceiling** — shipped first, as the premise check
    said it should be, because it is a live defect rather than preparation for M88. The 48-entry
    `OrderedDict` on each `PdfView` becomes one process-global `viewer/pixmap_cache.py` store with
    **two budgets**: retention in pages (24 — the band plus ~3 screenfuls of scrollback, ~44 MB of
    ordinary Letter) and a **1 GB byte backstop** that only binds once pages are genuinely enormous.
    Four defects answered: **per-window** (N documents were N independent budgets), **count not
    bytes**, **evicting pages still on screen**, and **holding pixmaps for windows nobody is looking
    at**. — *Windows (offscreen GUI + headless)* — 16 new tests (3 verified red), 1407 green
    - **Measured before/after, same machine, same script** — 60-page Letter at 1200×900, 40 zoom
      steps to the 8.0 ceiling. The store now *falls* as pages grow, which is the whole point: a
      page count cannot do that.

      | step | zoom | `main` RSS / entries / cached | **M87.2** RSS / entries / cached |
      | --- | --- | --- | --- |
      | open | 0.85 | 131.2 MB / 8 / 13.8 MB | 131.6 MB / 8 / 13.8 MB |
      | 16 | 2.62 | 426.0 MB / 48 / 307.8 MB | 339.1 MB / 24 / 220.8 MB |
      | 32 | 6.67 | 2104.1 MB / 48 / 1985.7 MB | 1156.2 MB / **15** / 1037.7 MB |
      | 40 | 8.00 | **3243.5 MB** / 48 / 3125.0 MB | **1183.7 MB** / **9** / 1065.2 MB |
      | | | peak working set **3450.9 MB** | peak **1498.2 MB** |

      (This sweep's schedule is gentler than the premise check's, which reached 4431 MB — both
      columns here are the same run shape, so the comparison is internal.)
    - **Pinning is what makes "no thrash" structural.** The view pins the band *before* painting it,
      so no page of a pass can evict a page of the same pass — a guarantee, not a big-enough number.
      It is also why a single page larger than the whole budget (an A0 poster at 500% ≈ 600 MB)
      still displays, putting the store over its nominal ceiling; that is graceful, not a leak
    - **Background release is two tiers, not one — a deliberate deviation from the spec's flat
      "background windows drop their pixmaps".** Losing *focus* drops the scrollback but keeps the
      band on screen (measured 1183.7 → 490.8 MB); **minimised**, where there is nothing to blank,
      drops everything including the scene items' own references (→ 118.5 MB, i.e. back to the
      as-opened figure). On Windows, windows tile — painting a still-visible window's pages white on
      deactivation would trade a visible defect for memory nobody asked to trade. Owner call if the
      full drop on deactivation is wanted anyway
    - **One new obligation the shared store creates**: the premise check ruled out a leak on close
      *under the old per-view dict*, which died with the view. A global store has no such luck, so
      `closeEvent` releases the window's entries and the handle releases again on collection.
      Tying that to the view's `destroyed` signal was tried first and **crashed the suite with an
      access violation** — dispatching a Python slot from inside Qt's C++ teardown during a GC pass
      is not safe; the handle is a plain Python object instead, holding no reference back to the view
    - **Five windows, one budget**: five documents open simultaneously → 585.1 MB RSS / 23 entries /
      410.9 MB cached, against the five independent 48-entry caches `main` would have allowed
- [x] **M88** DPI correctness — what "100%" means (owner-reported: "why does the document appear
  smaller than in Edge and Brave at the same zoom percentage?"). Because our 100% is 1 pt → 1 logical
  px at 96 DPI, so we show **75% of physical size and call it 100%**. Investigating it surfaced a
  worse defect: **`devicePixelRatio` is handled nowhere**, so on a 1.75× laptop panel every page is
  upscaled and the **text is blurry**. View-only. **Must follow M87** — see the renumbering note in
  `PLAN.md`. Spec in `PLAN.md` §M88. Shipped as **two PRs plus one closed row**:
  M88.1–.4 together ([#211](https://github.com/utyagi24/klarpdf/pull/211)) since they are one
  mechanism, M88.6 after them as the plan sequenced it
  ([#212](https://github.com/utyagi24/klarpdf/pull/212)), and M88.5 closed as no work required.
  **Premise check done 2026-07-28 before building, on the owner's own two screens** — every figure
  in the spec held exactly. Laptop panel `\\.\DISPLAY1` DPR **1.75**, external `DELL U2722DE` DPR
  **1.0**, both **96 logical DPI**; `logicalDpi/72` = 1.3333; a Letter page measured **6.375 in** at
  our "100%" (75% of physical, so Edge's 100% was our 133%); a fresh `QPixmap` reported
  `devicePixelRatio` **1.0**, i.e. DPR handled nowhere. Also confirmed the four Qt mechanics the fix
  rests on, none of which the spec had checked: `QGraphicsPixmapItem` lays a DPR'd pixmap out at its
  **`deviceIndependentSize()`** (so geometry is untouched), `QPixmap.fromImage` **inherits** the
  ratio from the `QImage`, `transformed()` **preserves** it (the rotation path), and
  `QWindow.screenChanged` fires on **both** directions of a drag between screens.
  - [x] **M88.1 + M88.2 + M88.3 + M88.4** shipped together — they are one mechanism, not four:
    **three scales where the code had one**. `zoom` stays what the reader asks for and the %
    indicator shows; **`scale`** (= `zoom × logicalDpi/72`) is scene units per point and drives all
    *geometry*; **`device_scale`** (= `scale × devicePixelRatio`) drives only the rasteriser and the
    cache key. — *Windows (offscreen GUI + hands-on, both screens)* — 17 new tests, 1448 green
    - **Measured on the 1.75× panel, at 100%**: layout `816 × 1056` logical px = **8.500 in** wide
      (was 6.375), pixmap `1428 × 1848` device px at `dpr = 1.75` — **1.75 device px per logical px,
      i.e. native, no upscaling**. The blur is gone because the pixels are real, not interpolated
    - **M88.3 verified by actually dragging the window** between the two screens and back. The
      layout is **byte-identical on both** (816 × 1056, 8.500 in) while the pixmap re-renders
      native each way — 1428 × 1848 on the panel, 816 × 1056 on the Dell. That is the whole
      contract: a screen change moves *pixels*, never *layout*
    - **The cache key is `device_scale`, not `zoom`.** The store went process-global in M87.2, so a
      zoom key would have handed the 1.0× Dell's pixmap to the 1.75× laptop — this milestone's own
      bug, cached and durable
    - **One M87 interaction the spec did not call out**: `_page_bytes` sizes M87.1's prefetch
      allowance off the *layout*, which is in logical units, so on a 1.75× panel it under-counted
      the real pixmap by **3.06×** — on exactly the machine that can least afford it. It now carries
      the `dpr²` term
    - **The plan's post-M88 projections were right to two decimal places.** Measured on the panel:
      **10.07 MiB** per page at 100%, **40.27 MiB** at 200% — the exact figures `PLAN.md` §M88
      predicted — and M87.1's band degrades **2 → 1 → 0** across 1×/2×/4× just as its curve said it
      would post-M88
    - **M88.3's first mechanism segfaulted Linux CI, and the mechanism was replaced rather than
      patched.** It connected to the **window's** `QWindow.screenChanged` and rebuilt the scene
      *inside* that callback — two lifetime hazards: the sender is not the view and outlives it (a
      slot invoked on a destroyed view is a **crash**, not an exception — measured: PySide6 does not
      reliably drop the connection when the receiver dies), and `scene.clear()` destroyed every item
      while Qt was mid-show. Now the view listens for Qt's own **widget** events
      (`DevicePixelRatioChange` / `ScreenChangeInternal`), which are delivered to the view and die
      with it, and answers on a `QTimer` parented to the view — nothing to dangle, nothing rebuilt
      inside a Qt callback, and a burst of screen events collapses into one pass. It is also
      **cheaper**: a DPR change now costs a re-render rather than a relayout, since the layout is in
      logical units; only a logical-DPI move restates the page rects, which on Windows never happens
      (every screen reports 96 DPI and the scaling rides in the DPR). **Windows and WSL both ran the
      suite green** — CI was the only place it showed, and the crash was in Qt rather than Python so
      it surfaced ~74% into the run, far from its cause
    - **Six existing tests had the old identity baked in** and were re-based, not weakened: three
      converted to the scale they actually mean (`device_scale` for pixmap probes, `scale` for the
      WYSIWYG text-box editor), one re-picked its zoom to keep the same scene geometry, and the
      adaptive-prefetch pair moved to post-M88 page weights — an A0 sheet at 100% now costs 55 MiB,
      past the whole 48 MB allowance, so the test pins two rungs (A1 → 1, A0 → 0) with real ISO
      sizes instead of one. A seventh, the highlight-blend probe, was a **latent fixture bug** the
      new scale exposed: `scene.render()` letterboxes by default, and the white bars it leaves grew
      from ~1 px to ~3 px and defeated a fixed inset — fixed by not scaling the source at all
  - [x] **M88.5** Migrate saved per-document zooms — **closed as no work required (owner call,
    2026-07-28)**. The premise was re-checked while building M88.1: nothing reopens at a saved zoom
    (documents open at Fit Page by the v0.9.1 decision, and `apply_state()` still has **no
    production caller** — only tests), so there was no remembered magnification for M88.1 to
    redefine and nothing to migrate. That left the narrower question the plan reserved — write a
    *basis stamp* now so a future "restore my zoom" could tell pre- from post-M88 values, or write
    nothing. **Owner chose nothing**: the feature may never be built, and if it is, it can start
    clean rather than migrate values of unknown era. The trade being accepted is recorded in
    `PLAN.md` §M88.5 so it does not get rediscovered as a bug — a pre-M88 stored `1.0` meant 6.375″
    and now means 8.5″, so any future restore that honours old values reopens them ~33% larger
  - [x] **M88.6** Zoom range → **25–500%** (was 10–800%), sequenced *after* M88.1 as the plan
    required, since the DPI correction shifts what every percentage draws. — *Windows (offscreen
    GUI)* — 7 new tests, 1455 green
    - **A hard floor would have broken Fit Page, and this was measured before choosing.** Fit Page
      on an **A0** sheet in a 1100×850 window wants **17%**; clamped to 25% the page *overshoots
      the viewport* — in portrait **and** landscape. A "Fit Page" that does not fit the page is
      broken, so the floor drops to the Fit Page zoom whenever that is smaller. The plan did not
      anticipate this
    - **The floor is derived from Fit Page, not from the current zoom** — the first attempt held it
      at `min(_MIN_ZOOM, current)` ("no step may zoom you *in*", which is true) and the new tests
      caught that it **traps**: zoom in one step from a 17% fit and the floor follows you up, so
      stepping back out to the fit becomes impossible. Fit Page is the natural bottom of zooming
      out, it is the smallest fit (Fit Width is never smaller), and it is computable at any moment,
      so one bound covers fits and manual steps with no special case
    - The preset list gains **500%**, so both ends of the range are reachable from the dropdown
      rather than only by typing; no preset sits outside the bounds, so none silently clamps to a
      different number than the item clicked. A saved zoom outside the new range (old files hold
      10% / 800%) is ignored by `apply_state`'s existing range check and the view keeps what it had
- [x] **M89** The rest of the reading-input conventions — **complete**: five parts shipped, one
  closed unmerged. All view-only. **M89.4 shipped early** on its own; **M89.1–.3** landed together
  ([#214](https://github.com/utyagi24/klarpdf/pull/214)) and **M89.6** alone
  ([#216](https://github.com/utyagi24/klarpdf/pull/216)); **M89.5 was closed, not merged**, once
  hands-on validation showed the gesture never reaches it. Spec in `PLAN.md` §M89; each part's
  status is on its own line below, not restated here.
  - [x] **M89.1 + M89.2 + M89.3** shipped together as the plan sequenced them — the first two edit
    the same `keyPressEvent` and the third the `wheelEvent` beside it, so separate PRs would have
    meant rebasing against one function three times for no review benefit. — *Windows (offscreen
    GUI)* — 18 new tests (12 verified red), 1477 green
    - **M89.1** `Home` / `End` / `Ctrl+Home` / `Ctrl+End` → the **document's** start/end, all four
      one verb. Every one of them was **dead**: `QAbstractScrollArea` binds Home/End only on macOS,
      so the two keys a reader reaches for in a long document did nothing while `PgUp`/`PgDn`
      worked. Implemented as the vertical scrollbar's minimum/maximum — the literal reading, and it
      gets the page-indicator update for free, since `_on_scroll` already does it
    - **The keypad's Home/End had to be spelled out.** Qt sets `KeypadModifier` on them, so an
      exact match on the modifier set would have taken the main keyboard's key and silently ignored
      the numeric keypad's — a distinction no reader means. It is stripped before comparing
    - **M89.2** `Space` / `Shift+Space` → one screenful down/up, the same `SliderPageStep` the
      working `PgDn`/`PgUp` already trigger. Also dead before this
    - **M89.3** `Shift+wheel` pans **horizontally**. An *override*, not a gap, and the test that
      pins it went red the expected way: Qt's own `Shift+wheel` scrolled this view **down** with the
      h-bar at full range, so a page zoomed wider than the window had no wheel gesture that could
      cross it. A wheel carrying a genuine horizontal component (tilt wheels, most touchpads) is
      left to `super()`, which already routes it correctly — only the vertical axis Shift is
      decorating gets reinterpreted
    - **The shifted wheel is consumed even when the page fits across the viewport**, so it is
      *inert* there rather than quietly scrolling down. It means one thing everywhere, which is how
      a browser behaves; a gesture that changes meaning based on invisible state is worse than one
      that sometimes does nothing
    - **All five keys live in `PdfView.keyPressEvent`, never as window-level `QAction` shortcuts**
      — a window shortcut fires wherever focus is, so `Home`/`Space` bound that way would hijack
      those keys from the inline text-box and form-field editors (children of this viewport), where
      they mean line-start and a literal space. Two tests pin the decision rather than the
      mechanism: one types a space into a live form-field editor and asserts the page did not move,
      one walks every `QAction` in the window and fails if any claims Space/Home/End
  - [x] **M89.4** `Ctrl+=` as a Zoom In alias (Qt's `ZoomIn` is `Ctrl++` = `Ctrl+Shift+=` on US) —
    **pulled out of M89 and shipped on its own**, with two open-path bugs the same testing pass
    turned up. The owner hit the dead accelerator by hand while verifying M86 ("Zoom with Ctrl+- is
    working but not with Ctrl++"), which made it a reported bug rather than a predicted one; waiting
    for the rest of M89 would have meant knowingly shipping a dead key. — *Windows (offscreen GUI)*
    — 10 new tests (7 verified red), 1379 green
    - **The alias.** `Ctrl+-` is unshifted and always worked; `Ctrl++` demands `Ctrl+Shift+=` on a
      US layout, so plain `Ctrl+=` matched nothing. `Ctrl++` stays the *first* binding, so the menu
      row still advertises the standard accelerator
    - **The sidebar opened with no page marked at all** (the M85 follow-up, now closed). The panel's
      current row starts at -1 and `open_at` restores a page without *changing* it, so
      `currentPageChanged` never fired. New `ThumbnailPanel.mark_open_page()` — it *selects* as well
      as makes current, unlike `set_current`, whose `NoUpdate` branch exists to protect a reader's
      staged multi-row selection; at open there is nothing to protect and the marker should look
      like the one a click leaves
    - **Reopening never restored the remembered page** — long-standing, uncovered, and found only
      because the marker sat on top of it. `open_at` passed `self._current` to `goto_page`, but
      `_build_scene` renders at the end of its rebuild and that render re-derives the current page
      from a viewport still scrolled to the top, resetting the field to 0. A document saved on page
      3 reopened on page 1. The page is now carried in a **local** across the rebuild, the idiom
      `rotate_view` and `set_page_layout` already used. Worth recording that **M86 (#205) masks this
      by accident** — its `_hold_render` suppresses the intermediate render, so the field survives;
      that is why the owner saw the page restore correctly while testing that branch. Fixing it here
      keeps the restore correct on its own terms rather than as a side effect of a perf change
  - [x] **M89.5** Pinch-zoom — **closed unmerged (owner call, 2026-07-29): the handler is
    unreachable on Windows.** PR [#215](https://github.com/utyagi24/klarpdf/pull/215) closed, not
    merged. Full measurement in `PLAN.md` §M89.
    - It was written, unit-tested and flagged for hands-on validation, because the suite can
      construct a `QNativeGestureEvent` but cannot prove Windows *delivers* one. Validated on a
      Synaptics Precision Touchpad + HID touchscreen with both zoom paths instrumented — **neither
      route reaches the handler**: the touchpad driver translates a pinch into **Ctrl+wheel**
      (`delta = ±120`, one whole detent per step; a native gesture would report a *fractional*
      value), and on the touchscreen Qt synthesises **mouse** events because the app never sets
      `WA_AcceptTouchEvents`, so the second finger is discarded. The plan's premise — "Windows
      delivers the event today and nothing consumes it" — was simply wrong
    - **Nothing user-facing is lost: pinch already zooms**, through M80's Ctrl+wheel path, and
      pointer-anchored at that, since the cursor sits where the fingers are. What M89.5 would have
      added is *continuity*, and the driver's whole-detent quantisation puts that out of reach
      without raw Precision-Touchpad HID input Qt does not surface
    - Reaching the touchscreen would take `WA_AcceptTouchEvents` + a pinch recogniser, which changes
      Qt's mouse synthesis for all touch input and risks the finger-drag selection and long-press
      menu that work today — declined for a gesture the touchpad already performs
    - **Shipping a handler no machine can reach is worse than not shipping it**, which is why this
      is a closure rather than a merge. The hands-on flag did its job
  - [x] **M89.6** `Ctrl+A` → select all text in the **whole** document, **plus** the repaint rework
    it depends on. — *Windows (headless + offscreen GUI)* — 16 new tests (15 verified red), 1475 green
    - **Whole document, not the current page** (owner call — Edge and Brave both do): a viewer that
      scrolls continuously has no page boundary a reader would recognise as the limit of "all".
      Nearly free in the model, which has always carried the selection as a `(page, word)`
      anchor/cursor pair spanning pages — `select_all` pins it to the two ends. The ends are the
      first and last pages that **have words**, not simply page 0 and page n−1: a leading or
      trailing scanned page has none, and an anchor on a non-existent word index would select
      nothing at all. A document with no text layer selects nothing and does not error
    - **The repaint rework was not optional, and the measurement now has both columns.** On a
      200-page / 104,000-word document, offscreen on this machine: **104,000 scene items, 8.85 s to
      select and 9.59 s for one zoom step** before → **120 items, 0.02 s and 0.07 s** after. Two
      changes did it: painting is clipped to `PdfView.overlay_band()` (the visible pages plus the
      `_PREFETCH` margin), so the item count follows viewport size rather than document length; and
      each line's run coalesces into one rect. **Pre-existing and latent**, not something Ctrl+A
      introduced — a drag carried across several hundred pages already reached that state
    - **`overlay_band` is deliberately the flat `_PREFETCH` margin, not the byte-budgeted band
      `_render_visible` computes.** An overlay item is a handful of rects, not megabytes, so
      M87.1's adaptive shrink has nothing to weigh — and this runs on every scroll and every drag
      update, where the constant costs a binary search and the budgeted one a page-weight scan
    - **Clipping forces a repaint on scroll**, or selected text scrolling in would have nothing
      painted over it. `repaint_for_scroll` returns immediately unless the band actually moved,
      which is the common case for a scroll event
    - **The coalescing closed a gap that predated it.** The mark this app *commits* has always been
      one bar per line (`_selection_line_bars`), so the per-word preview was the odd one out and a
      highlight visibly re-flowed on release. The grouping now lives on the selection as
      `line_bars()` and `MainWindow._selection_line_bars` delegates to it, so preview and mark
      **cannot** drift; reviewed on rendered grabs, which are pixel-identical
    - Bound in `PdfView.keyPressEvent`, not as a window `QAction` — a focused inline editor must
      keep its own select-all, and a test pins that
- [x] **M90** Notes: the interface — the visible half of M81. Spec in `PLAN.md` §M90.
  A note is a **field of its host mark**, and every surface here follows from that: there is one
  editor, one write path, and nothing to keep in step. The four parts below are one PR
  ([#219](https://github.com/utyagi24/klarpdf/pull/219)) because the glyph, the sidebar row and the
  foreign badge are all views of the field M90.1 writes — reviewed apart, each points at nothing.
  - [x] **M90.1** Create + edit — Note verb on **Markup ▾** (no new toolbar slot) + the M76 context
    menu. Attaching is primary; creating a highlight is the fallback when the selection has no HUS
    — *Windows (headless + offscreen GUI)* — 23 new tests, 1521 green
    - **Attaching is the primary act, and the fallback is the only thing that creates** (owner
      rule 4). `resolve_note_host` is the whole of rule 6 in the model, headless: a **Highlight**
      wins, failing that the **topmost** underline / strikeout — "topmost" being the last match in
      the page's annotation tuple, since that tuple *is* the z-order. A layered passage therefore
      has one deterministic host, and the note keeps the colour the reader already associates with
      the passage (rule 3)
    - **The popup opens before anything is created**, which is what makes "the creation plus the
      attach is one undo macro" true *and* leaves an abandoned sweep with no trace. Commit resolves
      the host again, merges the fallback highlight through the **M59.10 merge** (so a note-created
      highlight is the same mark Highlight Selection would have made, including how it folds into a
      neighbour), and pushes one `SetAnnotationsCommand` per touched page inside one macro
    - **Clearing the text removes the note and leaves the mark** — the one place in the app where
      emptying an editor is not a delete. It falls out of M81's shape: the note is a *field* of the
      mark, so `note=""` is an edit of the mark, not its removal. Remove Note on the context menu
      is the same write, so the two cannot drift
    - `viewer/note_editor.py` is a **new module, not a fourth branch of the text-box editor**: it
      reuses that editor's *idiom* (a `QPlainTextEdit` child of the viewport committing on
      focus-out, so M89's key guards and clipboard routing behave as they already do) but none of
      its WYSIWYG page-rect sizing, which a note has no use for. Fixed viewport-pixel size, anchored
      under the passage and flipped above it near the viewport bottom, washed towards white from the
      host's colour so black text stays legible on every palette entry
    - **The stale-callback trap bit again, in a new costume.** Opening a second note while the first
      is still alive delivers the *outgoing* widget's focus-out **during the incoming one's**
      `setFocus()` — so an unscoped callback commits and closes the popup that just opened (the
      re-open read back empty). Every callback now passes its own widget and the controller ignores
      one from a popup that is no longer current; this is the same guard `_on_editor_focus_out`
      already keeps by capturing its editor. Found by a test, verified red
    - Note is **one-shot, not sticky** like the M73 HUS quartet, and carries **no swatch row** on
      Markup ▾ — a note takes its host's colour, so there is no fourth colour to choose
  - [x] **M90.2** On-page glyph, so a note isn't invisible until you right-click the exact mark
    — *Windows (headless + offscreen GUI)* — 9 new tests, 1530 green
    - **It sits in the page's right margin**, on the line the mark ends on — *not* at the end of
      the marked run, which is where it first went and where the render showed it covering the
      text that *follows* the highlight ("jum" of "jumps"). Straddling the mark's own corner was
      the other option and obscures the very passage it annotates. A margin is empty by
      construction on a text page; a mark that runs into it pushes the badge just past its end,
      still clamped inside the page
    - **Sized in scene units, not page points**, which is the whole of "legible at low zoom": zoom
      rebuilds the scene rather than scaling the view, so a scene unit *is* a logical pixel and the
      badge is 15 px at Fit Page and at 400% alike. Page-point sizing would have failed the
      criterion by construction. Pinned by a test across three zooms
    - **It does not re-tint with the app theme, deliberately** — a stated departure from the
      milestone wording. The badge sits on the *page*, not on chrome: a page is white under every
      theme, and Night Reading Mode inverts the page render rather than theming it, so a
      palette-tinted glyph would turn light in dark mode and vanish on a yellow highlight. It is
      opaque in its host's washed colour (rule 3) with dark ink — the same `wash` the popup uses,
      so badge and editor read as one thing
    - **Click toggles** the note open and shut, hover reads it from the badge's tooltip. Routed in
      `PdfView` right after the resize handles — the next most specific target, mode-independent,
      and below the armed tools so arming still wins the press. The toggle needed a mechanism
      because a real click reaches the popup's **focus-out before** the view's press (measured):
      the popup is already gone by the time the click is handled, so a naive handler reopened it
      and the second click was a visible no-op. The close now records *which* note it was, armed
      **only by a genuine focus-out** — a programmatic close is nobody's click, and arming it there
      let the flag outlive its dispatch and swallow the next legitimate open. The key is
      `(page, bounds, type)`, not the mark object: descriptors are frozen, so committing an edit
      replaces the host and identity went stale the moment the text changed; and layered marks
      share bounds, so without the type, clicking one while the other was open closed it instead of
      switching. Toggling shut **commits** — it is the same "I'm done" as clicking away — and Esc
      stays the way to abandon an edit. No close button: three dismissals already exist, and
      §Design budgets does not spend chrome on a fourth
    - The popup's stylesheet was built by implicit concatenation where only the **first** literal
      was an f-string, so the second's `}}` stayed *two* closing braces and Qt logged
      "Could not parse stylesheet of object _NotePopup" on every note. The braces now live in plain
      literals with the interpolation in an f-string of its own, so there is nothing to escape and
      it cannot recur
    - **Layered marks fan along the margin** (owner-reported during testing): the app deliberately
      allows layered HUS — M59.10 scopes merging *per type* — and owner rule 5 gives each mark its
      **own** note, so an underline and a highlight on one passage carry two notes. Both badges
      anchor to the same line and landed on **exactly the same pixel**: the second painted hid the
      first completely and won every click, so a note the user had written was invisible *and*
      unreachable, reappearing only when its neighbour was deleted. A badge now slides left until
      it clears the ones already placed on that page. **Left, not down** — the vertical position is
      what says which line the remark is about, so pushing it down would claim the wrong passage.
      (The other half of that report was already correct and is now pinned: removing the highlight
      takes the highlight's note and leaves the underline's — a note dies with **its own** host,
      owner rule 2)
  - [x] **M90.3** Annotations sidebar shows and edits notes (M77 panel) — *Windows (headless +
    offscreen GUI)* — 6 new tests, 1536 green
    - The note is **appended to the row, not substituted for the passage**: the snippet is what
      lets a reader recognise *which* mark a row is, so it stays even when the remark is the more
      interesting half. Clipped harder than the snippet (32 vs 48 chars) with the **full note as
      the row's tooltip** — a remark you can only read half of is worse than one you can hover
    - **Editing there is the same popup, not a second editor.** A double-clicked row reveals the
      mark and opens the on-page editor, so "editing in the sidebar and on the page agree" is true
      **by construction** rather than by keeping two implementations in step — the drift this
      codebase has been bitten by before (preview vs committed mark, M89.6). An unnoted row writes
      its first note the same way, so the list is a creation path too
    - "Deleting the host removes the row" needed no code: `populate()` re-runs on every edit and
      reads the live model, and the note is a *field* of the mark it lists
  - [x] **M90.4** Foreign `/Contents` shows **read-only**, and M68 adopt-on-edit carries it across
    — *Windows (headless + offscreen GUI)* — 7 new tests, 1543 green
    - A foreign markup's comment gets **M90.2's badge in grey**, and grey does two jobs that
      agree: a `ForeignAnnot` carries no colour to take, and grey is the signal that this note is
      **read-only** until the mark is adopted (M68's rule). So the badge says whose note it is
      before it is opened. Only **commented text markups** are badged — an uncommented one has
      nothing to show, and a sticky note already draws its own icon into the page pixmap
    - **The same popup, read-only**: a reader should not have to learn a second place remarks
      appear based on who wrote one. It has no commit callback at all, so nothing can be saved by
      accident, and its placeholder names the way to make it editable — M68's existing
      double-click adoption, not a new verb
    - **The foreign pass is band-gated** like the content marks, because finding these comments
      reads each page's annotation dictionaries — document-wide on every edit is exactly the
      O(document) trap M87.3 and M78.8 were spent closing, and a reviewed 200-page PDF is the file
      it would have been slowest on. Pinned by a test
    - **A foreign sidebar row now reads like one of ours** — `type · passage — comment`. It read
      `type · comment` before, putting the comment in the slot our own rows use for the *passage*,
      so the same position on the same list meant two different things depending on who wrote the
      mark, and a commented foreign highlight never showed the words it covered at all
    - Adoption needed no new model work: **M81.3 already carries the comment across**. What M90.4
      adds is the interface following it — the grey read-only badge becomes a coloured editable one
      holding the same words, pinned end to end
- [x] **M91** Whitespace fidelity, glyph legibility, reading position — three defects from the
  owner's post-M90 testing pass (2026-07-29), plus **M91.4**, three more the pass on M91.3 itself
  turned up. Independent of one another, all **view-layer** (no model, no save path, no
  round-trip), **one PR per part**. Spec + the measurements behind each in `PLAN.md` §M91. **The
  numbering is the build order** (owner request), which is not the order the first three were
  reported in: fidelity bugs before features, the owner-gated pick in the middle so it is in flight
  while something else is reviewable, and the part that *adds* surface last.
  - [x] **M91.1** **A text box paints its leading spaces.** Owner-reported as truncation, then
    refined on re-test: the spaces *are* saved, the box *paints* without them, and they reappear only
    in edit mode. Measured: the model, the bake (`(    hello) Tj`) and the round-trip are all
    correct — **`QGraphicsSimpleTextItem` reserves leading whitespace in `boundingRect()` but paints
    the glyphs flush left** (288 px vs 160 px reserved at 24 px; first ink at **x = 2 in both
    cases**), so the box widens by the indent while the text stays put and the space becomes slack on
    the *right*. Fix: one item per line, indent removed from the string and paid as an x offset.
    `_wrap_textbox_lines` has a second, independent bug — `split(" ")` discards leading spaces as
    empty tokens, so it strips the indent from every wrapped line. One rule covers the lot:
    **whitespace decides whether text exists, never how it looks** — so `_commit_note` and the form
    field dialog's initial *value* stop stripping too (the field *name* keeps its strip; it is an
    identifier), while every all-blank drop stays. Also recorded: the headless platform resolves
    **no font at all** (every glyph, space included, measures exactly 1 em), so no test here may
    assert an absolute text pixel offset — *Windows (offscreen GUI)* — 12 new tests, 1575 green
    ([#222](https://github.com/utyagi24/klarpdf/pull/222))
    - **Verified against the file, not just the tests**: the same three boxes rendered through the
      overlay and baked to a PDF put the ink in the same place. That comparison *is* the milestone —
      the defect was the two disagreeing — and it is the check the pixel-free headless assertions
      cannot make, since the test platform resolves no font
    - The paint is now **one item per line**, which is also what makes differing per-line indents
      expressible at all: a single item can only be positioned once. A blank line paints nothing and
      still spaces, and vertical centring moved to `len(lines) * lineSpacing()` because no one item
      spans the box any more
    - The wrap holds the paragraph's indent **out of** the word loop rather than passing it through:
      it is charged against the width on the first line (so an indented line wraps earlier) and not
      repeated on continuations — a continuation is not separately indented
    - 9 of the 12 new tests fail without the fix; the 3 that pass either way are the all-blank drops
      and the line spacing, which were already right and are pinned so the fix cannot cost them
  - [x] **M91.2** **Rotate stops reading as Undo** — `rotate-left.svg` is Feather's `rotate-ccw`: a
    ~340° circle with an arrowhead and **nothing being rotated**, i.e. the universal undo/reload
    mark. So it reads as Undo *on its own merits*, which is why v0.16.2's removal of the neighbouring
    curved arrows didn't fix it. Both rotate glyphs are now **a full portrait page with the sweep
    traced parallel to its own top-left corner** — the bar already establishes *rounded rect = the
    page* in fit-width/fit-page. Owner call: redraw, keep the single direction — dropping the button
    and restoring both directions were both rejected — *Windows (offscreen render)* — 5 new tests,
    1568 green ([#223](https://github.com/utyagi24/klarpdf/pull/223))
    - **The shipped drawing is "Corner gutter", designed in Claude Design** (project *Sheaf PDF
      application branding*, `Rotate glyph candidates.dc.html`) and imported over the design MCP after
      two in-house rounds were rejected. Its idea is one our hand-drawn attempts did not have: the
      sweep is an **offset curve of the page outline** — top rail, corner arc of r 7.2 (= the page's
      own r 2.2 + 5), left rail — so the clearance is uniform *by construction* rather than tuned, and
      the two shapes visibly belong to each other
    - **The corner and the direction are coupled** (`PLAN.md` §M91), and that finding is what the
      design brief was built on. Asked for the sweep on the **top-right**, we drew it: Rotate Left is
      counter-clockwise, so a sweep ending there must point back left over the page, and an
      arrowhead's arms open backwards from its tip — one arm lands **1.3 units** from the arc it just
      travelled, less than a stroke width once inked, and at 20 px head and arc merge into a blob. A
      compact corner sweep can only face the direction its corner allows
    - **The imported SVG was re-measured against our own gate, not accepted on the design doc's
      numbers**: parses under QtSvg, no banned construct, ink span **62%** of the canvas, centre
      **(11.5, 11.5)** — dead on — nothing across the 2 px margin, and the hand-derived mirror is
      pixel-exact (**0** differing px at 48 px). Rendered through **Qt's own rasteriser** at 16/20/24 px
      and grabbed from the real toolbar: a browser's SVG renderer flatters every candidate, and the
      question was never how the drawing looks but what the toolbar paints
    - Rotate Right is the exact mirror, and a test compares the two as **rasters** so they cannot
      drift apart; both names joined `POLISHED_ICONS`, and a second test pins the thing the milestone
      is actually about — the glyph must *contain a page*
  - [x] **M91.3** A **page counter on the reading bar** — `[ 10 ] of 320`, editable, two-way bound to
    `currentPageChanged` exactly as `ZoomWidget` is bound to `zoomChanged`. `sidebar_visible`
    defaults to **`False`**, so today a reader gets **no** position indication out of the box; the
    sidebar's current-thumbnail highlight is the only one that exists. Owner call: the 11th slot
    against the "~10, modes-only" budget is **taken** — a live indicator is not a mode, the bar
    already carries one, and the field replaces the Ctrl+G dialog trip rather than adding a verb.
    Non-goals recorded so they aren't re-proposed: no ◀ ▶ buttons, nothing in Full Screen /
    Slideshow, and Two-Page shows the current page (M85's definition), not a `10–11` span —
    *Windows (offscreen GUI)* — 10 new tests, 1573 green
    ([#224](https://github.com/utyagi24/klarpdf/pull/224))
    - **A plain `QWidget` in a `QToolBar` eats the bar** — now in `CLAUDE.md` §Gotchas. `addWidget`
      leaves it on the default **Preferred** policy and the layout hands it every spare pixel: the
      counter stretched to **627 px** in an 1100 px window and pushed the entire zoom cluster *off the
      right-hand end*. `ZoomWidget` never showed it because it fixes its own width, and it is the only
      other widget on either bar. Caught by grabbing the bar and looking — the failure mode is chrome
      that is simply **not there**, which no assertion about the widget itself would have found
    - **The total is pushed, the position is signalled.** There is no `pageCountChanged`, and
      insert / delete / undo change the count *without* moving the current page, so binding the total
      to `currentPageChanged` would have left `of 320` on screen after deleting ten pages
    - `editingFinished` (Enter **and** focus-out) is what makes clicking away from a half-typed number
      harmless; out-of-range clamps **and echoes the clamped value**, because the field is a readout as
      well as an input and one that disagrees with the view is worse than none
    - Full Screen / Slideshow needed no code — M78 hides the whole reading bar — but it is pinned, so
      the next person to add a floating readout learns it from a test rather than from a report
    - Built: the reading bar is **555 px** of an 1100 px window, the counter costing 119 px with its
      separator (§Design budgets' argument was never about space)
  - [x] **M91.4** **`Space` pages by a page, and the sidebar hands it over** — three reports from the
    owner's testing pass on the new counter (2026-07-30), all against **M89.2**. `Space` was the
    scrollbar's `SliderPageStepAdd`, which advances by the **viewport height**; the strip advances by
    the **page pitch**, and at Fit Page those cannot be equal — `_fit_zoom` reserves `2 * _PAGE_GAP`
    of margin and the layout puts one gap back between pages, so the pitch is one gap *less*.
    Measured at 1100×800: viewport 746, page 718, pitch 732 — **every press overshoots by exactly
    14 px and nothing resets it** (126 px by page 10, past half a screen by page ~27, where M85's
    largest-visible-area rule hands the count to the *next* page while the previous one still fills
    the top of the window: the owner's "it says 10, I'm looking at the bottom half of 9"). Both
    readings were right; the scroll offset was wrong. Now every paging key steps to a **reading
    stop** — each page's top, plus a tall page cut into the fewest *equal* steps that each fit a
    screenful — and takes the **furthest** one within a screenful, so a zoomed-out view still
    advances every page it shows. `PgDn`/`PgUp` come off Qt to keep M89.2's promise that they and
    `Space` are one verb — *Windows (offscreen GUI)* — 24 new tests, 19 of them verified red,
    1611 green ([#225](https://github.com/utyagi24/klarpdf/pull/225))
    - **The sidebar was not inert — it was eating the key.** `QAbstractItemView` *accepts* `Space`,
      so Qt's propagation stopped at the panel and the document could not be paged at all while
      focus was there; and what Qt does with it is add the current row to **the selection Delete
      Pages and Rotate act on**. So "is that expected behaviour?" is no twice over: dead *and*
      quietly staging a page the reader never picked. The panels now leave it unaccepted and
      `MainWindow.keyPressEvent` hands it to the view — which is M89.2's own decision read the other
      way round: a **`QAction` shortcut** fires *before* the focused widget, so `Space` must never be
      one; a **window `keyPressEvent`** runs only after every widget has declined it, so the inline
      editors still type their space. Pinned by the existing form-field test
    - **A thumbnail click now jumps even onto the already-current row.** It was announced through
      `currentRowChanged`, which fires only when the row *changes* — but the view drags the highlight
      along as you read, so scrolling away from page 1 and clicking page 1's thumbnail to get back
      did nothing. That is the other half of "clicking on page 1 again … requires multiple attempts":
      a no-op click, then presses the panel swallowed. `OutlinePanel`/`AnnotationsPanel` already
      jumped from `itemClicked`; the three now agree
    - Only `Space` is handed over. The arrows, `PgUp`/`PgDn` and `Home`/`End` all mean something in a
      page list and each jumps the view through `pageActivated` anyway — pinned, so the next person
      to reach for "route the reading keys to the document" learns the boundary from a test
    - **The page counter was fighting the reader too** (owner re-test): "press spacebar, the first
      page flickers but stays at 1" reproduced only once focus was accounted for, and the culprit was
      the field M91.3 had just added — **two** faults in the same 44 px box. `Space` was *eaten*
      (integer-validated, so `QLineEdit` accepts the key and the validator drops the character —
      dead for the rest of the session once the field had been clicked); and `editingFinished` fires
      on **every** focus-out with acceptable input, Qt not requiring the text to have changed, so
      clicking the field and clicking back onto the page re-ran `goto_page` and **re-seated the view
      on that page's top** — the flicker. Guarded on `isModified`, which is the exact question. Enter
      now also hands the keyboard back to the page. Recorded so it is not "fixed" in the wrong place:
      `ZoomWidget` has the identical wiring and is *not* wrong, because `set_zoom` early-outs on an
      unchanged value while `goto_page` has no such early-out and must not
    - **A coasting wheel undoing a deliberate step — M78's bug, met a second time.** The owner's
      100%-reproducible case: no click anywhere, spin the wheel **hard** back to page 1, press
      `Space`, and "the page flickers and stays on page 1"; the next press "moves only half a page".
      A flywheel wheel (and Windows' smooth scrolling) keeps emitting long after the hand has left
      it, so the coast walks the view back out of the step — a harder flick coasts longer, which is
      what the **growing count** of dead presses was all along, and why round one's repro (keys
      fired with no wheel in flight) could not see it. It hides because **scrolling up at offset 0
      is a no-op**: the coast is invisible until a key gives it somewhere to go, so the *key* looks
      broken. M78 diagnosed and fixed this — then scoped the guard inside `if self.slideshow`, so
      ordinary reading never got it. Now hoisted to the top of `wheelEvent` and armed by every
      deliberate navigation (paging keys, Home/End, `_deliberate_step`, `goto_page`)
    - Two things the generalisation needed, both caught by M78's own tests: **a wheel-driven move
      must not park the wheel that drove it** (`step_slide` lands via `goto_page`, so the wheel muted
      itself after one detent and a four-detent flick moved one slide), and **the quiet test must
      fail open on a backwards clock** — `event.timestamp()` and the `time.monotonic()` fallback are
      different clocks, and once *every* wheel event keeps the timestamp, an unstamped event followed
      by a stamped one would have left the wheel muted for ever
    - **`open_at` now announces the page it restored** — reopening a document closed on page 10
      showed page 10 with the counter reading **1**. `_current` is assigned directly there (the fit
      is sized against that page's row before a scene exists), so `_update_current` found the page it
      already held and stayed silent. The sidebar never showed it because `showEvent` carried a
      private workaround (`mark_open_page`); the counter had none, and neither would the next
      indicator bound to that signal
- [ ] **M92** Mouse-wheel scrolling — the owner reported (2026-07-30) that **one detent moves too
  much of the page**, and separately that scrolling is less fluid than Edge. Measured on the owner's
  display, those are one defect and one polish, in that order of weight. Spec + every number behind
  it in `PLAN.md` §M92. **Touchpad scrolling is out of scope** by owner call (*"though not perfect I
  am satisfied with it for now"*); the inertia work it would need — and the reason it is the
  expensive half — is recorded in `PLAN.md` §Future enhancements. **One PR per part.**
  - [x] **M92.1** **A wheel detent moves a defined distance.** Qt's `QGraphicsView` sets the vertical
    `singleStep` to **`viewportHeight / 20`** (confirmed: viewport 846 → 42, viewport 832 → 41), so a
    detent is `wheelScrollLines × singleStep` = **15% of the window height and nothing else** —
    unrelated to document, text or zoom, and worse the more screen the window is given. `_place_window`
    opens at the **full available screen height** by design, which puts that at its maximum. Measured
    on the owner's display: 2560×1440 @ 100%, window 1000×1353, viewport 770×1246, Fit Page 91% →
    `singleStep` 61 → **one detent = 183 px = 19.1% of a page = 10.1 lines of body text**. The rule
    becomes **`wheelScrollLines × _WHEEL_LINE_PX × zoom`** — window-independent, and zoom-scaled so a
    detent always moves the same amount of *document*; Windows' *lines to scroll* setting finally
    means what it means everywhere else. **Verified on the same display after the change: 87 px at Fit
    Page (2.09× smaller), and 9.1% of a page at 91%, 100% and 200% alike** — the zoom-invariance Qt's
    rule never had. **The constant is measured, not borrowed**: it shipped at 40 (the Chromium/Gecko
    *web* figure) and the owner's side-by-side then put us at 10 lines per detent against Edge's 8 in
    the same document (2026-07-31), so 40 × 0.8 = **32**. Two independent observations agree on the
    target — "Edge moves about half" of 183 px is ~91 px, and 8/10 of 109 px is ~87 px — and the
    likely reason the web constant was wrong to borrow is that **Edge renders PDFs through PDFium**,
    not the generic web scroll path. **Scope is the mouse only**:
    `_is_mouse_detent` leaves a precision device on Qt's path, by `pixelDelta` where the platform
    fills it in and by delta granularity on Windows, where it never does (a notched wheel reports
    whole multiples of 120, a touchpad reports fractions). **`tools/probe_wheel.py`**, added with this
    milestone, has since **run on the owner's hardware (2026-07-31) and validated exactly that**:
    wheel-discrete **50/50** and wheel-free-spin **160/160** whole detents, touchpad **1/376**. Three
    findings came with it. `event.device()` reports **`Mouse` / "core pointer" for all three** — Qt's
    Windows plugin cannot tell a touchpad from a mouse, so granularity is not a stand-in for a better
    test, it is the only test. **Free-spin is mechanical, not hi-res** — it emits *more* whole detents
    (160 vs 50), not finer ones, so the "hi-res wheel keeps the old step" gap does not exist on this
    mouse, and the **87 px lattice a detented wheel imposes is unreachable by software**. And
    `phase()` is **`NoScrollPhase` everywhere**, which answers an open question for the deferred
    touchpad-inertia work. — *WSL + WSLg* — [#227](https://github.com/utyagi24/klarpdf/pull/227)
  - [x] **M92.2** **The step is eased, not teleported.** A clock-driven animator: a tick moves a
    target, a timer walks the bar to it on an ease-out over **200 ms**, and a tick arriving
    mid-animation **extends the target** from the current position instead of restarting from rest.
    Driven from the **wall clock, not a per-frame increment**, so a frame blocked by a page rasterise
    costs smoothness but never the landing pixel (pinned by a test that stalls a frame 80 ms).
    Behind **View ▸ Smooth Scrolling**, on by default, off = M92.1 byte for byte. Verified on the
    owner's display with the real clock: one detent traces `19 36 48 59 67 74 79 83 85 86 87` px over
    12 distinct positions and lands on the M92.1 pixel — **worst single-frame jump 19 px against 87
    unglided**. **The duration was chosen with the wheel in hand** against a throwaway toggle demo,
    not from the benchmark; 200 ms sat on the edge of both bounds the benchmark drew (lag 88 px vs a
    detent of 87; duty 100%), and then **`_glide_tick` ending on the pixels rather than the clock
    moved one of them** — an ease-out's tail moves under half a pixel a frame, so the motion is
    complete at t = 0.80 and duty at 5 detents/s falls **100% → 80%** for the same landing pixel.
    170 ms stays recorded as the largest value inside both bounds as first drawn. The timer interval
    comes from `QScreen.refreshRate()` (truncated, so we never under-sample); a hypothesis that
    `CoarseTimer` would be too loose on Windows was **disproven** — indistinguishable from
    `PreciseTimer` at 16 ms (mean 16.01 vs 16.00, sd 0.21 both). — *WSLg / Windows*
  - [x] **M92.3** **The coast-mute is bounded** — owner-reported 2026-07-31: *"scroll really fast,
    press space, and the mouse wheel becomes unavailable for a long duration; I have to click around
    before it becomes responsive."* M91.4's mute lifts after `_WHEEL_QUIET_MS` of quiet, but **a
    swallowed event still refreshed the timestamp**, so the window could never elapse while events
    kept arriving — the mute was **indefinitely renewable**. Reproduced at **200 events over 4
    seconds, every one swallowed**, recovering only after a 300 ms pause; the feedback loop is what
    makes it vicious, since the instinct on finding scrolling broken is to scroll *more*.
    **Pre-existing (M91.4), surfaced by M92** — the mute block was untouched, but M92.1's 2.09×
    smaller step doubles how much spinning a document takes (the probe caught 589 events, ~51 000 px,
    in one discrete-mode burst). `_mute_still_applies` keeps the quiet-gap escape and adds two that
    **cannot be renewed**: a **ceiling of 800 ms** from when the mute was armed, and a **direction
    reversal**. 800 ms is measured — the coast tail after a hard spin is **~660 ms discrete, ~720 ms
    free-spin**, with no gap reaching 250 ms until the very end, which is exactly why the quiet test
    never fires mid-coast. **A hypothesis was disproven on the way**: discrete/ratchet mode was
    expected not to coast at all, which would have meant the mute swallowed deliberate input on a
    false premise — it coasts much like free-spin, so the premise holds and only the renewability was
    wrong. — *WSL + WSLg*
  - [x] **M92.4** **Prefetch off the scroll's critical path** — owner-reported 2026-08-01: *"with
    smooth scrolling on, scrolling tends to stall on pages with images, while the pages with texts
    glide past smoothly."* **The stall was entirely prefetch**, which is not what it looks like:
    measured across a text/image document, **visible-page rendering costs 0 ms at every zoom** — the
    reader never waits for a page they are looking at — while prefetch cost 48/101/166/356 ms at zoom
    0.91/1.5/2/3, all paid synchronously inside the scroll handler. So the stall lands one or two
    pages *before* the image page the reader blames, and prefetch was destroying the smoothness it
    exists to protect. `_render_visible` now rasterises only the visible pages and queues the margin,
    drained **one page per tick** and **never while a glide is running**, ordered direction-of-travel
    first. A/B under the real animator, counting only frames where the page is **in motion**: at zoom
    2 and 3 the over-budget frames go **3 → 0** and **5 → 0**, worst frame **41.9 ms → 0.8 ms** and
    **91.4 ms → 1.0 ms**; the same work now lands in the idle gaps. **The metric had to be fixed
    first** — a first A/B counted every frame and made the fix look *worse* (7 vs 3), because
    deferred work landing between detents was still counted. **Honest limit**: outrun the queue and
    the page you reach is rasterised synchronously, a stall like the old one but only on genuinely
    outpacing prefetch; removing that needs `PLAN.md` §Deferred item **E**. — *WSL + WSLg*
  - [x] **M92.5** **Page 1 and the last page arrive smoothly** — owner-reported 2026-08-01: free-spin
    back from page 3–4 and *"when about 70% or 80% of the first page is visible there is an abrupt
    jerky stop… this might be related to how we have implemented the ease-out."* Right on both counts.
    Every detent reset `_glide_origin` / `_glide_start`, so each one re-entered the ease-out's **fast
    opening**: velocity snapped up, decayed, snapped up. Mid-document that is invisible (the target
    keeps advancing, and the run-up is *meant* to accelerate); against a target **pinned by the clamp
    at an end**, the same shrinking distance is re-traversed and the sawtooth is all that is left.
    Replayed with the owner's own probe gap pattern, the arrival ran **129, 66, 84, 43, 54, 30, 35 px
    per frame** — speeding up twice while "stopping", squarely in the 74–90% band reported — then took
    **240 ms to crawl the final 23 px**, because 23% of a small remainder is a pixel at a time.
    `_scroll_by` now returns without restarting when the target equals the one already in flight,
    which happens *only* at the ends. After: **673 562 462 370 290 219 158 107 … 0**, monotone, top
    reached at t=496 ms instead of t=944. **Two harness mistakes are recorded in `PLAN.md`** because
    each produced a confident wrong picture first: firing a detent and ticking at the same instant
    cannot move anything (zero elapsed), and asserting that the *whole* spin decelerates is wrong —
    only everything after peak speed must. — *WSL + WSLg*
  - [x] **M92.6** **The Pages sidebar rolls continuously.** Owner-reported 2026-08-01: *"scrolling on
    the thumbnails sidebar jumps three thumbnails at a time… can't this be improved to have a
    continuous rolling of thumbnails?"*, and then, having tested the Windows slider, *"changing mouse
    setting in Windows 'Lines to scroll at a time' to 1 changed our app behavior also"* — which
    identified the second of **two wrong factors multiplied together**. Qt sets an `IconMode` list's
    `singleStep` to **one whole item**, so every "line" of the Windows setting was already a whole
    page here (measured: 245 px icon + 8 px spacing = **253 px pitch**); × the Windows default of 3 =
    759 px, which Qt then **clamps to `pageStep`** — so a detent delivered **one entire viewport, 698
    px, 2.76 thumbnails**. `ThumbnailPanel.wheelEvent` now moves `angleDelta / notch × pitch / 3`.
    **Continuous, not stepped**, because the reference the owner named is Edge — *"in Edge even
    Thumbnails move continuously, no in step of 1. So I can scroll thumbnail such that only half or a
    fraction of it is visible on the top"* — and a whole-thumbnail step would re-frame the strip
    identically at every click; a third lands on the two intermediate fractions with the page still
    the legible unit. **Independent of `wheelScrollLines`** by owner request: it is a *lines of text*
    preference and the sidebar has no text (the document view still honours it, M92.1). **Scaled by
    the measured row pitch, not a pixel constant**, because thumbnails scale with the bar — verified
    **0.331 / 0.332 / 0.332** thumbnails per detent at bar widths 150 / 210 / 276, the sidebar's
    analogue of M92.1's zoom-scaling. At the default width that is **84 px**, within a few px of the
    **87 px** the document view moves at Fit Page, so the two surfaces agree without either being
    tuned to the other. **Easing is deliberately not folded in** (`PLAN.md` §M92.6) — the animator is
    `PdfView`-owned, and whether the sidebar should glide under View ▸ Smooth Scrolling is a separate
    question with a separate cost. — *Windows (offscreen GUI)* — 13 new tests, 9 of which fail
    without the change
  - **Cost, measured before committing to it** (`PLAN.md` §M92 §Cost): per animation frame
    **~0.11–0.15 ms handler + ~0.7–1.2 ms repaint** ≈ 1 ms of a 16.7 ms budget, ~6% of one core, only
    while animating. **Flat** across zoom, DPR and content — 0.148 ms with no marks vs **0.143 ms with
    880 marks over 40 pages**. **Memory unchanged**: the band is decided by `_visible_range` + M87.1
    prefetch regardless of how the distance is crossed (79/140/197 MB resting at 1.0×/1.5×/2.0× on a
    60-page Letter doc). The one risk is a **4.1–48.3 ms synchronous page rasterise** landing
    mid-glide; a ~110 px detent rarely crosses a page boundary, which is most of why dropping the
    touchpad scope dropped the cost.
  - **The first draft of this plan led with the animation and was wrong** — it compared a *recalled*
    Chromium constant against a detent measured in a 900 px bench window (126 px vs Edge's ~120), read
    them as comparable, and concluded the easing must be the whole difference. The real window is
    1353 px tall and the real detent 183 px. Kept as the standing lesson: **measure on the machine
    that has the problem, in the window it actually opens at**, before attributing a felt difference
    to a mechanism.
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

- [x] **M102** *(unplanned)* **The redaction safety net crashed instead of firing** — found
  2026-08-17 while reading `_no_residual_match` for M100, not by a report. Its **pass 1** is the
  check that catches a *matching* bug — an occurrence the matcher never boxed, the TC-001 shape —
  and building its message read `hit['box']` when a hit has carried `boxes`, one per line, since
  [#250](https://github.com/utyagi24/klarpdf/pull/250). So it raised `KeyError` before it could
  raise `RedactionLeak`, and **`_finish` catches only `RedactionLeak`**: the wrong exception walked
  past the delete, leaving the output of a redaction that had just failed verification sitting on
  disk. "Never leave a false-secure file behind" was broken by its own error handler, on the path
  that exists for the most dangerous failure redaction has. No test caught it because every
  redaction test drives a redaction that *works*, and pass 1 is silent on those. Design in
  `PLAN.md` §M102 — *WSL*

- [ ] **M99** **A region `clip` on `render_page` and `export_images`** — scheduled 2026-08-16 from
  TC-007's capability gap, asked for twice. "Extract this ID card as a PNG" cannot be finished inside
  the server today; the page has to come out whole and be cropped elsewhere. One keyword argument at
  each of two call sites (`get_pixmap(clip=…)`), and unlike most work here it **cannot fail
  silently** — a wrong clip makes a visibly wrong image. Its best argument is one no report made:
  `search` → `render_page(clip=hit box)` lets an agent show a person the actual pixels before
  deleting them, making M95–M98's *preview before you destroy* visual rather than textual. Design in
  `PLAN.md` §M99 — *WSL*

- [x] **M100** **`queries: [...]` — one redaction call, several terms** — scheduled 2026-08-16 from
  TC-007, built 2026-08-17. The argument is **data hygiene, not ergonomics**: six identifiers took
  four chained calls and left three intermediate files, each a partially-redacted copy holding live
  PII. That sprawl is caused by our own design (every write demands a fresh `out`), which makes it
  ours to fix. It also retires an ordering hazard — chained terms had to be removed longest-first or
  fragments survived; one pass computes every box against the *intact* source, so shortest-first and
  longest-first now produce byte-identical output. **The scheduled diagnosis was right and its
  prescribed fix was wrong.** A probe reproduced the negative budget exactly (`covered=3` against
  `before=2`) and then showed the double count is **textual, not geometric** — the same *characters*
  under two boxes, not overlapping rectangles. So coalescing the boxes, as planned, would have
  unioned two boxes across a line break into a block covering everything between them and **deleted
  text neither query matched**, silently. Counting each character once instead merges no rectangles
  at all, fixes `redact_regions` for free, and corrects a pre-existing mispairing where `before`
  counted occurrences while `covered` counted distinct tokens per box. Building it re-sprang M97's
  `TYAGI1703` trap *within* a line (`Smith` + `Jones` → the token `SmithJones`), caught by M98's
  existing tests. Design in `PLAN.md` §M100 — *WSL*

- [ ] **M101** ⭐ **Annotation tools, and the highlight → review → redact round trip** — owner-asked
  2026-08-16: *"highlight all PII data in this document"* → a person reviews → *"redact everything
  highlighted in orange"*. Three tools (`annotate`, `get_annotations`, `redact_annotated`) over the
  model that already exists: `Highlight`/`Underline`/`Strikeout` carry an RGB colour and a note,
  `apply_annotations` bakes them, and `parse_annotation` reads foreign marks as well as ours (M68).
  **The shape is the point** — the agent proposes in a form that deletes nothing, a human decides in
  the app's own Annotations sidebar (M79), and the agent executes only what was approved, with the
  colour as the reviewer's verdict channel and never the tool's judgement. Design in `PLAN.md` §M101
  — *WSL*

- [x] **M98** *(unplanned)* **Redaction reports the two things it used to be silent about.** From
  TC-007 (2026-08-16), which found **no defects** — the delivery was correct with zero residuals —
  but two failure modes the tool says nothing about, both silent in the direction that matters.
  Design in `PLAN.md` §M98 — *WSL*
  - **Separator variants** (`residual_normalized`). `607347469 203 1` and `6073474692031` are one
    policy number; a literal scan sees neither in the other, so redacting one form reported the file
    clean while the other stayed in it. Dropping every non-alphanumeric character collapses the
    whole family — dates, SSNs and phone numbers come along free. **Reported, never matched**:
    whitespace-insensitive *matching* in a destructive tool would start matching across table
    columns, and whether two spellings are one value is the caller's fact. Same move as M95's
    `residual_literal`.
  - **Both guards were measured, not guessed** — 49 documents, 270 identifier-shaped queries: it
    fires on **5% of calls**, produced **41 extra hits, every one a real variant**, and every false
    positive found came from a degenerate query (`000000` matching across `708.000 0.00`). Hence a
    floor of 7 normalised characters and 3 distinct ones. The report's boundary rule also needed
    correcting: "must not sit inside a longer alphanumeric run" is vacuous on the normalised stream,
    where *everything* is alphanumeric — it has to be read against the source, via an offset map.
  - **A leak class nobody had named**: an identifier broken by a line wrap (`526-\n5999`) is
    invisible to any literal check, because the newline is a character the query does not have. Found
    while measuring the corpus; the variant scan catches it for free.
  - **Over-redaction** (`query_terms`). The default word-list mode split `607347469 203 1` into three
    terms — one of them `1` — and destroyed every standalone digit in a 22-page document, reporting
    240 boxes, zero residuals, cross-engine verified, nothing else. The asymmetry is structural: a
    missed occurrence survives in the output and can be looked for; destroyed content leaves no trace
    there at all, and the only record is the input, which is never modified. So the write is the only
    moment a warning can be given.
  - **The signal is a comparison, not a share.** "One term dominates" would fire on any two-word
    query whose second word is commoner. Comparing against what the *phrase* would have matched
    answers the real question and stays quiet on a deliberate word list (phrase never occurs) and on
    a query behaving as expected. TC-007: 240 removed against a phrase occurring 9 times.
  - **M98.1 — the floor was blunter than the risk** (retest, same day). The variant scan silently
    skipped three obviously structured identifiers: `999 99 9999` and `4444 5555` (a repeated
    character) and `AB 12 CD` (six normalised characters). The retest filed it as a bug of unknown
    mechanism, having ruled the guards out — but `1111 2222 3333` was read as disproof of the
    entropy floor while sitting exactly *on* it (3 distinct), and `AB 12 CD` fails the **length**
    floor, not the entropy one; two guards were tested as one. All eleven reported cases are
    predicted by the thresholds, so the code was doing what it was told and the instruction was
    wrong.
    **Separators are the caller declaring the value structured**, so the floor now applies only to
    unpunctuated queries. Re-measured on the same 49 documents: 36 more queries scanned, **exactly
    the same 41 hits** — no precision lost. The original probe could not have caught this: it
    generated candidates with a digit-run regex and never asked what a person would type.
    **Absence is also no longer an answer** — `residual_normalized` is always present, `[]` meaning
    the scan ran and found nothing and `null` meaning it did not, with a warning saying why. A
    feature that exists to close an invisible failure must not go quiet in a way that reads as
    reassurance. One earlier test changed on purpose: it asserted the key was *absent* when nothing
    was found, which is the contract this replaces.
  - **Not built: multi-query and region clip** — both wanted, neither a silent failure, both carried
    below with the overlap hazard that makes multi-query less thin than it looks.

- [x] **M97** *(unplanned)* **A region redaction may cover more than one line.** From TC-005
  (2026-08-16): a single `box` spanning two or more text lines **always** failed and deleted its own
  correct output, on exactly the case the tool's docs recommend region redaction for — a signature
  block, a letterhead, a table cell. Design in `PLAN.md` §M97 — *WSL*
  - **The cause is one `join`.** `text_under` concatenated every character whose centre fell in the
    box, which is right within a line and a fabrication across two: `UMESH TYAGI` + `1703 PORCELLANO
    WAY` read back as `…TYAGI1703…`. The verification then took `TYAGI1703` as a token it had
    covered, found the source contained it 0 times, and computed a budget of **−1** — so no output
    could ever pass. Lines are now separated.
  - **Two corrections to the report**, each pointing at a different fix. It read the message as
    self-contradictory (*"`0 ≤ 0` is satisfied and it fails anyway"*) and concluded either fix alone
    would do; the comparison was really `0 > −1` and the arithmetic was right — the message printed
    `max(allowed, 0)`, rendering an impossible budget as a satisfied one. Fixing only that would
    print "at most −1 expected" and still fail. It is fixed anyway, because hiding the real fault is
    what cost the reporter the time. And its suggested "cheapest correct framing" — treat one `box`
    as `boxes: [box]` — is a **no-op**: boxes are already processed one at a time, and the plural
    form worked only because each rectangle was a single line.
  - **Blast radius checked, not assumed:** every other `text_under` caller passes a single-line box
    (the annotations panel reads one rect per line bar; `matches_case` / `group_matches` see only
    `search_for` rectangles, which are already per line). Bridge-only — the app is unaffected,
    unlike M96.
  - **Part 2 declined, with reasons recorded** so it is not re-proposed from scratch: no
    `elsewhere_in_document` warning. `residual_literal` and `invisible` disclose what a caller
    *cannot* discover; this would disclose what is one `search` call away, since `verified_text`
    already lists everything the boxes removed. It would also warn on `CA` and `1` from any table
    cell. A sentence in the tool doc points at `verified_text` instead. Revisit if a real session
    shows an agent doing visual region redaction on PII and missing occurrences.

- [x] **M96** *(unplanned)* **Whole-word search stops letting the next line veto a match.** Found by
  the owner while retesting TC-003 (TC-004, 2026-08-16, report beside it in `klarpdf-tests/`).
  **Pre-existing — reproduced identically on `main`, so it is not from the M94/M95 branches.**
  Design in `PLAN.md` §M96 — *WSL*
  - **The defect.** `search "Security"` with `whole_words: true` on `ssa-3.pdf` page 1 returned
    **1 of 5**; `Social` 3 of 5; `DATE` 2 of 4. Every miss is a free-standing word with a space on
    each side, so this is nothing to do with the deliberate token semantics of TC-003 §3.
  - **A word box is not the ink.** `get_text("words")` gives each word the font's full
    ascender-to-descender height, so on tight leading consecutive lines' boxes overlap vertically
    and `boxes_touch` — a plain 2-D intersection — cannot tell a neighbour from a word the hit
    covers. `struck` was returning words from the line above: the box for `Security` at
    y=[45.2, 58.8] struck `Discontinue` at y=[35.2, 48.8]. `is_whole_word` reads the first struck
    word as the one at the hit's left edge, that neighbour's letters run left of the hit, and the
    match was dropped.
  - **One cause, not two.** The report separated a "first match per line only" symptom; it is the
    same defect — the second `DATE` sits under `DECEASED` while the first has nothing above it — so
    which occurrence survives is a fact about neighbours, not position. Recorded because "first per
    line" points at `group_matches`, which is innocent. It also explains the inversion the report
    flagged: a longer query spans a wider box and so has different edge words, which is why
    `Social Security Number` found what `Social` missed.
  - **The fix** is one predicate: a word is struck only when it shares a line with the box —
    either vertical midline inside the other's span. Precision is untouched; `Smith`/`Smithsonian`,
    `ALPHA-zero-A0` (M64) and `expression.` (TC-001) all resolve exactly as before, because those
    turn on characters *within* the struck word.
  - **This was shipped in the app too.** `viewer/search.py` shares `PageText.is_whole_word`, so
    **Find ▸ Whole words** under-reported identically — 1 of 5 on the same document. That is the
    larger half of the impact.
  - **The M95 verifier caught it**, on a defect that did not exist as a known bug when it was
    written: `redact_text` refused the write and deleted its own output, counts exact. `redact_
    regions` has no query and so no such net — the tool docs say so.
  - **Tests** use a fixture whose line boxes genuinely overlap (asserted, so it cannot silently
    stop reproducing) and fail without the fix — including one for the find-bar path.

- [x] **M93** *(unplanned)* **A save no longer throws away the document it was given.** Found while
  filling a federal form through the MCP bridge (test case TC-002, 2026-08-13), but the defect is in
  the app: `main_window.py`'s Save calls the same `materialize`, so **every document the shipped
  viewer wrote was affected**. Design + the precise guarantee in `PLAN.md` §Key design idea — *WSL* —
  ([#253](https://github.com/utyagi24/klarpdf/pull/253))
  - **What happened.** Save assembled its output by grafting pages into an *empty* document.
    `insert_pdf` copies **pages**; a PDF keeps the accessibility structure tree, `/MarkInfo`, Reader
    Extensions `/Perms`, the `/Names` tree and encryption at the **document** level, and none of it
    rides along with a page. A tagged, AES-128 SSA-3 came back untagged, unencrypted, with every
    permission granted, and with both hyperlinks rewritten into `/Launch` actions naming local files
    that do not exist. It hid perfectly: the pages and the values were correct, so the output looked
    right in every viewer.

    | | tagged | permissions | encryption | `/Launch` |
    |---|---|---|---|---|
    | input | ✅ | `-1052` | AES-128 | 0 |
    | before | ❌ | `-4` (all granted) | none | **2** |
    | after | ✅ | `-1052` | AES-128 | 0 |

  - **The fix.** Two routes, chosen by `page_set_unchanged()`: an unchanged page set edits a copy of
    the origin, anything structural still grafts. Output page `i` is `ordered[i]` either way, so
    every existing pass applied unmodified — the reason this was a small change. Encryption needed
    two more repairs: `fresh_source` was passing no encryption to `tobytes()`, which defaults to
    writing the copy **decrypted** (harmless while it was only an `insert_pdf` donor, fatal once it
    became a save's starting point), and `_encryption_args` only ever covered documents that *need* a
    password — an owner-password-restricted file has none to record, so it saved unencrypted.
  - **Restrictions survive a password change.** Found by the owner's manual pass: setting a
    password on a restricted document granted copying, modification and assembly. `_permissions`
    started at `-1` ("allow everything") and was never seeded from the file, and the password
    dialog pre-ticks its boxes from it — so every box arrived ticked whatever the document said,
    and accepting the dialog unchanged lifted the restrictions. Seeded from the origin at open, so
    the dialog now shows what the document restricts. A new `_encryption_staged` flag rides the
    undo snapshot and separates "no password set" from "password deliberately removed", which are
    the same `password is None` but must save differently. Seeding alone was **not enough**: a
    checkbox covers several permission bits, ticks when *any* of them is set and grants *all* of
    them when applied, so a document allowing annotation and form filling while denying
    modification and assembly still lost two restrictions to a dialog nobody touched. An untouched
    group now passes the document's own bits through; a deliberate toggle still means what it says.
  - **One test changed on purpose.** `test_named_links_survive_identity_save_as_goto` asserted that
    an identity save bakes named destinations into direct GoTos. That bake was a *repair* for
    `insert_pdf` dropping the name tree; with no graft there is nothing to repair, and the named
    destination now survives with the `/XYZ` view the bake discarded. It asserts where the link lands
    rather than how it is spelled.
  - **Output size.** Keeping the document means keeping more objects, which exposed something the
    save had always done: it wrote every object as a plain uncompressed dictionary and never used
    **object streams**, though most real PDFs arrive using them. `use_objstms=1` turns a 9-page
    tagged form that saved at 316 KB (against a 233 KB input) into **151 KB** — so the structure is
    kept *and* the file gets smaller, rather than being traded off against it.

- [x] **M94** *(unplanned)* **The bridge says what it knows.** The three MCP-side defects TC-002 left
  open (2026-08-13), plus the encryption case M93 stopped one door short of. Every one of them is a
  place where the server had the answer and did not hand it over — no wrong results, three guessing
  callers. Design in `PLAN.md` §M94 — *WSL*
  - **`get_info` reports the file's encryption, not the call's.** `encrypted` was
    `password is not None`, which answers *"did the caller give me a password?"*, so an
    owner-password document — AES-128, copying and modification forbidden, opens with no password —
    came back `encrypted: false` from the one tool documented as the routing call. Now `true` for
    both kinds of protection, with the algorithm named and `permissions` broken out as named
    booleans (`copy`, `modify`, `assemble`, …) rather than a bitfield. `needs_password` is what
    still separates "you cannot read this" from "you may not copy it".
  - **A user-password document had been saving back **unrestricted**, and that is what the reporting
    fix uncovered.** M93 fixed the owner-password case, which is never decrypted; a document that
    *needs* a password is decrypted at open, and `_permissions` was seeded from the decrypted copy —
    which grants everything. Measured `-1052` in, `-4` out, with the password itself carrying
    through correctly, so the file still asked for a password and then allowed what it used to
    forbid. `_authenticate_and_decrypt` now reads the algorithm and the flags between the
    authenticate and the decrypt — the one moment both are legible — and `reload_from_file`
    re-baselines from the same record, closing the third door where a redaction commit on an
    owner-password document left `permissions` reading "everything allowed".
  - **`get_form_fields` reports how to tick the box.** A button's ticked value is an appearance-state
    name and it is per widget — `"1"` on one SSA-3 checkbox, `"2"` on the next, `"Yes"` on neither —
    and `choices` cannot carry it (PyMuPDF fills `choice_values` for combo/list only). `on_state`
    and `states` now do. The boolean shorthand `fill_form` already accepted is documented rather
    than left to be discovered. `read_only` / `required` / `multiline` / `max_len` come too: the
    form carries three read-only 3–5 pt slivers that were indistinguishable from real fields.
  - **`fill_form` warns on an XFA form instead of half-filling it in silence.** An XFA form keeps a
    second copy of its values as XML; the fill updates the AcroForm widgets and leaves the
    `datasets` packet byte-identical. **Owner decision, 2026-08-15: report it, do not resolve it** —
    the result carries `xfa: {present, dynamic, datasets_updated: false}` and a warning that
    distinguishes the two cases, because a static form is correct on screen and wrong only to a
    machine reading the XFA data, while a dynamic one can look empty too. Writing into `datasets`
    and dropping `/XFA` both stay open below. The static/dynamic test is `<dynamicRender>` read **by
    value**: the SSA-3 carries it set to `forbidden`, so a presence check gets that form backwards.
  - **The "lossless" claim now matches M93's.** Server instructions, `mcp_bridge/README.md` and the
    per-tool docstrings all say the same narrower thing: an unchanged page set keeps everything, a
    reorder keeps the content and not the document structure.
  - **Tests** follow the report's own suggested fixtures: a checkbox whose export value is `"2"`
    filled by both `true` and `"2"`; an owner-password document reported and round-tripped; a
    user-password document that keeps its restrictions across a save; and static **and dynamic** XFA
    forms — the dynamic case TC-002 named as untested now has a fixture.
  - **Three defects the retest found in this milestone's own new surface** (2026-08-15), fixed here
    rather than filed — `on_state`, `states` and `read_only` did not exist before this PR, so these
    are its own loose ends. The retest otherwise closed 5 of the 7 original issues outright and
    downgraded the XFA one to low/medium now that it is disclosed.
    - **An invalid checkbox state was silently coerced to `Off` and reported as `filled`.** Now that
      `states` is published, callers pass explicit state strings — and a value matching no state
      resolved as falsy, so `"3"` (the obvious slip on a form whose states are `"1"` and `"2"`)
      **cleared** the box and reported success. That is the failure the tool already refuses for a
      field *name*, one argument short: worse than a no-op, because it writes a wrong answer.
      `fill_form` now rejects any button value that is neither a real export state nor a boolean,
      naming the states the widget accepts, and writes nothing. **`"Of"` is rejected too** — it
      lands on `Off` today, which is what the caller meant, but by luck down the same silent path,
      and one wrong input that works is what hides the other (owner decision, 2026-08-16).
    - **Read-only fields were filled silently.** Allowed — a caller may be stamping a signature
      line deliberately — but not quietly, now that the server reads the flag and reports it. The
      written read-only fields come back named in `warnings`.
    - **`states` ordering was not stable.** It came from the `/AP/N` dictionary, which a write
      rebuilds: the same SSA-3 checkbox reported `["2", "Off"]` before a fill and `["Off", "2"]`
      after one, so a field changed under a round-trip that changed nothing. Now `on_state` first,
      then the rest sorted. Verified against the retest's own before/after artifacts.

- [x] **M95** *(unplanned)* **A redaction check that can fail when the matcher is wrong.** From the
  third hands-on session (TC-003, 2026-08-15, report at
  `/mnt/c/Users/umesh/Downloads/pdfs/klarpdf-tests/TC-003-redact_text-utility-bill-pii.md`):
  redacting an account number out of a utility bill reported `matches: 2`, `residual_matches: 0`,
  `cross_engine_verified: true` — over a file that still contained the account number **twice**.
  Design in `PLAN.md` §M95 — *WSL*
  - **The leak.** `220885-1063303` appears four times: twice as plain text and twice inside
    `<AccountNumber:220885-1063303>`, a machine tag with no spaces in it. `whole_words: true` — the
    documented, natural choice for a single token — matches only the two plain ones, because a
    "word" ends at a space and the whole tag is one word. Reproduced through the real code path
    before anything was changed.
  - **The cause was not the missing check the report described; it was the check that was there.**
    `_no_residual_match` already ran a second pass over both engines' extracted text through a
    separate code path, with a docstring saying it *"owes the matcher nothing"*. It owed it
    `_word_bounded` — written to be **deliberately the same rule** as the matcher's, on the
    reasoning that two disagreeing definitions of "word" would be worse than one. Right for
    choosing what to redact; exactly inverted for a safety net, which is only worth having if it
    can fail when the matcher does. Two engines and two code paths faithfully reproduced one blind
    spot.
  - **The fix is a third pass that owes the matcher nothing and can be held to it** — the query as
    a literal substring, no boundary rule, no term splitting. It reports `residual_literal` and
    names each survivor in `warnings`; the TC-003 call now comes back with both tags named and
    "re-run with `whole_words: false`".
  - **It warns and never deletes**, deliberately: redacting whole-word `Smith` correctly leaves
    `Smithsonian`, which literally contains the query, and failing on that would destroy a good
    output (pinned by `test_a_legitimate_survivor_is_not_mistaken_for_a_leak`). The reported
    **token** is what separates the two at a glance — `'Smithsonian'` reads as fine,
    `'<AccountNumber:…>'` does not — so the tool surfaces the evidence rather than the verdict.
  - **Invisible text is flagged, and colour alone could not have done it.** The two survivors were
    10 pt white-on-white at the page margins — live to `get_text`, absent from every render, so a
    human approving the redaction by comparing before/after sees nothing either way. The report
    proposed matching the fill colour against the background; **measured, that fires 21 times on
    this page and is right twice** — 19 of the white spans are ordinary table headers on dark
    banners. So colour is only a pre-filter and the box is rendered to see whether anything was
    drawn: contrast **1** for the two invisible tags against **163–215** for all 19 legible
    headers. `search` hits carry `invisible`; `redact_text` reports `invisible_matches`.
  - **`whole_words: true` means whole *token*, now stated.** The behaviour is deliberate (M64:
    `ALPHA-zero-A0` is one word) and was documented in the code while the tool docs said only
    "matched whole" and steered single tokens toward that mode. Corrected in `server.py` and
    `mcp_bridge/README.md`, with the shapes it bites on named.
  - **Cost**, measured on the 320-page `spaceX_prospectus.pdf`: `search` **+7–12%** — one extra
    `get_text("dict")` per hit page, and a pixmap only for pale candidates. The pathological
    one-letter query 4.60 s → 5.04 s.
  - **Tests** are the report's four suggested fixtures, including the one it asked for by name:
    *a deliberately broken matcher must fail verification, not pass it.*

## Public-Release Readiness — go open-source under AGPL-3.0 (planned)

**The repo is public** as of **2026-07-17**, as an `AGPL-3.0-or-later` project — the flip (G8) is done.
**G1–G5, G7 and G8 are complete; `G6 Part 2` (enrol in GitHub Sponsors) is the only item left**, and
nothing depends on it. Independent of the v0.11.0 MCP roadmap — this track landed first. Full
execution detail in `PLAN.md` §Public-release readiness (plan introduced in
[#83](https://github.com/utyagi24/pdfproj/pull/83)). **One PR per item**; tick the box on merge and
append the PR link. Steps were ordered — **G1 ran first, while the repo was still private**, and the
flip itself was a manual GitHub action, not a PR. The pre-public hygiene scan was clean (no secrets in
tree or history; `.gitignore` excludes build artifacts/wheels/`report.json`; CI uses `${{ secrets.* }}`).

- [x] **G1** Commit-author cleanup (**done** — history rewrite, no PR) — `git filter-repo` mailmap
  remapped the maintainer's personal email (162 commits) **and** the older bare-form no-reply (80
  web-commit authors) onto the canonical `<id>+username@users.noreply.github.com`, author + committer;
  content byte-identical (trees unchanged); `main` + all 15 release tags force-pushed; verified **0**
  personal-email / bare-form authors remaining and all Releases intact. Done first, while private. — *WSL/Windows*
- [x] **G2** Branding — name + logo (**done** — all three parts). **Decision gate: closed.** The product is **KlarPDF** (*klar* =
  "clear" in German / the Scandinavian languages); `pdfproj` was the dev codename. An earlier pick,
  *sheaf*, was dropped for clashing with existing GitHub PDF-processing projects — the marks were drawn
  under that name, hence the design-source title in `assets/brand/BRAND.md`. Name mapping: display
  string **`KlarPDF`** (window title, About, installer AppName) · drawn wordmark lowercase `klarpdf`
  (BRAND.md §Type) · repo + exe + `%LOCALAPPDATA%` leaf + single-instance id `klarpdf` · ProgID
  `KlarPDF.Document`.
  - [x] **Part 1 — visual assets** — toolbar glyph set (24 replaced + 3 new: `about`, `donate`,
    `export`), app mark, regenerated `packaging/klarpdf.ico`, and `assets/brand/` (tokens + `BRAND.md`
    + icon spec). No code changes; icon filenames unchanged. — *WSL* —
    [#91](https://github.com/utyagi24/pdfproj/pull/91)
  - [x] **Part 2 — the name sweep** — app strings (`app.py`, window title, `platform_integration.py`
    single-instance id, `store/settings.py` `%LOCALAPPDATA%` leaf); `PDFPROJ_AUTHOR` → `KLARPDF_AUTHOR`
    with the tag **value** `klarpdf`, and its five `*_klarpdf_annotations` helpers; asset filenames
    (`klarpdf.svg` / `.ico` / `.spec`); `installer.iss` AppName/Publisher/ProgID + **a fresh `AppId`
    GUID**; `build.ps1`, `release.yml`, `pyproject.toml`, `tasks.py`, tests, docs. **No back-compat
    shims**: the app has never been distributed (single user), so the settings dir and the annotation
    tag change outright rather than carrying a migration. The fresh `AppId` stops Inno treating the
    renamed setup as an in-place upgrade (which would skip the old uninstaller's registry +
    config-dir cleanup and reuse its install dir) — **uninstall `pdfproj` before installing
    `KlarPDF`** (`RELEASE.md`). Historical release notes, shipped artifact names and repo URLs are
    left as-is: they record what actually shipped. — *WSL* — [#92](https://github.com/utyagi24/pdfproj/pull/92)
  - [x] **Part 3 — GitHub repo rename** (**manual**) — `gh repo rename klarpdf` (**not**
    `gh repo edit --rename`, which doesn't exist), run while the repo was still private.
    `utyagi24/pdfproj` → `utyagi24/klarpdf`; GitHub redirects the old URLs, so the historical
    release/PR links above keep resolving, and PRs/issues/releases are untouched (a *repo* rename is
    safe — it is a *branch* rename that closes an open PR, see #86). Re-pointing each checkout's
    `origin` is **optional** — GitHub redirects the old remote over **both** HTTPS and SSH — but if you
    do it, **keep the checkout's existing protocol**: the Windows checkout is HTTPS
    (`https://github.com/utyagi24/klarpdf.git`), the WSL checkout is SSH
    (`git@github.com:utyagi24/klarpdf.git`). Rewriting an SSH remote to the HTTPS form makes git ignore
    `~/.ssh/config` and start prompting for a password that cannot work (password auth was removed in
    2021). Local working-directory names deliberately keep the old codename. — *GitHub*

  Feeds the copyright name (G3), the About name+logo (G4), and the community files (G5).
- [x] **G3** License + notices — root `LICENSE` (full AGPL-3.0-or-later) + `THIRD_PARTY_LICENSES`
  (PyMuPDF AGPL-3.0, PySide6 + shiboken6 LGPL-3.0, pypdf BSD-3; cross-ref `DEPENDENCIES.md`) +
  README license section + badge + build-from-source pointer (uses the G2 name) — *WSL* — [#95](https://github.com/utyagi24/klarpdf/pull/95)
- [x] **G4** In-app About + Open-Source Licenses dialog — Help menu (`main_window.py`) → **About**
  (mark + version + AGPL + the AGPL §15-16 no-warranty notice + a *tagged* corresponding-source link,
  never `main`), **Open-Source Licenses** (the bundled texts, one tab each, offline), **View Source**.
  New `ui/about.py` (dialogs) + `util/resources.py` (freeze-aware `resource_path()`, mirroring
  `ui/icons.py`'s `_MEIPASS` dance); `packaging/klarpdf.spec` `datas` ships `LICENSE` +
  `THIRD_PARTY_LICENSES` to the bundle root. Links open via `QDesktopServices` on **user click only**,
  so the offline / no-telemetry guarantee holds. `tests/test_about_dialog.py` drives the real Help
  menu and simulates `sys._MEIPASS` — the frozen path the headless suite otherwise never executes —
  and asserts the spec still bundles both texts, since a `datas` regression is invisible to CI.
  — *WSL + WSLg* — [#97](https://github.com/utyagi24/klarpdf/pull/97)
- [x] **G5** Community-health files — `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant), `.github/ISSUE_TEMPLATE/*` + `pull_request_template.md`
  — *WSL* — [#96](https://github.com/utyagi24/klarpdf/pull/96)
- [x] **G5.1** Governance — **open source, closed to PRs** (decided after G5 landed; rationale in
  `PLAN.md` §Public-release readiness). **Issues open to everyone** (bugs / security / feature
  requests); **PRs restricted to the maintainer + invited collaborators**, others auto-closed by
  `.github/workflows/close-external-prs.yml` (`pull_request_target`, *no* checkout — that trigger runs
  with write access, so it must never execute a fork's code). DCO 1.1 is now **deemed accepted** on
  submission: no `Signed-off-by`, no CI check — G5 had claimed unsigned PRs "cannot be merged", which
  nothing enforced. **Open decision:** the DCO grants no rights, so the *first* merged contribution by
  anyone other than the maintainer — a collaborator included — forecloses commercial relicensing
  (PLAN.md's Artifex hatch). A CLA or explicit relicensing grant must be settled **before** a
  collaborator's first merge. **Repo settings (manual):** keep Issues **on**; enable private
  vulnerability reporting; Wiki/Projects/Discussions off; **no interaction limits** (they would block
  the public from opening issues, defeating the policy). — *WSL + GitHub settings* —
  [#98](https://github.com/utyagi24/klarpdf/pull/98)
- [ ] **G6** Donations — repo + product — let users support the project. **Platform: GitHub Sponsors**
  (decision gate closed) — same host as the source link, so supporting the project introduces no
  third-party domain into the app or the repo. Open-source + donations is fully AGPL-compatible.
  - [x] **Part 1 — the code** — `.github/FUNDING.yml` (`github: utyagi24`); README **Support** section
    + Sponsor badge; **Help ▸ Donate…** (extends the G4 Help menu, grouped with *View Source* — the
    separator splits "opens a dialog" from "hands a URL to the browser") + an About-dialog link, both
    via `QDesktopServices.openUrl` on **user click only, so the offline / no-telemetry guarantee holds**
    (the app opens no socket itself). A test asserts `FUNDING.yml` and `ui/about.py` name the *same*
    account — see the trap below. — *WSL + WSLg* — [#107](https://github.com/utyagi24/klarpdf/pull/107)
  - [ ] **Part 2 — enrol the account in GitHub Sponsors (manual; the actual gate)** — Stripe +
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
- [x] **G7** Lock-in identity, hygiene & branch rulesets — keeps the G1 scrub true, permanently. Three
  parts: repo-side (one PR), manual identity, and the ruleset. The ruleset was *decided and
  pre-authored* here and **completed at G8**, where its premise turned out to be wrong: G7 recorded
  that a ruleset *cannot exist* while private (a 403 on `GET .../rulesets`), but two were already
  active — the 403 was the **API**, not the rulesets. See Part 3 and G8.
  - [x] **Part 1 — repo side** — `.gitignore` gains `*.pfx *.pem *.key .env *.log` (nothing matches
    today; `*.pfx` is the live one — Authenticode signing is a carried follow-up and a cert lands in the
    tree as exactly that). New `.github/workflows/author-email-guard.yml` fails a PR whose author (or
    committer) email is not a GitHub no-reply — the backstop under the local `user.email` and the
    account-level push block, both of which are per-machine/per-account state a fresh clone or a new
    machine silently loses. `test.yml` loses the `paths-ignore` on its **`pull_request`** trigger and
    gains an in-job docs-only gate, so the `pytest` check reports on every PR without running the suite
    on markdown — the prerequisite for requiring it below (rationale + the two-workflow trap it avoids:
    `PLAN.md` §Public-release readiness). — *WSL* — [#106](https://github.com/utyagi24/klarpdf/pull/106)
  - [x] **Part 2 — identity (manual, per machine + account)** — **all four verified.** `git config
    user.email` = the no-reply on **both** checkouts: **Windows** (local + global =
    `12071588+utyagi24@…`) and **WSL** (owner-verified — the two checkouts are bridged only by git, so
    each is its own machine). GitHub account ▸ Emails: **Keep my email addresses private** ✅ +
    **Block command line pushes that expose my email** ✅ (owner-verified in the UI — the first needs a
    `user` API scope this checkout's `gh` lacks, the second has **no API at all**, so neither is
    machine-checkable from here; the public profile `email` field now reads empty, which is consistent).
    With the push block on, the *server* now rejects an exposing push — Part 1's `emails` workflow
    remains the backstop for what these per-machine/per-account settings cannot cover (a fresh clone,
    a new machine, a changed account).
  - [x] **Part 3 — the `main` ruleset, decided + pre-authored** (**reconciled** at G8 — see the
    correction there). Payload lives at [`.github/rulesets/main.json`](.github/rulesets/main.json)
    with the rationale beside it, so the rules are reviewable in a diff rather than clicks made once
    in the UI. In short: **block force-push** + **restrict deletions** + **require the `pytest` and
    `emails` checks**, **empty bypass list**; *require review* dropped while solo (G5.1), *linear
    history* rejected (the project merges with merge commits), *signed commits* deferred (unsigned
    today; needs GPG/SSH for the no-reply identity first). Full rule-by-rule reasoning: `PLAN.md`
    §Public-release readiness. **This part's premise was wrong, corrected at G8:** it recorded that a
    ruleset *cannot exist* before the flip, from `GET /repos/utyagi24/klarpdf/rulesets` → **403
    "Upgrade to GitHub Pro or make this repository public"**. The 403 was about the **API**, not the
    rulesets — two were already active. — *WSL* — [#107](https://github.com/utyagi24/klarpdf/pull/107)
- [x] **G8** Flip to public (**manual; not a PR**) — **done 2026-07-17. The repo is public.**
  (`gh repo edit --visibility public --accept-visibility-change-consequences`; the second flag is
  **required**, `gh` refuses `--visibility` without it.) Every item in G8's own scope is complete and
  verified; the Sponsors-listing check that used to sit here is G6's and moved there. Docs landed in
  [#111](https://github.com/utyagi24/klarpdf/pull/111) + [#112](https://github.com/utyagi24/klarpdf/pull/112)
  — the settings themselves are not a PR.
  - [x] **Private vulnerability reporting** — enabled; `GET
    repos/utyagi24/klarpdf/private-vulnerability-reporting` → `{"enabled": true}`. Flip-gated (404s on
    a private repo — public-repo-only). Until it was on, `/security/advisories/new` 404'd — and that
    URL is the *only* reporting channel `SECURITY.md`, `CODE_OF_CONDUCT.md`,
    `.github/ISSUE_TEMPLATE/config.yml` and the auto-close workflow's comment advertise.
  - [x] **Secret scanning + push protection** — both `enabled`. Also flip-gated (they need paid GHAS
    while private; free once public).
  - [x] **Dependabot security updates** — enabled at G8, **turned back off on 2026-08-11**
    (`DELETE repos/utyagi24/klarpdf/automated-security-fixes`; verify → `{"enabled": false}`).
    **Not in the original G8 list**; it was added here because it is free on a public repo and looked
    like the mechanical version of the pypdf advisory (**GHSA-jm82-fx9c-mx94**) caught by hand in
    v0.9.4. That reasoning was **wrong**, and enabling it silently contradicted `RELEASE.md` §2 for a
    month — see the settled follow-up below. **Dependabot alerts stay on**
    (`GET .../vulnerability-alerts` → 204); alerts are the half §2's flow actually consumes.
  - [x] **The `main` ruleset — reconciled, not created.** G7's premise was **wrong**: it recorded that
    rulesets cannot exist before the flip (`GET .../rulesets` → 403 *"Upgrade to GitHub Pro or make
    this repository public"*). That 403 was the **API** being unavailable on a private free repo, not
    an absent ruleset — **"Protect Main"** (id 18233952) and **"Protect Tags"** (id 18234032) had been
    active since **2026-06-28**. So `deletion` + `non_fast_forward` were already in place and the only
    rule G8 actually added was **`required_status_checks`: `pytest` + `emails`**, `PUT` into the
    existing ruleset rather than `POST`ed as a second overlapping one. Its pre-existing `pull_request`
    rule (0 approvals) was **kept** — it enforces `CLAUDE.md`'s "never leave edits on `main`", and G7
    only ever meant to decline required *reviews*. `main.json` is now a **mirror** of the live
    ruleset. Verify: `gh api repos/utyagi24/klarpdf/rulesets/18233952 --jq '.rules'`.
    **Likely-but-unconfirmed:** a private free repo *creates* rulesets without *enforcing* them (it
    fits the 403, and G1's force-push succeeding weeks after `non_fast_forward` was listed) — so `main`
    plausibly acquired real protection only at the flip. `PLAN.md` §Public-release readiness.
  - [x] **The two dynamic README badges went live** — verified: `tests: passing`, `release: v0.10.1`.
    They read the GitHub API, so while private they rendered *"repo or workflow not found"* and
    *"inaccessible"*; the flip fixed both with no edit.
  - ~~Add repo description/topics~~ — **done ahead of the flip** (needs no public repo): description
    set + 13 topics. Nothing to do here at G8.
  - [x] **Upload the social preview** (**manual — the one G8 step a human had to click**; there is no
    REST API for it) — Settings ▸ General ▸ Social preview ▸ `assets/brand/social-preview.png`.
    **Done + verified**: the repo's `og:image` meta now resolves to `repository-images.
    githubusercontent.com` (a custom upload; the default would be `opengraph.githubassets.com`) and the
    served bytes are **sha256-identical** to `assets/brand/social-preview.png`, 1280×640. This is what
    renders when the repo link is pasted anywhere. Re-check with:
    `curl -sL https://github.com/utyagi24/klarpdf | grep 'og:image'`.
  - ~~Check the Sponsors listing is live~~ — **moved to G6 Part 2**, where it belongs: it verifies
    *G6's* deliverable, and was only parked here because the Sponsor button also needs a public repo
    — a condition now permanently satisfied. Leaving it here would make G8's status a lagging shadow
    of G6's, which is exactly the duplication `CLAUDE.md` §"update in exactly one place" forbids. — *GitHub*

## Open follow-ups (carried)

Carried items — none block work:

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
  **Not yet designed**, deliberately: `rotation` per `page_sizes` group, unrotated dimensions, or
  both would each serve, and the choice is about what `get_info` promises. Not scheduled. — *WSL*

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
  `model/page_text.py` and shared). Nothing carried.

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
  Worked around in `model/links_remap.py`: the materialise link pass re-adds any URI link
  `insert_pdf` dropped, with the text pre-escaped (round-trips correctly). Consider reporting
  upstream to PyMuPDF; if fixed there, the restore pass simply finds nothing missing.

- **A stale `vendor/wheels/` silently shadows the lock in `-Offline` builds.** Found while building
  v0.10.0: the local cache still held `pypdf-6.13.2` (the wheel the v0.9.4 security bump replaced), so
  `build.ps1 -Offline` failed with *"Could not find a version that satisfies pypdf==6.13.3"*. The repo
  was correct (`requirements-win.txt`, `vendor/wheels-sources.md` both say 6.13.3); `vendor/wheels/` is
  **gitignored**, so it drifts per-machine and never gets re-vendored by a `git pull`. CI is unaffected
  (it fetches fresh). Fix by re-running `build.ps1` **without** `-Offline` once, then re-running with
  it. Worth a guard in `build.ps1` that diffs the cache against the lock before an offline build.
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
  `PdfReader` in `model/edit_engine.py`, so a crafted PDF is the attack surface. The weekly `audit`
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

- **Help ▸ Donate… points at a GitHub Sponsors listing that does not exist.**
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
  `util/atomic.py:atomic_replace` retries `PermissionError` (WinError 5 / 32) four times over ~0.75 s
  before giving up, and both write sites — `_write_to` (Save / Save As) and `_export_pdf` (every
  Export) — go through it. Only lock contention is retried; `FileNotFoundError` and a cross-device
  `OSError` still fail on the first attempt. Kept here rather than deleted because the *diagnosis* is
  the durable part: if a "Save failed" ever returns, it is no longer this.

- **`MarkupStyleButton.style()` shadows `QWidget.style()`** (it returns the `MarkupStyle`
  dataclass). Harmless in paint — Qt calls the C++ method — but any Python-level `button.style()`
  gets the wrong object, and it already cost one debugging detour in M59.13 (workaround:
  `QWidget.style(btn)`). Rename to `markup_style()` when that file is next touched.
