"""Tests for read-only atomic literature CSV export."""

import codecs
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import src.csv_export as csv_export_module
from src.csv_export import export_literature_csv
from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
)
from src.search import search_literature


EXPECTED_LITERATURE_COLUMNS = (
    "id",
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
    "created_at",
    "updated_at",
)
EXPECTED_CSV_COLUMNS = EXPECTED_LITERATURE_COLUMNS + ("tags",)


class CsvExportTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.database_path = self.directory / "csv-export.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)
        self.output_path = self.directory / "literature.csv"

    def add_record(self, title: str, **values: object) -> int:
        return add_literature(
            self.connection,
            Literature(title=title, **values),
        )

    def attach_tags(self, literature_id: int, *names: str) -> list[int]:
        tag_ids = []
        for name in names:
            tag_id = create_tag(self.connection, name)
            attach_tag_to_literature(
                self.connection,
                literature_id,
                tag_id,
            )
            tag_ids.append(tag_id)
        return tag_ids

    def read_csv_rows(self, path: Optional[Path] = None) -> list[list[str]]:
        csv_path = self.output_path if path is None else path
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            return list(csv.reader(file))

    def read_csv_dicts(
        self,
        path: Optional[Path] = None,
    ) -> list[dict[str, str]]:
        csv_path = self.output_path if path is None else path
        with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def database_snapshot(self) -> dict[str, list[tuple[object, ...]]]:
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

    def temporary_artifacts(
        self,
        output_path: Optional[Path] = None,
    ) -> set[str]:
        csv_path = self.output_path if output_path is None else output_path
        return {
            path.name
            for path in self.directory.iterdir()
            if path.name.startswith(f".{csv_path.name}.")
        }

    def test_empty_database_writes_only_complete_header_and_returns_zero(
        self,
    ) -> None:
        row_count = export_literature_csv(
            self.connection,
            self.output_path,
        )

        schema_columns = tuple(
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(literature)"
            ).fetchall()
        )
        rows = self.read_csv_rows()
        self.assertEqual(row_count, 0)
        self.assertEqual(schema_columns, EXPECTED_LITERATURE_COLUMNS)
        self.assertEqual(rows, [list(EXPECTED_CSV_COLUMNS)])
        self.assertEqual(len(rows[0]), len(EXPECTED_CSV_COLUMNS))

    def test_all_literature_columns_values_nulls_and_order_are_preserved(
        self,
    ) -> None:
        populated_id = self.add_record(
            "Populated title",
            authors="Author A; Author B",
            journal="Journal",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="101-112",
            doi="10.1000/stored",
            pmid="00123456",
            url="https://example.test/article",
            language="ja",
            publication_type="Original",
            abstract="Abstract",
            pdf_path="/local/path/paper.pdf",
            personal_summary="Personal summary",
            ai_summary="AI summary",
            ai_summary_status="未確認",
            general_note="General note",
            key_findings="Key findings",
            methods_note="Methods note",
            clinical_note="Clinical note",
            limitation_note="Limitation note",
            relevance_note="Relevance note",
            evidence_level="Level II",
            verification_status="一部確認",
            adoption_status="採用候補",
            exclusion_reason="Reason",
            rating=5,
        )
        null_id = self.add_record("Null optional fields")
        raw_rows = self.connection.execute(
            "SELECT * FROM literature ORDER BY id ASC"
        ).fetchall()

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
        )

        rows = self.read_csv_rows()
        self.assertEqual(row_count, 2)
        self.assertEqual(rows[0], list(EXPECTED_CSV_COLUMNS))
        self.assertEqual(
            [int(row[0]) for row in rows[1:]],
            [populated_id, null_id],
        )
        for csv_row, database_row in zip(rows[1:], raw_rows):
            expected = [
                "" if value is None else str(value)
                for value in tuple(database_row)
            ]
            self.assertEqual(csv_row, expected + [""])
            self.assertEqual(len(csv_row), len(rows[0]))
        self.assertEqual(rows[2][2], "")
        self.assertEqual(rows[2][4], "")
        self.assertEqual(rows[1][4], "2025")
        self.assertEqual(rows[1][28], "5")

    def test_multiple_records_are_one_row_each_in_ascending_id_order(
        self,
    ) -> None:
        ids = [
            self.add_record("First"),
            self.add_record("Second"),
            self.add_record("Third"),
        ]

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
        )

        rows = self.read_csv_dicts()
        self.assertEqual(row_count, 3)
        self.assertEqual([int(row["id"]) for row in rows], ids)
        self.assertEqual([row["title"] for row in rows], ["First", "Second", "Third"])

    def test_utf8_bom_and_japanese_text_are_preserved(self) -> None:
        literature_id = self.add_record(
            "肩関節の超音波評価",
            authors="山田 太郎、佐藤 花子",
            abstract="日本語の抄録",
        )
        self.attach_tags(literature_id, "棘上筋")

        export_literature_csv(self.connection, self.output_path)

        contents = self.output_path.read_bytes()
        self.assertTrue(contents.startswith(codecs.BOM_UTF8))
        self.assertFalse(contents.startswith(codecs.BOM_UTF8 * 2))
        with self.output_path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as file:
            header = next(csv.reader(file))
        row = self.read_csv_dicts()[0]
        self.assertEqual(header[0], "id")
        self.assertNotIn("\ufeff", header[0])
        self.assertEqual(row["title"], "肩関節の超音波評価")
        self.assertEqual(row["authors"], "山田 太郎、佐藤 花子")
        self.assertEqual(row["abstract"], "日本語の抄録")
        self.assertEqual(row["tags"], "棘上筋")

    def test_csv_quoting_and_formula_like_strings_preserve_saved_values(
        self,
    ) -> None:
        values = {
            "title": '=日本語, "quoted"\nnext\r\nlast',
            "authors": "+Author with trailing space ",
            "journal": "-Journal",
            "volume": "@Volume",
            "issue": ' "issue" ',
            "pages": "1,2;3",
            "url": "https://example.test/path?a=1,b=2",
            "abstract": "line one\nline two\r\nline three",
            "pdf_path": "/Local Folder/paper;one.pdf",
            "personal_summary": " 前後空白 ",
        }
        literature_id = self.add_record(**values)
        self.attach_tags(
            literature_id,
            'tag,comma',
            'tag"quote',
            "semi;colon",
            "日本語",
        )

        export_literature_csv(self.connection, self.output_path)

        row = self.read_csv_dicts()[0]
        for column, expected in values.items():
            with self.subTest(column=column):
                self.assertEqual(row[column], expected)
        self.assertFalse(row["title"].startswith("'"))
        self.assertFalse(row["authors"].startswith("'"))
        self.assertFalse(row["journal"].startswith("'"))
        self.assertFalse(row["volume"].startswith("'"))
        self.assertEqual(
            row["tags"],
            'semi;colon;tag"quote;tag,comma;日本語',
        )

    def test_tags_are_sorted_joined_isolated_and_empty_when_unattached(
        self,
    ) -> None:
        first_id = self.add_record("First tags")
        second_id = self.add_record("Second tags")
        third_id = self.add_record("No tags")
        self.attach_tags(first_id, "gamma", "Beta", "alpha", "日本語")
        self.attach_tags(second_id, "other")
        duplicate_tag_id = create_tag(self.connection, "duplicate")
        self.assertTrue(
            attach_tag_to_literature(
                self.connection,
                first_id,
                duplicate_tag_id,
            )
        )
        self.assertFalse(
            attach_tag_to_literature(
                self.connection,
                first_id,
                duplicate_tag_id,
            )
        )

        export_literature_csv(self.connection, self.output_path)

        rows = {
            int(row["id"]): row
            for row in self.read_csv_dicts()
        }
        self.assertEqual(
            rows[first_id]["tags"],
            "alpha;Beta;duplicate;gamma;日本語",
        )
        self.assertEqual(rows[second_id]["tags"], "other")
        self.assertEqual(rows[third_id]["tags"], "")
        self.assertNotIn("other", rows[first_id]["tags"])

    def test_usage_history_is_excluded_and_does_not_duplicate_literature(
        self,
    ) -> None:
        literature_id = self.add_record("Usage exclusion")
        create_usage_history(
            self.connection,
            literature_id,
            "usage-marker",
            project_name="project-marker",
            usage_note="note-marker",
            used_at="2026-07-27",
        )
        create_usage_history(
            self.connection,
            literature_id,
            "second-usage-marker",
        )

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
        )

        rows = self.read_csv_rows()
        self.assertEqual(row_count, 1)
        self.assertEqual(len(rows), 2)
        for forbidden in (
            "usage_type",
            "project_name",
            "usage_note",
            "used_at",
        ):
            self.assertNotIn(forbidden, rows[0])
        flattened = "\n".join(value for row in rows for value in row)
        for marker in ("usage-marker", "project-marker", "note-marker"):
            self.assertNotIn(marker, flattened)

    def test_none_exports_all_and_empty_sequence_exports_header_only(
        self,
    ) -> None:
        ids = [self.add_record("First"), self.add_record("Second")]

        self.assertEqual(
            export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=None,
            ),
            2,
        )
        self.assertEqual(
            [int(row["id"]) for row in self.read_csv_dicts()],
            ids,
        )

        self.assertEqual(
            export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=[],
            ),
            0,
        )
        self.assertEqual(
            self.read_csv_rows(),
            [list(EXPECTED_CSV_COLUMNS)],
        )

    def test_selected_ids_are_deduplicated_and_sorted(self) -> None:
        first_id = self.add_record("First")
        second_id = self.add_record("Second")
        third_id = self.add_record("Third")
        self.attach_tags(first_id, "first-tag")
        self.attach_tags(third_id, "third-tag")

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
            literature_ids=[third_id, first_id, third_id],
        )

        rows = self.read_csv_dicts()
        self.assertEqual(row_count, 2)
        self.assertEqual(
            [int(row["id"]) for row in rows],
            [first_id, third_id],
        )
        self.assertEqual(
            [row["tags"] for row in rows],
            ["first-tag", "third-tag"],
        )
        self.assertNotIn(second_id, [int(row["id"]) for row in rows])

    def test_tuple_and_range_id_sequences_are_supported(self) -> None:
        ids = [
            self.add_record("First"),
            self.add_record("Second"),
            self.add_record("Third"),
        ]

        self.assertEqual(
            export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=(ids[2],),
            ),
            1,
        )
        self.assertEqual(
            [int(row["id"]) for row in self.read_csv_dicts()],
            [ids[2]],
        )

        self.assertEqual(
            export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=range(ids[0], ids[-1] + 1),
            ),
            3,
        )
        self.assertEqual(
            [int(row["id"]) for row in self.read_csv_dicts()],
            ids,
        )

    def test_invalid_id_container_types_are_rejected(self) -> None:
        self.output_path.write_text("kept", encoding="utf-8")
        invalid_values = (
            "1",
            b"1",
            bytearray(b"1"),
            memoryview(b"1"),
            1,
            {1},
            iter([1]),
            {"id": 1},
        )

        for value in invalid_values:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "literature_ids"):
                    export_literature_csv(
                        self.connection,
                        self.output_path,
                        literature_ids=value,
                    )
                self.assertEqual(
                    self.output_path.read_text(encoding="utf-8"),
                    "kept",
                )

    def test_invalid_id_values_are_rejected_and_preserve_existing_file(
        self,
    ) -> None:
        self.output_path.write_text("kept", encoding="utf-8")

        for value in (0, -1, 1.5, "1", True, False, None):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "正の整数"):
                    export_literature_csv(
                        self.connection,
                        self.output_path,
                        literature_ids=[value],
                    )
                self.assertEqual(
                    self.output_path.read_text(encoding="utf-8"),
                    "kept",
                )

    def test_sqlite_integer_max_binds_safely_then_reports_missing_id(
        self,
    ) -> None:
        sqlite_integer_max = 2**63 - 1
        before = self.database_snapshot()
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            with self.assertRaisesRegex(ValueError, "存在しない文献ID"):
                export_literature_csv(
                    self.connection,
                    self.output_path,
                    literature_ids=[sqlite_integer_max],
                )
        finally:
            self.connection.set_trace_callback(None)

        selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(selects), 1)
        self.assertIn("FROM LITERATURE", selects[0].upper())
        self.assertNotIn("FROM LITERATURE_TAGS", selects[0].upper())
        self.assertEqual(self.database_snapshot(), before)
        self.assertFalse(self.output_path.exists())
        self.assertEqual(self.temporary_artifacts(), set())
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_out_of_range_ids_fail_before_sql_and_preserve_all_state(
        self,
    ) -> None:
        existing_id = self.add_record("Existing")
        self.attach_tags(existing_id, "kept-tag")
        create_usage_history(
            self.connection,
            existing_id,
            "kept-usage",
        )
        pending_cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending",),
        )
        pending_id = pending_cursor.lastrowid
        before = self.database_snapshot()
        original = b"existing csv bytes"
        self.output_path.write_bytes(original)
        self.assertTrue(self.connection.in_transaction)

        invalid_id_sequences = (
            [2**63],
            [10**100],
            [existing_id, 2**63],
        )
        for literature_ids in invalid_id_sequences:
            with self.subTest(literature_ids=literature_ids):
                statements: list[str] = []
                self.connection.set_trace_callback(statements.append)
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "1以上9223372036854775807以下",
                    ):
                        export_literature_csv(
                            self.connection,
                            self.output_path,
                            literature_ids=literature_ids,
                        )
                finally:
                    self.connection.set_trace_callback(None)

                self.assertEqual(statements, [])
                self.assertEqual(self.output_path.read_bytes(), original)
                self.assertEqual(self.temporary_artifacts(), set())
                self.assertEqual(self.database_snapshot(), before)
                self.assertTrue(self.connection.in_transaction)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT title FROM literature WHERE id = ?",
                        (pending_id,),
                    ).fetchone()[0],
                    "Pending",
                )

        self.connection.rollback()

    def test_out_of_range_id_leaves_no_new_output_or_temporary_file(
        self,
    ) -> None:
        new_output_path = self.directory / "new-output.csv"

        with self.assertRaises(ValueError) as raised:
            export_literature_csv(
                self.connection,
                new_output_path,
                literature_ids=[2**63],
            )

        self.assertNotIsInstance(raised.exception, OverflowError)
        self.assertFalse(new_output_path.exists())
        self.assertEqual(
            self.temporary_artifacts(new_output_path),
            set(),
        )

    def test_unknown_or_partially_unknown_ids_preserve_existing_file(
        self,
    ) -> None:
        existing_id = self.add_record("Existing")
        self.output_path.write_text("kept", encoding="utf-8")

        for ids in ([999999], [existing_id, 999999]):
            with self.subTest(ids=ids):
                with self.assertRaisesRegex(ValueError, "存在しない文献ID"):
                    export_literature_csv(
                        self.connection,
                        self.output_path,
                        literature_ids=ids,
                    )
                self.assertEqual(
                    self.output_path.read_text(encoding="utf-8"),
                    "kept",
                )
                self.assertEqual(self.temporary_artifacts(), set())

    def test_literature_ids_is_keyword_only(self) -> None:
        with self.assertRaises(TypeError):
            export_literature_csv(
                self.connection,
                self.output_path,
                [],
            )

    def test_search_results_can_be_exported_with_tags(self) -> None:
        matching_id = self.add_record(
            "Matching",
            publication_year=2025,
        )
        self.add_record("Not matching", publication_year=2024)
        self.attach_tags(matching_id, "AHD")
        results = search_literature(self.connection, year=2025, tag="AHD")

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
            literature_ids=[item.id for item in results],
        )

        rows = self.read_csv_dicts()
        self.assertEqual(row_count, 1)
        self.assertEqual([int(row["id"]) for row in rows], [matching_id])
        self.assertEqual(rows[0]["tags"], "AHD")

    def test_str_path_path_object_and_unicode_space_filename_are_supported(
        self,
    ) -> None:
        self.add_record("Path test")
        string_path = str(self.directory / "string.csv")
        unicode_path = self.directory / "日本語 file.csv"

        self.assertEqual(
            export_literature_csv(self.connection, string_path),
            1,
        )
        self.assertTrue(Path(string_path).is_file())
        self.assertEqual(
            export_literature_csv(self.connection, unicode_path),
            1,
        )
        self.assertTrue(unicode_path.is_file())

    def test_invalid_empty_and_directory_output_paths_are_rejected(self) -> None:
        for value in (None, 1, b"file.csv", "", "  ", "\t\n"):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "output_path"):
                    export_literature_csv(self.connection, value)

        with self.assertRaisesRegex(ValueError, "既存ディレクトリ"):
            export_literature_csv(self.connection, self.directory)

    def test_missing_or_non_directory_parent_raises_filesystem_error(
        self,
    ) -> None:
        missing_path = self.directory / "missing" / "export.csv"
        non_directory_parent = self.directory / "parent-file"
        non_directory_parent.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(FileNotFoundError):
            export_literature_csv(self.connection, missing_path)
        with self.assertRaises(NotADirectoryError):
            export_literature_csv(
                self.connection,
                non_directory_parent / "export.csv",
            )
        self.assertFalse(missing_path.exists())

    def test_existing_file_is_replaced_only_after_success(self) -> None:
        literature_id = self.add_record("Replacement")
        self.output_path.write_text("old contents", encoding="utf-8")

        row_count = export_literature_csv(
            self.connection,
            self.output_path,
        )

        self.assertEqual(row_count, 1)
        self.assertNotEqual(
            self.output_path.read_text(encoding="utf-8-sig"),
            "old contents",
        )
        self.assertEqual(
            int(self.read_csv_dicts()[0]["id"]),
            literature_id,
        )
        self.assertEqual(self.temporary_artifacts(), set())

    def test_csv_write_failure_preserves_existing_file_and_removes_temporary(
        self,
    ) -> None:
        self.add_record("Write failure")
        original = b"original bytes"
        self.output_path.write_bytes(original)
        before_names = {path.name for path in self.directory.iterdir()}
        failing_writer = unittest.mock.Mock()
        failing_writer.writerow.side_effect = [
            None,
            OSError("forced write failure"),
        ]

        with patch.object(
            csv_export_module.csv,
            "writer",
            return_value=failing_writer,
        ):
            with self.assertRaisesRegex(OSError, "forced write failure"):
                export_literature_csv(
                    self.connection,
                    self.output_path,
                )

        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertEqual(
            {path.name for path in self.directory.iterdir()},
            before_names,
        )
        self.assertEqual(self.temporary_artifacts(), set())

    def test_replace_failure_preserves_existing_file_and_removes_temporary(
        self,
    ) -> None:
        self.add_record("Replace failure")
        original = b"original bytes"
        self.output_path.write_bytes(original)
        before_names = {path.name for path in self.directory.iterdir()}

        with patch.object(
            csv_export_module.os,
            "replace",
            side_effect=OSError("forced replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "forced replace failure"):
                export_literature_csv(
                    self.connection,
                    self.output_path,
                )

        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertEqual(
            {path.name for path in self.directory.iterdir()},
            before_names,
        )
        self.assertEqual(self.temporary_artifacts(), set())

    def test_export_is_read_only_and_preserves_pending_transaction(
        self,
    ) -> None:
        committed_id = self.add_record("Committed")
        pending_cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending",),
        )
        pending_id = pending_cursor.lastrowid
        tag_cursor = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-tag",),
        )
        self.connection.execute(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (pending_id, tag_cursor.lastrowid),
        )
        self.connection.execute(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (pending_id, "pending-use"),
        )
        before = self.database_snapshot()
        self.assertTrue(self.connection.in_transaction)
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            row_count = export_literature_csv(
                self.connection,
                self.output_path,
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(row_count, 2)
        self.assertEqual(
            [int(row["id"]) for row in self.read_csv_dicts()],
            [committed_id, pending_id],
        )
        self.assertEqual(self.database_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        observer = connect_database(self.database_path)
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
        forbidden_prefixes = (
            "INSERT",
            "UPDATE",
            "DELETE",
            "COMMIT",
            "ROLLBACK",
            "ALTER",
            "DROP",
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(forbidden_prefixes)
                for statement in statements
            )
        )
        self.connection.rollback()

    def test_export_uses_at_most_one_literature_and_one_tag_select(
        self,
    ) -> None:
        literature_id = self.add_record("Query count")
        self.attach_tags(literature_id, "query-tag")
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            export_literature_csv(self.connection, self.output_path)
        finally:
            self.connection.set_trace_callback(None)

        selects = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(selects), 2)
        self.assertEqual(
            sum("FROM LITERATURE\n" in statement.upper() for statement in selects),
            1,
        )
        self.assertEqual(
            sum("FROM LITERATURE_TAGS" in statement.upper() for statement in selects),
            1,
        )

    def test_empty_selection_executes_no_database_query_or_tag_select(
        self,
    ) -> None:
        self.add_record("Not selected")
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            row_count = export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=[],
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(row_count, 0)
        self.assertEqual(statements, [])

    @unittest.skipUnless(
        hasattr(sqlite3.Connection, "setlimit"),
        "sqlite3.Connection.setlimit is unavailable",
    )
    def test_sqlite_variable_limit_errors_preserve_files_and_transaction(
        self,
    ) -> None:
        first_id = self.add_record("First")
        second_id = self.add_record("Second")
        self.attach_tags(first_id, "kept-tag")
        create_usage_history(
            self.connection,
            first_id,
            "kept-usage",
        )
        pending_cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending",),
        )
        pending_id = pending_cursor.lastrowid
        before = self.database_snapshot()
        original = b"existing csv bytes"
        self.output_path.write_bytes(original)
        new_output_path = self.directory / "new-limit-output.csv"
        original_limit = self.connection.getlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER
        )

        self.connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 1)
        try:
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "too many SQL variables",
            ):
                export_literature_csv(
                    self.connection,
                    self.output_path,
                    literature_ids=[first_id, second_id],
                )
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "too many SQL variables",
            ):
                export_literature_csv(
                    self.connection,
                    self.output_path,
                )
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "too many SQL variables",
            ):
                export_literature_csv(
                    self.connection,
                    new_output_path,
                    literature_ids=[first_id, second_id],
                )
        finally:
            self.connection.setlimit(
                sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,
                original_limit,
            )

        self.assertEqual(self.output_path.read_bytes(), original)
        self.assertFalse(new_output_path.exists())
        self.assertEqual(self.temporary_artifacts(), set())
        self.assertEqual(
            self.temporary_artifacts(new_output_path),
            set(),
        )
        self.assertEqual(self.database_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT title FROM literature WHERE id = ?",
                (pending_id,),
            ).fetchone()[0],
            "Pending",
        )
        self.connection.rollback()

    def test_success_and_failure_do_not_leave_temporary_files(self) -> None:
        self.add_record("No temporary file")

        export_literature_csv(self.connection, self.output_path)
        self.assertEqual(self.temporary_artifacts(), set())

        with self.assertRaises(ValueError):
            export_literature_csv(
                self.connection,
                self.output_path,
                literature_ids=[999999],
            )
        self.assertEqual(self.temporary_artifacts(), set())


if __name__ == "__main__":
    unittest.main()
