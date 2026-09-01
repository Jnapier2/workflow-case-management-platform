"""Create a manual redacted, read-only Export20 package.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.diagnostics import create_manual_export


def main() -> int:
    result = create_manual_export()
    if result.get("result") == "success":
        print("Support Export20 created successfully.")
        print(f"Path: {result.get('path')}")
        print(f"SHA-256: {result.get('sha256')}")
        print(f"Elapsed: {result.get('elapsed_seconds')} seconds")
    else:
        print(f"Support Export20 was not created: {result.get('result', 'unknown_error')}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("result") == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
