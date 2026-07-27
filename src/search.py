"""Read-only literature search and filtering."""

import sqlite3
from datetime import date
from typing import Optional

from src.models import Literature


_LITERATURE_TEXT_COLUMNS = (
    "title",
    "authors",
    "journal",
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
    "created_at",
    "updated_at",
)

_VERIFICATION_STATUSES = frozenset(
    ("未確認", "一部確認", "確認済み", "要確認")
)
_ADOPTION_STATUSES = frozenset(("未判定", "採用候補", "採用", "除外"))
_AI_SUMMARY_STATUSES = frozenset(
    ("未作成", "未確認", "確認済み", "修正済み")
)


def _normalize_keyword(keyword: object) -> Optional[str]:
    """Validate and trim a keyword, treating an empty value as no condition."""
    if keyword is None:
        return None
    if not isinstance(keyword, str):
        raise ValueError("keywordはNoneまたは文字列で指定してください。")
    normalized = keyword.strip()
    return normalized or None


def _normalize_required_text(field_name: str, value: object) -> Optional[str]:
    """Validate and trim an optional filter that must not be empty."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field_name}はNoneまたは空でない文字列で指定してください。"
        )
    return value.strip()


def _validate_year(year: object) -> Optional[int]:
    """Validate an optional publication year using the runtime year."""
    if year is None:
        return None
    maximum_year = date.today().year + 1
    if (
        isinstance(year, bool)
        or not isinstance(year, int)
        or not 1800 <= year <= maximum_year
    ):
        raise ValueError(
            f"yearは1800〜{maximum_year}の整数で指定してください。"
        )
    return year


def _validate_rating(rating: object) -> Optional[int]:
    """Validate an optional rating."""
    if rating is None:
        return None
    if (
        isinstance(rating, bool)
        or not isinstance(rating, int)
        or not 1 <= rating <= 5
    ):
        raise ValueError("ratingは1〜5の整数で指定してください。")
    return rating


def _validate_status(
    field_name: str,
    value: object,
    allowed_values: frozenset[str],
) -> Optional[str]:
    """Validate and trim an optional status filter."""
    normalized = _normalize_required_text(field_name, value)
    if normalized is not None and normalized not in allowed_values:
        raise ValueError(f"{field_name}に許可されていない値が指定されました。")
    return normalized


def _escape_like(value: str) -> str:
    """Escape SQLite LIKE metacharacters using a backslash escape."""
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def search_literature(
    connection: sqlite3.Connection,
    *,
    keyword: object = None,
    year: object = None,
    tag: object = None,
    publication_type: object = None,
    verification_status: object = None,
    adoption_status: object = None,
    ai_summary_status: object = None,
    rating: object = None,
    usage_type: object = None,
) -> list[Literature]:
    """Return literature matching all supplied conditions in ascending ID order."""
    normalized_keyword = _normalize_keyword(keyword)
    validated_year = _validate_year(year)
    normalized_tag = _normalize_required_text("tag", tag)
    normalized_publication_type = _normalize_required_text(
        "publication_type",
        publication_type,
    )
    normalized_verification_status = _validate_status(
        "verification_status",
        verification_status,
        _VERIFICATION_STATUSES,
    )
    normalized_adoption_status = _validate_status(
        "adoption_status",
        adoption_status,
        _ADOPTION_STATUSES,
    )
    normalized_ai_summary_status = _validate_status(
        "ai_summary_status",
        ai_summary_status,
        _AI_SUMMARY_STATUSES,
    )
    validated_rating = _validate_rating(rating)
    normalized_usage_type = _normalize_required_text(
        "usage_type",
        usage_type,
    )

    conditions: list[str] = []
    parameters: list[object] = []

    if normalized_keyword is not None:
        keyword_pattern = f"%{_escape_like(normalized_keyword)}%"
        literature_matches = [
            (
                f"lower(literature.{column}) "
                "LIKE lower(?) ESCAPE '\\'"
            )
            for column in _LITERATURE_TEXT_COLUMNS
        ]
        conditions.append(
            "("
            + " OR ".join(literature_matches)
            + """
            OR EXISTS (
                SELECT 1
                FROM literature_tags
                JOIN tags ON tags.id = literature_tags.tag_id
                WHERE literature_tags.literature_id = literature.id
                  AND lower(tags.name) LIKE lower(?) ESCAPE '\\'
            )
            OR EXISTS (
                SELECT 1
                FROM usage_history
                WHERE usage_history.literature_id = literature.id
                  AND (
                      lower(usage_history.usage_type)
                          LIKE lower(?) ESCAPE '\\'
                      OR lower(usage_history.project_name)
                          LIKE lower(?) ESCAPE '\\'
                      OR lower(usage_history.usage_note)
                          LIKE lower(?) ESCAPE '\\'
                  )
            )
            )"""
        )
        parameters.extend(
            [keyword_pattern] * (len(_LITERATURE_TEXT_COLUMNS) + 4)
        )

    if validated_year is not None:
        conditions.append("literature.publication_year = ?")
        parameters.append(validated_year)

    if normalized_tag is not None:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM literature_tags
                JOIN tags ON tags.id = literature_tags.tag_id
                WHERE literature_tags.literature_id = literature.id
                  AND tags.name = ? COLLATE NOCASE
            )
            """
        )
        parameters.append(normalized_tag)

    if normalized_publication_type is not None:
        conditions.append("literature.publication_type = ?")
        parameters.append(normalized_publication_type)

    for column, value in (
        ("verification_status", normalized_verification_status),
        ("adoption_status", normalized_adoption_status),
        ("ai_summary_status", normalized_ai_summary_status),
    ):
        if value is not None:
            conditions.append(f"literature.{column} = ?")
            parameters.append(value)

    if validated_rating is not None:
        conditions.append("literature.rating = ?")
        parameters.append(validated_rating)

    if normalized_usage_type is not None:
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM usage_history
                WHERE usage_history.literature_id = literature.id
                  AND usage_history.usage_type = ?
            )
            """
        )
        parameters.append(normalized_usage_type)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    rows = connection.execute(
        f"""
        SELECT literature.*
        FROM literature
        {where_clause}
        ORDER BY literature.id ASC
        """,
        tuple(parameters),
    ).fetchall()
    return [Literature(**dict(row)) for row in rows]
