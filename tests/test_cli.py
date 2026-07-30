"""Tests for the Step 8 interactive CLI."""

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
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
    delete_literature,
    get_literature,
    get_literature_related_counts,
    list_literature,
    update_literature,
)
from src.search import search_literature


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
    ) -> tuple[object, InputFeeder, list[str]]:
        feeder = InputFeeder(actions)
        outputs: list[str] = []
        result = run_cli(
            self.connection if connection is None else connection,
            input_func=feeder,
            output_func=outputs.append,
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

    def add_record(self, title: str, **values: object) -> int:
        return add_literature(
            self.connection,
            Literature(title=title, **values),
        )

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

    def schema_snapshot(self) -> list[tuple[object, ...]]:
        return [
            tuple(row)
            for row in self.connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                ORDER BY type, name
                """
            ).fetchall()
        ]

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
        actions = ["", "invalid", *(["9"] * invalid_count), "0"]

        _, feeder, outputs = self.run_with_actions(actions)

        error_message = (
            "入力エラー: 0、1、2、3、4、5のいずれかを選択してください。"
        )
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
                item.startswith("登録エラー: ")
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
                        item.startswith("登録エラー: ")
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
                        item.startswith("登録エラー: ")
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
                        item.startswith("登録エラー: ")
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
                            item.startswith("登録エラー: ")
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
                        item.startswith("登録エラー: ")
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
            any(item.startswith("登録エラー: ") for item in outputs)
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
                        item.startswith("登録エラー: ")
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
                                item.startswith("登録エラー: ")
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
                                item.startswith("登録エラー: ")
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
            any(item.startswith("登録エラー: ") for item in outputs)
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
            any(item.startswith("登録エラー: ") for item in outputs)
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
        self.assertIn(cli_module._INVALID_MENU_MESSAGE, outputs)
        for choice in ("0", "1", "2", "3", "4", "5"):
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
                        item.startswith("更新エラー: ")
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
                    any(item.startswith("更新エラー: ") for item in outputs)
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
                    item.startswith("更新エラー: ")
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
                item.startswith("更新エラー: ")
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
                                item.startswith("更新エラー: ")
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
                        failing_message = f"更新エラー: {update_error}"
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
                                item.startswith("更新エラー: ")
                                for item in outputs
                            ),
                            1,
                        )
                    else:
                        self.assertFalse(
                            any(
                                item.startswith("更新エラー: ")
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
                            item.startswith("更新エラー: ")
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
                                item.startswith("更新エラー: ")
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
            any(item.startswith("更新エラー: ") for item in outputs)
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
