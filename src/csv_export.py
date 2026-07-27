"""Read-only CSV export for literature records."""

import csv
import os
import sqlite3
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Optional


_LITERATURE_COLUMNS = (
    "id",
    "title",
    "authors",
    "journal",
    "publication_year",
    "volume",
    "issue",
    "pages",
    "doi",
    "pmid",
    "url",
    "language",
    "publication_type",
    "abstract",
    "pdf_path",
    "personal_summary",
    "ai_summary",
    "ai_summary_status",
    "general_note",
    "key_findings",
    "methods_note",
    "clinical_note",
    "limitation_note",
    "relevance_note",
    "evidence_level",
    "verification_status",
    "adoption_status",
    "exclusion_reason",
    "rating",
    "created_at",
    "updated_at",
)
_CSV_COLUMNS = _LITERATURE_COLUMNS + ("tags",)
_REJECTED_ID_SEQUENCE_TYPES = (str, bytes, bytearray, memoryview)
_SQLITE_INTEGER_MAX = 2**63 - 1


def _validate_output_path(output_path: object) -> Path:
    """Return a usable output path without creating its parent directory."""
    if isinstance(output_path, str):
        path_value = output_path
    elif isinstance(output_path, os.PathLike):
        try:
            path_value = os.fspath(output_path)
        except TypeError as error:
            raise ValueError(
                "output_pathはstrまたは文字列パスを返すos.PathLikeで指定してください。"
            ) from error
        if not isinstance(path_value, str):
            raise ValueError(
                "output_pathはstrまたは文字列パスを返すos.PathLikeで指定してください。"
            )
    else:
        raise ValueError("output_pathはstrまたはos.PathLikeで指定してください。")

    if not path_value.strip():
        raise ValueError("output_pathは空でないパスを指定してください。")

    path = Path(path_value)
    if path.is_dir():
        raise ValueError("output_pathに既存ディレクトリは指定できません。")
    if not path.parent.exists():
        raise FileNotFoundError(
            f"出力先の親ディレクトリが存在しません: {path.parent}"
        )
    if not path.parent.is_dir():
        raise NotADirectoryError(
            f"出力先の親パスがディレクトリではありません: {path.parent}"
        )
    return path


def _validate_literature_ids(
    literature_ids: object,
) -> Optional[tuple[int, ...]]:
    """Validate, deduplicate, and sort an optional finite ID sequence."""
    if literature_ids is None:
        return None
    if isinstance(literature_ids, _REJECTED_ID_SEQUENCE_TYPES) or not isinstance(
        literature_ids,
        Sequence,
    ):
        raise ValueError(
            "literature_idsはNoneまたは正の整数からなる有限なシーケンス"
            "で指定してください。"
        )

    unique_ids: set[int] = set()
    for literature_id in literature_ids:
        if (
            isinstance(literature_id, bool)
            or not isinstance(literature_id, int)
            or literature_id <= 0
            or literature_id > _SQLITE_INTEGER_MAX
        ):
            raise ValueError(
                "literature_idsの各要素はboolを除く正の整数"
                f"（許可範囲: 1以上{_SQLITE_INTEGER_MAX}以下）"
                f"で指定してください。受け取った値: {literature_id!r}"
            )
        unique_ids.add(literature_id)
    return tuple(sorted(unique_ids))


def _fetch_literature_rows(
    connection: sqlite3.Connection,
    literature_ids: Optional[tuple[int, ...]],
) -> list[tuple[object, ...]]:
    """Fetch the requested literature in ascending ID order with one SELECT."""
    if literature_ids == ():
        return []

    columns = ", ".join(_LITERATURE_COLUMNS)
    parameters: tuple[int, ...] = ()
    where_clause = ""
    if literature_ids is not None:
        placeholders = ", ".join("?" for _ in literature_ids)
        where_clause = f"WHERE id IN ({placeholders})"
        parameters = literature_ids

    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM literature
        {where_clause}
        ORDER BY id ASC
        """,
        parameters,
    ).fetchall()
    result = [tuple(row) for row in rows]

    if literature_ids is not None:
        found_ids = {row[0] for row in result}
        missing_ids = set(literature_ids) - found_ids
        if missing_ids:
            missing_text = ", ".join(str(item) for item in sorted(missing_ids))
            raise ValueError(f"存在しない文献IDが指定されました: {missing_text}")
    return result


def _fetch_tags_by_literature(
    connection: sqlite3.Connection,
    literature_rows: Sequence[tuple[object, ...]],
) -> dict[int, list[str]]:
    """Fetch all target tags once and group them by literature ID."""
    if not literature_rows:
        return {}

    literature_ids = tuple(int(row[0]) for row in literature_rows)
    placeholders = ", ".join("?" for _ in literature_ids)
    rows = connection.execute(
        f"""
        SELECT
            literature_tags.literature_id,
            tags.id,
            tags.name
        FROM literature_tags
        JOIN tags ON tags.id = literature_tags.tag_id
        WHERE literature_tags.literature_id IN ({placeholders})
        ORDER BY
            literature_tags.literature_id ASC,
            tags.name COLLATE NOCASE ASC,
            tags.id ASC
        """,
        literature_ids,
    ).fetchall()

    tags_by_literature: dict[int, list[str]] = {}
    seen_tag_ids: dict[int, set[int]] = {}
    for row in rows:
        literature_id = int(row[0])
        tag_id = int(row[1])
        literature_tag_ids = seen_tag_ids.setdefault(literature_id, set())
        if tag_id in literature_tag_ids:
            continue
        literature_tag_ids.add(tag_id)
        tags_by_literature.setdefault(literature_id, []).append(row[2])
    return tags_by_literature


def _write_csv_atomically(
    output_path: Path,
    literature_rows: Sequence[tuple[object, ...]],
    tags_by_literature: dict[int, list[str]],
) -> None:
    """Write a complete temporary CSV before replacing the destination."""
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            writer = csv.writer(temporary_file)
            writer.writerow(_CSV_COLUMNS)
            for row in literature_rows:
                csv_values = ["" if value is None else value for value in row]
                tags = ";".join(tags_by_literature.get(int(row[0]), ()))
                writer.writerow((*csv_values, tags))

        os.replace(temporary_path, output_path)
        temporary_path = None
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        raise


def export_literature_csv(
    connection: sqlite3.Connection,
    output_path: object,
    *,
    literature_ids: object = None,
) -> int:
    """Export selected literature to a UTF-8 BOM CSV and return its row count."""
    validated_output_path = _validate_output_path(output_path)
    validated_ids = _validate_literature_ids(literature_ids)
    literature_rows = _fetch_literature_rows(connection, validated_ids)
    tags_by_literature = _fetch_tags_by_literature(
        connection,
        literature_rows,
    )
    _write_csv_atomically(
        validated_output_path,
        literature_rows,
        tags_by_literature,
    )
    return len(literature_rows)
