#!/usr/bin/env python3
"""Refresh engines/ in the current directory, including during Docker and bundle builds."""

import sys
from pathlib import Path

from src.engine_catalog import generate_engines


def main() -> int:
    count = generate_engines(Path("engines"))
    print(f"Wrote {count} engine files to engines", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
