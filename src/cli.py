"""Interactive CLI for literature and basic tag management."""

import sqlite3
from collections.abc import Callable, Sequence
from typing import Optional

from src.duplicates import DuplicateCandidate, find_duplicate_candidates
from src.models import Literature, Tag
from src.repository import (
    add_literature,
    create_tag,
    delete_literature,
    get_literature,
    get_literature_related_counts,
    get_tag,
    list_literature,
    list_tags,
    rename_tag,
    update_literature,
)
from src.search import search_literature


_MAIN_MENU = """理学療法文献ライブラリ

1. 文献一覧
2. 文献検索
3. 文献登録
4. 文献編集
5. 文献削除
6. タグ管理
0. 終了"""
_MENU_PROMPT = "選択してください: "
_INVALID_MENU_MESSAGE = (
    "入力エラー: 0、1、2、3、4、5、6のいずれかを選択してください。"
)
_EXIT_MESSAGE = "CLIを終了します。"
_DATABASE_ERROR_MESSAGE = "データベースエラーが発生しました。"
_RECORD_SEPARATOR = "-" * 40
_AI_SUMMARY_STATUSES = ("未作成", "未確認", "確認済み", "修正済み")
_VERIFICATION_STATUSES = ("未確認", "一部確認", "確認済み", "要確認")
_ADOPTION_STATUSES = ("未判定", "採用候補", "採用", "除外")

_SEARCH_PROMPTS = (
    ("keyword", "キーワード（空欄で指定なし）: "),
    ("year", "出版年 year（空欄で指定なし）: "),
    ("tag", "タグ（空欄で指定なし）: "),
    ("publication_type", "publication_type（空欄で指定なし）: "),
    (
        "verification_status",
        f"verification_status（{'・'.join(_VERIFICATION_STATUSES)}、"
        "空欄で指定なし）: ",
    ),
    (
        "adoption_status",
        f"adoption_status（{'・'.join(_ADOPTION_STATUSES)}、"
        "空欄で指定なし）: ",
    ),
    (
        "ai_summary_status",
        f"ai_summary_status（{'・'.join(_AI_SUMMARY_STATUSES)}、"
        "空欄で指定なし）: ",
    ),
    ("rating", "rating（1〜5、空欄で指定なし）: "),
    ("usage_type", "usage_type（空欄で指定なし）: "),
)

_REGISTRATION_PROMPTS = (
    ("title", "title（必須）: "),
    ("authors", "authors（空欄で未登録）: "),
    ("journal", "journal（空欄で未登録）: "),
    ("publication_year", "publication_year（空欄で未登録）: "),
    ("volume", "volume（空欄で未登録）: "),
    ("issue", "issue（空欄で未登録）: "),
    ("pages", "pages（空欄で未登録）: "),
    ("doi", "doi（空欄で未登録）: "),
    ("pmid", "pmid（空欄で未登録）: "),
    ("url", "url（空欄で未登録）: "),
    ("language", "language（空欄で未登録）: "),
    ("publication_type", "publication_type（空欄で未登録）: "),
    ("abstract", "abstract（空欄で未登録）: "),
    ("pdf_path", "pdf_path（空欄で未登録）: "),
    ("personal_summary", "personal_summary（空欄で未登録）: "),
    ("ai_summary", "ai_summary（空欄で未登録）: "),
    (
        "ai_summary_status",
        f"ai_summary_status（{'・'.join(_AI_SUMMARY_STATUSES)}、"
        "空欄で既定値）: ",
    ),
    ("general_note", "general_note（空欄で未登録）: "),
    ("key_findings", "key_findings（空欄で未登録）: "),
    ("methods_note", "methods_note（空欄で未登録）: "),
    ("clinical_note", "clinical_note（空欄で未登録）: "),
    ("limitation_note", "limitation_note（空欄で未登録）: "),
    ("relevance_note", "relevance_note（空欄で未登録）: "),
    ("evidence_level", "evidence_level（空欄で未登録）: "),
    (
        "verification_status",
        f"verification_status（{'・'.join(_VERIFICATION_STATUSES)}、"
        "空欄で既定値）: ",
    ),
    (
        "adoption_status",
        f"adoption_status（{'・'.join(_ADOPTION_STATUSES)}、"
        "空欄で既定値）: ",
    ),
    ("exclusion_reason", "exclusion_reason（空欄で未登録）: "),
    ("rating", "rating（1〜5、空欄で未登録）: "),
)
_REGISTRATION_CONFIRMATION_MENU = """1. この内容で登録する
0. 登録を中止する"""
_INVALID_CONFIRMATION_MESSAGE = "入力エラー: 0、1のいずれかを選択してください。"
_REGISTRATION_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は文献を登録できません。"
)
_EDIT_FIELDS = tuple(
    field_name for field_name, _ in _REGISTRATION_PROMPTS
)
_EDIT_FIELD_MENU = "\n".join(
    (
        *(
            f"{number}. {field_name}"
            for number, field_name in enumerate(_EDIT_FIELDS, start=1)
        ),
        "0. 編集を中止する",
    )
)
_EDIT_PROMPTS = {
    **{
        field_name: f"{field_name}（空欄で未登録）: "
        for field_name in _EDIT_FIELDS
    },
    "title": "title（必須）: ",
    "publication_year": "publication_year（空欄で未登録）: ",
    "ai_summary_status": (
        f"ai_summary_status（{'・'.join(_AI_SUMMARY_STATUSES)}）: "
    ),
    "verification_status": (
        f"verification_status（{'・'.join(_VERIFICATION_STATUSES)}）: "
    ),
    "adoption_status": (
        f"adoption_status（{'・'.join(_ADOPTION_STATUSES)}）: "
    ),
    "rating": "rating（1〜5、空欄で未登録）: ",
}
_INVALID_EDIT_FIELD_MESSAGE = (
    "入力エラー: 0〜28のいずれかを選択してください。"
)
_EDIT_CONFIRMATION_MENU = """1. この内容で更新する
0. 更新を中止する"""
_EDIT_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は文献を編集できません。"
)
_DELETE_CONFIRMATION_MENU = """1. 削除手続きを続ける
0. 削除を中止する"""
_DELETE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は文献を削除できません。"
)
_TAG_MANAGEMENT_MENU = """タグ管理

1. タグ一覧
2. タグ作成
3. タグ名称変更
0. メインメニューに戻る"""
_INVALID_TAG_MENU_MESSAGE = (
    "入力エラー: 0、1、2、3のいずれかを選択してください。"
)
_TAG_CREATE_CONFIRMATION_MENU = """1. このタグを登録する
0. 登録を中止する"""
_TAG_RENAME_CONFIRMATION_MENU = """1. この内容で変更する
0. 変更を中止する"""
_TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中はタグを登録できません。"
)
_TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中はタグ名称を変更できません。"
)
_DUPLICATE_REASON_LABELS = {
    "doi": "DOI一致",
    "pmid": "PMID一致",
    "title": "タイトル類似",
}


def _read_input(
    input_func: Callable[[str], str],
    prompt: str,
) -> str:
    """Call the configured input function without translating exceptions."""
    return input_func(prompt)


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


def _format_registration_literature(literature: Literature) -> str:
    """Format every user-supplied literature field in input order."""
    fields = tuple(
        (field_name, getattr(literature, field_name))
        for field_name, _ in _REGISTRATION_PROMPTS
    )
    return "\n".join(
        f"{label}: {_display_value(value)}" for label, value in fields
    )


def _format_edit_literature(literature: Literature) -> str:
    """Format all saved literature fields in their specified display order."""
    field_names = ("id", *_EDIT_FIELDS, "created_at", "updated_at")
    return "\n".join(
        f"{field_name}: {_display_value(getattr(literature, field_name))}"
        for field_name in field_names
    )


def _format_tag(tag: Tag) -> str:
    """Format one tag without changing its stored values."""
    return f"ID: {_display_value(tag.id)}\nname: {_display_value(tag.name)}"


def _format_duplicate_candidate(candidate: DuplicateCandidate) -> str:
    """Format one duplicate candidate without changing or reordering it."""
    literature = candidate.literature
    reason_labels = (
        _DUPLICATE_REASON_LABELS.get(reason, reason)
        for reason in candidate.match_reasons
    )
    fields = (
        ("既存文献ID", literature.id),
        ("title", literature.title),
        ("publication_year", literature.publication_year),
        ("DOI", literature.doi),
        ("PMID", literature.pmid),
        ("一致理由", "、".join(reason_labels)),
        ("title_similarity", candidate.title_similarity),
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


def _display_tags(
    tags: Sequence[Tag],
    output_func: Callable[[str], object],
) -> None:
    """Display tags in the order returned by the repository."""
    if not tags:
        output_func("登録されているタグはありません。")
        return

    for tag in tags:
        output_func(_format_tag(tag))
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


def _required_positive_ascii_integer(
    value: str,
    field_name: str,
) -> int:
    """Convert an ASCII-digit string to an integer greater than zero."""
    normalized = value.strip()
    if (
        not normalized
        or not all("0" <= character <= "9" for character in normalized)
        or int(normalized) < 1
    ):
        raise ValueError(
            f"{field_name}は1以上のASCII数字だけで入力してください。"
        )
    return int(normalized)


def _prepare_edit_value(field_name: str, raw_value: str) -> object:
    """Apply only the field-specific conversions owned by the CLI."""
    normalized = raw_value.strip()
    if field_name == "title":
        if not normalized:
            raise ValueError("タイトルは必須です。")
        return normalized
    if field_name in {
        "ai_summary_status",
        "verification_status",
        "adoption_status",
    }:
        if not normalized:
            raise ValueError(f"{field_name}は空欄にできません。")
        return normalized
    if field_name in {"publication_year", "rating"}:
        return _optional_ascii_integer(raw_value, field_name)
    return normalized or None


def _display_duplicate_candidates(
    candidates: Sequence[DuplicateCandidate],
    output_func: Callable[[str], object],
) -> None:
    """Display duplicate warnings and candidates in the API-provided order."""
    if not candidates:
        output_func("重複候補はありません。")
        return

    output_func("警告: 重複候補があります。")
    output_func(
        "候補は自動統合されず、既存文献も変更されません。"
    )
    for candidate in candidates:
        output_func(_RECORD_SEPARATOR)
        output_func(_format_duplicate_candidate(candidate))
    output_func(_RECORD_SEPARATOR)


def _prepare_registration_values(
    raw_values: dict[str, str],
) -> dict[str, object]:
    """Apply the registration flow's CLI-only conversions and defaults."""
    values: dict[str, object] = {
        field_name: _optional_text(raw_values[field_name])
        for field_name, _ in _REGISTRATION_PROMPTS
    }
    values["publication_year"] = _optional_ascii_integer(
        raw_values["publication_year"],
        "publication_year",
    )
    values["rating"] = _optional_ascii_integer(
        raw_values["rating"],
        "rating",
    )
    ai_summary = values["ai_summary"]
    values["ai_summary_status"] = (
        values["ai_summary_status"]
        or ("未確認" if ai_summary is not None else "未作成")
    )
    values["verification_status"] = (
        values["verification_status"] or "未確認"
    )
    values["adoption_status"] = values["adoption_status"] or "未判定"
    return values


def _run_registration(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect, review, duplicate-check, and optionally register literature."""
    if connection.in_transaction:
        output_func(_REGISTRATION_ACTIVE_TRANSACTION_MESSAGE)
        return False

    raw_values: dict[str, str] = {}
    for field_name, prompt in _REGISTRATION_PROMPTS:
        try:
            value = _read_input(input_func, prompt)
        except (EOFError, KeyboardInterrupt):
            return True
        raw_values[field_name] = value
        if field_name == "title" and not value.strip():
            output_func("入力エラー: タイトルは必須です。")
            return False

    try:
        values = _prepare_registration_values(raw_values)
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        literature = Literature(**values)
    except ValueError as error:
        output_func(f"登録エラー: {error}")
        return False

    output_func("登録内容を確認してください。")
    output_func(_format_registration_literature(literature))
    output_func("DOIとPMIDは登録時に標準形式へ正規化されます。")

    try:
        candidates = find_duplicate_candidates(
            connection,
            title=literature.title,
            doi=literature.doi,
            pmid=literature.pmid,
        )
    except ValueError as error:
        output_func(f"登録エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    _display_duplicate_candidates(candidates, output_func)
    output_func(_REGISTRATION_CONFIRMATION_MENU)
    while True:
        try:
            confirmation_input = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = confirmation_input.strip()
        if confirmation == "0":
            output_func("文献登録を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_REGISTRATION_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        literature_id = add_literature(connection, literature)
    except ValueError as error:
        output_func(f"登録エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func("文献を登録しました。")
    output_func(f"ID: {literature_id}")
    output_func(f"title: {literature.title}")
    return False


def _run_edit(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one partial literature update."""
    if connection.in_transaction:
        output_func(_EDIT_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        raw_literature_id = _read_input(
            input_func,
            "文献ID（ASCII数字）: ",
        )
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        literature_id = _required_positive_ascii_integer(
            raw_literature_id,
            "文献ID",
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        literature = get_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if literature is None:
        output_func("対象文献が見つかりません。")
        return False

    output_func("現在の文献情報:")
    output_func(_format_edit_literature(literature))
    output_func(_EDIT_FIELD_MENU)

    while True:
        try:
            raw_field_choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        field_choice = raw_field_choice.strip()
        if field_choice == "0":
            output_func("文献編集を中止しました。")
            return False
        if field_choice in {
            str(number) for number in range(1, len(_EDIT_FIELDS) + 1)
        }:
            break
        output_func(_INVALID_EDIT_FIELD_MESSAGE)

    field_name = _EDIT_FIELDS[int(field_choice) - 1]
    try:
        raw_new_value = _read_input(
            input_func,
            _EDIT_PROMPTS[field_name],
        )
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        new_value = _prepare_edit_value(field_name, raw_new_value)
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    output_func("文献の変更内容を確認してください。")
    output_func(f"ID: {literature_id}")
    output_func(f"title: {literature.title}")
    output_func(f"field: {field_name}")
    output_func(
        f"変更前: {_display_value(getattr(literature, field_name))}"
    )
    output_func(f"変更後: {_display_value(new_value)}")
    if field_name in {"doi", "pmid"}:
        output_func("DOIとPMIDは更新時に標準形式へ正規化されます。")

    output_func(_EDIT_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("文献更新を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_EDIT_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        updated = update_literature(
            connection,
            literature_id,
            {field_name: new_value},
        )
    except ValueError as error:
        output_func(f"更新エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not updated:
        output_func("確認後に対象文献が存在しなくなりました。")
        return False

    output_func("文献を更新しました。")
    output_func(f"ID: {literature_id}")
    output_func(f"field: {field_name}")
    return False


def _run_delete(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display deletion impact and require two confirmations before deletion."""
    if connection.in_transaction:
        output_func(_DELETE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        raw_literature_id = _read_input(
            input_func,
            "文献ID（ASCII数字）: ",
        )
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        literature_id = _required_positive_ascii_integer(
            raw_literature_id,
            "文献ID",
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        literature = get_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if literature is None:
        output_func("対象文献が見つかりません。")
        return False

    output_func("現在の文献情報:")
    output_func(_format_edit_literature(literature))

    try:
        related_counts = get_literature_related_counts(
            connection,
            literature_id,
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if related_counts is None:
        output_func(
            "現在の文献情報を表示した後に対象文献が存在しなくなりました。"
        )
        return False

    output_func("削除対象と影響を確認してください。")
    output_func(f"ID: {literature_id}")
    output_func(f"title: {literature.title}")
    output_func(f"タグ関連付け数: {related_counts['tag_count']}")
    output_func(f"使用履歴数: {related_counts['usage_history_count']}")
    output_func("関連件数は確認時点の値です。")
    output_func("警告: 文献レコードは削除されます。")
    output_func("タグとの関連付けは削除されます。")
    output_func("使用履歴は削除されます。")
    output_func("タグレコード自体は残ります。")
    output_func("pdf_pathが示す外部ファイルは削除されません。")
    output_func("CLIには自動復元機能がありません。")

    output_func(_DELETE_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("文献削除を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    final_confirmation_prompt = (
        f"削除を確定するため文献ID {literature_id} を再入力してください\n"
        "（0で中止）: "
    )
    invalid_final_confirmation_message = (
        f"入力エラー: 文献ID {literature_id} または0を入力してください。"
    )
    while True:
        try:
            raw_confirmed_id = _read_input(
                input_func,
                final_confirmation_prompt,
            )
        except (EOFError, KeyboardInterrupt):
            return True
        confirmed_id_text = raw_confirmed_id.strip()
        if confirmed_id_text == "0":
            output_func("文献削除を中止しました。")
            return False
        try:
            confirmed_id = _required_positive_ascii_integer(
                raw_confirmed_id,
                "文献ID",
            )
        except ValueError:
            output_func(invalid_final_confirmation_message)
            continue
        if confirmed_id == literature_id:
            break
        output_func(invalid_final_confirmation_message)

    if connection.in_transaction:
        output_func(_DELETE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        deleted = delete_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not deleted:
        output_func("確認後に対象文献が存在しなくなりました。")
        return False

    output_func("文献を削除しました。")
    output_func(f"ID: {literature_id}")
    output_func(f"title: {literature.title}")
    return False


def _run_tag_create(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one repository-backed tag creation request."""
    if connection.in_transaction:
        output_func(_TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        tag_name = _read_input(input_func, "タグ名（必須）: ")
    except (EOFError, KeyboardInterrupt):
        return True

    if not tag_name.strip():
        output_func("入力エラー: タグ名は必須です。")
        return False

    output_func("タグ登録内容を確認してください。")
    output_func(f"name: {tag_name}")
    output_func(_TAG_CREATE_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("タグ登録を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        tag_id = create_tag(connection, tag_name)
    except ValueError as error:
        output_func(f"タグ登録エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func("タグを登録または既存タグとして確認しました。")
    output_func(f"タグID: {tag_id}")
    return False


def _run_tag_rename(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one repository-backed tag rename request."""
    if connection.in_transaction:
        output_func(_TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        raw_tag_id = _read_input(input_func, "タグID（ASCII数字）: ")
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        tag_id = _required_positive_ascii_integer(raw_tag_id, "タグID")
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        tag = get_tag(connection, tag_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if tag is None:
        output_func("対象タグが見つかりません。")
        return False

    output_func("現在のタグ情報:")
    output_func(_format_tag(tag))

    try:
        new_name = _read_input(input_func, "新しいタグ名（必須）: ")
    except (EOFError, KeyboardInterrupt):
        return True

    if not new_name.strip():
        output_func("入力エラー: 新しいタグ名は必須です。")
        return False

    output_func("タグ名称の変更内容を確認してください。")
    output_func(f"ID: {tag_id}")
    output_func(f"変更前: {tag.name}")
    output_func(f"変更後: {new_name}")
    output_func(_TAG_RENAME_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("タグ名称変更を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        renamed = rename_tag(connection, tag_id, new_name)
    except ValueError as error:
        output_func(f"タグ名称変更エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not renamed:
        output_func("確認後に対象タグが存在しなくなりました。")
        return False

    output_func("タグ名称を変更しました。")
    output_func(f"タグID: {tag_id}")
    return False


def _run_tag_management(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Run the basic tag-management submenu without recursion."""
    while True:
        output_func(_TAG_MANAGEMENT_MENU)
        try:
            raw_choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        choice = raw_choice.strip()

        if choice == "0":
            return False
        if choice not in {"1", "2", "3"}:
            output_func(_INVALID_TAG_MENU_MESSAGE)
            continue

        if choice == "1":
            try:
                tags = list_tags(connection)
            except sqlite3.Error:
                output_func(_DATABASE_ERROR_MESSAGE)
                raise
            _display_tags(tags, output_func)
        elif choice == "2" and _run_tag_create(
            connection,
            input_func,
            output_func,
        ):
            return True
        elif choice == "3" and _run_tag_rename(
            connection,
            input_func,
            output_func,
        ):
            return True


def _run_search(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect all Step 8A filters, execute the existing search, and display it."""
    raw_values: dict[str, str] = {}
    for field_name, prompt in _SEARCH_PROMPTS:
        try:
            value = _read_input(input_func, prompt)
        except (EOFError, KeyboardInterrupt):
            return True
        raw_values[field_name] = value

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
    """Run the interactive menu using an existing SQLite connection."""
    while True:
        output_func(_MAIN_MENU)
        try:
            choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            output_func(_EXIT_MESSAGE)
            return None
        choice = choice.strip()

        if choice == "0":
            output_func(_EXIT_MESSAGE)
            return None
        if choice not in {"1", "2", "3", "4", "5", "6"}:
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
        elif choice == "2" and _run_search(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "3" and _run_registration(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "4" and _run_edit(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "5" and _run_delete(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "6" and _run_tag_management(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
