"""Disk artifact bytes, independent of metadata and HTTP policy."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class DiskStorage:
    def __init__(self, data_dir: Path) -> None:
        self._files = data_dir / "files"
        self._files.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def stage(self, artifact_id: str, src: BinaryIO) -> Iterator[Path]:
        tmp = self._files / f".{artifact_id}.{secrets.token_hex(4)}.tmp"
        try:
            with tmp.open("wb") as out:
                shutil.copyfileobj(src, out)
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)

    def publish(self, staged: Path, artifact_id: str) -> None:
        os.replace(staged, self._files / artifact_id)

    def open(self, artifact_id: str) -> BinaryIO:
        return (self._files / artifact_id).open("rb")

    def size(self, f: BinaryIO) -> int:
        return os.fstat(f.fileno()).st_size

    def disk_free(self) -> int:
        return shutil.disk_usage(self._files).free

    def available_for_serving(self, artifact_id: str) -> bool:
        return (self._files / artifact_id).exists()

    def exists(self, artifact_id: str) -> bool:
        try:
            (self._files / artifact_id).stat()
        except FileNotFoundError:
            return False
        return True

    def delete(self, artifact_id: str) -> None:
        (self._files / artifact_id).unlink(missing_ok=True)

    def cleanup(self, known: set[str], stale: float) -> None:
        for f in self._files.iterdir():
            try:
                old = f.stat().st_mtime < stale
            except FileNotFoundError:
                continue
            if f.name not in known and old:
                f.unlink(missing_ok=True)

    def file_path(self, artifact_id: str) -> Path:
        """Path for the disk-only Starlette FileResponse delivery adapter."""
        return self._files / artifact_id
