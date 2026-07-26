"""Minimal persistence operations for literature records."""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from src.models import Literature


_LITERATURE_COLUMNS = (
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
)

_SELECT_COLUMNS = ("id",) + _LITERATURE_COLUMNS + ("created_at", "updated_at")
_UPDATABLE_LITERATURE_COLUMNS = frozenset(_LITERATURE_COLUMNS)


def _row_to_literature(row: sqlite3.Row) -> Literature:
    """Convert a literature table row to the repository's model."""
    return Literature(**dict(row))


def _utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def _next_updated_at(previous_updated_at: str) -> str:
    """Return a UTC timestamp strictly later than the previous timestamp."""
    previous = datetime.fromisoformat(
        previous_updated_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    current = _utc_now()
    next_timestamp = (
        current
        if current > previous
        else previous + timedelta(microseconds=1)
    )
    return next_timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def add_literature(connection: sqlite3.Connection, literature: Literature) -> int:
    """Insert one literature record and return its generated ID."""
    column_names = ", ".join(_LITERATURE_COLUMNS)
    placeholders = ", ".join("?" for _ in _LITERATURE_COLUMNS)
    values = tuple(getattr(literature, column) for column in _LITERATURE_COLUMNS)

    with connection:
        cursor = connection.execute(
            f"INSERT INTO literature ({column_names}) VALUES ({placeholders})",
            values,
        )

    if cursor.lastrowid is None:
        raise RuntimeError("文献IDを取得できませんでした。")
    return cursor.lastrowid


def get_literature(
    connection: sqlite3.Connection, literature_id: int
) -> Optional[Literature]:
    """Return one literature record, or None when the ID does not exist."""
    columns = ", ".join(_SELECT_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM literature WHERE id = ?",
        (literature_id,),
    ).fetchone()

    if row is None:
        return None
    return _row_to_literature(row)


def list_literature(connection: sqlite3.Connection) -> list[Literature]:
    """Return all literature records ordered by ascending ID."""
    columns = ", ".join(_SELECT_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM literature ORDER BY id ASC"
    ).fetchall()
    return [_row_to_literature(row) for row in rows]


def update_literature(
    connection: sqlite3.Connection,
    literature_id: int,
    updates: Mapping[str, object],
) -> bool:
    """Update allowed fields and return whether the literature record existed."""
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")

    invalid_columns = set(updates) - _UPDATABLE_LITERATURE_COLUMNS
    if invalid_columns:
        invalid_names = ", ".join(sorted(repr(column) for column in invalid_columns))
        raise ValueError(f"更新できない項目が指定されました: {invalid_names}")

    columns = ", ".join(_SELECT_COLUMNS)
    current_row = connection.execute(
        f"SELECT {columns} FROM literature WHERE id = ?",
        (literature_id,),
    ).fetchone()
    if current_row is None:
        return False

    candidate_values = dict(current_row)
    candidate_values.update(updates)
    Literature(**candidate_values)

    update_columns = tuple(updates)
    assignments = ", ".join(f"{column} = ?" for column in update_columns)
    updated_at = _next_updated_at(current_row["updated_at"])
    values = tuple(updates[column] for column in update_columns)

    with connection:
        cursor = connection.execute(
            f"""
            UPDATE literature
            SET {assignments}, updated_at = ?
            WHERE id = ?
            """,
            values + (updated_at, literature_id),
        )

    return cursor.rowcount == 1


def get_literature_related_counts(
    connection: sqlite3.Connection, literature_id: int
) -> Optional[dict[str, int]]:
    """Return related row counts, or None when the literature ID is unknown."""
    row = connection.execute(
        """
        SELECT
            (
                SELECT COUNT(*)
                FROM literature_tags
                WHERE literature_id = literature.id
            ) AS tag_count,
            (
                SELECT COUNT(*)
                FROM usage_history
                WHERE literature_id = literature.id
            ) AS usage_history_count
        FROM literature
        WHERE id = ?
        """,
        (literature_id,),
    ).fetchone()

    if row is None:
        return None
    return {
        "tag_count": row["tag_count"],
        "usage_history_count": row["usage_history_count"],
    }


def delete_literature(
    connection: sqlite3.Connection, literature_id: int
) -> bool:
    """Delete one literature record and return whether it existed."""
    with connection:
        cursor = connection.execute(
            "DELETE FROM literature WHERE id = ?",
            (literature_id,),
        )
    return cursor.rowcount == 1
