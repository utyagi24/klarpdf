"""Regenerate ``vendor/wheels-sources.md`` from a pip ``--report`` JSON.

The vendored wheels are a local build input and are not committed; this script produces the
committed, auditable record of exactly which wheels (version + sha256 + source URL) the offline
Windows ship build uses. Run after any change to ``requirements.txt`` (from anywhere):

    py -3.12 -m pip download -r requirements.txt --only-binary=:all: -d vendor/wheels
    py -3.12 -m pip install -r requirements.txt --require-hashes --ignore-installed \\
        --dry-run --report report.json
    py -3.12 vendor/gen-sources.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

report = json.loads((ROOT / "report.json").read_text(encoding="utf-8"))
rows = []
for item in report.get("install", []):
    meta = item["metadata"]
    di = item["download_info"]
    url = di["url"]
    sha = di.get("archive_info", {}).get("hashes", {}).get("sha256", "")
    rows.append((meta["name"], meta["version"], url.rsplit("/", 1)[-1], sha, url))
rows.sort(key=lambda r: r[0].lower())

lines = [
    "# Vendored wheel sources (offline Windows ship build)",
    "",
    "Exact `win_amd64` wheels for the pinned ship lock (`requirements.txt`). The wheels themselves",
    "are **not committed** (binary bloat; GitHub's 100 MB/file limit) — this file is the auditable",
    "record so the set can be re-fetched and verified offline, and the M8 installer bundles them so",
    "the target machine needs no Python and no network. Regenerate with `vendor/gen-sources.py`",
    "(see its header) after any `requirements.txt` change.",
    "",
    "Every `sha256` below also appears in `requirements.txt`; `pip install --require-hashes` refuses",
    "any wheel whose hash does not match.",
    "",
]
for name, ver, fname, sha, url in rows:
    lines += [f"## {name} {ver}", f"- wheel: `{fname}`", f"- sha256: `{sha}`", f"- source: {url}", ""]

(ROOT / "vendor" / "wheels-sources.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
print(f"wrote vendor/wheels-sources.md ({len(rows)} wheels)")
