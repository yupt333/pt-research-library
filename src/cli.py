"""Interactive read-only CLI for listing and searching literature."""

import sqlite3
from collections.abc import Callable, Sequence
from typing import Optional

from src.models import Literature
from src.repository import list_literature
from src.search import search_literature


_MAIN_MENU = """理学療法文献ライブラリ

1. 文献一覧
2. 文献検索
0. 終了"""
_MENU_PROMPT = "選択してください: "
_INVALID_MENU_MESSAGE = "入力エラー: 0、1、2のいずれかを選択してください。"
_EXIT_MESSAGE = "CLIを終了します。"
_DATABASE_ERROR_MESSAGE = "データベースエラーが発生しました。"
_RECORD_SEPARATOR = "-" * 40

_SEARCH_PROMPTS = (
    ("keyword", "キーワード（空欄で指定なし）: "),
    ("year", "出版年 year（空欄で指定なし）: "),
    ("tag", "タグ（空欄で指定なし）: "),
    ("publication_type", "publication_type（空欄で指定なし）: "),
    (
        "verification_status",
        "verification_status（未確認・一部確認・確認済み・要確認、"
        "空欄で指定なし）: ",
    ),
    (
        "adoption_status",
        "adoption_status（未判定・採用候補・採用・除外、"
        "空欄で指定なし）: ",
    ),
    (
        "ai_summary_status",
        "ai_summary_status（未作成・未確認・確認済み・修正済み、"
        "空欄で指定なし）: ",
    ),
    ("rating", "rating（1〜5、空欄で指定なし）: "),
    ("usage_type", "usage_type（空欄で指定なし）: "),
)


class _InputTerminated(Exception):
    """Signal an EOF or interrupt raised only while calling input_func."""


def _read_input(
    input_func: Callable[[str], str],
    prompt: str,
) -> str:
    """Call input_func and translate only its normal termination exceptions."""
    try:
        return input_func(prompt)
    except (EOFError, KeyboardInterrupt) as error:
        raise _InputTerminated from error


def _display_value(value: object) -> str:
    """Return one saved value for display without changing its contents."""
    return "未登録" if value is None else str(value)


def _format_literature(literature: Literature) -> str:
    """Format the Step 8A summary fields in a deterministic order."""
    fields = (
        ("ID", literature.id),
        ("title", literature.title),
        ("publication_year", literature.publication_year),
        ("authors", literature.authors),
        ("journal", literature.journal),
        ("DOI", literature.doi),
        ("PMID", literature.pmid),
        ("verification_status", literature.verification_status),
        ("adoption_status", literature.adoption_status),
        ("rating", literature.rating),
    )
    return "\n".join(
        f"{label}: {_display_value(value)}" for label, value in fields
    )


def _display_literature(
    literature_records: Sequence[Literature],
    output_func: Callable[[str], object],
    *,
    empty_message: str,
) -> None:
    """Display records in a common list/search format."""
    if not literature_records:
        output_func(empty_message)
        return

    for literature in literature_records:
        output_func(_RECORD_SEPARATOR)
        output_func(_format_literature(literature))
    output_func(_RECORD_SEPARATOR)


def _optional_text(value: str) -> Optional[str]:
    """Trim CLI input and map an empty value to no search condition."""
    normalized = value.strip()
    return normalized or None


def _optional_ascii_integer(
    value: str,
    field_name: str,
) -> Optional[int]:
    """Convert a non-empty ASCII-digit string to an integer."""
    normalized = value.strip()
    if not normalized:
        return None
    if not all("0" <= character <= "9" for character in normalized):
        raise ValueError(
            f"{field_name}はASCII数字だけの整数表記で入力してください。"
        )
    return int(normalized)


def _run_search(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect all Step 8A filters, execute the existing search, and display it."""
    raw_values: dict[str, str] = {}
    try:
        for field_name, prompt in _SEARCH_PROMPTS:
            raw_values[field_name] = _read_input(input_func, prompt)
    except _InputTerminated:
        return True

    keyword = _optional_text(raw_values["keyword"])
    tag = _optional_text(raw_values["tag"])
    publication_type = _optional_text(raw_values["publication_type"])
    verification_status = _optional_text(
        raw_values["verification_status"]
    )
    adoption_status = _optional_text(raw_values["adoption_status"])
    ai_summary_status = _optional_text(raw_values["ai_summary_status"])
    usage_type = _optional_text(raw_values["usage_type"])
    try:
        year = _optional_ascii_integer(raw_values["year"], "year")
        rating = _optional_ascii_integer(raw_values["rating"], "rating")
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        results = search_literature(
            connection,
            keyword=keyword,
            year=year,
            tag=tag,
            publication_type=publication_type,
            verification_status=verification_status,
            adoption_status=adoption_status,
            ai_summary_status=ai_summary_status,
            rating=rating,
            usage_type=usage_type,
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    _display_literature(
        results,
        output_func,
        empty_message="条件に一致する文献はありません。",
    )
    return False


def run_cli(
    connection: sqlite3.Connection,
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], object] = print,
) -> None:
    """Run the Step 8A read-only menu using an existing SQLite connection."""
    while True:
        output_func(_MAIN_MENU)
        try:
            choice = _read_input(input_func, _MENU_PROMPT)
        except _InputTerminated:
            output_func(_EXIT_MESSAGE)
            return None
        choice = choice.strip()

        if choice == "0":
            output_func(_EXIT_MESSAGE)
            return None
        if choice not in {"1", "2"}:
            output_func(_INVALID_MENU_MESSAGE)
            continue

        if choice == "1":
            try:
                literature_records = list_literature(connection)
            except sqlite3.Error:
                output_func(_DATABASE_ERROR_MESSAGE)
                raise
            _display_literature(
                literature_records,
                output_func,
                empty_message="登録されている文献はありません。",
            )
        elif _run_search(connection, input_func, output_func):
            output_func(_EXIT_MESSAGE)
            return None
