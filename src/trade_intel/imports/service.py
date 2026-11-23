"""Safe upload spooling, import recording and post-import matching."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select

from trade_intel.core.logging import get_logger
from trade_intel.db import session_scope
from trade_intel.db.models import Demand, ImportRecord, SourceType
from trade_intel.matching import build_inventory_index
from trade_intel.ml import HashEmbedder
from trade_intel.parsers import parse_csv, parse_excel, parse_file
from trade_intel.persistence import ingest_messages, match_demand_against_inventory

log = get_logger(__name__, component="imports")

_CHUNK = 1024 * 1024
_UNSAFE = re.compile(r"[^A-Za-z0-9._ \-()]+")
_SUFFIX = {
    SourceType.WHATSAPP: ".txt",
    SourceType.CSV: ".csv",
    SourceType.EXCEL: ".xlsx",
}


class UploadTooLargeError(Exception):
    """Raised when an upload exceeds the configured byte cap."""


class _Readable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


def safe_filename(name: str | None) -> str:
    """Display-safe basename: no directories, no control/odd chars, <=255."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _UNSAFE.sub("_", base).strip(" .")
    return (base or "upload")[:255]


async def spool_upload(
    upload: _Readable, *, source_type: SourceType, max_bytes: int, suffix: str | None = None
) -> tuple[Path, str, int]:
    """Stream an upload into a private temp file (0600, server-chosen name).

    The client-supplied filename never touches the filesystem path.
    Returns ``(path, sha256_hex, size)``; caller must unlink ``path``.
    """
    digest = hashlib.sha256()
    size = 0
    fd, tmp = tempfile.mkstemp(prefix="ti_import_", suffix=suffix or _SUFFIX[source_type])
    path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(f"upload exceeds {max_bytes // (1024 * 1024)} MB")
                digest.update(chunk)
                fh.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, digest.hexdigest(), size


def extract_chat_from_zip(zip_path: Path, *, max_bytes: int) -> Path:
    """Pull the single WhatsApp chat ``.txt`` out of an export ``.zip``.

    Media and every other member are ignored and never written to disk.
    The member is streamed into a server-named temp file with a hard cap on
    *decompressed* bytes (zip-bomb guard); member names are never used as paths.
    Raises ``ValueError`` if the archive is unreadable or has no chat text.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("not a valid zip archive") from exc
    with zf:
        texts = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".txt")]
        if not texts:
            raise ValueError("zip has no .txt chat export inside")
        member = max(texts, key=lambda i: i.file_size)
        if member.file_size > max_bytes:
            raise UploadTooLargeError(f"chat text exceeds {max_bytes // (1024 * 1024)} MB")
        fd, tmp = tempfile.mkstemp(prefix="ti_chat_", suffix=".txt")
        out = Path(tmp)
        try:
            written = 0
            with zf.open(member) as src, os.fdopen(fd, "wb") as dst:
                while chunk := src.read(_CHUNK):
                    written += len(chunk)
                    if written > max_bytes:
                        raise UploadTooLargeError(
                            f"chat text exceeds {max_bytes // (1024 * 1024)} MB"
                        )
                    dst.write(chunk)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
    return out


def run_import(
    path: Path,
    *,
    filename: str,
    source_type: SourceType,
    uploader: str,
    sha256: str,
    size: int,
    group_name: str | None = None,
) -> tuple[int, dict[str, int]]:
    """Parse + persist one file and record an :class:`ImportRecord`.

    Returns ``(import_id, stats)``. Parser/ingest failures are recorded on the
    row (status=failed) and re-raised so the caller can return an error.
    """
    with session_scope() as session:
        rec = ImportRecord(
            uploader=uploader,
            filename=filename,
            source_format=source_type,
            status="running",
            file_sha256=sha256,
            bytes_size=size,
            messages_seen=0,
            messages_inserted=0,
            messages_skipped_dup=0,
            messages_skipped_other=0,
            inventory_inserted=0,
            demand_inserted=0,
            matches_written=0,
            errors=0,
        )
        session.add(rec)
        session.flush()
        import_id = rec.id

    try:
        if source_type is SourceType.WHATSAPP:
            parsed = parse_file(path)
        elif source_type is SourceType.CSV:
            parsed = parse_csv(path)
        elif source_type is SourceType.EXCEL:
            parsed = parse_excel(path)
        else:
            raise ValueError(f"unsupported source type {source_type!r}")
        with session_scope() as session:
            stats = ingest_messages(
                parsed,
                session=session,
                source_type=source_type,
                group_name=group_name or f"{uploader}: {filename}",
                source_path=f"import:{import_id}:{filename}",
            )
    except Exception as exc:
        with session_scope() as session:
            failed = session.get(ImportRecord, import_id)
            if failed is not None:
                failed.status = "failed"
                failed.errors = 1
                failed.error_detail = str(exc)[:1000]
                failed.finished_at = datetime.now(UTC).replace(tzinfo=None)
        raise

    d = stats.as_dict()
    other = (
        d["messages_skipped_system"]
        + d["messages_skipped_media"]
        + d["messages_skipped_malformed"]
        + d["messages_skipped_orphan"]
    )
    with session_scope() as session:
        done = session.get(ImportRecord, import_id)
        assert done is not None
        done.messages_seen = d["messages_seen"]
        done.messages_inserted = d["messages_inserted"]
        done.messages_skipped_dup = d["messages_skipped_dup"]
        done.messages_skipped_other = other
        done.inventory_inserted = d["inventory_inserted"]
        done.demand_inserted = d["demand_inserted"]
        done.errors = d["errors"]
        done.status = "matching"
    return import_id, d


def run_matching(import_id: int | None = None) -> int:
    """Rescore every active Demand against current inventory.

    Updates the import row (matches_written, status=completed) when given.
    Returns the number of matches created/updated. Never raises into the
    caller's response path — failures are recorded on the import row.
    """
    written = 0
    try:
        embedder = HashEmbedder()
        now = datetime.now(UTC).replace(tzinfo=None)
        with session_scope() as session:
            index, _ = build_inventory_index(session, embedder=embedder)
            if index.size:
                demands = session.scalars(
                    select(Demand).where(Demand.is_active.is_(True))
                ).all()
                for demand in demands:
                    st = match_demand_against_inventory(
                        demand,
                        session=session,
                        embedder=embedder,
                        inventory_index=index,
                        now=now,
                    )
                    written += st.matches_created + st.matches_updated
        status, detail = "completed", None
    except Exception as exc:
        log.exception("matching_failed", import_id=import_id)
        status, detail = "match_failed", str(exc)[:1000]

    if import_id is not None:
        with session_scope() as session:
            rec = session.get(ImportRecord, import_id)
            if rec is not None:
                rec.matches_written = written
                rec.status = status
                rec.error_detail = detail
                rec.finished_at = datetime.now(UTC).replace(tzinfo=None)
    _invalidate_broker_cache()
    return written


def _invalidate_broker_cache() -> None:
    """Best-effort ping so chat never serves stale cached answers."""
    import urllib.request

    from trade_intel.config import get_settings

    try:
        req = urllib.request.Request(
            get_settings().broker_url.rstrip("/") + "/cache/invalidate",
            data=b"",
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).close()
    except Exception:
        pass


