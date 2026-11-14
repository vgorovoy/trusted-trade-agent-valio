"""Append-only run allocation and SHA-256 archives; tamper-evident, not WORM storage.

Only explicitly supplied JSON and benchmark artifacts are archived. No credentials,
.env files or process environment are collected. A manifest marks finalization;
.partial folders are incomplete and must never be counted as completed runs.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(project: Path) -> dict[str, Any]:
    """Hash only known public code/config inputs; never enumerate or read .env files."""
    project = project.resolve()
    paths = sorted((project / "src").rglob("*.py")) + sorted((project / "scripts").glob("*.py"))
    paths += [project / name for name in ("pyproject.toml", "uv.lock") if (project / name).is_file()]
    hashes = {str(path.relative_to(project)): _hash(path) for path in paths if not path.is_symlink()}
    def git(*args):
        try:
            return subprocess.check_output(["git", "-C", str(project), *args],
                                           text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    dirty = git("status", "--porcelain", "--untracked-files=normal")
    return {"git_sha": git("rev-parse", "HEAD"), "dirty": bool(dirty) if dirty is not None else None,
            "code_sha256": hashlib.sha256(_json(hashes).encode()).hexdigest(), "code_files": hashes}


class RunArchive:
    """Allocate once, write in staging, publish a read-only folder with its manifest last."""

    def __init__(self, root: Path, run_id: str | None = None) -> None:
        self.run_id = run_id or (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
        if not _ID.fullmatch(self.run_id):
            raise ValueError("run id must be a single safe filename")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.final_path = root / self.run_id
        self.staging_path = root / f".{self.run_id}.partial"
        if self.final_path.exists():
            raise FileExistsError(f"run {self.run_id} already exists")
        self.staging_path.mkdir(exist_ok=False)
        self._finalized = False

    def _path(self, name: str) -> Path:
        if self._finalized:
            raise RuntimeError("archive already finalized")
        path = self.staging_path / name
        if (Path(name).is_absolute() or ".." in Path(name).parts or name == "manifest.json"
                or path.is_symlink()):
            raise ValueError("artifact must remain inside the archive; manifest is reserved")
        path.parent.mkdir(parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in path.parents if parent != self.staging_path.parent):
            raise ValueError("symlink artifacts are not supported")
        return path

    def write_json(self, name: str, value: Any) -> None:
        with self._path(name).open("x", encoding="utf-8") as output:
            output.write(_json(value))

    def append_event(self, value: Any) -> None:
        with self._path("events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")

    def finalize(self, *, status: str = "completed") -> Path:
        if self._finalized:
            raise RuntimeError("archive already finalized")
        if status not in {"completed", "error"}:
            raise ValueError("archive status must be completed or error")
        files = _files(self.staging_path)
        manifest = {"version": 1, "run_id": self.run_id, "status": status,
                    "finalized_at": datetime.now(UTC).isoformat(),
                    "artifacts": {str(p.relative_to(self.staging_path)): _hash(p) for p in files}}
        # Allocate without replacing a completed or incomplete existing archive. A crash
        # while moving leaves no manifest, so verification fails rather than claiming success.
        self.final_path.mkdir(exist_ok=False)
        for entry in self.staging_path.iterdir():
            entry.rename(self.final_path / entry.name)
        (self.final_path / "manifest.json").write_text(_json(manifest), encoding="utf-8")
        self.staging_path.rmdir()
        for path in self.final_path.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        self.final_path.chmod(0o555)
        self._finalized = True
        return self.final_path


def _files(root: Path) -> list[Path]:
    paths = list(root.rglob("*"))
    if root.is_symlink() or any(p.is_symlink() for p in paths):
        raise ValueError("symlinks are not supported in run archives")
    return sorted(p for p in paths if p.is_file())


def verify_run(path: Path) -> dict[str, Any]:
    """Reject absent, modified, additional artifacts and incomplete archives."""
    manifest_path = path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("archive has no valid completion manifest") from exc
    if manifest.get("version") != 1 or manifest.get("status") not in {"completed", "error"}:
        raise ValueError("invalid archive manifest")
    actual = {str(p.relative_to(path)): _hash(p) for p in _files(path) if p != manifest_path}
    if actual != manifest.get("artifacts"):
        raise ValueError("archive artifacts are modified, missing or unexpected")
    return manifest
