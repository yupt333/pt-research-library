"""Tests for SQLite connection and schema initialization."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.database as database_module
from src.database import connect_database, initialize_database


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "test.db"

    def open_initialized_database(self) -> sqlite3.Connection:
        initialize_database(self.database_path)
        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        return connection

    def test_initialize_database_creates_four_tables(self) -> None:
        initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()

        self.assertEqual(
            {row["name"] for row in rows},
            {"literature", "tags", "literature_tags", "usage_history"},
        )

    def test_initialize_database_can_run_more_than_once(self) -> None:
        initialize_database(self.database_path)
        initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        table_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'table' AND name = 'literature'
            """
        ).fetchone()[0]

        self.assertEqual(table_count, 1)

    def test_foreign_keys_are_enabled_for_each_connection(self) -> None:
        initialize_database(self.database_path)

        first_connection = connect_database(self.database_path)
        self.addCleanup(first_connection.close)
        second_connection = connect_database(self.database_path)
        self.addCleanup(second_connection.close)

        self.assertEqual(first_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(second_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_foreign_keys_use_delete_cascade(self) -> None:
        initialize_database(self.database_path)
        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)

        literature_tag_keys = connection.execute(
            "PRAGMA foreign_key_list(literature_tags)"
        ).fetchall()
        usage_history_keys = connection.execute(
            "PRAGMA foreign_key_list(usage_history)"
        ).fetchall()

        literature_tag_actions = {
            row["table"]: row["on_delete"] for row in literature_tag_keys
        }
        self.assertEqual(
            literature_tag_actions,
            {"literature": "CASCADE", "tags": "CASCADE"},
        )
        self.assertEqual(len(usage_history_keys), 1)
        self.assertEqual(usage_history_keys[0]["table"], "literature")
        self.assertEqual(usage_history_keys[0]["on_delete"], "CASCADE")

    def test_rating_null_can_be_stored(self) -> None:
        connection = self.open_initialized_database()

        cursor = connection.execute(
            "INSERT INTO literature (title, rating) VALUES (?, NULL)",
            ("No rating",),
        )
        stored_rating = connection.execute(
            "SELECT rating FROM literature WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()["rating"]

        self.assertIsNone(stored_rating)

    def test_rating_one_can_be_stored(self) -> None:
        connection = self.open_initialized_database()

        cursor = connection.execute(
            "INSERT INTO literature (title, rating) VALUES (?, ?)",
            ("Minimum rating", 1),
        )
        stored_rating = connection.execute(
            "SELECT rating FROM literature WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()["rating"]

        self.assertEqual(stored_rating, 1)

    def test_rating_five_can_be_stored(self) -> None:
        connection = self.open_initialized_database()

        cursor = connection.execute(
            "INSERT INTO literature (title, rating) VALUES (?, ?)",
            ("Maximum rating", 5),
        )
        stored_rating = connection.execute(
            "SELECT rating FROM literature WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()["rating"]

        self.assertEqual(stored_rating, 5)

    def test_rating_zero_is_rejected(self) -> None:
        connection = self.open_initialized_database()

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO literature (title, rating) VALUES (?, ?)",
                ("Rating below range", 0),
            )

    def test_rating_six_is_rejected(self) -> None:
        connection = self.open_initialized_database()

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO literature (title, rating) VALUES (?, ?)",
                ("Rating above range", 6),
            )

    def test_decimal_rating_is_rejected(self) -> None:
        connection = self.open_initialized_database()

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO literature (title, rating) VALUES (?, ?)",
                ("Decimal rating", 1.5),
            )

    def test_direct_insert_uses_state_field_database_defaults(self) -> None:
        connection = self.open_initialized_database()

        cursor = connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Database defaults",),
        )
        row = connection.execute(
            """
            SELECT
                ai_summary_status,
                verification_status,
                adoption_status,
                rating
            FROM literature
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()

        self.assertEqual(row["ai_summary_status"], "未作成")
        self.assertEqual(row["verification_status"], "未確認")
        self.assertEqual(row["adoption_status"], "未判定")
        self.assertIsNone(row["rating"])

    def test_schema_error_rolls_back_tables_created_before_failure(self) -> None:
        failing_schema = """
        BEGIN;
        CREATE TABLE first_table (id INTEGER PRIMARY KEY);
        CREATE TABLE broken_table (id INTEGER PRIMARY KEY,);
        COMMIT;
        """

        with patch.object(database_module, "SCHEMA_SQL", failing_schema):
            with self.assertRaises(sqlite3.OperationalError):
                initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        table_names = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()

        self.assertEqual(table_names, [])

    def test_schema_error_is_propagated_to_caller(self) -> None:
        failing_schema = """
        BEGIN;
        SELECT * FROM table_that_does_not_exist;
        COMMIT;
        """

        with patch.object(database_module, "SCHEMA_SQL", failing_schema):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "no such table: table_that_does_not_exist",
            ):
                initialize_database(self.database_path)


if __name__ == "__main__":
    unittest.main()
