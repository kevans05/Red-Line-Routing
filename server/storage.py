"""Content-addressed file blob storage.

Blobs are stored once per content hash (`objects/<hash[:2]>/<hash>`), so
re-uploading the same bytes (e.g. a client re-pushing after a partial
transfer, per the offline-sync design) never duplicates disk space. The
`files` DB table layers revision history and per-project ownership on top:
a new upload for the same (project_id, owner_type, owner_id, filename)
gets the next revision number rather than overwriting the previous row,
mirroring wire_planner.py's `_archive_revision` convention of keeping old
revisions around instead of clobbering them.
"""

import hashlib
import os

DEFAULT_STORAGE_DIR = os.path.expanduser("~/.redlinerouting-server-files")


class FileStorage:
    def __init__(self, root_dir=None):
        self.root_dir = root_dir or DEFAULT_STORAGE_DIR
        os.makedirs(self.root_dir, exist_ok=True)

    def _object_path(self, content_hash):
        return os.path.join(self.root_dir, "objects", content_hash[:2], content_hash)

    def save_blob(self, content: bytes) -> tuple:
        """Store bytes, returning (content_hash, storage_path). Idempotent —
        writing the same content twice is a no-op the second time."""
        content_hash = hashlib.sha256(content).hexdigest()
        path = self._object_path(content_hash)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + f".tmp-{os.getpid()}"
            with open(tmp_path, "wb") as f:
                f.write(content)
            os.replace(tmp_path, path)
        return content_hash, path

    def read_blob(self, storage_path: str) -> bytes:
        with open(storage_path, "rb") as f:
            return f.read()
