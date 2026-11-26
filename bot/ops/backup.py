"""Atomic backups and checksum-verified restores for the live paper loop.

A backup is one gzip tar archive plus a sidecar ``*.sha256``.  The archive contains
``manifest.json`` (per-file SHA-256 and the original absolute target) followed by
the state directory, the paper result CSVs and the optional live TOML config.  The
sidecar protects the archive as a whole; the manifest protects each member against
silent corruption after extraction.

Restore is deliberately conservative: it verifies both layers, refuses to
overwrite existing files unless ``force=True``, and stages each file before an
atomic ``os.replace``.  It never follows a path outside the requested roots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
from typing import Iterable

FORMAT = 1
MANIFEST = "manifest.json"


class BackupError(RuntimeError):
    pass


@dataclass
class BackupEntry:
    arcname: str
    target: str
    sha256: str
    size: int


@dataclass
class BackupInfo:
    path: Path
    checksum_path: Path
    digest: str
    created_at: str
    entries: list[BackupEntry] = field(default_factory=list)


@dataclass
class BackupVerification:
    ok: bool
    path: Path
    digest: str | None = None
    created_at: str | None = None
    entries: list[BackupEntry] = field(default_factory=list)
    error: str | None = None


@dataclass
class RestoreResult:
    archive: Path
    digest: str
    restored: int
    targets: list[str] = field(default_factory=list)


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def checksum_path(archive: Path) -> Path:
    return archive.with_name(archive.name + ".sha256")


def _safe_arcname(name: str) -> str:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise BackupError(f"unsafe archive member {name!r}")
    return p.as_posix()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _sources(
    root: Path,
    state_dir: Path,
    results_dir: Path,
    config_paths: Iterable[Path] | None,
    exclude: Path | None = None,
) -> list[tuple[str, Path]]:
    """(archive name, source path) pairs, deterministic and duplicate-free."""
    out: list[tuple[str, Path]] = []
    seen_arc: set[str] = set()

    def add(arc: str, path: Path) -> None:
        if not path.is_file():
            return
        if exclude is not None and (
            _inside(path, exclude) or path.resolve() == exclude.resolve()
        ):
            return
        arc = _safe_arcname(arc)
        if arc in seen_arc:
            raise BackupError(f"duplicate archive member {arc!r}")
        seen_arc.add(arc)
        out.append((arc, path))

    if state_dir.exists():
        for path in sorted(state_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(state_dir).as_posix()
                add(f"state/{rel}", path)

    if results_dir.exists():
        for path in sorted(results_dir.glob("paper_*.csv")):
            add(f"results/{path.name}", path)

    candidates: list[Path] = []
    if config_paths is not None:
        candidates.extend(Path(p) for p in config_paths)
    else:
        for name in ("live.toml", "config.toml"):
            candidates.append(root / name)
        config_dir = root / "config"
        if config_dir.exists():
            candidates.extend(sorted(config_dir.glob("*.toml")))
    for path in candidates:
        if path.is_file():
            # Keep the basename; a collision is an operator error, not a silent merge.
            add(f"config/{path.name}", path)
    return out


def create_backup(
    root: str | Path,
    out_dir: str | Path | None = None,
    *,
    state_dir: str | Path | None = None,
    results_dir: str | Path | None = None,
    config_paths: Iterable[str | Path] | None = None,
    now: datetime | None = None,
) -> BackupInfo:
    """Write a verified-format archive atomically and return its metadata."""
    root = Path(root).resolve()
    state = Path(state_dir).resolve() if state_dir else root / "state"
    results = Path(results_dir).resolve() if results_dir else root / "results"
    out = Path(out_dir).resolve() if out_dir else state / "backups"
    out.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    archive = out / f"stonks-backup-{stamp}.tar.gz"
    n = 1
    while archive.exists() or checksum_path(archive).exists():
        n += 1
        archive = out / f"stonks-backup-{stamp}-{n}.tar.gz"

    sources = _sources(
        root,
        state,
        results,
        None if config_paths is None else [Path(p) for p in config_paths],
        exclude=out,
    )
    entries: list[BackupEntry] = []
    for arc, source in sources:
        entries.append(
            BackupEntry(
                arcname=arc,
                target=str(source.resolve()),
                sha256=sha256_file(source),
                size=source.stat().st_size,
            )
        )
    manifest = {
        "format": FORMAT,
        "created_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "root": str(root),
        "state_dir": str(state),
        "results_dir": str(results),
        "entries": [e.__dict__ for e in entries],
    }
    blob = json.dumps(manifest, indent=2, sort_keys=True).encode()

    tmp = out / f".{archive.name}.{os.getpid()}.tmp"
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            info = tarfile.TarInfo(MANIFEST)
            info.size = len(blob)
            info.mtime = int((now or datetime.now(timezone.utc)).timestamp())
            import io

            tar.addfile(info, io.BytesIO(blob))
            for entry in entries:
                tar.add(entry.target, arcname=entry.arcname, recursive=False)
        with open(tmp, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, archive)
    finally:
        if tmp.exists():
            tmp.unlink()

    digest = sha256_file(archive)
    sidecar = checksum_path(archive)
    tmp_side = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    tmp_side.write_text(f"{digest}  {archive.name}\n")
    os.replace(tmp_side, sidecar)
    return BackupInfo(
        path=archive,
        checksum_path=sidecar,
        digest=digest,
        created_at=manifest["created_at"],
        entries=entries,
    )


def verify_backup(path: str | Path) -> BackupVerification:
    """Verify the sidecar digest, manifest and every archived member hash."""
    archive = Path(path)
    sidecar = checksum_path(archive)
    if not archive.exists():
        return BackupVerification(False, archive, error=f"archive not found: {archive}")
    if not sidecar.exists():
        return BackupVerification(
            False, archive, error=f"checksum not found: {sidecar}"
        )
    try:
        expected = sidecar.read_text().split()[0].strip()
    except Exception as exc:  # noqa: BLE001
        return BackupVerification(False, archive, error=f"checksum unreadable: {exc}")
    try:
        digest = sha256_file(archive)
    except Exception as exc:  # noqa: BLE001
        return BackupVerification(False, archive, error=f"archive unreadable: {exc}")
    if digest != expected:
        return BackupVerification(
            False, archive, digest=digest, error="archive checksum mismatch"
        )
    try:
        with tarfile.open(archive, "r:gz") as tar:
            member = tar.getmember(MANIFEST)
            fh = tar.extractfile(member)
            if fh is None:
                raise BackupError("manifest is not a regular file")
            manifest = json.loads(fh.read().decode())
            if int(manifest.get("format", 0)) != FORMAT:
                raise BackupError(
                    f"unsupported backup format {manifest.get('format')!r}"
                )
            entries = [BackupEntry(**x) for x in manifest.get("entries", [])]
            for entry in entries:
                arc = _safe_arcname(entry.arcname)
                try:
                    member = tar.getmember(arc)
                except KeyError as exc:
                    raise BackupError(f"missing archived file {arc}") from exc
                fh = tar.extractfile(member)
                if fh is None:
                    raise BackupError(f"archive member is not a file: {arc}")
                h = hashlib.sha256()
                size = 0
                while True:
                    block = fh.read(1024 * 1024)
                    if not block:
                        break
                    h.update(block)
                    size += len(block)
                if h.hexdigest() != entry.sha256:
                    raise BackupError(f"member checksum mismatch: {arc}")
                if size != int(entry.size):
                    raise BackupError(f"member size mismatch: {arc}")
        return BackupVerification(
            True,
            archive,
            digest=digest,
            created_at=manifest.get("created_at"),
            entries=entries,
        )
    except Exception as exc:  # noqa: BLE001
        return BackupVerification(False, archive, digest=digest, error=str(exc))


def restore_backup(
    path: str | Path,
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    results_dir: str | Path | None = None,
    force: bool = False,
) -> RestoreResult:
    """Verify and restore every archived member to its original target."""
    archive = Path(path)
    root = Path(root).resolve()
    state = Path(state_dir).resolve() if state_dir else root / "state"
    results = Path(results_dir).resolve() if results_dir else root / "results"
    verification = verify_backup(archive)
    if not verification.ok:
        raise BackupError(verification.error or "backup verification failed")

    allowed = (root, state, results)
    conflicts = [
        e.target for e in verification.entries if Path(e.target).exists() and not force
    ]
    if conflicts:
        raise BackupError(
            "refusing to overwrite existing files without --force: "
            + ", ".join(conflicts[:5])
        )

    restored: list[str] = []
    with tarfile.open(archive, "r:gz") as tar:
        for entry in verification.entries:
            target = Path(entry.target)
            if not any(_inside(target, base) for base in allowed):
                raise BackupError(
                    f"refusing target outside state/results/root: {target}"
                )
            member = tar.getmember(_safe_arcname(entry.arcname))
            fh = tar.extractfile(member)
            if fh is None:
                raise BackupError(f"archive member is not a file: {entry.arcname}")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(f".{target.name}.{os.getpid()}.restore")
            try:
                h = hashlib.sha256()
                with open(tmp, "wb") as out:
                    while True:
                        block = fh.read(1024 * 1024)
                        if not block:
                            break
                        h.update(block)
                        out.write(block)
                    out.flush()
                    os.fsync(out.fileno())
                if h.hexdigest() != entry.sha256:
                    raise BackupError(
                        f"member checksum changed before restore: {entry.arcname}"
                    )
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink()
            restored.append(str(target))
    return RestoreResult(
        archive=archive,
        digest=verification.digest or "",
        restored=len(restored),
        targets=restored,
    )
