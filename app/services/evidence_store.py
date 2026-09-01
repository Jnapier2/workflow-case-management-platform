"""Evidence-storage abstraction with a project-local default and integrity verification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.runtime_utils import sha256_file


@dataclass(slots=True)
class EvidenceReceipt:
    backend: str
    storage_key: str
    size_bytes: int
    sha256: str
    integrity_status: str
    scan_status: str


class LocalEvidenceStore:
    backend_name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("Evidence storage key escapes the configured root.")
        return candidate

    def receipt_for(self, path: Path) -> EvidenceReceipt:
        path = path.resolve()
        if self.root not in path.parents:
            raise ValueError("Evidence file is outside the configured store.")
        return EvidenceReceipt(
            backend=self.backend_name,
            storage_key=path.relative_to(self.root).as_posix(),
            size_bytes=path.stat().st_size,
            sha256=sha256_file(path),
            integrity_status="Verified",
            scan_status="NotConfigured",
        )

    def verify(self, storage_key: str, expected_sha256: str) -> bool:
        path = self.resolve(storage_key)
        return path.is_file() and sha256_file(path) == expected_sha256

async def save_upload(upload, root: Path, case_reference: str, original_name: str, max_bytes: int) -> tuple[Path, EvidenceReceipt, str]:  # type: ignore[no-untyped-def]
    """Stream one upload to the local store and return verified metadata.

    The caller owns database writes; this function owns only bounded file staging/finalization.
    """
    from uuid import uuid4
    suffix = Path(original_name).suffix.lower()
    if not suffix or len(suffix) > 16 or not all(ch.isalnum() or ch == "." for ch in suffix):
        suffix = ".bin"
    folder = root / case_reference
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}{suffix}"
    temp = folder / f".{stored}.tmp"
    final = folder / stored
    size = 0
    try:
        with temp.open("wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("Evidence file exceeds the configured upload limit.")
                handle.write(chunk)
        if size == 0:
            raise ValueError("Evidence file is empty.")
        temp.replace(final)
        store = LocalEvidenceStore(root)
        return final, store.receipt_for(final), stored
    except Exception:
        temp.unlink(missing_ok=True)
        final.unlink(missing_ok=True)
        raise
