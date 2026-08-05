"""
📦 FILE STORAGE ABSTRACTION

Single place that decides WHERE uploaded files live, chosen by the STORAGE_BACKEND
env var:
  - "local" (default) → the local filesystem, exactly as before (dev unchanged).
  - "gcs"             → a Google Cloud Storage bucket ($GCS_BUCKET), for Cloud Run
                        where the container filesystem is ephemeral.

Files are addressed by a relative *key* like "resumes/<uuid>.pdf" or
"uploads/photos/<name>.jpg". That key is what gets stored in the DB and is also the
URL path the frontend uses (served by StaticFiles locally, or by streaming routes
in main.py when on GCS). Keys are identical across backends, so only persistence
and serving change — not the data model.
"""

import os
import io
import shutil
import logging
import contextlib
import tempfile
from typing import BinaryIO

logger = logging.getLogger(__name__)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
GCS_BUCKET = os.getenv("GCS_BUCKET", "")

# Root that relative storage keys resolve against. Defaults to the backend/
# directory (this file is backend/src/services/storage.py), NOT the process
# working directory — systemd, cron and a shell all start with different CWDs,
# and a relative open() would silently write uploads to a second location where
# nothing would ever serve them. Override with HIREFLOW_DATA_DIR to put uploads
# on a separate volume. main.py mounts the same root, so the two cannot drift.
DATA_DIR = os.path.abspath(
    os.getenv("HIREFLOW_DATA_DIR")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


def _norm(key: str) -> str:
    """Canonical storage-key form: forward slashes, no leading slash."""
    return str(key).replace("\\", "/").lstrip("/")


def _abs(key: str) -> str:
    """Absolute on-disk path for a storage key.

    Keys are ALWAYS relative ("resumes/<uuid>.pdf") and stay relative in the DB
    and in URLs; this is the single place they become filesystem paths. The key
    is normalised *before* being resolved, so "logos/x.png" and "/logos/x.png"
    address the same file — every operation has to agree on that, or a value is
    written to one path and looked up at another.

    Containment is lexical (normpath, not realpath) on purpose: it collapses
    ".." so a key cannot escape DATA_DIR, while still allowing an upload
    directory to be a symlink onto another volume.

    Raises ValueError if the key would escape DATA_DIR.
    """
    root = os.path.normpath(DATA_DIR)
    path = os.path.normpath(os.path.join(root, _norm(key)))
    if path != root and not path.startswith(root + os.sep):
        raise ValueError(f"storage key escapes the data directory: {key!r}")
    return path


class LocalStorage:
    """Filesystem-backed storage (keys are paths relative to DATA_DIR)."""

    backend = "local"

    def save_fileobj(self, fileobj, key: str) -> str:
        key = _norm(key)
        path = _abs(key)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        try:
            fileobj.seek(0)
        except Exception:
            pass
        with open(path, "wb") as out:
            shutil.copyfileobj(fileobj, out)
        return key

    def save_bytes(self, data: bytes, key: str) -> str:
        key = _norm(key)
        path = _abs(key)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as out:
            out.write(data)
        return key

    def read_bytes(self, key: str) -> bytes:
        with open(_abs(key), "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        # A key that escapes DATA_DIR does not exist as far as storage is
        # concerned — report that rather than raising at every call site.
        try:
            return os.path.exists(_abs(key))
        except ValueError:
            return False

    def delete(self, key: str) -> None:
        try:
            os.remove(_abs(key))
        except (FileNotFoundError, ValueError):
            pass


class GCSStorage:
    """Google Cloud Storage-backed storage (keys are object names in the bucket)."""

    backend = "gcs"

    def __init__(self, bucket_name: str):
        if not bucket_name:
            raise RuntimeError("STORAGE_BACKEND=gcs requires GCS_BUCKET to be set")
        from google.cloud import storage as gcs  # lazy import (only when used)
        self._client = gcs.Client()
        self._bucket = self._client.bucket(bucket_name)
        logger.info("Storage backend: GCS bucket '%s'", bucket_name)

    def _blob(self, key: str):
        return self._bucket.blob(_norm(key))

    def save_fileobj(self, fileobj, key: str) -> str:
        key = _norm(key)
        self._blob(key).upload_from_file(fileobj, rewind=True)
        return key

    def save_bytes(self, data: bytes, key: str) -> str:
        key = _norm(key)
        self._blob(key).upload_from_string(data)
        return key

    def read_bytes(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self._blob(key).exists()

    def delete(self, key: str) -> None:
        try:
            self._blob(key).delete()
        except Exception:
            pass


# Global instance selected at import time.
if STORAGE_BACKEND == "gcs":
    storage = GCSStorage(GCS_BUCKET)
else:
    storage = LocalStorage()


def open_binary(key: str) -> BinaryIO:
    """Return a readable binary stream for a stored object."""
    return io.BytesIO(storage.read_bytes(key))


@contextlib.contextmanager
def as_local_path(key: str):
    """Yield a real local filesystem path for `key`, for libraries that need a path
    (pdfplumber, python-docx). If the path already exists on disk (local backend, or
    a caller-supplied temp file) it's yielded as-is; otherwise the object is
    downloaded from GCS to a temp file that is removed afterwards."""
    # Some callers hand us a real filesystem path (e.g. a NamedTemporaryFile)
    # rather than a storage key. Honour an existing absolute path as-is before
    # trying to interpret it as a key — _abs would otherwise reject it.
    if os.path.isabs(str(key)) and os.path.exists(key):
        yield key
        return

    local = _abs(key)
    if os.path.exists(local):
        yield local
        return
    if getattr(storage, "backend", "local") == "gcs":
        data = storage.read_bytes(key)
        suffix = os.path.splitext(key)[1]
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            tmp.write(data)
            tmp.flush()
            tmp.close()
            yield tmp.name
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
    else:
        # Local backend, file genuinely missing — let the caller's open() raise.
        yield local
