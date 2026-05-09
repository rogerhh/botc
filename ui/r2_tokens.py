"""Lazy fetcher for character-token PNGs hosted in a private R2 bucket.

The deployment story:

    * The token PNGs are *not* checked into the repo. ``assets/tokens/``
      is gitignored apart from ``manifest.json``, so a fresh clone has
      no images on disk.
    * The server (the operator's machine) holds R2 read credentials in
      ``.env`` (``R2_ENDPOINT``, ``R2_BUCKET``, ``R2_ACCESS_KEY_ID``,
      ``R2_SECRET_ACCESS_KEY``). End users — players, the storyteller's
      phone — never know R2 exists; they hit the local web server's
      ``/assets/tokens/...`` URLs as before.
    * On the *first* request for a given token, the server fetches the
      object from R2, atomically writes it to ``assets/tokens/<name>``,
      and serves it. Every subsequent request hits local disk.

If R2 is not configured (no ``.env``, missing keys), :func:`lazy_fetch`
quietly returns ``False`` and the request handler falls through to its
existing 404 path. The server still works for any tokens that happen
to be on disk locally — useful for tests and for poking around.
"""

from __future__ import annotations

import os
import logging
import tempfile
import threading
from dataclasses import dataclass
from typing import Dict, Optional

# python-dotenv is listed in requirements.txt but the import is guarded
# so the server still imports cleanly in environments where it isn't
# installed yet (CI, fresh clones before ``pip install -r``). When
# present, ``.env`` is loaded once on first use.
try:
    from dotenv import load_dotenv  # type: ignore
except ImportError:  # pragma: no cover - dependency not installed
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False

# boto3 likewise: importing this module must not crash if boto3 isn't
# installed yet. The actual fetch will fail loud at request time.
try:
    import boto3  # type: ignore
    from botocore.config import Config as BotoConfig  # type: ignore
    from botocore.exceptions import (  # type: ignore
        BotoCoreError,
        ClientError,
        NoCredentialsError,
    )
    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency not installed
    boto3 = None  # type: ignore
    BotoConfig = None  # type: ignore
    BotoCoreError = ClientError = NoCredentialsError = Exception  # type: ignore
    _BOTO3_AVAILABLE = False


_LOG = logging.getLogger(__name__)

# Module-level state. Initialized lazily on the first call so importing
# this module is side-effect-free (env vars haven't necessarily been
# loaded yet when ui.py imports us at the top of the file).
_INIT_LOCK = threading.Lock()
_INITIALIZED = False
_CONFIG: Optional["R2Config"] = None
_CLIENT = None  # boto3 S3 client, typed as Any to avoid hard dep
_PER_KEY_LOCKS: Dict[str, threading.Lock] = {}
_PER_KEY_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class R2Config:
    """R2 connection parameters resolved from the environment."""

    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def from_env(cls) -> Optional["R2Config"]:
        """Build a config from ``R2_*`` env vars, or return ``None`` if
        any required key is missing/empty."""
        endpoint = (os.environ.get("R2_ENDPOINT") or "").strip()
        bucket = (os.environ.get("R2_BUCKET") or "").strip()
        ak = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
        sk = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
        if not (endpoint and bucket and ak and sk):
            return None
        return cls(
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=ak,
            secret_access_key=sk,
        )


def _ensure_initialized() -> None:
    """Load ``.env`` and build the boto3 client on first use.

    Idempotent. If R2 isn't configured (or boto3 isn't installed), the
    module enters a "disabled" state: ``_CLIENT`` stays ``None`` and
    every :func:`lazy_fetch` call short-circuits to ``False``.
    """
    global _INITIALIZED, _CONFIG, _CLIENT
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        # Load .env from the repo root (parent of the ``ui/`` directory
        # this module lives in).
        here = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.dirname(here)
        env_path = os.path.join(repo_root, ".env")
        load_dotenv(env_path)

        cfg = R2Config.from_env()
        if cfg is None:
            _LOG.info(
                "R2 not configured (missing R2_ENDPOINT / R2_BUCKET / "
                "R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY). Token "
                "fetching is disabled; only tokens already on disk "
                "will be served."
            )
            _INITIALIZED = True
            return
        if not _BOTO3_AVAILABLE:
            _LOG.warning(
                "R2 is configured but boto3 isn't installed. Run "
                "`pip install -r requirements.txt`. Token fetching "
                "is disabled."
            )
            _INITIALIZED = True
            return

        _CONFIG = cfg
        _CLIENT = boto3.client(  # type: ignore[union-attr]
            "s3",
            endpoint_url=cfg.endpoint,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            region_name="auto",  # R2 ignores region but boto3 wants one
            config=BotoConfig(  # type: ignore[misc]
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        _LOG.info(
            "R2 token fetcher initialized (bucket=%s, endpoint=%s)",
            cfg.bucket, cfg.endpoint,
        )
        _INITIALIZED = True


def _key_lock(filename: str) -> threading.Lock:
    """Return (creating if needed) a per-filename lock so we don't
    fetch the same token twice when two requests race."""
    with _PER_KEY_LOCKS_GUARD:
        lock = _PER_KEY_LOCKS.get(filename)
        if lock is None:
            lock = threading.Lock()
            _PER_KEY_LOCKS[filename] = lock
        return lock


def is_enabled() -> bool:
    """Return True if R2 is configured and ready to serve fetches."""
    _ensure_initialized()
    return _CLIENT is not None


def lazy_fetch(filename: str, dest_path: str) -> bool:
    """Download ``filename`` from the R2 bucket to ``dest_path``.

    The destination is written *atomically* via a tempfile + rename, so
    a partially-downloaded file never appears on disk (and another
    request never observes a half-written PNG). If the destination
    already exists when we acquire the per-key lock, we treat that as a
    win (a parallel request beat us to it) and return True without
    re-fetching.

    Returns:
        True on success (file is now on disk at ``dest_path``).
        False if R2 isn't configured, the object doesn't exist, or any
        other error prevented the download. Errors are logged.
    """
    # Warm-cache fast path. Important to check this BEFORE the disabled
    # short-circuit: if the file is already on disk we're trivially done,
    # whether or not R2 happens to be configured right now.
    if os.path.isfile(dest_path):
        return True

    _ensure_initialized()
    if _CLIENT is None or _CONFIG is None:
        return False

    lock = _key_lock(filename)
    with lock:
        # Re-check after acquiring the lock: another thread may have
        # just finished fetching this same filename.
        if os.path.isfile(dest_path):
            return True

        dest_dir = os.path.dirname(dest_path)
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            _LOG.warning("R2 fetch: cannot create %s: %s", dest_dir, exc)
            return False

        # Write to a tempfile in the same directory so os.rename is
        # atomic (rename is only atomic within a single filesystem).
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=".r2_", suffix=".part", dir=dest_dir,
        )
        os.close(tmp_fd)
        try:
            try:
                _CLIENT.download_file(  # type: ignore[union-attr]
                    Bucket=_CONFIG.bucket,
                    Key=filename,
                    Filename=tmp_path,
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("NoSuchKey", "404"):
                    _LOG.info("R2 fetch: %s not found in bucket", filename)
                else:
                    _LOG.warning("R2 fetch %s: %s", filename, exc)
                return False
            except (BotoCoreError, NoCredentialsError, OSError) as exc:
                _LOG.warning("R2 fetch %s: %s", filename, exc)
                return False
            os.rename(tmp_path, dest_path)
            _LOG.info("R2 fetch: %s -> %s", filename, dest_path)
            return True
        finally:
            # Clean up the tempfile if rename never happened (any
            # error path above).
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
