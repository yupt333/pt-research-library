"""Tests for usage-history repository operations."""

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import Literature, UsageHistory
from src.repository import (
    add_literature,
    create_usage_history,
    delete_literature,
    delete_usage_history,
    get_literature,
    get_usage_history,
    list_usage_history_for_literature,
    update_usage_history,
)


class UsageHistoryRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "usage-history.db"
        )
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)
        self.literature_id = add_literature(
            self.connection,
            Literature(title="Usage-history literature"),
        )

    def raw_row(self, usage_history_id: int) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM usage_history WHERE id = ?",
            (usage_history_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return dict(row)

    def create_populated_history(self) -> int:
        return create_usage_history(
            self.connection,
            self.literature_id,
            "note",
            project_name="AHD article",
            usage_note="Cited in methods",
            used_at="2026-07-26",
        )

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

    def rows_from_separate_connection(
        self,
        sql: str,
        parameters: tuple[object, ...] = (),
    ) -> list[tuple[object, ...]]:
        observer = connect_database(self.database_path)
        try:
            return [
                tuple(row)
                for row in observer.execute(sql, parameters).fetchall()
            ]
        finally:
            observer.close()

    def test_create_usage_history_sets_all_fields_and_trims_usage_type(self) -> None:
        history_id = create_usage_history(
            self.connection,
            self.literature_id,
            "  学会発表  ",
            project_name="Shoulder conference",
            usage_note="Slide 5",
            used_at="2024-02-29",
        )

        stored = get_usage_history(self.connection, history_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(
            stored,
            UsageHistory(
                id=history_id,
                literature_id=self.literature_id,
                usage_type="学会発表",
                project_name="Shoulder conference",
                usage_note="Slide 5",
                used_at="2024-02-29",
                created_at=stored.created_at,
            ),
        )
        self.assertIsNotNone(stored.created_at)
        assert stored.created_at is not None
        self.assertTrue(stored.created_at.endswith("Z"))
        parsed = datetime.fromisoformat(stored.created_at.replace("Z", "+00:00"))
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))

    def test_create_usage_history_accepts_none_for_optional_fields(self) -> None:
        history_id = create_usage_history(
            self.connection,
            self.literature_id,
            "大学院研究",
        )

        stored = get_usage_history(self.connection, history_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertIsNone(stored.project_name)
        self.assertIsNone(stored.usage_note)
        self.assertIsNone(stored.used_at)

    def test_create_usage_history_rejects_unknown_literature(self) -> None:
        self.create_populated_history()
        before = self.database_snapshot()

        with self.assertRaisesRegex(ValueError, "文献ID"):
            create_usage_history(self.connection, 999999, "note")

        self.assertEqual(self.database_snapshot(), before)

    def test_create_usage_history_rejects_invalid_usage_type(self) -> None:
        for usage_type in ("", "  ", "\t\n", None, 1, True, ["note"]):
            with self.subTest(usage_type=repr(usage_type)):
                with self.assertRaisesRegex(ValueError, "usage_type"):
                    create_usage_history(
                        self.connection,
                        self.literature_id,
                        usage_type,
                    )

        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history"
            ).fetchone()[0],
            0,
        )

    def test_create_usage_history_rejects_non_string_optional_text(self) -> None:
        for field, value in (
            ("project_name", 1),
            ("project_name", False),
            ("usage_note", 1.5),
            ("usage_note", []),
        ):
            with self.subTest(field=field, value=repr(value)):
                arguments = {field: value}
                with self.assertRaisesRegex(ValueError, field):
                    create_usage_history(
                        self.connection,
                        self.literature_id,
                        "note",
                        **arguments,
                    )

    def test_create_usage_history_accepts_real_exact_dates(self) -> None:
        for used_at in ("2026-01-01", "2024-02-29", None):
            with self.subTest(used_at=used_at):
                history_id = create_usage_history(
                    self.connection,
                    self.literature_id,
                    "note",
                    used_at=used_at,
                )
                stored = get_usage_history(self.connection, history_id)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.used_at, used_at)

    def test_create_usage_history_rejects_invalid_dates_and_formats(self) -> None:
        invalid_dates = (
            "",
            " ",
            "2026-2-03",
            "26-02-03",
            "2026/02/03",
            "2026-02-03T00:00:00",
            "2026-02-30",
            "2025-02-29",
            date(2026, 2, 3),
            datetime(2026, 2, 3),
            True,
            False,
            20260203,
            2026.0203,
        )

        for used_at in invalid_dates:
            with self.subTest(used_at=repr(used_at)):
                with self.assertRaisesRegex(ValueError, "used_at"):
                    create_usage_history(
                        self.connection,
                        self.literature_id,
                        "note",
                        used_at=used_at,
                    )

    def test_create_validation_failures_preserve_all_usage_history(self) -> None:
        self.create_populated_history()
        other_literature_id = add_literature(
            self.connection,
            Literature(title="Other validation literature"),
        )
        create_usage_history(
            self.connection,
            other_literature_id,
            "論文",
        )
        before = self.database_snapshot()
        invalid_arguments = (
            {"usage_type": ""},
            {"usage_type": None},
            {"usage_type": "note", "project_name": 1},
            {"usage_type": "note", "usage_note": False},
            {"usage_type": "note", "used_at": "2025-02-29"},
            {"usage_type": "note", "used_at": date(2026, 2, 3)},
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=repr(arguments)):
                with self.assertRaises(ValueError):
                    create_usage_history(
                        self.connection,
                        self.literature_id,
                        **arguments,
                    )
                self.assertEqual(self.database_snapshot(), before)

    def test_create_usage_history_sql_failure_rolls_back(self) -> None:
        kept_history_id = self.create_populated_history()
        self.connection.execute(
            """
            CREATE TRIGGER force_usage_insert_failure
            BEFORE INSERT ON usage_history
            BEGIN
                SELECT RAISE(ABORT, 'forced usage insert failure');
            END
            """
        )
        self.connection.commit()
        before = self.database_snapshot()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced usage insert failure",
        ):
            create_usage_history(self.connection, self.literature_id, "note")

        self.assertEqual(self.database_snapshot(), before)
        self.assertIsNotNone(get_usage_history(self.connection, kept_history_id))

    def test_create_usage_history_commits_for_a_separate_connection(self) -> None:
        history_id = self.create_populated_history()

        self.assertEqual(
            self.rows_from_separate_connection(
                """
                SELECT id, literature_id, usage_type, project_name,
                       usage_note, used_at
                FROM usage_history
                WHERE id = ?
                """,
                (history_id,),
            ),
            [
                (
                    history_id,
                    self.literature_id,
                    "note",
                    "AHD article",
                    "Cited in methods",
                    "2026-07-26",
                )
            ],
        )

    def test_get_usage_history_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(get_usage_history(self.connection, 999999))

    def test_usage_history_reads_do_not_commit_pending_changes(self) -> None:
        cursor = self.connection.execute(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (self.literature_id, "pending"),
        )

        self.assertIsNotNone(get_usage_history(self.connection, cursor.lastrowid))
        self.assertEqual(
            len(
                list_usage_history_for_literature(
                    self.connection,
                    self.literature_id,
                )
                or []
            ),
            1,
        )
        self.connection.rollback()

        self.assertIsNone(get_usage_history(self.connection, cursor.lastrowid))

    def test_list_returns_none_for_unknown_literature_and_empty_for_no_rows(
        self,
    ) -> None:
        self.assertIsNone(
            list_usage_history_for_literature(self.connection, 999999)
        )
        self.assertEqual(
            list_usage_history_for_literature(
                self.connection,
                self.literature_id,
            ),
            [],
        )

    def test_list_restores_all_fields_nulls_in_id_order_and_is_isolated(
        self,
    ) -> None:
        first_id = self.create_populated_history()
        second_id = create_usage_history(
            self.connection,
            self.literature_id,
            "論文",
        )
        other_literature_id = add_literature(
            self.connection,
            Literature(title="Other usage-history literature"),
        )
        other_id = create_usage_history(
            self.connection,
            other_literature_id,
            "研究計画",
            project_name="Other project",
        )

        listed = list_usage_history_for_literature(
            self.connection,
            self.literature_id,
        )

        self.assertIsNotNone(listed)
        assert listed is not None
        self.assertEqual([item.id for item in listed], [first_id, second_id])
        self.assertNotIn(other_id, [item.id for item in listed])
        self.assertEqual(
            listed[0],
            get_usage_history(self.connection, first_id),
        )
        self.assertIsNone(listed[1].project_name)
        self.assertIsNone(listed[1].usage_note)
        self.assertIsNone(listed[1].used_at)

    def test_update_usage_type_trims_and_preserves_unspecified_fields(self) -> None:
        history_id = self.create_populated_history()
        before = self.raw_row(history_id)

        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {"usage_type": "  学会発表  "},
            )
        )

        after = self.raw_row(history_id)
        expected = before.copy()
        expected["usage_type"] = "学会発表"
        self.assertEqual(after, expected)

    def test_update_used_at_accepts_real_date_and_none(self) -> None:
        history_id = self.create_populated_history()

        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {"used_at": "2024-02-29"},
            )
        )
        self.assertEqual(
            get_usage_history(self.connection, history_id).used_at,
            "2024-02-29",
        )
        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {"used_at": None},
            )
        )
        self.assertIsNone(get_usage_history(self.connection, history_id).used_at)

    def test_update_optional_text_fields_accept_strings_and_none(self) -> None:
        history_id = self.create_populated_history()

        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {
                    "project_name": None,
                    "usage_note": "Updated note",
                },
            )
        )

        stored = get_usage_history(self.connection, history_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertIsNone(stored.project_name)
        self.assertEqual(stored.usage_note, "Updated note")

    def test_update_multiple_fields_is_atomic_and_preserves_identity(self) -> None:
        history_id = self.create_populated_history()
        before = self.raw_row(history_id)

        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {
                    "usage_type": " 論文 ",
                    "project_name": "Updated project",
                    "usage_note": None,
                    "used_at": "2026-12-31",
                },
            )
        )

        after = self.raw_row(history_id)
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["literature_id"], before["literature_id"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["usage_type"], "論文")
        self.assertEqual(after["project_name"], "Updated project")
        self.assertIsNone(after["usage_note"])
        self.assertEqual(after["used_at"], "2026-12-31")

    def test_update_rejects_empty_updates_and_unknown_id_returns_false(self) -> None:
        with self.assertRaisesRegex(ValueError, "更新対象を1項目以上"):
            update_usage_history(self.connection, self.create_populated_history(), {})

        self.assertFalse(
            update_usage_history(
                self.connection,
                999999,
                {"usage_type": "note"},
            )
        )

    def test_update_rejects_forbidden_and_injected_column_names(self) -> None:
        history_id = self.create_populated_history()
        before = self.raw_row(history_id)
        forbidden = (
            "id",
            "literature_id",
            "created_at",
            "unknown",
            "usage_type = ?; DROP TABLE usage_history; --",
        )

        for column in forbidden:
            with self.subTest(column=column):
                with self.assertRaisesRegex(ValueError, "更新できない項目"):
                    update_usage_history(
                        self.connection,
                        history_id,
                        {column: "not allowed"},
                    )
                self.assertEqual(self.raw_row(history_id), before)

    def test_invalid_update_values_preserve_entire_row(self) -> None:
        history_id = self.create_populated_history()
        other_literature_id = add_literature(
            self.connection,
            Literature(title="Other invalid update literature"),
        )
        create_usage_history(
            self.connection,
            other_literature_id,
            "学会発表",
        )
        before = self.database_snapshot()
        invalid_updates = (
            {"usage_type": ""},
            {"usage_type": "  "},
            {"usage_type": None},
            {"usage_type": 1},
            {"project_name": 1},
            {"usage_note": False},
            {"used_at": ""},
            {"used_at": "2026-02-30"},
            {"used_at": "2026/02/03"},
            {"usage_type": "valid", "used_at": "2026-02-30"},
        )

        for updates in invalid_updates:
            with self.subTest(updates=repr(updates)):
                with self.assertRaises(ValueError):
                    update_usage_history(
                        self.connection,
                        history_id,
                        updates,
                    )
                self.assertEqual(self.database_snapshot(), before)

    def test_update_sql_failure_rolls_back_entire_row(self) -> None:
        history_id = self.create_populated_history()
        other_literature_id = add_literature(
            self.connection,
            Literature(title="Other update rollback literature"),
        )
        create_usage_history(
            self.connection,
            other_literature_id,
            "学会発表",
        )
        self.connection.execute(
            """
            CREATE TRIGGER force_usage_update_failure
            BEFORE UPDATE ON usage_history
            BEGIN
                SELECT RAISE(ABORT, 'forced usage update failure');
            END
            """
        )
        self.connection.commit()
        before = self.database_snapshot()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced usage update failure",
        ):
            update_usage_history(
                self.connection,
                history_id,
                {
                    "usage_type": "論文",
                    "project_name": "Must roll back",
                },
            )

        self.assertEqual(self.database_snapshot(), before)

    def test_invalid_update_executes_no_update_statement(self) -> None:
        history_id = self.create_populated_history()
        traced_statements: list[str] = []
        self.connection.set_trace_callback(traced_statements.append)
        try:
            with self.assertRaises(ValueError):
                update_usage_history(
                    self.connection,
                    history_id,
                    {
                        "usage_type": "論文",
                        "used_at": "2025-02-29",
                    },
                )
        finally:
            self.connection.set_trace_callback(None)

        self.assertFalse(
            any(
                statement.lstrip().upper().startswith("UPDATE USAGE_HISTORY")
                for statement in traced_statements
            )
        )

    def test_update_commits_and_changes_no_other_history(self) -> None:
        history_id = self.create_populated_history()
        other_id = create_usage_history(
            self.connection,
            self.literature_id,
            "論文",
            project_name="Unchanged project",
            usage_note="Unchanged note",
            used_at="2026-01-01",
        )
        other_before = self.raw_row(other_id)

        self.assertTrue(
            update_usage_history(
                self.connection,
                history_id,
                {
                    "usage_type": "学会発表",
                    "project_name": "Committed project",
                },
            )
        )

        self.assertEqual(
            self.rows_from_separate_connection(
                """
                SELECT usage_type, project_name, usage_note, used_at
                FROM usage_history
                WHERE id = ?
                """,
                (history_id,),
            ),
            [
                (
                    "学会発表",
                    "Committed project",
                    "Cited in methods",
                    "2026-07-26",
                )
            ],
        )
        self.assertEqual(self.raw_row(other_id), other_before)

    def test_delete_usage_history_removes_only_target_and_keeps_literature(
        self,
    ) -> None:
        deleted_id = self.create_populated_history()
        kept_same_literature_id = create_usage_history(
            self.connection,
            self.literature_id,
            "論文",
        )
        other_literature_id = add_literature(
            self.connection,
            Literature(title="Other delete literature"),
        )
        kept_other_literature_id = create_usage_history(
            self.connection,
            other_literature_id,
            "学会発表",
        )

        self.assertTrue(delete_usage_history(self.connection, deleted_id))

        self.assertIsNone(get_usage_history(self.connection, deleted_id))
        self.assertIsNotNone(
            get_usage_history(self.connection, kept_same_literature_id)
        )
        self.assertIsNotNone(
            get_usage_history(self.connection, kept_other_literature_id)
        )
        self.assertIsNotNone(get_literature(self.connection, self.literature_id))
        self.assertIsNotNone(get_literature(self.connection, other_literature_id))
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT id FROM usage_history WHERE id = ?",
                (deleted_id,),
            ),
            [],
        )
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT id FROM usage_history WHERE id IN (?, ?) ORDER BY id",
                (kept_same_literature_id, kept_other_literature_id),
            ),
            [(kept_same_literature_id,), (kept_other_literature_id,)],
        )

    def test_delete_usage_history_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(delete_usage_history(self.connection, 999999))

    def test_delete_usage_history_sql_failure_rolls_back(self) -> None:
        history_id = self.create_populated_history()
        other_history_id = create_usage_history(
            self.connection,
            self.literature_id,
            "論文",
        )
        self.connection.execute(
            """
            CREATE TRIGGER force_usage_delete_failure
            BEFORE DELETE ON usage_history
            BEGIN
                SELECT RAISE(ABORT, 'forced usage delete failure');
            END
            """
        )
        self.connection.commit()
        before = self.database_snapshot()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced usage delete failure",
        ):
            delete_usage_history(self.connection, history_id)

        self.assertEqual(self.database_snapshot(), before)
        self.assertIsNotNone(get_literature(self.connection, self.literature_id))
        self.assertIsNotNone(get_usage_history(self.connection, other_history_id))

    def test_literature_deletion_still_cascades_usage_history(self) -> None:
        first_id = self.create_populated_history()
        second_id = create_usage_history(
            self.connection,
            self.literature_id,
            "論文",
        )

        self.assertTrue(delete_literature(self.connection, self.literature_id))

        self.assertIsNone(get_usage_history(self.connection, first_id))
        self.assertIsNone(get_usage_history(self.connection, second_id))


if __name__ == "__main__":
    unittest.main()
