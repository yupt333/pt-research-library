"""Tests for the independent Research Project repository."""

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import ResearchProject, ResearchProjectItem
from src.project_repository import (
    attach_literature_to_project,
    create_project_item,
    create_research_project,
    delete_project_item,
    delete_research_project,
    detach_literature_from_project,
    get_project_item,
    get_research_project,
    get_research_project_related_counts,
    list_literature_for_project,
    list_project_items,
    list_research_projects,
    update_project_item,
    update_research_project,
)
from src.repository import add_literature
from src.models import Literature


class TrackingConnection(sqlite3.Connection):
    def __init__(self, *args: object, **kwargs: object) -> None:
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


class ProjectRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "project.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    def add_literature(self, title: str) -> int:
        return add_literature(self.connection, Literature(title=title))

    @staticmethod
    def timestamp_is_utc(value: str | None) -> bool:
        if value is None or not value.endswith("Z"):
            return False
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True

    @staticmethod
    def snapshot_tables(
        connection: sqlite3.Connection, tables: tuple[str, ...]
    ) -> dict[str, list[tuple[object, ...]]]:
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            ]
            for table in tables
        }

    def test_project_models_expose_required_fields(self) -> None:
        project = ResearchProject(
            name="Synthetic project",
            id=3,
            objective="Objective",
            current_status="Status",
            protocol_note="Protocol",
            general_note="Note",
            created_at="created",
            updated_at="updated",
        )
        item = ResearchProjectItem(
            project_id=3,
            item_type="concept",
            content="Concept",
            id=4,
            note="Item note",
            sort_order=2,
            created_at="created",
            updated_at="updated",
        )
        self.assertEqual(project.name, "Synthetic project")
        self.assertEqual(item.project_id, project.id)
        self.assertEqual(item.item_type, "concept")

    def test_create_get_list_trim_and_timestamps(self) -> None:
        second_id = create_research_project(
            self.connection,
            "  AHD研究  ",
            "Synthetic objective",
            "Synthetic status",
            "Synthetic protocol",
            "Synthetic note",
        )
        third_id = create_research_project(self.connection, "Achilles project")

        self.assertEqual((second_id, third_id), (1, 2))
        project = get_research_project(self.connection, second_id)
        assert project is not None
        self.assertEqual(project.name, "AHD研究")
        self.assertEqual(project.objective, "Synthetic objective")
        self.assertTrue(self.timestamp_is_utc(project.created_at))
        self.assertTrue(self.timestamp_is_utc(project.updated_at))
        self.assertEqual(
            [item.id for item in list_research_projects(self.connection)],
            [second_id, third_id],
        )
        self.assertIsNone(get_research_project(self.connection, 999))

    def test_project_validation_and_case_insensitive_duplicate(self) -> None:
        create_research_project(self.connection, "Alpha Project")
        for invalid in (None, "", " \t\n"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    create_research_project(self.connection, invalid)
        with self.assertRaisesRegex(ValueError, "大文字・小文字"):
            create_research_project(self.connection, "alpha project")
        with self.assertRaisesRegex(ValueError, "objective"):
            create_research_project(self.connection, "Beta", objective=3)
        self.assertEqual(
            [project.name for project in list_research_projects(self.connection)],
            ["Alpha Project"],
        )

    def test_update_each_project_field_and_isolation(self) -> None:
        target_id = create_research_project(self.connection, "Target")
        other_id = create_research_project(
            self.connection, "Other", general_note="keep"
        )
        original = get_research_project(self.connection, target_id)
        assert original is not None
        values = {
            "name": "  Updated Target  ",
            "objective": "Objective",
            "current_status": "Free-text status",
            "protocol_note": "Protocol",
            "general_note": "General",
        }
        for field, value in values.items():
            self.assertTrue(
                update_research_project(
                    self.connection, target_id, {field: value}
                )
            )
        updated = get_research_project(self.connection, target_id)
        assert updated is not None
        self.assertEqual(updated.name, "Updated Target")
        for field in values.keys() - {"name"}:
            self.assertEqual(getattr(updated, field), values[field])
        self.assertEqual(updated.created_at, original.created_at)
        self.assertGreater(
            datetime.fromisoformat(updated.updated_at.replace("Z", "+00:00")),
            datetime.fromisoformat(original.updated_at.replace("Z", "+00:00")),
        )
        other = get_research_project(self.connection, other_id)
        assert other is not None
        self.assertEqual(other.general_note, "keep")
        self.assertFalse(
            update_research_project(self.connection, 999, {"name": "Unknown"})
        )
        with self.assertRaises(ValueError):
            update_research_project(self.connection, target_id, {"id": 8})

    def test_update_duplicate_or_invalid_value_preserves_project(self) -> None:
        first_id = create_research_project(self.connection, "First")
        second_id = create_research_project(self.connection, "Second")
        before = get_research_project(self.connection, second_id)
        with self.assertRaises(ValueError):
            update_research_project(
                self.connection, second_id, {"name": "FIRST"}
            )
        with self.assertRaises(ValueError):
            update_research_project(
                self.connection, second_id, {"current_status": False}
            )
        self.assertEqual(get_research_project(self.connection, second_id), before)
        self.assertIsNotNone(get_research_project(self.connection, first_id))

    def test_literature_many_to_many_duplicate_and_detach(self) -> None:
        first_project = create_research_project(self.connection, "First")
        second_project = create_research_project(self.connection, "Second")
        first_literature = self.add_literature("First literature")
        second_literature = self.add_literature("Second literature")

        self.assertTrue(
            attach_literature_to_project(
                self.connection, first_project, first_literature
            )
        )
        self.assertFalse(
            attach_literature_to_project(
                self.connection, first_project, first_literature
            )
        )
        self.assertTrue(
            attach_literature_to_project(
                self.connection, first_project, second_literature
            )
        )
        self.assertTrue(
            attach_literature_to_project(
                self.connection, second_project, first_literature
            )
        )
        linked = list_literature_for_project(self.connection, first_project)
        assert linked is not None
        self.assertEqual(
            [record.id for record in linked],
            [first_literature, second_literature],
        )
        self.assertTrue(
            detach_literature_from_project(
                self.connection, first_project, first_literature
            )
        )
        self.assertFalse(
            detach_literature_from_project(
                self.connection, first_project, first_literature
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature"
            ).fetchone()[0],
            2,
        )

    def test_unknown_relation_parents_are_clear(self) -> None:
        project_id = create_research_project(self.connection, "Project")
        literature_id = self.add_literature("Literature")
        for operation, args, message in (
            (attach_literature_to_project, (999, literature_id), "Project ID"),
            (attach_literature_to_project, (project_id, 999), "Literature ID"),
            (detach_literature_from_project, (999, literature_id), "Project ID"),
            (detach_literature_from_project, (project_id, 999), "Literature ID"),
        ):
            with self.subTest(operation=operation.__name__, message=message):
                with self.assertRaisesRegex(ValueError, message):
                    operation(self.connection, *args)

    def test_parent_deletions_only_cascade_relation_side(self) -> None:
        project_id = create_research_project(self.connection, "Project")
        literature_id = self.add_literature("Literature")
        attach_literature_to_project(
            self.connection, project_id, literature_id
        )
        self.assertTrue(delete_research_project(self.connection, project_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature WHERE id = ?",
                (literature_id,),
            ).fetchone()[0],
            1,
        )

        project_id = create_research_project(self.connection, "Kept Project")
        attach_literature_to_project(
            self.connection, project_id, literature_id
        )
        self.connection.execute(
            "DELETE FROM literature WHERE id = ?", (literature_id,)
        )
        self.connection.commit()
        self.assertIsNotNone(get_research_project(self.connection, project_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM research_project_literature"
            ).fetchone()[0],
            0,
        )

    def test_items_support_three_types_auto_order_and_determinism(self) -> None:
        project_id = create_research_project(self.connection, "Items")
        ids = []
        for item_type, content in (
            ("concept", " First concept "),
            ("concept", "Second concept"),
            ("unresolved_question", "Question"),
            ("next_action", "Action"),
        ):
            ids.append(
                create_project_item(
                    self.connection,
                    project_id,
                    item_type,
                    content,
                    "Synthetic note",
                )
            )
        items = list_project_items(self.connection, project_id)
        assert items is not None
        self.assertEqual([item.id for item in items], ids)
        self.assertEqual(items[0].content, "First concept")
        self.assertEqual([item.sort_order for item in items], [0, 1, 0, 0])
        concept_items = list_project_items(
            self.connection, project_id, item_type="concept"
        )
        assert concept_items is not None
        self.assertEqual([item.id for item in concept_items], ids[:2])

    def test_item_validation_update_delete_and_timestamps(self) -> None:
        project_id = create_research_project(self.connection, "Items")
        item_id = create_project_item(
            self.connection, project_id, "concept", "Concept"
        )
        original = get_project_item(self.connection, item_id)
        assert original is not None
        self.assertTrue(self.timestamp_is_utc(original.created_at))
        self.assertTrue(
            update_project_item(
                self.connection,
                item_id,
                {"content": " Updated ", "note": "Note", "sort_order": 4},
            )
        )
        updated = get_project_item(self.connection, item_id)
        assert updated is not None
        self.assertEqual(updated.content, "Updated")
        self.assertEqual(updated.note, "Note")
        self.assertEqual(updated.sort_order, 4)
        self.assertEqual(updated.item_type, "concept")
        self.assertEqual(updated.created_at, original.created_at)
        self.assertGreater(
            datetime.fromisoformat(updated.updated_at.replace("Z", "+00:00")),
            datetime.fromisoformat(original.updated_at.replace("Z", "+00:00")),
        )
        self.assertTrue(delete_project_item(self.connection, item_id))
        self.assertFalse(delete_project_item(self.connection, item_id))
        self.assertIsNone(get_project_item(self.connection, item_id))

    def test_item_sqlite_failure_rolls_back_without_partial_write(self) -> None:
        project_id = create_research_project(self.connection, "Rollback")
        self.connection.execute(
            """
            CREATE TRIGGER fail_project_item_insert
            BEFORE INSERT ON research_project_items
            BEGIN
                SELECT RAISE(ABORT, 'forced project item failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaises(sqlite3.DatabaseError):
            create_project_item(
                self.connection, project_id, "concept", "Not persisted"
            )

        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM research_project_items"
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(get_research_project(self.connection, project_id))

    def test_item_rejects_invalid_type_content_sort_and_unknown_project(self) -> None:
        project_id = create_research_project(self.connection, "Items")
        for kwargs in (
            {"item_type": "invalid", "content": "Content"},
            {"item_type": "concept", "content": " \n"},
            {"item_type": "concept", "content": "Content", "sort_order": -1},
            {"item_type": "concept", "content": "Content", "sort_order": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    create_project_item(
                        self.connection,
                        project_id,
                        kwargs.pop("item_type"),
                        kwargs.pop("content"),
                        **kwargs,
                    )
        with self.assertRaisesRegex(ValueError, "Project ID"):
            create_project_item(self.connection, 999, "concept", "Content")

    def test_related_counts_and_project_delete_item_cascade(self) -> None:
        project_id = create_research_project(self.connection, "Counts")
        literature_id = self.add_literature("Linked")
        attach_literature_to_project(
            self.connection, project_id, literature_id
        )
        for item_type in (
            "concept",
            "unresolved_question",
            "next_action",
        ):
            create_project_item(
                self.connection, project_id, item_type, f"{item_type} item"
            )
        self.assertEqual(
            get_research_project_related_counts(self.connection, project_id),
            {
                "literature_count": 1,
                "concept_count": 1,
                "unresolved_question_count": 1,
                "next_action_count": 1,
            },
        )
        self.assertIsNone(
            get_research_project_related_counts(self.connection, 999)
        )
        self.assertTrue(delete_research_project(self.connection, project_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM research_project_items"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM research_project_literature"
            ).fetchone()[0],
            0,
        )

    def test_project_delete_adversarially_preserves_all_nonproject_data(self) -> None:
        target_project = create_research_project(self.connection, "Target")
        other_project = create_research_project(self.connection, "Other")
        literature_id = self.add_literature("Protected synthetic literature")
        tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)", ("protected-tag",)
        ).lastrowid
        self.connection.execute(
            "INSERT INTO literature_tags VALUES (?, ?)",
            (literature_id, tag_id),
        )
        self.connection.execute(
            """
            INSERT INTO usage_history (
                literature_id, usage_type, project_name
            ) VALUES (?, ?, ?)
            """,
            (literature_id, "protected-use", "Free-text project label"),
        )
        entity_id = self.connection.execute(
            """
            INSERT INTO structured_entities (
                literature_id, entity_type, verification
            ) VALUES (?, ?, ?)
            """,
            (literature_id, "concept", "ai_unverified"),
        ).lastrowid
        field_id = self.connection.execute(
            """
            INSERT INTO structured_fields (
                literature_id, entity_id, field_key, content_role,
                value_json, availability, verification
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                literature_id,
                entity_id,
                "name",
                "source_fact",
                '"Synthetic literature concept"',
                "reported",
                "ai_unverified",
            ),
        ).lastrowid
        evidence_id = self.connection.execute(
            """
            INSERT INTO evidence_references (
                literature_id, pdf_page, verification
            ) VALUES (?, ?, ?)
            """,
            (literature_id, 1, "ai_unverified"),
        ).lastrowid
        self.connection.execute(
            "INSERT INTO structured_field_evidence VALUES (?, ?, ?)",
            (literature_id, field_id, evidence_id),
        )
        self.connection.execute(
            "INSERT INTO structured_entity_evidence VALUES (?, ?, ?)",
            (literature_id, entity_id, evidence_id),
        )
        self.connection.commit()
        attach_literature_to_project(
            self.connection, target_project, literature_id
        )
        attach_literature_to_project(
            self.connection, other_project, literature_id
        )
        create_project_item(
            self.connection, target_project, "concept", "Delete me"
        )
        create_project_item(
            self.connection, other_project, "concept", "Keep me"
        )
        protected_tables = (
            "literature",
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
            "tags",
            "literature_tags",
            "usage_history",
        )
        before = self.snapshot_tables(self.connection, protected_tables)

        self.assertTrue(
            delete_research_project(self.connection, target_project)
        )

        self.assertEqual(
            self.snapshot_tables(self.connection, protected_tables), before
        )
        other = get_research_project(self.connection, other_project)
        self.assertIsNotNone(other)
        other_items = list_project_items(self.connection, other_project)
        assert other_items is not None
        self.assertEqual([item.content for item in other_items], ["Keep me"])
        other_literature = list_literature_for_project(
            self.connection, other_project
        )
        assert other_literature is not None
        self.assertEqual([item.id for item in other_literature], [literature_id])
        self.assertEqual(
            self.connection.execute(
                "SELECT project_name FROM usage_history"
            ).fetchone()[0],
            "Free-text project label",
        )

    def test_active_caller_transaction_rejects_all_writes_but_allows_reads(
        self,
    ) -> None:
        project_id = create_research_project(self.connection, "Transaction")
        literature_id = self.add_literature("Transaction literature")
        item_id = create_project_item(
            self.connection, project_id, "concept", "Transaction item"
        )
        self.connection.close()
        tracking = sqlite3.connect(
            self.database_path, factory=TrackingConnection
        )
        tracking.row_factory = sqlite3.Row
        sqlite3.Connection.execute(tracking, "PRAGMA foreign_keys = ON")
        self.connection = tracking
        self.addCleanup(sqlite3.Connection.close, tracking)
        sqlite3.Connection.execute(
            tracking, "INSERT INTO tags (name) VALUES (?)", ("pending",)
        )
        self.assertTrue(tracking.in_transaction)

        operations = (
            lambda: create_research_project(tracking, "Blocked"),
            lambda: update_research_project(
                tracking, project_id, {"name": "Blocked"}
            ),
            lambda: delete_research_project(tracking, project_id),
            lambda: attach_literature_to_project(
                tracking, project_id, literature_id
            ),
            lambda: detach_literature_from_project(
                tracking, project_id, literature_id
            ),
            lambda: create_project_item(
                tracking, project_id, "concept", "Blocked"
            ),
            lambda: update_project_item(
                tracking, item_id, {"content": "Blocked"}
            ),
            lambda: delete_project_item(tracking, item_id),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, "caller transaction"):
                    operation()

        self.assertEqual(get_research_project(tracking, project_id).id, project_id)
        self.assertEqual(len(list_research_projects(tracking)), 1)
        self.assertEqual(len(list_literature_for_project(tracking, project_id)), 0)
        self.assertEqual(len(list_project_items(tracking, project_id)), 1)
        self.assertEqual(tracking.commit_calls, 0)
        self.assertEqual(tracking.rollback_calls, 0)
        self.assertEqual(tracking.close_calls, 0)
        self.assertTrue(tracking.in_transaction)
        self.assertEqual(
            tracking.execute(
                "SELECT COUNT(*) FROM tags WHERE name = ?", ("pending",)
            ).fetchone()[0],
            1,
        )

    def test_invalid_ids_and_sql_column_injection_are_rejected(self) -> None:
        project_id = create_research_project(self.connection, "Validation")
        item_id = create_project_item(
            self.connection, project_id, "concept", "Item"
        )
        for invalid in (True, 0, -1, "1"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    get_research_project(self.connection, invalid)
        with self.assertRaises(ValueError):
            update_research_project(
                self.connection,
                project_id,
                {"name = 'hacked' --": "value"},
            )
        with self.assertRaises(ValueError):
            update_project_item(
                self.connection,
                item_id,
                {"content = 'hacked' --": "value"},
            )
        self.assertIsNotNone(get_research_project(self.connection, project_id))
        self.assertIsNotNone(get_project_item(self.connection, item_id))


if __name__ == "__main__":
    unittest.main()
