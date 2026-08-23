"""Tests for SQLite connection, structured schema, and safe migrations."""

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import src.database as database_module
from src.database import connect_database, initialize_database


PHASE1_TABLES = {
    "literature",
    "tags",
    "literature_tags",
    "usage_history",
}
STRUCTURED_TABLES = {
    "schema_migrations",
    "structured_entities",
    "structured_fields",
    "evidence_references",
    "structured_field_evidence",
    "structured_entity_evidence",
}
CURRENT_TABLES = PHASE1_TABLES | STRUCTURED_TABLES


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "test.db"
        self.backup_directory = self.root / "backups"
        self.backup_directory.mkdir()

    def open_initialized_database(self) -> sqlite3.Connection:
        initialize_database(self.database_path)
        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        return connection

    @staticmethod
    def table_names(connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        return {row["name"] for row in rows}

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
    def phase1_data_snapshot(
        connection: sqlite3.Connection,
    ) -> dict[str, list[tuple[object, ...]]]:
        orderings = {
            "literature": "id",
            "tags": "id",
            "literature_tags": "literature_id, tag_id",
            "usage_history": "id",
        }
        return {
            table_name: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table_name} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table_name, ordering in orderings.items()
        }

    @staticmethod
    def phase1_definition_snapshot(
        connection: sqlite3.Connection,
    ) -> dict[str, dict[str, list[tuple[object, ...]] | str | None]]:
        snapshot = {}
        for table_name in sorted(PHASE1_TABLES):
            table_sql = connection.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()[0]
            table_info = [
                tuple(row)
                for row in connection.execute(
                    f"PRAGMA table_info({table_name})"
                ).fetchall()
            ]
            foreign_keys = [
                tuple(row)
                for row in connection.execute(
                    f"PRAGMA foreign_key_list({table_name})"
                ).fetchall()
            ]
            indexes = [
                tuple(row)
                for row in connection.execute(
                    f"PRAGMA index_list({table_name})"
                ).fetchall()
            ]
            snapshot[table_name] = {
                "sql": table_sql,
                "table_info": table_info,
                "foreign_keys": foreign_keys,
                "indexes": indexes,
            }
        return snapshot

    def create_legacy_database(self) -> None:
        connection = connect_database(self.database_path)
        try:
            connection.executescript(
                f"BEGIN;\n{database_module._PHASE1_TABLES_SQL}\nCOMMIT;"
            )
            literature_id = connection.execute(
                """
                INSERT INTO literature (
                    title,
                    authors,
                    publication_year,
                    methods_note,
                    verification_status,
                    rating
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "Synthetic legacy literature",
                    "Synthetic Legacy Author",
                    2020,
                    "Synthetic legacy free-text methods",
                    "一部確認",
                    4,
                ),
            ).lastrowid
            tag_id = connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                ("synthetic-legacy-tag",),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO literature_tags (literature_id, tag_id)
                VALUES (?, ?)
                """,
                (literature_id, tag_id),
            )
            connection.execute(
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
                    literature_id,
                    "synthetic-use",
                    "Synthetic legacy project label",
                    "Synthetic legacy usage note",
                    "2026-08-23",
                ),
            )
            connection.execute("PRAGMA user_version = 804")
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def add_literature(
        connection: sqlite3.Connection,
        title: str,
    ) -> int:
        literature_id = connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            (title,),
        ).lastrowid
        assert literature_id is not None
        return literature_id

    @staticmethod
    def add_entity(
        connection: sqlite3.Connection,
        literature_id: int,
        *,
        entity_type: str = "outcome",
        parent_entity_id: int | None = None,
        verification: str = "ai_unverified",
    ) -> int:
        entity_id = connection.execute(
            """
            INSERT INTO structured_entities (
                literature_id,
                entity_type,
                parent_entity_id,
                verification
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                literature_id,
                entity_type,
                parent_entity_id,
                verification,
            ),
        ).lastrowid
        assert entity_id is not None
        return entity_id

    @staticmethod
    def add_field(
        connection: sqlite3.Connection,
        literature_id: int,
        entity_id: int,
        *,
        field_key: str = "name",
        content_role: str = "source_fact",
        value_json: str | None = '"Synthetic value"',
        availability: str | None = "reported",
        verification: str = "ai_unverified",
    ) -> int:
        field_id = connection.execute(
            """
            INSERT INTO structured_fields (
                literature_id,
                entity_id,
                field_key,
                content_role,
                value_json,
                availability,
                verification
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                literature_id,
                entity_id,
                field_key,
                content_role,
                value_json,
                availability,
                verification,
            ),
        ).lastrowid
        assert field_id is not None
        return field_id

    @staticmethod
    def add_evidence(
        connection: sqlite3.Connection,
        literature_id: int,
        *,
        pdf_page: int | None = 1,
        section: str | None = None,
        verification: str = "ai_unverified",
    ) -> int:
        evidence_id = connection.execute(
            """
            INSERT INTO evidence_references (
                literature_id,
                pdf_page,
                section,
                verification
            )
            VALUES (?, ?, ?, ?)
            """,
            (literature_id, pdf_page, section, verification),
        ).lastrowid
        assert evidence_id is not None
        return evidence_id

    def test_initialize_database_creates_current_tables_and_version(self) -> None:
        initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.table_names(connection), CURRENT_TABLES)
        migration_rows = connection.execute(
            "SELECT version, applied_at FROM schema_migrations"
        ).fetchall()
        self.assertEqual(len(migration_rows), 1)
        self.assertEqual(
            migration_rows[0]["version"],
            database_module.CURRENT_SCHEMA_VERSION,
        )
        applied_at = migration_rows[0]["applied_at"]
        self.assertTrue(applied_at.endswith("Z"))
        datetime.fromisoformat(applied_at.replace("Z", "+00:00"))

    def test_fresh_database_does_not_create_migration_backup(self) -> None:
        initialize_database(
            self.database_path,
            migration_backup_directory=self.backup_directory,
        )

        self.assertEqual(list(self.backup_directory.iterdir()), [])

    def test_fresh_database_preserves_opaque_user_version(self) -> None:
        connection = connect_database(self.database_path)
        connection.execute("PRAGMA user_version = 804")
        connection.close()

        initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            804,
        )

    def test_initialize_database_can_run_more_than_once(self) -> None:
        initialize_database(self.database_path)
        initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.table_names(connection), CURRENT_TABLES)
        for table_name in (
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
        ):
            with self.subTest(table_name=table_name):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0],
                    0,
                )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = ?",
                (database_module.CURRENT_SCHEMA_VERSION,),
            ).fetchone()[0],
            1,
        )

    def test_legacy_migration_preserves_phase1_data_schema_and_user_version(
        self,
    ) -> None:
        self.create_legacy_database()
        connection = connect_database(self.database_path)
        before_data = self.phase1_data_snapshot(connection)
        before_definitions = self.phase1_definition_snapshot(connection)
        before_user_version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        connection.close()

        initialize_database(
            self.database_path,
            migration_backup_directory=self.backup_directory,
        )

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.phase1_data_snapshot(connection), before_data)
        self.assertEqual(
            self.phase1_definition_snapshot(connection),
            before_definitions,
        )
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            before_user_version,
        )
        self.assertEqual(self.table_names(connection), CURRENT_TABLES)
        for table_name in (
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
        ):
            with self.subTest(table_name=table_name):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0],
                    0,
                )
        self.assertEqual(
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ],
            [(database_module.CURRENT_SCHEMA_VERSION,)],
        )
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        self.assertEqual(
            connection.execute("PRAGMA quick_check").fetchone()[0],
            "ok",
        )

    def test_legacy_backup_is_created_before_migration(self) -> None:
        self.create_legacy_database()

        initialize_database(
            self.database_path,
            migration_backup_directory=self.backup_directory,
        )

        backup_paths = list(self.backup_directory.glob("*.sqlite3"))
        self.assertEqual(len(backup_paths), 1)
        backup_connection = connect_database(backup_paths[0])
        try:
            self.assertEqual(
                backup_connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            self.assertEqual(self.table_names(backup_connection), PHASE1_TABLES)
            self.assertEqual(
                [
                    tuple(row)
                    for row in backup_connection.execute(
                        "SELECT id, title FROM literature"
                    ).fetchall()
                ],
                [(1, "Synthetic legacy literature")],
            )
            self.assertEqual(
                backup_connection.execute("PRAGMA user_version").fetchone()[0],
                804,
            )
        finally:
            backup_connection.close()

    def test_repeated_initialization_after_migration_is_idempotent(self) -> None:
        self.create_legacy_database()
        initialize_database(
            self.database_path,
            migration_backup_directory=self.backup_directory,
        )
        connection = connect_database(self.database_path)
        before_data = self.phase1_data_snapshot(connection)
        before_schema = self.schema_snapshot(connection)
        before_user_version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        connection.close()
        backup_paths_before = list(self.backup_directory.iterdir())

        initialize_database(
            self.database_path,
            migration_backup_directory=self.backup_directory,
        )

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.phase1_data_snapshot(connection), before_data)
        self.assertEqual(self.schema_snapshot(connection), before_schema)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            before_user_version,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            1,
        )
        self.assertEqual(list(self.backup_directory.iterdir()), backup_paths_before)

    def test_legacy_migration_without_backup_directory_changes_nothing(
        self,
    ) -> None:
        self.create_legacy_database()
        connection = connect_database(self.database_path)
        before_data = self.phase1_data_snapshot(connection)
        before_schema = self.schema_snapshot(connection)
        before_user_version = connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0]
        connection.close()

        with self.assertRaisesRegex(
            database_module.DatabaseSchemaError,
            "migration_backup_directory",
        ):
            initialize_database(self.database_path)

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.phase1_data_snapshot(connection), before_data)
        self.assertEqual(self.schema_snapshot(connection), before_schema)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            before_user_version,
        )
        self.assertEqual(self.table_names(connection), PHASE1_TABLES)

    def test_backup_failure_blocks_migration_and_propagates(self) -> None:
        self.create_legacy_database()
        connection = connect_database(self.database_path)
        before_data = self.phase1_data_snapshot(connection)
        before_schema = self.schema_snapshot(connection)
        connection.close()
        expected = sqlite3.OperationalError("synthetic backup failure")

        with patch.object(
            database_module,
            "create_database_backup",
            side_effect=expected,
        ) as backup:
            with self.assertRaises(sqlite3.OperationalError) as raised:
                initialize_database(
                    self.database_path,
                    migration_backup_directory=self.backup_directory,
                )

        self.assertIs(raised.exception, expected)
        backup.assert_called_once()
        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.phase1_data_snapshot(connection), before_data)
        self.assertEqual(self.schema_snapshot(connection), before_schema)
        self.assertEqual(self.table_names(connection), PHASE1_TABLES)

    def test_migration_sql_failure_rolls_back_but_keeps_backup(self) -> None:
        self.create_legacy_database()
        connection = connect_database(self.database_path)
        before_data = self.phase1_data_snapshot(connection)
        before_schema = self.schema_snapshot(connection)
        connection.close()
        failing_migration = """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE partially_created_table (id INTEGER PRIMARY KEY);
        INSERT INTO schema_migrations (version, applied_at)
        VALUES (1, '2026-08-23T00:00:00Z');
        CREATE TABLE broken_table (id INTEGER PRIMARY KEY,);
        """

        with patch.object(
            database_module,
            "MIGRATION_1_SQL",
            failing_migration,
        ):
            with self.assertRaises(sqlite3.OperationalError):
                initialize_database(
                    self.database_path,
                    migration_backup_directory=self.backup_directory,
                )

        self.assertEqual(len(list(self.backup_directory.glob("*.sqlite3"))), 1)
        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.phase1_data_snapshot(connection), before_data)
        self.assertEqual(self.schema_snapshot(connection), before_schema)
        self.assertEqual(self.table_names(connection), PHASE1_TABLES)

    def test_newer_schema_is_rejected_without_changes(self) -> None:
        connection = connect_database(self.database_path)
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (2, '2026-08-23T00:00:00Z');
            CREATE TABLE synthetic_marker (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO synthetic_marker (value) VALUES ('preserve me');
            """
        )
        before = self.schema_snapshot(connection)
        marker_before = connection.execute(
            "SELECT * FROM synthetic_marker"
        ).fetchall()
        connection.close()

        with self.assertRaisesRegex(
            database_module.DatabaseSchemaError,
            "newer.*downgrade",
        ):
            initialize_database(
                self.database_path,
                migration_backup_directory=self.backup_directory,
            )

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.schema_snapshot(connection), before)
        self.assertEqual(
            connection.execute("SELECT * FROM synthetic_marker").fetchall(),
            marker_before,
        )
        self.assertEqual(list(self.backup_directory.iterdir()), [])

    def test_corrupted_current_schema_is_not_silently_repaired(self) -> None:
        connection = connect_database(self.database_path)
        connection.executescript(
            f"""
            BEGIN;
            {database_module._PHASE1_TABLES_SQL}
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (1, '2026-08-23T00:00:00Z');
            CREATE TABLE structured_entities (id INTEGER PRIMARY KEY);
            COMMIT;
            """
        )
        connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Synthetic corrupted-current marker",),
        )
        connection.commit()
        before = self.schema_snapshot(connection)
        data_before = self.phase1_data_snapshot(connection)
        connection.close()

        with self.assertRaisesRegex(
            database_module.DatabaseSchemaError,
            "current database.*必須table",
        ):
            initialize_database(
                self.database_path,
                migration_backup_directory=self.backup_directory,
            )

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.schema_snapshot(connection), before)
        self.assertEqual(self.phase1_data_snapshot(connection), data_before)
        self.assertNotEqual(self.table_names(connection), CURRENT_TABLES)
        self.assertEqual(list(self.backup_directory.iterdir()), [])

    def test_unknown_existing_database_missing_phase1_tables_is_rejected(
        self,
    ) -> None:
        connection = connect_database(self.database_path)
        connection.execute(
            "CREATE TABLE unrelated_table (id INTEGER PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO unrelated_table (id) VALUES (?)",
            (7,),
        )
        connection.commit()
        before = self.schema_snapshot(connection)
        connection.close()

        with self.assertRaisesRegex(
            database_module.DatabaseSchemaError,
            "legacy Phase 1 database.*必須table",
        ):
            initialize_database(
                self.database_path,
                migration_backup_directory=self.backup_directory,
            )

        connection = connect_database(self.database_path)
        self.addCleanup(connection.close)
        self.assertEqual(self.schema_snapshot(connection), before)
        self.assertEqual(
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT id FROM unrelated_table"
                ).fetchall()
            ],
            [(7,)],
        )
        self.assertEqual(list(self.backup_directory.iterdir()), [])

    def test_foreign_keys_are_enabled_for_each_connection(self) -> None:
        initialize_database(self.database_path)

        first_connection = connect_database(self.database_path)
        self.addCleanup(first_connection.close)
        second_connection = connect_database(self.database_path)
        self.addCleanup(second_connection.close)

        self.assertEqual(first_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(second_connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_foreign_keys_use_delete_cascade(self) -> None:
        connection = self.open_initialized_database()

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

    def test_invalid_entity_type_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Entity type")

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_entity(
                connection,
                literature_id,
                entity_type="unknown_entity",
            )

    def test_invalid_entity_verification_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Entity verification")

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_entity(
                connection,
                literature_id,
                verification="maybe_verified",
            )

    def test_result_requires_parent(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Result parent")

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_entity(
                connection,
                literature_id,
                entity_type="result",
            )

    def test_parent_entity_must_belong_to_same_literature(self) -> None:
        connection = self.open_initialized_database()
        first_literature = self.add_literature(connection, "Parent literature")
        second_literature = self.add_literature(connection, "Child literature")
        parent_id = self.add_entity(connection, first_literature)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_entity(
                connection,
                second_literature,
                entity_type="result",
                parent_entity_id=parent_id,
            )

    def test_duplicate_field_key_in_same_entity_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Duplicate field")
        entity_id = self.add_entity(connection, literature_id)
        self.add_field(connection, literature_id, entity_id, field_key="name")

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                field_key="name",
                value_json='"Another value"',
            )

    def test_invalid_content_role_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Content role")
        entity_id = self.add_entity(connection, literature_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                content_role="mixed_role",
            )

    def test_invalid_field_verification_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Field verification")
        entity_id = self.add_entity(connection, literature_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                verification="partially_verified",
            )

    def test_invalid_availability_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Availability")
        entity_id = self.add_entity(connection, literature_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                availability="missing",
                value_json=None,
            )

    def test_source_fact_reported_requires_value(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Reported value")
        entity_id = self.add_entity(connection, literature_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                value_json=None,
                availability="reported",
            )

    def test_source_fact_unreported_requires_null_value(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Unavailable value")
        entity_id = self.add_entity(connection, literature_id)

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                value_json='"Unsupported"',
                availability="not_reported",
            )

    def test_interpretation_requires_null_availability(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Interpretation state")
        entity_id = self.add_entity(
            connection,
            literature_id,
            entity_type="research_relevance",
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                content_role="interpretation",
                availability="reported",
            )

    def test_interpretation_requires_value(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Interpretation value")
        entity_id = self.add_entity(
            connection,
            literature_id,
            entity_type="research_relevance",
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                content_role="interpretation",
                availability=None,
                value_json=None,
            )

    def test_pdf_page_zero_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Evidence page")

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_evidence(connection, literature_id, pdf_page=0)

    def test_empty_evidence_and_note_only_evidence_are_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Empty evidence")

        for note in (None, "A note alone is not evidence"):
            with self.subTest(note=note):
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """
                        INSERT INTO evidence_references (
                            literature_id,
                            printed_page,
                            section,
                            quote_text,
                            note,
                            verification
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            literature_id,
                            "   ",
                            "\t",
                            "\n",
                            note,
                            "ai_unverified",
                        ),
                    )

    def test_invalid_evidence_verification_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(
            connection,
            "Evidence verification",
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.add_evidence(
                connection,
                literature_id,
                verification="source_verified",
            )

    def test_cross_literature_field_evidence_link_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        first_literature = self.add_literature(connection, "Field literature")
        second_literature = self.add_literature(connection, "Evidence literature")
        entity_id = self.add_entity(connection, first_literature)
        field_id = self.add_field(connection, first_literature, entity_id)
        evidence_id = self.add_evidence(connection, second_literature)

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO structured_field_evidence (
                    literature_id,
                    field_id,
                    evidence_id
                )
                VALUES (?, ?, ?)
                """,
                (first_literature, field_id, evidence_id),
            )

    def test_cross_literature_entity_evidence_link_is_rejected(self) -> None:
        connection = self.open_initialized_database()
        first_literature = self.add_literature(connection, "Entity literature")
        second_literature = self.add_literature(connection, "Other evidence")
        entity_id = self.add_entity(connection, first_literature)
        evidence_id = self.add_evidence(connection, second_literature)

        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO structured_entity_evidence (
                    literature_id,
                    entity_id,
                    evidence_id
                )
                VALUES (?, ?, ?)
                """,
                (first_literature, entity_id, evidence_id),
            )

    def test_same_name_outcomes_can_be_stored_as_separate_entities(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Same-name outcomes")
        first_outcome = self.add_entity(connection, literature_id)
        second_outcome = self.add_entity(connection, literature_id)
        for entity_id in (first_outcome, second_outcome):
            self.add_field(
                connection,
                literature_id,
                entity_id,
                field_key="name",
                value_json='"Synthetic Outcome"',
            )
            self.add_field(
                connection,
                literature_id,
                entity_id,
                field_key="definition",
                value_json='"Synthetic definition"',
            )
            self.add_field(
                connection,
                literature_id,
                entity_id,
                field_key="calculation_method",
                value_json='"Synthetic calculation"',
            )
        self.add_field(
            connection,
            literature_id,
            first_outcome,
            field_key="context",
            value_json='{"condition":"A"}',
        )
        self.add_field(
            connection,
            literature_id,
            second_outcome,
            field_key="context",
            value_json='{"condition":"B"}',
        )

        rows = connection.execute(
            """
            SELECT structured_entities.id, structured_fields.value_json
            FROM structured_entities
            JOIN structured_fields
                ON structured_fields.entity_id = structured_entities.id
            WHERE structured_entities.literature_id = ?
                AND structured_entities.entity_type = 'outcome'
                AND structured_fields.field_key = 'name'
            ORDER BY structured_entities.id
            """,
            (literature_id,),
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                (first_outcome, '"Synthetic Outcome"'),
                (second_outcome, '"Synthetic Outcome"'),
            ],
        )

    def test_evidence_delete_cascades_links_only(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Evidence cascade")
        entity_id = self.add_entity(connection, literature_id)
        field_id = self.add_field(connection, literature_id, entity_id)
        evidence_id = self.add_evidence(connection, literature_id)
        connection.execute(
            "INSERT INTO structured_field_evidence VALUES (?, ?, ?)",
            (literature_id, field_id, evidence_id),
        )
        connection.execute(
            "INSERT INTO structured_entity_evidence VALUES (?, ?, ?)",
            (literature_id, entity_id, evidence_id),
        )

        connection.execute(
            "DELETE FROM evidence_references WHERE id = ?",
            (evidence_id,),
        )

        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_entities").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_fields").fetchone()[0],
            1,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_field_evidence").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_entity_evidence").fetchone()[0],
            0,
        )

    def test_entity_delete_cascades_children_fields_and_links(self) -> None:
        connection = self.open_initialized_database()
        literature_id = self.add_literature(connection, "Entity cascade")
        outcome_id = self.add_entity(connection, literature_id)
        result_id = self.add_entity(
            connection,
            literature_id,
            entity_type="result",
            parent_entity_id=outcome_id,
        )
        outcome_field = self.add_field(connection, literature_id, outcome_id)
        result_field = self.add_field(
            connection,
            literature_id,
            result_id,
            field_key="result",
        )
        evidence_id = self.add_evidence(connection, literature_id)
        connection.execute(
            "INSERT INTO structured_field_evidence VALUES (?, ?, ?)",
            (literature_id, outcome_field, evidence_id),
        )
        connection.execute(
            "INSERT INTO structured_field_evidence VALUES (?, ?, ?)",
            (literature_id, result_field, evidence_id),
        )
        connection.execute(
            "INSERT INTO structured_entity_evidence VALUES (?, ?, ?)",
            (literature_id, outcome_id, evidence_id),
        )

        connection.execute(
            "DELETE FROM structured_entities WHERE id = ?",
            (outcome_id,),
        )

        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_entities").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_fields").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_field_evidence").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM structured_entity_evidence").fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM evidence_references").fetchone()[0],
            1,
        )

    def test_literature_delete_isolated_cascade_preserves_other_literature(
        self,
    ) -> None:
        connection = self.open_initialized_database()
        first_literature = self.add_literature(connection, "Delete target")
        second_literature = self.add_literature(connection, "Preserve target")
        ids_by_literature = {}
        for literature_id in (first_literature, second_literature):
            outcome_id = self.add_entity(connection, literature_id)
            result_id = self.add_entity(
                connection,
                literature_id,
                entity_type="result",
                parent_entity_id=outcome_id,
            )
            field_id = self.add_field(connection, literature_id, outcome_id)
            evidence_id = self.add_evidence(connection, literature_id)
            connection.execute(
                "INSERT INTO structured_field_evidence VALUES (?, ?, ?)",
                (literature_id, field_id, evidence_id),
            )
            connection.execute(
                "INSERT INTO structured_entity_evidence VALUES (?, ?, ?)",
                (literature_id, result_id, evidence_id),
            )
            ids_by_literature[literature_id] = (
                outcome_id,
                result_id,
                field_id,
                evidence_id,
            )

        connection.execute(
            "DELETE FROM literature WHERE id = ?",
            (first_literature,),
        )

        for table_name in (
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
        ):
            with self.subTest(table_name=table_name):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE literature_id = ?",
                        (first_literature,),
                    ).fetchone()[0],
                    0,
                )
                self.assertGreater(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE literature_id = ?",
                        (second_literature,),
                    ).fetchone()[0],
                    0,
                )
        self.assertEqual(
            [
                tuple(row)
                for row in connection.execute(
                    "SELECT id FROM literature ORDER BY id"
                ).fetchall()
            ],
            [(second_literature,)],
        )
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        expected_outcome, expected_result, expected_field, expected_evidence = (
            ids_by_literature[second_literature]
        )
        self.assertEqual(
            [
                row[0]
                for row in connection.execute(
                    """
                    SELECT id
                    FROM structured_entities
                    WHERE literature_id = ?
                    ORDER BY id
                    """,
                    (second_literature,),
                ).fetchall()
            ],
            [expected_outcome, expected_result],
        )
        self.assertEqual(
            connection.execute(
                "SELECT id FROM structured_fields WHERE literature_id = ?",
                (second_literature,),
            ).fetchone()[0],
            expected_field,
        )
        self.assertEqual(
            connection.execute(
                "SELECT id FROM evidence_references WHERE literature_id = ?",
                (second_literature,),
            ).fetchone()[0],
            expected_evidence,
        )

    def test_schema_does_not_require_sqlite_json1_functions(self) -> None:
        connection = self.open_initialized_database()
        schema_text = "\n".join(
            row[0] or ""
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        ).lower()

        self.assertNotIn("json_valid(", schema_text)
        self.assertNotIn("json_extract(", schema_text)

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
        self.assertEqual(self.table_names(connection), set())

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
