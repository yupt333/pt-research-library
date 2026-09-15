"""Tests for the Step 8 interactive CLI."""

import codecs
import copy
import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import src.cli as cli_module
import src.repository as repository_module
from src.cli import run_cli
from src.database import connect_database, initialize_database
from src.duplicates import DuplicateCandidate, find_duplicate_candidates
from src.models import Literature, Tag, UsageHistory
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
from src.structured_repository import (
    attach_evidence_to_entity,
    attach_evidence_to_field,
    create_evidence_reference,
    create_structured_entity,
    create_structured_field,
    get_evidence_reference,
    get_structured_entity,
    get_structured_field,
    list_evidence_for_entity,
    list_evidence_for_field,
    update_evidence_reference,
)


_SEARCH_FIELDS = (
    "keyword",
    "year",
    "tag",
    "publication_type",
    "verification_status",
    "adoption_status",
    "ai_summary_status",
    "rating",
    "usage_type",
)

_REGISTRATION_FIELDS = (
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


class InputFeeder:
    """Return queued inputs while recording prompts and supporting interrupts."""

    def __init__(self, actions: list[object]) -> None:
        self.actions = iter(actions)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        try:
            action = next(self.actions)
        except StopIteration as error:
            raise EOFError("test input exhausted") from error
        if isinstance(action, BaseException):
            raise action
        if not isinstance(action, str):
            raise TypeError("test input actions must be strings or exceptions")
        return action


class TrackingConnection(sqlite3.Connection):
    """Record lifecycle methods that the CLI must not call."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class FailingBackupConnection(TrackingConnection):
    """Fail source backup while recording retries and lifecycle calls."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.backup_calls = 0
        self.backup_error = sqlite3.OperationalError(
            "forced CLI backup failure"
        )

    def backup(
        self,
        target: sqlite3.Connection,
        *,
        pages: int = -1,
        progress=None,
        name: str = "main",
        sleep: float = 0.250,
    ) -> None:
        self.backup_calls += 1
        raise self.backup_error


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.database_path = self.directory / "cli.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    def run_with_actions(
        self,
        actions: list[object],
        *,
        connection: sqlite3.Connection | None = None,
        export_directory: object | None = None,
        backup_directory: object | None = None,
        project_root: object | None = None,
        pdf_opener: object | None = None,
    ) -> tuple[object, InputFeeder, list[str]]:
        feeder = InputFeeder(actions)
        outputs: list[str] = []
        options = {}
        if pdf_opener is not None:
            options["pdf_opener"] = pdf_opener
        result = run_cli(
            self.connection if connection is None else connection,
            input_func=feeder,
            output_func=outputs.append,
            export_directory=(
                self.directory
                if export_directory is None
                else export_directory
            ),
            backup_directory=(
                self.directory
                if backup_directory is None
                else backup_directory
            ),
            project_root=(
                self.directory if project_root is None else project_root
            ),
            **options,
        )
        return result, feeder, outputs

    @staticmethod
    def search_actions(**conditions: str) -> list[str]:
        values = {field: "" for field in _SEARCH_FIELDS}
        values.update(conditions)
        return ["2", *(values[field] for field in _SEARCH_FIELDS), "0"]

    @staticmethod
    def registration_values(**fields: str) -> list[str]:
        values = {field: "" for field in _REGISTRATION_FIELDS}
        values.update(fields)
        return [values[field] for field in _REGISTRATION_FIELDS]

    @classmethod
    def registration_actions(
        cls,
        *,
        confirmation: str = "1",
        final_menu_choice: str = "0",
        **fields: str,
    ) -> list[str]:
        return [
            "3",
            *cls.registration_values(**fields),
            confirmation,
            final_menu_choice,
        ]

    @staticmethod
    def edit_actions(
        literature_id: int,
        field_number: int,
        new_value: str,
        *,
        confirmation: str = "1",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "4",
            str(literature_id),
            str(field_number),
            new_value,
            confirmation,
            final_menu_choice,
        ]

    @staticmethod
    def delete_actions(
        literature_id: int,
        *,
        confirmation: str = "1",
        confirmed_id: str | None = None,
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "5",
            str(literature_id),
            confirmation,
            str(literature_id) if confirmed_id is None else confirmed_id,
            final_menu_choice,
        ]

    @staticmethod
    def tag_create_actions(
        name: str,
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "2",
            name,
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def tag_rename_actions(
        tag_id: int | str,
        new_name: str,
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "3",
            str(tag_id),
            new_name,
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def tag_delete_actions(
        tag_id: int | str,
        *,
        confirmation: str = "1",
        confirmed_id: str | None = None,
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "4",
            str(tag_id),
            confirmation,
            str(tag_id) if confirmed_id is None else confirmed_id,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def literature_tag_list_actions(
        literature_id: int | str,
        *,
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "5",
            str(literature_id),
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def tag_attach_actions(
        literature_id: int | str,
        tag_id: int | str,
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "6",
            str(literature_id),
            str(tag_id),
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def tag_detach_actions(
        literature_id: int | str,
        tag_id: int | str,
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "6",
            "7",
            str(literature_id),
            str(tag_id),
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def literature_usage_history_list_actions(
        literature_id: int | str,
        *,
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "7",
            "1",
            str(literature_id),
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def literature_detail_actions(
        literature_id: int | str,
        *,
        final_menu_choice: str = "0",
    ) -> list[str]:
        return ["8", str(literature_id), final_menu_choice]

    @staticmethod
    def usage_history_create_actions(
        literature_id: int | str,
        usage_type: str,
        project_name: str = "",
        usage_note: str = "",
        used_at: str = "",
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "7",
            "2",
            str(literature_id),
            usage_type,
            project_name,
            usage_note,
            used_at,
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def usage_history_edit_actions(
        usage_history_id: int | str,
        field_number: int | str,
        new_value: str,
        *,
        confirmation: str = "1",
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "7",
            "3",
            str(usage_history_id),
            str(field_number),
            new_value,
            confirmation,
            final_submenu_choice,
            final_menu_choice,
        ]

    @staticmethod
    def usage_history_delete_actions(
        usage_history_id: int | str,
        *,
        confirmation: str = "1",
        confirmed_id: str | None = None,
        final_submenu_choice: str = "0",
        final_menu_choice: str = "0",
    ) -> list[str]:
        return [
            "7",
            "4",
            str(usage_history_id),
            confirmation,
            (
                str(usage_history_id)
                if confirmed_id is None
                else confirmed_id
            ),
            final_submenu_choice,
            final_menu_choice,
        ]

    def add_record(self, title: str, **values: object) -> int:
        return add_literature(
            self.connection,
            Literature(title=title, **values),
        )

    def write_structured_import_json(
        self,
        *,
        data: dict[str, object] | None = None,
        filename: str = "structured import.json",
    ) -> Path:
        example_path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "examples"
            / "structured_import_v1.example.json"
        )
        payload = (
            json.loads(example_path.read_text(encoding="utf-8"))
            if data is None
            else data
        )
        path = self.directory / filename
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return path

    @staticmethod
    def structured_row_counts(
        connection: sqlite3.Connection,
    ) -> dict[str, int]:
        return {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in (
                "structured_entities",
                "structured_fields",
                "evidence_references",
                "structured_field_evidence",
                "structured_entity_evidence",
            )
        }

    def populate_search_records(self) -> tuple[int, int]:
        matching_id = self.add_record(
            "肩関節 %_\\ CLI検索対象",
            authors="対象著者",
            journal="対象雑誌",
            publication_year=2025,
            doi="DOI:10.1000/Stored Value",
            pmid=" 00123 ",
            publication_type="原著",
            verification_status="確認済み",
            adoption_status="採用",
            ai_summary_status="修正済み",
            rating=5,
        )
        other_id = self.add_record(
            "別の文献",
            authors="別著者",
            journal="別雑誌",
            publication_year=2024,
            publication_type="レビュー",
            verification_status="未確認",
            adoption_status="未判定",
            ai_summary_status="未作成",
            rating=2,
        )
        matching_tag = create_tag(self.connection, "AHD")
        other_tag = create_tag(self.connection, "other")
        attach_tag_to_literature(
            self.connection,
            matching_id,
            matching_tag,
        )
        attach_tag_to_literature(
            self.connection,
            other_id,
            other_tag,
        )
        create_usage_history(
            self.connection,
            matching_id,
            "学会発表",
            project_name="CLI対象プロジェクト",
        )
        create_usage_history(
            self.connection,
            other_id,
            "note",
        )
        return matching_id, other_id

    def table_snapshot(self) -> dict[str, list[tuple[object, ...]]]:
        order_by = {
            "literature": "id",
            "tags": "id",
            "literature_tags": "literature_id, tag_id",
            "usage_history": "id",
        }
        return {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table, ordering in order_by.items()
            }

    @staticmethod
    def table_snapshot_for(
        connection: sqlite3.Connection,
    ) -> dict[str, list[tuple[object, ...]]]:
        order_by = {
            "literature": "id",
            "tags": "id",
            "literature_tags": "literature_id, tag_id",
            "usage_history": "id",
        }
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table, ordering in order_by.items()
        }

    def create_tracking_edit_fixture(
        self,
        suffix: str,
    ) -> tuple[TrackingConnection, int, int]:
        database_path = self.directory / f"edit-exception-{suffix}.db"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        target_id = add_literature(
            connection,
            Literature(
                title="Exception matrix target",
                authors="Before",
                journal="Target journal",
            ),
        )
        other_id = add_literature(
            connection,
            Literature(
                title="Exception matrix other",
                authors="Other author",
            ),
        )
        tag_id = create_tag(connection, "exception-matrix-tag")
        attach_tag_to_literature(connection, target_id, tag_id)
        create_usage_history(
            connection,
            target_id,
            "exception-matrix-use",
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        return connection, target_id, other_id

    def create_tracking_delete_fixture(
        self,
        suffix: str,
    ) -> tuple[TrackingConnection, int, int]:
        database_path = self.directory / f"delete-exception-{suffix}.db"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        target_id = add_literature(
            connection,
            Literature(
                title="Delete exception matrix target",
                authors="Target author",
                pdf_path="/tmp/delete-exception-target.pdf",
            ),
        )
        other_id = add_literature(
            connection,
            Literature(
                title="Delete exception matrix other",
                authors="Other author",
            ),
        )
        shared_tag_id = create_tag(connection, "delete-shared-tag")
        target_tag_id = create_tag(connection, "delete-target-tag")
        attach_tag_to_literature(connection, target_id, shared_tag_id)
        attach_tag_to_literature(connection, target_id, target_tag_id)
        attach_tag_to_literature(connection, other_id, shared_tag_id)
        create_usage_history(connection, target_id, "delete-target-use")
        create_usage_history(connection, other_id, "delete-other-use")
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        return connection, target_id, other_id

    def create_tracking_tag_fixture(
        self,
        suffix: str,
    ) -> tuple[TrackingConnection, int, int, int]:
        database_path = self.directory / f"tag-exception-{suffix}.db"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        literature_id = add_literature(
            connection,
            Literature(title="Tag exception matrix literature"),
        )
        target_tag_id = create_tag(connection, "Shoulder")
        other_tag_id = create_tag(connection, "Ultrasound")
        attach_tag_to_literature(
            connection,
            literature_id,
            target_tag_id,
        )
        create_usage_history(
            connection,
            literature_id,
            "tag-exception-use",
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        return connection, literature_id, target_tag_id, other_tag_id

    def create_tracking_usage_history_fixture(
        self,
        suffix: str,
    ) -> tuple[TrackingConnection, int, int, int, int, int]:
        database_path = self.directory / f"usage-history-{suffix}.db"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        literature_id = add_literature(
            connection,
            Literature(title=f"Usage-history {suffix} target"),
        )
        other_literature_id = add_literature(
            connection,
            Literature(title=f"Usage-history {suffix} other"),
        )
        target_id = create_usage_history(
            connection,
            literature_id,
            "target",
            "Before project",
            "Kept note",
            "2026-08-01",
        )
        same_literature_id = create_usage_history(
            connection,
            literature_id,
            "kept same",
        )
        other_history_id = create_usage_history(
            connection,
            other_literature_id,
            "kept other",
        )
        target_tag_id = create_tag(connection, f"usage-{suffix}-target-tag")
        other_tag_id = create_tag(connection, f"usage-{suffix}-other-tag")
        attach_tag_to_literature(connection, literature_id, target_tag_id)
        attach_tag_to_literature(
            connection,
            other_literature_id,
            other_tag_id,
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        return (
            connection,
            literature_id,
            target_id,
            same_literature_id,
            other_history_id,
            target_tag_id,
        )

    @staticmethod
    def schema_snapshot_for(
        connection: sqlite3.Connection,
    ) -> list[tuple[object, ...]]:
        return [
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                ORDER BY type, name
                """
            ).fetchall()
        ]

    def schema_snapshot(self) -> list[tuple[object, ...]]:
        return self.schema_snapshot_for(self.connection)

    def test_menu_title_options_zero_exit_and_none_return(self) -> None:
        result, feeder, outputs = self.run_with_actions(["0"])

        self.assertIsNone(result)
        self.assertEqual(feeder.prompts, ["選択してください: "])
        self.assertIn("理学療法文献ライブラリ", outputs[0])
        self.assertIn("1. 文献一覧", outputs[0])
        self.assertIn("2. 文献検索", outputs[0])
        self.assertIn("3. 文献登録", outputs[0])
        self.assertIn("4. 文献編集", outputs[0])
        self.assertIn("5. 文献削除", outputs[0])
        self.assertIn("6. タグ管理", outputs[0])
        self.assertIn("7. 使用履歴管理", outputs[0])
        self.assertIn("8. 文献詳細", outputs[0])
        self.assertIn("9. CSV出力", outputs[0])
        self.assertIn("10. SQLiteバックアップ", outputs[0])
        self.assertIn("11. ChatGPT構造化JSON取込", outputs[0])
        self.assertIn("12. Evidence確認・管理", outputs[0])
        self.assertIn("13. 複数文献比較", outputs[0])
        self.assertIn("0. 終了", outputs[0])
        self.assertEqual(outputs[-1], "CLIを終了します。")
        self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_menu_trims_whitespace_around_zero(self) -> None:
        result, _, outputs = self.run_with_actions([" \t0\n "])

        self.assertIsNone(result)
        self.assertEqual(outputs.count("CLIを終了します。"), 1)
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            1,
        )

    def test_invalid_empty_and_many_choices_loop_without_recursion(self) -> None:
        invalid_count = 1200
        actions = ["", "invalid", *(["14"] * invalid_count), "0"]

        _, feeder, outputs = self.run_with_actions(actions)

        error_message = cli_module._INVALID_MENU_MESSAGE
        self.assertEqual(
            outputs.count(error_message),
            invalid_count + 2,
        )
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            invalid_count + 3,
        )
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 3,
        )

    def test_list_and_search_return_to_main_menu(self) -> None:
        actions = ["1", "2", *([""] * len(_SEARCH_FIELDS)), "0"]

        _, feeder, outputs = self.run_with_actions(actions)

        self.assertIn("登録されている文献はありません。", outputs)
        self.assertIn("条件に一致する文献はありません。", outputs)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            3,
        )
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            3,
        )

    def test_menu_eof_and_keyboard_interrupt_exit_normally_once(self) -> None:
        for interruption in (EOFError("end"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                result, _, outputs = self.run_with_actions([interruption])
                self.assertIsNone(result)
                self.assertEqual(outputs.count("CLIを終了します。"), 1)
                self.assertNotIn(repr(interruption), "\n".join(outputs))

    def test_connection_is_only_positional_argument(self) -> None:
        fake_input = InputFeeder(["0"])
        outputs: list[str] = []

        with self.assertRaises(TypeError):
            run_cli(self.connection, fake_input, outputs.append)  # type: ignore[misc]

        self.assertIsNone(
            run_cli(
                self.connection,
                input_func=InputFeeder(["0"]),
                output_func=outputs.append,
            )
        )

    def test_empty_list_uses_existing_repository_function(self) -> None:
        with patch.object(
            cli_module,
            "list_literature",
            wraps=list_literature,
        ) as listed:
            _, _, outputs = self.run_with_actions(["1", "0"])

        listed.assert_called_once_with(self.connection)
        self.assertIn("登録されている文献はありません。", outputs)

    def test_list_displays_all_required_fields_and_preserves_saved_values(
        self,
    ) -> None:
        title = '肩関節, "引用"\n省略しないタイトル'
        authors = "  Author A, Author B  "
        journal = 'Journal "Quoted", Volume'
        # Bypass the write-normalizing repository to model a legacy stored row.
        cursor = self.connection.execute(
            """
            INSERT INTO literature (
                title,
                authors,
                journal,
                publication_year,
                doi,
                pmid,
                verification_status,
                adoption_status,
                rating
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                authors,
                journal,
                2025,
                " DOI:10.1000/Mixed Case ",
                " PMID: 001 23 ",
                "要確認",
                "採用候補",
                4,
            ),
        )
        self.connection.commit()
        literature_id = cursor.lastrowid
        self.assertIsNotNone(literature_id)

        _, _, outputs = self.run_with_actions(["1", "0"])
        displayed = "\n".join(outputs)

        for expected in (
            f"ID: {literature_id}",
            f"title: {title}",
            "publication_year: 2025",
            f"authors: {authors}",
            f"journal: {journal}",
            "DOI:  DOI:10.1000/Mixed Case ",
            "PMID:  PMID: 001 23 ",
            "verification_status: 要確認",
            "adoption_status: 採用候補",
            "rating: 4",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, displayed)

    def test_list_displays_nulls_as_unregistered_not_none(self) -> None:
        self.add_record("NULL表示テスト")

        _, _, outputs = self.run_with_actions(["1", "0"])
        displayed = "\n".join(outputs)

        self.assertIn("authors: 未登録", displayed)
        self.assertIn("publication_year: 未登録", displayed)
        self.assertIn("rating: 未登録", displayed)
        self.assertNotIn("None", displayed)

    def test_display_value_preserves_zero_instead_of_treating_it_as_null(
        self,
    ) -> None:
        self.assertEqual(cli_module._display_value(0), "0")

    def test_list_multiple_records_are_ascending_isolated_and_separated(
        self,
    ) -> None:
        first_id = self.add_record(
            "First full title " + ("A" * 200),
            authors="First-only author",
        )
        second_id = self.add_record(
            "Second title",
            authors="Second-only author",
        )

        _, _, outputs = self.run_with_actions(["1", "0"])
        displayed = "\n".join(outputs)

        self.assertLess(
            displayed.index(f"ID: {first_id}"),
            displayed.index(f"ID: {second_id}"),
        )
        self.assertIn("First full title " + ("A" * 200), displayed)
        self.assertEqual(displayed.count("First-only author"), 1)
        self.assertEqual(displayed.count("Second-only author"), 1)
        self.assertGreaterEqual(outputs.count("-" * 40), 3)

    def test_search_prompts_all_fields_in_order_with_status_values(self) -> None:
        _, feeder, _ = self.run_with_actions(self.search_actions())
        search_prompts = feeder.prompts[1:-1]

        self.assertEqual(len(search_prompts), 9)
        for prompt, field in zip(search_prompts, _SEARCH_FIELDS):
            with self.subTest(field=field):
                self.assertIn("空欄で指定なし", prompt)
        self.assertIn("キーワード", search_prompts[0])
        self.assertIn("year", search_prompts[1])
        self.assertIn("タグ", search_prompts[2])
        self.assertIn("未確認・一部確認・確認済み・要確認", search_prompts[4])
        self.assertIn("未判定・採用候補・採用・除外", search_prompts[5])
        self.assertIn("未作成・未確認・確認済み・修正済み", search_prompts[6])

    def test_all_blank_search_returns_all_records_in_ascending_order(
        self,
    ) -> None:
        first_id = self.add_record("First search result")
        second_id = self.add_record("Second search result")

        _, _, outputs = self.run_with_actions(self.search_actions())
        displayed = "\n".join(outputs)

        self.assertLess(
            displayed.index(f"ID: {first_id}"),
            displayed.index(f"ID: {second_id}"),
        )
        self.assertIn("First search result", displayed)
        self.assertIn("Second search result", displayed)

    def test_each_search_filter_is_passed_to_existing_search_behavior(
        self,
    ) -> None:
        self.populate_search_records()
        cases = (
            {"keyword": "CLI検索対象"},
            {"year": "2025"},
            {"tag": "AHD"},
            {"publication_type": "原著"},
            {"verification_status": "確認済み"},
            {"adoption_status": "採用"},
            {"ai_summary_status": "修正済み"},
            {"rating": "5"},
            {"usage_type": "学会発表"},
        )

        for conditions in cases:
            with self.subTest(conditions=conditions):
                _, _, outputs = self.run_with_actions(
                    self.search_actions(**conditions)
                )
                displayed = "\n".join(outputs)
                self.assertIn("肩関節 %_\\ CLI検索対象", displayed)
                self.assertNotIn("title: 別の文献", displayed)

    def test_search_combines_all_conditions_with_and(self) -> None:
        self.populate_search_records()

        _, _, matching_outputs = self.run_with_actions(
            self.search_actions(
                keyword="CLI検索対象",
                year="2025",
                tag="AHD",
                publication_type="原著",
                verification_status="確認済み",
                adoption_status="採用",
                ai_summary_status="修正済み",
                rating="5",
                usage_type="学会発表",
            )
        )
        _, _, no_match_outputs = self.run_with_actions(
            self.search_actions(
                keyword="CLI検索対象",
                year="2024",
            )
        )

        self.assertIn(
            "title: 肩関節 %_\\ CLI検索対象",
            "\n".join(matching_outputs),
        )
        self.assertIn(
            "条件に一致する文献はありません。",
            no_match_outputs,
        )

    def test_search_handles_literal_special_and_japanese_keywords(self) -> None:
        self.populate_search_records()

        for keyword in ("%_\\", "肩関節"):
            with self.subTest(keyword=keyword):
                _, _, outputs = self.run_with_actions(
                    self.search_actions(keyword=keyword)
                )
                displayed = "\n".join(outputs)
                self.assertIn("title: 肩関節 %_\\ CLI検索対象", displayed)
                self.assertNotIn("title: 別の文献", displayed)

    def test_search_uses_existing_api_and_passes_all_trimmed_conditions(
        self,
    ) -> None:
        with patch.object(
            cli_module,
            "search_literature",
            wraps=search_literature,
        ) as searched:
            self.run_with_actions(
                self.search_actions(
                    keyword="  肩関節  ",
                    year=" 02025 ",
                    tag="  AHD ",
                    publication_type=" 原著 ",
                    verification_status=" 確認済み ",
                    adoption_status=" 採用 ",
                    ai_summary_status=" 修正済み ",
                    rating=" 05 ",
                    usage_type=" 学会発表 ",
                )
            )

        searched.assert_called_once_with(
            self.connection,
            keyword="肩関節",
            year=2025,
            tag="AHD",
            publication_type="原著",
            verification_status="確認済み",
            adoption_status="採用",
            ai_summary_status="修正済み",
            rating=5,
            usage_type="学会発表",
        )

    def test_list_and_search_share_the_same_record_format(self) -> None:
        literature = Literature(
            id=7,
            title="Shared format",
            publication_year=2026,
            authors="Shared Author",
            journal="Shared Journal",
            doi="10.1000/shared",
            pmid="00123",
            verification_status="一部確認",
            adoption_status="採用候補",
            rating=3,
        )
        list_outputs: list[str] = []
        search_outputs: list[str] = []

        with patch.object(
            cli_module,
            "list_literature",
            return_value=[literature],
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(["1", "0"]),
                output_func=list_outputs.append,
            )
        with patch.object(
            cli_module,
            "search_literature",
            return_value=[literature],
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(
                    ["2", *([""] * len(_SEARCH_FIELDS)), "0"]
                ),
                output_func=search_outputs.append,
            )

        formatted = cli_module._format_literature(literature)
        self.assertEqual(list_outputs.count(formatted), 1)
        self.assertEqual(search_outputs.count(formatted), 1)

    def test_invalid_year_formats_stop_search_immediately(self) -> None:
        invalid_values = (
            "+1",
            "-1",
            "1.5",
            "1e3",
            "２０２５",
            "١٢٣٤",
            "year",
        )

        for value in invalid_values:
            with self.subTest(value=value):
                feeder = InputFeeder(self.search_actions(year=value))
                outputs: list[str] = []
                with patch.object(cli_module, "search_literature") as searched:
                    run_cli(
                        self.connection,
                        input_func=feeder,
                        output_func=outputs.append,
                    )
                searched.assert_not_called()
                self.assertTrue(
                    any(
                        "yearはASCII数字だけの整数表記" in item
                        for item in outputs
                    )
                )
                self.assertEqual(
                    feeder.prompts.count("選択してください: "),
                    2,
                )

    def test_invalid_rating_formats_stop_before_search(self) -> None:
        invalid_values = ("1.5", "-1", "５", "١", "rating")

        for value in invalid_values:
            with self.subTest(value=value):
                outputs: list[str] = []
                with patch.object(cli_module, "search_literature") as searched:
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(
                            self.search_actions(rating=value)
                        ),
                        output_func=outputs.append,
                    )
                searched.assert_not_called()
                self.assertTrue(
                    any(
                        "ratingはASCII数字だけの整数表記" in item
                        for item in outputs
                    )
                )

    def test_api_range_errors_are_displayed_and_return_to_menu(self) -> None:
        for field, value in (
            ("year", "1799"),
            ("year", "9999"),
            ("rating", "0"),
            ("rating", "6"),
        ):
            with self.subTest(field=field, value=value):
                _, feeder, outputs = self.run_with_actions(
                    self.search_actions(**{field: value})
                )
                self.assertTrue(
                    any(item.startswith("入力エラー: ") for item in outputs)
                )
                self.assertEqual(
                    feeder.prompts.count("選択してください: "),
                    2,
                )
                self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_invalid_status_values_are_reported_by_search_api(self) -> None:
        for field in (
            "verification_status",
            "adoption_status",
            "ai_summary_status",
        ):
            with self.subTest(field=field):
                _, _, outputs = self.run_with_actions(
                    self.search_actions(**{field: "不正な状態"})
                )
                self.assertTrue(
                    any(
                        field in item and item.startswith("入力エラー: ")
                        for item in outputs
                    )
                )

    def test_blank_and_whitespace_filters_are_passed_as_none(self) -> None:
        with patch.object(
            cli_module,
            "search_literature",
            return_value=[],
        ) as searched:
            self.run_with_actions(
                self.search_actions(
                    keyword=" ",
                    year="\t",
                    tag="\n",
                    publication_type="  ",
                    verification_status="\t ",
                    adoption_status=" ",
                    ai_summary_status="\n",
                    rating=" ",
                    usage_type="\t",
                )
            )

        searched.assert_called_once_with(
            self.connection,
            keyword=None,
            year=None,
            tag=None,
            publication_type=None,
            verification_status=None,
            adoption_status=None,
            ai_summary_status=None,
            rating=None,
            usage_type=None,
        )

    def test_search_input_interruptions_exit_once_without_searching(self) -> None:
        cases = (
            ("keyword EOF", ["2", EOFError("keyword")]),
            ("year interrupt", ["2", "", KeyboardInterrupt()]),
            (
                "status EOF",
                ["2", "", "", "", "", EOFError("status")],
            ),
            (
                "usage interrupt",
                [
                    "2",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    KeyboardInterrupt(),
                ],
            ),
        )

        for case, actions in cases:
            with self.subTest(case=case):
                outputs: list[str] = []
                with patch.object(cli_module, "search_literature") as searched:
                    result = run_cli(
                        self.connection,
                        input_func=InputFeeder(actions),
                        output_func=outputs.append,
                    )
                self.assertIsNone(result)
                searched.assert_not_called()
                self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_input_value_error_from_menu_or_search_is_propagated(self) -> None:
        for source, action_prefix in (
            ("menu", []),
            ("search", ["2"]),
        ):
            with self.subTest(source=source):
                expected = ValueError(f"{source} input failure")
                outputs: list[str] = []
                with patch.object(cli_module, "search_literature") as searched:
                    with self.assertRaises(ValueError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                [*action_prefix, expected]
                            ),
                            output_func=outputs.append,
                        )

                self.assertIs(raised.exception, expected)
                searched.assert_not_called()
                self.assertFalse(
                    any(item.startswith("入力エラー: ") for item in outputs)
                )
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertNotIn("CLIを終了します。", outputs)

    def test_search_input_sqlite_error_is_not_misclassified(self) -> None:
        expected = sqlite3.OperationalError("search input failure")
        outputs: list[str] = []

        with patch.object(cli_module, "search_literature") as searched:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(["2", "", expected]),
                    output_func=outputs.append,
                )

        self.assertIs(raised.exception, expected)
        searched.assert_not_called()
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertFalse(
            any(item.startswith("入力エラー: ") for item in outputs)
        )
        self.assertNotIn("CLIを終了します。", outputs)

    def test_menu_output_exceptions_are_propagated_without_extra_output(
        self,
    ) -> None:
        for expected in (
            ValueError("menu output failure"),
            EOFError("menu output EOF"),
            KeyboardInterrupt(),
            RuntimeError("menu output runtime failure"),
        ):
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if len(outputs) == 1:
                        raise expected

                with self.assertRaises(type(expected)) as raised:
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(["0"]),
                        output_func=output_func,
                    )

                self.assertIs(raised.exception, expected)
                self.assertEqual(outputs, [cli_module._MAIN_MENU])

    def test_exit_message_output_interruptions_are_propagated(self) -> None:
        for expected in (
            EOFError("exit output EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "CLIを終了します。":
                        raise expected

                with self.assertRaises(type(expected)) as raised:
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(["0"]),
                        output_func=output_func,
                    )

                self.assertIs(raised.exception, expected)
                self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_list_output_sqlite_error_is_not_misclassified(self) -> None:
        expected = sqlite3.OperationalError("list output failure")
        outputs: list[str] = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == "登録されている文献はありません。":
                raise expected

        with self.assertRaises(sqlite3.OperationalError) as raised:
            run_cli(
                self.connection,
                input_func=InputFeeder(["1"]),
                output_func=output_func,
            )

        self.assertIs(raised.exception, expected)
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertNotIn("CLIを終了します。", outputs)

    def test_list_non_database_exceptions_are_propagated(self) -> None:
        for expected in (
            ValueError("list value failure"),
            EOFError("list EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []
                with patch.object(
                    cli_module,
                    "list_literature",
                    side_effect=expected,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(["1"]),
                            output_func=outputs.append,
                        )

                self.assertIs(raised.exception, expected)
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertFalse(
                    any(item.startswith("入力エラー: ") for item in outputs)
                )
                self.assertNotIn("CLIを終了します。", outputs)

    def test_search_interruptions_from_search_function_are_propagated(
        self,
    ) -> None:
        for expected in (
            EOFError("search EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []
                with patch.object(
                    cli_module,
                    "search_literature",
                    side_effect=expected,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                ["2", *([""] * len(_SEARCH_FIELDS))]
                            ),
                            output_func=outputs.append,
                        )

                self.assertIs(raised.exception, expected)
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertFalse(
                    any(item.startswith("入力エラー: ") for item in outputs)
                )
                self.assertNotIn("CLIを終了します。", outputs)

    def test_list_database_error_is_announced_and_same_error_is_raised(
        self,
    ) -> None:
        expected = sqlite3.OperationalError("forced list failure")
        outputs: list[str] = []

        with patch.object(
            cli_module,
            "list_literature",
            side_effect=expected,
        ):
            with self.assertRaises(sqlite3.OperationalError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(["1"]),
                    output_func=outputs.append,
                )

        self.assertIs(raised.exception, expected)
        self.assertEqual(
            outputs.count("データベースエラーが発生しました。"),
            1,
        )

    def test_search_database_error_is_announced_and_same_error_is_raised(
        self,
    ) -> None:
        expected = sqlite3.DatabaseError("forced search failure")
        outputs: list[str] = []

        with patch.object(
            cli_module,
            "search_literature",
            side_effect=expected,
        ):
            with self.assertRaises(sqlite3.DatabaseError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(
                        ["2", *([""] * len(_SEARCH_FIELDS))]
                    ),
                    output_func=outputs.append,
                )

        self.assertIs(raised.exception, expected)
        self.assertEqual(
            outputs.count("データベースエラーが発生しました。"),
            1,
        )

    def test_unexpected_exception_is_not_converted_or_suppressed(self) -> None:
        expected = RuntimeError("unexpected failure")
        outputs: list[str] = []

        with patch.object(
            cli_module,
            "list_literature",
            side_effect=expected,
        ):
            with self.assertRaises(RuntimeError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(["1"]),
                    output_func=outputs.append,
                )

        self.assertIs(raised.exception, expected)
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertFalse(
            any(item.startswith("入力エラー: ") for item in outputs)
        )

    def test_input_function_value_error_is_not_converted_or_suppressed(
        self,
    ) -> None:
        expected = ValueError("unexpected input failure")
        outputs: list[str] = []

        with self.assertRaises(ValueError) as raised:
            run_cli(
                self.connection,
                input_func=InputFeeder(["2", expected]),
                output_func=outputs.append,
            )

        self.assertIs(raised.exception, expected)
        self.assertFalse(
            any(item.startswith("入力エラー: ") for item in outputs)
        )

    def test_registration_prompts_all_fields_in_required_order(self) -> None:
        _, feeder, outputs = self.run_with_actions(
            self.registration_actions(
                title="Prompt order",
                confirmation="0",
            )
        )

        registration_prompts = feeder.prompts[1:29]
        self.assertEqual(len(registration_prompts), 28)
        for field_name, prompt in zip(
            _REGISTRATION_FIELDS,
            registration_prompts,
        ):
            with self.subTest(field_name=field_name):
                self.assertIn(field_name, prompt)
        for prompt in registration_prompts[1:]:
            self.assertIn("空欄", prompt)
        self.assertIn(
            "未作成・未確認・確認済み・修正済み",
            registration_prompts[16],
        )
        self.assertIn(
            "未確認・一部確認・確認済み・要確認",
            registration_prompts[24],
        )
        self.assertIn(
            "未判定・採用候補・採用・除外",
            registration_prompts[25],
        )
        confirmation_menu = "\n".join(outputs)
        self.assertIn("1. この内容で登録する", confirmation_menu)
        self.assertIn("0. 登録を中止する", confirmation_menu)

    def test_blank_title_stops_before_model_duplicate_check_or_add(self) -> None:
        before = self.table_snapshot()
        with (
            patch.object(cli_module, "Literature") as literature_class,
            patch.object(
                cli_module,
                "find_duplicate_candidates",
            ) as duplicate_check,
            patch.object(cli_module, "add_literature") as added,
        ):
            _, feeder, outputs = self.run_with_actions(
                ["3", " \t\n ", "0"]
            )

        literature_class.assert_not_called()
        duplicate_check.assert_not_called()
        added.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            feeder.prompts,
            [
                "選択してください: ",
                "title（必須）: ",
                "選択してください: ",
            ],
        )
        self.assertTrue(
            any(
                item.startswith("入力エラー: ")
                and "タイトルは必須" in item
                for item in outputs
            )
        )

    def test_minimal_registration_saves_defaults_and_no_related_rows(
        self,
    ) -> None:
        result, feeder, outputs = self.run_with_actions(
            self.registration_actions(title="Minimal registration")
        )

        self.assertIsNone(result)
        records = list_literature(self.connection)
        self.assertEqual(len(records), 1)
        stored = records[0]
        self.assertEqual(stored.title, "Minimal registration")
        for field_name in (
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
            "general_note",
            "key_findings",
            "methods_note",
            "clinical_note",
            "limitation_note",
            "relevance_note",
            "evidence_level",
            "exclusion_reason",
            "rating",
        ):
            with self.subTest(field_name=field_name):
                self.assertIsNone(getattr(stored, field_name))
        self.assertEqual(stored.ai_summary_status, "未作成")
        self.assertEqual(stored.verification_status, "未確認")
        self.assertEqual(stored.adoption_status, "未判定")
        self.assertIsNotNone(stored.created_at)
        self.assertIsNotNone(stored.updated_at)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature_tags"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history"
            ).fetchone()[0],
            0,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertIn("重複候補はありません。", outputs)
        self.assertIn("文献を登録しました。", outputs)
        self.assertIn(f"ID: {stored.id}", outputs)
        self.assertIn("title: Minimal registration", outputs)
        self.assertEqual(feeder.prompts.count("選択してください: "), 3)

    def test_full_registration_preserves_fields_and_normalizes_on_save(
        self,
    ) -> None:
        fields = {
            "title": '  肩関節 "Full", Study  ',
            "authors": '  Author A, "Author B"\nAuthor C  ',
            "journal": "  Journal 内部  空白  ",
            "publication_year": " 2025 ",
            "volume": " 12 ",
            "issue": " 3 ",
            "pages": " 101-112 ",
            "doi": " DOI:10.ABC/Example ",
            "pmid": " PMID: 001 23 ",
            "url": " https://example.test/full ",
            "language": " 日本語 / English ",
            "publication_type": " 原著 ",
            "abstract": '  Abstract, "quoted"\nsecond line  ',
            "pdf_path": " /tmp/full literature.pdf ",
            "personal_summary": " 自分の要約 ",
            "ai_summary": " AI要約\n未確認の本文 ",
            "ai_summary_status": " 修正済み ",
            "general_note": " 一般メモ ",
            "key_findings": " 主要な結果 ",
            "methods_note": " 方法, note ",
            "clinical_note": " 臨床的解釈 ",
            "limitation_note": " 限界 ",
            "relevance_note": " 研究との関連 ",
            "evidence_level": " Level II ",
            "verification_status": " 一部確認 ",
            "adoption_status": " 採用候補 ",
            "exclusion_reason": " 除外理由も保持 ",
            "rating": " 05 ",
        }
        captured: list[
            tuple[Literature, dict[str, object], dict[str, object]]
        ] = []

        def tracked_add(
            connection: sqlite3.Connection,
            literature: Literature,
        ) -> int:
            before = vars(literature).copy()
            literature_id = add_literature(connection, literature)
            captured.append((literature, before, vars(literature).copy()))
            return literature_id

        with (
            patch.object(
                cli_module,
                "find_duplicate_candidates",
                wraps=find_duplicate_candidates,
            ) as duplicate_check,
            patch.object(
                cli_module,
                "add_literature",
                side_effect=tracked_add,
            ) as added,
        ):
            _, _, outputs = self.run_with_actions(
                self.registration_actions(**fields)
            )

        self.assertEqual(duplicate_check.call_count, 1)
        duplicate_check.assert_called_once_with(
            self.connection,
            title='肩関節 "Full", Study',
            doi="DOI:10.ABC/Example",
            pmid="PMID: 001 23",
        )
        added.assert_called_once()
        literature, before_add, after_add = captured[0]
        self.assertEqual(before_add, after_add)
        self.assertEqual(literature.doi, "DOI:10.ABC/Example")
        self.assertEqual(literature.pmid, "PMID: 001 23")

        records = list_literature(self.connection)
        self.assertEqual(len(records), 1)
        stored = records[0]
        expected_values = {
            "title": '肩関節 "Full", Study',
            "authors": 'Author A, "Author B"\nAuthor C',
            "journal": "Journal 内部  空白",
            "publication_year": 2025,
            "volume": "12",
            "issue": "3",
            "pages": "101-112",
            "doi": "10.abc/example",
            "pmid": "00123",
            "url": "https://example.test/full",
            "language": "日本語 / English",
            "publication_type": "原著",
            "abstract": 'Abstract, "quoted"\nsecond line',
            "pdf_path": "/tmp/full literature.pdf",
            "personal_summary": "自分の要約",
            "ai_summary": "AI要約\n未確認の本文",
            "ai_summary_status": "修正済み",
            "general_note": "一般メモ",
            "key_findings": "主要な結果",
            "methods_note": "方法, note",
            "clinical_note": "臨床的解釈",
            "limitation_note": "限界",
            "relevance_note": "研究との関連",
            "evidence_level": "Level II",
            "verification_status": "一部確認",
            "adoption_status": "採用候補",
            "exclusion_reason": "除外理由も保持",
            "rating": 5,
        }
        for field_name, expected in expected_values.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(getattr(stored, field_name), expected)
        displayed = "\n".join(outputs)
        for expected in (
            'title: 肩関節 "Full", Study',
            'authors: Author A, "Author B"\nAuthor C',
            "DOI:10.ABC/Example",
            "PMID: 001 23",
            "https://example.test/full",
            "/tmp/full literature.pdf",
            'Abstract, "quoted"\nsecond line',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, displayed)
        self.assertIn(
            "DOIとPMIDは登録時に標準形式へ正規化されます。",
            outputs,
        )

    def test_registration_formatter_has_all_fields_in_order_and_is_read_only(
        self,
    ) -> None:
        values = {
            field_name: f"value-{field_name}"
            for field_name in _REGISTRATION_FIELDS
            if field_name
            not in {
                "publication_year",
                "rating",
                "ai_summary_status",
                "verification_status",
                "adoption_status",
            }
        }
        literature = Literature(
            **values,
            publication_year=0,
            rating=1,
            ai_summary_status="未確認",
            verification_status="要確認",
            adoption_status="除外",
        )
        before = vars(literature).copy()

        formatted = cli_module._format_registration_literature(literature)

        labels = [
            line.split(": ", 1)[0]
            for line in formatted.splitlines()
        ]
        self.assertEqual(labels, list(_REGISTRATION_FIELDS))
        self.assertIn("publication_year: 0", formatted)
        self.assertIn("rating: 1", formatted)
        self.assertEqual(vars(literature), before)

        null_literature = Literature(title="Null formatter")
        null_formatted = cli_module._format_registration_literature(
            null_literature
        )
        self.assertIn("authors: 未登録", null_formatted)
        self.assertNotIn("authors: None", null_formatted)

    def test_registration_does_not_nfkc_or_transform_text_fields(self) -> None:
        title = "ＳＨＯＵＬＤＥＲ： Study"
        abstract = "Line 1,\nLine 2  keeps  spaces"

        _, _, outputs = self.run_with_actions(
            self.registration_actions(
                title=f"  {title}  ",
                abstract=f"  {abstract}  ",
            )
        )

        stored = list_literature(self.connection)[0]
        self.assertEqual(stored.title, title)
        self.assertEqual(stored.abstract, abstract)
        displayed = "\n".join(outputs)
        self.assertIn(f"title: {title}", displayed)
        self.assertIn(f"abstract: {abstract}", displayed)

    def test_punctuation_only_title_uses_duplicate_api_value_error(self) -> None:
        before = self.table_snapshot()
        with patch.object(cli_module, "add_literature") as added:
            _, _, outputs = self.run_with_actions(
                [
                    "3",
                    *self.registration_values(title="：—、。"),
                    "0",
                ]
            )

        added.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertTrue(
            any(
                item.startswith("文献登録エラー: ")
                and "title" in item
                for item in outputs
            )
        )

    def test_ai_summary_status_defaults_and_explicit_values(self) -> None:
        cases = (
            ("", "", "未作成"),
            ("AI summary", "", "未確認"),
            ("AI summary", "確認済み", "確認済み"),
            ("AI summary", "修正済み", "修正済み"),
        )

        for index, (summary, status, expected) in enumerate(cases):
            with self.subTest(summary=summary, status=status):
                title = f"AI status {index}"
                self.run_with_actions(
                    self.registration_actions(
                        title=title,
                        ai_summary=summary,
                        ai_summary_status=status,
                    )
                )
                stored = list_literature(self.connection)[-1]
                self.assertEqual(stored.title, title)
                self.assertEqual(stored.ai_summary_status, expected)

    def test_invalid_status_is_repository_value_error_not_sqlite_error(
        self,
    ) -> None:
        for field_name in (
            "ai_summary_status",
            "verification_status",
            "adoption_status",
        ):
            with self.subTest(field_name=field_name):
                before = self.table_snapshot()
                _, _, outputs = self.run_with_actions(
                    self.registration_actions(
                        title=f"Invalid status {field_name}",
                        ai_summary="Manual AI text",
                        **{field_name: "不正状態"},
                    )
                )

                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("文献登録エラー: ")
                        and field_name in item
                        for item in outputs
                    )
                )
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertFalse(self.connection.in_transaction)

    def test_registration_integer_format_errors_stop_before_model_and_apis(
        self,
    ) -> None:
        cases = {
            "publication_year": (
                "+1",
                "-1",
                "1.5",
                "1e3",
                "２０２５",
                "١٢٣٤",
                "year",
            ),
            "rating": (
                "+1",
                "-1",
                "1.5",
                "1e3",
                "５",
                "١",
                "rating",
            ),
        }

        for field_name, invalid_values in cases.items():
            for index, invalid_value in enumerate(invalid_values):
                with self.subTest(
                    field_name=field_name,
                    invalid_value=invalid_value,
                ):
                    values = self.registration_values(
                        title=f"Invalid format {field_name} {index}",
                        **{field_name: invalid_value},
                    )
                    before = self.table_snapshot()
                    with (
                        patch.object(cli_module, "Literature") as model,
                        patch.object(
                            cli_module,
                            "find_duplicate_candidates",
                        ) as duplicate_check,
                        patch.object(
                            cli_module,
                            "add_literature",
                        ) as added,
                    ):
                        _, _, outputs = self.run_with_actions(
                            ["3", *values, "0"]
                        )

                    model.assert_not_called()
                    duplicate_check.assert_not_called()
                    added.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertTrue(
                        any(
                            item.startswith("入力エラー: ")
                            and field_name in item
                            for item in outputs
                        )
                    )

    def test_registration_integer_ascii_forms_and_model_rating_bounds(
        self,
    ) -> None:
        values = self.registration_values(
            title="ASCII conversion",
            publication_year="05",
            rating="05",
        )
        converted = cli_module._prepare_registration_values(
            dict(zip(_REGISTRATION_FIELDS, values))
        )
        self.assertEqual(converted["publication_year"], 5)
        self.assertEqual(converted["rating"], 5)

        for rating in ("0", "6"):
            with self.subTest(rating=rating):
                before = self.table_snapshot()
                with (
                    patch.object(
                        cli_module,
                        "find_duplicate_candidates",
                    ) as duplicate_check,
                    patch.object(cli_module, "add_literature") as added,
                ):
                    _, _, outputs = self.run_with_actions(
                        [
                            "3",
                            *self.registration_values(
                                title=f"Invalid rating {rating}",
                                rating=rating,
                            ),
                            "0",
                        ]
                    )
                duplicate_check.assert_not_called()
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("文献登録エラー: ")
                        and "rating" in item
                        for item in outputs
                    )
                )

    def test_unexpected_model_exceptions_are_not_registration_errors(
        self,
    ) -> None:
        for expected in (
            TypeError("model type failure"),
            RuntimeError("model runtime failure"),
        ):
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []
                before = self.table_snapshot()
                with (
                    patch.object(
                        cli_module,
                        "Literature",
                        side_effect=expected,
                    ),
                    patch.object(
                        cli_module,
                        "find_duplicate_candidates",
                    ) as duplicate_check,
                    patch.object(cli_module, "add_literature") as added,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                [
                                    "3",
                                    *self.registration_values(
                                        title="Unexpected model exception"
                                    ),
                                ]
                            ),
                            output_func=outputs.append,
                        )
                self.assertIs(raised.exception, expected)
                duplicate_check.assert_not_called()
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertFalse(
                    any(
                        item.startswith("文献登録エラー: ")
                        for item in outputs
                    )
                )

    def test_publication_year_repository_boundaries(self) -> None:
        fixed_today = date(2026, 12, 31)

        class FixedDate(date):
            @classmethod
            def today(cls) -> date:
                return fixed_today

        with patch.object(repository_module, "date", FixedDate):
            for publication_year in ("1800", "2027"):
                with self.subTest(publication_year=publication_year):
                    before_count = len(list_literature(self.connection))
                    self.run_with_actions(
                        self.registration_actions(
                            title=f"Valid year {publication_year}",
                            publication_year=publication_year,
                        )
                    )
                    self.assertEqual(
                        len(list_literature(self.connection)),
                        before_count + 1,
                    )

            for publication_year in ("1799", "2028"):
                with self.subTest(publication_year=publication_year):
                    before = self.table_snapshot()
                    _, _, outputs = self.run_with_actions(
                        self.registration_actions(
                            title=f"Invalid year {publication_year}",
                            publication_year=publication_year,
                        )
                    )
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertTrue(
                        any(
                            item.startswith("文献登録エラー: ")
                            and "publication_year" in item
                            for item in outputs
                        )
                    )

    def test_duplicate_candidates_are_displayed_in_api_order_then_cancelled(
        self,
    ) -> None:
        title_only_id = self.add_record(
            "Shared duplicate title",
            publication_year=2025,
        )
        pmid_id = self.add_record(
            "Distinct PMID title",
            publication_year=2024,
            pmid="00123",
        )
        doi_id = self.add_record(
            "Shared duplicate title",
            publication_year=2023,
            doi="10.1000/shared",
        )
        existing_before = {
            literature_id: get_literature(self.connection, literature_id)
            for literature_id in (title_only_id, pmid_id, doi_id)
        }
        expected_candidates = find_duplicate_candidates(
            self.connection,
            title="Shared duplicate title",
            doi="DOI:10.1000/SHARED",
            pmid="PMID: 001 23",
        )
        before = self.table_snapshot()

        _, _, outputs = self.run_with_actions(
            self.registration_actions(
                title="Shared duplicate title",
                doi="DOI:10.1000/SHARED",
                pmid="PMID: 001 23",
                confirmation="0",
            )
        )

        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("警告: 重複候補があります。", outputs)
        self.assertIn(
            "候補は自動統合されず、既存文献も変更されません。",
            outputs,
        )
        expected_ids = [
            candidate.literature.id for candidate in expected_candidates
        ]
        self.assertNotEqual(expected_ids, sorted(expected_ids))
        self.assertTrue(
            any(
                len(candidate.match_reasons) > 1
                for candidate in expected_candidates
            )
        )
        candidate_blocks = [
            item for item in outputs if item.startswith("既存文献ID: ")
        ]
        displayed_ids = [
            int(block.splitlines()[0].split(": ", 1)[1])
            for block in candidate_blocks
        ]
        self.assertEqual(displayed_ids, expected_ids)
        for candidate, block in zip(
            expected_candidates,
            candidate_blocks,
            strict=True,
        ):
            with self.subTest(literature_id=candidate.literature.id):
                literature = candidate.literature
                self.assertIn(f"title: {literature.title}", block)
                self.assertIn(
                    "publication_year: "
                    f"{cli_module._display_value(literature.publication_year)}",
                    block,
                )
                self.assertIn(
                    f"DOI: {cli_module._display_value(literature.doi)}",
                    block,
                )
                self.assertIn(
                    f"PMID: {cli_module._display_value(literature.pmid)}",
                    block,
                )
                self.assertIn(
                    f"title_similarity: {candidate.title_similarity}",
                    block,
                )
                expected_reasons = "、".join(
                    cli_module._DUPLICATE_REASON_LABELS[reason]
                    for reason in candidate.match_reasons
                )
                self.assertIn(f"一致理由: {expected_reasons}", block)
        self.assertIn("文献登録を中止しました。", outputs)
        for literature_id, expected in existing_before.items():
            self.assertEqual(
                get_literature(self.connection, literature_id),
                expected,
            )

    def test_duplicate_candidate_can_be_registered_as_separate_record(
        self,
    ) -> None:
        existing_id = self.add_record(
            "Duplicate continue",
            authors="Existing author",
            doi="10.1000/continue",
            pmid="00123",
        )
        tag_id = create_tag(self.connection, "existing-duplicate-tag")
        attach_tag_to_literature(self.connection, existing_id, tag_id)
        usage_id = create_usage_history(
            self.connection,
            existing_id,
            "existing-use",
        )
        existing_before = get_literature(self.connection, existing_id)

        _, _, outputs = self.run_with_actions(
            self.registration_actions(
                title="Duplicate continue",
                authors="New author",
                doi="DOI:10.1000/CONTINUE",
                pmid="PMID: 001 23",
            )
        )

        records = list_literature(self.connection)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], existing_before)
        self.assertNotEqual(records[1].id, existing_id)
        self.assertEqual(records[1].authors, "New author")
        self.assertEqual(records[1].doi, "10.1000/continue")
        self.assertEqual(records[1].pmid, "00123")
        self.assertIn("警告: 重複候補があります。", outputs)
        self.assertIn("文献を登録しました。", outputs)
        self.assertEqual(
            [
                tuple(row)
                for row in self.connection.execute(
                    "SELECT id, name FROM tags"
                ).fetchall()
            ],
            [(tag_id, "existing-duplicate-tag")],
        )
        self.assertEqual(
            [
                tuple(row)
                for row in self.connection.execute(
                    """
                    SELECT literature_id, tag_id
                    FROM literature_tags
                    """
                ).fetchall()
            ],
            [(existing_id, tag_id)],
        )
        self.assertEqual(
            [
                tuple(row)
                for row in self.connection.execute(
                    """
                    SELECT id, literature_id, usage_type
                    FROM usage_history
                    """
                ).fetchall()
            ],
            [(usage_id, existing_id, "existing-use")],
        )

    def test_duplicate_formatter_preserves_unknown_reasons_and_nulls(
        self,
    ) -> None:
        candidate = DuplicateCandidate(
            literature=Literature(
                id=7,
                title="Unknown reason",
                publication_year=None,
                doi=None,
                pmid=None,
            ),
            match_reasons=("doi", "future_reason"),
            title_similarity=0.25,
        )

        formatted = cli_module._format_duplicate_candidate(candidate)

        self.assertIn("既存文献ID: 7", formatted)
        self.assertIn("publication_year: 未登録", formatted)
        self.assertIn("DOI: 未登録", formatted)
        self.assertIn("PMID: 未登録", formatted)
        self.assertIn("一致理由: DOI一致、future_reason", formatted)
        self.assertIn("title_similarity: 0.25", formatted)

    def test_confirmation_trims_and_loops_without_recursion(self) -> None:
        invalid_count = 1200
        actions = [
            "3",
            *self.registration_values(title="Confirmation loop"),
            "",
            "invalid",
            *(["9"] * invalid_count),
            " 1 ",
            "0",
        ]

        _, feeder, outputs = self.run_with_actions(actions)

        self.assertEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertEqual(len(list_literature(self.connection)), 1)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 5,
        )

    def test_confirmation_zero_with_whitespace_cancels_without_writing(
        self,
    ) -> None:
        before = self.table_snapshot()

        _, _, outputs = self.run_with_actions(
            self.registration_actions(
                title="Whitespace cancel",
                confirmation=" \t0\n ",
            )
        )

        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("文献登録を中止しました。", outputs)

    def test_registration_input_interruptions_exit_once_without_writing(
        self,
    ) -> None:
        cases = (
            ["3", EOFError("title EOF")],
            ["3", "Title", KeyboardInterrupt()],
            [
                "3",
                *self.registration_values(title="Confirmation EOF"),
                EOFError("confirm EOF"),
            ],
            [
                "3",
                *self.registration_values(title="Confirmation interrupt"),
                KeyboardInterrupt(),
            ],
        )

        for actions in cases:
            with self.subTest(action_count=len(actions)):
                before = self.table_snapshot()
                outputs: list[str] = []
                with patch.object(cli_module, "add_literature") as added:
                    result = run_cli(
                        self.connection,
                        input_func=InputFeeder(actions),
                        output_func=outputs.append,
                    )
                self.assertIsNone(result)
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_registration_unexpected_input_exceptions_propagate(self) -> None:
        cases = (
            (
                ValueError("registration input value"),
                ["3"],
            ),
            (
                sqlite3.OperationalError("registration input sqlite"),
                ["3", "Title"],
            ),
            (
                ValueError("confirmation input value"),
                [
                    "3",
                    *self.registration_values(title="Confirmation value"),
                ],
            ),
            (
                RuntimeError("confirmation input runtime"),
                [
                    "3",
                    *self.registration_values(title="Confirmation runtime"),
                ],
            ),
            (
                sqlite3.OperationalError("confirmation input sqlite"),
                [
                    "3",
                    *self.registration_values(title="Confirmation sqlite"),
                ],
            ),
        )

        for expected, prefix in cases:
            with self.subTest(exception=str(expected)):
                before = self.table_snapshot()
                outputs: list[str] = []
                with patch.object(cli_module, "add_literature") as added:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder([*prefix, expected]),
                            output_func=outputs.append,
                        )
                self.assertIs(raised.exception, expected)
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertFalse(
                    any(
                        item.startswith("文献登録エラー: ")
                        for item in outputs
                    )
                )
                self.assertNotIn("CLIを終了します。", outputs)
                self.assertEqual(
                    self.connection.execute("SELECT 1").fetchone()[0],
                    1,
                )

    def test_registration_title_input_runtime_error_propagates(self) -> None:
        expected = RuntimeError("registration title input runtime")
        before = self.table_snapshot()
        outputs: list[str] = []

        with (
            patch.object(
                cli_module,
                "find_duplicate_candidates",
            ) as duplicate_check,
            patch.object(cli_module, "add_literature") as added,
        ):
            with self.assertRaises(RuntimeError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(["3", expected]),
                    output_func=outputs.append,
                )

        self.assertIs(raised.exception, expected)
        duplicate_check.assert_not_called()
        added.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertFalse(
            any(item.startswith("文献登録エラー: ") for item in outputs)
        )
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertNotIn("CLIを終了します。", outputs)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_blank_title_error_output_exceptions_propagate(self) -> None:
        for expected in (
            RuntimeError("blank title output runtime"),
            EOFError("blank title output EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "入力エラー: タイトルは必須です。":
                        raise expected

                with (
                    patch.object(
                        cli_module,
                        "find_duplicate_candidates",
                    ) as duplicate_check,
                    patch.object(cli_module, "add_literature") as added,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(["3", ""]),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, expected)
                duplicate_check.assert_not_called()
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertEqual(
                    outputs.count("入力エラー: タイトルは必須です。"),
                    1,
                )
                self.assertNotIn("CLIを終了します。", outputs)
                self.assertFalse(
                    any(
                        item.startswith("文献登録エラー: ")
                        for item in outputs
                    )
                )
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )

    def test_registration_output_exceptions_propagate_before_add(self) -> None:
        for expected in (
            ValueError("registration output value"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "重複候補はありません。":
                        raise expected

                with patch.object(cli_module, "add_literature") as added:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                [
                                    "3",
                                    *self.registration_values(
                                        title="Output failure"
                                    ),
                                ]
                            ),
                            output_func=output_func,
                        )
                self.assertIs(raised.exception, expected)
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )

    def test_registration_rejects_active_transaction_before_input(self) -> None:
        pending_cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending before registration",),
        )
        pending_id = pending_cursor.lastrowid
        self.assertTrue(self.connection.in_transaction)
        with (
            patch.object(cli_module, "Literature") as literature_class,
            patch.object(
                cli_module,
                "find_duplicate_candidates",
            ) as duplicate_check,
            patch.object(cli_module, "add_literature") as added,
        ):
            _, feeder, outputs = self.run_with_actions(["3", "0"])

        literature_class.assert_not_called()
        duplicate_check.assert_not_called()
        added.assert_not_called()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature WHERE id = ?",
                (pending_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            feeder.prompts,
            ["選択してください: ", "選択してください: "],
        )
        self.assertIn(
            cli_module._REGISTRATION_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.connection.rollback()

    def test_registration_rechecks_transaction_immediately_before_add(
        self,
    ) -> None:
        feeder = InputFeeder(
            [
                "3",
                *self.registration_values(title="Late transaction"),
                "1",
                "0",
            ]
        )

        def begin_before_confirmation_returns(prompt: str) -> str:
            value = feeder(prompt)
            if (
                prompt == "選択してください: "
                and value == "1"
                and len(feeder.prompts) == 30
            ):
                self.connection.execute("BEGIN")
            return value

        outputs: list[str] = []
        with patch.object(cli_module, "add_literature") as added:
            result = run_cli(
                self.connection,
                input_func=begin_before_confirmation_returns,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        added.assert_not_called()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature"
            ).fetchone()[0],
            0,
        )
        self.assertIn(
            cli_module._REGISTRATION_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.connection.rollback()

    def test_transaction_rejection_does_not_commit_rollback_or_close(
        self,
    ) -> None:
        tracking_path = self.directory / "registration-tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending lifecycle record",),
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0

        _, _, outputs = self.run_with_actions(
            ["3", "0"],
            connection=connection,
        )

        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertTrue(connection.in_transaction)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM literature"
            ).fetchone()[0],
            1,
        )
        self.assertIn(
            cli_module._REGISTRATION_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        sqlite3.Connection.rollback(connection)

    def test_duplicate_api_exception_boundaries(self) -> None:
        api_exceptions = (
            ValueError("duplicate value"),
            sqlite3.OperationalError("duplicate sqlite"),
            RuntimeError("duplicate runtime"),
            EOFError("duplicate EOF"),
            KeyboardInterrupt(),
        )

        for expected in api_exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                actions = [
                    "3",
                    *self.registration_values(
                        title=f"Duplicate API {type(expected).__name__}"
                    ),
                ]
                if isinstance(expected, ValueError):
                    actions.append("0")
                with (
                    patch.object(
                        cli_module,
                        "find_duplicate_candidates",
                        side_effect=expected,
                    ),
                    patch.object(cli_module, "add_literature") as added,
                ):
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("文献登録エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                added.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )

    def test_add_api_exception_boundaries(self) -> None:
        api_exceptions = (
            ValueError("add value"),
            sqlite3.OperationalError("add sqlite"),
            RuntimeError("add runtime"),
            EOFError("add EOF"),
            KeyboardInterrupt(),
        )

        for expected in api_exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                actions = [
                    "3",
                    *self.registration_values(
                        title=f"Add API {type(expected).__name__}"
                    ),
                    "1",
                ]
                if isinstance(expected, ValueError):
                    actions.append("0")
                with patch.object(
                    cli_module,
                    "add_literature",
                    side_effect=expected,
                ):
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("文献登録エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )

    def test_success_output_failure_preserves_committed_literature(
        self,
    ) -> None:
        tracking_path = self.directory / "success-output-tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        expected = RuntimeError("success output failure")
        outputs: list[str] = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == "文献を登録しました。":
                raise expected

        with patch.object(
            cli_module,
            "add_literature",
            wraps=add_literature,
        ) as added:
            with self.assertRaises(RuntimeError) as raised:
                run_cli(
                    connection,
                    input_func=InputFeeder(
                        [
                            "3",
                            *self.registration_values(
                                title="Committed before output failure"
                            ),
                            "1",
                        ]
                    ),
                    output_func=output_func,
                )

        self.assertIs(raised.exception, expected)
        added.assert_called_once()
        records = list_literature(connection)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Committed before output failure")
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertEqual(outputs.count("文献を登録しました。"), 1)
        self.assertFalse(
            any(item.startswith("文献登録エラー: ") for item in outputs)
        )
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertNotIn("CLIを終了します。", outputs)

    def test_real_sqlite_insert_failure_rolls_back_and_is_rethrown(
        self,
    ) -> None:
        tracking_path = self.directory / "insert-failure-tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(
            connection,
            "PRAGMA foreign_keys = ON",
        )
        existing_id = add_literature(
            connection,
            Literature(title="Existing before forced failure"),
        )
        connection.execute(
            """
            CREATE TRIGGER reject_forced_cli_insert
            BEFORE INSERT ON literature
            WHEN NEW.title = 'Forced SQLite failure'
            BEGIN
                SELECT RAISE(ABORT, 'forced insert failure');
            END
            """
        )
        connection.commit()
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        self.assertFalse(connection.in_transaction)
        existing_before = get_literature(connection, existing_id)
        outputs: list[str] = []
        api_errors: list[sqlite3.Error] = []

        def tracked_add(
            connection: sqlite3.Connection,
            literature: Literature,
        ) -> int:
            try:
                return add_literature(connection, literature)
            except sqlite3.Error as error:
                api_errors.append(error)
                raise

        with patch.object(
            cli_module,
            "add_literature",
            side_effect=tracked_add,
        ) as added:
            with self.assertRaises(sqlite3.Error) as raised:
                run_cli(
                    connection,
                    input_func=InputFeeder(
                        [
                            "3",
                            *self.registration_values(
                                title="Forced SQLite failure"
                            ),
                            "1",
                        ]
                    ),
                    output_func=outputs.append,
                )

        added.assert_called_once()
        self.assertEqual(len(api_errors), 1)
        self.assertIs(raised.exception, api_errors[0])
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM literature "
                "WHERE title = ?",
                ("Forced SQLite failure",),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            get_literature(connection, existing_id),
            existing_before,
        )
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertEqual(
            outputs.count("データベースエラーが発生しました。"),
            1,
        )
        self.assertFalse(
            any(item.startswith("文献登録エラー: ") for item in outputs)
        )
        self.assertNotIn("CLIを終了します。", outputs)

    def test_database_error_output_failure_propagates_output_exception(
        self,
    ) -> None:
        database_error = sqlite3.OperationalError("duplicate database")
        output_error = ValueError("database error output")

        def output_func(message: str) -> None:
            if message == "データベースエラーが発生しました。":
                raise output_error

        with patch.object(
            cli_module,
            "find_duplicate_candidates",
            side_effect=database_error,
        ):
            with self.assertRaises(ValueError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(
                        [
                            "3",
                            *self.registration_values(
                                title="DB error output"
                            ),
                        ]
                    ),
                    output_func=output_func,
                )

        self.assertIs(raised.exception, output_error)

    def test_candidate_display_sqlite_error_is_not_api_database_error(
        self,
    ) -> None:
        expected = sqlite3.OperationalError("candidate output")
        outputs: list[str] = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == "重複候補はありません。":
                raise expected

        with patch.object(cli_module, "add_literature") as added:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(
                        [
                            "3",
                            *self.registration_values(
                                title="Candidate output boundary"
                            ),
                        ]
                    ),
                    output_func=output_func,
                )

        self.assertIs(raised.exception, expected)
        added.assert_not_called()
        self.assertNotIn("データベースエラーが発生しました。", outputs)

    def test_success_changes_only_literature_and_preserves_schema_state(
        self,
    ) -> None:
        self.connection.execute("PRAGMA user_version = 81")
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        self.run_with_actions(
            self.registration_actions(title="Schema-safe registration")
        )

        self.assertEqual(len(list_literature(self.connection)), 1)
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        for table in ("tags", "literature_tags", "usage_history"):
            self.assertEqual(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0],
                0,
            )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_cli_preserves_tables_schema_pragmas_and_transaction_state(
        self,
    ) -> None:
        self.populate_search_records()
        self.connection.execute("PRAGMA user_version = 81")
        tables_before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        in_transaction_before = self.connection.in_transaction
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            self.run_with_actions(
                [
                    "1",
                    "2",
                    "CLI検索対象",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "0",
                ]
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(self.table_snapshot(), tables_before)
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertEqual(self.connection.in_transaction, in_transaction_before)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        forbidden = (
            "INSERT",
            "UPDATE",
            "DELETE",
            "ALTER",
            "DROP",
            "CREATE",
            "REPLACE",
            "COMMIT",
            "ROLLBACK",
            "PRAGMA",
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(forbidden)
                for statement in statements
            )
        )

    def test_cli_preserves_explicit_read_transaction(self) -> None:
        self.connection.execute("BEGIN")
        self.assertTrue(self.connection.in_transaction)

        result, _, outputs = self.run_with_actions(["1", "0"])

        self.assertIsNone(result)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_cli_does_not_commit_rollback_or_close_pending_transaction(
        self,
    ) -> None:
        tracking_path = self.directory / "tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        committed_id = add_literature(
            connection,
            Literature(title="Committed tracking record"),
        )
        pending_cursor = connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending tracking record",),
        )
        pending_id = pending_cursor.lastrowid
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        self.assertTrue(connection.in_transaction)

        _, _, outputs = self.run_with_actions(
            ["1", "0"],
            connection=connection,
        )

        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertTrue(connection.in_transaction)
        interruption_outputs: list[str] = []
        interruption_result = run_cli(
            connection,
            input_func=InputFeeder(["2", EOFError("keyword EOF")]),
            output_func=interruption_outputs.append,
        )
        self.assertIsNone(interruption_result)
        self.assertEqual(
            interruption_outputs.count("CLIを終了します。"),
            1,
        )
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertTrue(connection.in_transaction)
        displayed = "\n".join(outputs)
        self.assertIn(f"ID: {committed_id}", displayed)
        self.assertIn(f"ID: {pending_id}", displayed)
        observer = connect_database(tracking_path)
        try:
            self.assertEqual(
                observer.execute(
                    "SELECT COUNT(*) FROM literature WHERE id = ?",
                    (pending_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            observer.close()
        sqlite3.Connection.rollback(connection)

    def test_edit_menu_and_invalid_main_choice_contract(self) -> None:
        _, _, outputs = self.run_with_actions(["invalid", "0"])

        self.assertIn("4. 文献編集", outputs[0])
        self.assertIn("5. 文献削除", outputs[0])
        self.assertIn("6. タグ管理", outputs[0])
        self.assertIn("7. 使用履歴管理", outputs[0])
        self.assertIn("8. 文献詳細", outputs[0])
        self.assertIn(cli_module._INVALID_MENU_MESSAGE, outputs)
        for choice in (
            "0",
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
        ):
            with self.subTest(choice=choice):
                self.assertIn(choice, cli_module._INVALID_MENU_MESSAGE)

    def test_edit_id_validation_rejects_non_positive_or_non_ascii_forms(
        self,
    ) -> None:
        self.add_record("ID validation")
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                before = self.table_snapshot()
                with (
                    patch.object(cli_module, "get_literature") as retrieved,
                    patch.object(cli_module, "update_literature") as updated,
                ):
                    _, feeder, outputs = self.run_with_actions(
                        ["4", invalid_value, "0"]
                    )

                retrieved.assert_not_called()
                updated.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and "文献ID" in item
                        and "ASCII" in item
                        for item in outputs
                    )
                )
                self.assertEqual(
                    feeder.prompts,
                    [
                        "選択してください: ",
                        "文献ID（ASCII数字）: ",
                        "選択してください: ",
                    ],
                )

    def test_edit_accepts_trimmed_existing_maximum_id(self) -> None:
        first_id = self.add_record("First ID")
        maximum_id = self.add_record("Maximum existing ID")
        first_before = get_literature(self.connection, first_id)

        with patch.object(
            cli_module,
            "get_literature",
            wraps=get_literature,
        ) as retrieved:
            _, _, outputs = self.run_with_actions(
                ["4", f" \t00{maximum_id}\n ", "2", " New Author ", "1", "0"]
            )

        retrieved.assert_called_once_with(self.connection, maximum_id)
        self.assertEqual(
            get_literature(self.connection, maximum_id).authors,
            "New Author",
        )
        self.assertEqual(
            get_literature(self.connection, first_id),
            first_before,
        )
        self.assertIn("文献を更新しました。", outputs)

    def test_edit_unknown_id_uses_get_api_then_returns_to_menu(self) -> None:
        before = self.table_snapshot()

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(cli_module, "update_literature") as updated,
        ):
            _, feeder, outputs = self.run_with_actions(["4", "999999", "0"])

        retrieved.assert_called_once_with(self.connection, 999999)
        updated.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("対象文献が見つかりません。", outputs)
        self.assertEqual(
            feeder.prompts,
            [
                "選択してください: ",
                "文献ID（ASCII数字）: ",
                "選択してください: ",
            ],
        )

    def test_edit_displays_all_31_saved_fields_in_order_without_mutation(
        self,
    ) -> None:
        literature_id = self.add_record(
            '肩関節 "Full", Study',
            authors='Author A, "Author B"\nAuthor C',
            journal="Journal 内部  空白",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="101-112",
            doi="10.1000/display",
            pmid="00123",
            url="https://example.test/edit",
            language="日本語 / English",
            publication_type="原著",
            abstract='Abstract, "quoted"\nsecond line',
            pdf_path="/tmp/edit literature.pdf",
            personal_summary="自分の要約",
            ai_summary="AI要約\n未確認本文",
            ai_summary_status="修正済み",
            general_note="一般メモ",
            key_findings="主要な結果",
            methods_note="方法メモ",
            clinical_note="臨床メモ",
            limitation_note="限界メモ",
            relevance_note="関連メモ",
            evidence_level="Level II",
            verification_status="要確認",
            adoption_status="採用候補",
            exclusion_reason="除外理由",
            rating=4,
        )
        self.connection.execute(
            "UPDATE literature SET doi = ?, pmid = ? WHERE id = ?",
            (
                " DOI:10.1000/Mixed Case ",
                " PMID: 001 23 ",
                literature_id,
            ),
        )
        self.connection.commit()
        before = get_literature(self.connection, literature_id)

        _, _, outputs = self.run_with_actions(
            ["4", str(literature_id), "0", "0"]
        )

        after = get_literature(self.connection, literature_id)
        self.assertEqual(after, before)
        displayed = next(item for item in outputs if item.startswith("id: "))
        expected_fields = (
            "id",
            *_REGISTRATION_FIELDS,
            "created_at",
            "updated_at",
        )
        position = -1
        for index, field_name in enumerate(expected_fields):
            with self.subTest(field_name=field_name):
                prefix = "" if index == 0 else "\n"
                needle = f"{prefix}{field_name}: "
                position = displayed.find(needle, position + 1)
                self.assertNotEqual(position, -1)
                self.assertIn(
                    f"{field_name}: "
                    f"{cli_module._display_value(getattr(before, field_name))}",
                    displayed,
                )
        self.assertEqual(
            displayed,
            cli_module._format_edit_literature(before),
        )
        self.assertIn(" DOI:10.1000/Mixed Case ", displayed)
        self.assertIn(" PMID: 001 23 ", displayed)

    def test_edit_formatter_displays_none_and_zero_without_mutation(self) -> None:
        literature = Literature(
            id=0,
            title="Formatter",
            publication_year=0,
            rating=None,
            created_at="created",
            updated_at="updated",
        )
        before = vars(literature).copy()

        formatted = cli_module._format_edit_literature(literature)

        self.assertIn("id: 0", formatted)
        self.assertIn("publication_year: 0", formatted)
        self.assertIn("authors: 未登録", formatted)
        self.assertIn("rating: 未登録", formatted)
        self.assertNotIn("authors: None", formatted)
        self.assertEqual(vars(literature), before)

    def test_edit_field_menu_mapping_updates_each_of_28_fields_once(
        self,
    ) -> None:
        sentinel_id = self.add_record(
            "Mapping sentinel",
            general_note="must stay unchanged",
        )
        sentinel_before = get_literature(self.connection, sentinel_id)
        raw_and_expected = {
            "title": ("  New title  ", "New title"),
            "authors": (
                '  Author A, "Author B"\nkeeps  spaces  ',
                'Author A, "Author B"\nkeeps  spaces',
            ),
            "journal": ("  New Journal  ", "New Journal"),
            "publication_year": (" 2025 ", 2025),
            "volume": (" 12 ", "12"),
            "issue": (" 3 ", "3"),
            "pages": (" 101-112 ", "101-112"),
            "doi": (" DOI:10.ABC/Edited ", "10.abc/edited"),
            "pmid": (" PMID: 001 23 ", "00123"),
            "url": (" https://example.test/edited ", "https://example.test/edited"),
            "language": (" 日本語 / English ", "日本語 / English"),
            "publication_type": (" 原著 ", "原著"),
            "abstract": (
                '  Abstract, "quoted"\nsecond  line  ',
                'Abstract, "quoted"\nsecond  line',
            ),
            "pdf_path": (" /tmp/edited literature.pdf ", "/tmp/edited literature.pdf"),
            "personal_summary": (" 自分の要約 ", "自分の要約"),
            "ai_summary": (" AI要約本文 ", "AI要約本文"),
            "ai_summary_status": (" 確認済み ", "確認済み"),
            "general_note": (" 一般メモ ", "一般メモ"),
            "key_findings": (" 主要な結果 ", "主要な結果"),
            "methods_note": (" 方法メモ ", "方法メモ"),
            "clinical_note": (" 臨床メモ ", "臨床メモ"),
            "limitation_note": (" 限界メモ ", "限界メモ"),
            "relevance_note": (" 関連メモ ", "関連メモ"),
            "evidence_level": (" Level II ", "Level II"),
            "verification_status": (" 確認済み ", "確認済み"),
            "adoption_status": (" 採用 ", "採用"),
            "exclusion_reason": (" 除外理由 ", "除外理由"),
            "rating": (" 05 ", 5),
        }
        self.assertEqual(tuple(raw_and_expected), _REGISTRATION_FIELDS)
        self.assertEqual(cli_module._EDIT_FIELDS, _REGISTRATION_FIELDS)

        for field_number, field_name in enumerate(
            _REGISTRATION_FIELDS,
            start=1,
        ):
            with self.subTest(
                field_number=field_number,
                field_name=field_name,
            ):
                literature_id = self.add_record(
                    f"Mapping record {field_number}",
                )
                before = get_literature(self.connection, literature_id)
                raw_value, expected_value = raw_and_expected[field_name]
                api_value = (
                    raw_value.strip()
                    if field_name in {"doi", "pmid"}
                    else expected_value
                )
                with patch.object(
                    cli_module,
                    "update_literature",
                    wraps=update_literature,
                ) as updated:
                    _, _, outputs = self.run_with_actions(
                        self.edit_actions(
                            literature_id,
                            field_number,
                            raw_value,
                        )
                    )

                updated.assert_called_once_with(
                    self.connection,
                    literature_id,
                    {field_name: api_value},
                )
                after = get_literature(self.connection, literature_id)
                self.assertEqual(
                    getattr(after, field_name),
                    expected_value,
                )
                for other_field in _REGISTRATION_FIELDS:
                    if other_field != field_name:
                        self.assertEqual(
                            getattr(after, other_field),
                            getattr(before, other_field),
                        )
                self.assertEqual(after.created_at, before.created_at)
                self.assertGreater(
                    datetime.fromisoformat(
                        after.updated_at.replace("Z", "+00:00")
                    ),
                    datetime.fromisoformat(
                        before.updated_at.replace("Z", "+00:00")
                    ),
                )
                self.assertIn(f"field: {field_name}", outputs)
                self.assertIn("文献を更新しました。", outputs)

        self.assertEqual(
            get_literature(self.connection, sentinel_id),
            sentinel_before,
        )

    def test_edit_field_menu_trims_loops_and_zero_cancels(self) -> None:
        literature_id = self.add_record("Field menu loop")
        before = self.table_snapshot()
        invalid_count = 1200
        actions = [
            "4",
            str(literature_id),
            "",
            "-1",
            "２",
            "٢",
            "29",
            *(["invalid"] * invalid_count),
            " 0 ",
            "0",
        ]

        with patch.object(cli_module, "update_literature") as updated:
            _, feeder, outputs = self.run_with_actions(actions)

        updated.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            outputs.count(cli_module._INVALID_EDIT_FIELD_MESSAGE),
            invalid_count + 5,
        )
        self.assertIn("文献編集を中止しました。", outputs)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 8,
        )
        for field_number, field_name in enumerate(
            _REGISTRATION_FIELDS,
            start=1,
        ):
            self.assertIn(
                f"{field_number}. {field_name}",
                cli_module._EDIT_FIELD_MENU,
            )
        for forbidden in ("id", "created_at", "updated_at", "tag", "usage"):
            self.assertNotRegex(
                cli_module._EDIT_FIELD_MENU,
                rf"(?m)^\d+\. {forbidden}$",
            )

    def test_edit_optional_text_can_be_cleared_and_does_not_nfkc(self) -> None:
        literature_id = self.add_record(
            "Optional text",
            authors="Existing authors",
            general_note="Existing note",
        )

        self.run_with_actions(
            self.edit_actions(literature_id, 2, " \t\n ")
        )
        self.run_with_actions(
            self.edit_actions(
                literature_id,
                18,
                "  ＳＨＯＵＬＤＥＲ： 内部  空白  ",
            )
        )

        stored = get_literature(self.connection, literature_id)
        self.assertIsNone(stored.authors)
        self.assertEqual(
            stored.general_note,
            "ＳＨＯＵＬＤＥＲ： 内部  空白",
        )

    def test_edit_title_and_required_status_blank_stop_before_update(
        self,
    ) -> None:
        literature_id = self.add_record("Required fields")
        cases = (
            (1, " \t\n ", "タイトルは必須"),
            (17, " ", "ai_summary_status"),
            (25, "\t", "verification_status"),
            (26, "\n", "adoption_status"),
        )

        for field_number, raw_value, expected_message in cases:
            with self.subTest(field_number=field_number):
                before = get_literature(self.connection, literature_id)
                with patch.object(cli_module, "update_literature") as updated:
                    _, _, outputs = self.run_with_actions(
                        [
                            "4",
                            str(literature_id),
                            str(field_number),
                            raw_value,
                            "0",
                        ]
                    )
                updated.assert_not_called()
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and expected_message in item
                        for item in outputs
                    )
                )

    def test_edit_integer_formats_and_repository_ranges(self) -> None:
        literature_id = self.add_record(
            "Integer edits",
            publication_year=2025,
            rating=3,
        )
        invalid_formats = (
            "+1",
            "-1",
            "1.5",
            "1e3",
            "２０２５",
            "١٢٣٤",
            "value",
        )

        for field_number, field_name in ((4, "publication_year"), (28, "rating")):
            for invalid_value in invalid_formats:
                with self.subTest(
                    field_name=field_name,
                    invalid_value=invalid_value,
                ):
                    before = get_literature(self.connection, literature_id)
                    with patch.object(
                        cli_module,
                        "update_literature",
                    ) as updated:
                        _, _, outputs = self.run_with_actions(
                            [
                                "4",
                                str(literature_id),
                                str(field_number),
                                invalid_value,
                                "0",
                            ]
                        )
                    updated.assert_not_called()
                    self.assertEqual(
                        get_literature(self.connection, literature_id),
                        before,
                    )
                    self.assertTrue(
                        any(
                            item.startswith("入力エラー: ")
                            and field_name in item
                            and "ASCII" in item
                            for item in outputs
                        )
                    )

        for field_number, field_name, invalid_value in (
            (4, "publication_year", "1799"),
            (4, "publication_year", "9999"),
            (4, "publication_year", "05"),
            (28, "rating", "0"),
            (28, "rating", "6"),
        ):
            with self.subTest(
                field_name=field_name,
                invalid_value=invalid_value,
            ):
                before = get_literature(self.connection, literature_id)
                _, _, outputs = self.run_with_actions(
                    self.edit_actions(
                        literature_id,
                        field_number,
                        invalid_value,
                    )
                )
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertTrue(
                    any(
                        item.startswith("文献編集エラー: ")
                        and field_name in item
                        for item in outputs
                    )
                )

        self.run_with_actions(self.edit_actions(literature_id, 4, ""))
        self.assertIsNone(
            get_literature(self.connection, literature_id).publication_year
        )
        self.run_with_actions(self.edit_actions(literature_id, 28, ""))
        self.assertIsNone(get_literature(self.connection, literature_id).rating)
        self.run_with_actions(self.edit_actions(literature_id, 28, "05"))
        self.assertEqual(get_literature(self.connection, literature_id).rating, 5)

        fixed_today = date(2026, 12, 31)

        class FixedDate(date):
            @classmethod
            def today(cls) -> date:
                return fixed_today

        with patch.object(repository_module, "date", FixedDate):
            for valid_year in ("1800", "2027"):
                self.run_with_actions(
                    self.edit_actions(literature_id, 4, valid_year)
                )
                self.assertEqual(
                    get_literature(
                        self.connection,
                        literature_id,
                    ).publication_year,
                    int(valid_year),
                )
            before = get_literature(self.connection, literature_id)
            for invalid_year in ("1799", "2028"):
                _, _, outputs = self.run_with_actions(
                    self.edit_actions(literature_id, 4, invalid_year)
                )
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertTrue(
                    any(item.startswith("文献編集エラー: ") for item in outputs)
                )

    def test_edit_all_status_values_and_invalid_values(self) -> None:
        literature_id = self.add_record(
            "Status edits",
            ai_summary="AI body remains",
            exclusion_reason="Reason remains",
        )
        cases = (
            (
                17,
                "ai_summary_status",
                ("未作成", "未確認", "確認済み", "修正済み"),
            ),
            (
                25,
                "verification_status",
                ("未確認", "一部確認", "確認済み", "要確認"),
            ),
            (
                26,
                "adoption_status",
                ("未判定", "採用候補", "採用", "除外"),
            ),
        )

        for field_number, field_name, allowed_values in cases:
            for value in allowed_values:
                with self.subTest(field_name=field_name, value=value):
                    self.run_with_actions(
                        self.edit_actions(
                            literature_id,
                            field_number,
                            f" {value} ",
                        )
                    )
                    self.assertEqual(
                        getattr(
                            get_literature(self.connection, literature_id),
                            field_name,
                        ),
                        value,
                    )

            before = get_literature(self.connection, literature_id)
            _, _, outputs = self.run_with_actions(
                self.edit_actions(
                    literature_id,
                    field_number,
                    " 不正状態 ",
                )
            )
            self.assertEqual(
                get_literature(self.connection, literature_id),
                before,
            )
            self.assertTrue(
                any(
                    item.startswith("文献編集エラー: ")
                    and field_name in item
                    for item in outputs
                )
            )
            self.assertNotIn(
                "データベースエラーが発生しました。",
                outputs,
            )

        stored = get_literature(self.connection, literature_id)
        self.assertEqual(stored.ai_summary, "AI body remains")
        self.assertEqual(stored.exclusion_reason, "Reason remains")

    def test_edit_doi_pmid_confirm_raw_then_repository_normalizes(self) -> None:
        literature_id = self.add_record("Identifier edits")

        for field_number, field_name, raw_value, expected in (
            (8, "doi", " DOI:10.ABC/Raw ", "10.abc/raw"),
            (9, "pmid", " PMID: 001 23 ", "00123"),
        ):
            with self.subTest(field_name=field_name):
                with patch.object(
                    cli_module,
                    "find_duplicate_candidates",
                ) as duplicate_check:
                    _, _, outputs = self.run_with_actions(
                        self.edit_actions(
                            literature_id,
                            field_number,
                            raw_value,
                        )
                    )
                duplicate_check.assert_not_called()
                self.assertEqual(
                    getattr(
                        get_literature(self.connection, literature_id),
                        field_name,
                    ),
                    expected,
                )
                displayed = "\n".join(outputs)
                self.assertIn(f"変更後: {raw_value.strip()}", displayed)
                self.assertIn(
                    "DOIとPMIDは更新時に標準形式へ正規化されます。",
                    outputs,
                )
                success_index = outputs.index("文献を更新しました。")
                self.assertNotIn(
                    expected,
                    "\n".join(outputs[success_index:success_index + 3]),
                )

        before_invalid_pmid = get_literature(
            self.connection,
            literature_id,
        )
        _, _, invalid_outputs = self.run_with_actions(
            self.edit_actions(
                literature_id,
                9,
                " PMID: invalid ",
            )
        )
        self.assertEqual(
            get_literature(self.connection, literature_id),
            before_invalid_pmid,
        )
        self.assertTrue(
            any(
                item.startswith("文献編集エラー: ")
                and "pmid" in item
                for item in invalid_outputs
            )
        )

    def test_edit_confirmation_loops_trims_and_cancel_preserves_row(
        self,
    ) -> None:
        literature_id = self.add_record(
            "Confirmation edits",
            authors="Before",
        )
        invalid_count = 1200
        actions = [
            "4",
            str(literature_id),
            "2",
            "After",
            "",
            "invalid",
            *(["9"] * invalid_count),
            " \t0\n ",
            "0",
        ]
        before = get_literature(self.connection, literature_id)

        with patch.object(cli_module, "update_literature") as updated:
            _, feeder, outputs = self.run_with_actions(actions)

        updated.assert_not_called()
        self.assertEqual(
            get_literature(self.connection, literature_id),
            before,
        )
        self.assertEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("文献更新を中止しました。", outputs)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 6,
        )

    def test_edit_input_interruptions_exit_once_without_update(self) -> None:
        literature_id = self.add_record(
            "Interrupted edit",
            authors="Before",
        )
        cases = (
            ["4", EOFError("id EOF")],
            ["4", str(literature_id), KeyboardInterrupt()],
            ["4", str(literature_id), "2", EOFError("value EOF")],
            [
                "4",
                str(literature_id),
                "2",
                "After",
                KeyboardInterrupt(),
            ],
        )

        for actions in cases:
            with self.subTest(action_count=len(actions)):
                before = get_literature(self.connection, literature_id)
                outputs: list[str] = []
                with patch.object(cli_module, "update_literature") as updated:
                    result = run_cli(
                        self.connection,
                        input_func=InputFeeder(actions),
                        output_func=outputs.append,
                    )
                self.assertIsNone(result)
                updated.assert_not_called()
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertEqual(outputs.count("CLIを終了します。"), 1)

    def test_edit_unexpected_input_exceptions_propagate_unchanged(self) -> None:
        literature_id = self.add_record("Unexpected input")
        cases = (
            (["4"], ValueError("id input")),
            (["4", str(literature_id)], sqlite3.OperationalError("field input")),
            (["4", str(literature_id), "2"], RuntimeError("value input")),
            (
                ["4", str(literature_id), "2", "After"],
                sqlite3.OperationalError("confirmation input"),
            ),
        )

        for prefix, expected in cases:
            with self.subTest(exception=str(expected)):
                before = get_literature(self.connection, literature_id)
                outputs: list[str] = []
                with patch.object(cli_module, "update_literature") as updated:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder([*prefix, expected]),
                            output_func=outputs.append,
                        )
                self.assertIs(raised.exception, expected)
                updated.assert_not_called()
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )
                self.assertNotIn("CLIを終了します。", outputs)

    def test_edit_input_exception_matrix_preserves_state_and_boundary(
        self,
    ) -> None:
        positions = (
            (
                "literature_id",
                lambda literature_id: ["4"],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                ),
            ),
            (
                "field_choice",
                lambda literature_id: ["4", str(literature_id)],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                    "選択してください: ",
                ),
            ),
            (
                "new_value",
                lambda literature_id: [
                    "4",
                    str(literature_id),
                    "2",
                ],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                    "選択してください: ",
                    "authors（空欄で未登録）: ",
                ),
            ),
            (
                "confirmation",
                lambda literature_id: [
                    "4",
                    str(literature_id),
                    "2",
                    "After",
                ],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                    "選択してください: ",
                    "authors（空欄で未登録）: ",
                    "選択してください: ",
                ),
            ),
        )
        exception_types = (
            ("EOFError", EOFError),
            ("KeyboardInterrupt", KeyboardInterrupt),
            ("ValueError", ValueError),
            ("RuntimeError", RuntimeError),
            ("sqlite3.Error", sqlite3.Error),
        )

        for position_index, (
            position,
            action_prefix,
            expected_prompts,
        ) in enumerate(positions):
            for exception_index, (
                exception_name,
                exception_type,
            ) in enumerate(exception_types):
                with self.subTest(
                    position=position,
                    exception=exception_name,
                ):
                    connection, target_id, other_id = (
                        self.create_tracking_edit_fixture(
                            f"input-{position_index}-{exception_index}"
                        )
                    )
                    try:
                        before = self.table_snapshot_for(connection)
                        target_before = get_literature(
                            connection,
                            target_id,
                        )
                        other_before = get_literature(
                            connection,
                            other_id,
                        )
                        expected = exception_type(
                            f"{position} {exception_name} input failure"
                        )
                        feeder = InputFeeder(
                            [*action_prefix(target_id), expected]
                        )
                        outputs: list[str] = []

                        with patch.object(
                            cli_module,
                            "update_literature",
                        ) as updated:
                            if isinstance(
                                expected,
                                (EOFError, KeyboardInterrupt),
                            ):
                                result = run_cli(
                                    connection,
                                    input_func=feeder,
                                    output_func=outputs.append,
                                )
                                self.assertIsNone(result)
                                self.assertEqual(
                                    outputs.count(
                                        "CLIを終了します。"
                                    ),
                                    1,
                                )
                            else:
                                with self.assertRaises(
                                    exception_type
                                ) as raised:
                                    run_cli(
                                        connection,
                                        input_func=feeder,
                                        output_func=outputs.append,
                                    )
                                self.assertIs(
                                    raised.exception,
                                    expected,
                                )
                                self.assertNotIn(
                                    "CLIを終了します。",
                                    outputs,
                                )

                        updated.assert_not_called()
                        self.assertEqual(
                            feeder.prompts,
                            list(expected_prompts),
                        )
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                        self.assertEqual(
                            get_literature(connection, target_id),
                            target_before,
                        )
                        self.assertEqual(
                            get_literature(connection, other_id),
                            other_before,
                        )
                        self.assertEqual(
                            get_literature(
                                connection,
                                target_id,
                            ).updated_at,
                            target_before.updated_at,
                        )
                        self.assertFalse(
                            any(
                                item.startswith("入力エラー: ")
                                for item in outputs
                            )
                        )
                        self.assertFalse(
                            any(
                                item.startswith("文献編集エラー: ")
                                for item in outputs
                            )
                        )
                        self.assertNotIn(
                            "データベースエラーが発生しました。",
                            outputs,
                        )
                        self.assertEqual(connection.commit_calls, 0)
                        self.assertEqual(connection.rollback_calls, 0)
                        self.assertEqual(connection.close_calls, 0)
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.execute(
                                "SELECT 1"
                            ).fetchone()[0],
                            1,
                        )
                    finally:
                        if connection.in_transaction:
                            sqlite3.Connection.rollback(connection)
                        sqlite3.Connection.close(connection)

    def test_edit_output_exception_matrix_preserves_stage_contracts(
        self,
    ) -> None:
        cases = (
            (
                "initial_active_transaction",
                "initial_transaction",
                1,
                "not_called",
            ),
            (
                "invalid_literature_id",
                "invalid_id",
                2,
                "not_called",
            ),
            (
                "missing_literature",
                "missing",
                2,
                "not_called",
            ),
            (
                "current_literature",
                "current",
                2,
                "not_called",
            ),
            (
                "invalid_new_value",
                "invalid_value",
                4,
                "not_called",
            ),
            (
                "change_confirmation",
                "change",
                4,
                "not_called",
            ),
            (
                "pre_update_active_transaction",
                "late_transaction",
                5,
                "not_called",
            ),
            (
                "update_value_error",
                "update_error",
                5,
                "once",
            ),
            (
                "update_false",
                "disappeared",
                5,
                "once",
            ),
        )

        for case_index, (
            case_name,
            stage,
            expected_prompt_count,
            expected_update_calls,
        ) in enumerate(cases):
            with self.subTest(case=case_name, exception="RuntimeError"):
                connection, target_id, _ = (
                    self.create_tracking_edit_fixture(
                        f"output-{case_index}"
                    )
                )
                marker_name = "pending-late-transaction-marker"
                try:
                    before = self.table_snapshot_for(connection)
                    target_before = get_literature(
                        connection,
                        target_id,
                    )
                    update_error = ValueError(
                        "forced update validation failure"
                    )
                    expected = RuntimeError(
                        f"{case_name} output failure"
                    )
                    if stage == "initial_transaction":
                        connection.execute("BEGIN")
                        actions: list[object] = ["4"]
                        failing_message = (
                            cli_module._EDIT_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "invalid_id":
                        actions = ["4", "invalid"]
                        failing_message = (
                            "入力エラー: 文献IDは1以上の"
                            "ASCII数字だけで入力してください。"
                        )
                    elif stage == "missing":
                        actions = ["4", "999999"]
                        failing_message = "対象文献が見つかりません。"
                    elif stage == "current":
                        actions = ["4", str(target_id)]
                        failing_message = (
                            cli_module._format_edit_literature(
                                target_before
                            )
                        )
                    elif stage == "invalid_value":
                        actions = ["4", str(target_id), "1", ""]
                        failing_message = (
                            "入力エラー: タイトルは必須です。"
                        )
                    elif stage == "change":
                        actions = [
                            "4",
                            str(target_id),
                            "2",
                            "After",
                        ]
                        failing_message = (
                            "文献の変更内容を確認してください。"
                        )
                    elif stage == "late_transaction":
                        actions = [
                            "4",
                            str(target_id),
                            "2",
                            "After",
                            "1",
                        ]
                        failing_message = (
                            cli_module._EDIT_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "update_error":
                        actions = [
                            "4",
                            str(target_id),
                            "2",
                            "After",
                            "1",
                        ]
                        failing_message = f"文献編集エラー: {update_error}"
                    else:
                        actions = [
                            "4",
                            str(target_id),
                            "2",
                            "After",
                            "1",
                        ]
                        failing_message = (
                            "確認後に対象文献が存在しなくなりました。"
                        )

                    feeder = InputFeeder(actions)

                    def input_func(prompt: str) -> str:
                        value = feeder(prompt)
                        if (
                            stage == "late_transaction"
                            and value == "1"
                            and len(feeder.prompts) == 5
                        ):
                            self.assertFalse(connection.in_transaction)
                            connection.execute(
                                "INSERT INTO tags (name) VALUES (?)",
                                (marker_name,),
                            )
                            self.assertTrue(connection.in_transaction)
                        return value

                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    update_side_effect: object = None
                    update_return_value = True
                    if stage == "update_error":
                        update_side_effect = update_error
                    elif stage == "disappeared":
                        update_return_value = False

                    with patch.object(
                        cli_module,
                        "update_literature",
                        side_effect=update_side_effect,
                        return_value=update_return_value,
                    ) as updated:
                        with self.assertRaises(RuntimeError) as raised:
                            run_cli(
                                connection,
                                input_func=input_func,
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    self.assertIsNot(raised.exception, update_error)
                    if expected_update_calls == "once":
                        updated.assert_called_once_with(
                            connection,
                            target_id,
                            {"authors": "After"},
                        )
                    else:
                        updated.assert_not_called()
                    self.assertEqual(
                        len(feeder.prompts),
                        expected_prompt_count,
                    )
                    self.assertEqual(
                        outputs.count(failing_message),
                        1,
                    )
                    expected_snapshot = before
                    if stage == "late_transaction":
                        marker_rows = connection.execute(
                            "SELECT * FROM tags WHERE name = ?",
                            (marker_name,),
                        ).fetchall()
                        self.assertEqual(len(marker_rows), 1)
                        expected_snapshot = {
                            table: list(rows)
                            for table, rows in before.items()
                        }
                        expected_snapshot["tags"].append(
                            tuple(marker_rows[0])
                        )
                    self.assertEqual(
                        self.table_snapshot_for(connection),
                        expected_snapshot,
                    )
                    self.assertEqual(
                        get_literature(connection, target_id),
                        target_before,
                    )
                    self.assertEqual(
                        get_literature(
                            connection,
                            target_id,
                        ).updated_at,
                        target_before.updated_at,
                    )
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )
                    self.assertNotIn("文献を更新しました。", outputs)
                    self.assertNotIn("CLIを終了します。", outputs)
                    if stage == "update_error":
                        self.assertEqual(
                            sum(
                                item.startswith("文献編集エラー: ")
                                for item in outputs
                            ),
                            1,
                        )
                    else:
                        self.assertFalse(
                            any(
                                item.startswith("文献編集エラー: ")
                                for item in outputs
                            )
                        )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertEqual(
                        connection.in_transaction,
                        stage
                        in {
                            "initial_transaction",
                            "late_transaction",
                        },
                    )
                    self.assertEqual(
                        connection.execute("SELECT 1").fetchone()[0],
                        1,
                    )
                finally:
                    if stage == "late_transaction":
                        sqlite3.Connection.rollback(connection)
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM tags WHERE name = ?",
                                (marker_name,),
                            ).fetchone()[0],
                            0,
                        )
                        self.assertEqual(
                            connection.execute("SELECT 1").fetchone()[0],
                            1,
                        )
                        self.assertEqual(connection.commit_calls, 0)
                        self.assertEqual(connection.rollback_calls, 0)
                        self.assertEqual(connection.close_calls, 0)
                    elif connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

        for interruption_index, interruption_type in enumerate(
            (EOFError, KeyboardInterrupt)
        ):
            with self.subTest(
                case="current_literature",
                exception=interruption_type.__name__,
            ):
                connection, target_id, _ = (
                    self.create_tracking_edit_fixture(
                        f"output-interruption-{interruption_index}"
                    )
                )
                try:
                    before = self.table_snapshot_for(connection)
                    target_before = get_literature(
                        connection,
                        target_id,
                    )
                    failing_message = (
                        cli_module._format_edit_literature(target_before)
                    )
                    expected = interruption_type(
                        "current literature output interruption"
                    )
                    feeder = InputFeeder(["4", str(target_id)])
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with patch.object(
                        cli_module,
                        "update_literature",
                    ) as updated:
                        with self.assertRaises(
                            interruption_type
                        ) as raised:
                            run_cli(
                                connection,
                                input_func=feeder,
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    updated.assert_not_called()
                    self.assertEqual(len(feeder.prompts), 2)
                    self.assertEqual(
                        self.table_snapshot_for(connection),
                        before,
                    )
                    self.assertEqual(
                        get_literature(connection, target_id),
                        target_before,
                    )
                    self.assertEqual(
                        outputs.count(failing_message),
                        1,
                    )
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )
                    self.assertFalse(
                        any(
                            item.startswith("文献編集エラー: ")
                            for item in outputs
                        )
                    )
                    self.assertNotIn("CLIを終了します。", outputs)
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(
                        connection.execute("SELECT 1").fetchone()[0],
                        1,
                    )
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_edit_output_exceptions_before_update_propagate_unchanged(
        self,
    ) -> None:
        literature_id = self.add_record("Output exceptions")

        for expected in (
            RuntimeError("confirmation output"),
            EOFError("confirmation output EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                before = get_literature(self.connection, literature_id)
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == cli_module._EDIT_CONFIRMATION_MENU:
                        raise expected

                with patch.object(cli_module, "update_literature") as updated:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                [
                                    "4",
                                    str(literature_id),
                                    "2",
                                    "After",
                                ]
                            ),
                            output_func=output_func,
                        )
                self.assertIs(raised.exception, expected)
                updated.assert_not_called()
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                self.assertNotIn("CLIを終了します。", outputs)

    def test_edit_invalid_and_cancel_output_exceptions_propagate(
        self,
    ) -> None:
        literature_id = self.add_record(
            "Invalid and cancel output",
            authors="Before",
        )
        cases = (
            (
                ["4", str(literature_id), "29"],
                cli_module._INVALID_EDIT_FIELD_MESSAGE,
            ),
            (
                ["4", str(literature_id), "0"],
                "文献編集を中止しました。",
            ),
            (
                ["4", str(literature_id), "2", "After", "9"],
                cli_module._INVALID_CONFIRMATION_MESSAGE,
            ),
            (
                ["4", str(literature_id), "2", "After", "0"],
                "文献更新を中止しました。",
            ),
        )

        for actions, failing_message in cases:
            for expected in (
                RuntimeError("edit output runtime"),
                EOFError("edit output EOF"),
                KeyboardInterrupt(),
            ):
                with self.subTest(
                    failing_message=failing_message,
                    exception=type(expected).__name__,
                ):
                    before = get_literature(
                        self.connection,
                        literature_id,
                    )
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with patch.object(
                        cli_module,
                        "update_literature",
                    ) as updated:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )
                    self.assertIs(raised.exception, expected)
                    updated.assert_not_called()
                    self.assertEqual(
                        get_literature(
                            self.connection,
                            literature_id,
                        ),
                        before,
                    )
                    self.assertEqual(outputs.count(failing_message), 1)

    def test_edit_rejects_active_transaction_before_id_or_repository_api(
        self,
    ) -> None:
        pending_cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending before edit",),
        )
        pending_id = pending_cursor.lastrowid
        self.assertTrue(self.connection.in_transaction)

        with (
            patch.object(cli_module, "get_literature") as retrieved,
            patch.object(cli_module, "update_literature") as updated,
        ):
            _, feeder, outputs = self.run_with_actions(["4", "0"])

        retrieved.assert_not_called()
        updated.assert_not_called()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature WHERE id = ?",
                (pending_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            feeder.prompts,
            ["選択してください: ", "選択してください: "],
        )
        self.assertIn(cli_module._EDIT_ACTIVE_TRANSACTION_MESSAGE, outputs)
        self.connection.rollback()

    def test_edit_rechecks_transaction_immediately_before_update(self) -> None:
        literature_id = self.add_record(
            "Late edit transaction",
            authors="Before",
        )
        feeder = InputFeeder(
            ["4", str(literature_id), "2", "After", "1", "0"]
        )
        pending_ids: list[int] = []

        def begin_before_confirmation_returns(prompt: str) -> str:
            value = feeder(prompt)
            if (
                prompt == "選択してください: "
                and value == "1"
                and len(feeder.prompts) == 5
            ):
                cursor = self.connection.execute(
                    "INSERT INTO literature (title) VALUES (?)",
                    ("Pending during edit confirmation",),
                )
                pending_ids.append(cursor.lastrowid)
            return value

        outputs: list[str] = []
        with patch.object(cli_module, "update_literature") as updated:
            result = run_cli(
                self.connection,
                input_func=begin_before_confirmation_returns,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        updated.assert_not_called()
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            get_literature(self.connection, literature_id).authors,
            "Before",
        )
        self.assertEqual(len(pending_ids), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature WHERE id = ?",
                (pending_ids[0],),
            ).fetchone()[0],
            1,
        )
        self.assertIn(cli_module._EDIT_ACTIVE_TRANSACTION_MESSAGE, outputs)
        self.connection.rollback()

    def test_edit_transaction_rejection_calls_no_lifecycle_method(self) -> None:
        tracking_path = self.directory / "edit-transaction-tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending lifecycle edit",),
        )
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0

        _, _, outputs = self.run_with_actions(
            ["4", "0"],
            connection=connection,
        )

        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertTrue(connection.in_transaction)
        self.assertIn(cli_module._EDIT_ACTIVE_TRANSACTION_MESSAGE, outputs)
        sqlite3.Connection.rollback(connection)

    def test_edit_get_api_exception_boundaries(self) -> None:
        api_exceptions = (
            sqlite3.OperationalError("get sqlite"),
            ValueError("get value"),
            RuntimeError("get runtime"),
            EOFError("get EOF"),
            KeyboardInterrupt(),
        )

        for expected in api_exceptions:
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []
                with (
                    patch.object(
                        cli_module,
                        "get_literature",
                        side_effect=expected,
                    ),
                    patch.object(cli_module, "update_literature") as updated,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(["4", "1"]),
                            output_func=outputs.append,
                        )
                self.assertIs(raised.exception, expected)
                updated.assert_not_called()
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(
                            "データベースエラーが発生しました。"
                        ),
                        1,
                    )
                else:
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )

    def test_edit_update_api_exception_boundaries(self) -> None:
        literature_id = self.add_record("Update API exceptions")
        api_exceptions = (
            ValueError("update value"),
            sqlite3.OperationalError("update sqlite"),
            RuntimeError("update runtime"),
            EOFError("update EOF"),
            KeyboardInterrupt(),
        )

        for expected in api_exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = get_literature(self.connection, literature_id)
                outputs: list[str] = []
                actions: list[object] = [
                    "4",
                    str(literature_id),
                    "2",
                    "After",
                    "1",
                ]
                if isinstance(expected, ValueError):
                    actions.append("0")
                with patch.object(
                    cli_module,
                    "update_literature",
                    side_effect=expected,
                ):
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("文献編集エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                self.assertEqual(
                    get_literature(self.connection, literature_id),
                    before,
                )
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(
                            "データベースエラーが発生しました。"
                        ),
                        1,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(
                        "データベースエラーが発生しました。",
                        outputs,
                    )

    def test_edit_database_error_output_failure_propagates_output_error(
        self,
    ) -> None:
        literature_id = self.add_record("DB error output edit")
        database_error = sqlite3.OperationalError("update database")
        output_error = RuntimeError("database error output")

        def output_func(message: str) -> None:
            if message == "データベースエラーが発生しました。":
                raise output_error

        with patch.object(
            cli_module,
            "update_literature",
            side_effect=database_error,
        ):
            with self.assertRaises(RuntimeError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(
                        [
                            "4",
                            str(literature_id),
                            "2",
                            "After",
                            "1",
                        ]
                    ),
                    output_func=output_func,
                )

        self.assertIs(raised.exception, output_error)

        get_output_error = RuntimeError("get database error output")
        outputs: list[str] = []

        def get_output_func(message: str) -> None:
            outputs.append(message)
            if message == "データベースエラーが発生しました。":
                raise get_output_error

        with patch.object(
            cli_module,
            "get_literature",
            side_effect=database_error,
        ):
            with self.assertRaises(RuntimeError) as get_raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(["4", str(literature_id)]),
                    output_func=get_output_func,
                )

        self.assertIs(get_raised.exception, get_output_error)
        self.assertEqual(
            outputs.count("データベースエラーが発生しました。"),
            1,
        )

    def test_edit_update_false_reports_disappeared_without_success(self) -> None:
        literature_id = self.add_record("Disappearing edit")
        before = get_literature(self.connection, literature_id)

        with patch.object(
            cli_module,
            "update_literature",
            return_value=False,
        ) as updated:
            _, _, outputs = self.run_with_actions(
                self.edit_actions(literature_id, 2, "After")
            )

        updated.assert_called_once_with(
            self.connection,
            literature_id,
            {"authors": "After"},
        )
        self.assertEqual(
            get_literature(self.connection, literature_id),
            before,
        )
        self.assertIn("確認後に対象文献が存在しなくなりました。", outputs)
        self.assertNotIn("文献を更新しました。", outputs)

    def test_real_sqlite_edit_failure_rolls_back_and_rethrows_same_error(
        self,
    ) -> None:
        tracking_path = self.directory / "edit-failure-tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        literature_id = add_literature(
            connection,
            Literature(
                title="Forced update failure",
                authors="Before",
            ),
        )
        other_id = add_literature(
            connection,
            Literature(title="Other preserved record"),
        )
        connection.execute(
            """
            CREATE TRIGGER reject_forced_cli_update
            BEFORE UPDATE ON literature
            WHEN OLD.id = 1
            BEGIN
                SELECT RAISE(ABORT, 'forced update failure');
            END
            """
        )
        connection.commit()
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        before = get_literature(connection, literature_id)
        other_before = get_literature(connection, other_id)
        api_errors: list[sqlite3.Error] = []
        outputs: list[str] = []

        def tracked_update(
            target_connection: sqlite3.Connection,
            target_id: int,
            updates: dict[str, object],
        ) -> bool:
            try:
                return update_literature(
                    target_connection,
                    target_id,
                    updates,
                )
            except sqlite3.Error as error:
                api_errors.append(error)
                raise

        with patch.object(
            cli_module,
            "update_literature",
            side_effect=tracked_update,
        ) as updated:
            with self.assertRaises(sqlite3.Error) as raised:
                run_cli(
                    connection,
                    input_func=InputFeeder(
                        [
                            "4",
                            str(literature_id),
                            "2",
                            "After",
                            "1",
                        ]
                    ),
                    output_func=outputs.append,
                )

        updated.assert_called_once()
        self.assertEqual(len(api_errors), 1)
        self.assertIs(raised.exception, api_errors[0])
        self.assertEqual(get_literature(connection, literature_id), before)
        self.assertEqual(get_literature(connection, other_id), other_before)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertEqual(
            outputs.count("データベースエラーが発生しました。"),
            1,
        )

    def test_edit_success_output_failure_keeps_committed_single_update(
        self,
    ) -> None:
        tracking_path = self.directory / "edit-success-output.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, connection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        literature_id = add_literature(
            connection,
            Literature(
                title="Committed edit before output failure",
                authors="Before",
                journal="Unchanged",
            ),
        )
        before = get_literature(connection, literature_id)
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        expected = RuntimeError("edit success output failure")
        outputs: list[str] = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == "文献を更新しました。":
                raise expected

        with patch.object(
            cli_module,
            "update_literature",
            wraps=update_literature,
        ) as updated:
            with self.assertRaises(RuntimeError) as raised:
                run_cli(
                    connection,
                    input_func=InputFeeder(
                        [
                            "4",
                            str(literature_id),
                            "2",
                            "After",
                            "1",
                        ]
                    ),
                    output_func=output_func,
                )

        self.assertIs(raised.exception, expected)
        updated.assert_called_once_with(
            connection,
            literature_id,
            {"authors": "After"},
        )
        after = get_literature(connection, literature_id)
        self.assertEqual(after.authors, "After")
        self.assertEqual(after.journal, before.journal)
        self.assertEqual(after.created_at, before.created_at)
        self.assertGreater(
            datetime.fromisoformat(after.updated_at.replace("Z", "+00:00")),
            datetime.fromisoformat(before.updated_at.replace("Z", "+00:00")),
        )
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertEqual(outputs.count("文献を更新しました。"), 1)
        self.assertFalse(
            any(item.startswith("文献編集エラー: ") for item in outputs)
        )
        self.assertNotIn("データベースエラーが発生しました。", outputs)
        self.assertNotIn("CLIを終了します。", outputs)

    def test_edit_success_preserves_related_data_schema_and_other_records(
        self,
    ) -> None:
        target_id = self.add_record(
            "Safe target edit",
            authors="Before",
            journal="Unchanged",
        )
        other_id = self.add_record(
            "Safe other record",
            authors="Other",
        )
        tag_id = create_tag(self.connection, "edit-safe-tag")
        attach_tag_to_literature(self.connection, target_id, tag_id)
        usage_id = create_usage_history(
            self.connection,
            target_id,
            "edit-safe-use",
        )
        self.connection.execute("PRAGMA user_version = 82")
        target_before = get_literature(self.connection, target_id)
        other_before = get_literature(self.connection, other_id)
        related_before = {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1, 2"
                ).fetchall()
            ]
            for table in ("tags", "literature_tags", "usage_history")
        }
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        self.run_with_actions(
            self.edit_actions(target_id, 2, "After")
        )

        target_after = get_literature(self.connection, target_id)
        self.assertEqual(target_after.authors, "After")
        self.assertEqual(target_after.journal, target_before.journal)
        self.assertEqual(target_after.created_at, target_before.created_at)
        self.assertGreater(
            datetime.fromisoformat(
                target_after.updated_at.replace("Z", "+00:00")
            ),
            datetime.fromisoformat(
                target_before.updated_at.replace("Z", "+00:00")
            ),
        )
        self.assertEqual(
            get_literature(self.connection, other_id),
            other_before,
        )
        self.assertEqual(
            {
                table: [
                    tuple(row)
                    for row in self.connection.execute(
                        f"SELECT * FROM {table} ORDER BY 1, 2"
                    ).fetchall()
                ]
                for table in ("tags", "literature_tags", "usage_history")
            },
            related_before,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT id FROM tags WHERE id = ?",
                (tag_id,),
            ).fetchone()[0],
            tag_id,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT id FROM usage_history WHERE id = ?",
                (usage_id,),
            ).fetchone()[0],
            usage_id,
        )
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_delete_success_uses_repository_apis_and_isolated_cascades(
        self,
    ) -> None:
        external_pdf = self.directory / "synthetic external.pdf"
        external_pdf.write_text("must remain", encoding="utf-8")
        target_id = self.add_record(
            "Delete target",
            authors="Target Author",
            journal="Target Journal",
            pdf_path=str(external_pdf),
            general_note="Target note",
        )
        other_id = self.add_record(
            "Delete other",
            authors="Other Author",
            general_note="Other note",
        )
        shared_tag_id = create_tag(self.connection, "shared-delete")
        target_tag_id = create_tag(self.connection, "target-only-delete")
        other_tag_id = create_tag(self.connection, "other-only-delete")
        for literature_id, tag_id in (
            (target_id, shared_tag_id),
            (target_id, target_tag_id),
            (other_id, shared_tag_id),
            (other_id, other_tag_id),
        ):
            attach_tag_to_literature(
                self.connection,
                literature_id,
                tag_id,
            )
        create_usage_history(self.connection, target_id, "target-use-1")
        create_usage_history(self.connection, target_id, "target-use-2")
        create_usage_history(self.connection, other_id, "other-use")
        self.connection.execute("PRAGMA user_version = 83")
        target_before = get_literature(self.connection, target_id)
        other_before = get_literature(self.connection, other_id)
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "get_literature_related_counts",
                wraps=get_literature_related_counts,
            ) as counted,
            patch.object(
                cli_module,
                "delete_literature",
                wraps=delete_literature,
            ) as deleted,
        ):
            _, feeder, outputs = self.run_with_actions(
                self.delete_actions(target_id)
            )

        retrieved.assert_called_once_with(self.connection, target_id)
        counted.assert_called_once_with(self.connection, target_id)
        deleted.assert_called_once_with(self.connection, target_id)
        self.assertIsNone(get_literature(self.connection, target_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature_tags WHERE literature_id = ?",
                (target_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
                (target_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(get_literature(self.connection, other_id), other_before)
        self.assertEqual(
            {
                row["tag_id"]
                for row in self.connection.execute(
                    """
                    SELECT tag_id
                    FROM literature_tags
                    WHERE literature_id = ?
                    """,
                    (other_id,),
                ).fetchall()
            },
            {shared_tag_id, other_tag_id},
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
                (other_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            {
                row["id"]
                for row in self.connection.execute(
                    "SELECT id FROM tags"
                ).fetchall()
            },
            {shared_tag_id, target_tag_id, other_tag_id},
        )
        self.assertTrue(external_pdf.is_file())
        self.assertEqual(
            external_pdf.read_text(encoding="utf-8"),
            "must remain",
        )
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(
            feeder.prompts,
            [
                "選択してください: ",
                "文献ID（ASCII数字）: ",
                "選択してください: ",
                (
                    f"削除を確定するため文献ID {target_id} "
                    "を再入力してください\n（0で中止）: "
                ),
                "選択してください: ",
            ],
        )
        displayed = "\n".join(outputs)
        self.assertIn(cli_module._format_edit_literature(target_before), outputs)
        for expected in (
            "削除対象と影響を確認してください。",
            f"ID: {target_id}",
            "title: Delete target",
            "タグ関連付け数: 2",
            "使用履歴数: 2",
            "関連件数は確認時点の値です。",
            "文献レコードは削除されます。",
            "タグとの関連付けは削除されます。",
            "使用履歴は削除されます。",
            "タグレコード自体は残ります。",
            "pdf_pathが示す外部ファイルは削除されません。",
            "CLIには自動復元機能がありません。",
            "文献を削除しました。",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, displayed)
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            2,
        )

    def test_delete_displays_all_31_saved_fields_without_mutation_on_cancel(
        self,
    ) -> None:
        literature_id = self.add_record(
            '削除表示 "Full", Study',
            authors='Author A, "Author B"\nAuthor C',
            journal="Journal 内部  空白",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="101-112",
            doi="10.1000/delete-display",
            pmid="00123",
            url="https://example.test/delete",
            language="日本語 / English",
            publication_type="原著",
            abstract='Abstract, "quoted"\nsecond line',
            pdf_path="/tmp/delete literature.pdf",
            personal_summary="自分の要約",
            ai_summary="AI要約\n未確認本文",
            ai_summary_status="修正済み",
            general_note="一般メモ",
            key_findings="主要な結果",
            methods_note="方法メモ",
            clinical_note="臨床メモ",
            limitation_note="限界メモ",
            relevance_note="関連メモ",
            evidence_level="Level II",
            verification_status="要確認",
            adoption_status="採用候補",
            exclusion_reason="除外理由",
            rating=4,
        )
        self.connection.execute(
            "UPDATE literature SET doi = ?, pmid = ? WHERE id = ?",
            (
                " DOI:10.1000/Mixed Case ",
                " PMID: 001 23 ",
                literature_id,
            ),
        )
        self.connection.commit()
        before_record = get_literature(self.connection, literature_id)
        before_tables = self.table_snapshot()

        with patch.object(cli_module, "delete_literature") as deleted:
            _, _, outputs = self.run_with_actions(
                [
                    "5",
                    str(literature_id),
                    "0",
                    "0",
                ]
            )

        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before_tables)
        self.assertEqual(
            get_literature(self.connection, literature_id),
            before_record,
        )
        displayed = next(item for item in outputs if item.startswith("id: "))
        expected_fields = (
            "id",
            *_REGISTRATION_FIELDS,
            "created_at",
            "updated_at",
        )
        positions = [
            displayed.index(f"{'' if index == 0 else chr(10)}{field}: ")
            for index, field in enumerate(expected_fields)
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(
            displayed,
            cli_module._format_edit_literature(before_record),
        )
        self.assertIn(" DOI:10.1000/Mixed Case ", displayed)
        self.assertIn(" PMID: 001 23 ", displayed)
        self.assertIn("文献削除を中止しました。", outputs)

        null_id = self.add_record("Delete NULL display")
        null_before = get_literature(self.connection, null_id)
        _, _, null_outputs = self.run_with_actions(
            ["5", str(null_id), "0", "0"]
        )
        null_displayed = cli_module._format_edit_literature(null_before)
        self.assertIn(null_displayed, null_outputs)
        self.assertIn("authors: 未登録", null_displayed)
        self.assertIn("rating: 未登録", null_displayed)
        self.assertNotIn("authors: None", null_displayed)

    def test_delete_id_validation_and_existing_maximum_id_contract(
        self,
    ) -> None:
        self.add_record("First delete ID")
        maximum_id = self.add_record("Maximum delete ID")
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                before = self.table_snapshot()
                with (
                    patch.object(cli_module, "get_literature") as retrieved,
                    patch.object(
                        cli_module,
                        "get_literature_related_counts",
                    ) as counted,
                    patch.object(cli_module, "delete_literature") as deleted,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["5", invalid_value, "0"]
                    )

                retrieved.assert_not_called()
                counted.assert_not_called()
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and "文献ID" in item
                        and "ASCII" in item
                        for item in outputs
                    )
                )

        first_before = get_literature(self.connection, 1)
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "delete_literature",
                wraps=delete_literature,
            ) as deleted,
        ):
            _, _, outputs = self.run_with_actions(
                [
                    "5",
                    f" \t00{maximum_id}\n ",
                    " 1 ",
                    f" 000{maximum_id} ",
                    "0",
                ]
            )

        retrieved.assert_called_once_with(self.connection, maximum_id)
        deleted.assert_called_once_with(self.connection, maximum_id)
        self.assertIsNone(get_literature(self.connection, maximum_id))
        self.assertEqual(get_literature(self.connection, 1), first_before)
        self.assertIn("文献を削除しました。", outputs)

    def test_delete_unknown_id_and_related_count_disappearance_stop_safely(
        self,
    ) -> None:
        before = self.table_snapshot()
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "get_literature_related_counts",
            ) as counted,
            patch.object(cli_module, "delete_literature") as deleted,
        ):
            _, feeder, outputs = self.run_with_actions(["5", "999999", "0"])

        retrieved.assert_called_once_with(self.connection, 999999)
        counted.assert_not_called()
        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("対象文献が見つかりません。", outputs)
        self.assertEqual(len(feeder.prompts), 3)

        literature_id = self.add_record("Counts disappeared")
        record_before = get_literature(self.connection, literature_id)
        with (
            patch.object(
                cli_module,
                "get_literature_related_counts",
                return_value=None,
            ) as counted,
            patch.object(cli_module, "delete_literature") as deleted,
        ):
            _, feeder, outputs = self.run_with_actions(
                ["5", str(literature_id), "0"]
            )

        counted.assert_called_once_with(self.connection, literature_id)
        deleted.assert_not_called()
        self.assertEqual(
            get_literature(self.connection, literature_id),
            record_before,
        )
        self.assertIn(
            "現在の文献情報を表示した後に対象文献が存在しなくなりました。",
            outputs,
        )
        self.assertEqual(len(feeder.prompts), 3)

    def test_delete_related_count_combinations_use_api_values_at_confirmation(
        self,
    ) -> None:
        combinations = ((0, 0), (2, 0), (0, 2), (2, 3))

        for index, (tag_count, usage_count) in enumerate(combinations):
            with self.subTest(
                tag_count=tag_count,
                usage_count=usage_count,
            ):
                literature_id = self.add_record(f"Related counts {index}")
                for tag_index in range(tag_count):
                    tag_id = create_tag(
                        self.connection,
                        f"delete-count-{index}-{tag_index}",
                    )
                    attach_tag_to_literature(
                        self.connection,
                        literature_id,
                        tag_id,
                    )
                for history_index in range(usage_count):
                    create_usage_history(
                        self.connection,
                        literature_id,
                        f"delete-count-use-{index}-{history_index}",
                    )
                before = self.table_snapshot()

                with (
                    patch.object(
                        cli_module,
                        "get_literature_related_counts",
                        wraps=get_literature_related_counts,
                    ) as counted,
                    patch.object(cli_module, "delete_literature") as deleted,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["5", str(literature_id), "0", "0"]
                    )

                counted.assert_called_once_with(
                    self.connection,
                    literature_id,
                )
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                displayed = "\n".join(outputs)
                self.assertIn(
                    f"タグ関連付け数: {tag_count}",
                    displayed,
                )
                self.assertIn(
                    f"使用履歴数: {usage_count}",
                    displayed,
                )
                self.assertIn("関連件数は確認時点の値です。", outputs)

        sentinel_id = self.add_record("Unmodified API count values")
        with (
            patch.object(
                cli_module,
                "get_literature_related_counts",
                return_value={
                    "tag_count": -2,
                    "usage_history_count": -3,
                },
            ),
            patch.object(cli_module, "delete_literature") as deleted,
        ):
            _, _, outputs = self.run_with_actions(
                ["5", str(sentinel_id), "0", "0"]
            )
        deleted.assert_not_called()
        self.assertIn("タグ関連付け数: -2", outputs)
        self.assertIn("使用履歴数: -3", outputs)

    def test_delete_confirmation_loops_cancel_and_final_id_contract(
        self,
    ) -> None:
        literature_id = self.add_record("Delete confirmation loops")
        before = self.table_snapshot()
        invalid_count = 1200
        with patch.object(cli_module, "delete_literature") as deleted:
            _, feeder, outputs = self.run_with_actions(
                [
                    "5",
                    str(literature_id),
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " \t0\n ",
                    "0",
                ]
            )

        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("文献削除を中止しました。", outputs)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 5,
        )

        final_invalid_values = (
            "",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
            str(literature_id + 100),
        )
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "delete_literature",
                wraps=delete_literature,
            ) as deleted,
        ):
            _, _, outputs = self.run_with_actions(
                [
                    "5",
                    str(literature_id),
                    "1",
                    *final_invalid_values,
                    *(["invalid"] * invalid_count),
                    f" 000{literature_id} ",
                    "0",
                ]
            )

        retrieved.assert_called_once_with(self.connection, literature_id)
        deleted.assert_called_once_with(self.connection, literature_id)
        self.assertIsNone(get_literature(self.connection, literature_id))
        final_error = (
            f"入力エラー: 文献ID {literature_id} または0を入力してください。"
        )
        self.assertEqual(
            outputs.count(final_error),
            len(final_invalid_values) + invalid_count,
        )

        cancelled_id = self.add_record("Final ID cancel")
        other_id = self.add_record("Must not become delete target")
        cancelled_before = self.table_snapshot()
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(cli_module, "delete_literature") as deleted,
        ):
            _, _, outputs = self.run_with_actions(
                [
                    "5",
                    str(cancelled_id),
                    "1",
                    str(other_id),
                    " 0 ",
                    "0",
                ]
            )
        retrieved.assert_called_once_with(self.connection, cancelled_id)
        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), cancelled_before)
        self.assertIn("文献削除を中止しました。", outputs)

    def test_delete_input_exception_matrix_preserves_state_and_boundaries(
        self,
    ) -> None:
        positions = (
            (
                "literature_id",
                lambda literature_id: ["5"],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                ),
            ),
            (
                "first_confirmation",
                lambda literature_id: ["5", str(literature_id)],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                    "選択してください: ",
                ),
            ),
            (
                "final_id_confirmation",
                lambda literature_id: [
                    "5",
                    str(literature_id),
                    "1",
                ],
                (
                    "選択してください: ",
                    "文献ID（ASCII数字）: ",
                    "選択してください: ",
                ),
            ),
        )
        exception_types = (
            ("EOFError", EOFError),
            ("KeyboardInterrupt", KeyboardInterrupt),
            ("ValueError", ValueError),
            ("RuntimeError", RuntimeError),
            ("sqlite3.Error", sqlite3.Error),
        )

        for position_index, (
            position,
            action_prefix,
            expected_prompt_prefix,
        ) in enumerate(positions):
            for exception_index, (
                exception_name,
                exception_type,
            ) in enumerate(exception_types):
                with self.subTest(
                    position=position,
                    exception=exception_name,
                ):
                    connection, target_id, other_id = (
                        self.create_tracking_delete_fixture(
                            f"input-{position_index}-{exception_index}"
                        )
                    )
                    try:
                        before = self.table_snapshot_for(connection)
                        target_before = get_literature(connection, target_id)
                        other_before = get_literature(connection, other_id)
                        expected = exception_type(
                            f"{position} {exception_name} input failure"
                        )
                        feeder = InputFeeder(
                            [*action_prefix(target_id), expected]
                        )
                        outputs: list[str] = []

                        with patch.object(
                            cli_module,
                            "delete_literature",
                        ) as deleted:
                            if isinstance(
                                expected,
                                (EOFError, KeyboardInterrupt),
                            ):
                                result = run_cli(
                                    connection,
                                    input_func=feeder,
                                    output_func=outputs.append,
                                )
                                self.assertIsNone(result)
                                self.assertEqual(
                                    outputs.count("CLIを終了します。"),
                                    1,
                                )
                            else:
                                with self.assertRaises(
                                    exception_type
                                ) as raised:
                                    run_cli(
                                        connection,
                                        input_func=feeder,
                                        output_func=outputs.append,
                                    )
                                self.assertIs(raised.exception, expected)
                                self.assertNotIn(
                                    "CLIを終了します。",
                                    outputs,
                                )

                        deleted.assert_not_called()
                        self.assertEqual(
                            feeder.prompts[: len(expected_prompt_prefix)],
                            list(expected_prompt_prefix),
                        )
                        if position == "final_id_confirmation":
                            self.assertIn(
                                (
                                    f"削除を確定するため文献ID {target_id} "
                                    "を再入力してください\n（0で中止）: "
                                ),
                                feeder.prompts,
                            )
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                        self.assertEqual(
                            get_literature(connection, target_id),
                            target_before,
                        )
                        self.assertEqual(
                            get_literature(connection, other_id),
                            other_before,
                        )
                        self.assertFalse(
                            any(
                                item.startswith("入力エラー: ")
                                for item in outputs
                            )
                        )
                        self.assertNotIn(
                            "データベースエラーが発生しました。",
                            outputs,
                        )
                        self.assertEqual(connection.commit_calls, 0)
                        self.assertEqual(connection.rollback_calls, 0)
                        self.assertEqual(connection.close_calls, 0)
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.execute("SELECT 1").fetchone()[0],
                            1,
                        )
                    finally:
                        if connection.in_transaction:
                            sqlite3.Connection.rollback(connection)
                        sqlite3.Connection.close(connection)

    def test_delete_rejects_initial_transaction_without_input_or_apis(
        self,
    ) -> None:
        connection, target_id, other_id = (
            self.create_tracking_delete_fixture("initial-transaction")
        )
        try:
            marker_cursor = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("pending-delete-initial-marker",),
            )
            marker_id = marker_cursor.lastrowid
            target_before = get_literature(connection, target_id)
            other_before = get_literature(connection, other_id)
            self.assertTrue(connection.in_transaction)
            feeder = InputFeeder(["5", "0"])
            outputs: list[str] = []

            with (
                patch.object(cli_module, "get_literature") as retrieved,
                patch.object(
                    cli_module,
                    "get_literature_related_counts",
                ) as counted,
                patch.object(cli_module, "delete_literature") as deleted,
            ):
                result = run_cli(
                    connection,
                    input_func=feeder,
                    output_func=outputs.append,
                )

            self.assertIsNone(result)
            retrieved.assert_not_called()
            counted.assert_not_called()
            deleted.assert_not_called()
            self.assertEqual(
                feeder.prompts,
                ["選択してください: ", "選択してください: "],
            )
            self.assertIn(
                cli_module._DELETE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                get_literature(connection, target_id),
                target_before,
            )
            self.assertEqual(
                get_literature(connection, other_id),
                other_before,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_delete_rechecks_transaction_with_pending_marker_before_write(
        self,
    ) -> None:
        connection, target_id, other_id = (
            self.create_tracking_delete_fixture("late-transaction")
        )
        try:
            before = self.table_snapshot_for(connection)
            target_before = get_literature(connection, target_id)
            other_before = get_literature(connection, other_id)
            feeder = InputFeeder(
                ["5", str(target_id), "1", str(target_id), "0"]
            )
            marker_ids: list[int] = []

            def input_func(prompt: str) -> str:
                value = feeder(prompt)
                if prompt.startswith("削除を確定するため文献ID"):
                    self.assertFalse(connection.in_transaction)
                    cursor = connection.execute(
                        "INSERT INTO tags (name) VALUES (?)",
                        ("pending-delete-late-marker",),
                    )
                    marker_ids.append(cursor.lastrowid)
                    self.assertTrue(connection.in_transaction)
                return value

            outputs: list[str] = []
            with patch.object(cli_module, "delete_literature") as deleted:
                result = run_cli(
                    connection,
                    input_func=input_func,
                    output_func=outputs.append,
                )

            self.assertIsNone(result)
            deleted.assert_not_called()
            self.assertEqual(len(marker_ids), 1)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_ids[0],),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                get_literature(connection, target_id),
                target_before,
            )
            self.assertEqual(
                get_literature(connection, other_id),
                other_before,
            )
            self.assertIn(
                cli_module._DELETE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)

            sqlite3.Connection.rollback(connection)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_ids[0],),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(self.table_snapshot_for(connection), before)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_delete_repository_api_exception_boundaries(self) -> None:
        api_names = (
            "get_literature",
            "get_literature_related_counts",
            "delete_literature",
        )
        exception_types = (
            sqlite3.OperationalError,
            RuntimeError,
            ValueError,
            EOFError,
            KeyboardInterrupt,
        )

        for api_index, api_name in enumerate(api_names):
            for exception_index, exception_type in enumerate(exception_types):
                with self.subTest(
                    api=api_name,
                    exception=exception_type.__name__,
                ):
                    connection, target_id, other_id = (
                        self.create_tracking_delete_fixture(
                            f"api-{api_index}-{exception_index}"
                        )
                    )
                    try:
                        before = self.table_snapshot_for(connection)
                        expected = exception_type(
                            f"{api_name} {exception_type.__name__}"
                        )
                        actions: list[object] = ["5", str(target_id)]
                        if api_name == "delete_literature":
                            actions.extend(["1", str(target_id)])
                        outputs: list[str] = []

                        with patch.object(
                            cli_module,
                            api_name,
                            side_effect=expected,
                        ) as failed_api:
                            with self.assertRaises(
                                exception_type
                            ) as raised:
                                run_cli(
                                    connection,
                                    input_func=InputFeeder(actions),
                                    output_func=outputs.append,
                                )

                        self.assertIs(raised.exception, expected)
                        failed_api.assert_called_once()
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                        self.assertIsNotNone(
                            get_literature(connection, target_id)
                        )
                        self.assertIsNotNone(
                            get_literature(connection, other_id)
                        )
                        if isinstance(expected, sqlite3.Error):
                            self.assertEqual(
                                outputs.count(
                                    "データベースエラーが発生しました。"
                                ),
                                1,
                            )
                        else:
                            self.assertNotIn(
                                "データベースエラーが発生しました。",
                                outputs,
                            )
                        self.assertNotIn("CLIを終了します。", outputs)
                        self.assertEqual(connection.commit_calls, 0)
                        self.assertEqual(connection.rollback_calls, 0)
                        self.assertEqual(connection.close_calls, 0)
                        self.assertFalse(connection.in_transaction)
                        self.assertEqual(
                            connection.execute("SELECT 1").fetchone()[0],
                            1,
                        )
                    finally:
                        if connection.in_transaction:
                            sqlite3.Connection.rollback(connection)
                        sqlite3.Connection.close(connection)

    def test_delete_database_error_output_failure_propagates_output_error(
        self,
    ) -> None:
        for api_name in (
            "get_literature",
            "get_literature_related_counts",
            "delete_literature",
        ):
            with self.subTest(api=api_name):
                literature_id = self.add_record(f"DB output {api_name}")
                database_error = sqlite3.OperationalError(
                    f"{api_name} database error"
                )
                output_error = RuntimeError(
                    f"{api_name} database output error"
                )
                actions: list[object] = ["5", str(literature_id)]
                if api_name == "delete_literature":
                    actions.extend(["1", str(literature_id)])

                def output_func(message: str) -> None:
                    if message == "データベースエラーが発生しました。":
                        raise output_error

                with patch.object(
                    cli_module,
                    api_name,
                    side_effect=database_error,
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, output_error)
                self.assertIsNot(raised.exception, database_error)
                self.assertIsNotNone(
                    get_literature(self.connection, literature_id)
                )

    def test_delete_false_reports_disappearance_once_without_retry(
        self,
    ) -> None:
        literature_id = self.add_record("Delete false")
        before = self.table_snapshot()

        with patch.object(
            cli_module,
            "delete_literature",
            return_value=False,
        ) as deleted:
            _, _, outputs = self.run_with_actions(
                self.delete_actions(literature_id)
            )

        deleted.assert_called_once_with(self.connection, literature_id)
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("確認後に対象文献が存在しなくなりました。", outputs)
        self.assertNotIn("文献を削除しました。", outputs)
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            2,
        )

    def test_real_sqlite_delete_failure_rolls_back_and_rethrows_same_error(
        self,
    ) -> None:
        connection, target_id, other_id = (
            self.create_tracking_delete_fixture("real-sqlite-failure")
        )
        try:
            connection.execute(
                """
                CREATE TRIGGER reject_forced_cli_delete
                BEFORE DELETE ON literature
                WHEN OLD.title = 'Delete exception matrix target'
                BEGIN
                    SELECT RAISE(ABORT, 'forced delete failure');
                END
                """
            )
            connection.commit()
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            before = self.table_snapshot_for(connection)
            target_before = get_literature(connection, target_id)
            other_before = get_literature(connection, other_id)
            api_errors: list[sqlite3.Error] = []
            outputs: list[str] = []

            def tracked_delete(
                target_connection: sqlite3.Connection,
                literature_id: int,
            ) -> bool:
                try:
                    return delete_literature(
                        target_connection,
                        literature_id,
                    )
                except sqlite3.Error as error:
                    api_errors.append(error)
                    raise

            with patch.object(
                cli_module,
                "delete_literature",
                side_effect=tracked_delete,
            ) as deleted:
                with self.assertRaises(sqlite3.Error) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            [
                                "5",
                                str(target_id),
                                "1",
                                str(target_id),
                            ]
                        ),
                        output_func=outputs.append,
                    )

            deleted.assert_called_once_with(connection, target_id)
            self.assertEqual(len(api_errors), 1)
            self.assertIs(raised.exception, api_errors[0])
            self.assertEqual(self.table_snapshot_for(connection), before)
            self.assertEqual(
                get_literature(connection, target_id),
                target_before,
            )
            self.assertEqual(
                get_literature(connection, other_id),
                other_before,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature_tags "
                    "WHERE literature_id = ?",
                    (target_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM usage_history "
                    "WHERE literature_id = ?",
                    (target_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
                2,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(
                outputs.count("データベースエラーが発生しました。"),
                1,
            )
            self.assertNotIn("文献を削除しました。", outputs)
            self.assertNotIn("CLIを終了します。", outputs)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_delete_success_output_failure_keeps_committed_cascade(
        self,
    ) -> None:
        connection, target_id, other_id = (
            self.create_tracking_delete_fixture("success-output")
        )
        try:
            target_tag_ids = {
                row["tag_id"]
                for row in connection.execute(
                    """
                    SELECT tag_id
                    FROM literature_tags
                    WHERE literature_id = ?
                    """,
                    (target_id,),
                ).fetchall()
            }
            other_before = get_literature(connection, other_id)
            other_related_before = {
                "tags": [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT literature_id, tag_id
                        FROM literature_tags
                        WHERE literature_id = ?
                        ORDER BY tag_id
                        """,
                        (other_id,),
                    ).fetchall()
                ],
                "usage": [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT *
                        FROM usage_history
                        WHERE literature_id = ?
                        ORDER BY id
                        """,
                        (other_id,),
                    ).fetchall()
                ],
            }
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            expected = RuntimeError("delete success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "文献を削除しました。":
                    raise expected

            with patch.object(
                cli_module,
                "delete_literature",
                wraps=delete_literature,
            ) as deleted:
                with self.assertRaises(RuntimeError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            [
                                "5",
                                str(target_id),
                                "1",
                                str(target_id),
                            ]
                        ),
                        output_func=output_func,
                    )

            self.assertIs(raised.exception, expected)
            deleted.assert_called_once_with(connection, target_id)
            self.assertIsNone(get_literature(connection, target_id))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature_tags "
                    "WHERE literature_id = ?",
                    (target_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM usage_history "
                    "WHERE literature_id = ?",
                    (target_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM tags"
                    ).fetchall()
                },
                target_tag_ids,
            )
            self.assertEqual(
                get_literature(connection, other_id),
                other_before,
            )
            self.assertEqual(
                {
                    "tags": [
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT literature_id, tag_id
                            FROM literature_tags
                            WHERE literature_id = ?
                            ORDER BY tag_id
                            """,
                            (other_id,),
                        ).fetchall()
                    ],
                    "usage": [
                        tuple(row)
                        for row in connection.execute(
                            """
                            SELECT *
                            FROM usage_history
                            WHERE literature_id = ?
                            ORDER BY id
                            """,
                            (other_id,),
                        ).fetchall()
                    ],
                },
                other_related_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(
                outputs.count("文献を削除しました。"),
                1,
            )
            self.assertNotIn("データベースエラーが発生しました。", outputs)
            self.assertNotIn("CLIを終了します。", outputs)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_delete_output_exception_matrix_preserves_stage_contracts(
        self,
    ) -> None:
        cases = (
            ("initial_transaction", "initial_transaction"),
            ("invalid_id", "invalid_id"),
            ("missing_literature", "missing"),
            ("current_literature", "current"),
            ("related_counts_missing", "counts_missing"),
            ("impact_display", "impact"),
            ("first_confirmation_invalid", "first_invalid"),
            ("first_confirmation_cancel", "first_cancel"),
            ("final_id_invalid", "final_invalid"),
            ("final_id_cancel", "final_cancel"),
            ("late_transaction", "late_transaction"),
            ("get_database_error", "get_database_error"),
            ("counts_database_error", "counts_database_error"),
            ("delete_database_error", "delete_database_error"),
            ("delete_false", "delete_false"),
            ("delete_success", "delete_success"),
        )

        for case_index, (case_name, stage) in enumerate(cases):
            with self.subTest(case=case_name):
                connection, target_id, _ = (
                    self.create_tracking_delete_fixture(
                        f"output-{case_index}"
                    )
                )
                try:
                    literature = get_literature(connection, target_id)
                    assert literature is not None
                    before = self.table_snapshot_for(connection)
                    expected = RuntimeError(
                        f"{case_name} output failure"
                    )
                    get_return: Literature | None = literature
                    counts_return: dict[str, int] | None = {
                        "tag_count": 2,
                        "usage_history_count": 1,
                    }
                    delete_return = True
                    get_error: sqlite3.Error | None = None
                    counts_error: sqlite3.Error | None = None
                    delete_error: sqlite3.Error | None = None
                    marker_name = f"pending-output-marker-{case_index}"

                    if stage == "initial_transaction":
                        connection.execute("BEGIN")
                        actions: list[object] = ["5"]
                        failing_message = (
                            cli_module._DELETE_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "invalid_id":
                        actions = ["5", "invalid"]
                        failing_message = (
                            "入力エラー: 文献IDは1以上の"
                            "ASCII数字だけで入力してください。"
                        )
                    elif stage == "missing":
                        get_return = None
                        actions = ["5", "999999"]
                        failing_message = "対象文献が見つかりません。"
                    elif stage == "current":
                        actions = ["5", str(target_id)]
                        failing_message = (
                            cli_module._format_edit_literature(literature)
                        )
                    elif stage == "counts_missing":
                        counts_return = None
                        actions = ["5", str(target_id)]
                        failing_message = (
                            "現在の文献情報を表示した後に"
                            "対象文献が存在しなくなりました。"
                        )
                    elif stage == "impact":
                        actions = ["5", str(target_id)]
                        failing_message = (
                            "削除対象と影響を確認してください。"
                        )
                    elif stage == "first_invalid":
                        actions = ["5", str(target_id), "invalid"]
                        failing_message = (
                            cli_module._INVALID_CONFIRMATION_MESSAGE
                        )
                    elif stage == "first_cancel":
                        actions = ["5", str(target_id), "0"]
                        failing_message = "文献削除を中止しました。"
                    elif stage == "final_invalid":
                        actions = [
                            "5",
                            str(target_id),
                            "1",
                            "invalid",
                        ]
                        failing_message = (
                            f"入力エラー: 文献ID {target_id} "
                            "または0を入力してください。"
                        )
                    elif stage == "final_cancel":
                        actions = ["5", str(target_id), "1", "0"]
                        failing_message = "文献削除を中止しました。"
                    elif stage == "late_transaction":
                        actions = [
                            "5",
                            str(target_id),
                            "1",
                            str(target_id),
                        ]
                        failing_message = (
                            cli_module._DELETE_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "get_database_error":
                        get_error = sqlite3.OperationalError("get failure")
                        actions = ["5", str(target_id)]
                        failing_message = (
                            "データベースエラーが発生しました。"
                        )
                    elif stage == "counts_database_error":
                        counts_error = sqlite3.OperationalError(
                            "counts failure"
                        )
                        actions = ["5", str(target_id)]
                        failing_message = (
                            "データベースエラーが発生しました。"
                        )
                    elif stage == "delete_database_error":
                        delete_error = sqlite3.OperationalError(
                            "delete failure"
                        )
                        actions = [
                            "5",
                            str(target_id),
                            "1",
                            str(target_id),
                        ]
                        failing_message = (
                            "データベースエラーが発生しました。"
                        )
                    elif stage == "delete_false":
                        delete_return = False
                        actions = [
                            "5",
                            str(target_id),
                            "1",
                            str(target_id),
                        ]
                        failing_message = (
                            "確認後に対象文献が存在しなくなりました。"
                        )
                    else:
                        actions = [
                            "5",
                            str(target_id),
                            "1",
                            str(target_id),
                        ]
                        failing_message = "文献を削除しました。"

                    feeder = InputFeeder(actions)

                    def input_func(prompt: str) -> str:
                        value = feeder(prompt)
                        if (
                            stage == "late_transaction"
                            and prompt.startswith(
                                "削除を確定するため文献ID"
                            )
                        ):
                            connection.execute(
                                "INSERT INTO tags (name) VALUES (?)",
                                (marker_name,),
                            )
                        return value

                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with (
                        patch.object(
                            cli_module,
                            "get_literature",
                            return_value=get_return,
                            side_effect=get_error,
                        ) as retrieved,
                        patch.object(
                            cli_module,
                            "get_literature_related_counts",
                            return_value=counts_return,
                            side_effect=counts_error,
                        ) as counted,
                        patch.object(
                            cli_module,
                            "delete_literature",
                            return_value=delete_return,
                            side_effect=delete_error,
                        ) as deleted,
                    ):
                        with self.assertRaises(RuntimeError) as raised:
                            run_cli(
                                connection,
                                input_func=input_func,
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    self.assertEqual(outputs.count(failing_message), 1)
                    if stage in {
                        "delete_database_error",
                        "delete_false",
                        "delete_success",
                    }:
                        deleted.assert_called_once_with(
                            connection,
                            target_id,
                        )
                    else:
                        deleted.assert_not_called()
                    if stage in {"initial_transaction", "invalid_id"}:
                        retrieved.assert_not_called()
                    if stage in {
                        "initial_transaction",
                        "invalid_id",
                        "missing",
                        "current",
                        "get_database_error",
                    }:
                        counted.assert_not_called()
                    self.assertIsNotNone(
                        sqlite3.Connection.execute(
                            connection,
                            "SELECT 1",
                        ).fetchone()
                    )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertNotIn("CLIを終了します。", outputs)
                    if stage == "late_transaction":
                        self.assertTrue(connection.in_transaction)
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM tags WHERE name = ?",
                                (marker_name,),
                            ).fetchone()[0],
                            1,
                        )
                        sqlite3.Connection.rollback(connection)
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                    else:
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_delete_output_interruptions_are_not_input_interruptions(
        self,
    ) -> None:
        literature_id = self.add_record("Delete output interruption")

        for expected in (
            EOFError("delete impact output EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "削除対象と影響を確認してください。":
                        raise expected

                with patch.object(
                    cli_module,
                    "delete_literature",
                ) as deleted:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                ["5", str(literature_id)]
                            ),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, expected)
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn("CLIを終了します。", outputs)
                self.assertNotIn(
                    "データベースエラーが発生しました。",
                    outputs,
                )

    def test_tag_submenu_contract_returns_to_main_and_loops_without_recursion(
        self,
    ) -> None:
        invalid_count = 1200
        actions = [
            "6",
            "",
            "invalid",
            *(["9"] * invalid_count),
            " \t0\n ",
            "0",
        ]

        _, feeder, outputs = self.run_with_actions(actions)

        self.assertIn("6. タグ管理", outputs[0])
        self.assertIn("1. タグ一覧", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("2. タグ作成", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("3. タグ名称変更", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("4. タグ削除", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("5. 文献別タグ一覧", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("6. 文献へタグ付与", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn("7. 文献からタグ解除", cli_module._TAG_MANAGEMENT_MENU)
        self.assertIn(
            "0. メインメニューに戻る",
            cli_module._TAG_MANAGEMENT_MENU,
        )
        self.assertNotIn(
            "8. メインメニューに戻る",
            cli_module._TAG_MANAGEMENT_MENU,
        )
        self.assertNotIn("使用履歴", cli_module._TAG_MANAGEMENT_MENU)
        self.assertEqual(
            outputs.count(cli_module._INVALID_TAG_MENU_MESSAGE),
            invalid_count + 2,
        )
        self.assertEqual(
            outputs.count(cli_module._TAG_MANAGEMENT_MENU),
            invalid_count + 3,
        )
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            2,
        )
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 5,
        )

    def test_tag_submenu_zero_returns_eight_invalid_and_new_choices_dispatch(
        self,
    ) -> None:
        feeder = InputFeeder(["6", "8", "5", "6", "7", "0", "0"])
        outputs: list[str] = []

        with (
            patch.object(
                cli_module,
                "_run_literature_tag_list",
                return_value=False,
            ) as list_flow,
            patch.object(
                cli_module,
                "_run_tag_attach",
                return_value=False,
            ) as attach_flow,
            patch.object(
                cli_module,
                "_run_tag_detach",
                return_value=False,
            ) as detach_flow,
        ):
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        self.assertEqual(
            cli_module._INVALID_TAG_MENU_MESSAGE,
            "入力エラー: 0〜7のいずれかを選択してください。",
        )
        self.assertEqual(
            outputs.count(cli_module._INVALID_TAG_MENU_MESSAGE),
            1,
        )
        self.assertEqual(outputs.count(cli_module._TAG_MANAGEMENT_MENU), 5)
        self.assertEqual(
            sum("理学療法文献ライブラリ" in item for item in outputs),
            2,
        )
        list_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        attach_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        detach_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )

    def test_new_literature_tag_flows_validate_positive_ascii_ids(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 ID validation")
        tag_id = create_tag(self.connection, "id-validation-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )
        cases = (
            ("list literature", "5", []),
            ("attach literature", "6", []),
            ("attach tag", "6", [str(literature_id)]),
            ("detach literature", "7", []),
            ("detach tag", "7", [str(literature_id)]),
        )

        for case, operation, prefix in cases:
            for invalid_value in invalid_values:
                with self.subTest(case=case, invalid_value=invalid_value):
                    before = self.table_snapshot()
                    with (
                        patch.object(
                            cli_module,
                            "attach_tag_to_literature",
                        ) as attached,
                        patch.object(
                            cli_module,
                            "detach_tag_from_literature",
                        ) as detached,
                    ):
                        _, _, outputs = self.run_with_actions(
                            [
                                "6",
                                operation,
                                *prefix,
                                invalid_value,
                                "0",
                                "0",
                            ]
                        )

                    attached.assert_not_called()
                    detached.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertTrue(
                        any(
                            item.startswith("入力エラー: ")
                            and "ASCII" in item
                            for item in outputs
                        )
                    )

    def test_literature_tag_list_handles_missing_empty_and_race_none(
        self,
    ) -> None:
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
            ) as listed,
        ):
            _, _, missing_outputs = self.run_with_actions(
                self.literature_tag_list_actions(999999)
            )

        retrieved.assert_called_once_with(self.connection, 999999)
        listed.assert_not_called()
        self.assertIn("対象文献が見つかりません。", missing_outputs)

        literature_id = self.add_record("タグなし文献")
        before = self.table_snapshot()
        with patch.object(
            cli_module,
            "list_tags_for_literature",
            wraps=list_tags_for_literature,
        ) as listed:
            _, feeder, empty_outputs = self.run_with_actions(
                self.literature_tag_list_actions(literature_id)
            )

        listed.assert_called_once_with(self.connection, literature_id)
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn(f"文献ID: {literature_id}", empty_outputs)
        self.assertIn("title: タグなし文献", empty_outputs)
        self.assertIn(
            "この文献にはタグが登録されていません。",
            empty_outputs,
        )
        self.assertNotIn("タグID（ASCII数字）: ", feeder.prompts)

        with patch.object(
            cli_module,
            "list_tags_for_literature",
            return_value=None,
        ) as listed:
            _, _, race_outputs = self.run_with_actions(
                self.literature_tag_list_actions(literature_id)
            )

        listed.assert_called_once_with(self.connection, literature_id)
        self.assertIn(
            "文献情報の確認後に対象文献が存在しなくなりました。",
            race_outputs,
        )
        self.assertEqual(self.table_snapshot(), before)

    def test_literature_tag_list_preserves_order_and_read_transaction(
        self,
    ) -> None:
        literature_id = self.add_record("一覧順序とtransaction")
        beta_id = create_tag(self.connection, "Beta")
        alpha_id = create_tag(self.connection, "alpha")
        pending_id = create_tag(self.connection, "gamma")
        attach_tag_to_literature(self.connection, literature_id, beta_id)
        attach_tag_to_literature(self.connection, literature_id, alpha_id)
        self.connection.execute(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (literature_id, pending_id),
        )
        self.assertTrue(self.connection.in_transaction)
        before = self.table_snapshot()

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
                wraps=list_tags_for_literature,
            ) as listed,
        ):
            _, _, outputs = self.run_with_actions(
                self.literature_tag_list_actions(f" \t00{literature_id}\n ")
            )

        retrieved.assert_called_once_with(self.connection, literature_id)
        listed.assert_called_once_with(self.connection, literature_id)
        self.assertEqual(self.table_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        displayed_tags = [
            item
            for item in outputs
            if item.startswith("ID: ") and "\nname: " in item
        ]
        self.assertEqual(
            displayed_tags,
            [
                f"ID: {alpha_id}\nname: alpha",
                f"ID: {beta_id}\nname: Beta",
                f"ID: {pending_id}\nname: gamma",
            ],
        )
        self.connection.rollback()
        self.assertEqual(
            list_tags_for_literature(self.connection, literature_id),
            [
                Tag(id=alpha_id, name="alpha"),
                Tag(id=beta_id, name="Beta"),
            ],
        )

    def test_tag_attach_success_and_duplicate_are_isolated_and_safe(
        self,
    ) -> None:
        target_literature_id = self.add_record("付与対象文献")
        other_literature_id = self.add_record("付与対象外文献")
        target_tag_id = create_tag(self.connection, "Shoulder")
        other_tag_id = create_tag(self.connection, "Ultrasound")
        attach_tag_to_literature(
            self.connection,
            other_literature_id,
            target_tag_id,
        )
        attach_tag_to_literature(
            self.connection,
            target_literature_id,
            other_tag_id,
        )
        create_usage_history(
            self.connection,
            target_literature_id,
            "attach-preserved-use",
        )
        self.connection.execute("PRAGMA user_version = 102")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "get_tag",
                wraps=get_tag,
            ) as tag_retrieved,
            patch.object(
                cli_module,
                "attach_tag_to_literature",
                wraps=attach_tag_to_literature,
            ) as attached,
            patch.object(cli_module, "create_tag") as created,
        ):
            _, _, outputs = self.run_with_actions(
                self.tag_attach_actions(
                    f" 00{target_literature_id} ",
                    f"\t00{target_tag_id}\n",
                )
            )

        retrieved.assert_called_once_with(
            self.connection,
            target_literature_id,
        )
        tag_retrieved.assert_called_once_with(self.connection, target_tag_id)
        attached.assert_called_once_with(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        created.assert_not_called()
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["tags"], before["tags"])
        self.assertEqual(after["usage_history"], before["usage_history"])
        self.assertEqual(
            after["literature_tags"],
            sorted(
                [
                    *before["literature_tags"],
                    (target_literature_id, target_tag_id),
                ]
            ),
        )
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        for expected in (
            f"文献ID: {target_literature_id}",
            "title: 付与対象文献",
            f"タグID: {target_tag_id}",
            "tag name: Shoulder",
            "文献へタグを付与しました。",
        ):
            self.assertIn(expected, outputs)

        before_duplicate = self.table_snapshot()
        with patch.object(
            cli_module,
            "attach_tag_to_literature",
            wraps=attach_tag_to_literature,
        ) as attached:
            _, _, duplicate_outputs = self.run_with_actions(
                self.tag_attach_actions(
                    target_literature_id,
                    target_tag_id,
                )
            )

        attached.assert_called_once_with(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        self.assertEqual(self.table_snapshot(), before_duplicate)
        self.assertIn(
            "このタグは既に対象文献へ付与されています。",
            duplicate_outputs,
        )
        self.assertNotIn("文献へタグを付与しました。", duplicate_outputs)
        self.assertFalse(self.connection.in_transaction)

    def test_tag_attach_missing_targets_confirmation_cancel_and_value_error(
        self,
    ) -> None:
        literature_id = self.add_record("付与安全性")
        tag_id = create_tag(self.connection, "attach-safety")
        before = self.table_snapshot()

        with (
            patch.object(cli_module, "get_tag") as tag_retrieved,
            patch.object(
                cli_module,
                "attach_tag_to_literature",
            ) as attached,
        ):
            _, _, missing_literature_outputs = self.run_with_actions(
                self.tag_attach_actions(999999, tag_id)
            )
        tag_retrieved.assert_not_called()
        attached.assert_not_called()
        self.assertIn(
            "対象文献が見つかりません。",
            missing_literature_outputs,
        )

        with patch.object(
            cli_module,
            "attach_tag_to_literature",
        ) as attached:
            _, _, missing_tag_outputs = self.run_with_actions(
                self.tag_attach_actions(literature_id, 999999)
            )
        attached.assert_not_called()
        self.assertIn("対象タグが見つかりません。", missing_tag_outputs)

        invalid_count = 1200
        with patch.object(
            cli_module,
            "attach_tag_to_literature",
        ) as attached:
            _, _, cancel_outputs = self.run_with_actions(
                [
                    "6",
                    "6",
                    str(literature_id),
                    str(tag_id),
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " 0 ",
                    "0",
                    "0",
                ]
            )
        attached.assert_not_called()
        self.assertEqual(
            cancel_outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("文献へのタグ付与を中止しました。", cancel_outputs)

        expected = ValueError("parents disappeared before attach")
        with patch.object(
            cli_module,
            "attach_tag_to_literature",
            side_effect=expected,
        ) as attached:
            _, _, error_outputs = self.run_with_actions(
                self.tag_attach_actions(literature_id, tag_id)
            )
        attached.assert_called_once_with(
            self.connection,
            literature_id,
            tag_id,
        )
        self.assertIn(f"タグ付与エラー: {expected}", error_outputs)
        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, error_outputs)
        self.assertEqual(self.table_snapshot(), before)

    def test_tag_detach_success_isolated_and_false_is_not_retried(
        self,
    ) -> None:
        target_literature_id = self.add_record("解除対象文献")
        other_literature_id = self.add_record("解除対象外文献")
        target_tag_id = create_tag(self.connection, "Shared")
        other_tag_id = create_tag(self.connection, "Reliability")
        for literature_id, tag_id in (
            (target_literature_id, target_tag_id),
            (target_literature_id, other_tag_id),
            (other_literature_id, target_tag_id),
        ):
            attach_tag_to_literature(
                self.connection,
                literature_id,
                tag_id,
            )
        create_usage_history(
            self.connection,
            target_literature_id,
            "detach-preserved-use",
        )
        self.connection.execute("PRAGMA user_version = 103")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
                wraps=list_tags_for_literature,
            ) as listed,
            patch.object(
                cli_module,
                "get_tag",
                wraps=get_tag,
            ) as tag_retrieved,
            patch.object(
                cli_module,
                "detach_tag_from_literature",
                wraps=detach_tag_from_literature,
            ) as detached,
        ):
            _, _, outputs = self.run_with_actions(
                self.tag_detach_actions(
                    f" 00{target_literature_id} ",
                    f"\t00{target_tag_id}\n",
                )
            )

        retrieved.assert_called_once_with(
            self.connection,
            target_literature_id,
        )
        listed.assert_called_once_with(
            self.connection,
            target_literature_id,
        )
        tag_retrieved.assert_called_once_with(self.connection, target_tag_id)
        detached.assert_called_once_with(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["tags"], before["tags"])
        self.assertEqual(after["usage_history"], before["usage_history"])
        self.assertEqual(
            after["literature_tags"],
            [
                row
                for row in before["literature_tags"]
                if row != (target_literature_id, target_tag_id)
            ],
        )
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        for expected in (
            f"文献ID: {target_literature_id}",
            "title: 解除対象文献",
            f"タグID: {target_tag_id}",
            "tag name: Shared",
            "タグレコード自体は削除されません。",
            "文献レコード自体は削除されません。",
            "文献からタグを解除しました。",
        ):
            self.assertIn(expected, outputs)
        self.assertEqual(
            list_tags_for_literature(
                self.connection,
                target_literature_id,
            ),
            [Tag(id=other_tag_id, name="Reliability")],
        )
        self.assertEqual(
            list_tags_for_literature(
                self.connection,
                other_literature_id,
            ),
            [Tag(id=target_tag_id, name="Shared")],
        )
        self.assertIsNotNone(get_literature(self.connection, target_literature_id))
        self.assertIsNotNone(get_tag(self.connection, target_tag_id))

        attach_tag_to_literature(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        before_false = self.table_snapshot()
        with patch.object(
            cli_module,
            "detach_tag_from_literature",
            return_value=False,
        ) as detached:
            _, _, false_outputs = self.run_with_actions(
                self.tag_detach_actions(
                    target_literature_id,
                    target_tag_id,
                )
            )

        detached.assert_called_once_with(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        self.assertEqual(self.table_snapshot(), before_false)
        self.assertIn(
            "確認後に対象のタグ関連付けが存在しなくなりました。",
            false_outputs,
        )
        self.assertNotIn("文献からタグを解除しました。", false_outputs)
        self.assertFalse(self.connection.in_transaction)

    def test_tag_detach_missing_empty_unattached_and_cancel_are_safe(
        self,
    ) -> None:
        literature_id = self.add_record("解除安全性")
        empty_literature_id = self.add_record("解除タグなし")
        attached_tag_id = create_tag(self.connection, "attached")
        unattached_tag_id = create_tag(self.connection, "unattached")
        attach_tag_to_literature(
            self.connection,
            literature_id,
            attached_tag_id,
        )
        before = self.table_snapshot()

        with (
            patch.object(
                cli_module,
                "list_tags_for_literature",
            ) as listed,
            patch.object(
                cli_module,
                "detach_tag_from_literature",
            ) as detached,
        ):
            _, _, missing_literature_outputs = self.run_with_actions(
                self.tag_detach_actions(999999, attached_tag_id)
            )
        listed.assert_not_called()
        detached.assert_not_called()
        self.assertIn(
            "対象文献が見つかりません。",
            missing_literature_outputs,
        )

        with (
            patch.object(cli_module, "get_tag") as tag_retrieved,
            patch.object(
                cli_module,
                "detach_tag_from_literature",
            ) as detached,
        ):
            _, feeder, empty_outputs = self.run_with_actions(
                ["6", "7", str(empty_literature_id), "0", "0"]
            )
        tag_retrieved.assert_not_called()
        detached.assert_not_called()
        self.assertNotIn("タグID（ASCII数字）: ", feeder.prompts)
        self.assertIn(
            "この文献には解除できるタグがありません。",
            empty_outputs,
        )

        with patch.object(
            cli_module,
            "detach_tag_from_literature",
        ) as detached:
            _, _, missing_tag_outputs = self.run_with_actions(
                self.tag_detach_actions(literature_id, 999999)
            )
        detached.assert_not_called()
        self.assertIn("対象タグが見つかりません。", missing_tag_outputs)

        with patch.object(
            cli_module,
            "detach_tag_from_literature",
        ) as detached:
            _, _, unattached_outputs = self.run_with_actions(
                self.tag_detach_actions(
                    literature_id,
                    unattached_tag_id,
                )
            )
        detached.assert_not_called()
        self.assertIn(
            "このタグは対象文献に付与されていません。",
            unattached_outputs,
        )

        invalid_count = 1200
        with patch.object(
            cli_module,
            "detach_tag_from_literature",
        ) as detached:
            _, _, cancel_outputs = self.run_with_actions(
                [
                    "6",
                    "7",
                    str(literature_id),
                    str(attached_tag_id),
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " 0 ",
                    "0",
                    "0",
                ]
            )
        detached.assert_not_called()
        self.assertEqual(
            cancel_outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn(
            "文献からのタグ解除を中止しました。",
            cancel_outputs,
        )
        self.assertEqual(self.table_snapshot(), before)

    def test_tag_attach_and_detach_reject_initial_and_late_transactions(
        self,
    ) -> None:
        cases = (
            (
                "attach",
                "6",
                cli_module._TAG_ATTACH_ACTIVE_TRANSACTION_MESSAGE,
                "attach_tag_to_literature",
            ),
            (
                "detach",
                "7",
                cli_module._TAG_DETACH_ACTIVE_TRANSACTION_MESSAGE,
                "detach_tag_from_literature",
            ),
        )

        for index, (case, option, message, api_name) in enumerate(cases):
            with self.subTest(case=case):
                connection, literature_id, target_tag_id, other_tag_id = (
                    self.create_tracking_tag_fixture(
                        f"step-8c2-transaction-{index}"
                    )
                )
                try:
                    operation_tag_id = (
                        other_tag_id if case == "attach" else target_tag_id
                    )
                    marker = connection.execute(
                        "INSERT INTO tags (name) VALUES (?)",
                        (f"pending-{case}-initial",),
                    )
                    connection.commit_calls = 0
                    connection.rollback_calls = 0
                    connection.close_calls = 0

                    with patch.object(cli_module, api_name) as write_api:
                        _, feeder, outputs = self.run_with_actions(
                            ["6", option, "0", "0"],
                            connection=connection,
                        )

                    write_api.assert_not_called()
                    self.assertEqual(
                        feeder.prompts,
                        [
                            "選択してください: ",
                            "選択してください: ",
                            "選択してください: ",
                            "選択してください: ",
                        ],
                    )
                    self.assertIn(message, outputs)
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM tags WHERE id = ?",
                            (marker.lastrowid,),
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    sqlite3.Connection.rollback(connection)

                    relation_before = connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM literature_tags
                        WHERE literature_id = ? AND tag_id = ?
                        """,
                        (literature_id, operation_tag_id),
                    ).fetchone()[0]
                    feeder = InputFeeder(
                        [
                            "6",
                            option,
                            str(literature_id),
                            str(operation_tag_id),
                            "1",
                            "0",
                            "0",
                        ]
                    )
                    marker_ids: list[int] = []

                    def input_func(prompt: str) -> str:
                        value = feeder(prompt)
                        if value == "1" and len(feeder.prompts) == 5:
                            inserted = connection.execute(
                                "INSERT INTO tags (name) VALUES (?)",
                                (f"pending-{case}-late",),
                            )
                            marker_ids.append(inserted.lastrowid)
                        return value

                    connection.commit_calls = 0
                    connection.rollback_calls = 0
                    connection.close_calls = 0
                    outputs = []
                    with patch.object(cli_module, api_name) as write_api:
                        result = run_cli(
                            connection,
                            input_func=input_func,
                            output_func=outputs.append,
                        )

                    self.assertIsNone(result)
                    write_api.assert_not_called()
                    self.assertEqual(len(marker_ids), 1)
                    self.assertIn(message, outputs)
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(
                        connection.execute(
                            """
                            SELECT COUNT(*)
                            FROM literature_tags
                            WHERE literature_id = ? AND tag_id = ?
                            """,
                            (literature_id, operation_tag_id),
                        ).fetchone()[0],
                        relation_before,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM tags WHERE id = ?",
                            (marker_ids[0],),
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    sqlite3.Connection.rollback(connection)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_new_literature_tag_repository_exception_boundaries(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 API boundaries")
        tag_id = create_tag(self.connection, "api-boundary-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        cases = (
            (
                "list get literature",
                "get_literature",
                ["6", "5", str(literature_id)],
            ),
            (
                "list tags",
                "list_tags_for_literature",
                ["6", "5", str(literature_id)],
            ),
            (
                "attach get literature",
                "get_literature",
                ["6", "6", str(literature_id)],
            ),
            (
                "attach get tag",
                "get_tag",
                ["6", "6", str(literature_id), str(tag_id)],
            ),
            (
                "attach write",
                "attach_tag_to_literature",
                ["6", "6", str(literature_id), str(tag_id), "1"],
            ),
            (
                "detach get literature",
                "get_literature",
                ["6", "7", str(literature_id)],
            ),
            (
                "detach list tags",
                "list_tags_for_literature",
                ["6", "7", str(literature_id)],
            ),
            (
                "detach get tag",
                "get_tag",
                ["6", "7", str(literature_id), str(tag_id)],
            ),
            (
                "detach write",
                "detach_tag_from_literature",
                ["6", "7", str(literature_id), str(tag_id), "1"],
            ),
        )

        for case, api_name, actions in cases:
            for exception_type in (
                sqlite3.OperationalError,
                RuntimeError,
            ):
                with self.subTest(case=case, exception=exception_type.__name__):
                    expected = exception_type(f"{case} failure")
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    with patch.object(
                        cli_module,
                        api_name,
                        side_effect=expected,
                    ) as failed_api:
                        with self.assertRaises(exception_type) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )

                    self.assertIs(raised.exception, expected)
                    failed_api.assert_called_once()
                    self.assertEqual(self.table_snapshot(), before)
                    if issubclass(exception_type, sqlite3.Error):
                        self.assertEqual(
                            outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                            1,
                        )
                    else:
                        self.assertNotIn(
                            cli_module._DATABASE_ERROR_MESSAGE,
                            outputs,
                        )
                    self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)

        expected = ValueError("unexpected detach value failure")
        before = self.table_snapshot()
        with patch.object(
            cli_module,
            "detach_tag_from_literature",
            side_effect=expected,
        ) as detached:
            with self.assertRaises(ValueError) as raised:
                run_cli(
                    self.connection,
                    input_func=InputFeeder(
                        ["6", "7", str(literature_id), str(tag_id), "1"]
                    ),
                    output_func=lambda _: None,
                )
        self.assertIs(raised.exception, expected)
        detached.assert_called_once()
        self.assertEqual(self.table_snapshot(), before)

    def test_new_literature_tag_input_interruptions_exit_without_writes(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 input interruption")
        tag_id = create_tag(self.connection, "input-interruption-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        positions = (
            ("list literature ID", ["6", "5"]),
            ("attach literature ID", ["6", "6"]),
            ("attach tag ID", ["6", "6", str(literature_id)]),
            (
                "attach confirmation",
                ["6", "6", str(literature_id), str(tag_id)],
            ),
            ("detach literature ID", ["6", "7"]),
            ("detach tag ID", ["6", "7", str(literature_id)]),
            (
                "detach confirmation",
                ["6", "7", str(literature_id), str(tag_id)],
            ),
        )

        for position, prefix in positions:
            for expected in (EOFError(position), KeyboardInterrupt()):
                with self.subTest(
                    position=position,
                    exception=type(expected).__name__,
                ):
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    with (
                        patch.object(
                            cli_module,
                            "attach_tag_to_literature",
                        ) as attached,
                        patch.object(
                            cli_module,
                            "detach_tag_from_literature",
                        ) as detached,
                    ):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder([*prefix, expected]),
                            output_func=outputs.append,
                        )

                    self.assertIsNone(result)
                    attached.assert_not_called()
                    detached.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertEqual(
                        outputs.count(cli_module._EXIT_MESSAGE),
                        1,
                    )
                    self.assertNotIn(repr(expected), "\n".join(outputs))

    def test_new_literature_tag_unexpected_input_exceptions_propagate(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 unexpected input")
        tag_id = create_tag(self.connection, "unexpected-input-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        cases = (
            (
                "list",
                ["6", "5"],
                RuntimeError("list input failure"),
            ),
            (
                "attach",
                ["6", "6", str(literature_id)],
                sqlite3.OperationalError("attach input sqlite failure"),
            ),
            (
                "detach",
                ["6", "7", str(literature_id), str(tag_id)],
                ValueError("detach confirmation input failure"),
            ),
        )

        for case, prefix, expected in cases:
            with self.subTest(case=case):
                before = self.table_snapshot()
                outputs: list[str] = []
                with (
                    patch.object(
                        cli_module,
                        "attach_tag_to_literature",
                    ) as attached,
                    patch.object(
                        cli_module,
                        "detach_tag_from_literature",
                    ) as detached,
                ):
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder([*prefix, expected]),
                            output_func=outputs.append,
                        )

                self.assertIs(raised.exception, expected)
                attached.assert_not_called()
                detached.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
                self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)

    def test_new_literature_tag_output_exceptions_propagate_before_write(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 output boundary")
        tag_id = create_tag(self.connection, "output-boundary-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        cases = (
            (
                "list",
                ["6", "5", str(literature_id)],
                "文献情報:",
            ),
            (
                "attach",
                ["6", "6", str(literature_id), str(tag_id)],
                "文献へのタグ付与内容を確認してください。",
            ),
            (
                "detach",
                ["6", "7", str(literature_id), str(tag_id)],
                "文献からのタグ解除内容を確認してください。",
            ),
        )

        for case, actions, failing_message in cases:
            for expected in (
                RuntimeError(f"{case} output failure"),
                sqlite3.OperationalError(f"{case} output sqlite failure"),
            ):
                with self.subTest(case=case, exception=type(expected).__name__):
                    before = self.table_snapshot()
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with (
                        patch.object(
                            cli_module,
                            "attach_tag_to_literature",
                        ) as attached,
                        patch.object(
                            cli_module,
                            "detach_tag_from_literature",
                        ) as detached,
                    ):
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    attached.assert_not_called()
                    detached.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertEqual(outputs.count(failing_message), 1)
                    self.assertNotIn(
                        cli_module._DATABASE_ERROR_MESSAGE,
                        outputs,
                    )
                    self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)

    def test_new_tag_database_error_output_failure_propagates_output_error(
        self,
    ) -> None:
        literature_id = self.add_record("Step 8C-2 DB output boundary")
        tag_id = create_tag(self.connection, "db-output-boundary-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        cases = (
            (
                "literature tag list",
                "list_tags_for_literature",
                ["6", "5", str(literature_id)],
            ),
            (
                "tag attach",
                "attach_tag_to_literature",
                ["6", "6", str(literature_id), str(tag_id), "1"],
            ),
            (
                "tag detach",
                "detach_tag_from_literature",
                ["6", "7", str(literature_id), str(tag_id), "1"],
            ),
        )

        for case, api_name, actions in cases:
            with self.subTest(case=case):
                database_error = sqlite3.OperationalError(
                    f"{case} database failure"
                )
                output_error = RuntimeError(f"{case} output failure")

                def output_func(message: str) -> None:
                    if message == cli_module._DATABASE_ERROR_MESSAGE:
                        raise output_error

                with patch.object(
                    cli_module,
                    api_name,
                    side_effect=database_error,
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, output_error)
                self.assertIsNot(raised.exception, database_error)

    def test_real_sqlite_tag_attach_and_detach_failures_are_atomic(
        self,
    ) -> None:
        cases = (
            (
                "attach",
                "attach_tag_to_literature",
                """
                CREATE TRIGGER force_cli_mapping_insert_failure
                BEFORE INSERT ON literature_tags
                BEGIN
                    SELECT RAISE(ABORT, 'forced CLI mapping insert failure');
                END
                """,
                "文献へタグを付与しました。",
            ),
            (
                "detach",
                "detach_tag_from_literature",
                """
                CREATE TRIGGER force_cli_mapping_delete_failure
                BEFORE DELETE ON literature_tags
                BEGIN
                    SELECT RAISE(ABORT, 'forced CLI mapping delete failure');
                END
                """,
                "文献からタグを解除しました。",
            ),
        )

        for index, (case, api_name, trigger_sql, success_message) in enumerate(
            cases
        ):
            with self.subTest(case=case):
                connection, literature_id, target_tag_id, other_tag_id = (
                    self.create_tracking_tag_fixture(
                        f"real-sqlite-{case}-failure-{index}"
                    )
                )
                try:
                    operation_tag_id = (
                        other_tag_id if case == "attach" else target_tag_id
                    )
                    connection.execute("PRAGMA user_version = 104")
                    connection.execute(trigger_sql)
                    connection.commit()
                    before = self.table_snapshot_for(connection)
                    schema_before = self.schema_snapshot_for(connection)
                    schema_version_before = connection.execute(
                        "PRAGMA schema_version"
                    ).fetchone()[0]
                    user_version_before = connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                    connection.commit_calls = 0
                    connection.rollback_calls = 0
                    connection.close_calls = 0
                    original_api = (
                        attach_tag_to_literature
                        if case == "attach"
                        else detach_tag_from_literature
                    )
                    captured_errors: list[sqlite3.Error] = []

                    def failing_write(
                        write_connection: sqlite3.Connection,
                        write_literature_id: int,
                        write_tag_id: int,
                    ) -> bool:
                        try:
                            return original_api(
                                write_connection,
                                write_literature_id,
                                write_tag_id,
                            )
                        except sqlite3.Error as error:
                            captured_errors.append(error)
                            raise

                    actions = (
                        self.tag_attach_actions(
                            literature_id,
                            operation_tag_id,
                        )
                        if case == "attach"
                        else self.tag_detach_actions(
                            literature_id,
                            operation_tag_id,
                        )
                    )
                    outputs: list[str] = []
                    with patch.object(
                        cli_module,
                        api_name,
                        side_effect=failing_write,
                    ) as failed_api:
                        with self.assertRaises(sqlite3.IntegrityError) as raised:
                            run_cli(
                                connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )

                    failed_api.assert_called_once_with(
                        connection,
                        literature_id,
                        operation_tag_id,
                    )
                    self.assertEqual(len(captured_errors), 1)
                    self.assertIs(raised.exception, captured_errors[0])
                    self.assertEqual(
                        self.table_snapshot_for(connection),
                        before,
                    )
                    self.assertEqual(
                        self.schema_snapshot_for(connection),
                        schema_before,
                    )
                    self.assertEqual(
                        connection.execute(
                            "PRAGMA schema_version"
                        ).fetchone()[0],
                        schema_version_before,
                    )
                    self.assertEqual(
                        connection.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0],
                        user_version_before,
                    )
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(
                        connection.execute("SELECT 1").fetchone()[0],
                        1,
                    )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                    self.assertNotIn(success_message, outputs)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_attach_and_detach_success_output_failure_keeps_committed_change(
        self,
    ) -> None:
        cases = (
            (
                "attach",
                "attach_tag_to_literature",
                "文献へタグを付与しました。",
            ),
            (
                "detach",
                "detach_tag_from_literature",
                "文献からタグを解除しました。",
            ),
        )

        for index, (case, api_name, success_message) in enumerate(cases):
            with self.subTest(case=case):
                connection, literature_id, target_tag_id, other_tag_id = (
                    self.create_tracking_tag_fixture(
                        f"{case}-success-output-{index}"
                    )
                )
                try:
                    operation_tag_id = (
                        other_tag_id if case == "attach" else target_tag_id
                    )
                    connection.execute("PRAGMA user_version = 105")
                    before = self.table_snapshot_for(connection)
                    schema_before = self.schema_snapshot_for(connection)
                    schema_version_before = connection.execute(
                        "PRAGMA schema_version"
                    ).fetchone()[0]
                    user_version_before = connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0]
                    connection.commit_calls = 0
                    connection.rollback_calls = 0
                    connection.close_calls = 0
                    expected = RuntimeError(f"{case} success output failure")
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == success_message:
                            raise expected

                    original_api = (
                        attach_tag_to_literature
                        if case == "attach"
                        else detach_tag_from_literature
                    )
                    actions = (
                        self.tag_attach_actions(
                            literature_id,
                            operation_tag_id,
                        )
                        if case == "attach"
                        else self.tag_detach_actions(
                            literature_id,
                            operation_tag_id,
                        )
                    )
                    with patch.object(
                        cli_module,
                        api_name,
                        wraps=original_api,
                    ) as write_api:
                        with self.assertRaises(RuntimeError) as raised:
                            run_cli(
                                connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    write_api.assert_called_once_with(
                        connection,
                        literature_id,
                        operation_tag_id,
                    )
                    after = self.table_snapshot_for(connection)
                    if case == "attach":
                        expected_mappings = sorted(
                            [
                                *before["literature_tags"],
                                (literature_id, operation_tag_id),
                            ]
                        )
                    else:
                        expected_mappings = [
                            row
                            for row in before["literature_tags"]
                            if row != (literature_id, operation_tag_id)
                        ]
                    self.assertEqual(
                        after["literature_tags"],
                        expected_mappings,
                    )
                    self.assertEqual(
                        after["literature"],
                        before["literature"],
                    )
                    self.assertEqual(after["tags"], before["tags"])
                    self.assertEqual(
                        after["usage_history"],
                        before["usage_history"],
                    )
                    self.assertEqual(
                        self.schema_snapshot_for(connection),
                        schema_before,
                    )
                    self.assertEqual(
                        connection.execute(
                            "PRAGMA schema_version"
                        ).fetchone()[0],
                        schema_version_before,
                    )
                    self.assertEqual(
                        connection.execute(
                            "PRAGMA user_version"
                        ).fetchone()[0],
                        user_version_before,
                    )
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(
                        connection.execute("SELECT 1").fetchone()[0],
                        1,
                    )
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertEqual(outputs.count(success_message), 1)
                    self.assertNotIn(
                        cli_module._DATABASE_ERROR_MESSAGE,
                        outputs,
                    )
                    self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_tag_list_uses_repository_order_and_preserves_objects_and_db(
        self,
    ) -> None:
        tag_ids = {
            name: create_tag(self.connection, name)
            for name in ("gamma", "Beta", "alpha")
        }
        tags_before = list_tags(self.connection)
        object_values_before = [vars(tag).copy() for tag in tags_before]
        tables_before = self.table_snapshot()

        with patch.object(
            cli_module,
            "list_tags",
            wraps=list_tags,
        ) as listed:
            _, _, outputs = self.run_with_actions(["6", "1", "0", "0"])

        listed.assert_called_once_with(self.connection)
        self.assertEqual(self.table_snapshot(), tables_before)
        self.assertEqual(
            [vars(tag) for tag in tags_before],
            object_values_before,
        )
        displayed_tags = [
            item for item in outputs if item.startswith("ID: ")
        ]
        self.assertEqual(
            displayed_tags,
            [
                f"ID: {tag_ids['alpha']}\nname: alpha",
                f"ID: {tag_ids['Beta']}\nname: Beta",
                f"ID: {tag_ids['gamma']}\nname: gamma",
            ],
        )
        self.assertEqual(outputs.count(cli_module._RECORD_SEPARATOR), 3)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_empty_tag_list_and_pending_read_transaction_are_preserved(
        self,
    ) -> None:
        _, _, empty_outputs = self.run_with_actions(["6", "1", "0", "0"])
        self.assertIn("登録されているタグはありません。", empty_outputs)

        marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-list-tag",),
        )
        self.assertTrue(self.connection.in_transaction)
        _, _, outputs = self.run_with_actions(["6", "1", "0", "0"])

        self.assertIn(
            f"ID: {marker.lastrowid}\nname: pending-list-tag",
            outputs,
        )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (marker.lastrowid,),
            ).fetchone()[0],
            1,
        )
        self.connection.rollback()
        self.assertEqual(list_tags(self.connection), [])

    def test_tag_list_api_exception_boundaries(self) -> None:
        exceptions = (
            sqlite3.OperationalError("tag list sqlite"),
            ValueError("tag list value"),
            RuntimeError("tag list runtime"),
            EOFError("tag list EOF"),
            KeyboardInterrupt(),
        )

        for expected in exceptions:
            with self.subTest(exception=type(expected).__name__):
                outputs: list[str] = []
                with patch.object(
                    cli_module,
                    "list_tags",
                    side_effect=expected,
                ) as listed:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(["6", "1"]),
                            output_func=outputs.append,
                        )

                self.assertIs(raised.exception, expected)
                listed.assert_called_once_with(self.connection)
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                else:
                    self.assertNotIn(
                        cli_module._DATABASE_ERROR_MESSAGE,
                        outputs,
                    )
                self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)

    def test_tag_create_uses_raw_name_and_changes_only_target_tag(self) -> None:
        literature_id = self.add_record("Tag create preserved literature")
        existing_tag_id = create_tag(self.connection, "existing")
        attach_tag_to_literature(
            self.connection,
            literature_id,
            existing_tag_id,
        )
        create_usage_history(
            self.connection,
            literature_id,
            "preserved-use",
        )
        self.connection.execute("PRAGMA user_version = 84")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        raw_name = "  肩関節  内部_記号!  "

        with patch.object(
            cli_module,
            "create_tag",
            wraps=create_tag,
        ) as created:
            _, _, outputs = self.run_with_actions(
                self.tag_create_actions(raw_name)
            )

        created.assert_called_once_with(self.connection, raw_name)
        tags = list_tags(self.connection)
        self.assertEqual(len(tags), 2)
        created_tag = next(tag for tag in tags if tag.id != existing_tag_id)
        self.assertEqual(created_tag.name, "肩関節  内部_記号!")
        self.assertEqual(
            self.table_snapshot()["literature"],
            before["literature"],
        )
        self.assertEqual(
            self.table_snapshot()["literature_tags"],
            before["literature_tags"],
        )
        self.assertEqual(
            self.table_snapshot()["usage_history"],
            before["usage_history"],
        )
        self.assertEqual(get_tag(self.connection, existing_tag_id).name, "existing")
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertIn("タグ登録内容を確認してください。", outputs)
        self.assertIn(f"name: {raw_name}", outputs)
        self.assertIn(
            "タグを登録または既存タグとして確認しました。",
            outputs,
        )
        self.assertIn(f"タグID: {created_tag.id}", outputs)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_tag_create_reuses_case_insensitive_existing_id_without_changes(
        self,
    ) -> None:
        literature_id = self.add_record("Existing tag reuse")
        existing_id = create_tag(self.connection, "Shoulder")
        attach_tag_to_literature(self.connection, literature_id, existing_id)
        before = self.table_snapshot()

        with patch.object(
            cli_module,
            "create_tag",
            wraps=create_tag,
        ) as created:
            _, _, outputs = self.run_with_actions(
                self.tag_create_actions("  shoulder  ")
            )

        created.assert_called_once_with(self.connection, "  shoulder  ")
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            list_tags(self.connection),
            [Tag(id=existing_id, name="Shoulder")],
        )
        self.assertIn(f"タグID: {existing_id}", outputs)
        self.assertNotIn("タグを新規作成しました。", outputs)

    def test_tag_create_blank_confirmation_loop_and_cancel_do_not_write(
        self,
    ) -> None:
        for blank_name in ("", " ", "\t\n"):
            with self.subTest(blank_name=repr(blank_name)):
                before = self.table_snapshot()
                with patch.object(cli_module, "create_tag") as created:
                    _, _, outputs = self.run_with_actions(
                        ["6", "2", blank_name, "0", "0"]
                    )
                created.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertIn("入力エラー: タグ名は必須です。", outputs)

        before = self.table_snapshot()
        invalid_count = 1200
        with patch.object(cli_module, "create_tag") as created:
            _, feeder, outputs = self.run_with_actions(
                [
                    "6",
                    "2",
                    "cancelled tag",
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " \t0\n ",
                    "0",
                    "0",
                ]
            )

        created.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("タグ登録を中止しました。", outputs)
        self.assertEqual(
            feeder.prompts.count("選択してください: "),
            invalid_count + 7,
        )

    def test_tag_create_rejects_initial_and_late_transactions_with_markers(
        self,
    ) -> None:
        initial_marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-create-initial",),
        )
        self.assertTrue(self.connection.in_transaction)
        with patch.object(cli_module, "create_tag") as created:
            _, feeder, outputs = self.run_with_actions(["6", "2", "0", "0"])

        created.assert_not_called()
        self.assertEqual(
            feeder.prompts,
            [
                "選択してください: ",
                "選択してください: ",
                "選択してください: ",
                "選択してください: ",
            ],
        )
        self.assertIn(
            cli_module._TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (initial_marker.lastrowid,),
            ).fetchone()[0],
            1,
        )
        self.connection.rollback()

        feeder = InputFeeder(
            ["6", "2", "late create", "1", "0", "0"]
        )
        late_marker_ids: list[int] = []

        def input_func(prompt: str) -> str:
            value = feeder(prompt)
            if value == "1" and len(feeder.prompts) == 4:
                self.assertFalse(self.connection.in_transaction)
                marker = self.connection.execute(
                    "INSERT INTO tags (name) VALUES (?)",
                    ("pending-create-late",),
                )
                late_marker_ids.append(marker.lastrowid)
                self.assertTrue(self.connection.in_transaction)
            return value

        outputs = []
        with patch.object(cli_module, "create_tag") as created:
            result = run_cli(
                self.connection,
                input_func=input_func,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        created.assert_not_called()
        self.assertEqual(len(late_marker_ids), 1)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (late_marker_ids[0],),
            ).fetchone()[0],
            1,
        )
        self.assertIn(
            cli_module._TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (late_marker_ids[0],),
            ).fetchone()[0],
            0,
        )

    def test_tag_create_api_exception_boundaries(self) -> None:
        exceptions = (
            ValueError("create tag value"),
            sqlite3.OperationalError("create tag sqlite"),
            RuntimeError("create tag runtime"),
            EOFError("create tag EOF"),
            KeyboardInterrupt(),
        )

        for expected in exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                actions: list[object] = ["6", "2", "api tag", "1"]
                if isinstance(expected, ValueError):
                    actions.extend(["0", "0"])
                with patch.object(
                    cli_module,
                    "create_tag",
                    side_effect=expected,
                ) as created:
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("タグ登録エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)

                created.assert_called_once_with(self.connection, "api tag")
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(
                        cli_module._DATABASE_ERROR_MESSAGE,
                        outputs,
                    )

    def test_tag_create_success_output_failure_keeps_committed_tag(self) -> None:
        connection, _, _, _ = self.create_tracking_tag_fixture(
            "create-success-output"
        )
        try:
            connection.execute("PRAGMA user_version = 86")
            before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            schema_version_before = connection.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            user_version_before = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            before_count = len(list_tags(connection))
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            expected = RuntimeError("tag create success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "タグを登録または既存タグとして確認しました。":
                    raise expected

            with patch.object(
                cli_module,
                "create_tag",
                wraps=create_tag,
            ) as created:
                with self.assertRaises(RuntimeError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            ["6", "2", "committed-created-tag", "1"]
                        ),
                        output_func=output_func,
                    )

            self.assertIs(raised.exception, expected)
            created.assert_called_once_with(
                connection,
                "committed-created-tag",
            )
            after = self.table_snapshot_for(connection)
            created_rows = [
                row
                for row in after["tags"]
                if row not in before["tags"]
            ]
            self.assertEqual(len(created_rows), 1)
            created_id, created_name = created_rows[0]
            self.assertEqual(
                get_tag(connection, created_id),
                Tag(id=created_id, name="committed-created-tag"),
            )
            expected_tags = sorted(
                [*before["tags"], (created_id, created_name)],
                key=lambda row: row[0],
            )
            self.assertEqual(after["tags"], expected_tags)
            self.assertEqual(len(list_tags(connection)), before_count + 1)
            self.assertEqual(
                [
                    tag.name
                    for tag in list_tags(connection)
                    if tag.name == "committed-created-tag"
                ],
                ["committed-created-tag"],
            )
            self.assertEqual(after["literature"], before["literature"])
            self.assertEqual(
                after["literature_tags"],
                before["literature_tags"],
            )
            self.assertEqual(
                after["usage_history"],
                before["usage_history"],
            )
            self.assertEqual(
                self.schema_snapshot_for(connection),
                schema_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                outputs.count(
                    "タグを登録または既存タグとして確認しました。"
                ),
                1,
            )
            self.assertFalse(
                any(item.startswith("タグ登録エラー: ") for item in outputs)
            )
            self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
            self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_tag_rename_validates_id_and_handles_missing_target(self) -> None:
        tag_id = create_tag(self.connection, "Rename ID target")
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                before = self.table_snapshot()
                with (
                    patch.object(cli_module, "get_tag") as retrieved,
                    patch.object(cli_module, "rename_tag") as renamed,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["6", "3", invalid_value, "0", "0"]
                    )
                retrieved.assert_not_called()
                renamed.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and "タグID" in item
                        and "ASCII" in item
                        for item in outputs
                    )
                )

        with (
            patch.object(
                cli_module,
                "get_tag",
                wraps=get_tag,
            ) as retrieved,
            patch.object(cli_module, "rename_tag") as renamed,
        ):
            _, _, outputs = self.run_with_actions(
                ["6", "3", "999999", "0", "0"]
            )
        retrieved.assert_called_once_with(self.connection, 999999)
        renamed.assert_not_called()
        self.assertIn("対象タグが見つかりません。", outputs)
        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="Rename ID target"),
        )

    def test_tag_rename_success_preserves_id_relationships_and_other_data(
        self,
    ) -> None:
        target_literature_id = self.add_record("Rename target literature")
        other_literature_id = self.add_record("Rename other literature")
        target_tag_id = create_tag(self.connection, "Shoulder")
        other_tag_id = create_tag(self.connection, "Other")
        attach_tag_to_literature(
            self.connection,
            target_literature_id,
            target_tag_id,
        )
        attach_tag_to_literature(
            self.connection,
            other_literature_id,
            other_tag_id,
        )
        create_usage_history(
            self.connection,
            target_literature_id,
            "rename-preserved-use",
        )
        self.connection.execute("PRAGMA user_version = 85")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        raw_new_name = "  ＳＨＯＵＬＤＥＲ  内部_記号!  "

        with (
            patch.object(
                cli_module,
                "get_tag",
                wraps=get_tag,
            ) as retrieved,
            patch.object(
                cli_module,
                "rename_tag",
                wraps=rename_tag,
            ) as renamed,
        ):
            _, _, outputs = self.run_with_actions(
                self.tag_rename_actions(
                    f" \t00{target_tag_id}\n ",
                    raw_new_name,
                )
            )

        retrieved.assert_called_once_with(self.connection, target_tag_id)
        renamed.assert_called_once_with(
            self.connection,
            target_tag_id,
            raw_new_name,
        )
        self.assertEqual(
            get_tag(self.connection, target_tag_id),
            Tag(id=target_tag_id, name="ＳＨＯＵＬＤＥＲ  内部_記号!"),
        )
        self.assertEqual(
            get_tag(self.connection, other_tag_id),
            Tag(id=other_tag_id, name="Other"),
        )
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["literature_tags"], before["literature_tags"])
        self.assertEqual(after["usage_history"], before["usage_history"])
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            85,
        )
        self.assertIn("現在のタグ情報:", outputs)
        self.assertIn(f"ID: {target_tag_id}\nname: Shoulder", outputs)
        self.assertIn("タグ名称の変更内容を確認してください。", outputs)
        self.assertIn(f"変更後: {raw_new_name}", outputs)
        self.assertIn("タグ名称を変更しました。", outputs)
        self.assertIn(f"タグID: {target_tag_id}", outputs)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_tag_rename_blank_confirmation_cancel_and_false_are_safe(
        self,
    ) -> None:
        tag_id = create_tag(self.connection, "Rename safety")
        before = self.table_snapshot()

        for blank_name in ("", " ", "\t\n"):
            with self.subTest(blank_name=repr(blank_name)):
                with patch.object(cli_module, "rename_tag") as renamed:
                    _, _, outputs = self.run_with_actions(
                        [
                            "6",
                            "3",
                            str(tag_id),
                            blank_name,
                            "0",
                            "0",
                        ]
                    )
                renamed.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertIn(
                    "入力エラー: 新しいタグ名は必須です。",
                    outputs,
                )

        invalid_count = 1200
        with patch.object(cli_module, "rename_tag") as renamed:
            _, _, outputs = self.run_with_actions(
                [
                    "6",
                    "3",
                    str(tag_id),
                    "Cancelled rename",
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " 0 ",
                    "0",
                    "0",
                ]
            )
        renamed.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("タグ名称変更を中止しました。", outputs)

        with patch.object(
            cli_module,
            "rename_tag",
            return_value=False,
        ) as renamed:
            _, _, outputs = self.run_with_actions(
                self.tag_rename_actions(tag_id, "Disappeared rename")
            )
        renamed.assert_called_once_with(
            self.connection,
            tag_id,
            "Disappeared rename",
        )
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("確認後に対象タグが存在しなくなりました。", outputs)
        self.assertNotIn("タグ名称を変更しました。", outputs)

    def test_tag_rename_rejects_initial_and_late_transactions_with_markers(
        self,
    ) -> None:
        target_id = create_tag(self.connection, "Transaction rename")
        initial_marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-rename-initial",),
        )
        target_before = get_tag(self.connection, target_id)
        self.assertTrue(self.connection.in_transaction)

        with (
            patch.object(cli_module, "get_tag") as retrieved,
            patch.object(cli_module, "rename_tag") as renamed,
        ):
            _, _, outputs = self.run_with_actions(["6", "3", "0", "0"])

        retrieved.assert_not_called()
        renamed.assert_not_called()
        self.assertEqual(get_tag(self.connection, target_id), target_before)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (initial_marker.lastrowid,),
            ).fetchone()[0],
            1,
        )
        self.assertTrue(self.connection.in_transaction)
        self.assertIn(
            cli_module._TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.connection.rollback()

        feeder = InputFeeder(
            ["6", "3", str(target_id), "Late renamed", "1", "0", "0"]
        )
        marker_ids: list[int] = []

        def input_func(prompt: str) -> str:
            value = feeder(prompt)
            if value == "1" and len(feeder.prompts) == 5:
                self.assertFalse(self.connection.in_transaction)
                marker = self.connection.execute(
                    "INSERT INTO tags (name) VALUES (?)",
                    ("pending-rename-late",),
                )
                marker_ids.append(marker.lastrowid)
                self.assertTrue(self.connection.in_transaction)
            return value

        outputs = []
        with patch.object(cli_module, "rename_tag") as renamed:
            result = run_cli(
                self.connection,
                input_func=input_func,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        renamed.assert_not_called()
        self.assertEqual(get_tag(self.connection, target_id), target_before)
        self.assertEqual(len(marker_ids), 1)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (marker_ids[0],),
            ).fetchone()[0],
            1,
        )
        self.assertIn(
            cli_module._TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE,
            outputs,
        )
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (marker_ids[0],),
            ).fetchone()[0],
            0,
        )

    def test_tag_get_and_rename_api_exception_boundaries(self) -> None:
        tag_id = create_tag(self.connection, "API rename target")
        exceptions = (
            sqlite3.OperationalError("tag API sqlite"),
            ValueError("tag API value"),
            RuntimeError("tag API runtime"),
            EOFError("tag API EOF"),
            KeyboardInterrupt(),
        )

        for api_name in ("get_tag", "rename_tag"):
            for expected in exceptions:
                with self.subTest(
                    api=api_name,
                    exception=type(expected).__name__,
                ):
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    actions: list[object] = ["6", "3", str(tag_id)]
                    if api_name == "rename_tag":
                        actions.extend(["Renamed API", "1"])
                        if isinstance(expected, ValueError):
                            actions.extend(["0", "0"])
                    with patch.object(
                        cli_module,
                        api_name,
                        side_effect=expected,
                    ) as failed_api:
                        if api_name == "rename_tag" and isinstance(
                            expected,
                            ValueError,
                        ):
                            result = run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                            self.assertIsNone(result)
                            self.assertTrue(
                                any(
                                    item.startswith(
                                        "タグ名称変更エラー: "
                                    )
                                    for item in outputs
                                )
                            )
                        else:
                            with self.assertRaises(type(expected)) as raised:
                                run_cli(
                                    self.connection,
                                    input_func=InputFeeder(actions),
                                    output_func=outputs.append,
                                )
                            self.assertIs(raised.exception, expected)

                    failed_api.assert_called_once()
                    self.assertEqual(self.table_snapshot(), before)
                    if isinstance(expected, sqlite3.Error):
                        self.assertEqual(
                            outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                            1,
                        )
                    elif not (
                        api_name == "rename_tag"
                        and isinstance(expected, ValueError)
                    ):
                        self.assertNotIn(
                            cli_module._DATABASE_ERROR_MESSAGE,
                            outputs,
                        )

    def test_tag_rename_real_duplicate_value_error_preserves_database(
        self,
    ) -> None:
        shoulder_id = create_tag(self.connection, "Shoulder")
        ultrasound_id = create_tag(self.connection, "Ultrasound")
        before = self.table_snapshot()

        _, _, outputs = self.run_with_actions(
            self.tag_rename_actions(ultrasound_id, "  shoulder  ")
        )

        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            get_tag(self.connection, shoulder_id),
            Tag(id=shoulder_id, name="Shoulder"),
        )
        self.assertEqual(
            get_tag(self.connection, ultrasound_id),
            Tag(id=ultrasound_id, name="Ultrasound"),
        )
        self.assertTrue(
            any(
                item.startswith("タグ名称変更エラー: ")
                and "既に存在" in item
                for item in outputs
            )
        )
        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_tag_rename_success_output_failure_keeps_committed_name(self) -> None:
        connection, _, target_id, other_tag_id = (
            self.create_tracking_tag_fixture("rename-success-output")
        )
        try:
            other_literature_id = add_literature(
                connection,
                Literature(title="Rename output other literature"),
            )
            attach_tag_to_literature(
                connection,
                other_literature_id,
                other_tag_id,
            )
            create_usage_history(
                connection,
                other_literature_id,
                "rename-output-other-use",
            )
            connection.execute("PRAGMA user_version = 87")
            before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            schema_version_before = connection.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            user_version_before = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            target_relationships_before = [
                row
                for row in before["literature_tags"]
                if row[1] == target_id
            ]
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            expected = RuntimeError("tag rename success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "タグ名称を変更しました。":
                    raise expected

            with patch.object(
                cli_module,
                "rename_tag",
                wraps=rename_tag,
            ) as renamed:
                with self.assertRaises(RuntimeError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            [
                                "6",
                                "3",
                                str(target_id),
                                "Committed renamed tag",
                                "1",
                            ]
                        ),
                        output_func=output_func,
                    )

            self.assertIs(raised.exception, expected)
            renamed.assert_called_once_with(
                connection,
                target_id,
                "Committed renamed tag",
            )
            after = self.table_snapshot_for(connection)
            expected_tags = [
                (
                    row[0],
                    "Committed renamed tag"
                    if row[0] == target_id
                    else row[1],
                )
                for row in before["tags"]
            ]
            self.assertEqual(after["tags"], expected_tags)
            self.assertEqual(
                get_tag(connection, target_id),
                Tag(id=target_id, name="Committed renamed tag"),
            )
            self.assertEqual(
                get_tag(connection, other_tag_id),
                Tag(id=other_tag_id, name="Ultrasound"),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature_tags WHERE tag_id = ?",
                    (target_id,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(after["literature"], before["literature"])
            self.assertEqual(
                after["literature_tags"],
                before["literature_tags"],
            )
            self.assertEqual(
                [
                    row
                    for row in after["literature_tags"]
                    if row[1] == target_id
                ],
                target_relationships_before,
            )
            self.assertEqual(
                after["usage_history"],
                before["usage_history"],
            )
            self.assertEqual(
                self.schema_snapshot_for(connection),
                schema_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(outputs.count("タグ名称を変更しました。"), 1)
            self.assertFalse(
                any(
                    item.startswith("タグ名称変更エラー: ")
                    for item in outputs
                )
            )
            self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
            self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_tag_delete_success_uses_repository_apis_and_isolated_cascades(
        self,
    ) -> None:
        first_literature_id = self.add_record("Tag delete first literature")
        second_literature_id = self.add_record("Tag delete second literature")
        target_tag_id = create_tag(self.connection, "Delete target tag")
        shared_tag_id = create_tag(self.connection, "Delete kept shared tag")
        other_tag_id = create_tag(self.connection, "Delete kept other tag")
        for literature_id, tag_id in (
            (first_literature_id, target_tag_id),
            (second_literature_id, target_tag_id),
            (first_literature_id, shared_tag_id),
            (second_literature_id, shared_tag_id),
            (second_literature_id, other_tag_id),
        ):
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        create_usage_history(
            self.connection,
            first_literature_id,
            "delete-tag-first-use",
        )
        create_usage_history(
            self.connection,
            second_literature_id,
            "delete-tag-second-use",
        )
        self.connection.execute("PRAGMA user_version = 88")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        with (
            patch.object(cli_module, "get_tag", wraps=get_tag) as retrieved,
            patch.object(
                cli_module,
                "delete_tag",
                wraps=delete_tag,
            ) as deleted,
        ):
            _, feeder, outputs = self.run_with_actions(
                [
                    "6",
                    "4",
                    f" \t00{target_tag_id}\n ",
                    "1",
                    str(other_tag_id),
                    f"00{target_tag_id}",
                    "0",
                    "0",
                ]
            )

        retrieved.assert_called_once_with(self.connection, target_tag_id)
        deleted.assert_called_once_with(self.connection, target_tag_id)
        self.assertIsNone(get_tag(self.connection, target_tag_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature_tags WHERE tag_id = ?",
                (target_tag_id,),
            ).fetchone()[0],
            0,
        )
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["usage_history"], before["usage_history"])
        self.assertEqual(
            after["tags"],
            [row for row in before["tags"] if row[0] != target_tag_id],
        )
        self.assertEqual(
            after["literature_tags"],
            [
                row
                for row in before["literature_tags"]
                if row[1] != target_tag_id
            ],
        )
        self.assertEqual(
            {
                tuple(row)
                for row in self.connection.execute(
                    """
                    SELECT literature_id, tag_id
                    FROM literature_tags
                    WHERE tag_id IN (?, ?)
                    """,
                    (shared_tag_id, other_tag_id),
                ).fetchall()
            },
            {
                (first_literature_id, shared_tag_id),
                (second_literature_id, shared_tag_id),
                (second_literature_id, other_tag_id),
            },
        )
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertIn(f"ID: {target_tag_id}\nname: Delete target tag", outputs)
        for warning in (
            "警告: タグレコード自体は削除されます。",
            "このタグとすべての文献との関連付けは削除されます。",
            "文献レコード自体は削除されません。",
            "使用履歴は削除されません。",
            "他のタグは削除されません。",
            "CLIには自動復元機能がありません。",
        ):
            self.assertIn(warning, outputs)
        self.assertIn("タグを削除しました。", outputs)
        self.assertIn(f"タグID: {target_tag_id}", outputs)
        self.assertIn("name: Delete target tag", outputs)
        self.assertIn(
            f"入力エラー: タグID {target_tag_id} または0を入力してください。",
            outputs,
        )
        self.assertTrue(
            any(
                prompt.startswith(
                    f"削除を確定するためタグID {target_tag_id}"
                )
                for prompt in feeder.prompts
            )
        )

    def test_tag_delete_id_validation_missing_and_confirmation_contract(
        self,
    ) -> None:
        target_tag_id = create_tag(self.connection, "Delete safety target")
        invalid_values = (
            "",
            "0",
            "-1",
            "+1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )
        for invalid_value in invalid_values:
            with self.subTest(stage="initial_id", value=invalid_value):
                before = self.table_snapshot()
                with (
                    patch.object(cli_module, "get_tag") as retrieved,
                    patch.object(cli_module, "delete_tag") as deleted,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["6", "4", invalid_value, "0", "0"]
                    )
                retrieved.assert_not_called()
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and "タグID" in item
                        and "ASCII" in item
                        for item in outputs
                    )
                )

        before = self.table_snapshot()
        with (
            patch.object(cli_module, "get_tag", wraps=get_tag) as retrieved,
            patch.object(cli_module, "delete_tag") as deleted,
        ):
            _, _, missing_outputs = self.run_with_actions(
                ["6", "4", "999999", "0", "0"]
            )
        retrieved.assert_called_once_with(self.connection, 999999)
        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("対象タグが見つかりません。", missing_outputs)

        invalid_count = 1200
        with patch.object(cli_module, "delete_tag") as deleted:
            _, _, cancel_outputs = self.run_with_actions(
                [
                    "6",
                    "4",
                    str(target_tag_id),
                    "",
                    "invalid",
                    *(["9"] * invalid_count),
                    " 0 ",
                    "0",
                    "0",
                ]
            )
        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            cancel_outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            invalid_count + 2,
        )
        self.assertIn("タグ削除を中止しました。", cancel_outputs)

        final_invalid_values = ("", "+1", "１", "999998")
        with patch.object(cli_module, "delete_tag") as deleted:
            _, _, final_cancel_outputs = self.run_with_actions(
                [
                    "6",
                    "4",
                    str(target_tag_id),
                    "1",
                    *final_invalid_values,
                    "0",
                    "0",
                    "0",
                ]
            )
        deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            final_cancel_outputs.count(
                f"入力エラー: タグID {target_tag_id} または0を入力してください。"
            ),
            len(final_invalid_values),
        )
        self.assertIn("タグ削除を中止しました。", final_cancel_outputs)

        with patch.object(
            cli_module,
            "delete_tag",
            return_value=False,
        ) as deleted:
            _, _, false_outputs = self.run_with_actions(
                self.tag_delete_actions(target_tag_id)
            )
        deleted.assert_called_once_with(self.connection, target_tag_id)
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("確認後に対象タグが存在しなくなりました。", false_outputs)
        self.assertNotIn("タグを削除しました。", false_outputs)

    def test_tag_delete_rejects_initial_and_late_transactions_with_markers(
        self,
    ) -> None:
        connection, _, target_id, _ = self.create_tracking_tag_fixture(
            "delete-transactions"
        )
        try:
            target_before = get_tag(connection, target_id)
            initial_marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("pending-tag-delete-initial",),
            )
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            with (
                patch.object(cli_module, "get_tag") as retrieved,
                patch.object(cli_module, "delete_tag") as deleted,
            ):
                _, _, outputs = self.run_with_actions(
                    ["6", "4", "0", "0"],
                    connection=connection,
                )
            retrieved.assert_not_called()
            deleted.assert_not_called()
            self.assertIn(
                cli_module._TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertTrue(connection.in_transaction)
            self.assertEqual(get_tag(connection, target_id), target_before)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (initial_marker.lastrowid,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            sqlite3.Connection.rollback(connection)

            feeder = InputFeeder(
                ["6", "4", str(target_id), "1", str(target_id), "0", "0"]
            )
            marker_ids: list[int] = []

            def input_func(prompt: str) -> str:
                value = feeder(prompt)
                if prompt.startswith("削除を確定するためタグID"):
                    marker = connection.execute(
                        "INSERT INTO tags (name) VALUES (?)",
                        ("pending-tag-delete-late",),
                    )
                    marker_ids.append(marker.lastrowid)
                return value

            outputs = []
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            with patch.object(cli_module, "delete_tag") as deleted:
                result = run_cli(
                    connection,
                    input_func=input_func,
                    output_func=outputs.append,
                )
            self.assertIsNone(result)
            deleted.assert_not_called()
            self.assertEqual(len(marker_ids), 1)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(get_tag(connection, target_id), target_before)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_ids[0],),
                ).fetchone()[0],
                1,
            )
            self.assertIn(
                cli_module._TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_tag_delete_repository_api_exception_boundaries(self) -> None:
        target_id = create_tag(self.connection, "Delete API target")
        exception_factories = (
            ("sqlite3.Error", lambda: sqlite3.OperationalError("delete API sqlite")),
            ("ValueError", lambda: ValueError("delete API value")),
            ("RuntimeError", lambda: RuntimeError("delete API runtime")),
            ("EOFError", lambda: EOFError("delete API EOF")),
            ("KeyboardInterrupt", KeyboardInterrupt),
        )

        for api_name in ("get_tag", "delete_tag"):
            for exception_name, exception_factory in exception_factories:
                with self.subTest(api=api_name, exception=exception_name):
                    expected = exception_factory()
                    before = self.table_snapshot()
                    actions = ["6", "4", str(target_id)]
                    if api_name == "delete_tag":
                        actions.extend(["1", str(target_id)])
                    outputs: list[str] = []
                    with (
                        patch.object(
                            cli_module,
                            api_name,
                            side_effect=expected,
                        ) as failed_api,
                        patch.object(cli_module, "delete_tag")
                        if api_name == "get_tag"
                        else patch.object(cli_module, "get_tag", wraps=get_tag),
                    ):
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )

                    self.assertIs(raised.exception, expected)
                    failed_api.assert_called_once()
                    self.assertEqual(self.table_snapshot(), before)
                    if isinstance(expected, sqlite3.Error):
                        self.assertEqual(
                            outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                            1,
                        )
                    else:
                        self.assertNotIn(
                            cli_module._DATABASE_ERROR_MESSAGE,
                            outputs,
                        )

    def test_real_sqlite_tag_delete_failure_rolls_back_and_rethrows_same_error(
        self,
    ) -> None:
        connection, literature_id, target_id, other_tag_id = (
            self.create_tracking_tag_fixture("delete-real-sqlite-failure")
        )
        try:
            other_literature_id = add_literature(
                connection,
                Literature(title="Delete failure other literature"),
            )
            attach_tag_to_literature(connection, other_literature_id, target_id)
            attach_tag_to_literature(
                connection,
                other_literature_id,
                other_tag_id,
            )
            create_usage_history(
                connection,
                other_literature_id,
                "delete-failure-other-use",
            )
            connection.execute(
                """
                CREATE TRIGGER reject_forced_tag_delete
                BEFORE DELETE ON tags
                BEGIN
                    SELECT RAISE(ABORT, 'forced CLI tag delete failure');
                END
                """
            )
            connection.commit()
            before = self.table_snapshot_for(connection)
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            api_errors: list[sqlite3.Error] = []

            def tracked_delete(
                candidate_connection: sqlite3.Connection,
                tag_id: int,
            ) -> bool:
                try:
                    return delete_tag(candidate_connection, tag_id)
                except sqlite3.Error as error:
                    api_errors.append(error)
                    raise

            outputs: list[str] = []
            with patch.object(
                cli_module,
                "delete_tag",
                side_effect=tracked_delete,
            ) as deleted:
                with self.assertRaises(sqlite3.IntegrityError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            ["6", "4", str(target_id), "1", str(target_id)]
                        ),
                        output_func=outputs.append,
                    )

            deleted.assert_called_once_with(connection, target_id)
            self.assertEqual(len(api_errors), 1)
            self.assertIs(raised.exception, api_errors[0])
            self.assertEqual(self.table_snapshot_for(connection), before)
            self.assertIsNotNone(get_tag(connection, target_id))
            self.assertIsNotNone(get_tag(connection, other_tag_id))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature_tags WHERE tag_id = ?",
                    (target_id,),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature WHERE id IN (?, ?)",
                    (literature_id, other_literature_id),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM usage_history"
                ).fetchone()[0],
                2,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                1,
            )
            self.assertNotIn("タグを削除しました。", outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_tag_delete_success_output_failure_keeps_committed_cascade(
        self,
    ) -> None:
        connection, literature_id, target_id, other_tag_id = (
            self.create_tracking_tag_fixture("delete-success-output")
        )
        try:
            second_literature_id = add_literature(
                connection,
                Literature(title="Delete output second literature"),
            )
            attach_tag_to_literature(connection, second_literature_id, target_id)
            attach_tag_to_literature(
                connection,
                second_literature_id,
                other_tag_id,
            )
            create_usage_history(
                connection,
                second_literature_id,
                "delete-output-second-use",
            )
            connection.execute("PRAGMA user_version = 89")
            before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            schema_version_before = connection.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            user_version_before = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            expected = RuntimeError("tag delete success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "タグを削除しました。":
                    raise expected

            with patch.object(
                cli_module,
                "delete_tag",
                wraps=delete_tag,
            ) as deleted:
                with self.assertRaises(RuntimeError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            ["6", "4", str(target_id), "1", str(target_id)]
                        ),
                        output_func=output_func,
                    )

            self.assertIs(raised.exception, expected)
            deleted.assert_called_once_with(connection, target_id)
            after = self.table_snapshot_for(connection)
            self.assertEqual(
                after["tags"],
                [row for row in before["tags"] if row[0] != target_id],
            )
            self.assertEqual(
                after["literature_tags"],
                [row for row in before["literature_tags"] if row[1] != target_id],
            )
            self.assertEqual(after["literature"], before["literature"])
            self.assertEqual(after["usage_history"], before["usage_history"])
            self.assertIsNone(get_tag(connection, target_id))
            self.assertIsNotNone(get_tag(connection, other_tag_id))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature WHERE id IN (?, ?)",
                    (literature_id, second_literature_id),
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                self.schema_snapshot_for(connection),
                schema_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(outputs.count("タグを削除しました。"), 1)
            self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
            self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_tag_delete_output_interruptions_are_not_input_interruptions(
        self,
    ) -> None:
        target_id = create_tag(self.connection, "Delete output target")
        exceptions = (
            EOFError("tag delete output EOF"),
            KeyboardInterrupt(),
            sqlite3.OperationalError("tag delete output sqlite"),
        )
        for expected in exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "警告: タグレコード自体は削除されます。":
                        raise expected

                with patch.object(cli_module, "delete_tag") as deleted:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                ["6", "4", str(target_id)]
                            ),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, expected)
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_tag_input_exception_matrix_preserves_state_and_boundaries(
        self,
    ) -> None:
        positions = (
            (
                "submenu",
                lambda _: ["6"],
                ["選択してください: ", "選択してください: "],
            ),
            (
                "create_name",
                lambda _: ["6", "2"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグ名（必須）: ",
                ],
            ),
            (
                "create_confirmation",
                lambda _: ["6", "2", "Input tag"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグ名（必須）: ",
                    "選択してください: ",
                ],
            ),
            (
                "rename_id",
                lambda _: ["6", "3"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                ],
            ),
            (
                "rename_new_name",
                lambda tag_id: ["6", "3", str(tag_id)],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                    "新しいタグ名（必須）: ",
                ],
            ),
            (
                "rename_confirmation",
                lambda tag_id: ["6", "3", str(tag_id), "Input renamed"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                    "新しいタグ名（必須）: ",
                    "選択してください: ",
                ],
            ),
            (
                "delete_id",
                lambda _: ["6", "4"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                ],
            ),
            (
                "delete_confirmation",
                lambda tag_id: ["6", "4", str(tag_id)],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                    "選択してください: ",
                ],
            ),
            (
                "delete_final_id",
                lambda tag_id: ["6", "4", str(tag_id), "1"],
                [
                    "選択してください: ",
                    "選択してください: ",
                    "タグID（ASCII数字）: ",
                    "選択してください: ",
                    f"削除を確定するためタグID 1 を再入力してください\n"
                    "（0で中止）: ",
                ],
            ),
        )
        exception_types = (
            ("EOFError", EOFError),
            ("KeyboardInterrupt", KeyboardInterrupt),
            ("ValueError", ValueError),
            ("RuntimeError", RuntimeError),
            ("sqlite3.Error", sqlite3.Error),
        )
        database_paths: set[Path] = set()

        for position, prefix_factory, expected_prompts in positions:
            for exception_name, exception_type in exception_types:
                with self.subTest(
                    position=position,
                    exception=exception_name,
                ):
                    with tempfile.TemporaryDirectory() as temporary_directory:
                        database_path = Path(temporary_directory) / "test.db"
                        self.assertNotIn(database_path, database_paths)
                        database_paths.add(database_path)
                        initialize_database(database_path)
                        connection = connect_database(database_path)
                        try:
                            literature_id = add_literature(
                                connection,
                                Literature(title="Input matrix literature"),
                            )
                            target_id = create_tag(
                                connection,
                                "Input matrix target",
                            )
                            attach_tag_to_literature(
                                connection,
                                literature_id,
                                target_id,
                            )
                            create_usage_history(
                                connection,
                                literature_id,
                                "input-matrix-use",
                            )
                            before = self.table_snapshot_for(connection)
                            expected = exception_type(
                                f"{position} {exception_name} input failure"
                            )
                            outputs: list[str] = []
                            feeder = InputFeeder(
                                [*prefix_factory(target_id), expected]
                            )

                            with (
                                patch.object(
                                    cli_module,
                                    "create_tag",
                                    wraps=cli_module.create_tag,
                                ) as created,
                                patch.object(
                                    cli_module,
                                    "rename_tag",
                                    wraps=cli_module.rename_tag,
                                ) as renamed,
                                patch.object(
                                    cli_module,
                                    "delete_tag",
                                    wraps=cli_module.delete_tag,
                                ) as deleted,
                            ):
                                if isinstance(
                                    expected,
                                    (EOFError, KeyboardInterrupt),
                                ):
                                    result = run_cli(
                                        connection,
                                        input_func=feeder,
                                        output_func=outputs.append,
                                    )
                                    self.assertIsNone(result)
                                    self.assertEqual(
                                        outputs.count(cli_module._EXIT_MESSAGE),
                                        1,
                                    )
                                else:
                                    with self.assertRaises(
                                        exception_type
                                    ) as raised:
                                        run_cli(
                                            connection,
                                            input_func=feeder,
                                            output_func=outputs.append,
                                        )
                                    self.assertIs(raised.exception, expected)
                                    self.assertNotIn(
                                        cli_module._EXIT_MESSAGE,
                                        outputs,
                                    )

                            self.assertEqual(feeder.prompts, expected_prompts)
                            created.assert_not_called()
                            renamed.assert_not_called()
                            deleted.assert_not_called()
                            self.assertEqual(
                                self.table_snapshot_for(connection),
                                before,
                            )
                            self.assertNotIn(
                                cli_module._DATABASE_ERROR_MESSAGE,
                                outputs,
                            )
                            self.assertFalse(
                                any(
                                    item.startswith("入力エラー: ")
                                    or item.startswith("タグ登録エラー: ")
                                    or item.startswith("タグ名称変更エラー: ")
                                    for item in outputs
                                )
                            )
                            self.assertFalse(connection.in_transaction)
                            self.assertEqual(
                                connection.execute("SELECT 1").fetchone()[0],
                                1,
                            )
                        finally:
                            connection.close()

        self.assertEqual(
            len(database_paths),
            len(positions) * len(exception_types),
        )

    def test_tag_output_exception_matrix_propagates_without_retry(
        self,
    ) -> None:
        stages = (
            "submenu_menu",
            "list_nonempty",
            "list_empty",
            "create_initial_transaction",
            "create_name_error",
            "create_confirmation",
            "create_cancel",
            "create_api_error",
            "create_success",
            "rename_initial_transaction",
            "rename_id_error",
            "rename_missing",
            "rename_current",
            "rename_name_error",
            "rename_confirmation",
            "rename_cancel",
            "rename_api_error",
            "rename_false",
            "rename_success",
        )

        for stage_index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                connection, _, target_id, _ = self.create_tracking_tag_fixture(
                    f"output-matrix-{stage_index}"
                )
                try:
                    expected = RuntimeError(f"{stage} output failure")
                    api_error = ValueError(f"{stage} API value failure")
                    list_side_effect = list_tags
                    create_side_effect = create_tag
                    get_side_effect = get_tag
                    rename_side_effect = rename_tag

                    if stage == "submenu_menu":
                        actions: list[object] = ["6"]
                        failing_message = cli_module._TAG_MANAGEMENT_MENU
                    elif stage == "list_nonempty":
                        actions = ["6", "1"]
                        failing_message = "ID: 1\nname: Shoulder"
                    elif stage == "list_empty":
                        actions = ["6", "1"]
                        failing_message = "登録されているタグはありません。"
                        list_side_effect = lambda _: []
                    elif stage == "create_initial_transaction":
                        connection.execute(
                            "INSERT INTO tags (name) VALUES (?)",
                            ("pending-create-output",),
                        )
                        actions = ["6", "2"]
                        failing_message = (
                            cli_module._TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "create_name_error":
                        actions = ["6", "2", ""]
                        failing_message = "入力エラー: タグ名は必須です。"
                    elif stage == "create_confirmation":
                        actions = ["6", "2", "Output create"]
                        failing_message = "タグ登録内容を確認してください。"
                    elif stage == "create_cancel":
                        actions = ["6", "2", "Output create", "0"]
                        failing_message = "タグ登録を中止しました。"
                    elif stage == "create_api_error":
                        actions = ["6", "2", "Output create", "1"]
                        failing_message = f"タグ登録エラー: {api_error}"

                        def create_side_effect(*args: object) -> int:
                            raise api_error

                    elif stage == "create_success":
                        actions = ["6", "2", "Output created", "1"]
                        failing_message = (
                            "タグを登録または既存タグとして確認しました。"
                        )
                    elif stage == "rename_initial_transaction":
                        connection.execute(
                            "INSERT INTO tags (name) VALUES (?)",
                            ("pending-rename-output",),
                        )
                        actions = ["6", "3"]
                        failing_message = (
                            cli_module._TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE
                        )
                    elif stage == "rename_id_error":
                        actions = ["6", "3", "invalid"]
                        failing_message = (
                            "入力エラー: タグIDは1以上の"
                            "ASCII数字だけで入力してください。"
                        )
                    elif stage == "rename_missing":
                        actions = ["6", "3", "999999"]
                        failing_message = "対象タグが見つかりません。"
                    elif stage == "rename_current":
                        actions = ["6", "3", str(target_id)]
                        failing_message = "現在のタグ情報:"
                    elif stage == "rename_name_error":
                        actions = ["6", "3", str(target_id), ""]
                        failing_message = (
                            "入力エラー: 新しいタグ名は必須です。"
                        )
                    elif stage == "rename_confirmation":
                        actions = [
                            "6",
                            "3",
                            str(target_id),
                            "Output renamed",
                        ]
                        failing_message = (
                            "タグ名称の変更内容を確認してください。"
                        )
                    elif stage == "rename_cancel":
                        actions = [
                            "6",
                            "3",
                            str(target_id),
                            "Output renamed",
                            "0",
                        ]
                        failing_message = "タグ名称変更を中止しました。"
                    elif stage == "rename_api_error":
                        actions = [
                            "6",
                            "3",
                            str(target_id),
                            "Output renamed",
                            "1",
                        ]
                        failing_message = f"タグ名称変更エラー: {api_error}"

                        def rename_side_effect(*args: object) -> bool:
                            raise api_error

                    elif stage == "rename_false":
                        actions = [
                            "6",
                            "3",
                            str(target_id),
                            "Output renamed",
                            "1",
                        ]
                        failing_message = (
                            "確認後に対象タグが存在しなくなりました。"
                        )
                        rename_side_effect = lambda *args: False
                    else:
                        actions = [
                            "6",
                            "3",
                            str(target_id),
                            "Output renamed",
                            "1",
                        ]
                        failing_message = "タグ名称を変更しました。"

                    before = self.table_snapshot_for(connection)
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with (
                        patch.object(
                            cli_module,
                            "list_tags",
                            side_effect=list_side_effect,
                        ) as listed,
                        patch.object(
                            cli_module,
                            "create_tag",
                            side_effect=create_side_effect,
                        ) as created,
                        patch.object(
                            cli_module,
                            "get_tag",
                            side_effect=get_side_effect,
                        ) as retrieved,
                        patch.object(
                            cli_module,
                            "rename_tag",
                            side_effect=rename_side_effect,
                        ) as renamed,
                    ):
                        with self.assertRaises(RuntimeError) as raised:
                            run_cli(
                                connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    self.assertEqual(outputs.count(failing_message), 1)
                    self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                    self.assertNotIn(
                        cli_module._DATABASE_ERROR_MESSAGE,
                        outputs,
                    )
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    if stage == "create_success":
                        created.assert_called_once()
                        self.assertEqual(
                            get_tag(
                                connection,
                                max(tag.id for tag in list_tags(connection)),
                            ).name,
                            "Output created",
                        )
                        self.assertFalse(connection.in_transaction)
                    elif stage == "rename_success":
                        renamed.assert_called_once()
                        self.assertEqual(
                            get_tag(connection, target_id).name,
                            "Output renamed",
                        )
                        self.assertFalse(connection.in_transaction)
                    else:
                        self.assertEqual(
                            self.table_snapshot_for(connection),
                            before,
                        )
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_tag_output_interruptions_and_sqlite_errors_are_not_reclassified(
        self,
    ) -> None:
        tag_id = create_tag(self.connection, "Output boundary target")
        exceptions = (
            EOFError("tag output EOF"),
            KeyboardInterrupt(),
            sqlite3.OperationalError("tag output sqlite"),
        )

        for expected in exceptions:
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == "現在のタグ情報:":
                        raise expected

                with patch.object(cli_module, "rename_tag") as renamed:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                ["6", "3", str(tag_id)]
                            ),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, expected)
                renamed.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                self.assertNotIn(
                    cli_module._DATABASE_ERROR_MESSAGE,
                    outputs,
                )

    def test_tag_database_error_output_failure_propagates_output_error(
        self,
    ) -> None:
        tag_id = create_tag(self.connection, "DB output target")
        cases = (
            ("list_tags", ["6", "1"]),
            ("create_tag", ["6", "2", "DB create", "1"]),
            ("get_tag", ["6", "3", str(tag_id)]),
            (
                "rename_tag",
                ["6", "3", str(tag_id), "DB rename", "1"],
            ),
            (
                "delete_tag",
                ["6", "4", str(tag_id), "1", str(tag_id)],
            ),
        )

        for api_name, actions in cases:
            with self.subTest(api=api_name):
                database_error = sqlite3.OperationalError(
                    f"{api_name} database failure"
                )
                output_error = RuntimeError(
                    f"{api_name} database output failure"
                )

                def output_func(message: str) -> None:
                    if message == cli_module._DATABASE_ERROR_MESSAGE:
                        raise output_error

                with patch.object(
                    cli_module,
                    api_name,
                    side_effect=database_error,
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, output_error)
                self.assertIsNot(raised.exception, database_error)

    def test_tag_transaction_rejections_call_no_lifecycle_methods(self) -> None:
        connection, _, target_id, _ = self.create_tracking_tag_fixture(
            "transaction-lifecycle"
        )
        try:
            marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("pending-tag-lifecycle",),
            )
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0

            _, _, outputs = self.run_with_actions(
                ["6", "2", "3", "4", "0", "0"],
                connection=connection,
            )

            self.assertIn(
                cli_module._TAG_CREATE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertIn(
                cli_module._TAG_RENAME_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertIn(
                cli_module._TAG_DELETE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker.lastrowid,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                get_tag(connection, target_id),
                Tag(id=target_id, name="Shoulder"),
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_usage_history_main_and_submenu_contracts_and_dispatch(self) -> None:
        invalid_count = 1200
        feeder = InputFeeder(
            [
                "14",
                "7",
                "5",
                "invalid",
                *(["9"] * invalid_count),
                "1",
                "2",
                "3",
                "4",
                "0",
                "0",
            ]
        )
        outputs: list[str] = []

        with (
            patch.object(
                cli_module,
                "_run_literature_usage_history_list",
                return_value=False,
            ) as list_flow,
            patch.object(
                cli_module,
                "_run_usage_history_create",
                return_value=False,
            ) as create_flow,
            patch.object(
                cli_module,
                "_run_usage_history_edit",
                return_value=False,
            ) as edit_flow,
            patch.object(
                cli_module,
                "_run_usage_history_delete",
                return_value=False,
            ) as delete_flow,
        ):
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        self.assertIn("7. 使用履歴管理", outputs[0])
        self.assertNotIn("8. 終了", outputs[0])
        self.assertEqual(outputs.count(cli_module._INVALID_MENU_MESSAGE), 1)
        self.assertIn("1. 文献別使用履歴一覧", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertIn("2. 使用履歴登録", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertIn("3. 使用履歴編集", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertIn("4. 使用履歴削除", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertIn("0. メインメニューに戻る", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertNotIn("5. メインメニューに戻る", cli_module._USAGE_HISTORY_MANAGEMENT_MENU)
        self.assertEqual(
            outputs.count(cli_module._INVALID_USAGE_HISTORY_MENU_MESSAGE),
            invalid_count + 2,
        )
        list_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        create_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        edit_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        delete_flow.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

    def test_usage_history_ids_are_positive_ascii_and_missing_stops_apis(
        self,
    ) -> None:
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )
        for operation in ("1", "2"):
            for invalid_value in invalid_values:
                with self.subTest(operation=operation, value=invalid_value):
                    before = self.table_snapshot()
                    with (
                        patch.object(cli_module, "get_literature") as gotten,
                        patch.object(
                            cli_module,
                            "list_usage_history_for_literature",
                        ) as listed,
                        patch.object(
                            cli_module,
                            "create_usage_history",
                        ) as created,
                    ):
                        _, _, outputs = self.run_with_actions(
                            ["7", operation, invalid_value, "0", "0"]
                        )

                    gotten.assert_not_called()
                    listed.assert_not_called()
                    created.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertTrue(
                        any(
                            item.startswith("入力エラー: ")
                            and "ASCII" in item
                            for item in outputs
                        )
                    )

        for operation in ("1", "2"):
            with self.subTest(operation=operation, target="missing"):
                with (
                    patch.object(
                        cli_module,
                        "list_usage_history_for_literature",
                    ) as listed,
                    patch.object(
                        cli_module,
                        "create_usage_history",
                    ) as created,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["7", operation, "999999", "0", "0"]
                    )

                listed.assert_not_called()
                created.assert_not_called()
                self.assertIn("対象文献が見つかりません。", outputs)

        literature_id = self.add_record("Usage padded ID")
        padded_id = f"  000{literature_id}\t"
        _, _, list_outputs = self.run_with_actions(
            self.literature_usage_history_list_actions(padded_id)
        )
        self.assertIn(f"文献ID: {literature_id}", list_outputs)
        _, _, create_outputs = self.run_with_actions(
            self.usage_history_create_actions(padded_id, "note")
        )
        self.assertIn("使用履歴を登録しました。", create_outputs)

    def test_usage_history_list_empty_race_order_fields_and_read_safety(
        self,
    ) -> None:
        literature_id = self.add_record("Usage list target")
        other_id = self.add_record("Usage list other")
        first_id = create_usage_history(
            self.connection,
            literature_id,
            "note",
            project_name="AHD article",
            usage_note="Methods",
            used_at="2026-08-01",
        )
        second_id = create_usage_history(
            self.connection,
            literature_id,
            "論文",
        )
        other_history_id = create_usage_history(
            self.connection,
            other_id,
            "other",
        )
        pending = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-usage-list",),
        )
        before = self.table_snapshot()

        with patch.object(
            cli_module,
            "list_usage_history_for_literature",
            wraps=list_usage_history_for_literature,
        ) as listed:
            _, _, outputs = self.run_with_actions(
                self.literature_usage_history_list_actions(literature_id)
            )

        listed.assert_called_once_with(self.connection, literature_id)
        displayed = "\n".join(outputs)
        self.assertIn(f"文献ID: {literature_id}", outputs)
        self.assertIn("title: Usage list target", outputs)
        self.assertLess(
            displayed.index(f"id: {first_id}"),
            displayed.index(f"id: {second_id}"),
        )
        self.assertNotIn(f"id: {other_history_id}", displayed)
        for label in (
            "id",
            "literature_id",
            "usage_type",
            "project_name",
            "usage_note",
            "used_at",
            "created_at",
        ):
            self.assertIn(f"{label}:", displayed)
        self.assertGreaterEqual(displayed.count("未登録"), 3)
        self.assertEqual(self.table_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?",
                (pending.lastrowid,),
            ).fetchone()[0],
            1,
        )
        self.connection.rollback()

        empty_id = self.add_record("Usage list empty")
        _, _, empty_outputs = self.run_with_actions(
            self.literature_usage_history_list_actions(empty_id)
        )
        self.assertIn("この文献には使用履歴がありません。", empty_outputs)

        with patch.object(
            cli_module,
            "list_usage_history_for_literature",
            return_value=None,
        ):
            _, _, race_outputs = self.run_with_actions(
                self.literature_usage_history_list_actions(empty_id)
            )
        self.assertIn(
            "文献情報の確認後に対象文献が存在しなくなりました。",
            race_outputs,
        )

    def test_usage_history_create_success_preserves_data_and_schema(self) -> None:
        literature_id = self.add_record("Usage create target")
        other_id = self.add_record("Usage create other")
        tag_id = create_tag(self.connection, "usage-create-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        existing_id = create_usage_history(
            self.connection,
            literature_id,
            "existing",
        )
        create_usage_history(self.connection, other_id, "other")
        self.connection.execute("PRAGMA user_version = 108")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        raw_usage_type = "  独自用途  "
        raw_project_name = "  Project name  "
        raw_usage_note = "\tNote contents \n"

        with patch.object(
            cli_module,
            "create_usage_history",
            wraps=create_usage_history,
        ) as created:
            _, _, outputs = self.run_with_actions(
                self.usage_history_create_actions(
                    literature_id,
                    raw_usage_type,
                    raw_project_name,
                    raw_usage_note,
                    "2026-08-17",
                )
            )

        created.assert_called_once_with(
            self.connection,
            literature_id,
            raw_usage_type,
            raw_project_name,
            raw_usage_note,
            "2026-08-17",
        )
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["tags"], before["tags"])
        self.assertEqual(after["literature_tags"], before["literature_tags"])
        self.assertEqual(after["usage_history"][:-1], before["usage_history"])
        self.assertEqual(len(after["usage_history"]), len(before["usage_history"]) + 1)
        new_row = after["usage_history"][-1]
        self.assertEqual(
            new_row[1:6],
            (
                literature_id,
                "独自用途",
                raw_project_name,
                raw_usage_note,
                "2026-08-17",
            ),
        )
        self.assertIsInstance(new_row[6], str)
        self.assertTrue(str(new_row[6]).endswith("Z"))
        datetime.fromisoformat(str(new_row[6]).replace("Z", "+00:00"))
        self.assertTrue(any(row[0] == existing_id for row in after["usage_history"]))
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertIn("使用履歴を登録しました。", outputs)
        self.assertIn(f"使用履歴ID: {new_row[0]}", outputs)
        self.assertIn(f"文献ID: {literature_id}", outputs)

    def test_usage_history_create_optional_validation_and_confirmation(self) -> None:
        literature_id = self.add_record("Usage optional target")

        _, _, outputs = self.run_with_actions(
            self.usage_history_create_actions(
                literature_id,
                "note",
                "  ",
                "\t",
                "\n",
            )
        )
        rows = list_usage_history_for_literature(
            self.connection,
            literature_id,
        )
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].project_name)
        self.assertIsNone(rows[0].usage_note)
        self.assertIsNone(rows[0].used_at)
        self.assertIn("project_name: 未登録", outputs)

        for case, actions, expected_message in (
            (
                "blank usage type",
                ["7", "2", str(literature_id), "  ", "0", "0"],
                "入力エラー: usage_typeは必須です。",
            ),
            (
                "invalid date",
                self.usage_history_create_actions(
                    literature_id,
                    "note",
                    used_at="2025-02-29",
                ),
                "使用履歴登録エラー:",
            ),
            (
                "confirmation loop and cancel",
                [
                    "7",
                    "2",
                    str(literature_id),
                    "論文",
                    "project",
                    "note",
                    "",
                    "9",
                    "2",
                    "0",
                    "0",
                    "0",
                ],
                "使用履歴登録を中止しました。",
            ),
        ):
            with self.subTest(case=case):
                before = self.table_snapshot()
                with patch.object(
                    cli_module,
                    "create_usage_history",
                    wraps=create_usage_history,
                ) as created:
                    _, _, case_outputs = self.run_with_actions(actions)

                self.assertEqual(self.table_snapshot(), before)
                self.assertTrue(
                    any(item.startswith(expected_message) for item in case_outputs)
                )
                if case == "invalid date":
                    created.assert_called_once()
                else:
                    created.assert_not_called()

    def test_usage_history_create_rejects_initial_and_late_transactions(
        self,
    ) -> None:
        tracking_path = self.directory / "usage-transaction.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        literature_id = add_literature(
            connection,
            Literature(title="Usage transaction target"),
        )
        try:
            initial_marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("pending-usage-initial",),
            )
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            with patch.object(cli_module, "create_usage_history") as created:
                _, _, outputs = self.run_with_actions(
                    ["7", "2", "0", "0"],
                    connection=connection,
                )

            created.assert_not_called()
            self.assertIn(
                cli_module._USAGE_HISTORY_CREATE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (initial_marker.lastrowid,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            sqlite3.Connection.rollback(connection)

            feeder = InputFeeder(
                self.usage_history_create_actions(literature_id, "note")
            )
            late_markers: list[int] = []

            def input_func(prompt: str) -> str:
                value = feeder(prompt)
                if value == "1" and len(feeder.prompts) == 8:
                    marker = connection.execute(
                        "INSERT INTO tags (name) VALUES (?)",
                        ("pending-usage-late",),
                    )
                    late_markers.append(marker.lastrowid)
                return value

            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            outputs = []
            with patch.object(cli_module, "create_usage_history") as created:
                result = run_cli(
                    connection,
                    input_func=input_func,
                    output_func=outputs.append,
                )

            self.assertIsNone(result)
            created.assert_not_called()
            self.assertEqual(len(late_markers), 1)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (late_markers[0],),
                ).fetchone()[0],
                1,
            )
            self.assertIn(
                cli_module._USAGE_HISTORY_CREATE_ACTIVE_TRANSACTION_MESSAGE,
                outputs,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_usage_history_repository_exception_boundaries(self) -> None:
        literature_id = self.add_record("Usage API errors")
        cases = (
            (
                "list get",
                "get_literature",
                ["7", "1", str(literature_id)],
            ),
            (
                "create get",
                "get_literature",
                ["7", "2", str(literature_id)],
            ),
            (
                "list API",
                "list_usage_history_for_literature",
                ["7", "1", str(literature_id)],
            ),
        )
        for case, api_name, actions in cases:
            for expected in (
                sqlite3.OperationalError(f"{case} sqlite"),
                RuntimeError(f"{case} runtime"),
            ):
                with self.subTest(case=case, exception=type(expected).__name__):
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    with patch.object(
                        cli_module,
                        api_name,
                        side_effect=expected,
                    ) as failed_api:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )

                    self.assertIs(raised.exception, expected)
                    failed_api.assert_called_once_with(
                        self.connection,
                        literature_id,
                    )
                    self.assertEqual(self.table_snapshot(), before)
                    if isinstance(expected, sqlite3.Error):
                        self.assertEqual(
                            outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                            1,
                        )
                    else:
                        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_usage_history_create_api_exception_boundaries(self) -> None:
        literature_id = self.add_record("Usage create API errors")
        for expected in (
            ValueError("usage value"),
            sqlite3.OperationalError("usage sqlite"),
            RuntimeError("usage runtime"),
            EOFError("usage API EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                actions: list[object] = self.usage_history_create_actions(
                    literature_id,
                    "custom use",
                    "project",
                    "note",
                    "2026-08-17",
                )
                if not isinstance(expected, ValueError):
                    actions = actions[:-2]
                with patch.object(
                    cli_module,
                    "create_usage_history",
                    side_effect=expected,
                ) as created:
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("使用履歴登録エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)

                created.assert_called_once_with(
                    self.connection,
                    literature_id,
                    "custom use",
                    "project",
                    "note",
                    "2026-08-17",
                )
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

        for api_error in (
            ValueError("usage error before output failure"),
            sqlite3.OperationalError("usage database error before output failure"),
        ):
            with self.subTest(error_output=type(api_error).__name__):
                output_error = RuntimeError("usage error output failure")

                def output_func(message: str) -> None:
                    if message.startswith("使用履歴登録エラー: ") or message == (
                        cli_module._DATABASE_ERROR_MESSAGE
                    ):
                        raise output_error

                with patch.object(
                    cli_module,
                    "create_usage_history",
                    side_effect=api_error,
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                self.usage_history_create_actions(
                                    literature_id,
                                    "custom use",
                                )[:-2]
                            ),
                            output_func=output_func,
                        )

                self.assertIs(raised.exception, output_error)
                self.assertIsNot(raised.exception, api_error)

    def test_real_sqlite_usage_history_insert_failure_is_atomic(self) -> None:
        tracking_path = self.directory / "usage-insert-failure.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        literature_id = add_literature(
            connection,
            Literature(title="Usage failure target"),
        )
        other_id = add_literature(
            connection,
            Literature(title="Usage failure other"),
        )
        create_usage_history(connection, literature_id, "kept target")
        create_usage_history(connection, other_id, "kept other")
        tag_id = create_tag(connection, "usage-failure-tag")
        attach_tag_to_literature(connection, literature_id, tag_id)
        connection.execute("PRAGMA user_version = 109")
        connection.execute(
            """
            CREATE TRIGGER force_cli_usage_insert_failure
            BEFORE INSERT ON usage_history
            BEGIN
                SELECT RAISE(ABORT, 'forced CLI usage insert failure');
            END
            """
        )
        connection.commit()
        before = self.table_snapshot_for(connection)
        schema_before = self.schema_snapshot_for(connection)
        schema_version_before = connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        captured: list[sqlite3.Error] = []

        def failing_create(*args: object) -> int:
            try:
                return create_usage_history(*args)  # type: ignore[arg-type]
            except sqlite3.Error as error:
                captured.append(error)
                raise

        outputs: list[str] = []
        try:
            with patch.object(
                cli_module,
                "create_usage_history",
                side_effect=failing_create,
            ) as created:
                with self.assertRaises(sqlite3.IntegrityError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            self.usage_history_create_actions(
                                literature_id,
                                "forced",
                            )[:-2]
                        ),
                        output_func=outputs.append,
                    )

            created.assert_called_once_with(
                connection,
                literature_id,
                "forced",
                None,
                None,
                None,
            )
            self.assertEqual(len(captured), 1)
            self.assertIs(raised.exception, captured[0])
            self.assertEqual(self.table_snapshot_for(connection), before)
            self.assertEqual(self.schema_snapshot_for(connection), schema_before)
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(outputs.count(cli_module._DATABASE_ERROR_MESSAGE), 1)
            self.assertNotIn("使用履歴を登録しました。", outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_usage_history_success_output_failure_keeps_one_committed_row(
        self,
    ) -> None:
        tracking_path = self.directory / "usage-output-failure.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(
            tracking_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        literature_id = add_literature(
            connection,
            Literature(title="Usage output target"),
        )
        other_id = add_literature(
            connection,
            Literature(title="Usage output other"),
        )
        create_usage_history(connection, literature_id, "kept target")
        create_usage_history(connection, other_id, "kept other")
        tag_id = create_tag(connection, "usage-output-tag")
        attach_tag_to_literature(connection, literature_id, tag_id)
        connection.execute("PRAGMA user_version = 110")
        before = self.table_snapshot_for(connection)
        schema_before = self.schema_snapshot_for(connection)
        schema_version_before = connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        expected = RuntimeError("usage success output failure")
        outputs: list[str] = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == "使用履歴を登録しました。":
                raise expected

        try:
            with patch.object(
                cli_module,
                "create_usage_history",
                wraps=create_usage_history,
            ) as created:
                with self.assertRaises(RuntimeError) as raised:
                    run_cli(
                        connection,
                        input_func=InputFeeder(
                            self.usage_history_create_actions(
                                literature_id,
                                "conference",
                                "project",
                                "slide 5",
                                "2026-08-17",
                            )[:-2]
                        ),
                        output_func=output_func,
                    )

            self.assertIs(raised.exception, expected)
            created.assert_called_once_with(
                connection,
                literature_id,
                "conference",
                "project",
                "slide 5",
                "2026-08-17",
            )
            after = self.table_snapshot_for(connection)
            self.assertEqual(after["literature"], before["literature"])
            self.assertEqual(after["tags"], before["tags"])
            self.assertEqual(after["literature_tags"], before["literature_tags"])
            self.assertEqual(after["usage_history"][:-1], before["usage_history"])
            self.assertEqual(
                len(after["usage_history"]),
                len(before["usage_history"]) + 1,
            )
            self.assertEqual(
                after["usage_history"][-1][1:6],
                (literature_id, "conference", "project", "slide 5", "2026-08-17"),
            )
            self.assertEqual(self.schema_snapshot_for(connection), schema_before)
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertFalse(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(outputs.count("使用履歴を登録しました。"), 1)
            self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_usage_history_input_exception_matrix(self) -> None:
        literature_id = self.add_record("Usage input exceptions")
        prefixes: tuple[tuple[str, list[object]], ...] = (
            ("submenu", ["7"]),
            ("list literature ID", ["7", "1"]),
            ("create literature ID", ["7", "2"]),
            ("usage type", ["7", "2", str(literature_id)]),
            ("project name", ["7", "2", str(literature_id), "note"]),
            (
                "usage note",
                ["7", "2", str(literature_id), "note", "project"],
            ),
            (
                "used at",
                ["7", "2", str(literature_id), "note", "project", "note"],
            ),
            (
                "confirmation",
                [
                    "7",
                    "2",
                    str(literature_id),
                    "note",
                    "project",
                    "note",
                    "2026-08-17",
                ],
            ),
        )
        exception_factories = (
            ("EOFError", lambda stage: EOFError(stage)),
            ("KeyboardInterrupt", lambda stage: KeyboardInterrupt()),
            ("RuntimeError", lambda stage: RuntimeError(stage)),
            ("sqlite3.Error", lambda stage: sqlite3.OperationalError(stage)),
        )
        for stage, prefix in prefixes:
            for exception_name, exception_factory in exception_factories:
                with self.subTest(stage=stage, exception=exception_name):
                    expected = exception_factory(stage)
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    feeder = InputFeeder([*prefix, expected])
                    if isinstance(expected, (EOFError, KeyboardInterrupt)):
                        result = run_cli(
                            self.connection,
                            input_func=feeder,
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=feeder,
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                        self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                    if isinstance(expected, sqlite3.Error):
                        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
                    self.assertEqual(self.table_snapshot(), before)

    def test_usage_history_output_exceptions_propagate_without_writes(self) -> None:
        literature_id = self.add_record("Usage output exceptions")
        history_id = create_usage_history(self.connection, literature_id, "listed")
        cases = (
            (
                "submenu",
                ["7"],
                cli_module._USAGE_HISTORY_MANAGEMENT_MENU,
            ),
            (
                "list record",
                ["7", "1", str(literature_id)],
                f"id: {history_id}\nliterature_id: {literature_id}",
            ),
            (
                "create confirmation",
                [
                    "7",
                    "2",
                    str(literature_id),
                    "note",
                    "project",
                    "note",
                    "",
                ],
                cli_module._USAGE_HISTORY_CREATE_CONFIRMATION_MENU,
            ),
        )
        for case, actions, failing_prefix in cases:
            for expected in (
                RuntimeError(f"{case} output failure"),
                sqlite3.OperationalError(f"{case} output sqlite failure"),
            ):
                with self.subTest(case=case, exception=type(expected).__name__):
                    before = self.table_snapshot()
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message.startswith(failing_prefix):
                            raise expected

                    with patch.object(
                        cli_module,
                        "create_usage_history",
                    ) as created:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    created.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_usage_history_edit_each_field_preserves_all_other_data(self) -> None:
        cases = (
            (1, "usage_type", "  学会発表  ", "学会発表", 2),
            (2, "project_name", "  Updated project  ", "  Updated project  ", 3),
            (3, "usage_note", "\tUpdated note \n", "\tUpdated note \n", 4),
            (4, "used_at", "2026-08-17", "2026-08-17", 5),
        )
        self.connection.execute("PRAGMA user_version = 201")

        for field_number, field_name, raw_value, stored_value, column_index in cases:
            with self.subTest(field=field_name):
                literature_id = self.add_record(f"Usage edit target {field_name}")
                other_literature_id = self.add_record(
                    f"Usage edit other {field_name}"
                )
                tag_id = create_tag(self.connection, f"usage-edit-{field_name}")
                attach_tag_to_literature(
                    self.connection,
                    literature_id,
                    tag_id,
                )
                history_id = create_usage_history(
                    self.connection,
                    literature_id,
                    "note",
                    "Original project",
                    "Original note",
                    "2026-08-01",
                )
                create_usage_history(
                    self.connection,
                    literature_id,
                    "same-literature history",
                )
                create_usage_history(
                    self.connection,
                    other_literature_id,
                    "other-literature history",
                )
                before = self.table_snapshot()
                schema_before = self.schema_snapshot()
                schema_version_before = self.connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()[0]
                user_version_before = self.connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]

                with patch.object(
                    cli_module,
                    "update_usage_history",
                    wraps=update_usage_history,
                ) as updated:
                    _, _, outputs = self.run_with_actions(
                        self.usage_history_edit_actions(
                            f"  000{history_id}\t",
                            field_number,
                            raw_value,
                        )
                    )

                updated.assert_called_once_with(
                    self.connection,
                    history_id,
                    {field_name: raw_value},
                )
                after = self.table_snapshot()
                self.assertEqual(after["literature"], before["literature"])
                self.assertEqual(after["tags"], before["tags"])
                self.assertEqual(
                    after["literature_tags"],
                    before["literature_tags"],
                )
                expected_history = list(before["usage_history"])
                target_index = next(
                    index
                    for index, row in enumerate(expected_history)
                    if row[0] == history_id
                )
                expected_row = list(expected_history[target_index])
                expected_row[column_index] = stored_value
                expected_history[target_index] = tuple(expected_row)
                self.assertEqual(after["usage_history"], expected_history)
                stored = get_usage_history(self.connection, history_id)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.literature_id, literature_id)
                self.assertEqual(stored.created_at, before["usage_history"][target_index][6])
                self.assertEqual(self.schema_snapshot(), schema_before)
                self.assertEqual(
                    self.connection.execute("PRAGMA schema_version").fetchone()[0],
                    schema_version_before,
                )
                self.assertEqual(
                    self.connection.execute("PRAGMA user_version").fetchone()[0],
                    user_version_before,
                )
                displayed = "\n".join(outputs)
                for label in (
                    "id",
                    "literature_id",
                    "usage_type",
                    "project_name",
                    "usage_note",
                    "used_at",
                    "created_at",
                ):
                    self.assertIn(f"{label}:", displayed)
                self.assertIn(f"使用履歴ID: {history_id}", outputs)
                self.assertIn(f"編集項目: {field_name}", outputs)
                self.assertIn("使用履歴を更新しました。", outputs)
                self.assertIn(f"更新項目: {field_name}", outputs)
                self.assertFalse(self.connection.in_transaction)

        for forbidden in ("id", "literature_id", "created_at"):
            self.assertNotIn(
                forbidden,
                cli_module._USAGE_HISTORY_EDIT_FIELD_MENU,
            )

    def test_usage_history_edit_optional_blank_validation_and_cancel(self) -> None:
        literature_id = self.add_record("Usage edit validation")
        history_id = create_usage_history(
            self.connection,
            literature_id,
            "note",
            "Project",
            "Note",
            "2026-08-01",
        )

        for field_number, field_name in (
            (2, "project_name"),
            (3, "usage_note"),
            (4, "used_at"),
        ):
            with self.subTest(blank_optional=field_name):
                with patch.object(
                    cli_module,
                    "update_usage_history",
                    wraps=update_usage_history,
                ) as updated:
                    _, _, outputs = self.run_with_actions(
                        self.usage_history_edit_actions(
                            history_id,
                            field_number,
                            " \t\n ",
                        )
                    )

                updated.assert_called_once_with(
                    self.connection,
                    history_id,
                    {field_name: None},
                )
                stored = get_usage_history(self.connection, history_id)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertIsNone(getattr(stored, field_name))
                self.assertIn("変更後: 未登録", outputs)

        before = self.table_snapshot()
        with patch.object(cli_module, "update_usage_history") as updated:
            _, _, outputs = self.run_with_actions(
                self.usage_history_edit_actions(history_id, 1, " \t ")
            )
        updated.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertIn("入力エラー: usage_typeは必須です。", outputs)

        raw_invalid_date = "2025-02-29"
        with patch.object(
            cli_module,
            "update_usage_history",
            wraps=update_usage_history,
        ) as updated:
            _, _, outputs = self.run_with_actions(
                self.usage_history_edit_actions(
                    history_id,
                    4,
                    raw_invalid_date,
                )
            )
        updated.assert_called_once_with(
            self.connection,
            history_id,
            {"used_at": raw_invalid_date},
        )
        self.assertTrue(
            any(item.startswith("使用履歴編集エラー: ") for item in outputs)
        )
        self.assertEqual(self.table_snapshot(), before)

        cancel_cases = (
            (
                "field selection",
                [
                    "7",
                    "3",
                    str(history_id),
                    "5",
                    "invalid",
                    "0",
                    "0",
                    "0",
                ],
                cli_module._INVALID_USAGE_HISTORY_EDIT_FIELD_MESSAGE,
            ),
            (
                "confirmation",
                [
                    "7",
                    "3",
                    str(history_id),
                    "2",
                    "new project",
                    "2",
                    "invalid",
                    "0",
                    "0",
                    "0",
                ],
                cli_module._INVALID_CONFIRMATION_MESSAGE,
            ),
        )
        for case, actions, expected_message in cancel_cases:
            with self.subTest(cancel=case):
                before_cancel = self.table_snapshot()
                with patch.object(cli_module, "update_usage_history") as updated:
                    _, _, outputs = self.run_with_actions(actions)
                updated.assert_not_called()
                self.assertEqual(self.table_snapshot(), before_cancel)
                self.assertIn(expected_message, outputs)
                self.assertTrue(
                    any("使用履歴" in item and "中止しました。" in item for item in outputs)
                )

    def test_usage_history_edit_delete_id_validation_missing_and_races(self) -> None:
        invalid_values = (
            "",
            "0",
            "+1",
            "-1",
            "1.5",
            "1e3",
            "１",
            "١",
            "history",
        )
        for operation in ("3", "4"):
            for invalid_value in invalid_values:
                with self.subTest(operation=operation, value=invalid_value):
                    before = self.table_snapshot()
                    with (
                        patch.object(cli_module, "get_usage_history") as gotten,
                        patch.object(cli_module, "update_usage_history") as updated,
                        patch.object(cli_module, "delete_usage_history") as deleted,
                    ):
                        _, _, outputs = self.run_with_actions(
                            ["7", operation, invalid_value, "0", "0"]
                        )
                    gotten.assert_not_called()
                    updated.assert_not_called()
                    deleted.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertTrue(
                        any(
                            item.startswith("入力エラー: ") and "ASCII" in item
                            for item in outputs
                        )
                    )

        for operation in ("3", "4"):
            with self.subTest(operation=operation, target="missing"):
                with (
                    patch.object(
                        cli_module,
                        "get_usage_history",
                        return_value=None,
                    ) as gotten,
                    patch.object(cli_module, "update_usage_history") as updated,
                    patch.object(cli_module, "delete_usage_history") as deleted,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["7", operation, "999999", "0", "0"]
                    )
                gotten.assert_called_once_with(self.connection, 999999)
                updated.assert_not_called()
                deleted.assert_not_called()
                self.assertIn("対象の使用履歴が見つかりません。", outputs)

        literature_id = self.add_record("Usage race target")
        history_id = create_usage_history(self.connection, literature_id, "note")
        for operation, actions, api_name in (
            (
                "edit",
                self.usage_history_edit_actions(history_id, 2, "new"),
                "update_usage_history",
            ),
            (
                "delete",
                self.usage_history_delete_actions(history_id),
                "delete_usage_history",
            ),
        ):
            with self.subTest(race=operation):
                before = self.table_snapshot()
                with patch.object(cli_module, api_name, return_value=False) as api:
                    _, _, outputs = self.run_with_actions(actions)
                api.assert_called_once()
                self.assertEqual(self.table_snapshot(), before)
                self.assertIn(
                    "確認後に対象の使用履歴が存在しなくなりました。",
                    outputs,
                )
                self.assertNotIn("使用履歴を更新しました。", outputs)
                self.assertNotIn("使用履歴を削除しました。", outputs)

    def test_usage_history_delete_two_step_confirmation_and_isolation(self) -> None:
        literature_id = self.add_record("Usage delete target literature")
        other_literature_id = self.add_record("Usage delete other literature")
        target_id = create_usage_history(
            self.connection,
            literature_id,
            "target",
            "Target project",
            "Target note",
            "2026-08-17",
        )
        same_literature_id = create_usage_history(
            self.connection,
            literature_id,
            "same literature",
        )
        other_history_id = create_usage_history(
            self.connection,
            other_literature_id,
            "other literature",
        )
        tag_id = create_tag(self.connection, "usage-delete-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        self.connection.execute("PRAGMA user_version = 202")
        before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        actions = [
            "7",
            "4",
            f"  000{target_id} ",
            "2",
            "invalid",
            "1",
            str(same_literature_id),
            "+1",
            f"000{target_id}",
            "0",
            "0",
        ]

        with patch.object(
            cli_module,
            "delete_usage_history",
            wraps=delete_usage_history,
        ) as deleted:
            _, feeder, outputs = self.run_with_actions(actions)

        deleted.assert_called_once_with(self.connection, target_id)
        after = self.table_snapshot()
        self.assertEqual(after["literature"], before["literature"])
        self.assertEqual(after["tags"], before["tags"])
        self.assertEqual(after["literature_tags"], before["literature_tags"])
        self.assertEqual(
            after["usage_history"],
            [row for row in before["usage_history"] if row[0] != target_id],
        )
        self.assertIsNone(get_usage_history(self.connection, target_id))
        self.assertIsNotNone(
            get_usage_history(self.connection, same_literature_id)
        )
        self.assertIsNotNone(get_usage_history(self.connection, other_history_id))
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)
        displayed = "\n".join(outputs)
        for label in (
            "id",
            "literature_id",
            "usage_type",
            "project_name",
            "usage_note",
            "used_at",
            "created_at",
        ):
            self.assertIn(f"{label}:", displayed)
        for warning in (
            "警告: この使用履歴レコード自体が削除されます。",
            "対象文献は削除されません。",
            "タグは削除されません。",
            "文献とタグの関連付けは変更されません。",
            "他の使用履歴は削除されません。",
            "CLIには自動復元機能がありません。",
        ):
            self.assertIn(warning, outputs)
        self.assertGreaterEqual(
            outputs.count(cli_module._INVALID_CONFIRMATION_MESSAGE),
            2,
        )
        final_prompt = next(
            prompt
            for prompt in feeder.prompts
            if prompt.startswith("削除を確定するため使用履歴ID")
        )
        self.assertIn(f"使用履歴ID {target_id}", final_prompt)
        self.assertIn("（0で中止）", final_prompt)
        self.assertIn("使用履歴を削除しました。", outputs)
        self.assertIn(f"使用履歴ID: {target_id}", outputs)

    def test_usage_history_delete_cancellation_never_calls_api(self) -> None:
        literature_id = self.add_record("Usage delete cancellation")
        history_id = create_usage_history(self.connection, literature_id, "note")
        cases = (
            (
                "first confirmation",
                ["7", "4", str(history_id), "2", "0", "0", "0"],
            ),
            (
                "second confirmation",
                ["7", "4", str(history_id), "1", "0", "0", "0"],
            ),
        )
        for case, actions in cases:
            with self.subTest(case=case):
                before = self.table_snapshot()
                with patch.object(cli_module, "delete_usage_history") as deleted:
                    _, _, outputs = self.run_with_actions(actions)
                deleted.assert_not_called()
                self.assertEqual(self.table_snapshot(), before)
                self.assertIn("使用履歴削除を中止しました。", outputs)
                self.assertNotIn("使用履歴を削除しました。", outputs)

    def test_usage_history_edit_delete_reject_initial_and_late_transactions(
        self,
    ) -> None:
        for operation, menu_choice, api_name, active_message in (
            (
                "edit",
                "3",
                "update_usage_history",
                cli_module._USAGE_HISTORY_EDIT_ACTIVE_TRANSACTION_MESSAGE,
            ),
            (
                "delete",
                "4",
                "delete_usage_history",
                cli_module._USAGE_HISTORY_DELETE_ACTIVE_TRANSACTION_MESSAGE,
            ),
        ):
            with self.subTest(operation=operation, timing="initial"):
                connection, _, history_id, _, _, _ = (
                    self.create_tracking_usage_history_fixture(
                        f"{operation}-initial"
                    )
                )
                marker = connection.execute(
                    "INSERT INTO tags (name) VALUES (?)",
                    (f"pending-usage-{operation}-initial",),
                )
                connection.commit_calls = 0
                connection.rollback_calls = 0
                connection.close_calls = 0
                try:
                    with patch.object(cli_module, api_name) as write_api:
                        _, _, outputs = self.run_with_actions(
                            ["7", menu_choice, "0", "0"],
                            connection=connection,
                        )

                    write_api.assert_not_called()
                    self.assertIn(active_message, outputs)
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM tags WHERE id = ?",
                            (marker.lastrowid,),
                        ).fetchone()[0],
                        1,
                    )
                    self.assertIsNotNone(get_usage_history(connection, history_id))
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

            with self.subTest(operation=operation, timing="late"):
                connection, _, history_id, _, _, _ = (
                    self.create_tracking_usage_history_fixture(
                        f"{operation}-late"
                    )
                )
                actions = (
                    self.usage_history_edit_actions(history_id, 2, "after")
                    if operation == "edit"
                    else self.usage_history_delete_actions(history_id)
                )
                mutation_prompt_number = 6 if operation == "edit" else 5
                feeder = InputFeeder(actions)
                marker_ids: list[int] = []

                def input_func(prompt: str) -> str:
                    value = feeder(prompt)
                    if len(feeder.prompts) == mutation_prompt_number:
                        marker = connection.execute(
                            "INSERT INTO tags (name) VALUES (?)",
                            (f"pending-usage-{operation}-late",),
                        )
                        assert marker.lastrowid is not None
                        marker_ids.append(marker.lastrowid)
                    return value

                connection.commit_calls = 0
                connection.rollback_calls = 0
                connection.close_calls = 0
                outputs: list[str] = []
                try:
                    with patch.object(cli_module, api_name) as write_api:
                        result = run_cli(
                            connection,
                            input_func=input_func,
                            output_func=outputs.append,
                        )

                    self.assertIsNone(result)
                    write_api.assert_not_called()
                    self.assertEqual(len(marker_ids), 1)
                    self.assertIn(active_message, outputs)
                    self.assertTrue(connection.in_transaction)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM tags WHERE id = ?",
                            (marker_ids[0],),
                        ).fetchone()[0],
                        1,
                    )
                    stored = get_usage_history(connection, history_id)
                    self.assertIsNotNone(stored)
                    assert stored is not None
                    self.assertEqual(stored.project_name, "Before project")
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_usage_history_edit_delete_repository_exception_boundaries(
        self,
    ) -> None:
        literature_id = self.add_record("Usage edit/delete API errors")
        history_id = create_usage_history(self.connection, literature_id, "note")

        for operation in ("edit", "delete"):
            menu_choice = "3" if operation == "edit" else "4"
            for expected in (
                sqlite3.OperationalError(f"{operation} get sqlite"),
                RuntimeError(f"{operation} get runtime"),
            ):
                with self.subTest(
                    operation=operation,
                    api="get",
                    exception=type(expected).__name__,
                ):
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    with patch.object(
                        cli_module,
                        "get_usage_history",
                        side_effect=expected,
                    ) as gotten:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(
                                    ["7", menu_choice, str(history_id)]
                                ),
                                output_func=outputs.append,
                            )
                    self.assertIs(raised.exception, expected)
                    gotten.assert_called_once_with(self.connection, history_id)
                    self.assertEqual(self.table_snapshot(), before)
                    if isinstance(expected, sqlite3.Error):
                        self.assertEqual(
                            outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                            1,
                        )
                    else:
                        self.assertNotIn(
                            cli_module._DATABASE_ERROR_MESSAGE,
                            outputs,
                        )

        for expected in (
            ValueError("edit value"),
            sqlite3.OperationalError("edit sqlite"),
            RuntimeError("edit runtime"),
            EOFError("edit API EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(api="update", exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                actions: list[object] = self.usage_history_edit_actions(
                    history_id,
                    2,
                    "updated",
                )
                if not isinstance(expected, ValueError):
                    actions = actions[:-2]
                with patch.object(
                    cli_module,
                    "update_usage_history",
                    side_effect=expected,
                ) as updated:
                    if isinstance(expected, ValueError):
                        result = run_cli(
                            self.connection,
                            input_func=InputFeeder(actions),
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertTrue(
                            any(
                                item.startswith("使用履歴編集エラー: ")
                                for item in outputs
                            )
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                updated.assert_called_once_with(
                    self.connection,
                    history_id,
                    {"project_name": "updated"},
                )
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                elif not isinstance(expected, ValueError):
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

        for expected in (
            sqlite3.OperationalError("delete sqlite"),
            RuntimeError("delete runtime"),
            EOFError("delete API EOF"),
            KeyboardInterrupt(),
        ):
            with self.subTest(api="delete", exception=type(expected).__name__):
                before = self.table_snapshot()
                outputs: list[str] = []
                with patch.object(
                    cli_module,
                    "delete_usage_history",
                    side_effect=expected,
                ) as deleted:
                    with self.assertRaises(type(expected)) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(
                                self.usage_history_delete_actions(history_id)[:-2]
                            ),
                            output_func=outputs.append,
                        )
                self.assertIs(raised.exception, expected)
                deleted.assert_called_once_with(self.connection, history_id)
                self.assertEqual(self.table_snapshot(), before)
                if isinstance(expected, sqlite3.Error):
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                else:
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_usage_history_edit_delete_input_exception_matrix(self) -> None:
        literature_id = self.add_record("Usage edit/delete input exceptions")
        history_id = create_usage_history(self.connection, literature_id, "note")
        prefixes: tuple[tuple[str, list[object]], ...] = (
            ("edit ID", ["7", "3"]),
            ("edit field", ["7", "3", str(history_id)]),
            ("edit value", ["7", "3", str(history_id), "1"]),
            (
                "edit confirmation",
                ["7", "3", str(history_id), "1", "new usage"],
            ),
            ("delete ID", ["7", "4"]),
            ("delete confirmation", ["7", "4", str(history_id)]),
            (
                "delete repeated ID",
                ["7", "4", str(history_id), "1"],
            ),
        )
        exception_factories = (
            ("EOFError", lambda stage: EOFError(stage)),
            ("KeyboardInterrupt", lambda stage: KeyboardInterrupt()),
            ("RuntimeError", lambda stage: RuntimeError(stage)),
            (
                "sqlite3.Error",
                lambda stage: sqlite3.OperationalError(stage),
            ),
        )
        for stage, prefix in prefixes:
            for exception_name, exception_factory in exception_factories:
                with self.subTest(stage=stage, exception=exception_name):
                    expected = exception_factory(stage)
                    before = self.table_snapshot()
                    outputs: list[str] = []
                    feeder = InputFeeder([*prefix, expected])
                    if isinstance(expected, (EOFError, KeyboardInterrupt)):
                        result = run_cli(
                            self.connection,
                            input_func=feeder,
                            output_func=outputs.append,
                        )
                        self.assertIsNone(result)
                        self.assertEqual(
                            outputs.count(cli_module._EXIT_MESSAGE),
                            1,
                        )
                    else:
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=feeder,
                                output_func=outputs.append,
                            )
                        self.assertIs(raised.exception, expected)
                        self.assertNotIn(cli_module._EXIT_MESSAGE, outputs)
                    if isinstance(expected, sqlite3.Error):
                        self.assertNotIn(
                            cli_module._DATABASE_ERROR_MESSAGE,
                            outputs,
                        )
                    self.assertEqual(self.table_snapshot(), before)

    def test_real_sqlite_usage_history_edit_delete_failures_are_atomic(
        self,
    ) -> None:
        for operation in ("edit", "delete"):
            with self.subTest(operation=operation):
                connection, _, target_id, _, _, _ = (
                    self.create_tracking_usage_history_fixture(
                        f"{operation}-failure"
                    )
                )
                connection.execute(
                    f"PRAGMA user_version = {211 if operation == 'edit' else 212}"
                )
                sql_operation = "UPDATE" if operation == "edit" else "DELETE"
                connection.execute(
                    f"""
                    CREATE TRIGGER force_cli_usage_{operation}_failure
                    BEFORE {sql_operation} ON usage_history
                    BEGIN
                        SELECT RAISE(ABORT, 'forced CLI usage {operation} failure');
                    END
                    """
                )
                connection.commit()
                before = self.table_snapshot_for(connection)
                schema_before = self.schema_snapshot_for(connection)
                schema_version_before = connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()[0]
                user_version_before = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                connection.commit_calls = 0
                connection.rollback_calls = 0
                connection.close_calls = 0
                captured: list[sqlite3.Error] = []

                def failing_write(*args: object) -> bool:
                    try:
                        if operation == "edit":
                            return update_usage_history(  # type: ignore[arg-type]
                                *args
                            )
                        return delete_usage_history(*args)  # type: ignore[arg-type]
                    except sqlite3.Error as error:
                        captured.append(error)
                        raise

                api_name = (
                    "update_usage_history"
                    if operation == "edit"
                    else "delete_usage_history"
                )
                actions = (
                    self.usage_history_edit_actions(
                        target_id,
                        2,
                        "Must roll back",
                    )[:-2]
                    if operation == "edit"
                    else self.usage_history_delete_actions(target_id)[:-2]
                )
                outputs: list[str] = []
                try:
                    with patch.object(
                        cli_module,
                        api_name,
                        side_effect=failing_write,
                    ) as write_api:
                        with self.assertRaises(sqlite3.IntegrityError) as raised:
                            run_cli(
                                connection,
                                input_func=InputFeeder(actions),
                                output_func=outputs.append,
                            )

                    write_api.assert_called_once()
                    if operation == "edit":
                        write_api.assert_called_once_with(
                            connection,
                            target_id,
                            {"project_name": "Must roll back"},
                        )
                    else:
                        write_api.assert_called_once_with(connection, target_id)
                    self.assertEqual(len(captured), 1)
                    self.assertIs(raised.exception, captured[0])
                    self.assertEqual(
                        self.table_snapshot_for(connection),
                        before,
                    )
                    self.assertEqual(
                        self.schema_snapshot_for(connection),
                        schema_before,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA schema_version").fetchone()[0],
                        schema_version_before,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        user_version_before,
                    )
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
                    self.assertIsNotNone(get_usage_history(connection, target_id))
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertEqual(
                        outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                        1,
                    )
                    self.assertNotIn("使用履歴を更新しました。", outputs)
                    self.assertNotIn("使用履歴を削除しました。", outputs)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_usage_history_edit_delete_success_output_failure_keeps_commit(
        self,
    ) -> None:
        for operation in ("edit", "delete"):
            with self.subTest(operation=operation):
                connection, _, target_id, _, _, _ = (
                    self.create_tracking_usage_history_fixture(
                        f"{operation}-output"
                    )
                )
                connection.execute(
                    f"PRAGMA user_version = {221 if operation == 'edit' else 222}"
                )
                before = self.table_snapshot_for(connection)
                schema_before = self.schema_snapshot_for(connection)
                schema_version_before = connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()[0]
                user_version_before = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                connection.commit_calls = 0
                connection.rollback_calls = 0
                connection.close_calls = 0
                success_message = (
                    "使用履歴を更新しました。"
                    if operation == "edit"
                    else "使用履歴を削除しました。"
                )
                expected = RuntimeError(f"usage {operation} success output failure")
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if message == success_message:
                        raise expected

                api_name = (
                    "update_usage_history"
                    if operation == "edit"
                    else "delete_usage_history"
                )
                wrapped_api = (
                    update_usage_history
                    if operation == "edit"
                    else delete_usage_history
                )
                actions = (
                    self.usage_history_edit_actions(
                        target_id,
                        2,
                        "After project",
                    )[:-2]
                    if operation == "edit"
                    else self.usage_history_delete_actions(target_id)[:-2]
                )
                try:
                    with patch.object(
                        cli_module,
                        api_name,
                        wraps=wrapped_api,
                    ) as write_api:
                        with self.assertRaises(RuntimeError) as raised:
                            run_cli(
                                connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    write_api.assert_called_once()
                    after = self.table_snapshot_for(connection)
                    self.assertEqual(after["literature"], before["literature"])
                    self.assertEqual(after["tags"], before["tags"])
                    self.assertEqual(
                        after["literature_tags"],
                        before["literature_tags"],
                    )
                    if operation == "edit":
                        write_api.assert_called_once_with(
                            connection,
                            target_id,
                            {"project_name": "After project"},
                        )
                        expected_history = list(before["usage_history"])
                        target_index = next(
                            index
                            for index, row in enumerate(expected_history)
                            if row[0] == target_id
                        )
                        expected_row = list(expected_history[target_index])
                        expected_row[3] = "After project"
                        expected_history[target_index] = tuple(expected_row)
                        self.assertEqual(
                            after["usage_history"],
                            expected_history,
                        )
                    else:
                        write_api.assert_called_once_with(connection, target_id)
                        self.assertEqual(
                            after["usage_history"],
                            [
                                row
                                for row in before["usage_history"]
                                if row[0] != target_id
                            ],
                        )
                    self.assertEqual(
                        self.schema_snapshot_for(connection),
                        schema_before,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA schema_version").fetchone()[0],
                        schema_version_before,
                    )
                    self.assertEqual(
                        connection.execute("PRAGMA user_version").fetchone()[0],
                        user_version_before,
                    )
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
                    self.assertEqual(connection.commit_calls, 0)
                    self.assertEqual(connection.rollback_calls, 0)
                    self.assertEqual(connection.close_calls, 0)
                    self.assertEqual(outputs.count(success_message), 1)
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
                finally:
                    if connection.in_transaction:
                        sqlite3.Connection.rollback(connection)
                    sqlite3.Connection.close(connection)

    def test_usage_history_edit_delete_output_exceptions_before_write(
        self,
    ) -> None:
        literature_id = self.add_record("Usage edit/delete output errors")
        history_id = create_usage_history(
            self.connection,
            literature_id,
            "note",
            "project",
            "note",
            "2026-08-17",
        )
        formatted_history = cli_module._format_usage_history(
            get_usage_history(self.connection, history_id)
        )
        cases = (
            (
                "edit record",
                ["7", "3", str(history_id)],
                formatted_history,
            ),
            (
                "edit confirmation",
                ["7", "3", str(history_id), "2", "new project"],
                cli_module._USAGE_HISTORY_EDIT_CONFIRMATION_MENU,
            ),
            (
                "delete record",
                ["7", "4", str(history_id)],
                formatted_history,
            ),
            (
                "delete warning",
                ["7", "4", str(history_id)],
                "削除対象と影響を確認してください。",
            ),
            (
                "delete confirmation",
                ["7", "4", str(history_id)],
                cli_module._DELETE_CONFIRMATION_MENU,
            ),
        )
        for case, actions, failing_message in cases:
            for expected in (
                RuntimeError(f"{case} output failure"),
                sqlite3.OperationalError(f"{case} output sqlite failure"),
                EOFError(f"{case} output EOF"),
                KeyboardInterrupt(),
            ):
                with self.subTest(case=case, exception=type(expected).__name__):
                    before = self.table_snapshot()
                    outputs: list[str] = []

                    def output_func(message: str) -> None:
                        outputs.append(message)
                        if message == failing_message:
                            raise expected

                    with (
                        patch.object(cli_module, "update_usage_history") as updated,
                        patch.object(cli_module, "delete_usage_history") as deleted,
                    ):
                        with self.assertRaises(type(expected)) as raised:
                            run_cli(
                                self.connection,
                                input_func=InputFeeder(actions),
                                output_func=output_func,
                            )

                    self.assertIs(raised.exception, expected)
                    updated.assert_not_called()
                    deleted.assert_not_called()
                    self.assertEqual(self.table_snapshot(), before)
                    self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

    def test_csv_submenu_contract_default_path_and_unset_search(self) -> None:
        feeder = InputFeeder(["9", "3", "2", "1", "0", "14", "0"])
        outputs: list[str] = []

        with patch.object(
            cli_module,
            "export_literature_csv",
            return_value=2,
        ) as exported:
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        self.assertIn("1. 全文献を出力", cli_module._CSV_EXPORT_MENU)
        self.assertIn(
            "2. 直前の検索結果を出力",
            cli_module._CSV_EXPORT_MENU,
        )
        self.assertIn(
            "0. メインメニューに戻る",
            cli_module._CSV_EXPORT_MENU,
        )
        self.assertNotIn("3. ", cli_module._CSV_EXPORT_MENU)
        self.assertEqual(
            outputs.count(cli_module._INVALID_CSV_EXPORT_MENU_MESSAGE),
            1,
        )
        self.assertEqual(
            outputs.count(cli_module._MISSING_SEARCH_RESULTS_MESSAGE),
            1,
        )
        exported.assert_called_once_with(
            self.connection,
            Path("exports") / "literature_all.csv",
            literature_ids=None,
        )
        self.assertIn("CSVを出力しました。", outputs)
        self.assertIn("件数: 2", outputs)
        self.assertIn("出力先: exports/literature_all.csv", outputs)
        self.assertEqual(outputs.count(cli_module._INVALID_MENU_MESSAGE), 1)
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

    def test_csv_export_all_integration_content_and_messages(self) -> None:
        first_id = self.add_record("全文献CSV一件目")
        second_id = self.add_record("全文献CSV二件目")
        first_tag_id = create_tag(self.connection, "AHD")
        second_tag_id = create_tag(self.connection, "ultrasound")
        attach_tag_to_literature(self.connection, first_id, first_tag_id)
        attach_tag_to_literature(self.connection, first_id, second_tag_id)
        create_usage_history(
            self.connection,
            first_id,
            "csv-usage-marker",
        )
        output_path = self.directory / "literature_all.csv"

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            _, _, outputs = self.run_with_actions(["9", "1", "0", "0"])

        exported.assert_called_once_with(
            self.connection,
            output_path,
            literature_ids=None,
        )
        self.assertTrue(output_path.read_bytes().startswith(codecs.BOM_UTF8))
        with output_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
            fieldnames = reader.fieldnames
        self.assertEqual([int(row["id"]) for row in rows], [first_id, second_id])
        self.assertEqual(rows[0]["tags"], "AHD;ultrasound")
        self.assertEqual(rows[1]["tags"], "")
        self.assertIsNotNone(fieldnames)
        assert fieldnames is not None
        self.assertIn("tags", fieldnames)
        for excluded_column in (
            "usage_type",
            "project_name",
            "usage_note",
            "used_at",
        ):
            self.assertNotIn(excluded_column, fieldnames)
        self.assertNotIn("csv-usage-marker", output_path.read_text("utf-8-sig"))
        self.assertIn("CSVを出力しました。", outputs)
        self.assertIn("件数: 2", outputs)
        self.assertIn(f"出力先: {output_path}", outputs)

    def test_csv_export_uses_only_last_search_result_ids(self) -> None:
        first_matching_id = self.add_record("CSV検索対象 alpha")
        nonmatching_id = self.add_record("無関係な別文献")
        second_matching_id = self.add_record("CSV検索対象 beta")
        tag_id = create_tag(self.connection, "search-export-tag")
        attach_tag_to_literature(
            self.connection,
            second_matching_id,
            tag_id,
        )
        actions = [
            *self.search_actions(keyword="CSV検索対象 ")[:-1],
            "9",
            "2",
            "0",
            "0",
        ]
        output_path = self.directory / "literature_search_results.csv"

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            _, _, outputs = self.run_with_actions(actions)

        exported.assert_called_once_with(
            self.connection,
            output_path,
            literature_ids=(first_matching_id, second_matching_id),
        )
        with output_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        csv_ids = [int(row["id"]) for row in rows]
        self.assertEqual(csv_ids, [first_matching_id, second_matching_id])
        self.assertNotIn(nonmatching_id, csv_ids)
        self.assertEqual(rows[1]["tags"], "search-export-tag")
        self.assertIn("件数: 2", outputs)

    def test_zero_result_search_replaces_previous_result_and_exports_header(
        self,
    ) -> None:
        self.add_record("置換前検索対象")
        actions = [
            *self.search_actions(keyword="置換前検索対象")[:-1],
            *self.search_actions(keyword="絶対に一致しない検索語")[:-1],
            "9",
            "2",
            "0",
            "0",
        ]
        output_path = self.directory / "literature_search_results.csv"

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            _, _, outputs = self.run_with_actions(actions)

        exported.assert_called_once_with(
            self.connection,
            output_path,
            literature_ids=(),
        )
        with output_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        self.assertEqual(len(rows), 1)
        self.assertIn("id", rows[0])
        self.assertIn("tags", rows[0])
        self.assertIn("件数: 0", outputs)

    def test_invalid_search_preserves_previous_successful_result(self) -> None:
        matching_id = self.add_record("保持する検索結果")
        self.add_record("検索結果の対象外")
        actions = [
            *self.search_actions(keyword="保持する検索結果")[:-1],
            *self.search_actions(year="invalid-year")[:-1],
            *self.search_actions(verification_status="invalid-status")[:-1],
            "9",
            "2",
            "0",
            "0",
        ]
        output_path = self.directory / "literature_search_results.csv"

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            _, _, outputs = self.run_with_actions(actions)

        exported.assert_called_once_with(
            self.connection,
            output_path,
            literature_ids=(matching_id,),
        )
        with output_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([int(row["id"]) for row in rows], [matching_id])
        self.assertTrue(any(item.startswith("入力エラー: ") for item in outputs))

    def test_unset_search_result_does_not_call_export_or_create_file(
        self,
    ) -> None:
        output_path = self.directory / "literature_search_results.csv"

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            _, _, outputs = self.run_with_actions(["9", "2", "0", "0"])

        exported.assert_not_called()
        self.assertFalse(output_path.exists())
        self.assertIn(cli_module._MISSING_SEARCH_RESULTS_MESSAGE, outputs)

    def test_deleted_search_result_preserves_existing_csv_on_export_error(
        self,
    ) -> None:
        literature_id = self.add_record("検索後削除対象")
        self.add_record("検索対象外の保持文献")
        output_path = self.directory / "literature_search_results.csv"
        marker = b"existing-csv-marker"
        output_path.write_bytes(marker)
        actions = [
            *self.search_actions(keyword="検索後削除対象")[:-1],
            "9",
            "2",
            "0",
            "0",
        ]
        feeder = InputFeeder(actions)
        outputs: list[str] = []
        deleted = False

        def output_func(message: str) -> None:
            nonlocal deleted
            outputs.append(message)
            if not deleted and "title: 検索後削除対象" in message:
                deleted = True
                self.assertTrue(delete_literature(self.connection, literature_id))

        with patch.object(
            cli_module,
            "export_literature_csv",
            wraps=cli_module.export_literature_csv,
        ) as exported:
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=output_func,
                export_directory=self.directory,
            )

        self.assertIsNone(result)
        self.assertTrue(deleted)
        exported.assert_called_once_with(
            self.connection,
            output_path,
            literature_ids=(literature_id,),
        )
        self.assertEqual(output_path.read_bytes(), marker)
        self.assertTrue(
            any(item.startswith("CSV出力エラー: ") for item in outputs)
        )
        self.assertEqual(
            list(self.directory.glob(f".{output_path.name}.*.tmp")),
            [],
        )
        self.assertEqual(list(self.directory.glob("*.csv")), [output_path])

    def test_missing_export_directory_is_not_created(self) -> None:
        missing_directory = self.directory / "missing-exports"

        _, _, outputs = self.run_with_actions(
            ["9", "1", "0", "0"],
            export_directory=missing_directory,
        )

        self.assertFalse(missing_directory.exists())
        self.assertTrue(
            any(item.startswith("CSV出力エラー: ") for item in outputs)
        )

    def test_csv_export_api_exception_boundaries(self) -> None:
        for expected in (
            ValueError("csv value failure"),
            OSError("csv filesystem failure"),
        ):
            with self.subTest(expected=type(expected).__name__):
                with patch.object(
                    cli_module,
                    "export_literature_csv",
                    side_effect=expected,
                ) as exported:
                    _, _, outputs = self.run_with_actions(["9", "1", "0", "0"])

                exported.assert_called_once()
                self.assertIn(f"CSV出力エラー: {expected}", outputs)
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

        database_error = sqlite3.OperationalError("csv database failure")
        outputs: list[str] = []
        with (
            patch.object(
                cli_module,
                "export_literature_csv",
                side_effect=database_error,
            ) as exported,
            self.assertRaises(sqlite3.OperationalError) as raised,
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(["9", "1"]),
                output_func=outputs.append,
                export_directory=self.directory,
            )
        self.assertIs(raised.exception, database_error)
        exported.assert_called_once()
        self.assertEqual(outputs.count(cli_module._DATABASE_ERROR_MESSAGE), 1)
        self.assertFalse(any(item.startswith("CSV出力エラー: ") for item in outputs))

        unexpected = RuntimeError("unexpected csv failure")
        outputs = []
        with (
            patch.object(
                cli_module,
                "export_literature_csv",
                side_effect=unexpected,
            ) as exported,
            self.assertRaises(RuntimeError) as raised,
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(["9", "1"]),
                output_func=outputs.append,
                export_directory=self.directory,
            )
        self.assertIs(raised.exception, unexpected)
        exported.assert_called_once()
        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
        self.assertFalse(any(item.startswith("CSV出力エラー: ") for item in outputs))

    def test_csv_submenu_input_and_pre_export_output_exceptions(self) -> None:
        for interruption in (EOFError("csv eof"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                with patch.object(cli_module, "export_literature_csv") as exported:
                    _, _, outputs = self.run_with_actions(["9", interruption])
                exported.assert_not_called()
                self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

        for expected in (
            ValueError("csv input value failure"),
            sqlite3.OperationalError("csv input sqlite failure"),
        ):
            with self.subTest(expected=type(expected).__name__):
                outputs: list[str] = []
                with (
                    patch.object(cli_module, "export_literature_csv") as exported,
                    self.assertRaises(type(expected)) as raised,
                ):
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(["9", expected]),
                        output_func=outputs.append,
                        export_directory=self.directory,
                    )
                self.assertIs(raised.exception, expected)
                exported.assert_not_called()
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

        expected = RuntimeError("csv menu output failure")
        outputs = []

        def output_func(message: str) -> None:
            outputs.append(message)
            if message == cli_module._CSV_EXPORT_MENU:
                raise expected

        with (
            patch.object(cli_module, "export_literature_csv") as exported,
            self.assertRaises(RuntimeError) as raised,
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(["9"]),
                output_func=output_func,
                export_directory=self.directory,
            )
        self.assertIs(raised.exception, expected)
        exported.assert_not_called()
        self.assertEqual(outputs.count(cli_module._CSV_EXPORT_MENU), 1)

    def test_csv_success_output_failure_keeps_file_without_retry_or_db_change(
        self,
    ) -> None:
        database_path = self.directory / "csv-output-failure.db"
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            literature_id = add_literature(
                connection,
                Literature(title="CSV output failure target"),
            )
            tag_id = create_tag(connection, "csv-output-tag")
            attach_tag_to_literature(connection, literature_id, tag_id)
            create_usage_history(connection, literature_id, "csv-output-use")
            tables_before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            output_path = self.directory / "literature_all.csv"
            expected = RuntimeError("csv success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "CSVを出力しました。":
                    raise expected

            with (
                patch.object(
                    cli_module,
                    "export_literature_csv",
                    wraps=cli_module.export_literature_csv,
                ) as exported,
                self.assertRaises(RuntimeError) as raised,
            ):
                run_cli(
                    connection,
                    input_func=InputFeeder(["9", "1"]),
                    output_func=output_func,
                    export_directory=self.directory,
                )

            self.assertIs(raised.exception, expected)
            exported.assert_called_once_with(
                connection,
                output_path,
                literature_ids=None,
            )
            self.assertTrue(output_path.is_file())
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual([int(row["id"]) for row in rows], [literature_id])
            self.assertEqual(self.table_snapshot_for(connection), tables_before)
            self.assertEqual(self.schema_snapshot_for(connection), schema_before)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_csv_export_preserves_active_transaction_and_database_state(
        self,
    ) -> None:
        database_path = self.directory / "csv-active-transaction.db"
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            committed_id = add_literature(
                connection,
                Literature(title="CSV committed literature"),
            )
            committed_tag_id = create_tag(connection, "csv-committed-tag")
            attach_tag_to_literature(
                connection,
                committed_id,
                committed_tag_id,
            )
            create_usage_history(connection, committed_id, "csv-committed-use")
            connection.execute("PRAGMA user_version = 802")
            sqlite3.Connection.commit(connection)

            pending_cursor = connection.execute(
                "INSERT INTO literature (title) VALUES (?)",
                ("CSV pending marker",),
            )
            pending_id = pending_cursor.lastrowid
            pending_tag_cursor = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("csv-pending-tag",),
            )
            pending_tag_id = pending_tag_cursor.lastrowid
            connection.execute(
                "INSERT INTO literature_tags (literature_id, tag_id) VALUES (?, ?)",
                (pending_id, pending_tag_id),
            )
            connection.execute(
                "INSERT INTO usage_history (literature_id, usage_type) VALUES (?, ?)",
                (pending_id, "csv-pending-use"),
            )
            self.assertTrue(connection.in_transaction)
            tables_before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            schema_version_before = connection.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            user_version_before = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0

            _, _, outputs = self.run_with_actions(
                ["9", "1", "0", "0"],
                connection=connection,
            )

            output_path = self.directory / "literature_all.csv"
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(
                [int(row["id"]) for row in rows],
                [committed_id, pending_id],
            )
            self.assertIn("件数: 2", outputs)
            self.assertEqual(self.table_snapshot_for(connection), tables_before)
            self.assertEqual(self.schema_snapshot_for(connection), schema_before)
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)

            sqlite3.Connection.rollback(connection)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature WHERE id = ?",
                    (pending_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (pending_tag_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_backup_default_directory_and_returned_path_are_used(self) -> None:
        backup_path = Path("backups") / "core-returned-backup.sqlite3"
        outputs: list[str] = []

        with patch.object(
            cli_module,
            "create_database_backup",
            return_value=backup_path,
        ) as backed_up:
            result = run_cli(
                self.connection,
                input_func=InputFeeder(["10", "0"]),
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        backed_up.assert_called_once_with(self.connection, "backups")
        self.assertIn("データベースをバックアップしました。", outputs)
        self.assertIn(f"保存先: {backup_path}", outputs)

    def test_backup_success_integrates_content_and_preserves_source(self) -> None:
        database_path = self.directory / "backup-success-source.db"
        backup_directory = self.directory / "backup-success-output"
        backup_directory.mkdir()
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            first_id = add_literature(
                connection,
                Literature(title="Backup integration first", rating=5),
            )
            second_id = add_literature(
                connection,
                Literature(title="Backup integration second"),
            )
            first_tag_id = create_tag(connection, "backup-alpha")
            second_tag_id = create_tag(connection, "backup-beta")
            attach_tag_to_literature(connection, first_id, first_tag_id)
            attach_tag_to_literature(connection, first_id, second_tag_id)
            attach_tag_to_literature(connection, second_id, first_tag_id)
            create_usage_history(
                connection,
                first_id,
                "backup-use",
                project_name="Backup integration project",
            )
            create_usage_history(connection, second_id, "backup-other-use")
            connection.execute("PRAGMA user_version = 803")
            sqlite3.Connection.commit(connection)
            tables_before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            schema_version_before = connection.execute(
                "PRAGMA schema_version"
            ).fetchone()[0]
            user_version_before = connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0]
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0

            with patch.object(
                cli_module,
                "create_database_backup",
                wraps=cli_module.create_database_backup,
            ) as backed_up:
                _, _, outputs = self.run_with_actions(
                    ["10", "0"],
                    connection=connection,
                    backup_directory=backup_directory,
                )

            backed_up.assert_called_once_with(connection, backup_directory)
            backup_files = list(backup_directory.glob("*.sqlite3"))
            self.assertEqual(len(backup_files), 1)
            backup_path = backup_files[0]
            self.assertIn(f"保存先: {backup_path}", outputs)
            backup_connection = sqlite3.connect(backup_path)
            backup_connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(
                    backup_connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    self.table_snapshot_for(backup_connection),
                    tables_before,
                )
                self.assertEqual(
                    self.schema_snapshot_for(backup_connection),
                    schema_before,
                )
                self.assertEqual(
                    backup_connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0],
                    user_version_before,
                )
            finally:
                backup_connection.close()

            self.assertEqual(self.table_snapshot_for(connection), tables_before)
            self.assertEqual(self.schema_snapshot_for(connection), schema_before)
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                list(
                    backup_directory.glob(
                        ".pt_research_library_backup_in_progress_*"
                    )
                ),
                [],
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_two_cli_backups_are_distinct_valid_and_preserve_source(self) -> None:
        self.add_record("Two CLI backups source")
        backup_directory = self.directory / "two-cli-backups"
        backup_directory.mkdir()
        tables_before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        create_backup = cli_module.create_database_backup
        first_backup_state: dict[str, object] = {}

        def create_and_track_backup(
            connection: sqlite3.Connection,
            directory: object,
        ) -> Path:
            backup_path = create_backup(connection, directory)
            if not first_backup_state:
                first_backup_state.update(
                    path=backup_path,
                    inode=backup_path.stat().st_ino,
                    contents=backup_path.read_bytes(),
                )
            else:
                first_path = first_backup_state["path"]
                assert isinstance(first_path, Path)
                self.assertTrue(first_path.is_file())
                self.assertEqual(
                    first_path.stat().st_ino,
                    first_backup_state["inode"],
                )
                self.assertEqual(
                    first_path.read_bytes(),
                    first_backup_state["contents"],
                )
            return backup_path

        with patch.object(
            cli_module,
            "create_database_backup",
            side_effect=create_and_track_backup,
        ) as backed_up:
            _, _, outputs = self.run_with_actions(
                ["10", "10", "0"],
                backup_directory=backup_directory,
            )

        self.assertEqual(backed_up.call_count, 2)
        backup_files = sorted(backup_directory.glob("*.sqlite3"))
        self.assertEqual(len(backup_files), 2)
        self.assertNotEqual(backup_files[0], backup_files[1])
        self.assertEqual(outputs.count("データベースをバックアップしました。"), 2)
        for backup_path in backup_files:
            with self.subTest(backup_path=backup_path):
                backup_connection = sqlite3.connect(backup_path)
                try:
                    self.assertEqual(
                        backup_connection.execute(
                            "PRAGMA quick_check"
                        ).fetchone()[0],
                        "ok",
                    )
                finally:
                    backup_connection.close()
        self.assertEqual(self.table_snapshot(), tables_before)
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            list(
                backup_directory.glob(
                    ".pt_research_library_backup_in_progress_*"
                )
            ),
            [],
        )

    def test_backup_active_transaction_is_preserved_and_creates_nothing(
        self,
    ) -> None:
        database_path = self.directory / "backup-active-source.db"
        backup_directory = self.directory / "backup-active-output"
        backup_directory.mkdir()
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            add_literature(
                connection,
                Literature(title="Backup committed source"),
            )
            pending_cursor = connection.execute(
                "INSERT INTO literature (title) VALUES (?)",
                ("Backup pending marker",),
            )
            pending_id = pending_cursor.lastrowid
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            self.assertTrue(connection.in_transaction)

            with patch.object(
                cli_module,
                "create_database_backup",
                wraps=cli_module.create_database_backup,
            ) as backed_up:
                _, _, outputs = self.run_with_actions(
                    ["10", "0"],
                    connection=connection,
                    backup_directory=backup_directory,
                )

            backed_up.assert_called_once_with(connection, backup_directory)
            self.assertTrue(
                any(item.startswith("バックアップエラー: ") for item in outputs)
            )
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT title FROM literature WHERE id = ?",
                    (pending_id,),
                ).fetchone()[0],
                "Backup pending marker",
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(list(backup_directory.iterdir()), [])

            sqlite3.Connection.rollback(connection)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM literature WHERE id = ?",
                    (pending_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_backup_invalid_directories_are_not_created_or_modified(
        self,
    ) -> None:
        self.add_record("Backup invalid directory source")
        source_before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        missing_directory = self.directory / "missing-backup-directory"
        file_path = self.directory / "backup-directory-file"
        file_path.write_text("kept directory marker", encoding="utf-8")

        for backup_directory in (missing_directory, file_path, None):
            with self.subTest(backup_directory=backup_directory):
                outputs: list[str] = []
                result = run_cli(
                    self.connection,
                    input_func=InputFeeder(["10", "0"]),
                    output_func=outputs.append,
                    export_directory=self.directory,
                    backup_directory=backup_directory,
                )
                self.assertIsNone(result)
                self.assertTrue(
                    any(
                        item.startswith("バックアップエラー: ")
                        for item in outputs
                    )
                )

        self.assertFalse(missing_directory.exists())
        self.assertEqual(
            file_path.read_text(encoding="utf-8"),
            "kept directory marker",
        )
        self.assertEqual(self.table_snapshot(), source_before)
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertFalse(
            any(
                path.name.startswith("pt_research_library_backup_")
                or path.name.startswith(
                    ".pt_research_library_backup_in_progress_"
                )
                for path in self.directory.iterdir()
            )
        )

    def test_backup_sqlite_error_is_rethrown_once_and_cleans_temporary(
        self,
    ) -> None:
        database_path = self.directory / "backup-failure-source.db"
        backup_directory = self.directory / "backup-failure-output"
        backup_directory.mkdir()
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=FailingBackupConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        connection.commit_calls = 0
        connection.rollback_calls = 0
        connection.close_calls = 0
        outputs: list[str] = []
        try:
            with (
                patch.object(
                    cli_module,
                    "create_database_backup",
                    wraps=cli_module.create_database_backup,
                ) as backed_up,
                self.assertRaises(sqlite3.OperationalError) as raised,
            ):
                run_cli(
                    connection,
                    input_func=InputFeeder(["10"]),
                    output_func=outputs.append,
                    export_directory=self.directory,
                    backup_directory=backup_directory,
                )

            self.assertIs(raised.exception, connection.backup_error)
            backed_up.assert_called_once_with(connection, backup_directory)
            self.assertEqual(connection.backup_calls, 1)
            self.assertEqual(outputs.count(cli_module._DATABASE_ERROR_MESSAGE), 1)
            self.assertFalse(
                any(item.startswith("バックアップエラー: ") for item in outputs)
            )
            self.assertEqual(list(backup_directory.iterdir()), [])
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_backup_success_output_failure_keeps_backup_without_retry(
        self,
    ) -> None:
        database_path = self.directory / "backup-output-source.db"
        backup_directory = self.directory / "backup-output-output"
        backup_directory.mkdir()
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            literature_id = add_literature(
                connection,
                Literature(title="Backup output failure source"),
            )
            source_before = self.table_snapshot_for(connection)
            schema_before = self.schema_snapshot_for(connection)
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            expected = RuntimeError("backup success output failure")
            outputs: list[str] = []

            def output_func(message: str) -> None:
                outputs.append(message)
                if message == "データベースをバックアップしました。":
                    raise expected

            with (
                patch.object(
                    cli_module,
                    "create_database_backup",
                    wraps=cli_module.create_database_backup,
                ) as backed_up,
                self.assertRaises(RuntimeError) as raised,
            ):
                run_cli(
                    connection,
                    input_func=InputFeeder(["10"]),
                    output_func=output_func,
                    export_directory=self.directory,
                    backup_directory=backup_directory,
                )

            self.assertIs(raised.exception, expected)
            backed_up.assert_called_once_with(connection, backup_directory)
            backup_files = list(backup_directory.glob("*.sqlite3"))
            self.assertEqual(len(backup_files), 1)
            backup_connection = sqlite3.connect(backup_files[0])
            try:
                self.assertEqual(
                    backup_connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    backup_connection.execute(
                        "SELECT id FROM literature"
                    ).fetchall(),
                    [(literature_id,)],
                )
            finally:
                backup_connection.close()
            self.assertEqual(
                self.table_snapshot_for(connection),
                source_before,
            )
            self.assertEqual(
                self.schema_snapshot_for(connection),
                schema_before,
            )
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_backup_unexpected_exception_is_propagated_unchanged(self) -> None:
        expected = RuntimeError("unexpected backup failure")
        outputs: list[str] = []

        with (
            patch.object(
                cli_module,
                "create_database_backup",
                side_effect=expected,
            ) as backed_up,
            self.assertRaises(RuntimeError) as raised,
        ):
            run_cli(
                self.connection,
                input_func=InputFeeder(["10"]),
                output_func=outputs.append,
                export_directory=self.directory,
                backup_directory=self.directory,
            )

        self.assertIs(raised.exception, expected)
        backed_up.assert_called_once_with(self.connection, self.directory)
        self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
        self.assertFalse(
            any(item.startswith("バックアップエラー: ") for item in outputs)
        )

    def test_literature_detail_main_menu_zero_through_thirteen_contract(
        self,
    ) -> None:
        feeder = InputFeeder(
            [
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
                "0",
            ]
        )
        outputs: list[str] = []

        with (
            patch.object(
                cli_module,
                "list_literature",
                return_value=[],
            ) as listed,
            patch.object(cli_module, "_run_search", return_value=False) as searched,
            patch.object(
                cli_module,
                "_run_registration",
                return_value=False,
            ) as registered,
            patch.object(cli_module, "_run_edit", return_value=False) as edited,
            patch.object(cli_module, "_run_delete", return_value=False) as deleted,
            patch.object(
                cli_module,
                "_run_tag_management",
                return_value=False,
            ) as tags_managed,
            patch.object(
                cli_module,
                "_run_usage_history_management",
                return_value=False,
            ) as histories_managed,
            patch.object(
                cli_module,
                "_run_literature_detail",
                return_value=False,
            ) as detailed,
            patch.object(
                cli_module,
                "_run_csv_export",
                return_value=False,
            ) as csv_exported,
            patch.object(cli_module, "_run_database_backup") as backed_up,
            patch.object(
                cli_module,
                "_run_structured_import",
                return_value=False,
            ) as structured_imported,
            patch.object(
                cli_module,
                "_run_evidence_management",
                return_value=False,
            ) as evidence_managed,
            patch.object(
                cli_module,
                "_run_comparison_management",
                return_value=False,
            ) as comparison_managed,
        ):
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=outputs.append,
            )

        self.assertIsNone(result)
        expected_options = (
            "1. 文献一覧",
            "2. 文献検索",
            "3. 文献登録",
            "4. 文献編集",
            "5. 文献削除",
            "6. タグ管理",
            "7. 使用履歴管理",
            "8. 文献詳細",
            "9. CSV出力",
            "10. SQLiteバックアップ",
            "11. ChatGPT構造化JSON取込",
            "12. Evidence確認・管理",
            "13. 複数文献比較",
            "0. 終了",
        )
        for option in expected_options:
            with self.subTest(option=option):
                self.assertIn(option, outputs[0])
        listed.assert_called_once_with(self.connection)
        for flow in (
            searched,
            registered,
            edited,
            deleted,
            tags_managed,
            histories_managed,
            detailed,
        ):
            flow.assert_called_once_with(
                self.connection,
                feeder,
                outputs.append,
            )
        csv_exported.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
            "exports",
            None,
        )
        backed_up.assert_called_once_with(
            self.connection,
            outputs.append,
            "backups",
        )
        structured_imported.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
        )
        evidence_managed.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
            project_root=None,
            pdf_opener=None,
        )
        comparison_managed.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
            None,
        )
        self.assertEqual(outputs.count(cli_module._INVALID_MENU_MESSAGE), 0)
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

    def test_absolute_application_export_path_supplies_navigation_project_root(
        self,
    ) -> None:
        feeder = InputFeeder(["12", "0"])
        outputs: list[str] = []
        export_directory = self.directory / "exports"

        with patch.object(
            cli_module,
            "_run_evidence_management",
            return_value=True,
        ) as evidence_managed:
            result = run_cli(
                self.connection,
                input_func=feeder,
                output_func=outputs.append,
                export_directory=export_directory,
            )

        self.assertIsNone(result)
        evidence_managed.assert_called_once_with(
            self.connection,
            feeder,
            outputs.append,
            project_root=self.directory,
            pdf_opener=None,
        )
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

    def test_structured_import_target_id_and_file_errors_return_to_menu(
        self,
    ) -> None:
        literature_id = self.add_record(
            "Synthetic Literature for Structured Import Contract"
        )
        invalid_json_path = self.directory / "invalid import.json"
        invalid_json_path.write_text("{invalid", encoding="utf-8")
        missing_path = self.directory / "missing import.json"
        actions = [
            "11",
            "abc",
            "11",
            "99999",
            "11",
            str(literature_id),
            f"'{missing_path}'",
            "11",
            str(literature_id),
            f"'{invalid_json_path}'",
            "0",
        ]

        _, feeder, outputs = self.run_with_actions(actions)

        self.assertEqual(len(feeder.prompts), len(actions))
        text = "\n".join(outputs)
        self.assertIn("Literature IDは1以上のASCII数字", text)
        self.assertIn("対象Literatureが見つかりません。", outputs)
        self.assertIn("JSON file読込エラー:", text)
        self.assertIn("JSON validation error:", text)
        self.assertEqual(
            self.structured_row_counts(self.connection),
            {
                "structured_entities": 0,
                "structured_fields": 0,
                "evidence_references": 0,
                "structured_field_evidence": 0,
                "structured_entity_evidence": 0,
            },
        )

    def test_structured_import_preview_and_cancel_write_nothing(self) -> None:
        literature_id = self.add_record(
            "Synthetic Literature for Structured Import Contract",
            authors="Existing author",
            verification_status="確認済み",
        )
        json_path = self.write_structured_import_json()
        literature_before = get_literature(self.connection, literature_id)

        _, feeder, outputs = self.run_with_actions(
            [
                "11",
                str(literature_id),
                f"'{json_path}'",
                "invalid",
                "0",
                "0",
            ]
        )

        self.assertEqual(len(feeder.prompts), 6)
        output = "\n".join(outputs)
        self.assertIn("Import Preview", output)
        self.assertIn(f"ID: {literature_id}", output)
        self.assertIn("analysis_scope: partial_text", output)
        self.assertIn("Study: 1", output)
        self.assertIn("Methods: 5", output)
        self.assertIn("Outcome: 2", output)
        self.assertIn("Evidence: 4", output)
        self.assertIn("ai_unverified", output)
        self.assertIn("bibliography", output)
        self.assertIn("import-only metadata", output)
        self.assertIn(cli_module._INVALID_CONFIRMATION_MESSAGE, outputs)
        self.assertIn("構造化JSONを保存せず戻ります。", outputs)
        self.assertEqual(
            self.structured_row_counts(self.connection),
            {table: 0 for table in self.structured_row_counts(self.connection)},
        )
        self.assertEqual(get_literature(self.connection, literature_id), literature_before)

    def test_structured_import_confirm_saves_atomic_mapping(self) -> None:
        literature_id = self.add_record(
            "Synthetic Literature for Structured Import Contract",
            authors="Existing author",
            verification_status="一部確認",
        )
        json_path = self.write_structured_import_json()

        _, feeder, outputs = self.run_with_actions(
            [
                "11",
                str(literature_id),
                str(json_path).replace(" ", "\\ "),
                "1",
                "0",
            ]
        )

        self.assertEqual(len(feeder.prompts), 5)
        self.assertIn("構造化JSONを保存しました。", outputs)
        counts = self.structured_row_counts(self.connection)
        self.assertEqual(counts["structured_entities"], 15)
        self.assertEqual(counts["structured_fields"], 96)
        self.assertEqual(counts["evidence_references"], 4)
        literature = get_literature(self.connection, literature_id)
        self.assertEqual(literature.authors, "Existing author")
        self.assertEqual(literature.verification_status, "一部確認")
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_structured_import_blocked_preview_never_prompts_to_save(self) -> None:
        literature_id = self.add_record(
            "Synthetic Literature for Structured Import Contract"
        )
        create_structured_entity(self.connection, literature_id, "study")
        json_path = self.write_structured_import_json()
        before = self.structured_row_counts(self.connection)

        _, feeder, outputs = self.run_with_actions(
            ["11", str(literature_id), f"'{json_path}'", "0"]
        )

        self.assertEqual(len(feeder.prompts), 4)
        output = "\n".join(outputs)
        self.assertIn("保存不可", output)
        self.assertIn("merge / overwrite", output)
        self.assertIn("保存不可のため確認menuへ進みません。", outputs)
        self.assertNotIn(cli_module._STRUCTURED_IMPORT_CONFIRMATION_MENU, outputs)
        self.assertEqual(self.structured_row_counts(self.connection), before)

    def test_structured_import_identifier_conflict_blocks_confirmation(self) -> None:
        literature_id = self.add_record(
            "Synthetic Literature for Structured Import Contract",
            doi="10.1000/existing",
        )
        example_path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "examples"
            / "structured_import_v1.example.json"
        )
        data = copy.deepcopy(json.loads(example_path.read_text(encoding="utf-8")))
        data["bibliography"]["doi"].update(
            value="10.1000/payload", availability="reported"
        )
        json_path = self.write_structured_import_json(data=data)

        _, _, outputs = self.run_with_actions(
            ["11", str(literature_id), f"'{json_path}'", "0"]
        )

        output = "\n".join(outputs)
        self.assertIn("DOI: 不一致（保存不可）", output)
        self.assertIn("Existing DOIとPayload DOIが不一致", output)
        self.assertNotIn(cli_module._STRUCTURED_IMPORT_CONFIRMATION_MENU, outputs)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_entities").fetchone()[0],
            0,
        )

    def test_literature_detail_displays_all_fields_tags_and_histories_in_order(
        self,
    ) -> None:
        literature_id = self.add_record(
            '詳細 "Title", 改行\n保持',
            authors='Author A, "Author B"',
            journal="Detail Journal",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="101-112",
            doi="10.1000/detail",
            pmid="00123",
            url="https://example.test/detail",
            language="日本語 / English",
            publication_type="原著",
            abstract="詳細抄録\nsecond line",
            pdf_path="/tmp/detail literature.pdf",
            personal_summary="自分の要約",
            ai_summary="手動入力したAI要約",
            ai_summary_status="修正済み",
            general_note="一般メモ",
            key_findings="主要な結果",
            methods_note="方法メモ",
            clinical_note="臨床メモ",
            limitation_note="限界メモ",
            relevance_note="関連メモ",
            evidence_level="Level II",
            verification_status="要確認",
            adoption_status="採用候補",
            exclusion_reason="除外理由",
            rating=4,
        )
        literature = get_literature(self.connection, literature_id)
        self.assertIsNotNone(literature)
        assert literature is not None
        tags = [
            Tag(id=20, name="repository-first"),
            Tag(id=10, name="repository-second"),
        ]
        histories = [
            UsageHistory(
                id=30,
                literature_id=literature_id,
                usage_type="学会発表",
                project_name="AHD project",
                usage_note="Methods slide",
                used_at="2026-08-01",
                created_at="2026-08-02T01:02:03.000Z",
            ),
            UsageHistory(
                id=25,
                literature_id=literature_id,
                usage_type="note",
                project_name=None,
                usage_note=None,
                used_at=None,
                created_at="2026-08-03T01:02:03.000Z",
            ),
        ]
        literature_before = vars(literature).copy()
        tags_before = [vars(tag).copy() for tag in tags]
        histories_before = [vars(history).copy() for history in histories]

        with (
            patch.object(
                cli_module,
                "get_literature",
                return_value=literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
                return_value=tags,
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
                return_value=histories,
            ) as listed_histories,
        ):
            _, _, outputs = self.run_with_actions(
                self.literature_detail_actions(literature_id)
            )

        retrieved.assert_called_once_with(self.connection, literature_id)
        listed_tags.assert_called_once_with(self.connection, literature_id)
        listed_histories.assert_called_once_with(self.connection, literature_id)
        self.assertEqual(vars(literature), literature_before)
        self.assertEqual([vars(tag) for tag in tags], tags_before)
        self.assertEqual([vars(history) for history in histories], histories_before)
        detail = cli_module._format_edit_literature(literature)
        self.assertIn(detail, outputs)
        expected_fields = ("id", *_REGISTRATION_FIELDS, "created_at", "updated_at")
        position = -1
        for index, field_name in enumerate(expected_fields):
            with self.subTest(field_name=field_name):
                prefix = "" if index == 0 else "\n"
                position = detail.find(
                    f"{prefix}{field_name}: ",
                    position + 1,
                )
                self.assertNotEqual(position, -1)
        displayed = "\n".join(outputs)
        self.assertLess(displayed.index("文献詳細:"), displayed.index("タグ:"))
        self.assertLess(displayed.index("タグ:"), displayed.index("使用履歴:"))
        self.assertLess(
            displayed.index(cli_module._format_tag(tags[0])),
            displayed.index(cli_module._format_tag(tags[1])),
        )
        self.assertLess(
            displayed.index(cli_module._format_usage_history(histories[0])),
            displayed.index(cli_module._format_usage_history(histories[1])),
        )
        for value in (
            "title: 詳細 \"Title\", 改行\n保持",
            "personal_summary: 自分の要約",
            "ai_summary: 手動入力したAI要約",
            "ai_summary_status: 修正済み",
            "general_note: 一般メモ",
            "verification_status: 要確認",
            "adoption_status: 採用候補",
        ):
            with self.subTest(value=value):
                self.assertIn(value, detail)

    def test_literature_detail_empty_related_records_and_null_display(
        self,
    ) -> None:
        literature_id = self.add_record("詳細NULL表示")
        literature = get_literature(self.connection, literature_id)
        self.assertIsNotNone(literature)
        assert literature is not None
        before = self.table_snapshot()

        with (
            patch.object(
                cli_module,
                "list_tags_for_literature",
                wraps=list_tags_for_literature,
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
                wraps=list_usage_history_for_literature,
            ) as listed_histories,
        ):
            _, _, outputs = self.run_with_actions(
                self.literature_detail_actions(literature_id)
            )

        listed_tags.assert_called_once_with(self.connection, literature_id)
        listed_histories.assert_called_once_with(self.connection, literature_id)
        detail = cli_module._format_edit_literature(literature)
        self.assertIn(detail, outputs)
        self.assertIn("authors: 未登録", detail)
        self.assertIn("rating: 未登録", detail)
        self.assertNotIn("None", detail)
        self.assertIn("この文献にはタグが登録されていません。", outputs)
        self.assertIn("この文献には使用履歴がありません。", outputs)
        self.assertEqual(self.table_snapshot(), before)

    def test_literature_detail_id_validation_missing_and_leading_zero(
        self,
    ) -> None:
        invalid_values = (
            "",
            "0",
            "-1",
            "+1",
            "1.5",
            "1e3",
            "１",
            "١",
            "id",
            "1x",
        )
        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                with (
                    patch.object(cli_module, "get_literature") as retrieved,
                    patch.object(
                        cli_module,
                        "list_tags_for_literature",
                    ) as listed_tags,
                    patch.object(
                        cli_module,
                        "list_usage_history_for_literature",
                    ) as listed_histories,
                ):
                    _, _, outputs = self.run_with_actions(
                        ["8", invalid_value, "0"]
                    )

                retrieved.assert_not_called()
                listed_tags.assert_not_called()
                listed_histories.assert_not_called()
                self.assertTrue(
                    any(
                        item.startswith("入力エラー: ")
                        and "文献ID" in item
                        and "ASCII" in item
                        for item in outputs
                    )
                )

        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
            ) as listed_histories,
        ):
            _, _, outputs = self.run_with_actions(
                self.literature_detail_actions(999999)
            )

        retrieved.assert_called_once_with(self.connection, 999999)
        listed_tags.assert_not_called()
        listed_histories.assert_not_called()
        self.assertIn("対象文献が見つかりません。", outputs)

        literature_id = self.add_record("詳細leading zero")
        padded_id = f" \t000{literature_id}\n "
        with (
            patch.object(
                cli_module,
                "get_literature",
                wraps=get_literature,
            ) as retrieved,
            patch.object(
                cli_module,
                "list_tags_for_literature",
                wraps=list_tags_for_literature,
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
                wraps=list_usage_history_for_literature,
            ) as listed_histories,
        ):
            _, _, outputs = self.run_with_actions(
                self.literature_detail_actions(padded_id)
            )

        retrieved.assert_called_once_with(self.connection, literature_id)
        listed_tags.assert_called_once_with(self.connection, literature_id)
        listed_histories.assert_called_once_with(self.connection, literature_id)
        self.assertTrue(
            any(item.startswith(f"id: {literature_id}\n") for item in outputs)
        )

    def test_literature_detail_stops_at_each_disappearance_race(self) -> None:
        literature_id = self.add_record("詳細race")
        before = self.table_snapshot()

        with (
            patch.object(
                cli_module,
                "list_tags_for_literature",
                return_value=None,
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
            ) as listed_histories,
        ):
            _, _, tag_outputs = self.run_with_actions(
                self.literature_detail_actions(literature_id)
            )

        listed_tags.assert_called_once_with(self.connection, literature_id)
        listed_histories.assert_not_called()
        self.assertIn(
            "文献情報の取得中に対象文献が存在しなくなりました。",
            tag_outputs,
        )

        tags = [Tag(id=2, name="race-tag")]
        with (
            patch.object(
                cli_module,
                "list_tags_for_literature",
                return_value=tags,
            ) as listed_tags,
            patch.object(
                cli_module,
                "list_usage_history_for_literature",
                return_value=None,
            ) as listed_histories,
        ):
            _, _, usage_outputs = self.run_with_actions(
                self.literature_detail_actions(literature_id)
            )

        listed_tags.assert_called_once_with(self.connection, literature_id)
        listed_histories.assert_called_once_with(self.connection, literature_id)
        self.assertIn(cli_module._format_tag(tags[0]), usage_outputs)
        self.assertIn(
            "文献情報の取得中に対象文献が存在しなくなりました。",
            usage_outputs,
        )
        self.assertEqual(self.table_snapshot(), before)

    def test_literature_detail_repository_sqlite_errors_are_announced_and_raised(
        self,
    ) -> None:
        literature_id = self.add_record("詳細repository error")
        literature = get_literature(self.connection, literature_id)
        self.assertIsNotNone(literature)
        assert literature is not None
        before = self.table_snapshot()

        for failing_api in ("get", "tags", "histories"):
            with self.subTest(failing_api=failing_api):
                expected = sqlite3.OperationalError(f"detail {failing_api}")
                with (
                    patch.object(
                        cli_module,
                        "get_literature",
                        return_value=literature,
                    ) as retrieved,
                    patch.object(
                        cli_module,
                        "list_tags_for_literature",
                        return_value=[],
                    ) as listed_tags,
                    patch.object(
                        cli_module,
                        "list_usage_history_for_literature",
                        return_value=[],
                    ) as listed_histories,
                ):
                    {
                        "get": retrieved,
                        "tags": listed_tags,
                        "histories": listed_histories,
                    }[failing_api].side_effect = expected
                    with self.assertRaises(sqlite3.OperationalError) as raised:
                        run_cli(
                            self.connection,
                            input_func=InputFeeder(["8", str(literature_id)]),
                            output_func=(outputs := []).append,
                        )

                self.assertIs(raised.exception, expected)
                retrieved.assert_called_once_with(self.connection, literature_id)
                if failing_api == "get":
                    listed_tags.assert_not_called()
                    listed_histories.assert_not_called()
                elif failing_api == "tags":
                    listed_tags.assert_called_once_with(
                        self.connection,
                        literature_id,
                    )
                    listed_histories.assert_not_called()
                else:
                    listed_tags.assert_called_once_with(
                        self.connection,
                        literature_id,
                    )
                    listed_histories.assert_called_once_with(
                        self.connection,
                        literature_id,
                    )
                self.assertEqual(
                    outputs.count(cli_module._DATABASE_ERROR_MESSAGE),
                    1,
                )
                self.assertEqual(self.table_snapshot(), before)

    def test_literature_detail_input_exception_boundaries(self) -> None:
        before = self.table_snapshot()
        for interruption in (EOFError("detail eof"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                with patch.object(cli_module, "get_literature") as retrieved:
                    _, _, outputs = self.run_with_actions(["8", interruption])

                retrieved.assert_not_called()
                self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)

        for expected in (
            ValueError("detail input value error"),
            sqlite3.OperationalError("detail input sqlite error"),
        ):
            with self.subTest(expected=type(expected).__name__):
                outputs: list[str] = []
                with (
                    patch.object(cli_module, "get_literature") as retrieved,
                    self.assertRaises(type(expected)) as raised,
                ):
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(["8", expected]),
                        output_func=outputs.append,
                    )

                self.assertIs(raised.exception, expected)
                retrieved.assert_not_called()
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
        self.assertEqual(self.table_snapshot(), before)

    def test_literature_detail_output_exceptions_do_not_retry_reads(
        self,
    ) -> None:
        literature_id = self.add_record("詳細output error")
        tag_id = create_tag(self.connection, "detail-output-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        history_id = create_usage_history(
            self.connection,
            literature_id,
            "detail-output-use",
        )
        before = self.table_snapshot()
        cases = (
            ("文献詳細:", RuntimeError("detail heading output"), 0, 0),
            (
                f"ID: {tag_id}\nname: detail-output-tag",
                ValueError("detail tag output"),
                1,
                0,
            ),
            ("使用履歴:", sqlite3.OperationalError("detail usage heading"), 1, 0),
            (
                f"id: {history_id}\nliterature_id: {literature_id}\n",
                RuntimeError("detail history output"),
                1,
                1,
            ),
        )

        for failing_text, expected, tag_calls, history_calls in cases:
            with self.subTest(failing_text=failing_text):
                outputs: list[str] = []

                def output_func(message: str) -> None:
                    outputs.append(message)
                    if failing_text in message:
                        raise expected

                with (
                    patch.object(
                        cli_module,
                        "get_literature",
                        wraps=get_literature,
                    ) as retrieved,
                    patch.object(
                        cli_module,
                        "list_tags_for_literature",
                        wraps=list_tags_for_literature,
                    ) as listed_tags,
                    patch.object(
                        cli_module,
                        "list_usage_history_for_literature",
                        wraps=list_usage_history_for_literature,
                    ) as listed_histories,
                    self.assertRaises(type(expected)) as raised,
                ):
                    run_cli(
                        self.connection,
                        input_func=InputFeeder(["8", str(literature_id)]),
                        output_func=output_func,
                    )

                self.assertIs(raised.exception, expected)
                retrieved.assert_called_once_with(self.connection, literature_id)
                self.assertEqual(listed_tags.call_count, tag_calls)
                self.assertEqual(listed_histories.call_count, history_calls)
                self.assertNotIn(cli_module._DATABASE_ERROR_MESSAGE, outputs)
                self.assertEqual(self.table_snapshot(), before)

    def test_literature_detail_preserves_active_transaction_and_marker(
        self,
    ) -> None:
        database_path = self.directory / "detail-active-transaction.db"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            literature_id = add_literature(
                connection,
                Literature(title="詳細active transaction"),
            )
            tag_id = create_tag(connection, "detail-committed-tag")
            attach_tag_to_literature(connection, literature_id, tag_id)
            create_usage_history(connection, literature_id, "committed use")
            marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("detail-pending-marker",),
            )
            marker_id = marker.lastrowid
            self.assertIsNotNone(marker_id)
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            self.assertTrue(connection.in_transaction)

            result, _, outputs = self.run_with_actions(
                self.literature_detail_actions(literature_id),
                connection=connection,
            )

            self.assertIsNone(result)
            self.assertIn("文献詳細:", outputs)
            self.assertIn("ID: " + str(tag_id) + "\nname: detail-committed-tag", outputs)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_id,),
                ).fetchone()[0],
                1,
            )

            sqlite3.Connection.rollback(connection)
            self.assertFalse(connection.in_transaction)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?",
                    (marker_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_literature_detail_preserves_all_tables_schema_and_pragmas(
        self,
    ) -> None:
        literature_id = self.add_record(
            "詳細DB不変対象",
            ai_summary="AI要約",
            personal_summary="自分の要約",
        )
        other_id = self.add_record("詳細DB不変対象外")
        first_tag_id = create_tag(self.connection, "detail-alpha")
        second_tag_id = create_tag(self.connection, "detail-beta")
        attach_tag_to_literature(
            self.connection,
            literature_id,
            second_tag_id,
        )
        attach_tag_to_literature(
            self.connection,
            literature_id,
            first_tag_id,
        )
        attach_tag_to_literature(self.connection, other_id, first_tag_id)
        create_usage_history(self.connection, literature_id, "note")
        create_usage_history(self.connection, literature_id, "学会発表")
        create_usage_history(self.connection, other_id, "other")
        self.connection.execute("PRAGMA user_version = 801")
        tables_before = self.table_snapshot()
        schema_before = self.schema_snapshot()
        schema_version_before = self.connection.execute(
            "PRAGMA schema_version"
        ).fetchone()[0]
        user_version_before = self.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]

        _, _, outputs = self.run_with_actions(
            self.literature_detail_actions(literature_id)
        )

        self.assertIn("文献詳細:", outputs)
        self.assertEqual(self.table_snapshot(), tables_before)
        self.assertEqual(self.schema_snapshot(), schema_before)
        self.assertEqual(
            self.connection.execute("PRAGMA schema_version").fetchone()[0],
            schema_version_before,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA user_version").fetchone()[0],
            user_version_before,
        )
        self.assertFalse(self.connection.in_transaction)

    def test_full_cli_session_integrates_all_completed_features(self) -> None:
        integration_directory = self.directory / "full-cli-session"
        export_directory = integration_directory / "exports"
        backup_directory = integration_directory / "backups"
        export_directory.mkdir(parents=True)
        backup_directory.mkdir()

        title = "Synthetic Step 8D-5 CLI integration literature"
        updated_author = "Updated synthetic integration author"
        tag_name = "synthetic-integration-tag"
        usage_type = "synthetic-integration-use"
        actions = [
            "3",
            *self.registration_values(
                title=title,
                authors="Initial synthetic integration author",
                publication_year="2026",
                personal_summary="Synthetic test-only summary",
            ),
            "1",
            "1",
            "8",
            "1",
            "4",
            "1",
            "2",
            updated_author,
            "1",
            "6",
            "2",
            tag_name,
            "1",
            "6",
            "1",
            "1",
            "1",
            "0",
            "7",
            "2",
            "1",
            usage_type,
            "Synthetic integration project",
            "Synthetic integration note",
            "2026-08-17",
            "1",
            "0",
            *self.search_actions(keyword=title)[:-1],
            "9",
            "2",
            "0",
            "10",
            "0",
        ]

        with (
            patch.object(
                cli_module,
                "export_literature_csv",
                wraps=cli_module.export_literature_csv,
            ) as exported,
            patch.object(
                cli_module,
                "create_database_backup",
                wraps=cli_module.create_database_backup,
            ) as backed_up,
        ):
            result, feeder, outputs = self.run_with_actions(
                actions,
                export_directory=export_directory,
                backup_directory=backup_directory,
            )

        self.assertIsNone(result)
        self.assertEqual(len(feeder.prompts), len(actions))
        self.assertEqual(outputs.count(cli_module._MAIN_MENU), 10)
        self.assertEqual(outputs.count(cli_module._TAG_MANAGEMENT_MENU), 3)
        self.assertEqual(
            outputs.count(cli_module._USAGE_HISTORY_MANAGEMENT_MENU),
            2,
        )
        self.assertEqual(outputs.count(cli_module._CSV_EXPORT_MENU), 2)
        for success_message in (
            "文献を登録しました。",
            "文献詳細:",
            "文献を更新しました。",
            "文献へタグを付与しました。",
            "使用履歴を登録しました。",
            "CSVを出力しました。",
            "データベースをバックアップしました。",
            cli_module._EXIT_MESSAGE,
        ):
            self.assertIn(success_message, outputs)
        self.assertGreaterEqual("\n".join(outputs).count(f"title: {title}"), 7)

        literature = get_literature(self.connection, 1)
        self.assertIsNotNone(literature)
        assert literature is not None
        self.assertEqual(literature.title, title)
        self.assertEqual(literature.authors, updated_author)
        self.assertEqual(
            list_tags_for_literature(self.connection, 1),
            [Tag(id=1, name=tag_name)],
        )
        histories = list_usage_history_for_literature(self.connection, 1)
        self.assertIsNotNone(histories)
        assert histories is not None
        self.assertEqual(len(histories), 1)
        self.assertEqual(histories[0].usage_type, usage_type)
        self.assertEqual(
            {
                table: self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "literature",
                    "tags",
                    "literature_tags",
                    "usage_history",
                )
            },
            {
                "literature": 1,
                "tags": 1,
                "literature_tags": 1,
                "usage_history": 1,
            },
        )
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        self.assertFalse(self.connection.in_transaction)

        csv_path = export_directory / "literature_search_results.csv"
        exported.assert_called_once_with(
            self.connection,
            csv_path,
            literature_ids=(1,),
        )
        self.assertTrue(csv_path.read_bytes().startswith(codecs.BOM_UTF8))
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            rows = list(reader)
            fieldnames = reader.fieldnames
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], title)
        self.assertEqual(rows[0]["authors"], updated_author)
        self.assertEqual(rows[0]["tags"], tag_name)
        self.assertIsNotNone(fieldnames)
        assert fieldnames is not None
        self.assertNotIn("usage_type", fieldnames)

        backed_up.assert_called_once_with(self.connection, backup_directory)
        backup_paths = list(backup_directory.glob("*.sqlite3"))
        self.assertEqual(len(backup_paths), 1)
        backup_connection = sqlite3.connect(backup_paths[0])
        try:
            self.assertEqual(
                backup_connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            self.assertEqual(
                backup_connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
            self.assertEqual(
                backup_connection.execute(
                    "SELECT title, authors FROM literature WHERE id = ?",
                    (1,),
                ).fetchone(),
                (title, updated_author),
            )
            self.assertEqual(
                backup_connection.execute(
                    "SELECT COUNT(*) FROM literature_tags"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                backup_connection.execute(
                    "SELECT usage_type FROM usage_history"
                ).fetchone()[0],
                usage_type,
            )
        finally:
            backup_connection.close()

    def test_cancel_operations_share_zero_contract_and_do_not_write(self) -> None:
        literature_id = self.add_record(
            "Synthetic cancellation integration literature",
            authors="Unchanged synthetic author",
        )
        tag_id = create_tag(self.connection, "synthetic-cancel-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        history_id = create_usage_history(
            self.connection,
            literature_id,
            "synthetic-cancel-use",
            "Unchanged synthetic project",
        )
        before = self.table_snapshot()
        actions = [
            "4",
            str(literature_id),
            "0",
            "7",
            "3",
            str(history_id),
            "5",
            "invalid",
            "0",
            "0",
            "5",
            str(literature_id),
            "0",
            "1",
            "0",
        ]

        with (
            patch.object(cli_module, "update_literature") as literature_updated,
            patch.object(cli_module, "update_usage_history") as history_updated,
            patch.object(cli_module, "delete_literature") as literature_deleted,
        ):
            result, feeder, outputs = self.run_with_actions(actions)

        self.assertIsNone(result)
        self.assertEqual(len(feeder.prompts), len(actions))
        literature_updated.assert_not_called()
        history_updated.assert_not_called()
        literature_deleted.assert_not_called()
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        self.assertEqual(
            cli_module._USAGE_HISTORY_EDIT_FIELD_MENU,
            """1. usage_type
2. project_name
3. usage_note
4. used_at
0. 編集中止""",
        )
        self.assertNotIn(
            "5. 編集中止",
            cli_module._USAGE_HISTORY_EDIT_FIELD_MENU,
        )
        self.assertEqual(
            cli_module._INVALID_USAGE_HISTORY_EDIT_FIELD_MESSAGE,
            "入力エラー: 0、1、2、3、4のいずれかを選択してください。",
        )
        self.assertEqual(
            outputs.count(cli_module._INVALID_USAGE_HISTORY_EDIT_FIELD_MESSAGE),
            2,
        )
        for cancellation_message in (
            "文献編集を中止しました。",
            "使用履歴編集を中止しました。",
            "文献削除を中止しました。",
        ):
            self.assertIn(cancellation_message, outputs)
        self.assertIn(
            "title: Synthetic cancellation integration literature",
            "\n".join(outputs),
        )
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

    def test_evidence_submenu_contract_invalid_navigation_and_interrupts(self) -> None:
        invalid_count = 50
        feeder = InputFeeder(
            ["12", "", "invalid", *(["11"] * invalid_count), "0", "0"]
        )
        outputs: list[str] = []

        result = run_cli(
            self.connection,
            input_func=feeder,
            output_func=outputs.append,
        )

        self.assertIsNone(result)
        for option in (
            "1. Literature別Evidence一覧",
            "2. Evidence詳細",
            "3. Evidence新規作成",
            "4. Evidence編集",
            "5. Evidence確認状態変更",
            "6. Structured item → Evidence確認",
            "7. EvidenceをStructured itemへ関連付け",
            "8. EvidenceとStructured itemの関連解除",
            "9. Evidence削除",
            "10. Evidenceから原著PDFを開く",
            "0. メインメニューへ戻る",
        ):
            self.assertIn(option, cli_module._EVIDENCE_MANAGEMENT_MENU)
        self.assertEqual(
            outputs.count(cli_module._INVALID_EVIDENCE_MENU_MESSAGE),
            invalid_count + 2,
        )
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

        for interruption in (EOFError("evidence EOF"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                _, _, interrupted_outputs = self.run_with_actions(
                    ["12", interruption]
                )
                self.assertEqual(
                    interrupted_outputs.count(cli_module._EXIT_MESSAGE), 1
                )

    def test_evidence_cli_list_empty_unknown_detail_and_human_backlinks(self) -> None:
        empty_id = self.add_record("Synthetic empty Evidence target")
        target_id = self.add_record("Synthetic Evidence detail target")
        outcome_id = create_structured_entity(
            self.connection, target_id, "outcome"
        )
        name_id = create_structured_field(
            self.connection,
            outcome_id,
            "name",
            content_role="source_fact",
            value="Synthetic outcome",
            availability="reported",
        )
        evidence_id = create_evidence_reference(
            self.connection,
            target_id,
            pdf_page=3,
            printed_page="S4",
            section="Results",
            subsection="Primary",
            table_label="Table 2",
            figure_label="Figure 1",
            quote_text="Synthetic exact quote",
            note="Synthetic Evidence note",
            verification="user_verified",
        )
        attach_evidence_to_entity(self.connection, outcome_id, evidence_id)
        attach_evidence_to_field(self.connection, name_id, evidence_id)

        actions = [
            "12",
            "1",
            "999999",
            "1",
            str(empty_id),
            "1",
            str(target_id),
            "2",
            str(target_id),
            "1",
            "0",
            "0",
        ]
        _, _, outputs = self.run_with_actions(actions)
        text = "\n".join(outputs)

        self.assertIn("対象Literatureが見つかりません。", outputs)
        self.assertIn("Evidence件数: 0", outputs)
        self.assertIn(
            "このLiteratureにはEvidenceが登録されていません。", outputs
        )
        for expected in (
            "pdf_page: 3",
            "printed_page: S4",
            "section: Results",
            "subsection: Primary",
            "table_label: Table 2",
            "figure_label: Figure 1",
            "quote_text: あり",
            "note: あり",
            "verification: user_verified",
            "このEvidenceが支えているstructured item:",
            "Field link: Outcome",
            "name",
            "value: Synthetic outcome",
            "Entity link: Outcome",
            "Synthetic exact quote",
            "Synthetic Evidence note",
        ):
            self.assertIn(expected, text)
        self.assertNotIn(f"evidence_id: {evidence_id}", text)

    def test_evidence_cli_uses_unique_outcome_labels_across_review_screens(
        self,
    ) -> None:
        literature_id = self.add_record("Synthetic colliding Outcome target")
        outcome_ids: list[int] = []
        result_ids: list[int] = []
        for sort_order in (0, 1):
            outcome_id = create_structured_entity(
                self.connection,
                literature_id,
                "outcome",
                sort_order=sort_order,
            )
            outcome_ids.append(outcome_id)
            create_structured_field(
                self.connection,
                outcome_id,
                "name",
                content_role="source_fact",
                value="Strain",
                availability="reported",
            )
            result_id = create_structured_entity(
                self.connection,
                literature_id,
                "result",
                parent_entity_id=outcome_id,
                sort_order=sort_order + 2,
            )
            result_ids.append(result_id)
            create_structured_field(
                self.connection,
                result_id,
                "condition_or_comparison",
                content_role="source_fact",
                value="Baseline",
                availability="reported",
            )
            create_structured_field(
                self.connection,
                result_id,
                "result",
                content_role="source_fact",
                value="No significant difference",
                availability="reported",
            )
        evidence_id = create_evidence_reference(
            self.connection, literature_id, section="Results"
        )
        attach_evidence_to_entity(
            self.connection, outcome_ids[1], evidence_id
        )
        attach_evidence_to_entity(
            self.connection, result_ids[1], evidence_id
        )

        _, _, outputs = self.run_with_actions(
            [
                "12",
                "2",
                str(literature_id),
                "1",
                "6",
                str(literature_id),
                "7",
                str(literature_id),
                "1",
                "1",
                "0",
                "8",
                str(literature_id),
                "1",
                "1",
                "0",
                "0",
                "0",
            ]
        )

        text = "\n".join(outputs)
        first_outcome = "Strain (1)"
        second_outcome = "Strain (2)"
        second_result = (
            "Strain (2) — Baseline — No significant difference"
        )
        self.assertIn(first_outcome, text)
        self.assertIn(second_outcome, text)
        self.assertIn(second_result, text)
        self.assertGreaterEqual(text.count(first_outcome), 2)
        self.assertGreaterEqual(text.count(second_outcome), 4)
        self.assertIn("Evidenceの関連付けを中止しました。", outputs)
        self.assertIn("Evidenceの関連解除を中止しました。", outputs)
        self.assertIsNotNone(get_evidence_reference(self.connection, evidence_id))
        self.assertEqual(
            [item.id for item in list_evidence_for_entity(self.connection, outcome_ids[1])],
            [evidence_id],
        )

    def test_evidence_cli_create_preview_cancel_validation_and_default(self) -> None:
        literature_id = self.add_record("Synthetic Evidence create target")
        cancel_values = ["1", "S1", "Methods", "", "", "", "quote", "note"]
        valid_values = [
            "2",
            "S2",
            "Results",
            "Primary",
            "Table 1",
            "Figure 2",
            "Synthetic source text",
            "Synthetic note",
        ]
        note_only = ["", "", "", "", "", "", "", "note only"]
        actions = [
            "12",
            "3",
            str(literature_id),
            *cancel_values,
            "0",
            "3",
            str(literature_id),
            *note_only,
            "1",
            "3",
            str(literature_id),
            *valid_values,
            "1",
            "0",
            "0",
        ]

        _, _, outputs = self.run_with_actions(actions)
        evidence = self.connection.execute(
            """
            SELECT pdf_page, printed_page, section, subsection,
                   table_label, figure_label, quote_text, note, verification
            FROM evidence_references
            ORDER BY id
            """
        ).fetchall()

        self.assertEqual(len(evidence), 1)
        self.assertEqual(
            tuple(evidence[0]),
            (2, *valid_values[1:], "ai_unverified"),
        )
        text = "\n".join(outputs)
        self.assertIn("Evidence保存前Preview:", outputs)
        self.assertIn("Evidenceの保存を中止しました。", outputs)
        self.assertIn("Evidence作成エラー:", text)
        self.assertIn("note以外のlocatorまたはquote", text)
        self.assertIn("確認状態: ai_unverified", text)

    def test_evidence_cli_edit_reset_warning_cancel_invalid_and_note_preserve(self) -> None:
        literature_id = self.add_record("Synthetic Evidence edit target")
        evidence_id = create_evidence_reference(
            self.connection,
            literature_id,
            pdf_page=5,
            section="Before",
            quote_text="Original quote",
            note="Before note",
            verification="user_verified",
        )

        self.run_with_actions(
            [
                "12",
                "4",
                str(literature_id),
                "1",
                "8",
                "Cancelled note",
                "0",
                "4",
                str(literature_id),
                "1",
                "1",
                "0",
                "4",
                str(literature_id),
                "1",
                "8",
                "Updated note",
                "1",
                "0",
                "0",
            ]
        )
        after_note = get_evidence_reference(self.connection, evidence_id)
        self.assertEqual(after_note.note, "Updated note")
        self.assertEqual(after_note.verification, "user_verified")
        self.assertEqual(after_note.pdf_page, 5)

        _, _, outputs = self.run_with_actions(
            [
                "12",
                "4",
                str(literature_id),
                "1",
                "3",
                "After",
                "1",
                "0",
                "0",
            ]
        )
        updated = get_evidence_reference(self.connection, evidence_id)
        self.assertEqual(updated.section, "After")
        self.assertEqual(updated.verification, "ai_unverified")
        self.assertIn(
            "根拠位置または原文を変更するため、確認状態はai_unverifiedへ戻ります",
            outputs,
        )

    def test_evidence_cli_verification_explicit_cancel_reverse_and_no_cascade(self) -> None:
        literature_id = self.add_record(
            "Synthetic Evidence verification target",
            verification_status="要確認",
            ai_summary_status="修正済み",
        )
        entity_id = create_structured_entity(
            self.connection,
            literature_id,
            "outcome",
            verification="ai_unverified",
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            "name",
            content_role="source_fact",
            value="Synthetic outcome",
            availability="reported",
            verification="ai_unverified",
        )
        evidence_id = create_evidence_reference(
            self.connection, literature_id, pdf_page=6
        )
        before_literature = get_literature(self.connection, literature_id)
        before_entity = get_structured_entity(self.connection, entity_id)
        before_field = get_structured_field(self.connection, field_id)

        _, _, outputs = self.run_with_actions(
            [
                "12",
                "5",
                str(literature_id),
                "1",
                "0",
                "5",
                str(literature_id),
                "1",
                "1",
                "5",
                str(literature_id),
                "1",
                "1",
                "0",
                "0",
            ]
        )

        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "ai_unverified",
        )
        self.assertEqual(get_literature(self.connection, literature_id), before_literature)
        self.assertEqual(get_structured_entity(self.connection, entity_id), before_entity)
        self.assertEqual(get_structured_field(self.connection, field_id), before_field)
        text = "\n".join(outputs)
        self.assertIn(
            "原著の該当箇所を確認した場合のみ確認済みにしてください。",
            outputs,
        )
        self.assertIn(
            "このEvidenceの確認済み状態を取り消します。", outputs
        )
        self.assertIn("ai_unverified → user_verified", text)
        self.assertIn("user_verified → ai_unverified", text)

    def test_evidence_cli_structured_item_read_attach_duplicate_and_detach(self) -> None:
        literature_id = self.add_record("Synthetic Evidence association target")
        entity_id = create_structured_entity(
            self.connection, literature_id, "method_body_condition"
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            "load",
            content_role="source_fact",
            value={"inferior_force_N": 90},
            availability="reported",
        )
        evidence_id = create_evidence_reference(
            self.connection, literature_id, section="Methods"
        )
        before_verification = get_evidence_reference(
            self.connection, evidence_id
        ).verification

        _, _, outputs = self.run_with_actions(
            [
                "12",
                "6",
                str(literature_id),
                "7",
                str(literature_id),
                "1",
                "2",
                "1",
                "7",
                str(literature_id),
                "1",
                "2",
                "1",
                "8",
                str(literature_id),
                "1",
                "1",
                "1",
                "0",
                "0",
            ]
        )

        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])
        self.assertIsNotNone(get_evidence_reference(self.connection, evidence_id))
        self.assertIsNotNone(get_structured_field(self.connection, field_id))
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            before_verification,
        )
        text = "\n".join(outputs)
        self.assertIn("Methods / Body Condition", text)
        self.assertIn('value: {"inferior_force_N": 90}', text)
        self.assertIn("linked Evidence count: 0", text)
        self.assertIn("Evidenceをstructured itemへ関連付けました。", text)
        self.assertIn("重複作成しませんでした。", text)
        self.assertIn("関連を解除しました。", text)

    def test_evidence_cli_delete_two_steps_preserves_structured_items_and_unrelated(self) -> None:
        literature_id = self.add_record("Synthetic Evidence delete target")
        entity_id = create_structured_entity(
            self.connection, literature_id, "outcome"
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            "name",
            content_role="source_fact",
            value="Synthetic delete outcome",
            availability="reported",
        )
        target_id = create_evidence_reference(
            self.connection, literature_id, pdf_page=8
        )
        unrelated_id = create_evidence_reference(
            self.connection, literature_id, pdf_page=9
        )
        attach_evidence_to_entity(self.connection, entity_id, target_id)
        attach_evidence_to_field(self.connection, field_id, target_id)

        self.run_with_actions(
            [
                "12",
                "9",
                str(literature_id),
                "1",
                "0",
                "9",
                str(literature_id),
                "1",
                "1",
                "2",
                "0",
                "9",
                str(literature_id),
                "1",
                "1",
                "1",
                "0",
                "0",
            ]
        )

        self.assertIsNone(get_evidence_reference(self.connection, target_id))
        self.assertIsNotNone(get_evidence_reference(self.connection, unrelated_id))
        self.assertIsNotNone(get_literature(self.connection, literature_id))
        self.assertIsNotNone(get_structured_entity(self.connection, entity_id))
        self.assertIsNotNone(get_structured_field(self.connection, field_id))
        self.assertEqual(list_evidence_for_entity(self.connection, entity_id), [])
        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])

    def test_evidence_cli_literature_detail_counts_and_transaction_safety(self) -> None:
        literature_id = self.add_record("Synthetic Evidence detail counts")
        create_evidence_reference(
            self.connection,
            literature_id,
            pdf_page=1,
            verification="ai_unverified",
        )
        create_evidence_reference(
            self.connection,
            literature_id,
            pdf_page=2,
            verification="user_verified",
        )

        _, _, outputs = self.run_with_actions(["8", str(literature_id), "0"])
        text = "\n".join(outputs)
        self.assertIn("Structured Evidence:", text)
        self.assertIn("Evidence件数: 2", text)
        self.assertIn("ai_unverified件数: 1", text)
        self.assertIn("user_verified件数: 1", text)

        marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)", ("pending-evidence-cli",)
        )
        marker_id = marker.lastrowid
        _, feeder, transaction_outputs = self.run_with_actions(
            ["12", "3", "0", "0"]
        )
        self.assertIn(
            cli_module._EVIDENCE_ACTIVE_TRANSACTION_MESSAGE,
            transaction_outputs,
        )
        self.assertEqual(len(feeder.prompts), 4)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?", (marker_id,)
            ).fetchone()[0],
            0,
        )

    def test_evidence_pdf_navigation_preview_confirm_and_page_guidance(self) -> None:
        pdf_path = self.directory / "originals" / "Synthetic 原著.PDF"
        pdf_path.parent.mkdir()
        pdf_path.write_bytes(b"synthetic test-only PDF placeholder")
        literature_id = self.add_record(
            "Synthetic PDF CLI target",
            pdf_path="originals/Synthetic 原著.PDF",
            verification_status="要確認",
            ai_summary_status="未確認",
            adoption_status="採用候補",
        )
        evidence_id = create_evidence_reference(
            self.connection,
            literature_id,
            pdf_page=5,
            printed_page="164",
            section="Results",
            subsection="Primary",
            table_label="Table 1",
            figure_label="Figure 2",
            verification="ai_unverified",
        )
        before = self.table_snapshot()
        calls = []

        def opener(connection, target, *, project_root):
            calls.append((connection, target, project_root))
            return cli_module.PdfOpenResult(True, target)

        _, _, outputs = self.run_with_actions(
            [
                "12",
                "10",
                str(literature_id),
                "1",
                "invalid",
                "1",
                "0",
                "0",
            ],
            pdf_opener=opener,
        )

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][0], self.connection)
        self.assertEqual(calls[0][1].evidence_id, evidence_id)
        self.assertEqual(calls[0][1].resolved_pdf_path, pdf_path.resolve())
        self.assertEqual(calls[0][2], self.directory)
        text = "\n".join(outputs)
        for expected in (
            "Navigation Preview:",
            "Literature title: Synthetic PDF CLI target",
            "PDF path: originals/Synthetic 原著.PDF",
            "Evidence verification: ai_unverified",
            "PDF page: 5",
            "Printed page: 164",
            "Section: Results",
            "Subsection: Primary",
            "Table: Table 1",
            "Figure: Figure 2",
            "原著PDFを開きました。",
            "確認位置:",
            "PreviewでPDF page 5へ移動:",
            "⌘⌥G → 5",
        ):
            self.assertIn(expected, text)
        self.assertIn(cli_module._INVALID_CONFIRMATION_MESSAGE, outputs)
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "ai_unverified",
        )
        literature = get_literature(self.connection, literature_id)
        self.assertEqual(literature.verification_status, "要確認")
        self.assertEqual(literature.ai_summary_status, "未確認")
        self.assertEqual(literature.adoption_status, "採用候補")

    def test_evidence_pdf_navigation_cancel_interrupt_and_zero_evidence_never_open(
        self,
    ) -> None:
        pdf_path = self.directory / "cancel.pdf"
        pdf_path.write_bytes(b"synthetic")
        literature_id = self.add_record(
            "Synthetic PDF cancel target", pdf_path=str(pdf_path)
        )
        create_evidence_reference(
            self.connection, literature_id, section="Methods"
        )
        empty_literature_id = self.add_record(
            "Synthetic empty PDF target", pdf_path=str(pdf_path)
        )
        calls = []

        def opener(connection, target, *, project_root):
            calls.append((connection, target, project_root))
            return cli_module.PdfOpenResult(True, target)

        _, _, cancel_outputs = self.run_with_actions(
            ["12", "10", str(literature_id), "1", "0", "0", "0"],
            pdf_opener=opener,
        )
        self.assertIn("原著PDFを開く操作を中止しました。", cancel_outputs)
        self.assertEqual(calls, [])

        _, _, empty_outputs = self.run_with_actions(
            ["12", "10", str(empty_literature_id), "0", "0"],
            pdf_opener=opener,
        )
        self.assertIn(
            "このLiteratureにはEvidenceが登録されていません。",
            empty_outputs,
        )
        self.assertEqual(calls, [])

        for interruption in (EOFError("PDF confirmation EOF"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                _, _, outputs = self.run_with_actions(
                    ["12", "10", str(literature_id), "1", interruption],
                    pdf_opener=opener,
                )
                self.assertIn(cli_module._EXIT_MESSAGE, outputs)
                self.assertEqual(calls, [])

    def test_evidence_pdf_navigation_path_and_open_failures_are_safe(self) -> None:
        missing_literature_id = self.add_record(
            "Synthetic missing PDF target", pdf_path="missing source.pdf"
        )
        create_evidence_reference(
            self.connection, missing_literature_id, pdf_page=3
        )
        blank_literature_id = self.add_record(
            "Synthetic blank PDF target", pdf_path="   "
        )
        create_evidence_reference(
            self.connection, blank_literature_id, section="Results"
        )
        calls = []

        def opener(connection, target, *, project_root):
            calls.append((connection, target, project_root))
            return cli_module.PdfOpenResult(True, target)

        _, _, missing_outputs = self.run_with_actions(
            ["12", "10", str(missing_literature_id), "1", "0", "0"],
            pdf_opener=opener,
        )
        missing_text = "\n".join(missing_outputs)
        self.assertIn("PDF path: missing source.pdf", missing_text)
        self.assertIn("PDFファイルが見つかりません。", missing_text)
        self.assertNotIn(cli_module._EVIDENCE_PDF_OPEN_CONFIRMATION_MENU, missing_outputs)

        _, _, blank_outputs = self.run_with_actions(
            ["12", "10", str(blank_literature_id), "1", "0", "0"],
            pdf_opener=opener,
        )
        self.assertIn("pdf_path未登録", "\n".join(blank_outputs))
        self.assertIn("文献編集", "\n".join(blank_outputs))
        self.assertEqual(calls, [])

        valid_path = self.directory / "failure.pdf"
        valid_path.write_bytes(b"synthetic")
        failure_literature_id = self.add_record(
            "Synthetic open failure", pdf_path=str(valid_path)
        )
        create_evidence_reference(
            self.connection, failure_literature_id, printed_page="S8"
        )

        def failing_opener(connection, target, *, project_root):
            calls.append((connection, target, project_root))
            return cli_module.PdfOpenResult(
                False, target, cli_module.PDF_OPEN_FAILURE_MESSAGE
            )

        _, _, failure_outputs = self.run_with_actions(
            ["12", "10", str(failure_literature_id), "1", "1", "0", "0"],
            pdf_opener=failing_opener,
        )
        self.assertEqual(len(calls), 1)
        self.assertIn(cli_module.PDF_OPEN_FAILURE_MESSAGE, failure_outputs)
        self.assertNotIn("原著PDFを開きました。", failure_outputs)
        self.assertNotIn("⌘⌥G", "\n".join(failure_outputs))

    def test_evidence_pdf_navigation_without_pdf_page_has_no_jump_guidance(
        self,
    ) -> None:
        pdf_path = self.directory / "no-page.pdf"
        pdf_path.write_bytes(b"synthetic")
        literature_id = self.add_record(
            "Synthetic no-page target", pdf_path=str(pdf_path)
        )
        create_evidence_reference(
            self.connection,
            literature_id,
            printed_page="S12",
            section="Discussion",
        )

        def opener(connection, target, *, project_root):
            return cli_module.PdfOpenResult(True, target)

        _, _, outputs = self.run_with_actions(
            ["12", "10", str(literature_id), "1", "1", "0", "0"],
            pdf_opener=opener,
        )
        text = "\n".join(outputs)
        self.assertIn("PDF page: 未登録", text)
        self.assertIn("Printed page: S12", text)
        self.assertNotIn("⌘⌥G", text)

    def test_comparison_submenu_navigation_invalid_input_and_interrupts(self) -> None:
        _, feeder, outputs = self.run_with_actions(
            ["13", "invalid", "0", "0"]
        )

        for option in (
            "1. 直前の検索結果から選択",
            "2. Literature IDを指定",
            "0. メインメニューへ戻る",
        ):
            self.assertIn(option, cli_module._COMPARISON_MENU)
        self.assertEqual(
            outputs.count(cli_module._INVALID_COMPARISON_MENU_MESSAGE), 1
        )
        self.assertEqual(feeder.prompts.count("選択してください: "), 4)
        self.assertEqual(outputs.count(cli_module._EXIT_MESSAGE), 1)

        for interruption in (EOFError("comparison EOF"), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                _, _, interrupted_outputs = self.run_with_actions(
                    ["13", interruption]
                )
                self.assertEqual(
                    interrupted_outputs.count(cli_module._EXIT_MESSAGE), 1
                )

    def test_comparison_no_previous_search_and_manual_validation_errors(self) -> None:
        first_id = self.add_record("Synthetic comparison validation A")
        actions = [
            "13",
            "1",
            "2",
            "",
            "2",
            str(first_id),
            "2",
            f"{first_id},{first_id}",
            "2",
            f"{first_id},999999",
            "2",
            f"{first_id},１",
            "0",
            "0",
        ]

        _, _, outputs = self.run_with_actions(actions)
        text = "\n".join(outputs)

        self.assertIn(
            cli_module._MISSING_COMPARISON_SEARCH_RESULTS_MESSAGE, outputs
        )
        self.assertGreaterEqual(text.count("比較エラー:"), 5)
        self.assertIn("2件以上", text)
        self.assertIn("重複", text)
        self.assertIn("存在しません", text)
        self.assertIn("ASCII数字", text)
        self.assertNotIn("比較文献\n", text)

    def test_comparison_reuses_last_search_for_subset_and_all_in_order(self) -> None:
        first_id = self.add_record("Synthetic search comparison A")
        second_id = self.add_record("Synthetic search comparison B")
        third_id = self.add_record("Synthetic search comparison C")
        actions = [
            "2",
            *([""] * len(_SEARCH_FIELDS)),
            "13",
            "1",
            "4,1",
            "1",
            "3,1",
            "1",
            "all",
            "0",
            "0",
        ]

        with patch.object(
            cli_module,
            "build_comparison_matrix",
            wraps=cli_module.build_comparison_matrix,
        ) as built:
            _, _, outputs = self.run_with_actions(actions)

        self.assertEqual(
            [call.args[1] for call in built.call_args_list],
            [(third_id, first_id), (first_id, second_id, third_id)],
        )
        text = "\n".join(outputs)
        for number, title in enumerate(
            (
                "Synthetic search comparison A",
                "Synthetic search comparison B",
                "Synthetic search comparison C",
            ),
            start=1,
        ):
            self.assertIn(f"選択番号: {number}\nTitle: {title}\nYear:", text)
        self.assertEqual(text.count("比較文献"), 2)
        self.assertIn("表示範囲外", text)

    def test_comparison_manual_output_is_row_first_and_preserves_metadata(self) -> None:
        first_id = self.add_record(
            "Synthetic manual comparison A", publication_year=2024
        )
        second_id = self.add_record(
            "Synthetic manual comparison B", publication_year=2025
        )
        first_entity = create_structured_entity(
            self.connection, first_id, "method_body_condition", sort_order=2
        )
        first_field = create_structured_field(
            self.connection,
            first_entity,
            "load",
            content_role="source_fact",
            value="90 N",
            availability="reported",
            verification="ai_unverified",
        )
        earlier_entity = create_structured_entity(
            self.connection, first_id, "method_body_condition", sort_order=1
        )
        create_structured_field(
            self.connection,
            earlier_entity,
            "load",
            content_role="source_fact",
            value="9.2 kg",
            availability="reported",
            verification="user_verified",
        )
        second_entity = create_structured_entity(
            self.connection, second_id, "method_body_condition"
        )
        create_structured_field(
            self.connection,
            second_entity,
            "load",
            content_role="source_fact",
            value=None,
            availability="not_reported",
        )
        evidence_id = create_evidence_reference(
            self.connection,
            first_id,
            section="Synthetic Methods",
            verification="user_verified",
        )
        attach_evidence_to_field(self.connection, first_field, evidence_id)

        before = self.structured_row_counts(self.connection)
        _, _, outputs = self.run_with_actions(
            ["13", "2", f"{second_id},{first_id}", "0", "0"]
        )
        text = "\n".join(outputs)

        self.assertLess(
            text.index("[1] Synthetic manual comparison B (2025)"),
            text.index("[2] Synthetic manual comparison A (2024)"),
        )
        self.assertIn("Methods / Body Condition\n\nload", text)
        self.assertIn("[1] not_reported", text)
        self.assertIn("[2] ① 9.2 kg", text)
        self.assertIn("② 90 N", text)
        self.assertIn("field verification: ai_unverified", text)
        self.assertIn("Evidence: 1", text)
        self.assertIn("user_verified Evidence: 1/1", text)
        self.assertEqual(self.structured_row_counts(self.connection), before)

    def test_comparison_cli_outcomes_stay_as_separate_logical_units(self) -> None:
        first_id = self.add_record("Synthetic Outcome comparison A")
        second_id = self.add_record("Synthetic Outcome comparison B")
        for literature_id, contexts in (
            (first_id, ("Context A", "Context B")),
            (second_id, ("Context C",)),
        ):
            for sort_order, context in enumerate(contexts):
                outcome_id = create_structured_entity(
                    self.connection,
                    literature_id,
                    "outcome",
                    sort_order=sort_order,
                )
                for key, value in (
                    ("name", "Same stored Outcome"),
                    ("definition", f"Definition {context}"),
                    ("calculation_method", f"Calculation {context}"),
                    ("unit", "%"),
                    ("context_condition", context),
                ):
                    create_structured_field(
                        self.connection,
                        outcome_id,
                        key,
                        content_role="source_fact",
                        value=value,
                        availability="reported",
                    )
                if context == "Context A":
                    result_id = create_structured_entity(
                        self.connection,
                        literature_id,
                        "result",
                        parent_entity_id=outcome_id,
                    )
                    for key, value in (
                        ("condition_or_comparison", "Stored condition"),
                        ("result", 3.2),
                        ("statistics", {"sem": 0.2}),
                    ):
                        create_structured_field(
                            self.connection,
                            result_id,
                            key,
                            content_role="source_fact",
                            value=value,
                            availability="reported",
                        )

        _, _, outputs = self.run_with_actions(
            ["13", "2", f"{first_id},{second_id}", "0", "0"]
        )
        text = "\n".join(outputs)

        self.assertEqual(text.count("name: Same stored Outcome"), 3)
        self.assertIn("Outcome 1", text)
        self.assertIn("Outcome 2", text)
        self.assertIn("definition: Definition Context A", text)
        self.assertIn("calculation_method: Calculation Context B", text)
        self.assertIn("context_condition: Context C", text)
        self.assertIn("condition_or_comparison: Stored condition", text)
        self.assertIn("result: 3.2", text)
        self.assertIn('statistics: {"sem": 0.2}', text)
        for forbidden in (
            "directly comparable",
            "partially comparable",
            "not directly comparable",
            "needs review",
        ):
            self.assertNotIn(forbidden, text.lower())

    def test_comparison_cli_preserves_active_transaction_and_search_state(self) -> None:
        first_id = self.add_record("Synthetic transaction comparison A")
        second_id = self.add_record("Synthetic transaction comparison B")
        marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)", ("pending-comparison-cli",)
        )
        before = self.table_snapshot()

        _, _, outputs = self.run_with_actions(
            ["13", "2", f"{second_id},{first_id}", "0", "0"]
        )

        self.assertIn("比較文献", "\n".join(outputs))
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.table_snapshot(), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT name FROM tags WHERE id = ?", (marker.lastrowid,)
            ).fetchone()[0],
            "pending-comparison-cli",
        )
        self.connection.rollback()

    def test_cli_creates_no_database_export_or_backup_artifacts(self) -> None:
        self.populate_search_records()
        names_before = {path.name for path in self.directory.iterdir()}

        self.run_with_actions(
            ["1", *self.search_actions(keyword="CLI検索対象")]
        )

        self.assertEqual(
            {path.name for path in self.directory.iterdir()},
            names_before,
        )
        self.assertFalse(any(path.suffix == ".csv" for path in self.directory.iterdir()))
        self.assertFalse(
            any(
                "backup" in path.name.lower()
                for path in self.directory.iterdir()
            )
        )


if __name__ == "__main__":
    unittest.main()
