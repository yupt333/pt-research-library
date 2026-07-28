"""Tests for the Step 8A read-only interactive CLI."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.cli as cli_module
from src.cli import run_cli
from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
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

        error_message = "入力エラー: 0、1、2のいずれかを選択してください。"
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
        literature_id = self.add_record(
            title,
            authors=authors,
            journal=journal,
            publication_year=2025,
            doi=" DOI:10.1000/Mixed Case ",
            pmid=" PMID: 001 23 ",
            verification_status="要確認",
            adoption_status="採用候補",
            rating=4,
        )

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
