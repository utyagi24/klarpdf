# Release notes, before the current release

The current release is at the top of [`PROGRESS.md`](../PROGRESS.md). Each release's notes move here when the next one ships.

**v0.20.0** — **structure an agent can use, and a window that keeps up**
(M138–M157). A **minor** under `RELEASE.md` §3's SemVer rule: new tools and app features, no
breaking change. Release: <https://github.com/utyagi24/klarpdf/releases/tag/v0.20.0>.

**(1) The bridge reads a document's structure and writes it back: 19 tools become 23.**
`get_links` (**M138**) returns every link a PDF carries. A document with no bookmarks often has a
printed contents page made of links that already name each entry's title, page and level (591 of
them on the owner's `WH-1000XM6.pdf`). `set_outline` (**M139**) writes that structure back as real
bookmarks, and since **M150.2** each entry can carry a `top`, so a bookmark lands on its heading
rather than the top of its page. `get_heading_candidates` (**M140**) lists the lines set to stand
out from the body text, for documents with neither. `get_tables` (**M141**) returns tables checked
against their page: a table the page contradicts is declined with the reason, not guessed at.
**M145** makes a table's `title` the caption the page sets over it, or `null`: 0 wrong on 105
tables judged by eye, where the old rule got 16 wrong. **M148** stops a link's underline starting a
table. Testing them found outline defects in the page tools, fixed in **M138.1–M138.5**: `merge`
now keeps every document's outline, not only the first one's (one report lost all 223 of its
bookmarks), and bookmarks no longer come out of a page move pointing nowhere. **M143**: `install.py` asks which scope you meant instead
of guessing.

**(2) In the app.** Links and bookmarks land on the spot they name, not the top of the page. A page
move now keeps every destination byte-identical; across the test corpus a save used to flatten
1,526 link positions and 689 bookmark positions (**M150.1**). Zoom and resize keep the page you are
reading (**M151**). **A slow page no longer freezes the window** (**M152**, the 1.0 gate's Item E):
it is drawn in pieces, and on the NADA cover the longest pause is about 0.1 s, where a zoom to 300%
froze the window for 7.5 s. Web links open in the browser, a file that cannot be opened says so
instead of crashing, and the window has a minimum size (**M149**). A style change on several shapes
changes only that setting (**M154**), and a filled shape can have no border (**M155**). On WSL,
lists and menus no longer stay on the desktop after they close (**M153**).

**(3) Dependencies.** In the bridge, `pyjwt` 2.13.0 → 2.14.0 (**M157**, which fixes ten advisories, one of them critical) and
`httpx2` 2.10.0 → 2.12.0 (**M144**, three advisories). Neither was reachable, since the bridge
talks over stdio and makes no HTTP request, but the wheel on PyPI pins its dependencies exactly.
**pypdf leaves the installer** (**M156**): it served a fallback engine that nothing ever used.

**1.0 is still deliberately not taken.** Item E and the Donate link are done; the clean-machine
install and the flaky `test_single_instance` remain (§Toward 1.0, below).

2,991 headless tests green on Windows (9 expected skips under PowerShell — see `RELEASE.md` §3).

**v0.19.0** — **the bridge you can actually install (M133–M136)**. Installing
it was nine commands: clone, check out a tag, make a virtualenv, activate it, two `pip` lines, then
find the path by hand — and the step that failed was the path. It is now **one**:

```bash
pipx install klarpdf            # or: uv tool install klarpdf
```

and for a machine with neither, a single downloaded **`install.py`** that needs nothing but a
supported Python — no clone, no `uv`, no `pipx`, no global `pip`. **`klarpdf` is published to PyPI**
with all 29 dependencies pinned exactly, so every install route resolves the set CI tests and the
weekly audit scans, rather than whatever is newest that day. Publishing is Trusted Publishing (OIDC)
off the `release: published` event, so **no API token exists anywhere** and the manual smoke test
that already gated the GitHub Release gates PyPI too. Underneath, `packaging/` now says which product
each file builds (M133) and the wheel installs **one** top-level name instead of four (M134) —
`model`, `util` and a module called plain `version` would otherwise have collided with anyone else's.
Also shipping: **M137**, the dev lock must be compiled off Windows, and a **pypdf 6.15.0 → 6.17.0**
security bump clearing three crafted-PDF parse-DoS advisories. A **minor** under `RELEASE.md` §3's
SemVer rule: new user-facing capability, no breaking change.

**v0.18.0** — **the MCP / Agent Bridge (M39–M44)**, the roadmap's last
unticked box, plus the annotation, save-cost and markup work that landed alongside it. A **minor**
under `RELEASE.md` §3's SemVer rule: a new user-facing component, no breaking change.

**(1) The bridge is complete and verified.** `klarpdf/mcp_bridge/` exposes the same PDF engine the app uses
to **Claude Code, Claude Desktop** and other agentic clients as a local MCP server — **19 tools**
across read (`get_info`, `get_outline`, `search`, `extract_text`, `render_page`, `get_form_fields`,
`get_annotations`), lossless transform (`split`, `merge`, `reorder`, `delete_pages`, `extract_pages`,
`rotate`, `fill_form`, `flatten`, `export_images`), destructive redaction with cross-engine leak
verification (`redact_text`, `redact_regions`), and markup (`annotate`). Every write tool takes an
explicit output path and leaves its input **byte-identical**; the server makes **no network
connections** and imports **no Qt**, both asserted by tests that run every tool in a fresh
interpreter. It is a **separate, optional component** — `klarpdf-setup-x64.exe` is unchanged: same
size, same hashed offline lock, same clean-machine install. Nobody installing the app gets any of it.

**(2) M44's verification pass found four defects that no automated check could see** — the argument
for doing it before the tag rather than after. The `.mcpb` **could not be built on Windows at all**
(**M127**: `subprocess` with a list argv cannot start `npx.CMD`, and the bundle had only ever been
built in WSL); it declared **`Python >=3.12,<3.13`, which no Python can satisfy** (**M128**: PEP 440
syntax in a node-semver field, so a comma where a space belongs); and the runbook's own row 10
instructions **did not put the lock in the bundle** (**M129**), which would have produced a confident
wrong answer to a question carried since M42. That question is now answered: **`uv run --directory`
honours a committed `uv.lock`**, so the bundle ships one and a Desktop install resolves from a hashed
file we wrote and review.

**(3) Alongside it, in the app.** **M101** adds annotation as a capability rather than a redaction
pre-step. **M116/M117** make adding a highlight **append 1,865 bytes instead of rewriting 8.8 MB**,
then stop the append from rewriting every mark the file already carried. **M110/M111** cut a save on
an object-heavy document from minutes to ~1.9 s and stop Reduced-Size PDF returning a file *bigger*
than a plain Save. **M120** fixes a rectangle or ellipse silently **changing size on every save**
unless its outline was exactly 2 pt. **M121** leaves Insert Blank Page looking at the page you made.

2,401 headless tests green (7 expected skips on Windows / 3 under Git Bash — see `RELEASE.md` §3).

**v0.17.1** — a **security patch plus the last M92 fix**, superseded by v0.18.0 above.

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
(`klarpdf/model/content_marks.py`) underlies stamps, signatures and watermarks — two descriptors, `Stamp`
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

Done, each with its record in its own file: [M0](milestones/M0.md), [M1](milestones/M1.md), [M2](milestones/M2.md), [M3](milestones/M3.md), [M4](milestones/M4.md), [M5](milestones/M5.md), [M6](milestones/M6.md), [M7](milestones/M7.md), [M8](milestones/M8.md), [M9](milestones/M9.md).

⭐ M1 is the keystone — most correctness risk, GUI-free, fully testable in WSL/CI.

## Releases — v0.2.0 ✅ → v0.3.0 ✅ → v0.4.0 ✅ → v0.5.0 ✅ → v0.6.0 ✅ → v0.7.0 ✅ → v0.8.0 ✅ → v0.9.0 ✅

Spec + architecture in `PLAN.md` (§Shipped roadmap for v0.2–v0.4, §Next roadmap for v0.5–v0.9). Same
conventions: **one PR per milestone**, tick the box here on merge. ⭐ marks a keystone (most risk,
GUI-free core, fully headless-testable).

**v0.2.0 ✅ — "Polish, Print & Forms"** (shipped)

Done, each with its record in its own file: [M10](milestones/M10.md), [M11](milestones/M11.md), [M12](milestones/M12.md), [M13](milestones/M13.md), [M14](milestones/M14.md), [M15](milestones/M15.md).

**v0.3.0 ✅ — "Interaction & Drag-and-Drop"** (shipped)

Done, each with its record in its own file: [M16](milestones/M16.md), [M17](milestones/M17.md), [M18](milestones/M18.md), [M19](milestones/M19.md).

**v0.4.0 ✅ — "Annotate & Redact"** (keystone release, shipped)

Done, each with its record in its own file: [M20](milestones/M20.md), [M21](milestones/M21.md), [M22](milestones/M22.md).

**v0.5.0 — "File Safety & Output"** (planned)

Done, each with its record in its own file: [M23](milestones/M23.md), [M24](milestones/M24.md), [M25](milestones/M25.md), [M26](milestones/M26.md).

**v0.6.0 ✅ — "Rich Text & Live Preview"** (shipped)

Done, each with its record in its own file: [M27](milestones/M27.md), [M28](milestones/M28.md), [M29](milestones/M29.md), [M30](milestones/M30.md).

**v0.7.0 ✅ — "Round-trip & Export"** (shipped)

Done, each with its record in its own file: [M31](milestones/M31.md), [M31.5](milestones/M31.md), [M34](milestones/M34.md).

> Re-scope (owner, 2026-06-20): encrypted-PDF (M32) + internal-link remap (M33) moved **out of
> v0.7.0** to a new **v0.9.0**, so the image work (v0.8.0) ships next.

**v0.8.0 ✅ — "Images"** (shipped)

Done, each with its record in its own file: [M35](milestones/M35.md), [M36](milestones/M36.md), [M37](milestones/M37.md).

> Pre-release polish (owner, 2026-06-20): clearer multi-page selection in the sidebar + vertically
> centred fitting page ([#52](https://github.com/utyagi24/pdfproj/pull/52)) and vertically centred
> text-box text ([#53](https://github.com/utyagi24/pdfproj/pull/53)).

**v0.9.0 ✅ — "Encrypted & Links"** (shipped; re-scoped out of v0.7.0)

Done, each with its record in its own file: [M32](milestones/M32.md), [M33](milestones/M33.md), [M38](milestones/M38.md).
