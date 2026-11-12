"""What each tool sends back: every field of every reply, named once (M174.2).

These are types for mypy only. When the code runs, every reply is a plain ``dict``, built exactly as
before, and an agent receives exactly what it received before M174.2.

The tools in :mod:`mcp_bridge.server` do not name these types in their own signatures, and must
not. The MCP library reads a tool's return type to build the reply schema it advertises, and checks
each reply against that schema. Measured on mcp 2.0.0: a type it cannot resolve, as it cannot
resolve these, makes it drop the schema and the structured copy of every reply, saying so only in a
log line. So a tool says it returns ``dict[str, Any]``, which gives the schema it always had, and
hands its reply over through ``server._sent`` (`PLAN.md` §M174).

A field marked ``NotRequired`` is sent only in some replies: a warning, a note when a reply was cut
short, or the fields of a document that opened.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

# ---- reads ---------------------------------------------------------------------


class PageSize(TypedDict):
    width_pt: float
    height_pt: float
    rotation: int
    pages: list[int]


class InfoReply(TypedDict):
    """``get_info``. A document that needs a password reports only what can be known without one."""

    path: str
    pages: NotRequired[int]
    size_bytes: int
    encrypted: bool
    encryption: NotRequired[str | None]
    needs_password: bool
    permissions: NotRequired[dict[str, bool]]
    has_text_layer: NotRequired[bool]
    first_page_with_text: NotRequired[int | None]
    has_outline: NotRequired[bool]
    title: NotRequired[str | None]
    author: NotRequired[str | None]
    page_sizes: NotRequired[list[PageSize]]


class OutlineEntry(TypedDict):
    level: int
    title: str
    page: int
    top: float | None


class OutlineReply(TypedDict):
    count: int
    entries: list[OutlineEntry]


class LinkEntry(TypedDict):
    page: int
    rect: list[float]
    kind: str
    target_page: int | None
    target_top: float | None
    uri: str | None
    file: str | None
    text: str | None


class LinksReply(TypedDict):
    count: int
    total_links: int
    offset: int
    links: list[LinkEntry]
    kinds: dict[str, int]
    links_without_action: int
    links_with_unresolved_target: int
    pages_scanned: list[int]
    source: str
    more_available: bool
    warnings: NotRequired[list[str]]


class SearchHit(TypedDict):
    page: int
    snippet: str
    boxes: list[list[float]]
    invisible: bool


class SearchReply(TypedDict):
    count: int
    hits: list[SearchHit]
    truncated: NotRequired[bool]
    total_matches: NotRequired[int]
    note: NotRequired[str]


class ExtractedPage(TypedDict):
    page: int
    text: str


class ExtractTextReply(TypedDict):
    page_count: int
    pages: list[ExtractedPage]
    table_pages: list[int]
    truncated: NotRequired[bool]
    pages_omitted: NotRequired[list[int]]
    note: NotRequired[str]


class RenderedPage(TypedDict):
    """``render_page``'s picture and its measurements. The tool sends the picture alone."""

    page: int
    dpi: int
    clip: list[float] | None
    width_px: int
    height_px: int
    png: bytes


class TableEntry(TypedDict):
    page: int
    bbox: list[float]
    rows: list[list[str]]
    header: list[str] | None
    title: str | None
    title_from_previous_page: bool
    row_count: int
    col_count: int
    continues_from: int | None
    continuation_checked: bool


class UnreadRegion(TypedDict):
    page: int
    bbox: list[float]
    reason: str
    suggestion: str


class TablesReply(TypedDict):
    count: int
    total_tables: int
    offset: int
    tables: list[TableEntry]
    unread_regions: list[UnreadRegion]
    pages_scanned: list[int]
    source: str
    more_available: bool
    warnings: NotRequired[list[str]]


class StyleInfo(TypedDict):
    font: str
    size: float
    bold: bool
    italic: bool


class StyleEntry(TypedDict):
    style: str
    font: str
    size: float
    bold: bool
    italic: bool
    count: int
    distinct: int
    pages: int
    examples: list[str]
    in_table: NotRequired[int]


class HeadingCandidate(TypedDict):
    page: int
    bbox: list[float]
    text: str
    style: str
    run_in: bool
    in_table: NotRequired[bool]


class HeadingsReply(TypedDict):
    count: int
    total_candidates: int
    offset: int
    candidates: list[HeadingCandidate]
    body: StyleInfo | None
    styles: list[StyleEntry]
    tables_checked: bool
    pages_without_visible_text: list[int]
    pages_scanned: list[int]
    source: str
    more_available: bool
    warnings: NotRequired[list[str]]


class FormFieldEntry(TypedDict):
    name: str
    type: str
    page: int
    rect: list[float]
    choices: list[str] | None
    value: object
    on_state: str | None
    states: list[str] | None
    read_only: bool
    required: bool
    multiline: bool
    max_len: int | None


class FormFieldsReply(TypedDict):
    count: int
    fields: list[FormFieldEntry]


class AnnotationEntry(TypedDict):
    page: int
    type: str
    boxes: list[list[float]]
    snippet: str
    text_length: int
    color: list[float] | None
    color_name: str | None
    color_exact: bool
    note: str
    author: str
    mine: bool
    editable: bool
    note_truncated: NotRequired[bool]
    note_length: NotRequired[int]


class AnnotationsReply(TypedDict):
    count: int
    total_annotations: int
    offset: int
    annotations: list[AnnotationEntry]
    pages_scanned: list[int]
    source: str
    more_available: bool
    warnings: NotRequired[list[str]]


# ---- writes --------------------------------------------------------------------


class WriteReply(TypedDict):
    """What every write that produces one document reports first."""

    out: str
    pages: int
    bytes: int
    source: str
    source_unchanged: bool


class DeleteReply(WriteReply):
    deleted: list[int]


class ReorderReply(WriteReply):
    order: list[int]


class RotateReply(WriteReply):
    rotated: list[int]
    degrees: int


class FlattenReply(WriteReply):
    flattened: bool


class XfaInfo(TypedDict):
    present: bool
    dynamic: bool
    datasets_updated: bool


class FillFormReply(WriteReply):
    filled: list[str]
    xfa: NotRequired[XfaInfo]
    warnings: NotRequired[list[str]]


class TopOutsideItsPage(TypedDict):
    index: int
    title: object
    top: float
    page: object
    page_height: float


# `from` is a keyword, so these two are written in the form that takes any key.
LevelChange = TypedDict("LevelChange", {"index": int, "title": str, "from": int, "to": int})
ImageRecoded = TypedDict(
    "ImageRecoded", {"page": int, "from": str, "to": str, "bytes_before": int, "bytes_after": int}
)


class SetOutlineReply(WriteReply):
    entries: int
    replaced: int
    positions_discarded: NotRequired[int]
    tops_outside_the_page: NotRequired[list[TopOutsideItsPage]]
    levels_normalised: NotRequired[list[LevelChange]]
    warnings: NotRequired[list[str]]


class SplitPart(TypedDict):
    out: str
    pages: int
    source_pages: list[int]


class SplitReply(TypedDict):
    parts: list[SplitPart]
    count: int
    source: str
    source_unchanged: bool


class ExtractPagesReply(TypedDict):
    out: str
    pages: int
    source_pages: list[int]
    bytes: int
    source: str
    source_unchanged: bool


class MergeReply(TypedDict):
    out: str
    pages: int
    bytes: int
    sources: list[str]
    source_unchanged: bool


class ExportImagesReply(TypedDict):
    files: list[str]
    count: int
    out_dir: str
    dpi: int
    clip: list[float] | None
    source: str
    source_unchanged: bool
    truncated: NotRequired[bool]
    note: NotRequired[str]


class AnnotateReply(TypedDict):
    out: str
    source: str
    source_unchanged: bool
    pages: int
    bytes: int
    marks_requested: int
    pages_annotated: list[int]
    marks_added: int
    annotations: list[AnnotationEntry]
    warnings: NotRequired[list[str]]


# ---- redaction -----------------------------------------------------------------


class Spelling(TypedDict):
    """A spelling of a query still in the file, and where."""

    as_written: str
    count: int
    pages: list[int]


class TermMatches(TypedDict):
    term: str
    matches: int


class PartialWordMatch(TypedDict):
    term: str
    inside: str
    leaves: str
    pages: list[int]
    count: int


class RedactionReport(TypedDict, total=False):
    """``redact_text`` and ``redact_regions``, one entry of ``queries``, and each part they are built from.

    Every field is optional, unlike the other replies, because a redaction reply is put together from
    parts (``redaction._merged``): the write, its verification, the residual scans, the image report
    and what the call itself adds. Each part holds some of these fields, and the whole holds the
    rest. What this checks is that no part writes a field that is not here, or of the wrong type.
    """

    out: str
    pages: int
    bytes: int
    source: str
    source_unchanged: bool
    verified_text: dict[str, list[str]]
    verified_with: list[str]
    cross_engine_verified: bool
    verification_note: str
    query: str | None
    queries: list[RedactionReport]
    matches: int
    residual_matches: int
    residual_literal: int
    residual_literal_forms: list[Spelling]
    residual_normalized: list[Spelling] | None
    query_terms: list[TermMatches]
    partial_word_matches: list[PartialWordMatch]
    images_recoded: list[ImageRecoded]
    invisible_matches: int
    regions: int
    pages_redacted: list[int]
    boxes_redacted: int
    residual_scope: list[int]
    warnings: list[str]
