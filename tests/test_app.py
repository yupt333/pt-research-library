"""Tests for application startup and the package entry point."""

import importlib
import os
import runpy
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import src.app as app_module
import src.cli as cli_module
import src.database as database_module
from src.database import connect_database, initialize_database


CURRENT_TABLES = {
    "literature",
    "tags",
    "literature_tags",
    "usage_history",
    "schema_migrations",
    "structured_entities",
    "structured_fields",
    "evidence_references",
    "structured_field_evidence",
    "structured_entity_evidence",
    "research_projects",
    "research_project_literature",
    "research_project_items",
}


class TrackingConnection(sqlite3.Connection):
    """SQLite connection that records lifecycle operations."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self) -> None:
        self.commit_calls += 1
        return super().commit()

    def rollback(self) -> None:
        self.rollback_calls += 1
        return super().rollback()

    def close(self) -> None:
        self.close_calls += 1
        return super().close()


class ApplicationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project_root = Path(self.temporary_directory.name)

    def open_tracking_connection(self) -> TrackingConnection:
        data_directory = self.project_root / "data"
        data_directory.mkdir(exist_ok=True)
        database_path = data_directory / "pt_research_library.sqlite3"
        initialize_database(database_path)
        connection = sqlite3.connect(
            database_path,
            factory=TrackingConnection,
        )
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        self.addCleanup(sqlite3.Connection.close, connection)
        return connection

    @staticmethod
    def database_snapshot(
        connection: sqlite3.Connection,
    ) -> tuple[dict[str, list[tuple[object, ...]]], list[tuple[object, ...]]]:
        tables = {}
        for table_name, order_by in (
            ("literature", "id"),
            ("tags", "id"),
            ("literature_tags", "literature_id, tag_id"),
            ("usage_history", "id"),
        ):
            rows = connection.execute(
                f"SELECT * FROM {table_name} ORDER BY {order_by}"
            ).fetchall()
            tables[table_name] = [tuple(row) for row in rows]
        schema = connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            ORDER BY type, name
            """
        ).fetchall()
        return tables, [tuple(row) for row in schema]

    def test_first_run_creates_directories_database_and_calls_cli_once(
        self,
    ) -> None:
        def inspect_runtime_connection(
            connection: sqlite3.Connection,
            *,
            export_directory: Path,
            backup_directory: Path,
        ) -> None:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            self.assertEqual(export_directory, self.project_root / "exports")
            self.assertEqual(backup_directory, self.project_root / "backups")

        with patch.object(
            app_module,
            "run_cli",
            side_effect=inspect_runtime_connection,
        ) as run_cli:
            app_module.run_application(self.project_root)

        run_cli.assert_called_once()
        for directory_name in ("data", "exports", "backups"):
            self.assertTrue((self.project_root / directory_name).is_dir())

        database_path = (
            self.project_root / "data" / "pt_research_library.sqlite3"
        )
        self.assertTrue(database_path.is_file())
        connection = sqlite3.connect(database_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            table_names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
        finally:
            connection.close()
        self.assertEqual(
            table_names,
            CURRENT_TABLES,
        )

        runtime_connection = run_cli.call_args.args[0]
        with self.assertRaises(sqlite3.ProgrammingError):
            runtime_connection.execute("SELECT 1")

    def test_second_run_preserves_existing_database_and_all_data(self) -> None:
        with patch.object(app_module, "run_cli") as run_cli:
            app_module.run_application(self.project_root)

            database_path = (
                self.project_root / "data" / "pt_research_library.sqlite3"
            )
            connection = connect_database(database_path)
            try:
                literature_id = connection.execute(
                    "INSERT INTO literature (title) VALUES (?)",
                    ("Synthetic persistence literature",),
                ).lastrowid
                tag_id = connection.execute(
                    "INSERT INTO tags (name) VALUES (?)",
                    ("synthetic-persistence-tag",),
                ).lastrowid
                self.assertIsNotNone(literature_id)
                self.assertIsNotNone(tag_id)
                connection.execute(
                    """
                    INSERT INTO literature_tags (literature_id, tag_id)
                    VALUES (?, ?)
                    """,
                    (literature_id, tag_id),
                )
                usage_history_id = connection.execute(
                    """
                    INSERT INTO usage_history (
                        literature_id, usage_type, project_name, usage_note
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        literature_id,
                        "synthetic-use",
                        "synthetic-project",
                        "synthetic-note",
                    ),
                ).lastrowid
                self.assertIsNotNone(usage_history_id)
                connection.execute("PRAGMA user_version = 804")
                connection.commit()
                before = self.database_snapshot(connection)
                schema_version_before = connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()[0]
                user_version_before = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            finally:
                connection.close()

            app_module.run_application(self.project_root)

        self.assertEqual(run_cli.call_count, 2)
        connection = connect_database(database_path)
        try:
            self.assertEqual(self.database_snapshot(connection), before)
            self.assertEqual(
                connection.execute("PRAGMA schema_version").fetchone()[0],
                schema_version_before,
            )
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                user_version_before,
            )
        finally:
            connection.close()

    def test_existing_directory_contents_are_preserved(self) -> None:
        markers = {}
        for directory_name in ("data", "exports", "backups"):
            directory = self.project_root / directory_name
            directory.mkdir()
            marker = directory / "marker.txt"
            marker.write_text(
                f"preserve-{directory_name}",
                encoding="utf-8",
            )
            markers[marker] = marker.read_bytes()

        with patch.object(app_module, "run_cli"):
            app_module.run_application(self.project_root)

        for marker, expected_content in markers.items():
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_bytes(), expected_content)

    def test_legacy_database_is_backed_up_then_migrated_on_startup(self) -> None:
        data_directory = self.project_root / "data"
        data_directory.mkdir()
        database_path = data_directory / "pt_research_library.sqlite3"
        connection = connect_database(database_path)
        try:
            connection.executescript(
                f"BEGIN;\n{database_module._PHASE1_TABLES_SQL}\nCOMMIT;"
            )
            connection.execute(
                "INSERT INTO literature (title) VALUES (?)",
                ("Synthetic app legacy literature",),
            )
            connection.execute("PRAGMA user_version = 805")
            connection.commit()
        finally:
            connection.close()

        with patch.object(app_module, "run_cli") as run_cli:
            app_module.run_application(self.project_root)

        run_cli.assert_called_once()
        backup_paths = list((self.project_root / "backups").glob("*.sqlite3"))
        self.assertEqual(len(backup_paths), 1)
        backup_connection = sqlite3.connect(backup_paths[0])
        try:
            backup_tables = {
                row[0]
                for row in backup_connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            self.assertEqual(backup_tables, {
                "literature",
                "tags",
                "literature_tags",
                "usage_history",
            })
            self.assertEqual(
                backup_connection.execute(
                    "SELECT id, title FROM literature"
                ).fetchall(),
                [(1, "Synthetic app legacy literature")],
            )
        finally:
            backup_connection.close()

        migrated_connection = connect_database(database_path)
        try:
            migrated_tables = {
                row["name"]
                for row in migrated_connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            self.assertEqual(migrated_tables, CURRENT_TABLES)
            self.assertEqual(
                [
                    tuple(row)
                    for row in migrated_connection.execute(
                        "SELECT id, title FROM literature"
                    ).fetchall()
                ],
                [(1, "Synthetic app legacy literature")],
            )
            self.assertEqual(
                migrated_connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0],
                805,
            )
        finally:
            migrated_connection.close()

    def test_runner_uses_project_root_instead_of_current_working_directory(
        self,
    ) -> None:
        unrelated_directory = Path(tempfile.mkdtemp())
        self.addCleanup(unrelated_directory.rmdir)
        original_cwd = Path.cwd()
        try:
            os.chdir(unrelated_directory)
            with patch.object(app_module, "run_cli") as run_cli:
                app_module.run_application(self.project_root)
        finally:
            os.chdir(original_cwd)

        run_cli.assert_called_once()
        self.assertEqual(list(unrelated_directory.iterdir()), [])
        for directory_name in ("data", "exports", "backups"):
            self.assertTrue((self.project_root / directory_name).is_dir())

    def test_main_derives_project_root_from_app_module_location(self) -> None:
        with patch.object(app_module, "run_application") as runner:
            app_module.main()

        runner.assert_called_once_with(
            Path(app_module.__file__).resolve().parent.parent
        )

    def test_normal_cli_return_closes_owned_connection_without_transaction(
        self,
    ) -> None:
        connection = self.open_tracking_connection()
        with (
            patch.object(app_module, "connect_database", return_value=connection),
            patch.object(app_module, "run_cli") as run_cli,
        ):
            app_module.run_application(self.project_root)

        run_cli.assert_called_once()
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_cli_exception_is_propagated_after_connection_close(self) -> None:
        connection = self.open_tracking_connection()
        expected = RuntimeError("synthetic CLI failure")
        with (
            patch.object(app_module, "connect_database", return_value=connection),
            patch.object(app_module, "run_cli", side_effect=expected) as run_cli,
            self.assertRaises(RuntimeError) as raised,
        ):
            app_module.run_application(self.project_root)

        self.assertIs(raised.exception, expected)
        run_cli.assert_called_once()
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)

    def test_keyboard_interrupt_is_propagated_after_connection_close(self) -> None:
        connection = self.open_tracking_connection()
        expected = KeyboardInterrupt()
        with (
            patch.object(app_module, "connect_database", return_value=connection),
            patch.object(app_module, "run_cli", side_effect=expected) as run_cli,
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            app_module.run_application(self.project_root)

        self.assertIs(raised.exception, expected)
        run_cli.assert_called_once()
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)

    def test_initialize_failure_stops_startup_and_is_propagated(self) -> None:
        expected = sqlite3.OperationalError("synthetic initialize failure")
        with (
            patch.object(
                app_module,
                "initialize_database",
                side_effect=expected,
            ) as initialized,
            patch.object(app_module, "connect_database") as connected,
            patch.object(app_module, "run_cli") as run_cli,
            self.assertRaises(sqlite3.OperationalError) as raised,
        ):
            app_module.run_application(self.project_root)

        self.assertIs(raised.exception, expected)
        initialized.assert_called_once_with(
            self.project_root / "data" / "pt_research_library.sqlite3",
            migration_backup_directory=self.project_root / "backups",
        )
        connected.assert_not_called()
        run_cli.assert_not_called()

    def test_connect_failure_stops_startup_and_is_propagated(self) -> None:
        expected = sqlite3.OperationalError("synthetic connect failure")
        with (
            patch.object(app_module, "initialize_database") as initialized,
            patch.object(
                app_module,
                "connect_database",
                side_effect=expected,
            ) as connected,
            patch.object(app_module, "run_cli") as run_cli,
            self.assertRaises(sqlite3.OperationalError) as raised,
        ):
            app_module.run_application(self.project_root)

        database_path = (
            self.project_root / "data" / "pt_research_library.sqlite3"
        )
        self.assertIs(raised.exception, expected)
        initialized.assert_called_once_with(
            database_path,
            migration_backup_directory=self.project_root / "backups",
        )
        connected.assert_called_once_with(database_path)
        run_cli.assert_not_called()

    def test_directory_file_conflicts_are_preserved_and_stop_startup(
        self,
    ) -> None:
        for conflicting_name in ("data", "exports", "backups"):
            with self.subTest(conflicting_name=conflicting_name):
                with tempfile.TemporaryDirectory() as directory:
                    project_root = Path(directory)
                    conflict = project_root / conflicting_name
                    marker = f"preserve-{conflicting_name}".encode()
                    conflict.write_bytes(marker)
                    with (
                        patch.object(app_module, "initialize_database") as initialized,
                        patch.object(app_module, "connect_database") as connected,
                        patch.object(app_module, "run_cli") as run_cli,
                        self.assertRaises(FileExistsError),
                    ):
                        app_module.run_application(project_root)

                    self.assertTrue(conflict.is_file())
                    self.assertEqual(conflict.read_bytes(), marker)
                    self.assertFalse(
                        (
                            project_root
                            / "data"
                            / "pt_research_library.sqlite3"
                        ).exists()
                    )
                    initialized.assert_not_called()
                    connected.assert_not_called()
                    run_cli.assert_not_called()

    def test_directory_os_error_is_propagated_without_startup(self) -> None:
        expected = PermissionError("synthetic directory failure")
        with (
            patch.object(Path, "mkdir", side_effect=expected) as mkdir,
            patch.object(app_module, "initialize_database") as initialized,
            patch.object(app_module, "connect_database") as connected,
            patch.object(app_module, "run_cli") as run_cli,
            self.assertRaises(PermissionError) as raised,
        ):
            app_module.run_application(self.project_root)

        self.assertIs(raised.exception, expected)
        mkdir.assert_called_once_with(exist_ok=True)
        initialized.assert_not_called()
        connected.assert_not_called()
        run_cli.assert_not_called()

    def test_missing_project_root_is_not_created(self) -> None:
        missing_root = self.project_root / "missing-project-root"
        with (
            patch.object(app_module, "initialize_database") as initialized,
            patch.object(app_module, "connect_database") as connected,
            patch.object(app_module, "run_cli") as run_cli,
            self.assertRaises(FileNotFoundError),
        ):
            app_module.run_application(missing_root)

        self.assertFalse(missing_root.exists())
        initialized.assert_not_called()
        connected.assert_not_called()
        run_cli.assert_not_called()


class EntrypointTestCase(unittest.TestCase):
    def test_module_entrypoint_calls_main_once(self) -> None:
        with (
            patch.object(app_module, "main") as main,
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("src.__main__", run_name="__main__")

        main.assert_called_once_with()

    def test_normal_entrypoint_import_does_not_call_main(self) -> None:
        with patch.object(app_module, "main") as main:
            module = importlib.import_module("src.__main__")

        self.assertEqual(module.__name__, "src.__main__")
        main.assert_not_called()

    def test_app_import_has_no_startup_side_effects(self) -> None:
        try:
            with (
                patch.object(
                    database_module,
                    "initialize_database",
                ) as initialized,
                patch.object(database_module, "connect_database") as connected,
                patch.object(cli_module, "run_cli") as run_cli,
            ):
                importlib.reload(app_module)

                initialized.assert_not_called()
                connected.assert_not_called()
                run_cli.assert_not_called()
        finally:
            importlib.reload(app_module)


if __name__ == "__main__":
    unittest.main()
