"""Write-side PDF operations for the MCP bridge — split, merge, reorder, rotate, fill, flatten.

The same split as :mod:`mcp_bridge.queries`: plain Python over ``model/``, no SDK import, JSON-ready
returns. Every function here drives a :class:`~model.virtual_document.VirtualDocument` exactly the
way the GUI's own Save does and then materialises it — object-level page copy, per-page edits,
outline remap and internal-link remap all come from the shared engine rather than from anything
written here. `tests/test_mcp_transforms.py` asserts the same invariants as
`tests/test_materialize.py`, against the same fixtures.

That shared engine is also what bounds the word **lossless** here, and the bound is worth stating
because the tool docs used to overstate it (TC-002 ISSUE 2). Content — text layer, annotations,
form fields, bookmarks, internal links — always survives. Everything a PDF keeps at the *document*
level, the structure tree included, survives only when the page set is unchanged, because moving
pages means grafting a new document and pages do not carry it. PLAN.md §Key design idea, M93.

**The safety model (PLAN.md §Safety model — agent-driven means untrusted caller):**

* **The source is never written to.** Every function takes an explicit ``out`` path, in-place save
  is not exposed at all, and :func:`_resolve_out` refuses an output that resolves to the same file
  as any input — through symlinks and Windows case-folding, since ``util/paths.py:normalize_path``
  is the project's single identity chokepoint and a string compare would miss both.
* **Existing files are not clobbered** unless the caller passes ``overwrite=True``. PLAN.md only
  required protecting the *source*; refusing to silently overwrite an unrelated file is the same
  argument applied consistently, and an agent that meant it can say so in one word.
* **Writes go to a sibling temp and are renamed into place** — through the same
  :func:`util.atomic.atomic_replace` the GUI's Save uses (M38.5), so a transient antivirus lock on
  the fresh temp cannot fail a write that would succeed a moment later, and a failure leaves nothing
  half-written for the caller to read back as a corrupt PDF.
"""

from __future__ import annotations

import os
import tempfile

from model.edit_engine import PyMuPDFEngine
from model.virtual_document import PageRef, VirtualDocument
from mcp_bridge.queries import open_document, resolve_pages
from util.atomic import atomic_replace
from util.paths import normalize_path

# Rotation is stored as a multiple of 90 (PDF /Rotate); anything else is a caller mistake.
_QUARTER_TURN = 90


def _resolve_out(out: str, *, sources: list[str], overwrite: bool) -> str:
    """Validate an output path against the inputs and the filesystem. Returns the absolute path.

    The identity test goes through ``normalize_path`` — the project's one chokepoint for "are these
    the same file" — so a symlink, a ``..`` segment or a case-different spelling on Windows cannot
    smuggle the source in as the destination.
    """
    absolute = os.path.abspath(out)
    if os.path.isdir(absolute):
        raise ValueError(f"{out!r} is a directory; give a file path for the output")
    key = normalize_path(absolute) if os.path.exists(absolute) else os.path.normcase(absolute)
    for source in sources:
        if key == normalize_path(source):
            raise ValueError(
                f"refusing to write over the input document ({source!r}) — "
                "transforms always write to a new file"
            )
    if os.path.exists(absolute) and not overwrite:
        raise ValueError(f"{out!r} already exists; pass overwrite=true to replace it")
    parent = os.path.dirname(absolute) or "."
    if not os.path.isdir(parent):
        raise ValueError(f"the output directory {parent!r} does not exist")
    return absolute


def _write(vdoc: VirtualDocument, out: str, writer=None) -> None:
    """Materialise ``vdoc`` to ``out`` via a sibling temp file, then rename it into place.

    ``writer(vdoc, tmp)`` overrides the default materialise for the derived exports (flatten).

    The rename goes through :func:`util.atomic.atomic_replace` (M38.5), the same helper the GUI's
    Save and Export use: on Windows the rename needs exclusive access to both paths, and an
    on-access antivirus scanner holding the just-written temp is enough to fail a write that would
    succeed 200 ms later. The two write paths deliberately do not diverge on this.
    """
    directory = os.path.dirname(out) or "."
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=directory)
    os.close(fd)
    try:
        if writer is None:
            PyMuPDFEngine().materialize(vdoc, tmp)
        else:
            writer(vdoc, tmp)
        atomic_replace(tmp, out)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _result(out: str, vdoc: VirtualDocument, source: str, **extra) -> dict:
    """The shape every transform returns: what was written, and the promise about the input."""
    return {
        "out": out,
        "pages": vdoc.page_count,
        "bytes": os.path.getsize(out),
        "source": os.path.abspath(source),
        "source_unchanged": True,
        **extra,
    }


# ---- page-set transforms ------------------------------------------------------


def delete_pages(
    path: str,
    pages: list[int],
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Write a copy of ``path`` without ``pages`` (1-based). Bookmarks to deleted pages are dropped
    and the survivors re-point at their new positions."""
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        if len(indices) >= vdoc.page_count:
            raise ValueError("refusing to delete every page — the result would be an empty document")
        vdoc.delete_pages(indices)
        _write(vdoc, target)
        return _result(target, vdoc, path, deleted=sorted(p for p in pages))


def reorder(
    path: str,
    order: list[int],
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Write ``path``'s pages in the sequence ``order`` (1-based, a full permutation).

    A full permutation rather than a move-this-there operation: it is the form an agent can state in
    one call and verify by reading back, and it makes "every page is accounted for" checkable —
    which is the property that stops a reorder from quietly dropping a page.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, order)
        if len(indices) != vdoc.page_count:
            missing = sorted(set(range(1, vdoc.page_count + 1)) - {i + 1 for i in indices})
            raise ValueError(
                f"order must list every page exactly once; missing {missing} "
                f"(use delete_pages to drop pages)"
            )
        # resolve_pages sorts and de-duplicates for the read tools; the *sequence* is what matters
        # here, so re-derive it from the caller's list after that validation.
        vdoc.ordered = [vdoc.ordered[page - 1] for page in order]
        vdoc.dirty = True
        _write(vdoc, target)
        return _result(target, vdoc, path, order=list(order))


def rotate(
    path: str,
    degrees: int,
    out: str,
    *,
    pages: list[int] | None = None,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Turn ``pages`` (1-based; ``None`` = all) by ``degrees``, a multiple of 90.

    ``degrees`` is a **delta**, which is what the verb means: 90 turns a page a quarter turn
    clockwise from wherever it already is, so calling it twice gives 180. A page that a scanner
    already stored rotated stays consistent with what the reader sees.
    """
    if degrees % _QUARTER_TURN:
        raise ValueError(f"degrees must be a multiple of 90, got {degrees}")
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        vdoc.rotate_pages(indices, degrees)
        _write(vdoc, target)
        return _result(
            target, vdoc, path, rotated=[i + 1 for i in indices], degrees=degrees
        )


def split(
    path: str,
    out_dir: str,
    *,
    ranges: list[str] | None = None,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Split ``path`` into several PDFs in ``out_dir``. Returns the written paths in order.

    ``ranges`` are print-dialog page-range strings — ``["1-3", "4", "5-"]`` — one output file per
    entry, using the same parser as the app's own dialogs (``util/page_range.py``), so the syntax an
    agent uses is the syntax a person types. Omit ``ranges`` to write one file per page.

    Each part keeps the content of its pages: text layer, form fields, annotations, and the
    bookmarks whose targets landed in that part. A split is a page-set change, so the parts do not
    inherit the document-level structure of the original — see this module's docstring.
    """
    from util.page_range import parse_page_range

    if not os.path.isdir(out_dir):
        raise ValueError(f"the output directory {out_dir!r} does not exist")
    stem = os.path.splitext(os.path.basename(path))[0]
    written: list[dict] = []
    with open_document(path, password) as vdoc:
        if ranges is None:
            groups = [[i] for i in range(vdoc.page_count)]
            labels = [str(i + 1) for i in range(vdoc.page_count)]
        else:
            # An empty spec means "every page" to parse_page_range — the right default for a
            # dialog's untouched Pages box, and a trap here: `["1-2", ""]` would silently make part
            # two the whole document. In a split list it can only be a mistake, so it is refused.
            for spec in ranges:
                if not spec.strip():
                    raise ValueError(
                        "an empty range means 'every page'; name the pages you want, "
                        "or omit `ranges` to split into single pages"
                    )
            groups = [parse_page_range(spec, vdoc.page_count) for spec in ranges]
            labels = [spec.replace(" ", "").replace(",", "_") for spec in ranges]
            for spec, group in zip(ranges, groups):
                if not group:
                    raise ValueError(f"the range {spec!r} selects no pages")
        pad = max(2, len(str(len(groups))))
        for n, (group, label) in enumerate(zip(groups, labels), start=1):
            out = _resolve_out(
                os.path.join(out_dir, f"{stem}-{n:0{pad}d}-p{label}.pdf"),
                sources=[path],
                overwrite=overwrite,
            )
            part = vdoc.subset(group)
            _write(part, out)
            written.append(
                {"out": out, "pages": len(group), "source_pages": [i + 1 for i in group]}
            )
    return {
        "parts": written,
        "count": len(written),
        "source": os.path.abspath(path),
        "source_unchanged": True,
    }


def extract_pages(
    path: str,
    pages: list[int],
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Write ``pages`` (1-based) to a single new PDF, in document order.

    The operation everybody actually asks for by name — "give me pages 10-20 as a file" — and the
    one the first tool surface was missing. :func:`split` could already produce it via
    ``ranges=["10-20"]``, but it is named for a different intent and picks the output filename
    itself, so an agent asked to *extract* did not find it and fell back to shelling out to
    `pdfunite`. That is what a missing tool looks like from the outside.

    Runs `model/export.py:export_selected_pages` (M51 — the app's Export ▸ Selected Pages as PDF),
    so it is the same object-level extract the GUI does: text layer, form fields and annotations
    carried, rotation/crop applied, and the origin bookmarks **and internal links remapped to the
    extracted page numbers** rather than left dangling.
    """
    from model.export import export_selected_pages

    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        if not indices:
            raise ValueError("no pages given — extract_pages must select something")
        _write(vdoc, target, writer=lambda doc, tmp: export_selected_pages(doc, indices, tmp))
        return {
            "out": target,
            "pages": len(indices),  # the OUTPUT's page count, not the source's
            "source_pages": [i + 1 for i in indices],
            "bytes": os.path.getsize(target),
            "source": os.path.abspath(path),
            "source_unchanged": True,
        }


def merge(paths: list[str], out: str, *, overwrite: bool = False) -> dict:
    """Concatenate ``paths`` into one PDF, in the order given.

    Lossless in the way that matters on a merge: colliding AcroForm field names are **renamed rather
    than dropped**, so two documents that both have a field called `name` come out with two working
    fields — the shared engine's dedup, not a re-implementation.
    """
    if len(paths) < 2:
        raise ValueError("merge needs at least two documents")
    target = _resolve_out(out, sources=paths, overwrite=overwrite)
    vdoc = VirtualDocument.from_path(paths[0])
    try:
        for extra in paths[1:]:
            source_id = vdoc.open_source(extra)
            vdoc.append_pages(
                [PageRef(source_id, i) for i in range(vdoc.sources[source_id].page_count)]
            )
        _write(vdoc, target)
        return {
            "out": target,
            "pages": vdoc.page_count,
            "bytes": os.path.getsize(target),
            "sources": [os.path.abspath(p) for p in paths],
            "source_unchanged": True,
        }
    finally:
        vdoc.close()


# ---- content transforms --------------------------------------------------------


def fill_form(
    path: str,
    values: dict,
    out: str,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Set AcroForm field values and write the filled copy. Fields stay **editable**.

    ``values`` maps field name to value; a field appearing on several pages is filled on all of
    them, because the name is what carries the value. Use ``get_form_fields`` first — an unknown
    name is an error rather than a silent no-op, since a typo that writes nothing and reports
    success is the worst outcome here.

    A checkbox takes ``True``/``False`` and the widget's own on-state is looked up for you, or the
    export value itself (``"1"``, ``"2"``, ``"Off"`` — it is per-widget) if you would rather be
    explicit. ``get_form_fields`` reports it as ``on_state``. **Anything else is an error**, for
    the reason the unknown-name check exists — see :func:`_check_button_values`.

    Writing a **read-only** field is allowed and reported: the caller may mean it, but the document
    says a person may not do it, so it is not something to do quietly.

    An **XFA** input is filled on its AcroForm side only and says so — see :func:`_describe_xfa`.
    """
    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    from model.page_edits import read_form_fields

    with open_document(path, password) as vdoc:
        fields = read_form_fields(vdoc)
        known = {field.name for field in fields}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(
                f"no such form field(s): {unknown} — the document has {sorted(known)}"
            )
        _check_button_values(fields, values)
        for name, value in values.items():
            vdoc.set_field_value(name, value)
        _write(vdoc, target)

        extra = _describe_xfa(vdoc)
        warnings = list(extra.pop("warnings", []))
        written_read_only = sorted({f.name for f in fields if f.read_only and f.name in values})
        if written_read_only:
            warnings.append(
                f"{len(written_read_only)} field(s) marked **read-only** by the document were "
                f"filled: {written_read_only}. That is allowed and the values are written — but a "
                "reader cannot edit or clear them in a viewer, and on a real form these are "
                "usually either plumbing or a signature line. Check this is what you meant."
            )
        if warnings:
            extra["warnings"] = warnings
        return _result(target, vdoc, path, filled=sorted(values), **extra)


# Values a caller may reasonably write for a button instead of naming its export state. PyMuPDF
# resolves each to the widget's own on/off state, which is the convenience worth keeping — the
# point of the check below is that it is a *closed* set, not that anything truthy will do.
_BOOLEAN_WORDS = frozenset({"true", "false", "yes", "no", "on", "off"})


def _check_button_values(fields, values: dict) -> None:
    """Reject a checkbox/radio value that is neither a real export state nor a boolean.

    ``fill_form`` already refuses an unknown field *name* — "a typo that writes nothing and reports
    success is the worst outcome here" — and the guarantee stopped there, one argument short. A
    state that matches nothing was resolved as falsy and written as ``Off``, so asking to tick a box
    with ``"3"`` (the obvious slip on a form whose states are ``"1"`` and ``"2"``) **cleared** it
    and reported the field under ``filled``. That is worse than the no-op the name check exists to
    prevent: it is a wrong answer on a form, reported as success (TC-002 retest, NEW ISSUE 8).

    Only buttons are checked, and only when the widget declares its states — a text field takes any
    string by definition, and a button with no ``/AP`` states has nothing to validate against. A
    bare ``None`` clears the field and is always allowed.

    ``"Of"`` is rejected too, though it happens to do the right thing today: it lands on ``Off`` by
    falling through the same silent path that mishandles ``"3"``, and keeping one wrong-input case
    working *by luck* is what makes the other one hard to see. Owner decision, 2026-08-16.
    """
    states: dict[str, set[str]] = {}
    for field in fields:
        if field.states:
            states.setdefault(field.name, set()).update(field.states)
    for name, value in values.items():
        allowed = states.get(name)
        if not allowed or value is None or isinstance(value, bool):
            continue
        if str(value) in allowed or str(value).casefold() in _BOOLEAN_WORDS:
            continue
        raise ValueError(
            f"{value!r} is not a state of the button field {name!r} — it accepts "
            f"{sorted(allowed)}, or a boolean (true ticks it, false clears it). Nothing was "
            "written; `get_form_fields` reports each widget's `on_state` and `states`."
        )


def _describe_xfa(vdoc: VirtualDocument) -> dict:
    """``{}`` for an ordinary AcroForm; an ``xfa`` block and a ``warnings`` list for an XFA one.

    An XFA form (LiveCycle Designer) keeps a second copy of itself as XML under ``/AcroForm/XFA``,
    and its ``datasets`` packet is where an XFA-aware consumer reads the values from. A fill writes
    the AcroForm widgets, which is what every ordinary viewer renders, and leaves ``datasets``
    byte-identical — so the file ends up asserting two different things (TC-002 ISSUE 3).

    **The bridge reports this rather than resolving it, deliberately** (owner decision,
    2026-08-15). Writing the values into ``datasets`` too means mapping AcroForm names onto XFA
    nodes, where a wrong write is worse than no write; dropping ``/XFA`` so the file degrades to a
    plain AcroForm is the conventional fix but removes the only thing that renders a *dynamic*
    form. Both remain open (`PROGRESS.md` §Open follow-ups); neither is silently guessed at here.
    """
    from model.page_edits import describe_xfa

    origin = vdoc.origin_source_id
    xfa = describe_xfa(vdoc.sources[origin]) if origin in vdoc.sources else None
    if xfa is None:
        return {}
    warning = (
        "this is an XFA (LiveCycle) form: the AcroForm widgets were filled but the XFA `datasets` "
        "packet was not, so a consumer that reads the XFA data — Acrobat's form-data export, "
        "agency intake tooling — sees an empty form. "
    ) + (
        "This one is **dynamic** XFA, which is the case that also renders wrong: Acrobat builds "
        "its pages from the XFA template rather than from the page content, so it may show the "
        "form unfilled. Check the output in Acrobat before sending it anywhere."
        if xfa["dynamic"]
        else "This one is static XFA, so the values are visible on the page in every viewer, "
        "including `render_page`."
    )
    return {
        "xfa": {"present": True, "dynamic": xfa["dynamic"], "datasets_updated": False},
        "warnings": [warning],
    }


def flatten(
    path: str, out: str, *, password: str | None = None, overwrite: bool = False
) -> dict:
    """Write a copy whose annotations and form fields are **baked into the page content**.

    The text layer survives; what goes away is the ability to edit or un-fill any of it. This is the
    "final copy" operation — a filled form nobody can change back, a marked-up review nobody can
    peel the markup off.
    """
    from model.export import export_flattened_pdf

    target = _resolve_out(out, sources=[path], overwrite=overwrite)
    with open_document(path, password) as vdoc:
        _write(vdoc, target, writer=export_flattened_pdf)
        return _result(target, vdoc, path, flattened=True)


def export_images(
    path: str,
    out_dir: str,
    *,
    pages: list[int] | None = None,
    dpi: int = 150,
    fmt: str = "png",
    password: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Rasterise ``pages`` (1-based; ``None`` = all) to image files in ``out_dir``.

    Unlike ``render_page``, which hands one page back inline for the model to look at, this writes
    files — for when the images are the deliverable rather than something to read.
    """
    if fmt.lower() not in {"png", "jpg", "jpeg"}:
        raise ValueError(f"format must be png or jpg, got {fmt!r}")
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    if not os.path.isdir(out_dir):
        raise ValueError(f"the output directory {out_dir!r} does not exist")
    from model.export import export_page_images

    stem = os.path.splitext(os.path.basename(path))[0]
    base = os.path.join(os.path.abspath(out_dir), f"{stem}.{fmt.lower()}")
    with open_document(path, password) as vdoc:
        indices = resolve_pages(vdoc, pages)
        if not overwrite:
            # export_page_images decides the final names itself (it appends page numbers only when
            # there is more than one), so the clobber check has to run against what it will write.
            for existing in _predicted_image_paths(base, indices):
                if os.path.exists(existing):
                    raise ValueError(
                        f"{existing!r} already exists; pass overwrite=true to replace it"
                    )
        written = export_page_images(vdoc, indices, base, dpi=dpi)
        return {
            "files": written,
            "count": len(written),
            "dpi": dpi,
            "source": os.path.abspath(path),
            "source_unchanged": True,
        }


def _predicted_image_paths(base: str, indices: list[int]) -> list[str]:
    """The names ``export_page_images`` will write, so the no-clobber check can see them."""
    if len(indices) == 1:
        return [base]
    root, ext = os.path.splitext(base)
    pad = len(str(max(i + 1 for i in indices)))
    return [f"{root}-{i + 1:0{pad}d}{ext}" for i in indices]
