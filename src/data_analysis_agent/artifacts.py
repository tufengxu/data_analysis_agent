"""ArtifactStore: persist binary tool outputs (charts, files) for user delivery.

Pure stdlib LEAF module. Sandboxed tools hand rendered images back as base64
in ``ToolResult.metadata["images"]``; the agent loop saves them here so they
survive sandbox teardown and can be shown to the user as real file paths.
"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ArtifactStore:
    """Disk-backed store for user-facing analysis artifacts."""

    def __init__(self, store_dir: str | Path) -> None:
        self.dir = Path(store_dir)
        self._available = True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._available = False  # read-only fs -> disabled, degrade gracefully

    @property
    def available(self) -> bool:
        return self._available

    def save_image(self, name: str, fmt: str, data_b64: str) -> Path | None:
        """Decode a base64 image and persist it; None on any failure."""
        if not self._available or not data_b64:
            return None
        safe_name = _SAFE_NAME.sub("_", name).strip("._") or "artifact"
        ext = _SAFE_NAME.sub("", fmt) or "png"
        path = self.dir / f"{safe_name}.{ext}"
        try:
            payload = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            return None
        try:
            path.write_bytes(payload)
        except OSError:
            return None
        return path
