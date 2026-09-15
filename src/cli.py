"""Interactive CLI for literature, tag, and usage-history management."""

import shlex
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Mapping, Optional

from src.backup import create_database_backup
from src.csv_export import export_literature_csv
from src.comparison_matrix import (
    ComparisonMatrix,
    ComparisonMatrixError,
    ComparisonRow,
    ComparisonValue,
    build_comparison_matrix,
    parse_literature_id_input,
    select_search_result_ids,
)
from src.duplicates import DuplicateCandidate, find_duplicate_candidates
from src.evidence_review import (
    EVIDENCE_EDITABLE_FIELDS,
    EvidenceDetail,
    StructuredItemEvidence,
    attach_evidence,
    build_evidence_edit_preview,
    change_evidence_verification,
    create_review_evidence,
    delete_review_evidence,
    detach_evidence,
    evidence_locator_summary,
    get_evidence_detail,
    list_literature_evidence,
    list_structured_items_with_evidence,
    save_evidence_edit,
)
from src.models import EvidenceReference, Literature, Tag, UsageHistory
from src.pdf_navigation import (
    PDF_OPEN_FAILURE_MESSAGE,
    PdfNavigationError,
    PdfNavigationTarget,
    PdfOpenResult,
    build_navigation_target,
    format_locator_summary,
    open_evidence_pdf,
)
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
    delete_literature,
    delete_tag,
    delete_usage_history,
    detach_tag_from_literature,
    get_literature,
    get_literature_related_counts,
    get_tag,
    get_usage_history,
    list_literature,
    list_tags,
    list_tags_for_literature,
    list_usage_history_for_literature,
    rename_tag,
    update_literature,
    update_usage_history,
)
from src.search import search_literature
from src.structured_import import (
    ImportPreview,
    StructuredImportValidationError,
    build_import_preview,
    parse_structured_import,
    save_structured_import,
)


_MAIN_MENU = """理学療法文献ライブラリ

1. 文献一覧
2. 文献検索
3. 文献登録
4. 文献編集
5. 文献削除
6. タグ管理
7. 使用履歴管理
8. 文献詳細
9. CSV出力
10. SQLiteバックアップ
11. ChatGPT構造化JSON取込
12. Evidence確認・管理
13. 複数文献比較
0. 終了"""
_MENU_PROMPT = "選択してください: "
_INVALID_MENU_MESSAGE = (
    "入力エラー: "
    "0、1、2、3、4、5、6、7、8、9、10、11、12、13のいずれかを選択してください。"
)
_EXIT_MESSAGE = "CLIを終了します。"
_DATABASE_ERROR_MESSAGE = "データベースエラーが発生しました。"
_RECORD_SEPARATOR = "-" * 40
_AI_SUMMARY_STATUSES = ("未作成", "未確認", "確認済み", "修正済み")
_VERIFICATION_STATUSES = ("未確認", "一部確認", "確認済み", "要確認")
_ADOPTION_STATUSES = ("未判定", "採用候補", "採用", "除外")

_STRUCTURED_IMPORT_CONFIRMATION_MENU = """1. この内容で保存する
0. 保存せず戻る"""
_IDENTIFIER_STATE_LABELS = {
    "match": "一致",
    "conflict": "不一致（保存不可）",
    "existing_only": "Existing Literatureのみ",
    "payload_only": "Payloadのみ",
    "missing": "両方なし",
}

_EVIDENCE_MANAGEMENT_MENU = """Evidence確認・管理

1. Literature別Evidence一覧
2. Evidence詳細
3. Evidence新規作成
4. Evidence編集
5. Evidence確認状態変更
6. Structured item → Evidence確認
7. EvidenceをStructured itemへ関連付け
8. EvidenceとStructured itemの関連解除
9. Evidence削除
10. Evidenceから原著PDFを開く
0. メインメニューへ戻る"""
_INVALID_EVIDENCE_MENU_MESSAGE = (
    "入力エラー: 0〜10のいずれかを選択してください。"
)
_EVIDENCE_CREATE_CONFIRMATION_MENU = """1. 保存
0. 中止"""
_EVIDENCE_EDIT_FIELD_MENU = "\n".join(
    (
        *(
            f"{number}. {field_name}"
            for number, field_name in enumerate(
                EVIDENCE_EDITABLE_FIELDS, start=1
            )
        ),
        "0. 編集を中止する",
    )
)
_EVIDENCE_EDIT_CONFIRMATION_MENU = """1. この内容で保存
0. 中止"""
_EVIDENCE_VERIFICATION_CONFIRMATION_MENU = """1. 確認状態を変更
0. 中止"""
_EVIDENCE_ATTACH_CONFIRMATION_MENU = """1. この関連付けを保存
0. 中止"""
_EVIDENCE_DETACH_CONFIRMATION_MENU = """1. この関連を解除
0. 中止"""
_EVIDENCE_DELETE_CONFIRMATION_MENU = """1. 削除手続きを続ける
0. 中止"""
_EVIDENCE_PDF_OPEN_CONFIRMATION_MENU = """1. 原著PDFを開く
0. 中止"""
_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中はEvidenceを変更できません。"
)

_COMPARISON_MENU = """複数文献比較

1. 直前の検索結果から選択
2. Literature IDを指定
0. メインメニューへ戻る"""
_INVALID_COMPARISON_MENU_MESSAGE = (
    "入力エラー: 0、1、2のいずれかを選択してください。"
)
_MISSING_COMPARISON_SEARCH_RESULTS_MESSAGE = (
    "比較できる直前の検索結果がありません。"
    "先に文献検索を実行してください。"
)

_CSV_EXPORT_MENU = """CSV出力

1. 全文献を出力
2. 直前の検索結果を出力
0. メインメニューに戻る"""
_INVALID_CSV_EXPORT_MENU_MESSAGE = (
    "入力エラー: 0、1、2のいずれかを選択してください。"
)
_MISSING_SEARCH_RESULTS_MESSAGE = (
    "出力できる検索結果がありません。"
    "先に文献検索を実行してください。"
)
_ALL_LITERATURE_CSV_FILENAME = "literature_all.csv"
_SEARCH_RESULTS_CSV_FILENAME = "literature_search_results.csv"

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
4. タグ削除
5. 文献別タグ一覧
6. 文献へタグ付与
7. 文献からタグ解除
0. メインメニューに戻る"""
_INVALID_TAG_MENU_MESSAGE = (
    "入力エラー: 0〜7のいずれかを選択してください。"
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
_TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中はタグを削除できません。"
)
_TAG_ATTACH_CONFIRMATION_MENU = """1. このタグを文献へ付与する
0. 中止する"""
_TAG_DETACH_CONFIRMATION_MENU = """1. このタグを文献から解除する
0. 中止する"""
_TAG_ATTACH_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は文献へタグを付与できません。"
)
_TAG_DETACH_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は文献からタグを解除できません。"
)
_USAGE_HISTORY_MANAGEMENT_MENU = """使用履歴管理

1. 文献別使用履歴一覧
2. 使用履歴登録
3. 使用履歴編集
4. 使用履歴削除
0. メインメニューに戻る"""
_INVALID_USAGE_HISTORY_MENU_MESSAGE = (
    "入力エラー: 0、1、2、3、4のいずれかを選択してください。"
)
_USAGE_HISTORY_CREATE_CONFIRMATION_MENU = """1. この使用履歴を登録する
0. 登録を中止する"""
_USAGE_HISTORY_CREATE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は使用履歴を登録できません。"
)
_USAGE_HISTORY_EDIT_FIELDS = (
    "usage_type",
    "project_name",
    "usage_note",
    "used_at",
)
_USAGE_HISTORY_EDIT_FIELD_MENU = """1. usage_type
2. project_name
3. usage_note
4. used_at
0. 編集中止"""
_INVALID_USAGE_HISTORY_EDIT_FIELD_MESSAGE = (
    "入力エラー: 0、1、2、3、4のいずれかを選択してください。"
)
_USAGE_HISTORY_EDIT_PROMPTS = {
    "usage_type": "新しいusage_type（必須）: ",
    "project_name": "新しいproject_name（空欄で未登録）: ",
    "usage_note": "新しいusage_note（空欄で未登録）: ",
    "used_at": "新しいused_at（YYYY-MM-DD、空欄で未登録）: ",
}
_USAGE_HISTORY_EDIT_CONFIRMATION_MENU = """1. この内容で更新する
0. 更新を中止する"""
_USAGE_HISTORY_EDIT_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は使用履歴を編集できません。"
)
_USAGE_HISTORY_DELETE_ACTIVE_TRANSACTION_MESSAGE = (
    "アクティブなトランザクション中は使用履歴を削除できません。"
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


def _format_usage_history(usage_history: UsageHistory) -> str:
    """Format every usage-history field in repository model order."""
    fields = (
        ("id", usage_history.id),
        ("literature_id", usage_history.literature_id),
        ("usage_type", usage_history.usage_type),
        ("project_name", usage_history.project_name),
        ("usage_note", usage_history.usage_note),
        ("used_at", usage_history.used_at),
        ("created_at", usage_history.created_at),
    )
    return "\n".join(
        f"{label}: {_display_value(value)}" for label, value in fields
    )


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


def _optional_unmodified_text(value: str) -> Optional[str]:
    """Map blank CLI input to None while preserving non-blank contents."""
    return None if not value.strip() else value


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
        output_func(f"文献登録エラー: {error}")
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
        output_func(f"文献登録エラー: {error}")
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
        output_func(f"文献登録エラー: {error}")
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
        output_func(f"文献編集エラー: {error}")
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


def _run_tag_delete(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display tag-deletion impact and require two confirmations."""
    if connection.in_transaction:
        output_func(_TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE)
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

    output_func("削除対象タグ:")
    output_func(_format_tag(tag))
    output_func("削除対象と影響を確認してください。")
    output_func("警告: タグレコード自体は削除されます。")
    output_func("このタグとすべての文献との関連付けは削除されます。")
    output_func("文献レコード自体は削除されません。")
    output_func("使用履歴は削除されません。")
    output_func("他のタグは削除されません。")
    output_func("CLIには自動復元機能がありません。")

    output_func(_DELETE_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("タグ削除を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    final_confirmation_prompt = (
        f"削除を確定するためタグID {tag_id} を再入力してください\n"
        "（0で中止）: "
    )
    invalid_final_confirmation_message = (
        f"入力エラー: タグID {tag_id} または0を入力してください。"
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
            output_func("タグ削除を中止しました。")
            return False
        try:
            confirmed_id = _required_positive_ascii_integer(
                raw_confirmed_id,
                "タグID",
            )
        except ValueError:
            output_func(invalid_final_confirmation_message)
            continue
        if confirmed_id == tag_id:
            break
        output_func(invalid_final_confirmation_message)

    if connection.in_transaction:
        output_func(_TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        deleted = delete_tag(connection, tag_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not deleted:
        output_func("確認後に対象タグが存在しなくなりました。")
        return False

    output_func("タグを削除しました。")
    output_func(f"タグID: {tag_id}")
    output_func(f"name: {tag.name}")
    return False


def _run_literature_tag_list(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display one existing literature record's repository-provided tags."""
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

    output_func("文献情報:")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")

    try:
        tags = list_tags_for_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if tags is None:
        output_func("文献情報の確認後に対象文献が存在しなくなりました。")
        return False
    if not tags:
        output_func("この文献にはタグが登録されていません。")
        return False

    output_func("この文献に付与されているタグ:")
    for tag in tags:
        output_func(_format_tag(tag))
        output_func(_RECORD_SEPARATOR)
    return False


def _run_tag_attach(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one repository-backed tag attachment."""
    if connection.in_transaction:
        output_func(_TAG_ATTACH_ACTIVE_TRANSACTION_MESSAGE)
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

    output_func("タグ付与対象の文献:")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")

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

    output_func("文献へのタグ付与内容を確認してください。")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")
    output_func(f"タグID: {tag_id}")
    output_func(f"tag name: {tag.name}")
    output_func(_TAG_ATTACH_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("文献へのタグ付与を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_TAG_ATTACH_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        attached = attach_tag_to_literature(
            connection,
            literature_id,
            tag_id,
        )
    except ValueError as error:
        output_func(f"タグ付与エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not attached:
        output_func("このタグは既に対象文献へ付与されています。")
        return False

    output_func("文献へタグを付与しました。")
    output_func(f"文献ID: {literature_id}")
    output_func(f"タグID: {tag_id}")
    return False


def _run_tag_detach(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm removal of one literature-tag relationship."""
    if connection.in_transaction:
        output_func(_TAG_DETACH_ACTIVE_TRANSACTION_MESSAGE)
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

    output_func("タグ解除対象の文献:")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")

    try:
        current_tags = list_tags_for_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if current_tags is None:
        output_func("文献情報の確認後に対象文献が存在しなくなりました。")
        return False
    if not current_tags:
        output_func("この文献には解除できるタグがありません。")
        return False

    output_func("現在この文献に付与されているタグ:")
    for current_tag in current_tags:
        output_func(_format_tag(current_tag))
        output_func(_RECORD_SEPARATOR)

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
    if not any(current_tag.id == tag_id for current_tag in current_tags):
        output_func("このタグは対象文献に付与されていません。")
        return False

    output_func("文献からのタグ解除内容を確認してください。")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")
    output_func(f"タグID: {tag_id}")
    output_func(f"tag name: {tag.name}")
    output_func("解除するのは文献とタグの関連付けだけです。")
    output_func("タグレコード自体は削除されません。")
    output_func("文献レコード自体は削除されません。")
    output_func(_TAG_DETACH_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("文献からのタグ解除を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_TAG_DETACH_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        detached = detach_tag_from_literature(
            connection,
            literature_id,
            tag_id,
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not detached:
        output_func("確認後に対象のタグ関連付けが存在しなくなりました。")
        return False

    output_func("文献からタグを解除しました。")
    output_func(f"文献ID: {literature_id}")
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
        if choice not in {"1", "2", "3", "4", "5", "6", "7"}:
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
        elif choice == "4" and _run_tag_delete(
            connection,
            input_func,
            output_func,
        ):
            return True
        elif choice == "5" and _run_literature_tag_list(
            connection,
            input_func,
            output_func,
        ):
            return True
        elif choice == "6" and _run_tag_attach(
            connection,
            input_func,
            output_func,
        ):
            return True
        elif choice == "7" and _run_tag_detach(
            connection,
            input_func,
            output_func,
        ):
            return True


def _run_literature_usage_history_list(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display one existing literature record's repository-provided history."""
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

    output_func("文献情報:")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")

    try:
        histories = list_usage_history_for_literature(
            connection,
            literature_id,
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if histories is None:
        output_func("文献情報の確認後に対象文献が存在しなくなりました。")
        return False
    if not histories:
        output_func("この文献には使用履歴がありません。")
        return False

    output_func("この文献の使用履歴:")
    for usage_history in histories:
        output_func(_format_usage_history(usage_history))
        output_func(_RECORD_SEPARATOR)
    return False


def _run_usage_history_create(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one repository-backed usage-history creation."""
    if connection.in_transaction:
        output_func(_USAGE_HISTORY_CREATE_ACTIVE_TRANSACTION_MESSAGE)
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

    output_func("使用履歴登録対象の文献:")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")

    raw_values: dict[str, str] = {}
    prompts = (
        ("usage_type", "usage_type（必須）: "),
        ("project_name", "project_name（空欄で未登録）: "),
        ("usage_note", "usage_note（空欄で未登録）: "),
        ("used_at", "used_at（YYYY-MM-DD、空欄で未登録）: "),
    )
    for field_name, prompt in prompts:
        try:
            value = _read_input(input_func, prompt)
        except (EOFError, KeyboardInterrupt):
            return True
        raw_values[field_name] = value
        if field_name == "usage_type" and not value.strip():
            output_func("入力エラー: usage_typeは必須です。")
            return False

    usage_type = raw_values["usage_type"]
    project_name = _optional_unmodified_text(raw_values["project_name"])
    usage_note = _optional_unmodified_text(raw_values["usage_note"])
    used_at = _optional_unmodified_text(raw_values["used_at"])

    output_func("使用履歴登録内容を確認してください。")
    output_func(f"文献ID: {literature_id}")
    output_func(f"title: {literature.title}")
    output_func(f"usage_type: {_display_value(usage_type)}")
    output_func(f"project_name: {_display_value(project_name)}")
    output_func(f"usage_note: {_display_value(usage_note)}")
    output_func(f"used_at: {_display_value(used_at)}")
    output_func(_USAGE_HISTORY_CREATE_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("使用履歴登録を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_USAGE_HISTORY_CREATE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        usage_history_id = create_usage_history(
            connection,
            literature_id,
            usage_type,
            project_name,
            usage_note,
            used_at,
        )
    except ValueError as error:
        output_func(f"使用履歴登録エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func("使用履歴を登録しました。")
    output_func(f"使用履歴ID: {usage_history_id}")
    output_func(f"文献ID: {literature_id}")
    return False


def _run_usage_history_edit(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Collect and confirm one repository-backed usage-history update."""
    if connection.in_transaction:
        output_func(_USAGE_HISTORY_EDIT_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        raw_usage_history_id = _read_input(
            input_func,
            "使用履歴ID（ASCII数字）: ",
        )
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        usage_history_id = _required_positive_ascii_integer(
            raw_usage_history_id,
            "使用履歴ID",
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        usage_history = get_usage_history(connection, usage_history_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if usage_history is None:
        output_func("対象の使用履歴が見つかりません。")
        return False

    output_func("現在の使用履歴情報:")
    output_func(_format_usage_history(usage_history))
    output_func(_USAGE_HISTORY_EDIT_FIELD_MENU)

    while True:
        try:
            raw_field_choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        field_choice = raw_field_choice.strip()
        if field_choice == "0":
            output_func("使用履歴編集を中止しました。")
            return False
        if field_choice in {"1", "2", "3", "4"}:
            break
        output_func(_INVALID_USAGE_HISTORY_EDIT_FIELD_MESSAGE)

    field_name = _USAGE_HISTORY_EDIT_FIELDS[int(field_choice) - 1]
    try:
        raw_new_value = _read_input(
            input_func,
            _USAGE_HISTORY_EDIT_PROMPTS[field_name],
        )
    except (EOFError, KeyboardInterrupt):
        return True

    if field_name == "usage_type":
        if not raw_new_value.strip():
            output_func("入力エラー: usage_typeは必須です。")
            return False
        new_value: object = raw_new_value
    else:
        new_value = _optional_unmodified_text(raw_new_value)

    output_func("使用履歴の変更内容を確認してください。")
    output_func(f"使用履歴ID: {usage_history_id}")
    output_func(f"編集項目: {field_name}")
    output_func(
        f"変更前: {_display_value(getattr(usage_history, field_name))}"
    )
    output_func(f"変更後: {_display_value(new_value)}")
    output_func(_USAGE_HISTORY_EDIT_CONFIRMATION_MENU)
    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("使用履歴更新を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    if connection.in_transaction:
        output_func(_USAGE_HISTORY_EDIT_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        updated = update_usage_history(
            connection,
            usage_history_id,
            {field_name: new_value},
        )
    except ValueError as error:
        output_func(f"使用履歴編集エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not updated:
        output_func("確認後に対象の使用履歴が存在しなくなりました。")
        return False

    output_func("使用履歴を更新しました。")
    output_func(f"使用履歴ID: {usage_history_id}")
    output_func(f"更新項目: {field_name}")
    return False


def _run_usage_history_delete(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display impact and require two confirmations before deleting history."""
    if connection.in_transaction:
        output_func(_USAGE_HISTORY_DELETE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        raw_usage_history_id = _read_input(
            input_func,
            "使用履歴ID（ASCII数字）: ",
        )
    except (EOFError, KeyboardInterrupt):
        return True

    try:
        usage_history_id = _required_positive_ascii_integer(
            raw_usage_history_id,
            "使用履歴ID",
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        usage_history = get_usage_history(connection, usage_history_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if usage_history is None:
        output_func("対象の使用履歴が見つかりません。")
        return False

    output_func("削除対象の使用履歴:")
    output_func(_format_usage_history(usage_history))
    output_func("削除対象と影響を確認してください。")
    output_func("警告: この使用履歴レコード自体が削除されます。")
    output_func("対象文献は削除されません。")
    output_func("タグは削除されません。")
    output_func("文献とタグの関連付けは変更されません。")
    output_func("他の使用履歴は削除されません。")
    output_func("CLIには自動復元機能がありません。")
    output_func(_DELETE_CONFIRMATION_MENU)

    while True:
        try:
            raw_confirmation = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        confirmation = raw_confirmation.strip()
        if confirmation == "0":
            output_func("使用履歴削除を中止しました。")
            return False
        if confirmation == "1":
            break
        output_func(_INVALID_CONFIRMATION_MESSAGE)

    final_confirmation_prompt = (
        f"削除を確定するため使用履歴ID {usage_history_id} "
        "を再入力してください\n（0で中止）: "
    )
    invalid_final_confirmation_message = (
        f"入力エラー: 使用履歴ID {usage_history_id} または0を入力してください。"
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
            output_func("使用履歴削除を中止しました。")
            return False
        try:
            confirmed_id = _required_positive_ascii_integer(
                raw_confirmed_id,
                "使用履歴ID",
            )
        except ValueError:
            output_func(invalid_final_confirmation_message)
            continue
        if confirmed_id == usage_history_id:
            break
        output_func(invalid_final_confirmation_message)

    if connection.in_transaction:
        output_func(_USAGE_HISTORY_DELETE_ACTIVE_TRANSACTION_MESSAGE)
        return False

    try:
        deleted = delete_usage_history(connection, usage_history_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not deleted:
        output_func("確認後に対象の使用履歴が存在しなくなりました。")
        return False

    output_func("使用履歴を削除しました。")
    output_func(f"使用履歴ID: {usage_history_id}")
    return False


def _run_usage_history_management(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Run the usage-history submenu without recursion."""
    while True:
        output_func(_USAGE_HISTORY_MANAGEMENT_MENU)
        try:
            raw_choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        choice = raw_choice.strip()

        if choice == "0":
            return False
        if choice not in {"1", "2", "3", "4"}:
            output_func(_INVALID_USAGE_HISTORY_MENU_MESSAGE)
            continue

        if choice == "1" and _run_literature_usage_history_list(
            connection,
            input_func,
            output_func,
        ):
            return True
        if choice == "2" and _run_usage_history_create(
            connection,
            input_func,
            output_func,
        ):
            return True
        if choice == "3" and _run_usage_history_edit(
            connection,
            input_func,
            output_func,
        ):
            return True
        if choice == "4" and _run_usage_history_delete(
            connection,
            input_func,
            output_func,
        ):
            return True


def _run_search(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool | tuple[int, ...]:
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
    return tuple(result.id for result in results)


def _run_csv_export(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
    export_directory: object,
    search_result_ids: Optional[tuple[int, ...]],
) -> bool:
    """Export all literature or the last successful search without recursion."""
    while True:
        output_func(_CSV_EXPORT_MENU)
        try:
            raw_choice = _read_input(input_func, _MENU_PROMPT)
        except (EOFError, KeyboardInterrupt):
            return True
        choice = raw_choice.strip()

        if choice == "0":
            return False
        if choice not in {"1", "2"}:
            output_func(_INVALID_CSV_EXPORT_MENU_MESSAGE)
            continue

        if choice == "1":
            output_path = (
                Path(export_directory) / _ALL_LITERATURE_CSV_FILENAME
            )
            literature_ids: object = None
        else:
            if search_result_ids is None:
                output_func(_MISSING_SEARCH_RESULTS_MESSAGE)
                continue
            output_path = (
                Path(export_directory) / _SEARCH_RESULTS_CSV_FILENAME
            )
            literature_ids = search_result_ids

        try:
            row_count = export_literature_csv(
                connection,
                output_path,
                literature_ids=literature_ids,
            )
        except (ValueError, OSError) as error:
            output_func(f"CSV出力エラー: {error}")
            continue
        except sqlite3.Error:
            output_func(_DATABASE_ERROR_MESSAGE)
            raise

        output_func("CSVを出力しました。")
        output_func(f"件数: {row_count}")
        output_func(f"出力先: {output_path}")


def _run_database_backup(
    connection: sqlite3.Connection,
    output_func: Callable[[str], object],
    backup_directory: object,
) -> None:
    """Create one backup through the existing core API."""
    try:
        backup_path = create_database_backup(
            connection,
            backup_directory,
        )
    except (ValueError, OSError) as error:
        output_func(f"バックアップエラー: {error}")
        return None
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func("データベースをバックアップしました。")
    output_func(f"保存先: {backup_path}")
    return None


def _run_literature_detail(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Display one literature record with its tags and usage history."""
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

    output_func("文献詳細:")
    output_func(_format_edit_literature(literature))
    output_func("タグ:")

    try:
        tags = list_tags_for_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if tags is None:
        output_func("文献情報の取得中に対象文献が存在しなくなりました。")
        return False
    if not tags:
        output_func("この文献にはタグが登録されていません。")
    else:
        for tag in tags:
            output_func(_format_tag(tag))
            output_func(_RECORD_SEPARATOR)

    output_func("使用履歴:")
    try:
        histories = list_usage_history_for_literature(
            connection,
            literature_id,
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if histories is None:
        output_func("文献情報の取得中に対象文献が存在しなくなりました。")
        return False
    if not histories:
        output_func("この文献には使用履歴がありません。")
    else:
        for usage_history in histories:
            output_func(_format_usage_history(usage_history))
            output_func(_RECORD_SEPARATOR)

    try:
        evidence = list_literature_evidence(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if evidence is None:
        output_func("文献情報の取得中に対象文献が存在しなくなりました。")
        return False
    output_func("Structured Evidence:")
    if not evidence:
        output_func("Evidence: 0件")
    else:
        ai_unverified_count = sum(
            item.verification == "ai_unverified" for item in evidence
        )
        user_verified_count = sum(
            item.verification == "user_verified" for item in evidence
        )
        output_func(f"Evidence件数: {len(evidence)}")
        output_func(f"ai_unverified件数: {ai_unverified_count}")
        output_func(f"user_verified件数: {user_verified_count}")
    return False


def _format_evidence_list_item(
    evidence: EvidenceReference, selection_number: int
) -> str:
    """Format one Evidence choice without exposing its internal DB ID."""
    fields = (
        ("選択番号", selection_number),
        ("pdf_page", evidence.pdf_page),
        ("printed_page", evidence.printed_page),
        ("section", evidence.section),
        ("subsection", evidence.subsection),
        ("table_label", evidence.table_label),
        ("figure_label", evidence.figure_label),
        ("quote_text", "あり" if evidence.quote_text is not None else "なし"),
        ("verification", evidence.verification),
        (
            "note",
            "あり"
            if isinstance(evidence.note, str) and evidence.note.strip()
            else "なし",
        ),
    )
    return "\n".join(
        f"{label}: {_display_value(value)}" for label, value in fields
    )


def _format_evidence_values(evidence: EvidenceReference) -> str:
    """Format complete Evidence metadata for detail and verification review."""
    fields = (
        ("pdf_page", evidence.pdf_page),
        ("printed_page", evidence.printed_page),
        ("section", evidence.section),
        ("subsection", evidence.subsection),
        ("table_label", evidence.table_label),
        ("figure_label", evidence.figure_label),
        ("quote_text", evidence.quote_text),
        ("note", evidence.note),
        ("verification", evidence.verification),
        ("created_at", evidence.created_at),
        ("updated_at", evidence.updated_at),
    )
    return "\n".join(
        f"{label}: {_display_value(value)}" for label, value in fields
    )


def _format_structured_item_link(item: StructuredItemEvidence) -> str:
    kind_label = "Field link" if item.kind == "field" else "Entity link"
    lines = [
        f"{kind_label}: {item.category_label}",
        item.item_label,
    ]
    if item.value_text is not None:
        lines.append(f"value: {item.value_text}")
    return "\n".join(lines)


def _format_evidence_detail(detail: EvidenceDetail) -> str:
    lines = ["Evidence詳細:", _format_evidence_values(detail.evidence), ""]
    lines.append("このEvidenceが支えているstructured item:")
    if not detail.field_links and not detail.entity_links:
        lines.append("関連するstructured itemはありません。")
    else:
        for item in (*detail.field_links, *detail.entity_links):
            lines.append(_format_structured_item_link(item))
            lines.append(_RECORD_SEPARATOR)
    return "\n".join(lines)


def _format_structured_item_choice(
    item: StructuredItemEvidence, selection_number: int
) -> str:
    lines = [
        f"選択番号: {selection_number}",
        f"種類: {'Structured field' if item.kind == 'field' else 'Structured entity'}",
        f"区分: {item.category_label}",
        f"項目: {item.item_label}",
    ]
    if item.value_text is not None:
        lines.append(f"value: {item.value_text}")
    lines.append(f"linked Evidence count: {len(item.evidence)}")
    if item.evidence:
        lines.extend(
            f"Evidence {index}: {evidence_locator_summary(evidence)}"
            for index, evidence in enumerate(item.evidence, start=1)
        )
    else:
        lines.append("linked Evidence: なし")
    return "\n".join(lines)


def _display_evidence_choices(
    evidence: Sequence[EvidenceReference],
    output_func: Callable[[str], object],
) -> None:
    if not evidence:
        output_func("このLiteratureにはEvidenceが登録されていません。")
        return
    for selection_number, item in enumerate(evidence, start=1):
        output_func(_format_evidence_list_item(item, selection_number))
        output_func(_RECORD_SEPARATOR)


def _read_evidence_literature(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> tuple[Optional[Literature], Optional[list[EvidenceReference]], bool]:
    try:
        raw_literature_id = _read_input(
            input_func, "対象Literature ID（ASCII数字）: "
        )
    except (EOFError, KeyboardInterrupt):
        return None, None, True
    try:
        literature_id = _required_positive_ascii_integer(
            raw_literature_id, "Literature ID"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return None, None, False
    try:
        literature = get_literature(connection, literature_id)
        evidence = list_literature_evidence(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if literature is None or evidence is None:
        output_func("対象Literatureが見つかりません。")
        return None, None, False
    return literature, evidence, False


def _select_evidence(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> tuple[
    Optional[Literature], Optional[EvidenceReference], Optional[int], bool
]:
    literature, evidence, interrupted = _read_evidence_literature(
        connection, input_func, output_func
    )
    if interrupted or literature is None or evidence is None:
        return literature, None, None, interrupted
    output_func(f"Literature title: {literature.title}")
    _display_evidence_choices(evidence, output_func)
    if not evidence:
        return literature, None, None, False
    try:
        raw_selection = _read_input(
            input_func, "Evidence選択番号（ASCII数字）: "
        )
    except (EOFError, KeyboardInterrupt):
        return literature, None, None, True
    try:
        selection = _required_positive_ascii_integer(
            raw_selection, "Evidence選択番号"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return literature, None, None, False
    if selection > len(evidence):
        output_func("入力エラー: 表示されたEvidence選択番号を入力してください。")
        return literature, None, None, False
    return literature, evidence[selection - 1], selection, False


def _run_evidence_list(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    literature, evidence, interrupted = _read_evidence_literature(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None or evidence is None:
        return False
    output_func(f"Literature title: {literature.title}")
    output_func(f"Evidence件数: {len(evidence)}")
    _display_evidence_choices(evidence, output_func)
    return False


def _run_evidence_detail(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    _, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if evidence is None:
        return False
    try:
        detail = get_evidence_detail(connection, evidence.id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if detail is None:
        output_func("選択後に対象Evidenceが存在しなくなりました。")
        return False
    output_func(_format_evidence_detail(detail))
    return False


def _read_evidence_input_values(
    input_func: Callable[[str], str],
) -> dict[str, object]:
    values: dict[str, object] = {}
    for field_name in EVIDENCE_EDITABLE_FIELDS:
        suffix = (
            "1以上のASCII数字、空欄で未登録"
            if field_name == "pdf_page"
            else "空欄で未登録"
        )
        raw_value = _read_input(input_func, f"{field_name}（{suffix}）: ")
        if field_name == "pdf_page":
            value = _optional_ascii_integer(raw_value, field_name)
            if value is not None and value < 1:
                raise ValueError("pdf_pageは1以上の整数で入力してください。")
            values[field_name] = value
        else:
            values[field_name] = _optional_unmodified_text(raw_value)
    return values


def _format_evidence_preview_values(
    values: Mapping[str, object], *, verification: str
) -> str:
    return "\n".join(
        (
            *(
                f"{field_name}: {_display_value(values.get(field_name))}"
                for field_name in EVIDENCE_EDITABLE_FIELDS
            ),
            f"verification: {verification}",
        )
    )


def _confirm_evidence_action(
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
    menu: str,
) -> tuple[bool, bool]:
    while True:
        output_func(menu)
        try:
            choice = _read_input(input_func, _MENU_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            return False, True
        if choice == "0":
            return False, False
        if choice == "1":
            return True, False
        output_func(_INVALID_CONFIRMATION_MESSAGE)


def _run_evidence_create(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    literature, _, interrupted = _read_evidence_literature(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None:
        return False
    try:
        values = _read_evidence_input_values(input_func)
    except (EOFError, KeyboardInterrupt):
        return True
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False
    output_func("Evidence保存前Preview:")
    output_func(
        _format_evidence_preview_values(values, verification="ai_unverified")
    )
    confirmed, interrupted = _confirm_evidence_action(
        input_func,
        output_func,
        _EVIDENCE_CREATE_CONFIRMATION_MENU,
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidenceの保存を中止しました。")
        return False
    try:
        create_review_evidence(
            connection,
            literature.id,
            **values,
            confirmed=True,
        )
    except ValueError as error:
        output_func(f"Evidence作成エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    output_func("Evidenceを保存しました。確認状態: ai_unverified")
    return False


def _run_evidence_edit(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    _, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if evidence is None:
        return False
    output_func("現在のEvidence:")
    output_func(_format_evidence_values(evidence))
    output_func(_EVIDENCE_EDIT_FIELD_MENU)
    try:
        raw_field_choice = _read_input(input_func, _MENU_PROMPT)
    except (EOFError, KeyboardInterrupt):
        return True
    field_choice = raw_field_choice.strip()
    if field_choice == "0":
        output_func("Evidence編集を中止しました。")
        return False
    try:
        field_number = _required_positive_ascii_integer(
            field_choice, "編集項目番号"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False
    if field_number > len(EVIDENCE_EDITABLE_FIELDS):
        output_func("入力エラー: 0〜8のいずれかを選択してください。")
        return False
    field_name = EVIDENCE_EDITABLE_FIELDS[field_number - 1]
    try:
        raw_value = _read_input(
            input_func,
            f"新しい{field_name}（空欄で未登録）: ",
        )
    except (EOFError, KeyboardInterrupt):
        return True
    try:
        if field_name == "pdf_page":
            new_value = _optional_ascii_integer(raw_value, field_name)
            if new_value is not None and new_value < 1:
                raise ValueError("pdf_pageは1以上の整数で入力してください。")
        else:
            new_value = _optional_unmodified_text(raw_value)
        preview = build_evidence_edit_preview(
            connection, evidence.id, {field_name: new_value}
        )
    except ValueError as error:
        output_func(f"Evidence編集エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if preview is None:
        output_func("選択後に対象Evidenceが存在しなくなりました。")
        return False
    preview_values = {
        name: getattr(preview.original, name)
        for name in EVIDENCE_EDITABLE_FIELDS
    }
    preview_values.update(dict(preview.updates))
    output_func("Evidence更新前Preview:")
    output_func(
        _format_evidence_preview_values(
            preview_values,
            verification=preview.verification_after_save,
        )
    )
    if (
        preview.original.verification == "user_verified"
        and preview.substantive_change
    ):
        output_func(
            "根拠位置または原文を変更するため、"
            "確認状態はai_unverifiedへ戻ります"
        )
    confirmed, interrupted = _confirm_evidence_action(
        input_func,
        output_func,
        _EVIDENCE_EDIT_CONFIRMATION_MENU,
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidence更新を中止しました。")
        return False
    try:
        updated = save_evidence_edit(connection, preview, confirmed=True)
    except ValueError as error:
        output_func(f"Evidence編集エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if not updated:
        output_func("確認後に対象Evidenceが存在しなくなりました。")
        return False
    output_func("Evidenceを更新しました。")
    return False


def _run_evidence_verification(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    _, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if evidence is None:
        return False
    try:
        detail = get_evidence_detail(connection, evidence.id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if detail is None:
        output_func("選択後に対象Evidenceが存在しなくなりました。")
        return False
    output_func(_format_evidence_detail(detail))
    target = (
        "user_verified"
        if evidence.verification == "ai_unverified"
        else "ai_unverified"
    )
    if target == "user_verified":
        output_func(
            "原著の該当箇所を確認した場合のみ確認済みにしてください。"
        )
    else:
        output_func("このEvidenceの確認済み状態を取り消します。")
    output_func(f"確認状態: {evidence.verification} → {target}")
    confirmed, interrupted = _confirm_evidence_action(
        input_func,
        output_func,
        _EVIDENCE_VERIFICATION_CONFIRMATION_MENU,
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidence確認状態の変更を中止しました。")
        return False
    try:
        updated = change_evidence_verification(
            connection, evidence.id, target, confirmed=True
        )
    except ValueError as error:
        output_func(f"Evidence確認状態変更エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if not updated:
        output_func("確認後に対象Evidenceが存在しなくなりました。")
        return False
    output_func(f"Evidence確認状態を{target}へ変更しました。")
    return False


def _read_structured_items(
    connection: sqlite3.Connection,
    literature_id: int,
    output_func: Callable[[str], object],
) -> Optional[list[StructuredItemEvidence]]:
    try:
        items = list_structured_items_with_evidence(
            connection, literature_id
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if items is None:
        output_func("対象Literatureが見つかりません。")
        return None
    if not items:
        output_func("このLiteratureにはstructured itemがありません。")
        return []
    for selection_number, item in enumerate(items, start=1):
        output_func(_format_structured_item_choice(item, selection_number))
        output_func(_RECORD_SEPARATOR)
    return items


def _run_structured_item_evidence_list(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    literature, _, interrupted = _read_evidence_literature(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None:
        return False
    output_func(f"Literature title: {literature.title}")
    _read_structured_items(connection, literature.id, output_func)
    return False


def _select_structured_item(
    connection: sqlite3.Connection,
    literature_id: int,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> tuple[Optional[StructuredItemEvidence], bool]:
    items = _read_structured_items(connection, literature_id, output_func)
    if not items:
        return None, False
    try:
        raw_selection = _read_input(
            input_func, "Structured item選択番号（ASCII数字）: "
        )
    except (EOFError, KeyboardInterrupt):
        return None, True
    try:
        selection = _required_positive_ascii_integer(
            raw_selection, "Structured item選択番号"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return None, False
    if selection > len(items):
        output_func(
            "入力エラー: 表示されたStructured item選択番号を入力してください。"
        )
        return None, False
    return items[selection - 1], False


def _run_evidence_attach(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    literature, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None or evidence is None:
        return False
    item, interrupted = _select_structured_item(
        connection, literature.id, input_func, output_func
    )
    if interrupted:
        return True
    if item is None:
        return False
    output_func("関連付け内容:")
    output_func(_format_structured_item_link(item))
    output_func(f"Evidence: {evidence_locator_summary(evidence)}")
    confirmed, interrupted = _confirm_evidence_action(
        input_func, output_func, _EVIDENCE_ATTACH_CONFIRMATION_MENU
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidenceの関連付けを中止しました。")
        return False
    try:
        attached = attach_evidence(
            connection,
            item.kind,
            item.owner_id,
            evidence.id,
            confirmed=True,
        )
    except ValueError as error:
        output_func(f"Evidence関連付けエラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if attached:
        output_func("Evidenceをstructured itemへ関連付けました。")
    else:
        output_func("同じ関連付けが既に存在します。重複作成しませんでした。")
    return False


def _run_evidence_detach(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    _, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if evidence is None:
        return False
    try:
        detail = get_evidence_detail(connection, evidence.id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if detail is None:
        output_func("選択後に対象Evidenceが存在しなくなりました。")
        return False
    links = [*detail.entity_links, *detail.field_links]
    if not links:
        output_func("このEvidenceに解除可能な関連はありません。")
        return False
    for selection_number, item in enumerate(links, start=1):
        output_func(f"選択番号: {selection_number}")
        output_func(_format_structured_item_link(item))
        output_func(_RECORD_SEPARATOR)
    try:
        raw_selection = _read_input(
            input_func, "解除する関連の選択番号（ASCII数字）: "
        )
    except (EOFError, KeyboardInterrupt):
        return True
    try:
        selection = _required_positive_ascii_integer(
            raw_selection, "関連選択番号"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False
    if selection > len(links):
        output_func("入力エラー: 表示された関連選択番号を入力してください。")
        return False
    item = links[selection - 1]
    confirmed, interrupted = _confirm_evidence_action(
        input_func, output_func, _EVIDENCE_DETACH_CONFIRMATION_MENU
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidenceの関連解除を中止しました。")
        return False
    try:
        detached = detach_evidence(
            connection,
            item.kind,
            item.owner_id,
            evidence.id,
            confirmed=True,
        )
    except ValueError as error:
        output_func(f"Evidence関連解除エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if detached:
        output_func("Evidenceとstructured itemの関連を解除しました。")
    else:
        output_func("確認後に対象の関連が存在しなくなりました。")
    return False


def _run_evidence_delete(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    if connection.in_transaction:
        output_func(_EVIDENCE_ACTIVE_TRANSACTION_MESSAGE)
        return False
    literature, evidence, selection_number, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None or evidence is None or selection_number is None:
        return False
    try:
        detail = get_evidence_detail(connection, evidence.id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if detail is None:
        output_func("選択後に対象Evidenceが存在しなくなりました。")
        return False
    output_func("Evidence削除対象と影響:")
    output_func(f"Literature title: {literature.title}")
    output_func(f"Evidence locator: {evidence_locator_summary(evidence)}")
    output_func(f"verification: {evidence.verification}")
    output_func(f"field link数: {len(detail.field_links)}")
    output_func(f"entity link数: {len(detail.entity_links)}")
    output_func("警告: Evidence本体は削除されます。")
    output_func("Evidence linkも削除されます。")
    output_func("structured entity / field本体は削除されません。")
    output_func("Literature本体は削除されません。")
    output_func("PDFファイルは削除されません。")
    confirmed, interrupted = _confirm_evidence_action(
        input_func, output_func, _EVIDENCE_DELETE_CONFIRMATION_MENU
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("Evidence削除を中止しました。")
        return False
    final_prompt = (
        "削除を確定するためEvidence選択番号 "
        f"{selection_number} を再入力してください（0で中止）: "
    )
    while True:
        try:
            raw_final = _read_input(input_func, final_prompt)
        except (EOFError, KeyboardInterrupt):
            return True
        final_value = raw_final.strip()
        if final_value == "0":
            output_func("Evidence削除を中止しました。")
            return False
        try:
            confirmed_number = _required_positive_ascii_integer(
                raw_final, "Evidence選択番号"
            )
        except ValueError:
            output_func(
                f"入力エラー: Evidence選択番号 {selection_number} または0を入力してください。"
            )
            continue
        if confirmed_number == selection_number:
            break
        output_func(
            f"入力エラー: Evidence選択番号 {selection_number} または0を入力してください。"
        )
    try:
        deleted = delete_review_evidence(
            connection, evidence.id, confirmed=True
        )
    except ValueError as error:
        output_func(f"Evidence削除エラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if not deleted:
        output_func("確認後に対象Evidenceが存在しなくなりました。")
        return False
    output_func("Evidenceを削除しました。")
    return False


def _format_pdf_navigation_preview(target: PdfNavigationTarget) -> str:
    """Format a researcher-facing preview without emphasizing internal IDs."""
    return "\n".join(
        (
            "Navigation Preview:",
            f"Literature title: {target.literature_title}",
            f"PDF path: {_display_value(target.stored_pdf_path)}",
            f"Evidence verification: {target.verification}",
            format_locator_summary(target),
        )
    )


def _run_evidence_pdf_navigation(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
    *,
    project_root: object | None,
    pdf_opener: Callable[..., PdfOpenResult],
) -> bool:
    literature, evidence, _, interrupted = _select_evidence(
        connection, input_func, output_func
    )
    if interrupted:
        return True
    if literature is None or evidence is None:
        return False

    try:
        target = build_navigation_target(
            connection,
            literature.id,
            evidence.id,
            project_root=project_root,
        )
    except PdfNavigationError as error:
        output_func(f"PDF navigationエラー: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func(_format_pdf_navigation_preview(target))
    if not target.can_open:
        output_func(target.path_error or PDF_OPEN_FAILURE_MESSAGE)
        return False

    confirmed, interrupted = _confirm_evidence_action(
        input_func,
        output_func,
        _EVIDENCE_PDF_OPEN_CONFIRMATION_MENU,
    )
    if interrupted:
        return True
    if not confirmed:
        output_func("原著PDFを開く操作を中止しました。")
        return False

    try:
        result = pdf_opener(
            connection,
            target,
            project_root=project_root,
        )
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    if not result.success:
        output_func(PDF_OPEN_FAILURE_MESSAGE)
        if result.error_message not in {None, PDF_OPEN_FAILURE_MESSAGE}:
            output_func(f"PDF navigationエラー: {result.error_message}")
        return False

    output_func("原著PDFを開きました。")
    output_func("確認位置:")
    output_func(format_locator_summary(result.target))
    if result.target.pdf_page is not None:
        output_func(
            "PreviewでPDF page "
            f"{result.target.pdf_page}へ移動:\n"
            f"⌘⌥G → {result.target.pdf_page}"
        )
    return False


def _run_evidence_management(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
    *,
    project_root: object | None = None,
    pdf_opener: Optional[Callable[..., PdfOpenResult]] = None,
) -> bool:
    navigation_opener = open_evidence_pdf if pdf_opener is None else pdf_opener
    actions = {
        "1": _run_evidence_list,
        "2": _run_evidence_detail,
        "3": _run_evidence_create,
        "4": _run_evidence_edit,
        "5": _run_evidence_verification,
        "6": _run_structured_item_evidence_list,
        "7": _run_evidence_attach,
        "8": _run_evidence_detach,
        "9": _run_evidence_delete,
    }
    while True:
        output_func(_EVIDENCE_MANAGEMENT_MENU)
        try:
            choice = _read_input(input_func, _MENU_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if choice == "0":
            return False
        action = actions.get(choice)
        if action is None:
            if choice == "10":
                if _run_evidence_pdf_navigation(
                    connection,
                    input_func,
                    output_func,
                    project_root=project_root,
                    pdf_opener=navigation_opener,
                ):
                    return True
                continue
            output_func(_INVALID_EVIDENCE_MENU_MESSAGE)
            continue
        if action(connection, input_func, output_func):
            return True


def _comparison_value_metadata(value: ComparisonValue, indent: str) -> list[str]:
    return [
        f"{indent}field verification: {value.verification}",
        f"{indent}Evidence: {value.evidence_count}",
        (
            f"{indent}user_verified Evidence: "
            f"{value.user_verified_evidence_count}/{value.evidence_count}"
        ),
    ]


def _comparison_ordinal(number: int) -> str:
    circled = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    return circled[number - 1] if number <= len(circled) else f"({number})"


def _format_comparison_row(row: ComparisonRow) -> list[str]:
    lines = [row.field_key]
    for column_number, cell in enumerate(row.cells, start=1):
        if not cell.values:
            lines.append(f"[{column_number}] 未登録")
            continue
        multiple = len(cell.values) > 1
        for value_number, value in enumerate(cell.values, start=1):
            prefix = f"[{column_number}] " if value_number == 1 else "    "
            ordinal = (
                f"{_comparison_ordinal(value_number)} " if multiple else ""
            )
            lines.append(f"{prefix}{ordinal}{value.display_text}")
            lines.extend(_comparison_value_metadata(value, "    "))
    return lines


def _append_profile_field(
    lines: list[str],
    field_key: str,
    value: Optional[ComparisonValue],
    *,
    indent: str,
) -> None:
    if value is None:
        lines.append(f"{indent}{field_key}: 未登録")
        return
    lines.append(f"{indent}{field_key}: {value.display_text}")
    lines.extend(_comparison_value_metadata(value, indent + "  "))


def _format_comparison_matrix(matrix: ComparisonMatrix) -> str:
    """Format the semantic matrix row-first while keeping Outcomes unaligned."""
    lines = ["=" * 40, "比較文献"]
    for number, literature in enumerate(matrix.literature_columns, start=1):
        year = _display_value(literature.publication_year)
        lines.append(f"[{number}] {literature.title} ({year})")
    lines.extend(("=" * 40, "", "Study"))
    if matrix.study_rows:
        for row in matrix.study_rows:
            lines.extend(("", *_format_comparison_row(row)))
    else:
        lines.append("比較対象の登録済みStudy fieldはありません。")

    lines.extend(("", _RECORD_SEPARATOR, "", "Methods"))
    for group in matrix.method_groups:
        lines.extend(("", f"Methods / {group.name}"))
        if group.rows:
            for row in group.rows:
                lines.extend(("", *_format_comparison_row(row)))
        else:
            lines.append("比較対象の登録済みfieldはありません。")

    lines.extend(("", _RECORD_SEPARATOR, "", "Outcomes"))
    for column_number, literature_outcomes in enumerate(
        matrix.outcomes_by_literature, start=1
    ):
        literature = literature_outcomes.literature
        year = _display_value(literature.publication_year)
        lines.extend(("", f"[{column_number}] {literature.title} ({year})"))
        if not literature_outcomes.outcomes:
            lines.append("登録済みOutcomeはありません。")
            continue
        for outcome_number, outcome in enumerate(
            literature_outcomes.outcomes, start=1
        ):
            lines.extend(
                (
                    "",
                    f"Outcome {outcome_number}",
                    f"entity verification: {outcome.entity_verification}",
                    f"Evidence: {outcome.evidence_count}",
                    (
                        "user_verified Evidence: "
                        f"{outcome.user_verified_evidence_count}/"
                        f"{outcome.evidence_count}"
                    ),
                )
            )
            for field in outcome.fields:
                _append_profile_field(
                    lines,
                    field.field_key,
                    field.value,
                    indent="",
                )
            lines.append("Results:")
            if not outcome.results:
                lines.append("  登録済みResultはありません。")
            for result_number, result in enumerate(outcome.results, start=1):
                lines.extend(
                    (
                        f"  Result {result_number}",
                        f"    entity verification: {result.entity_verification}",
                        f"    Evidence: {result.evidence_count}",
                        (
                            "    user_verified Evidence: "
                            f"{result.user_verified_evidence_count}/"
                            f"{result.evidence_count}"
                        ),
                    )
                )
                for field in result.fields:
                    _append_profile_field(
                        lines,
                        field.field_key,
                        field.value,
                        indent="    ",
                    )
    return "\n".join(lines)


def _load_last_search_choices(
    connection: sqlite3.Connection,
    search_result_ids: tuple[int, ...],
) -> tuple[Literature, ...]:
    choices: list[Literature] = []
    for literature_id in search_result_ids:
        literature = get_literature(connection, literature_id)
        if literature is None:
            raise ValueError(
                "直前の検索結果に、現在は存在しないLiteratureが含まれています。"
            )
        choices.append(literature)
    return tuple(choices)


def _run_comparison_management(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
    search_result_ids: Optional[tuple[int, ...]],
) -> bool:
    """Select Literature and display a read-only comparison."""
    while True:
        output_func(_COMPARISON_MENU)
        try:
            choice = _read_input(input_func, _MENU_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if choice == "0":
            return False
        if choice not in {"1", "2"}:
            output_func(_INVALID_COMPARISON_MENU_MESSAGE)
            continue

        try:
            if choice == "1":
                if not search_result_ids:
                    output_func(_MISSING_COMPARISON_SEARCH_RESULTS_MESSAGE)
                    continue
                search_choices = _load_last_search_choices(
                    connection, search_result_ids
                )
                output_func("直前の検索結果:")
                for number, literature in enumerate(search_choices, start=1):
                    output_func(
                        "\n".join(
                            (
                                f"選択番号: {number}",
                                f"Title: {literature.title}",
                                f"Year: {_display_value(literature.publication_year)}",
                            )
                        )
                    )
                    output_func(_RECORD_SEPARATOR)
                try:
                    raw_selection = _read_input(
                        input_func,
                        "選択番号（all またはカンマ区切り）: ",
                    )
                except (EOFError, KeyboardInterrupt):
                    return True
                literature_ids = select_search_result_ids(
                    search_result_ids, raw_selection
                )
            else:
                try:
                    raw_ids = _read_input(
                        input_func,
                        "Literature ID（カンマ区切り）: ",
                    )
                except (EOFError, KeyboardInterrupt):
                    return True
                literature_ids = parse_literature_id_input(raw_ids)

            matrix = build_comparison_matrix(connection, literature_ids)
        except (ComparisonMatrixError, ValueError) as error:
            output_func(f"比較エラー: {error}")
            continue
        except sqlite3.Error:
            output_func(_DATABASE_ERROR_MESSAGE)
            raise

        output_func(_format_comparison_matrix(matrix))


def _parse_json_file_path(raw_path: str) -> Path:
    """Parse one shell-style path without executing any shell command."""
    try:
        parts = shlex.split(raw_path, posix=True)
    except ValueError as error:
        raise ValueError(f"JSON file pathを解釈できません: {error}") from error
    if len(parts) != 1 or not parts[0]:
        raise ValueError("JSON file pathを1つ指定してください。")
    return Path(parts[0])


def _format_import_preview(preview: ImportPreview) -> str:
    """Format a researcher-facing Japanese Import Preview."""
    target = preview.target_literature
    similarity = (
        "比較不可"
        if preview.title_similarity is None
        else f"{preview.title_similarity:.3f}"
    )
    count_labels = (
        ("Study", "study"),
        ("Methods", "methods"),
        ("Outcome", "outcomes"),
        ("Result", "results"),
        ("Limitations", "limitations"),
        ("Concepts", "concepts"),
        ("Research Relevance", "research_relevance"),
        ("Evidence", "evidence"),
        ("Fields", "fields"),
        ("Evidence links", "evidence_links"),
    )
    lines = [
        "Import Preview",
        "",
        "Target Literature（明示選択）:",
        f"ID: {target.id}",
        f"title: {_display_value(target.title)}",
        f"DOI: {_display_value(target.doi)}",
        f"PMID: {_display_value(target.pmid)}",
        "",
        "Payload:",
        f"source_document_name: {_display_value(preview.source_document_name)}",
        f"analysis_scope: {preview.analysis_scope}",
        f"title: {_display_value(preview.payload_title)}",
        f"DOI: {_display_value(preview.payload_doi)}",
        f"PMID: {_display_value(preview.payload_pmid)}",
        "",
        "Target check:",
        f"title similarity: {similarity}",
        f"DOI: {_IDENTIFIER_STATE_LABELS[preview.doi_state]}",
        f"PMID: {_IDENTIFIER_STATE_LABELS[preview.pmid_state]}",
        f"保存可否: {'保存不可' if preview.blocked else '確認後に保存可能'}",
        "",
        "保存予定:",
    ]
    lines.extend(
        f"{label}: {preview.planned_counts[key]}" for label, key in count_labels
    )
    lines.extend(("", "重要注意・Warning:"))
    lines.extend(f"- {warning}" for warning in preview.warnings)
    if preview.blocking_reasons:
        lines.extend(("", "保存をBLOCKする理由:"))
        lines.extend(f"- {reason}" for reason in preview.blocking_reasons)

    unavailable_lines: list[str] = []
    for index, (_, states) in enumerate(
        preview.evidence_locator_availability, start=1
    ):
        unavailable = [
            f"{name}={state}" for name, state in states if state != "reported"
        ]
        if unavailable:
            unavailable_lines.append(
                f"- Evidence {index}: {', '.join(unavailable)}"
            )
    if unavailable_lines:
        lines.extend(
            (
                "",
                "Evidence locator availability（import-only metadata）:",
                *unavailable_lines,
            )
        )
    return "\n".join(lines)


def _run_structured_import(
    connection: sqlite3.Connection,
    input_func: Callable[[str], str],
    output_func: Callable[[str], object],
) -> bool:
    """Preview and optionally save one strict ChatGPT Contract v1 JSON file."""
    try:
        raw_literature_id = _read_input(
            input_func, "対象Literature ID（ASCII数字）: "
        )
    except (EOFError, KeyboardInterrupt):
        return True
    try:
        literature_id = _required_positive_ascii_integer(
            raw_literature_id, "Literature ID"
        )
    except ValueError as error:
        output_func(f"入力エラー: {error}")
        return False

    try:
        target = get_literature(connection, literature_id)
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise
    if target is None:
        output_func("対象Literatureが見つかりません。")
        return False

    try:
        raw_path = _read_input(input_func, "JSON file path: ")
    except (EOFError, KeyboardInterrupt):
        return True
    try:
        json_path = _parse_json_file_path(raw_path)
        json_text = json_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        output_func(f"JSON file読込エラー: {error}")
        return False

    try:
        payload = parse_structured_import(json_text)
        preview = build_import_preview(connection, literature_id, payload)
    except StructuredImportValidationError as error:
        output_func(f"JSON validation error: {error}")
        return False
    except ValueError as error:
        output_func(f"Import Preview error: {error}")
        return False
    except sqlite3.Error:
        output_func(_DATABASE_ERROR_MESSAGE)
        raise

    output_func(_format_import_preview(preview))
    if preview.blocked:
        output_func("保存不可のため確認menuへ進みません。")
        return False

    while True:
        output_func(_STRUCTURED_IMPORT_CONFIRMATION_MENU)
        try:
            choice = _read_input(input_func, _MENU_PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if choice == "0":
            output_func("構造化JSONを保存せず戻ります。")
            return False
        if choice != "1":
            output_func(_INVALID_CONFIRMATION_MESSAGE)
            continue
        try:
            result = save_structured_import(
                connection, preview, confirmed=True
            )
        except (StructuredImportValidationError, ValueError) as error:
            output_func(f"構造化JSON取込エラー: {error}")
            return False
        except sqlite3.Error:
            output_func(_DATABASE_ERROR_MESSAGE)
            raise
        output_func("構造化JSONを保存しました。")
        output_func(f"対象Literature ID: {result.literature_id}")
        return False


def run_cli(
    connection: sqlite3.Connection,
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], object] = print,
    export_directory: object = "exports",
    backup_directory: object = "backups",
    project_root: object | None = None,
    pdf_opener: Optional[Callable[..., PdfOpenResult]] = None,
) -> None:
    """Run the interactive menu using an existing SQLite connection."""
    navigation_project_root = project_root
    if navigation_project_root is None:
        try:
            export_path = Path(export_directory).expanduser()
        except (TypeError, ValueError, RuntimeError):
            pass
        else:
            if export_path.is_absolute():
                navigation_project_root = export_path.parent
    last_search_result_ids: Optional[tuple[int, ...]] = None
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
        if choice not in {
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "13",
        }:
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
        elif choice == "2":
            search_outcome = _run_search(
                connection,
                input_func,
                output_func,
            )
            if search_outcome is True:
                output_func(_EXIT_MESSAGE)
                return None
            if isinstance(search_outcome, tuple):
                last_search_result_ids = search_outcome
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
        elif choice == "7" and _run_usage_history_management(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "8" and _run_literature_detail(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "9" and _run_csv_export(
            connection,
            input_func,
            output_func,
            export_directory,
            last_search_result_ids,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "10":
            _run_database_backup(
                connection,
                output_func,
                backup_directory,
            )
        elif choice == "11" and _run_structured_import(
            connection,
            input_func,
            output_func,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "12" and _run_evidence_management(
            connection,
            input_func,
            output_func,
            project_root=navigation_project_root,
            pdf_opener=pdf_opener,
        ):
            output_func(_EXIT_MESSAGE)
            return None
        elif choice == "13" and _run_comparison_management(
            connection,
            input_func,
            output_func,
            last_search_result_ids,
        ):
            output_func(_EXIT_MESSAGE)
            return None
