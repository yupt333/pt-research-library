"""Tests for the Step 8 interactive CLI."""

import sqlite3
import tempfile
import unittest
from datetime import date
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
    get_literature,
    list_literature,
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
        self.assertIn("0. 終了", outputs[0])
        self.assertNotIn("文献編集", outputs[0])
        self.assertNotIn("文献削除", outputs[0])
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

        error_message = "入力エラー: 0、1、2、3のいずれかを選択してください。"
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
        self.assertIn(cli_module._ACTIVE_TRANSACTION_MESSAGE, outputs)
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
        self.assertIn(cli_module._ACTIVE_TRANSACTION_MESSAGE, outputs)
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
        self.assertIn(cli_module._ACTIVE_TRANSACTION_MESSAGE, outputs)
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
