"""Safe SQLite database backup creation."""

import os
import sqlite3
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


_BACKUP_PREFIX = "pt_research_library_backup_"
_BACKUP_SUFFIX = ".sqlite3"
_TEMPORARY_PREFIX = ".pt_research_library_backup_in_progress_"
_TEMPORARY_SUFFIX = ".tmp"
_MAX_PUBLISH_ATTEMPTS = 1000


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _normalize_backup_directory(backup_directory: object) -> Path:
    """Return a directory path represented by a string path."""
    if isinstance(backup_directory, str):
        path_value = backup_directory
    elif isinstance(backup_directory, os.PathLike):
        try:
            path_value = os.fspath(backup_directory)
        except TypeError as error:
            raise ValueError(
                "backup_directoryはstrまたは文字列パスを返す"
                "os.PathLikeで指定してください。"
            ) from error
        if not isinstance(path_value, str):
            raise ValueError(
                "backup_directoryはstrまたは文字列パスを返す"
                "os.PathLikeで指定してください。"
            )
    else:
        raise ValueError(
            "backup_directoryはstrまたはos.PathLikeで指定してください。"
        )

    if not path_value.strip():
        raise ValueError(
            "backup_directoryは空でないディレクトリパスを指定してください。"
        )

    return Path(path_value)


def _require_existing_directory(directory: Path) -> None:
    """Require an existing directory without creating it."""
    mode = directory.stat().st_mode
    if not stat.S_ISDIR(mode):
        raise NotADirectoryError(
            f"バックアップ先がディレクトリではありません: {directory}"
        )


def _timestamp_text() -> str:
    """Return a filesystem-safe UTC timestamp with microseconds."""
    return _utc_now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _candidate_path(
    backup_directory: Path,
    timestamp: str,
    collision_number: int,
) -> Path:
    """Build one deterministic final backup path."""
    collision_suffix = (
        "" if collision_number == 0 else f"_{collision_number}"
    )
    return backup_directory / (
        f"{_BACKUP_PREFIX}{timestamp}{collision_suffix}{_BACKUP_SUFFIX}"
    )


def _close_after_operation(
    connection: sqlite3.Connection,
    operation,
) -> object:
    """Run an operation and close without hiding its original exception."""
    try:
        result = operation()
    except BaseException:
        try:
            connection.close()
        except BaseException:
            pass
        raise
    connection.close()
    return result


def _copy_database(
    source: sqlite3.Connection,
    temporary_path: Path,
) -> None:
    """Copy the entire source database into a temporary SQLite file."""
    destination = sqlite3.connect(temporary_path)
    _close_after_operation(
        destination,
        lambda: source.backup(destination),
    )


def _verify_backup(temporary_path: Path) -> None:
    """Require the temporary backup's quick_check result to be exactly ok."""
    verification_connection = sqlite3.connect(temporary_path)
    rows = _close_after_operation(
        verification_connection,
        lambda: verification_connection.execute(
            "PRAGMA quick_check"
        ).fetchall(),
    )
    results = [row[0] for row in rows]
    if results != ["ok"]:
        details = "; ".join(str(result) for result in results)
        raise sqlite3.DatabaseError(
            "バックアップのPRAGMA quick_checkが失敗しました: "
            f"{details or '結果なし'}"
        )


def _cleanup_temporary_file(temporary_path: Path) -> None:
    """Best-effort removal that never hides an earlier failure."""
    try:
        os.unlink(temporary_path)
    except OSError:
        pass


def _publish_without_overwrite(
    temporary_path: Path,
    backup_directory: Path,
    timestamp: str,
) -> Path:
    """Atomically create a final hard link without replacing existing paths."""
    for collision_number in range(_MAX_PUBLISH_ATTEMPTS):
        final_path = _candidate_path(
            backup_directory,
            timestamp,
            collision_number,
        )
        try:
            os.link(temporary_path, final_path)
        except FileExistsError:
            continue

        _cleanup_temporary_file(temporary_path)
        return final_path

    raise FileExistsError(
        "利用可能なバックアップ名を1000候補以内で確保できませんでした。"
    )


def create_database_backup(
    connection: sqlite3.Connection,
    backup_directory: object,
) -> Path:
    """Create and verify one non-overwriting SQLite backup."""
    if not isinstance(connection, sqlite3.Connection):
        raise ValueError("connectionはsqlite3.Connectionで指定してください。")

    directory = _normalize_backup_directory(backup_directory)

    if connection.in_transaction:
        raise ValueError(
            "未Commitトランザクションをcommitまたはrollbackで終了してから、"
            "バックアップを再実行してください。"
        )

    _require_existing_directory(directory)

    timestamp = _timestamp_text()

    temporary_path = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=_TEMPORARY_PREFIX,
            suffix=_TEMPORARY_SUFFIX,
        )
        temporary_path = Path(temporary_name)
        os.close(descriptor)

        _copy_database(connection, temporary_path)
        _verify_backup(temporary_path)
        final_path = _publish_without_overwrite(
            temporary_path,
            directory,
            timestamp,
        )
        temporary_path = None
        return final_path
    finally:
        if temporary_path is not None:
            _cleanup_temporary_file(temporary_path)
