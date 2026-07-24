"""Tests for SQLite connection and schema initialization."""

import tempfile
import unittest
from pathlib import Path

from src.database import connect_database, initialize_database


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "test.db"

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


if __name__ == "__main__":
    unittest.main()
