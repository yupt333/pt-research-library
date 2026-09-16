"""Persistence operations for independent user research projects."""

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from src.models import Literature, ResearchProject, ResearchProjectItem


PROJECT_ITEM_TYPES = (
    "concept",
    "unresolved_question",
    "next_action",
)

_PROJECT_COLUMNS = (
    "name",
    "objective",
    "current_status",
    "protocol_note",
    "general_note",
)
_PROJECT_SELECT_COLUMNS = ("id",) + _PROJECT_COLUMNS + (
    "created_at",
    "updated_at",
)
_UPDATABLE_PROJECT_COLUMNS = frozenset(_PROJECT_COLUMNS)

_PROJECT_ITEM_COLUMNS = (
    "project_id",
    "item_type",
    "content",
    "note",
    "sort_order",
)
_PROJECT_ITEM_SELECT_COLUMNS = ("id",) + _PROJECT_ITEM_COLUMNS + (
    "created_at",
    "updated_at",
)
_UPDATABLE_PROJECT_ITEM_COLUMNS = frozenset(
    {"content", "note", "sort_order"}
)


def _require_write_context(connection: sqlite3.Connection) -> None:
    """Reject writes without changing an active caller transaction."""
    if connection.in_transaction:
        raise ValueError(
            "アクティブなcaller transaction中はProjectを変更できません。"
        )


def _validate_record_id(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name}は1以上の整数で指定してください。")
    return value


def _normalize_project_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Project nameは空でない文字列で指定してください。")
    return name.strip()


def _validate_optional_text(field_name: str, value: object) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{field_name}はNoneまたは文字列で指定してください。")
    return value


def _validate_item_type(item_type: object) -> str:
    if not isinstance(item_type, str) or item_type not in PROJECT_ITEM_TYPES:
        allowed = "、".join(PROJECT_ITEM_TYPES)
        raise ValueError(f"item_typeは次の許可値から指定してください: {allowed}")
    return item_type


def _normalize_item_content(content: object) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("contentは空でない文字列で指定してください。")
    return content.strip()


def _validate_sort_order(sort_order: object) -> int:
    if (
        isinstance(sort_order, bool)
        or not isinstance(sort_order, int)
        or sort_order < 0
    ):
        raise ValueError("sort_orderは0以上の整数で指定してください。")
    return sort_order


def _is_sqlite_constraint(
    error: sqlite3.IntegrityError,
    error_code: int,
    error_name: str,
) -> bool:
    return (
        getattr(error, "sqlite_errorcode", None) == error_code
        or getattr(error, "sqlite_errorname", None) == error_name
    )


def _next_updated_at(previous_updated_at: str) -> str:
    previous = datetime.fromisoformat(
        previous_updated_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    current = datetime.now(timezone.utc)
    next_timestamp = (
        current
        if current > previous
        else previous + timedelta(microseconds=1)
    )
    return next_timestamp.isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _row_to_project(row: sqlite3.Row) -> ResearchProject:
    return ResearchProject(**dict(row))


def _row_to_project_item(row: sqlite3.Row) -> ResearchProjectItem:
    return ResearchProjectItem(**dict(row))


def _project_exists(connection: sqlite3.Connection, project_id: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM research_projects WHERE id = ?", (project_id,)
        ).fetchone()
        is not None
    )


def _literature_exists(
    connection: sqlite3.Connection, literature_id: int
) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM literature WHERE id = ?", (literature_id,)
        ).fetchone()
        is not None
    )


def _require_related_records(
    connection: sqlite3.Connection,
    project_id: int,
    literature_id: int,
) -> None:
    if not _project_exists(connection, project_id):
        raise ValueError(f"Project ID {project_id} は存在しません。")
    if not _literature_exists(connection, literature_id):
        raise ValueError(f"Literature ID {literature_id} は存在しません。")


def create_research_project(
    connection: sqlite3.Connection,
    name: object,
    objective: object = None,
    current_status: object = None,
    protocol_note: object = None,
    general_note: object = None,
) -> int:
    """Create one independent research project and return its ID."""
    _require_write_context(connection)
    values = (
        _normalize_project_name(name),
        _validate_optional_text("objective", objective),
        _validate_optional_text("current_status", current_status),
        _validate_optional_text("protocol_note", protocol_note),
        _validate_optional_text("general_note", general_note),
    )
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO research_projects (
                    name,
                    objective,
                    current_status,
                    protocol_note,
                    general_note
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                values,
            )
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            "SQLITE_CONSTRAINT_UNIQUE",
        ):
            raise ValueError(
                "同じProject nameが大文字・小文字を区別せず既に存在します。"
            ) from error
        raise
    if cursor.lastrowid is None:
        raise RuntimeError("Project IDを取得できませんでした。")
    return cursor.lastrowid


def get_research_project(
    connection: sqlite3.Connection, project_id: object
) -> Optional[ResearchProject]:
    """Return one research project, or None for an unknown ID."""
    validated_id = _validate_record_id("project_id", project_id)
    columns = ", ".join(_PROJECT_SELECT_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM research_projects WHERE id = ?",
        (validated_id,),
    ).fetchone()
    return None if row is None else _row_to_project(row)


def list_research_projects(
    connection: sqlite3.Connection,
) -> list[ResearchProject]:
    """Return all research projects ordered by persistent ID."""
    columns = ", ".join(_PROJECT_SELECT_COLUMNS)
    rows = connection.execute(
        f"SELECT {columns} FROM research_projects ORDER BY id ASC"
    ).fetchall()
    return [_row_to_project(row) for row in rows]


def update_research_project(
    connection: sqlite3.Connection,
    project_id: object,
    updates: Mapping[str, object],
) -> bool:
    """Update allowed project fields without touching other projects."""
    _require_write_context(connection)
    validated_id = _validate_record_id("project_id", project_id)
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    invalid_columns = set(updates) - _UPDATABLE_PROJECT_COLUMNS
    if invalid_columns:
        names = ", ".join(sorted(repr(name) for name in invalid_columns))
        raise ValueError(f"更新できないProject項目が指定されました: {names}")

    columns = ", ".join(_PROJECT_SELECT_COLUMNS)
    current = connection.execute(
        f"SELECT {columns} FROM research_projects WHERE id = ?",
        (validated_id,),
    ).fetchone()
    if current is None:
        return False

    validated_updates: dict[str, object] = {}
    for column, value in updates.items():
        validated_updates[column] = (
            _normalize_project_name(value)
            if column == "name"
            else _validate_optional_text(column, value)
        )
    update_columns = tuple(validated_updates)
    assignments = ", ".join(f"{column} = ?" for column in update_columns)
    values = tuple(validated_updates[column] for column in update_columns)
    updated_at = _next_updated_at(current["updated_at"])
    try:
        with connection:
            cursor = connection.execute(
                f"""
                UPDATE research_projects
                SET {assignments}, updated_at = ?
                WHERE id = ?
                """,
                values + (updated_at, validated_id),
            )
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            "SQLITE_CONSTRAINT_UNIQUE",
        ):
            raise ValueError(
                "同じProject nameが大文字・小文字を区別せず既に存在します。"
            ) from error
        raise
    return cursor.rowcount == 1


def get_research_project_related_counts(
    connection: sqlite3.Connection, project_id: object
) -> Optional[dict[str, int]]:
    """Return deletion-impact counts, or None for an unknown project."""
    validated_id = _validate_record_id("project_id", project_id)
    row = connection.execute(
        """
        SELECT
            (
                SELECT COUNT(*) FROM research_project_literature
                WHERE project_id = research_projects.id
            ) AS literature_count,
            (
                SELECT COUNT(*) FROM research_project_items
                WHERE project_id = research_projects.id
                  AND item_type = 'concept'
            ) AS concept_count,
            (
                SELECT COUNT(*) FROM research_project_items
                WHERE project_id = research_projects.id
                  AND item_type = 'unresolved_question'
            ) AS unresolved_question_count,
            (
                SELECT COUNT(*) FROM research_project_items
                WHERE project_id = research_projects.id
                  AND item_type = 'next_action'
            ) AS next_action_count
        FROM research_projects
        WHERE id = ?
        """,
        (validated_id,),
    ).fetchone()
    return None if row is None else dict(row)


def delete_research_project(
    connection: sqlite3.Connection, project_id: object
) -> bool:
    """Delete one project and only its project-local links and items."""
    _require_write_context(connection)
    validated_id = _validate_record_id("project_id", project_id)
    with connection:
        cursor = connection.execute(
            "DELETE FROM research_projects WHERE id = ?", (validated_id,)
        )
    return cursor.rowcount == 1


def attach_literature_to_project(
    connection: sqlite3.Connection,
    project_id: object,
    literature_id: object,
) -> bool:
    """Attach an existing Literature to an existing Project once."""
    _require_write_context(connection)
    validated_project_id = _validate_record_id("project_id", project_id)
    validated_literature_id = _validate_record_id(
        "literature_id", literature_id
    )
    _require_related_records(
        connection, validated_project_id, validated_literature_id
    )
    with connection:
        cursor = connection.execute(
            """
            INSERT INTO research_project_literature (
                project_id, literature_id
            )
            VALUES (?, ?)
            ON CONFLICT(project_id, literature_id) DO NOTHING
            """,
            (validated_project_id, validated_literature_id),
        )
    return cursor.rowcount == 1


def detach_literature_from_project(
    connection: sqlite3.Connection,
    project_id: object,
    literature_id: object,
) -> bool:
    """Delete only one Project/Literature relationship."""
    _require_write_context(connection)
    validated_project_id = _validate_record_id("project_id", project_id)
    validated_literature_id = _validate_record_id(
        "literature_id", literature_id
    )
    _require_related_records(
        connection, validated_project_id, validated_literature_id
    )
    with connection:
        cursor = connection.execute(
            """
            DELETE FROM research_project_literature
            WHERE project_id = ? AND literature_id = ?
            """,
            (validated_project_id, validated_literature_id),
        )
    return cursor.rowcount == 1


def list_literature_for_project(
    connection: sqlite3.Connection, project_id: object
) -> Optional[list[Literature]]:
    """Return linked Literature by ID, or None for an unknown Project."""
    validated_id = _validate_record_id("project_id", project_id)
    if not _project_exists(connection, validated_id):
        return None
    rows = connection.execute(
        """
        SELECT literature.*
        FROM research_project_literature
        JOIN literature
          ON literature.id = research_project_literature.literature_id
        WHERE research_project_literature.project_id = ?
        ORDER BY literature.id ASC
        """,
        (validated_id,),
    ).fetchall()
    return [Literature(**dict(row)) for row in rows]


def create_project_item(
    connection: sqlite3.Connection,
    project_id: object,
    item_type: object,
    content: object,
    note: object = None,
    *,
    sort_order: object = None,
) -> int:
    """Create one project-local item with optional automatic ordering."""
    _require_write_context(connection)
    validated_project_id = _validate_record_id("project_id", project_id)
    validated_type = _validate_item_type(item_type)
    validated_content = _normalize_item_content(content)
    validated_note = _validate_optional_text("note", note)
    if sort_order is not None:
        validated_sort_order = _validate_sort_order(sort_order)
    else:
        validated_sort_order = None

    try:
        with connection:
            if not _project_exists(connection, validated_project_id):
                raise ValueError(
                    f"Project ID {validated_project_id} は存在しません。"
                )
            if validated_sort_order is None:
                maximum = connection.execute(
                    """
                    SELECT MAX(sort_order)
                    FROM research_project_items
                    WHERE project_id = ? AND item_type = ?
                    """,
                    (validated_project_id, validated_type),
                ).fetchone()[0]
                validated_sort_order = 0 if maximum is None else maximum + 1
            cursor = connection.execute(
                """
                INSERT INTO research_project_items (
                    project_id,
                    item_type,
                    content,
                    note,
                    sort_order
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    validated_project_id,
                    validated_type,
                    validated_content,
                    validated_note,
                    validated_sort_order,
                ),
            )
    except sqlite3.IntegrityError as error:
        if _is_sqlite_constraint(
            error,
            sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY,
            "SQLITE_CONSTRAINT_FOREIGNKEY",
        ):
            raise ValueError(
                f"Project ID {validated_project_id} は存在しません。"
            ) from error
        raise
    if cursor.lastrowid is None:
        raise RuntimeError("Project item IDを取得できませんでした。")
    return cursor.lastrowid


def get_project_item(
    connection: sqlite3.Connection, item_id: object
) -> Optional[ResearchProjectItem]:
    """Return one project item, or None for an unknown ID."""
    validated_id = _validate_record_id("item_id", item_id)
    columns = ", ".join(_PROJECT_ITEM_SELECT_COLUMNS)
    row = connection.execute(
        f"SELECT {columns} FROM research_project_items WHERE id = ?",
        (validated_id,),
    ).fetchone()
    return None if row is None else _row_to_project_item(row)


def list_project_items(
    connection: sqlite3.Connection,
    project_id: object,
    *,
    item_type: object = None,
) -> Optional[list[ResearchProjectItem]]:
    """Return project items in stable type/order/ID order."""
    validated_project_id = _validate_record_id("project_id", project_id)
    if not _project_exists(connection, validated_project_id):
        return None
    columns = ", ".join(_PROJECT_ITEM_SELECT_COLUMNS)
    if item_type is None:
        rows = connection.execute(
            f"""
            SELECT {columns}
            FROM research_project_items
            WHERE project_id = ?
            ORDER BY
                CASE item_type
                    WHEN 'concept' THEN 1
                    WHEN 'unresolved_question' THEN 2
                    WHEN 'next_action' THEN 3
                END ASC,
                sort_order ASC,
                id ASC
            """,
            (validated_project_id,),
        ).fetchall()
    else:
        validated_type = _validate_item_type(item_type)
        rows = connection.execute(
            f"""
            SELECT {columns}
            FROM research_project_items
            WHERE project_id = ? AND item_type = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (validated_project_id, validated_type),
        ).fetchall()
    return [_row_to_project_item(row) for row in rows]


def update_project_item(
    connection: sqlite3.Connection,
    item_id: object,
    updates: Mapping[str, object],
) -> bool:
    """Update content, note, or ordering without changing item type."""
    _require_write_context(connection)
    validated_id = _validate_record_id("item_id", item_id)
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    invalid_columns = set(updates) - _UPDATABLE_PROJECT_ITEM_COLUMNS
    if invalid_columns:
        names = ", ".join(sorted(repr(name) for name in invalid_columns))
        raise ValueError(f"更新できないProject item項目が指定されました: {names}")

    columns = ", ".join(_PROJECT_ITEM_SELECT_COLUMNS)
    current = connection.execute(
        f"SELECT {columns} FROM research_project_items WHERE id = ?",
        (validated_id,),
    ).fetchone()
    if current is None:
        return False

    validated_updates: dict[str, object] = {}
    for column, value in updates.items():
        if column == "content":
            validated_updates[column] = _normalize_item_content(value)
        elif column == "note":
            validated_updates[column] = _validate_optional_text(column, value)
        else:
            validated_updates[column] = _validate_sort_order(value)
    update_columns = tuple(validated_updates)
    assignments = ", ".join(f"{column} = ?" for column in update_columns)
    values = tuple(validated_updates[column] for column in update_columns)
    updated_at = _next_updated_at(current["updated_at"])
    with connection:
        cursor = connection.execute(
            f"""
            UPDATE research_project_items
            SET {assignments}, updated_at = ?
            WHERE id = ?
            """,
            values + (updated_at, validated_id),
        )
    return cursor.rowcount == 1


def delete_project_item(
    connection: sqlite3.Connection, item_id: object
) -> bool:
    """Delete exactly one project-local item."""
    _require_write_context(connection)
    validated_id = _validate_record_id("item_id", item_id)
    with connection:
        cursor = connection.execute(
            "DELETE FROM research_project_items WHERE id = ?",
            (validated_id,),
        )
    return cursor.rowcount == 1
