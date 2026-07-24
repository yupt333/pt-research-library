"""Minimal persistence operations for literature records."""

import sqlite3
from typing import Optional

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
    return Literature(**dict(row))
