"""Persistence operations for literature, tags, and usage history."""

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Mapping, Optional

from src.duplicates import normalize_doi, normalize_pmid
from src.models import Literature, Tag, UsageHistory


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
_TAG_SELECT_COLUMNS = ("id", "name")
_USAGE_HISTORY_COLUMNS = (
    "literature_id",
    "usage_type",
    "project_name",
    "usage_note",
    "used_at",
)
_USAGE_HISTORY_SELECT_COLUMNS = (
    ("id",) + _USAGE_HISTORY_COLUMNS + ("created_at",)
)
_UPDATABLE_USAGE_HISTORY_COLUMNS = frozenset(
    ("usage_type", "project_name", "usage_note", "used_at")
)
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_AI_SUMMARY_STATUSES = ("未作成", "未確認", "確認済み", "修正済み")
_VERIFICATION_STATUSES = ("未確認", "一部確認", "確認済み", "要確認")
_ADOPTION_STATUSES = ("未判定", "採用候補", "採用", "除外")


def _row_to_literature(row: sqlite3.Row) -> Literature:
    """Convert a literature table row to the repository's model."""
    return Literature(**dict(row))


def _row_to_tag(row: sqlite3.Row) -> Tag:
    """Convert a tags table row to the repository's model."""
    return Tag(**dict(row))


def _row_to_usage_history(row: sqlite3.Row) -> UsageHistory:
    """Convert a usage_history table row to the repository's model."""
    return UsageHistory(**dict(row))


def _normalize_tag_name(name: object) -> str:
    """Validate and trim a tag name."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("タグ名は空でない文字列で指定してください。")
    return name.strip()


def _normalize_usage_type(usage_type: object) -> str:
    """Validate and trim a usage type."""
    if not isinstance(usage_type, str) or not usage_type.strip():
        raise ValueError("usage_typeは空でない文字列で指定してください。")
    return usage_type.strip()


def _validate_optional_text(field_name: str, value: object) -> Optional[str]:
    """Validate a nullable text value without changing its contents."""
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field_name}はNoneまたは文字列で指定してください。")
    return value


def _validate_used_at(value: object) -> Optional[str]:
    """Validate a nullable date in exact YYYY-MM-DD form."""
    if value is None:
        return None
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError("used_atはNoneまたはYYYY-MM-DD形式で指定してください。")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("used_atには実在する日付を指定してください。") from error
    return value


def _validate_publication_year(value: object) -> Optional[int]:
    """Validate a nullable publication year using the runtime year."""
    if value is None:
        return None
    maximum_year = date.today().year + 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1800 <= value <= maximum_year
    ):
        raise ValueError(
            "publication_yearはNoneまたは"
            f"1800〜{maximum_year}の整数で指定してください。"
        )
    return value


def _validate_literature_status(
    field_name: str,
    value: object,
    allowed_values: tuple[str, ...],
) -> str:
    """Validate one required literature status without normalizing it."""
    if not isinstance(value, str) or value not in allowed_values:
        allowed_text = "、".join(allowed_values)
        raise ValueError(
            f"{field_name}は次の許可値から指定してください: {allowed_text}"
        )
    return value


def _is_sqlite_constraint(
    error: sqlite3.IntegrityError,
    error_code: int,
    error_name: str,
) -> bool:
    """Return whether an integrity error is one expected SQLite constraint."""
    return (
        getattr(error, "sqlite_errorcode", None) == error_code
        or getattr(error, "sqlite_errorname", None) == error_name
    )


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
    validated_values = {
        column: getattr(literature, column)
        for column in _LITERATURE_COLUMNS
    }
    validated_values["publication_year"] = _validate_publication_year(
        literature.publication_year
    )
    validated_values["ai_summary_status"] = _validate_literature_status(
        "ai_summary_status",
        literature.ai_summary_status,
        _AI_SUMMARY_STATUSES,
    )
    validated_values["verification_status"] = _validate_literature_status(
        "verification_status",
        literature.verification_status,
        _VERIFICATION_STATUSES,
    )
    validated_values["adoption_status"] = _validate_literature_status(
        "adoption_status",
        literature.adoption_status,
        _ADOPTION_STATUSES,
    )
    validated_values["doi"] = normalize_doi(literature.doi)
    validated_values["pmid"] = normalize_pmid(literature.pmid)

    column_names = ", ".join(_LITERATURE_COLUMNS)
    placeholders = ", ".join("?" for _ in _LITERATURE_COLUMNS)
    values = tuple(validated_values[column] for column in _LITERATURE_COLUMNS)

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

    validated_updates = dict(updates)
    if "publication_year" in validated_updates:
        validated_updates["publication_year"] = _validate_publication_year(
            validated_updates["publication_year"]
        )
    if "ai_summary_status" in validated_updates:
        validated_updates["ai_summary_status"] = _validate_literature_status(
            "ai_summary_status",
            validated_updates["ai_summary_status"],
            _AI_SUMMARY_STATUSES,
        )
    if "verification_status" in validated_updates:
        validated_updates["verification_status"] = (
            _validate_literature_status(
                "verification_status",
                validated_updates["verification_status"],
                _VERIFICATION_STATUSES,
            )
        )
    if "adoption_status" in validated_updates:
        validated_updates["adoption_status"] = _validate_literature_status(
            "adoption_status",
            validated_updates["adoption_status"],
            _ADOPTION_STATUSES,
        )
    if "doi" in validated_updates:
        validated_updates["doi"] = normalize_doi(validated_updates["doi"])
    if "pmid" in validated_updates:
        validated_updates["pmid"] = normalize_pmid(validated_updates["pmid"])

    candidate_values = dict(current_row)
    candidate_values.update(validated_updates)
    Literature(**candidate_values)

    update_columns = tuple(validated_updates)
    assignments = ", ".join(f"{column} = ?" for column in update_columns)
    updated_at = _next_updated_at(current_row["updated_at"])
    values = tuple(validated_updates[column] for column in update_columns)

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


def create_tag(connection: sqlite3.Connection, name: object) -> int:
    """Create a tag, or return the matching case-insensitive tag ID."""
    normalized_name = _normalize_tag_name(name)

    with connection:
        cursor = connection.execute(
            """
            INSERT INTO tags (name)
            VALUES (?)
            ON CONFLICT DO NOTHING
            """,
            (normalized_name,),
        )
        if cursor.rowcount == 1:
            if cursor.lastrowid is None:
                raise RuntimeError("タグIDを取得できませんでした。")
            return cursor.lastrowid

        row = connection.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE",
            (normalized_name,),
        ).fetchone()
        if row is None:
            raise RuntimeError("既存タグのIDを取得できませんでした。")
        return row["id"]


def get_tag(
    connection: sqlite3.Connection, tag_id: int
) -> Optional[Tag]:
    """Return one tag, or None when the ID does not exist."""
    columns = ", ".join(_TAG_SELECT_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM tags WHERE id = ?",
        (tag_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_tag(row)


def list_tags(connection: sqlite3.Connection) -> list[Tag]:
    """Return all tags in deterministic case-insensitive name order."""
    columns = ", ".join(_TAG_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM tags
        ORDER BY name COLLATE NOCASE ASC, id ASC
        """
    ).fetchall()
    return [_row_to_tag(row) for row in rows]


def list_tags_for_literature(
    connection: sqlite3.Connection, literature_id: int
) -> Optional[list[Tag]]:
    """Return attached tags, or None when the literature ID is unknown."""
    columns = ", ".join(f"tags.{column}" for column in _TAG_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM literature
        LEFT JOIN literature_tags
            ON literature_tags.literature_id = literature.id
        LEFT JOIN tags
            ON tags.id = literature_tags.tag_id
        WHERE literature.id = ?
        ORDER BY tags.name COLLATE NOCASE ASC, tags.id ASC
        """,
        (literature_id,),
    ).fetchall()
    if not rows:
        return None
    if rows[0]["id"] is None:
        return []
    return [_row_to_tag(row) for row in rows]


def rename_tag(
    connection: sqlite3.Connection,
    tag_id: int,
    new_name: object,
) -> bool:
    """Rename one tag while preserving its ID and relationships."""
    normalized_name = _normalize_tag_name(new_name)

    try:
        with connection:
            cursor = connection.execute(
                "UPDATE tags SET name = ? WHERE id = ?",
                (normalized_name, tag_id),
            )
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            "SQLITE_CONSTRAINT_UNIQUE",
        ):
            raise ValueError(
                "同じ名前のタグが大文字・小文字を区別せず既に存在します。"
            ) from error
        raise
    return cursor.rowcount == 1


def delete_tag(connection: sqlite3.Connection, tag_id: int) -> bool:
    """Delete one tag and its relationships, but not literature records."""
    with connection:
        cursor = connection.execute(
            "DELETE FROM tags WHERE id = ?",
            (tag_id,),
        )
    return cursor.rowcount == 1


def attach_tag_to_literature(
    connection: sqlite3.Connection,
    literature_id: int,
    tag_id: int,
) -> bool:
    """Attach a tag once when both referenced records exist."""
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO literature_tags (literature_id, tag_id)
                VALUES (?, ?)
                ON CONFLICT(literature_id, tag_id) DO NOTHING
                """,
                (literature_id, tag_id),
            )
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY,
            "SQLITE_CONSTRAINT_FOREIGNKEY",
        ):
            raise ValueError(
                "literature_idまたはtag_idが存在しません。"
            ) from error
        raise
    return cursor.rowcount == 1


def detach_tag_from_literature(
    connection: sqlite3.Connection,
    literature_id: int,
    tag_id: int,
) -> bool:
    """Detach one tag relationship without deleting either parent record."""
    with connection:
        cursor = connection.execute(
            """
            DELETE FROM literature_tags
            WHERE literature_id = ? AND tag_id = ?
            """,
            (literature_id, tag_id),
        )
    return cursor.rowcount == 1


def create_usage_history(
    connection: sqlite3.Connection,
    literature_id: int,
    usage_type: object,
    project_name: object = None,
    usage_note: object = None,
    used_at: object = None,
) -> int:
    """Create one usage-history record and return its generated ID."""
    normalized_usage_type = _normalize_usage_type(usage_type)
    validated_project_name = _validate_optional_text(
        "project_name", project_name
    )
    validated_usage_note = _validate_optional_text("usage_note", usage_note)
    validated_used_at = _validate_used_at(used_at)

    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO usage_history (
                    literature_id,
                    usage_type,
                    project_name,
                    usage_note,
                    used_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    literature_id,
                    normalized_usage_type,
                    validated_project_name,
                    validated_usage_note,
                    validated_used_at,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("使用履歴IDを取得できませんでした。")
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY,
            "SQLITE_CONSTRAINT_FOREIGNKEY",
        ):
            raise ValueError(
                f"文献ID {literature_id!r} は存在しません。"
            ) from error
        raise
    return cursor.lastrowid


def get_usage_history(
    connection: sqlite3.Connection,
    usage_history_id: int,
) -> Optional[UsageHistory]:
    """Return one usage-history record, or None when its ID is unknown."""
    columns = ", ".join(_USAGE_HISTORY_SELECT_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM usage_history WHERE id = ?",
        (usage_history_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_usage_history(row)


def list_usage_history_for_literature(
    connection: sqlite3.Connection,
    literature_id: int,
) -> Optional[list[UsageHistory]]:
    """Return one literature record's history, or None for unknown literature."""
    literature_exists = connection.execute(
        "SELECT 1 FROM literature WHERE id = ?",
        (literature_id,),
    ).fetchone()
    if literature_exists is None:
        return None

    columns = ", ".join(_USAGE_HISTORY_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM usage_history
        WHERE literature_id = ?
        ORDER BY id ASC
        """,
        (literature_id,),
    ).fetchall()
    return [_row_to_usage_history(row) for row in rows]


def update_usage_history(
    connection: sqlite3.Connection,
    usage_history_id: int,
    updates: Mapping[str, object],
) -> bool:
    """Partially update editable usage-history fields."""
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")

    invalid_columns = set(updates) - _UPDATABLE_USAGE_HISTORY_COLUMNS
    if invalid_columns:
        invalid_names = ", ".join(
            sorted(repr(column) for column in invalid_columns)
        )
        raise ValueError(f"更新できない項目が指定されました: {invalid_names}")

    validated_updates: dict[str, object] = {}
    for column, value in updates.items():
        if column == "usage_type":
            validated_updates[column] = _normalize_usage_type(value)
        elif column == "used_at":
            validated_updates[column] = _validate_used_at(value)
        else:
            validated_updates[column] = _validate_optional_text(column, value)

    update_columns = tuple(validated_updates)
    assignments = ", ".join(f"{column} = ?" for column in update_columns)
    values = tuple(validated_updates[column] for column in update_columns)
    with connection:
        cursor = connection.execute(
            f"UPDATE usage_history SET {assignments} WHERE id = ?",
            values + (usage_history_id,),
        )
    return cursor.rowcount == 1


def delete_usage_history(
    connection: sqlite3.Connection,
    usage_history_id: int,
) -> bool:
    """Delete exactly one usage-history row when it exists."""
    with connection:
        cursor = connection.execute(
            "DELETE FROM usage_history WHERE id = ?",
            (usage_history_id,),
        )
    return cursor.rowcount == 1
