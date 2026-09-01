"""Run a non-destructive backup/restore recovery qualification.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backup_restore import run_recovery_doctor
from app.config import Settings


def main() -> int:
    receipt = run_recovery_doctor(Settings())
    result = str(receipt.get("result", "UNKNOWN"))
    print(f"Recovery Doctor: {result}")
    if receipt.get("schema_compatibility"):
        print(f"Schema compatibility: {receipt['schema_compatibility']}")
    if receipt.get("backup_path"):
        print(f"Verified backup: {receipt['backup_path']}")
    advisories = receipt.get("advisories") or []
    for advisory in advisories:
        print(f"ADVISORY: {advisory}")
    for problem in receipt.get("problems") or []:
        print(f"PROBLEM: {problem}")
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if result.startswith("PASS") else 2


if __name__ == "__main__":
    raise SystemExit(main())
