#!/usr/bin/env python3
"""Stdio entry point for local MCP hosts such as the Claude Desktop MCP Bundle.

The hosted deployment (``src/server.py``) serves MCP over HTTP and reads the
SerpApi key from every request. Local hosts launch the server as a subprocess
and talk to it over stdin/stdout instead, so this entry point reads the key
from the ``SERPAPI_API_KEY`` environment variable (wired up in ``manifest.json``).

Run it directly with::

    SERPAPI_API_KEY=... uv run src/stdio.py
"""

import sys
from pathlib import Path

# ``uv run src/stdio.py`` puts ``src/`` on sys.path rather than the project
# root, so add the root explicitly to make the ``src.*`` imports resolve no
# matter where the bundle is installed or which directory the host starts from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.server import mcp  # noqa: E402

if __name__ == "__main__":
    # stdout carries the MCP protocol; keep the process quiet on stderr too so
    # host logs stay readable.
    mcp.run(transport="stdio", show_banner=False)
