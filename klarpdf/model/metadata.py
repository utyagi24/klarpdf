"""Document metadata — the Info dict **and** the XMP packet, kept honest (PLAN.md, M53).

A PDF carries document metadata in **two stores**: the classic Info dictionary and the XMP
packet (`/Metadata` in the catalog) — and Acrobat-class viewers prefer XMP when both exist. Any
verb that touches one store must touch the other: an *edit* that only wrote the Info dict would
show the old values in viewers that read XMP; a *remove* that left the XMP packet behind would be
a false promise of a strip. This module owns that both-stores rule.

It also owns **carry-through**: ``insert_pdf`` copies pages, never the Info dict or the XMP
packet, so before M53 every materialised save silently stripped the document's metadata.
:func:`apply_metadata` runs in the materialise build and writes the origin's stores through
unchanged when the user touched nothing.

Model-layer (PyMuPDF only, no GUI) and headless-testable.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

# The Info-dict keys ``set_metadata`` writes ('format' / 'encryption' in ``doc.metadata`` are
# read-only pseudo-fields and must not be passed back in).
INFO_KEYS = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator",
    "producer",
    "creationDate",
    "modDate",
    "trapped",
)

# What the Properties dialog lets the user change; the rest is view-only provenance.
EDITABLE_KEYS = ("title", "author", "subject", "keywords")


def read_info(doc) -> dict:
    """The writable Info-dict fields of ``doc`` as a plain ``{key: str}`` (blanks as ``""``)."""
    metadata = doc.metadata or {}
    return {key: (metadata.get(key) or "") for key in INFO_KEYS}


def build_xmp(values: dict) -> str:
    """A minimal, standard XMP packet holding ``values``' user-facing fields.

    Emitted whenever the user *edits* metadata, so the two stores agree: Dublin Core for
    title / author / subject, ``pdf:Keywords``, plus CreatorTool / Producer when present.
    Dates are left to the Info dict (XMP wants ISO-8601, PDF dates are ``D:…`` — a lossy
    conversion this packet doesn't need, since every viewer falls back to Info for dates).
    """
    title = escape(values.get("title", ""))
    author = escape(values.get("author", ""))
    subject = escape(values.get("subject", ""))
    keywords = escape(values.get("keywords", ""))
    creator_tool = escape(values.get("creator", ""))
    producer = escape(values.get("producer", ""))
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        '    xmlns:pdf="http://ns.adobe.com/pdf/1.3/">\n'
        f'   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>\n'
        f"   <dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>\n"
        "   <dc:description><rdf:Alt>"
        f'<rdf:li xml:lang="x-default">{subject}</rdf:li></rdf:Alt></dc:description>\n'
        f"   <pdf:Keywords>{keywords}</pdf:Keywords>\n"
        f"   <xmp:CreatorTool>{creator_tool}</xmp:CreatorTool>\n"
        f"   <pdf:Producer>{producer}</pdf:Producer>\n"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    )


def apply_metadata(out, vdoc) -> None:
    """Write the document metadata onto the materialised output (the M53 verbs).

    * untouched → **carry through** the origin's Info dict and XMP packet byte-for-byte
      (``insert_pdf`` copies neither);
    * edited → write **both stores** from the effective values, so viewers that prefer either
      one agree;
    * removed (override ``{}``) → clear **both stores** — an Info-only strip would leave the
      XMP packet still telling viewers everything.
    """
    override = vdoc.metadata_override
    if override == {}:
        out.set_metadata({})
        out.del_xml_metadata()
    elif override is not None:
        out.set_metadata({k: v for k, v in override.items() if k in INFO_KEYS})
        out.set_xml_metadata(build_xmp(override))
    else:
        out.set_metadata({k: v for k, v in vdoc.origin_metadata.items() if k in INFO_KEYS})
        if vdoc.origin_xmp:
            out.set_xml_metadata(vdoc.origin_xmp)
