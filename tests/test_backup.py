"""Tests for safe, verified SQLite database backups."""

import errno
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import src.backup as backup_module
from src.backup import create_database_backup
from src.database import SCHEMA_SQL, connect_database, initialize_database
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
)


_TABLE_ORDER = {
    "literature": "id",
    "tags": "id",
    "literature_tags": "literature_id, tag_id",
    "usage_history": "id",
}
_FILENAME_PATTERN = re.compile(
    r"^pt_research_library_backup_"
    r"\d{8}T\d{12}Z(?:_\d+)?\.sqlite3$"
)


class StringPathLike:
    """A test PathLike that returns a string."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __fspath__(self) -> str:
        return self.value


class BytesPathLike:
    """A test PathLike that returns bytes."""

    def __init__(self, value: bytes) -> None:
        self.value = value

    def __fspath__(self) -> bytes:
        return self.value


class FailingPathLike:
    """A test PathLike whose conversion fails."""

    def __fspath__(self) -> str:
        raise TypeError("forced path conversion failure")


class FailingBackupConnection(sqlite3.Connection):
    """A source connection that records and fails backup calls."""

    backup_calls = 0

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
        raise sqlite3.OperationalError("forced backup failure")


class TrackingConnection(sqlite3.Connection):
    """A source connection that records prohibited lifecycle calls."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.backup_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

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
        super().backup(
            target,
            pages=pages,
            progress=progress,
            name=name,
            sleep=sleep,
        )

    def commit(self) -> None:
        self.commit_calls += 1
        super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        super().rollback()

    def close(self) -> None:
        self.close_calls += 1
        super().close()


class DatabaseBackupTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.source_directory = self.root / "source"
        self.backup_directory = self.root / "backup output"
        self.source_directory.mkdir()
        self.backup_directory.mkdir()
        self.database_path = self.source_directory / "library.sqlite3"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    @staticmethod
    def table_snapshot(
        connection: sqlite3.Connection,
    ) -> dict[str, list[tuple[object, ...]]]:
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table, ordering in _TABLE_ORDER.items()
        }

    @staticmethod
    def schema_snapshot(
        connection: sqlite3.Connection,
    ) -> list[tuple[object, ...]]:
        return [
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index', 'trigger')
                ORDER BY type, name
                """
            ).fetchall()
        ]

    @staticmethod
    def pragma_snapshot(
        connection: sqlite3.Connection,
    ) -> dict[str, object]:
        return {
            name: connection.execute(f"PRAGMA {name}").fetchone()[0]
            for name in ("schema_version", "user_version", "journal_mode")
        }

    def temporary_artifacts(self) -> list[Path]:
        return sorted(
            (
                path
                for path in self.backup_directory.iterdir()
                if path.name.startswith(
                    ".pt_research_library_backup_in_progress_"
                )
            ),
            key=lambda path: path.name,
        )

    def assert_valid_sqlite_backup(self, path: Path) -> None:
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 0)
        with path.open("rb") as backup_file:
            self.assertEqual(
                backup_file.read(16),
                b"SQLite format 3\x00",
            )
        backup_connection = sqlite3.connect(path)
        try:
            self.assertEqual(
                backup_connection.execute(
                    "PRAGMA quick_check"
                ).fetchall(),
                [("ok",)],
            )
        finally:
            backup_connection.close()

    def add_complete_dataset(self) -> tuple[int, int]:
        populated_id = add_literature(
            self.connection,
            Literature(
                title='日本語, "引用"\n改行を含む合成テスト文献',
                authors="Synthetic Author A; Synthetic Author B",
                journal="Synthetic Test Journal",
                publication_year=2026,
                volume="12",
                issue="3",
                pages="101-112",
                doi="synthetic-doi-value",
                pmid="900000001",
                url="https://example.test/synthetic-record",
                language="ja",
                publication_type="Synthetic Test Type",
                abstract='要約, "引用"\n2行目',
                pdf_path="/temporary/synthetic-paper.pdf",
                personal_summary="自分の合成要約",
                ai_summary="手動登録した合成AI要約",
                ai_summary_status="修正済み",
                general_note="一般メモ",
                key_findings="主要な結果",
                methods_note="方法メモ",
                clinical_note="臨床メモ",
                limitation_note="限界メモ",
                relevance_note="研究との関連",
                evidence_level="Synthetic Level",
                verification_status="確認済み",
                adoption_status="採用",
                exclusion_reason="合成除外理由",
                rating=5,
            ),
        )
        null_id = add_literature(
            self.connection,
            Literature(title="NULL項目を保持する合成テスト文献"),
        )
        self.connection.execute(
            """
            UPDATE literature
            SET created_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "2026-07-27T01:02:03.123456Z",
                "2026-07-27T04:05:06.654321Z",
                populated_id,
            ),
        )
        self.connection.commit()

        first_tag_id = create_tag(self.connection, "肩関節")
        second_tag_id = create_tag(self.connection, 'tag, "quoted"\nline')
        attach_tag_to_literature(
            self.connection,
            populated_id,
            first_tag_id,
        )
        attach_tag_to_literature(
            self.connection,
            populated_id,
            second_tag_id,
        )
        attach_tag_to_literature(
            self.connection,
            null_id,
            first_tag_id,
        )
        create_usage_history(
            self.connection,
            populated_id,
            "学会発表",
            project_name='合成, "プロジェクト"',
            usage_note="1行目\n2行目",
            used_at="2026-07-27",
        )
        create_usage_history(
            self.connection,
            populated_id,
            "大学院研究",
        )
        create_usage_history(
            self.connection,
            null_id,
            "note",
            project_name=None,
            usage_note=None,
            used_at=None,
        )
        return populated_id, null_id

    def test_empty_initialized_database_creates_openable_sqlite_backup(
        self,
    ) -> None:
        before = self.table_snapshot(self.connection)

        backup_path = create_database_backup(
            self.connection,
            self.backup_directory,
        )

        self.assertIsInstance(backup_path, Path)
        self.assertEqual(backup_path.parent, self.backup_directory)
        self.assertEqual(backup_path.suffix, ".sqlite3")
        self.assertRegex(backup_path.name, _FILENAME_PATTERN)
        self.assert_valid_sqlite_backup(backup_path)
        self.assertEqual(self.table_snapshot(self.connection), before)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(self.temporary_artifacts(), [])
        self.assertEqual(
            set(self.backup_directory.iterdir()),
            {backup_path},
        )

    def test_utc_filename_converts_offset_and_keeps_six_microsecond_digits(
        self,
    ) -> None:
        japan_time = datetime(
            2026,
            7,
            27,
            0,
            0,
            0,
            123,
            tzinfo=timezone(timedelta(hours=9)),
        )

        with patch.object(
            backup_module,
            "_utc_now",
            return_value=japan_time,
        ):
            backup_path = create_database_backup(
                self.connection,
                self.backup_directory,
            )

        self.assertEqual(
            backup_path.name,
            "pt_research_library_backup_20260726T150000000123Z.sqlite3",
        )

    def test_all_rows_values_nulls_ids_and_order_are_preserved(self) -> None:
        self.add_complete_dataset()
        source_snapshot = self.table_snapshot(self.connection)

        backup_path = create_database_backup(
            self.connection,
            self.backup_directory,
        )

        backup_connection = connect_database(backup_path)
        try:
            self.assertEqual(
                self.table_snapshot(backup_connection),
                source_snapshot,
            )
            populated = backup_connection.execute(
                "SELECT * FROM literature ORDER BY id LIMIT 1"
            ).fetchone()
            null_record = backup_connection.execute(
                "SELECT * FROM literature ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(
                populated["title"],
                '日本語, "引用"\n改行を含む合成テスト文献',
            )
            self.assertEqual(populated["rating"], 5)
            self.assertEqual(populated["doi"], "synthetic-doi-value")
            self.assertEqual(populated["pmid"], "900000001")
            self.assertIsNone(null_record["authors"])
            self.assertIsNone(null_record["publication_year"])
            self.assertIsNone(null_record["rating"])
        finally:
            backup_connection.close()

    def test_tables_indexes_triggers_constraints_and_sequences_are_preserved(
        self,
    ) -> None:
        self.add_complete_dataset()
        self.connection.execute(
            """
            CREATE INDEX synthetic_backup_index
            ON literature(publication_year, id)
            """
        )
        self.connection.execute(
            """
            CREATE TRIGGER synthetic_backup_trigger
            AFTER UPDATE ON literature
            BEGIN
                SELECT 1;
            END
            """
        )
        self.connection.commit()
        source_schema = self.schema_snapshot(self.connection)
        source_sequence = [
            tuple(row)
            for row in self.connection.execute(
                "SELECT * FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        ]

        backup_path = create_database_backup(
            self.connection,
            self.backup_directory,
        )

        backup_connection = sqlite3.connect(backup_path)
        try:
            self.assertEqual(
                self.schema_snapshot(backup_connection),
                source_schema,
            )
            self.assertEqual(
                [
                    tuple(row)
                    for row in backup_connection.execute(
                        "SELECT * FROM sqlite_sequence ORDER BY name"
                    ).fetchall()
                ],
                source_sequence,
            )
            schema_text = "\n".join(
                str(row[3]) for row in source_schema if row[3] is not None
            )
            self.assertIn("CHECK", schema_text)
            self.assertIn("FOREIGN KEY", schema_text)
            self.assertIn("synthetic_backup_index", schema_text)
            self.assertIn("synthetic_backup_trigger", schema_text)
        finally:
            backup_connection.close()

    def test_same_timestamp_uses_deterministic_suffixes_without_overwrite(
        self,
    ) -> None:
        fixed_time = datetime(
            2026,
            7,
            27,
            12,
            34,
            56,
            123456,
            tzinfo=timezone.utc,
        )
        with patch.object(
            backup_module,
            "_utc_now",
            return_value=fixed_time,
        ):
            first = create_database_backup(
                self.connection,
                self.backup_directory,
            )
            with first.open("rb") as first_file:
                first_contents = first_file.read()
            second = create_database_backup(
                self.connection,
                self.backup_directory,
            )
            with second.open("rb") as second_file:
                second_contents = second_file.read()
            third = create_database_backup(
                self.connection,
                self.backup_directory,
            )

        self.assertEqual(
            [path.name for path in (first, second, third)],
            [
                "pt_research_library_backup_20260727T123456123456Z.sqlite3",
                "pt_research_library_backup_20260727T123456123456Z_1.sqlite3",
                "pt_research_library_backup_20260727T123456123456Z_2.sqlite3",
            ],
        )
        with first.open("rb") as first_file:
            self.assertEqual(first_file.read(), first_contents)
        with second.open("rb") as second_file:
            self.assertEqual(second_file.read(), second_contents)
        for path in (first, second, third):
            self.assert_valid_sqlite_backup(path)
        self.assertEqual(self.temporary_artifacts(), [])

    def test_publish_race_preserves_racing_file_and_uses_next_suffix(
        self,
    ) -> None:
        fixed_time = datetime(
            2026,
            7,
            27,
            1,
            2,
            3,
            4,
            tzinfo=timezone.utc,
        )
        original_link = os.link
        linked_paths: list[Path] = []
        racing_contents: dict[Path, str] = {}

        def racing_link(source: object, destination: object) -> None:
            destination_path = Path(destination)
            linked_paths.append(destination_path)
            if len(linked_paths) <= 3:
                contents = f"racing process contents {len(linked_paths)}"
                destination_path.write_text(
                    contents,
                    encoding="utf-8",
                )
                racing_contents[destination_path] = contents
                raise FileExistsError("forced publication race")
            original_link(source, destination)

        with (
            patch.object(
                backup_module,
                "_utc_now",
                return_value=fixed_time,
            ),
            patch.object(backup_module.os, "link", side_effect=racing_link),
        ):
            backup_path = create_database_backup(
                self.connection,
                self.backup_directory,
            )

        self.assertEqual(len(linked_paths), 4)
        for racing_path, contents in racing_contents.items():
            self.assertEqual(
                racing_path.read_text(encoding="utf-8"),
                contents,
            )
        self.assertEqual(
            backup_path.name,
            "pt_research_library_backup_20260727T010203000004Z_3.sqlite3",
        )
        self.assert_valid_sqlite_backup(backup_path)
        self.assertEqual(self.temporary_artifacts(), [])

    def test_valid_directory_path_forms_and_special_names_are_supported(
        self,
    ) -> None:
        japanese_directory = self.root / "日本語バックアップ"
        spaced_directory = self.root / "more backup files"
        japanese_directory.mkdir()
        spaced_directory.mkdir()
        relative_directory = Path(
            os.path.relpath(self.backup_directory, Path.cwd())
        )
        path_cases = (
            str(self.backup_directory),
            self.backup_directory,
            StringPathLike(str(self.backup_directory)),
            japanese_directory,
            spaced_directory,
            relative_directory,
        )

        for directory_value in path_cases:
            with self.subTest(directory_value=repr(directory_value)):
                backup_path = create_database_backup(
                    self.connection,
                    directory_value,
                )
                self.assertTrue(backup_path.is_file())
                self.assertEqual(
                    backup_path.parent,
                    Path(os.fspath(directory_value)),
                )
                self.assert_valid_sqlite_backup(backup_path)

    def test_invalid_path_types_and_empty_strings_create_nothing(self) -> None:
        before = self.table_snapshot(self.connection)
        invalid_values = (
            None,
            1,
            True,
            b"backup",
            bytearray(b"backup"),
            memoryview(b"backup"),
            BytesPathLike(b"backup"),
            FailingPathLike(),
            "",
            " ",
            "\t\n",
        )

        for value in invalid_values:
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "backup_directory"):
                    create_database_backup(self.connection, value)

        self.assertEqual(list(self.backup_directory.iterdir()), [])
        self.assertEqual(self.table_snapshot(self.connection), before)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_missing_directory_and_file_path_raise_specific_errors(
        self,
    ) -> None:
        missing_directory = self.root / "missing"
        file_path = self.root / "not-a-directory"
        file_path.write_text("kept file", encoding="utf-8")
        before = self.table_snapshot(self.connection)

        with self.assertRaises(FileNotFoundError):
            create_database_backup(self.connection, missing_directory)
        with self.assertRaises(NotADirectoryError):
            create_database_backup(self.connection, file_path)

        self.assertFalse(missing_directory.exists())
        self.assertEqual(
            file_path.read_text(encoding="utf-8"),
            "kept file",
        )
        self.assertEqual(list(self.backup_directory.iterdir()), [])
        self.assertEqual(self.table_snapshot(self.connection), before)

    def test_non_connection_is_rejected_before_filesystem_changes(self) -> None:
        with self.assertRaisesRegex(ValueError, "connection"):
            create_database_backup(None, self.backup_directory)

        self.assertEqual(list(self.backup_directory.iterdir()), [])

    def test_active_transaction_is_rejected_before_directory_or_backup(
        self,
    ) -> None:
        sqlite3.Connection.close(self.connection)
        source = sqlite3.connect(
            self.database_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, source)
        source.row_factory = sqlite3.Row
        sqlite3.Connection.execute(source, "PRAGMA foreign_keys = ON")
        self.connection = source
        pending_cursor = source.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending synthetic record",),
        )
        pending_id = pending_cursor.lastrowid
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "kept backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }
        self.assertTrue(source.in_transaction)

        with self.assertRaisesRegex(
            ValueError,
            "commitまたはrollbackで終了",
        ):
            create_database_backup(
                source,
                self.root / "missing-directory",
            )

        self.assertEqual(source.backup_calls, 0)
        self.assertEqual(source.commit_calls, 0)
        self.assertEqual(source.rollback_calls, 0)
        self.assertEqual(source.close_calls, 0)
        self.assertTrue(source.in_transaction)
        self.assertEqual(
            source.execute(
                "SELECT title FROM literature WHERE id = ?",
                (pending_id,),
            ).fetchone()[0],
            "Pending synthetic record",
        )
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
        self.assertEqual(
            {path.name for path in self.backup_directory.iterdir()},
            before_names,
        )
        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "kept backup contents",
        )
        self.assertEqual(self.temporary_artifacts(), [])
        sqlite3.Connection.rollback(source)

    def test_explicit_read_transaction_is_rejected_without_side_effects(
        self,
    ) -> None:
        sqlite3.Connection.close(self.connection)
        source = sqlite3.connect(
            self.database_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, source)
        source.row_factory = sqlite3.Row
        sqlite3.Connection.execute(source, "PRAGMA foreign_keys = ON")
        self.connection = source
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "kept backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }

        source.execute("BEGIN")
        self.assertEqual(source.execute("SELECT 1").fetchone()[0], 1)
        self.assertTrue(source.in_transaction)

        with (
            patch.object(backup_module.tempfile, "mkstemp") as mkstemp,
            self.assertRaisesRegex(
                ValueError,
                "commitまたはrollbackで終了",
            ),
        ):
            create_database_backup(
                source,
                self.backup_directory,
            )

        mkstemp.assert_not_called()
        self.assertEqual(source.backup_calls, 0)
        self.assertEqual(source.commit_calls, 0)
        self.assertEqual(source.rollback_calls, 0)
        self.assertEqual(source.close_calls, 0)
        self.assertTrue(source.in_transaction)
        self.assertEqual(source.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(
            {path.name for path in self.backup_directory.iterdir()},
            before_names,
        )
        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "kept backup contents",
        )
        self.assertEqual(self.temporary_artifacts(), [])
        sqlite3.Connection.rollback(source)

    def test_source_commit_rollback_and_close_are_never_called(self) -> None:
        sqlite3.Connection.close(self.connection)
        source = sqlite3.connect(
            self.database_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, source)
        source.row_factory = sqlite3.Row
        sqlite3.Connection.execute(source, "PRAGMA foreign_keys = ON")
        self.connection = source

        backup_path = create_database_backup(
            source,
            self.backup_directory,
        )

        self.assertEqual(source.backup_calls, 1)
        self.assertEqual(source.commit_calls, 0)
        self.assertEqual(source.rollback_calls, 0)
        self.assertEqual(source.close_calls, 0)
        self.assertFalse(source.in_transaction)
        self.assertEqual(source.execute("SELECT 1").fetchone()[0], 1)
        self.assert_valid_sqlite_backup(backup_path)

    def test_memory_database_preserves_data_and_schema(self) -> None:
        memory_connection = sqlite3.connect(":memory:")
        self.addCleanup(memory_connection.close)
        memory_connection.executescript(SCHEMA_SQL)
        cursor = memory_connection.execute(
            """
            INSERT INTO literature (title, rating)
            VALUES (?, ?)
            """,
            ("Synthetic memory record", 4),
        )
        tag_cursor = memory_connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("memory-tag",),
        )
        memory_connection.execute(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (cursor.lastrowid, tag_cursor.lastrowid),
        )
        memory_connection.execute(
            """
            INSERT INTO usage_history (
                literature_id,
                usage_type,
                project_name,
                usage_note,
                used_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                cursor.lastrowid,
                "memory-use",
                "Synthetic memory project",
                "Synthetic memory note",
                "2026-07-27",
            ),
        )
        memory_connection.commit()
        before_schema = self.schema_snapshot(memory_connection)

        backup_path = create_database_backup(
            memory_connection,
            self.backup_directory,
        )

        backup_connection = sqlite3.connect(backup_path)
        try:
            self.assertEqual(
                backup_connection.execute(
                    "SELECT id, title, rating FROM literature"
                ).fetchall(),
                [(cursor.lastrowid, "Synthetic memory record", 4)],
            )
            self.assertEqual(
                backup_connection.execute(
                    "SELECT name FROM tags"
                ).fetchall(),
                [("memory-tag",)],
            )
            self.assertEqual(
                backup_connection.execute(
                    """
                    SELECT literature_id, tag_id
                    FROM literature_tags
                    """
                ).fetchall(),
                [(cursor.lastrowid, tag_cursor.lastrowid)],
            )
            self.assertEqual(
                backup_connection.execute(
                    """
                    SELECT
                        literature_id,
                        usage_type,
                        project_name,
                        usage_note,
                        used_at
                    FROM usage_history
                    """
                ).fetchall(),
                [
                    (
                        cursor.lastrowid,
                        "memory-use",
                        "Synthetic memory project",
                        "Synthetic memory note",
                        "2026-07-27",
                    )
                ],
            )
            self.assertEqual(
                self.schema_snapshot(backup_connection),
                before_schema,
            )
        finally:
            backup_connection.close()
        self.assertEqual(
            memory_connection.execute("SELECT 1").fetchone()[0],
            1,
        )

    def test_wal_database_is_backed_up_without_changing_journal_mode(
        self,
    ) -> None:
        journal_mode = self.connection.execute(
            "PRAGMA journal_mode = WAL"
        ).fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        literature_id = add_literature(
            self.connection,
            Literature(title="Synthetic WAL record"),
        )

        backup_path = create_database_backup(
            self.connection,
            self.backup_directory,
        )

        self.assertEqual(
            self.connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0].lower(),
            "wal",
        )
        backup_connection = sqlite3.connect(backup_path)
        try:
            self.assertEqual(
                backup_connection.execute(
                    "SELECT id, title FROM literature"
                ).fetchall(),
                [(literature_id, "Synthetic WAL record")],
            )
        finally:
            backup_connection.close()

    def test_source_tables_schema_pragmas_and_directory_are_unchanged(
        self,
    ) -> None:
        self.add_complete_dataset()
        self.connection.execute("PRAGMA user_version = 73")
        tables_before = self.table_snapshot(self.connection)
        schema_before = self.schema_snapshot(self.connection)
        pragmas_before = self.pragma_snapshot(self.connection)
        source_names_before = {
            path.name for path in self.source_directory.iterdir()
        }

        create_database_backup(
            self.connection,
            self.backup_directory,
        )

        self.assertEqual(self.table_snapshot(self.connection), tables_before)
        self.assertEqual(self.schema_snapshot(self.connection), schema_before)
        self.assertEqual(self.pragma_snapshot(self.connection), pragmas_before)
        self.assertEqual(
            {path.name for path in self.source_directory.iterdir()},
            source_names_before,
        )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_backup_failure_preserves_existing_files_and_original_error(
        self,
    ) -> None:
        failing_source = sqlite3.connect(
            self.database_path,
            factory=FailingBackupConnection,
        )
        self.addCleanup(failing_source.close)
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "existing backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }

        with self.assertRaisesRegex(
            sqlite3.OperationalError,
            "forced backup failure",
        ):
            create_database_backup(
                failing_source,
                self.backup_directory,
            )

        self.assertEqual(failing_source.backup_calls, 1)
        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "existing backup contents",
        )
        self.assertEqual(
            {path.name for path in self.backup_directory.iterdir()},
            before_names,
        )
        self.assertEqual(self.temporary_artifacts(), [])
        self.assertEqual(failing_source.execute("SELECT 1").fetchone()[0], 1)

    def test_destination_connection_is_closed_after_success_and_failure(
        self,
    ) -> None:
        successful_source = Mock()
        successful_destination = Mock()
        with patch.object(
            backup_module.sqlite3,
            "connect",
            return_value=successful_destination,
        ):
            backup_module._copy_database(
                successful_source,
                Path("unused-success"),
            )

        successful_source.backup.assert_called_once_with(
            successful_destination
        )
        successful_destination.close.assert_called_once_with()

        expected_error = sqlite3.OperationalError(
            "forced destination backup error"
        )
        failing_source = Mock()
        failing_source.backup.side_effect = expected_error
        failing_destination = Mock()
        with patch.object(
            backup_module.sqlite3,
            "connect",
            return_value=failing_destination,
        ):
            with self.assertRaises(sqlite3.OperationalError) as raised:
                backup_module._copy_database(
                    failing_source,
                    Path("unused-failure"),
                )

        self.assertIs(raised.exception, expected_error)
        failing_destination.close.assert_called_once_with()

    def test_quick_check_failure_creates_no_final_and_removes_temporary(
        self,
    ) -> None:
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "existing backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }
        original_verify = backup_module._verify_backup

        def corrupt_then_verify(temporary_path: Path) -> None:
            with temporary_path.open("wb") as temporary_file:
                temporary_file.write(b"not a sqlite database")
            original_verify(temporary_path)

        with patch.object(
            backup_module,
            "_verify_backup",
            side_effect=corrupt_then_verify,
        ):
            with self.assertRaises(sqlite3.DatabaseError):
                create_database_backup(
                    self.connection,
                    self.backup_directory,
                )

        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "existing backup contents",
        )
        self.assertEqual(
            {path.name for path in self.backup_directory.iterdir()},
            before_names,
        )
        self.assertEqual(self.temporary_artifacts(), [])
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_non_ok_quick_check_rows_raise_database_error(self) -> None:
        fake_connection = Mock()
        fake_connection.execute.return_value.fetchall.return_value = [
            ("first problem",),
            ("second problem",),
        ]

        with patch.object(
            backup_module.sqlite3,
            "connect",
            return_value=fake_connection,
        ):
            with self.assertRaisesRegex(
                sqlite3.DatabaseError,
                "first problem; second problem",
            ):
                backup_module._verify_backup(Path("unused"))

        fake_connection.close.assert_called_once_with()

    def test_quick_check_sql_error_is_preserved_and_connection_is_closed(
        self,
    ) -> None:
        expected_error = sqlite3.DatabaseError("forced quick_check error")
        fake_connection = Mock()
        fake_connection.execute.side_effect = expected_error

        with patch.object(
            backup_module.sqlite3,
            "connect",
            return_value=fake_connection,
        ):
            with self.assertRaises(sqlite3.DatabaseError) as raised:
                backup_module._verify_backup(Path("unused"))

        self.assertIs(raised.exception, expected_error)
        fake_connection.close.assert_called_once_with()

    def test_quick_check_happens_before_final_publication(self) -> None:
        events: list[str] = []
        original_verify = backup_module._verify_backup
        original_publish = backup_module._publish_without_overwrite

        def tracked_verify(temporary_path: Path) -> None:
            events.append("quick_check")
            original_verify(temporary_path)

        def tracked_publish(*args, **kwargs) -> Path:
            events.append("publish")
            return original_publish(*args, **kwargs)

        with (
            patch.object(
                backup_module,
                "_verify_backup",
                side_effect=tracked_verify,
            ),
            patch.object(
                backup_module,
                "_publish_without_overwrite",
                side_effect=tracked_publish,
            ),
        ):
            backup_path = create_database_backup(
                self.connection,
                self.backup_directory,
            )

        self.assertEqual(events, ["quick_check", "publish"])
        self.assert_valid_sqlite_backup(backup_path)

    def test_non_collision_publish_errors_propagate_without_retry(
        self,
    ) -> None:
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "existing backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }
        expected_errors = (
            PermissionError(errno.EACCES, "forced permission failure"),
            OSError(errno.EXDEV, "forced cross-device failure"),
            OSError(
                getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
                "forced hard-link support failure",
            ),
        )

        for expected_error in expected_errors:
            with self.subTest(error=repr(expected_error)):
                with patch.object(
                    backup_module.os,
                    "link",
                    side_effect=expected_error,
                ) as link:
                    with self.assertRaises(OSError) as raised:
                        create_database_backup(
                            self.connection,
                            self.backup_directory,
                        )

                self.assertIs(raised.exception, expected_error)
                link.assert_called_once()
                self.assertEqual(
                    existing_backup.read_text(encoding="utf-8"),
                    "existing backup contents",
                )
                self.assertEqual(
                    {path.name for path in self.backup_directory.iterdir()},
                    before_names,
                )
                self.assertEqual(self.temporary_artifacts(), [])

    def test_all_publish_candidates_colliding_stops_after_1000_attempts(
        self,
    ) -> None:
        self.add_complete_dataset()
        source_snapshot = self.table_snapshot(self.connection)
        sqlite3.Connection.close(self.connection)
        source = sqlite3.connect(
            self.database_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, source)
        source.row_factory = sqlite3.Row
        sqlite3.Connection.execute(source, "PRAGMA foreign_keys = ON")
        self.connection = source
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "kept backup contents",
            encoding="utf-8",
        )
        before_names = {
            path.name for path in self.backup_directory.iterdir()
        }
        attempted_paths: list[Path] = []
        fixed_time = datetime(
            2026,
            7,
            27,
            1,
            2,
            3,
            4,
            tzinfo=timezone.utc,
        )

        def collide(source_path: object, destination: object) -> None:
            attempted_paths.append(Path(destination))
            raise FileExistsError("forced name collision")

        with (
            patch.object(
                backup_module,
                "_utc_now",
                return_value=fixed_time,
            ),
            patch.object(
                backup_module.os,
                "link",
                side_effect=collide,
            ) as link,
        ):
            with self.assertRaisesRegex(
                FileExistsError,
                "利用可能なバックアップ名.*確保できませんでした",
            ):
                create_database_backup(
                    source,
                    self.backup_directory,
                )

        self.assertEqual(link.call_count, backup_module._MAX_PUBLISH_ATTEMPTS)
        self.assertEqual(len(attempted_paths), 1000)
        self.assertEqual(
            attempted_paths[0].name,
            "pt_research_library_backup_20260727T010203000004Z.sqlite3",
        )
        self.assertEqual(
            attempted_paths[-1].name,
            "pt_research_library_backup_"
            "20260727T010203000004Z_999.sqlite3",
        )
        self.assertEqual(len(set(attempted_paths)), 1000)
        self.assertEqual(
            {path.name for path in self.backup_directory.iterdir()},
            before_names,
        )
        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "kept backup contents",
        )
        self.assertEqual(self.temporary_artifacts(), [])
        self.assertEqual(self.table_snapshot(source), source_snapshot)
        self.assertEqual(source.backup_calls, 1)
        self.assertEqual(source.commit_calls, 0)
        self.assertEqual(source.rollback_calls, 0)
        self.assertEqual(source.close_calls, 0)
        self.assertFalse(source.in_transaction)
        self.assertEqual(source.execute("SELECT 1").fetchone()[0], 1)

    def test_post_publish_cleanup_failure_returns_verified_final_path(
        self,
    ) -> None:
        self.add_complete_dataset()
        source_snapshot = self.table_snapshot(self.connection)
        source_schema = self.schema_snapshot(self.connection)
        sqlite3.Connection.close(self.connection)
        source = sqlite3.connect(
            self.database_path,
            factory=TrackingConnection,
        )
        self.addCleanup(sqlite3.Connection.close, source)
        source.row_factory = sqlite3.Row
        sqlite3.Connection.execute(source, "PRAGMA foreign_keys = ON")
        self.connection = source
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "kept backup contents",
            encoding="utf-8",
        )
        original_unlink = os.unlink
        unlink_attempts: list[Path] = []

        def fail_temporary_unlink(path: object) -> None:
            unlink_path = Path(path)
            unlink_attempts.append(unlink_path)
            if unlink_path.name.startswith(
                ".pt_research_library_backup_in_progress_"
            ):
                raise PermissionError(
                    errno.EACCES,
                    "forced temporary cleanup failure",
                )
            original_unlink(path)

        with patch.object(
            backup_module.os,
            "unlink",
            side_effect=fail_temporary_unlink,
        ):
            backup_path = create_database_backup(
                source,
                self.backup_directory,
            )

        self.assertIsInstance(backup_path, Path)
        self.assertEqual(backup_path.parent, self.backup_directory)
        self.assert_valid_sqlite_backup(backup_path)
        backup_connection = connect_database(backup_path)
        try:
            self.assertEqual(
                self.table_snapshot(backup_connection),
                source_snapshot,
            )
            self.assertEqual(
                self.schema_snapshot(backup_connection),
                source_schema,
            )
        finally:
            backup_connection.close()
        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "kept backup contents",
        )
        self.assertNotIn(backup_path, unlink_attempts)
        self.assertEqual(len(unlink_attempts), 1)
        remaining_temporary = self.temporary_artifacts()
        self.assertEqual(len(remaining_temporary), 1)
        self.assertEqual(
            remaining_temporary[0].stat().st_ino,
            backup_path.stat().st_ino,
        )
        self.assertEqual(self.table_snapshot(source), source_snapshot)
        self.assertEqual(source.backup_calls, 1)
        self.assertEqual(source.commit_calls, 0)
        self.assertEqual(source.rollback_calls, 0)
        self.assertEqual(source.close_calls, 0)
        self.assertFalse(source.in_transaction)
        self.assertEqual(source.execute("SELECT 1").fetchone()[0], 1)

    def test_cleanup_failure_does_not_hide_original_operation_errors(
        self,
    ) -> None:
        failing_source = sqlite3.connect(
            self.database_path,
            factory=FailingBackupConnection,
        )
        self.addCleanup(failing_source.close)
        existing_backup = (
            self.backup_directory / "existing_backup.sqlite3"
        )
        existing_backup.write_text(
            "existing backup contents",
            encoding="utf-8",
        )

        cleanup_error = OSError("forced cleanup failure")
        with patch.object(
            backup_module.os,
            "unlink",
            side_effect=cleanup_error,
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "forced backup failure",
            ):
                create_database_backup(
                    failing_source,
                    self.backup_directory,
                )

        verification_error = sqlite3.DatabaseError(
            "forced verification failure"
        )
        with (
            patch.object(
                backup_module,
                "_verify_backup",
                side_effect=verification_error,
            ),
            patch.object(
                backup_module.os,
                "unlink",
                side_effect=cleanup_error,
            ),
        ):
            with self.assertRaises(sqlite3.DatabaseError) as raised:
                create_database_backup(
                    self.connection,
                    self.backup_directory,
                )
        self.assertIs(raised.exception, verification_error)

        publication_error = OSError("forced publication failure")
        with (
            patch.object(
                backup_module.os,
                "link",
                side_effect=publication_error,
            ),
            patch.object(
                backup_module.os,
                "unlink",
                side_effect=cleanup_error,
            ),
        ):
            with self.assertRaises(OSError) as raised:
                create_database_backup(
                    self.connection,
                    self.backup_directory,
                )
        self.assertIs(raised.exception, publication_error)

        self.assertEqual(
            existing_backup.read_text(encoding="utf-8"),
            "existing backup contents",
        )
        self.assertEqual(failing_source.execute("SELECT 1").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
