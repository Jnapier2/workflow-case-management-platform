"""Capture a bounded critical diagnostic after a launcher-observed fatal exit.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.diagnostics import capture_critical


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trigger", choices=["startup_dependency_failure", "terminal_critical_exit"])
    parser.add_argument("--exit-code", type=int, default=1)
    args = parser.parse_args()
    result = capture_critical(
        args.trigger,
        RuntimeError(f"Launcher observed exit code {args.exit_code}"),
        context={
            "component": "windows_launcher",
            "exit_code": args.exit_code,
            "last_progress": (
                "dependency bootstrap" if args.trigger == "startup_dependency_failure" else "server process"
            ),
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
