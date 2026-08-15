"""``python -m mcp_bridge`` — the same entry point as the ``klarpdf-mcp`` console script.

Useful before the package is installed (running straight out of a checkout) and as the ``command``
in a client config that would rather point at an interpreter than at a script shim.
"""

from mcp_bridge.server import main

main()
