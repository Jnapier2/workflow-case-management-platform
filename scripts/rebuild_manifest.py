"""Rebuild the managed-file manifest after an intentional source change.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sys

from release_inventory import (
    MANIFEST_PATH,
    PACKAGE_METADATA_PATH,
    atomic_write_json,
    build_entries,
    load_json,
    managed_paths,
    parse_version_module,
    parse_version_txt,
)


def main() -> int:
    version, build_id = parse_version_txt()
    module_identity = parse_version_module()
    if module_identity["APP_VERSION"] != version or module_identity["BUILD_ID"] != build_id:
        print("ERROR: VERSION.txt and app/version.py disagree.", file=sys.stderr)
        return 2

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    paths = managed_paths()
    metadata = load_json(PACKAGE_METADATA_PATH)
    metadata.update(
        {
            "version": version,
            "build_id": build_id,
            "display_name": module_identity["DISPLAY_NAME"],
            "package_id": module_identity["PACKAGE_ID"],
            "managed_file_count": len(paths),
            "manifest_generated_utc": generated,
        }
    )
    atomic_write_json(PACKAGE_METADATA_PATH, metadata)

    # PACKAGE_METADATA.json changed; inventory cardinality is stable, so hash the final bytes now.
    paths = managed_paths()
    entries = build_entries(paths)
    manifest = {
        "schema": "gateway-managed-file-manifest-1",
        "package_id": module_identity["PACKAGE_ID"],
        "display_name": module_identity["DISPLAY_NAME"],
        "version": version,
        "build_id": build_id,
        "generated_utc": generated,
        "managed_file_count": len(entries),
        "hash_algorithm": "SHA-256",
        "path_rules": "root-relative, forward-slash, case-insensitive collision rejection, no symlinks",
        "files": entries,
    }
    atomic_write_json(MANIFEST_PATH, manifest)
    print(f"MANIFEST rebuilt: {len(entries)} managed files | version {version} | build {build_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
