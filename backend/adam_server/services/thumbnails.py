"""Cached first-page thumbnails for the library view.

PyMuPDF is not thread-safe, and ingestion also uses it in the API process. Rendering
therefore happens in a small spawned worker-process pool, so thumbnail work never shares
a MuPDF context with request threads and needs no lock coordinated with ingestion. This
module deliberately avoids importing the app's settings or database so spawned workers
start with only PyMuPDF loaded.
"""

import asyncio
import multiprocessing
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import fitz

# Bump when rendering output changes; the version is part of the cache filename and ETag.
THUMBNAIL_VERSION = 1
THUMBNAIL_WIDTHS = (270, 540)
THUMBNAIL_MEDIA_TYPE = "image/jpeg"
MAX_ASPECT_RATIO = 2.0
JPEG_QUALITY = 82
MAX_WORKERS = 2
MAX_PENDING_RENDERS = 64

_executor: ProcessPoolExecutor | None = None
_inflight: dict[Path, asyncio.Future[None]] = {}


class ThumbnailBusy(Exception):
    """Raised when too many distinct thumbnails are already queued."""


def thumbnail_dir(data_dir: Path, sha256: str) -> Path:
    return data_dir / "derived" / "thumbnails" / sha256[:2] / sha256


def thumbnail_path(data_dir: Path, sha256: str, width: int) -> Path:
    return thumbnail_dir(data_dir, sha256) / f"page1-v{THUMBNAIL_VERSION}-w{width}.jpg"


def thumbnail_etag(sha256: str, width: int) -> str:
    return f'"{sha256}-p1-v{THUMBNAIL_VERSION}-w{width}"'


def render_thumbnail(source: str, destination: str, width: int) -> None:
    """Render page 1 to a JPEG and publish it atomically. Runs in a worker process."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}-{os.urandom(6).hex()}.tmp")
    try:
        with fitz.open(source) as pdf:
            page = pdf[0]
            rect = page.rect
            scale = width / rect.width
            # Very tall pages are cropped to the top; the card only shows the first part anyway.
            clip = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + min(rect.height, rect.width * MAX_ASPECT_RATIO))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
            temp.write_bytes(pixmap.tobytes("jpg", jpg_quality=JPEG_QUALITY))
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        # Spawn rather than fork: the API process has threads and an open MuPDF context.
        _executor = ProcessPoolExecutor(max_workers=MAX_WORKERS, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=100)
    return _executor


def shutdown_thumbnail_workers() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True, cancel_futures=True)
        _executor = None


async def _render(source: Path, destination: Path, width: int) -> None:
    global _executor
    executor = _get_executor()
    try:
        await asyncio.get_running_loop().run_in_executor(executor, render_thumbnail, str(source), str(destination), width)
    except BrokenProcessPool:
        # A crashed worker (for example on a hostile PDF) poisons the pool; replace it for later requests.
        if _executor is executor:
            _executor = None
        raise


def _finish(destination: Path, done: asyncio.Future[None]) -> None:
    _inflight.pop(destination, None)
    if not done.cancelled():
        done.exception()  # Mark as retrieved; awaiting requests receive the error themselves.


async def ensure_thumbnail(data_dir: Path, sha256: str, source: Path, width: int) -> Path:
    """Return the cached thumbnail path, rendering it once if needed.

    Concurrent requests for the same thumbnail share one render, and the number of
    distinct renders waiting for a worker is capped so a large library cannot queue
    unbounded work.
    """
    if width not in THUMBNAIL_WIDTHS:
        raise ValueError(f"Unsupported thumbnail width: {width}")
    destination = thumbnail_path(data_dir, sha256, width)
    if destination.is_file():
        return destination
    pending = _inflight.get(destination)
    if pending is None:
        if len(_inflight) >= MAX_PENDING_RENDERS:
            raise ThumbnailBusy()
        pending = asyncio.ensure_future(_render(source, destination, width))
        _inflight[destination] = pending
        pending.add_done_callback(lambda done: _finish(destination, done))
    # Shield so one disconnected client does not cancel a render other requests are awaiting.
    await asyncio.shield(pending)
    return destination


def remove_thumbnails(data_dir: Path, sha256: str) -> None:
    directory = thumbnail_dir(data_dir, sha256)
    shutil.rmtree(directory, ignore_errors=True)
    try:
        directory.parent.rmdir()
    except OSError:
        pass
