"""Single source of the application version (PLAN.md, Build & release pipeline).

Consumed by ``packaging/pdfproj.spec`` (frozen exe metadata), ``packaging/installer.iss`` (Inno
``AppVersion``), and the ``v<version>`` git release tag. A version bump is an explicit edit here
followed by a new tag — versions never change automatically.
"""

from __future__ import annotations

__version__ = "0.9.1"
