"""Tests for tag repository operations."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import Literature, Tag
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    delete_tag,
    detach_tag_from_literature,
    get_literature,
    get_tag,
    list_tags,
    list_tags_for_literature,
    rename_tag,
)


class TagRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "tags.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    def add_literature(self, title: str) -> int:
        return add_literature(self.connection, Literature(title=title))

    def mapping_count(self, literature_id: int, tag_id: int) -> int:
        return self.connection.execute(
            """
            SELECT COUNT(*)
            FROM literature_tags
            WHERE literature_id = ? AND tag_id = ?
            """,
            (literature_id, tag_id),
        ).fetchone()[0]

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

    def test_no_default_tags_are_created_and_empty_list_is_returned(self) -> None:
        self.assertEqual(list_tags(self.connection), [])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            0,
        )

    def test_create_tag_trims_name_and_get_tag_restores_all_fields(self) -> None:
        tag_id = create_tag(self.connection, "  ultrasound \n")

        self.assertGreater(tag_id, 0)
        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="ultrasound"),
        )

    def test_get_tag_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(get_tag(self.connection, 999999))

    def test_create_tag_rejects_empty_and_non_string_names(self) -> None:
        invalid_names = ("", "   ", "\t\n", None, 1, True, ["tag"])

        for name in invalid_names:
            with self.subTest(name=repr(name)):
                with self.assertRaisesRegex(ValueError, "タグ名"):
                    create_tag(self.connection, name)

        self.assertEqual(list_tags(self.connection), [])

    def test_case_insensitive_duplicate_returns_existing_id_and_one_row(self) -> None:
        original_id = create_tag(self.connection, "Shoulder")

        duplicate_id = create_tag(self.connection, "  shoulder ")

        self.assertEqual(duplicate_id, original_id)
        self.assertEqual(
            list_tags(self.connection),
            [Tag(id=original_id, name="Shoulder")],
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
            1,
        )

    def test_list_tags_uses_deterministic_case_insensitive_order(self) -> None:
        tag_ids = {
            name: create_tag(self.connection, name)
            for name in ("gamma", "Beta", "alpha")
        }

        self.assertEqual(
            list_tags(self.connection),
            [
                Tag(id=tag_ids["alpha"], name="alpha"),
                Tag(id=tag_ids["Beta"], name="Beta"),
                Tag(id=tag_ids["gamma"], name="gamma"),
            ],
        )

    def test_tag_reads_do_not_commit_pending_changes(self) -> None:
        cursor = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-tag",),
        )

        self.assertEqual(
            get_tag(self.connection, cursor.lastrowid),
            Tag(id=cursor.lastrowid, name="pending-tag"),
        )
        self.assertEqual(len(list_tags(self.connection)), 1)
        self.connection.rollback()

        self.assertEqual(list_tags(self.connection), [])

    def test_create_tag_sql_failure_rolls_back(self) -> None:
        self.connection.execute(
            """
            CREATE TRIGGER force_tag_insert_failure
            BEFORE INSERT ON tags
            BEGIN
                SELECT RAISE(ABORT, 'forced tag insert failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced tag insert failure",
        ):
            create_tag(self.connection, "rollback-tag")

        self.assertEqual(list_tags(self.connection), [])

    def test_rename_tag_trims_name_and_preserves_id_and_relationship(self) -> None:
        literature_id = self.add_literature("Rename relationship")
        tag_id = create_tag(self.connection, "original")
        self.assertTrue(
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        )

        self.assertTrue(rename_tag(self.connection, tag_id, "  Renamed  "))

        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="Renamed"),
        )
        self.assertEqual(
            list_tags_for_literature(self.connection, literature_id),
            [Tag(id=tag_id, name="Renamed")],
        )
        self.assertEqual(self.mapping_count(literature_id, tag_id), 1)

    def test_rename_tag_to_exact_same_name_returns_true(self) -> None:
        tag_id = create_tag(self.connection, "Shoulder")

        self.assertTrue(rename_tag(self.connection, tag_id, "Shoulder"))
        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="Shoulder"),
        )

    def test_case_only_rename_preserves_id_and_relationship(self) -> None:
        literature_id = self.add_literature("Case-only rename")
        tag_id = create_tag(self.connection, "Shoulder")
        attach_tag_to_literature(self.connection, literature_id, tag_id)

        self.assertTrue(rename_tag(self.connection, tag_id, "shoulder"))

        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="shoulder"),
        )
        self.assertEqual(
            list_tags_for_literature(self.connection, literature_id),
            [Tag(id=tag_id, name="shoulder")],
        )
        self.assertEqual(self.mapping_count(literature_id, tag_id), 1)

    def test_rename_tag_rejects_invalid_names_and_preserves_original(self) -> None:
        tag_id = create_tag(self.connection, "original")

        for name in ("", "  ", None, 7, False):
            with self.subTest(name=repr(name)):
                with self.assertRaisesRegex(ValueError, "タグ名"):
                    rename_tag(self.connection, tag_id, name)
                self.assertEqual(
                    get_tag(self.connection, tag_id),
                    Tag(id=tag_id, name="original"),
                )

    def test_rename_tag_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(rename_tag(self.connection, 999999, "valid-name"))

    def test_rename_tag_rejects_case_insensitive_duplicate_atomically(self) -> None:
        literature_id = self.add_literature("Duplicate rename")
        other_literature_id = self.add_literature("Other duplicate rename")
        first_id = create_tag(self.connection, "Shoulder")
        second_id = create_tag(self.connection, "Ultrasound")
        other_id = create_tag(self.connection, "Reliability")
        attach_tag_to_literature(self.connection, literature_id, first_id)
        attach_tag_to_literature(self.connection, literature_id, second_id)
        attach_tag_to_literature(
            self.connection,
            other_literature_id,
            other_id,
        )
        before = self.database_snapshot()

        with self.assertRaisesRegex(ValueError, "既に存在"):
            rename_tag(self.connection, second_id, " shoulder ")

        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(
            get_tag(self.connection, first_id),
            Tag(id=first_id, name="Shoulder"),
        )
        self.assertEqual(
            get_tag(self.connection, second_id),
            Tag(id=second_id, name="Ultrasound"),
        )
        self.assertEqual(self.mapping_count(literature_id, second_id), 1)

    def test_rename_tag_sql_failure_rolls_back(self) -> None:
        tag_id = create_tag(self.connection, "before")
        self.connection.execute(
            """
            CREATE TRIGGER force_tag_update_failure
            BEFORE UPDATE ON tags
            BEGIN
                SELECT RAISE(ABORT, 'forced tag update failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced tag update failure",
        ):
            rename_tag(self.connection, tag_id, "after")

        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="before"),
        )

    def test_attach_tag_converts_only_missing_parent_foreign_keys(self) -> None:
        literature_id = self.add_literature("Attach validation")
        other_literature_id = self.add_literature("Other attach validation")
        tag_id = create_tag(self.connection, "attach")
        other_tag_id = create_tag(self.connection, "other-attach")
        attach_tag_to_literature(
            self.connection,
            other_literature_id,
            other_tag_id,
        )
        before = self.database_snapshot()

        for missing_literature_id, missing_tag_id in (
            (999999, tag_id),
            (literature_id, 999999),
        ):
            with self.subTest(
                literature_id=missing_literature_id,
                tag_id=missing_tag_id,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "literature_idまたはtag_id",
                ):
                    attach_tag_to_literature(
                        self.connection,
                        missing_literature_id,
                        missing_tag_id,
                    )
                self.assertEqual(self.database_snapshot(), before)

        self.assertEqual(self.mapping_count(other_literature_id, other_tag_id), 1)

    def test_attach_tag_is_idempotent_and_preserves_unique_mapping(self) -> None:
        literature_id = self.add_literature("Attach once")
        tag_id = create_tag(self.connection, "reliability")

        self.assertTrue(
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        )
        self.assertFalse(
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        )
        self.assertEqual(self.mapping_count(literature_id, tag_id), 1)

    def test_attach_tag_sql_failure_rolls_back(self) -> None:
        literature_id = self.add_literature("Attach rollback")
        tag_id = create_tag(self.connection, "attach-rollback")
        self.connection.execute(
            """
            CREATE TRIGGER force_mapping_insert_failure
            BEFORE INSERT ON literature_tags
            BEGIN
                SELECT RAISE(ABORT, 'forced mapping insert failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced mapping insert failure",
        ):
            attach_tag_to_literature(self.connection, literature_id, tag_id)

        self.assertEqual(self.mapping_count(literature_id, tag_id), 0)

    def test_list_tags_for_unknown_literature_returns_none(self) -> None:
        self.assertIsNone(
            list_tags_for_literature(self.connection, 999999)
        )

    def test_list_tags_for_existing_literature_without_tags_returns_empty_list(
        self,
    ) -> None:
        literature_id = self.add_literature("No attached tags")

        self.assertEqual(
            list_tags_for_literature(self.connection, literature_id),
            [],
        )

    def test_list_tags_for_literature_is_sorted_and_isolated(self) -> None:
        first_literature_id = self.add_literature("First tag list")
        second_literature_id = self.add_literature("Second tag list")
        beta_id = create_tag(self.connection, "Beta")
        alpha_id = create_tag(self.connection, "alpha")
        other_id = create_tag(self.connection, "other")
        attach_tag_to_literature(self.connection, first_literature_id, beta_id)
        attach_tag_to_literature(self.connection, first_literature_id, alpha_id)
        attach_tag_to_literature(self.connection, second_literature_id, other_id)

        self.assertEqual(
            list_tags_for_literature(self.connection, first_literature_id),
            [
                Tag(id=alpha_id, name="alpha"),
                Tag(id=beta_id, name="Beta"),
            ],
        )
        self.assertEqual(
            list_tags_for_literature(self.connection, second_literature_id),
            [Tag(id=other_id, name="other")],
        )

    def test_detach_tag_removes_only_requested_mapping_and_keeps_parents(self) -> None:
        first_literature_id = self.add_literature("Detach first")
        second_literature_id = self.add_literature("Detach second")
        tag_id = create_tag(self.connection, "shared")
        attach_tag_to_literature(self.connection, first_literature_id, tag_id)
        attach_tag_to_literature(self.connection, second_literature_id, tag_id)

        self.assertTrue(
            detach_tag_from_literature(
                self.connection,
                first_literature_id,
                tag_id,
            )
        )
        self.assertFalse(
            detach_tag_from_literature(
                self.connection,
                first_literature_id,
                tag_id,
            )
        )

        self.assertEqual(self.mapping_count(first_literature_id, tag_id), 0)
        self.assertEqual(self.mapping_count(second_literature_id, tag_id), 1)
        self.assertIsNotNone(get_literature(self.connection, first_literature_id))
        self.assertIsNotNone(get_literature(self.connection, second_literature_id))
        self.assertIsNotNone(get_tag(self.connection, tag_id))

    def test_detach_tag_failure_rolls_back_all_records(self) -> None:
        first_literature_id = self.add_literature("Detach rollback first")
        second_literature_id = self.add_literature("Detach rollback second")
        first_tag_id = create_tag(self.connection, "detach-rollback-first")
        second_tag_id = create_tag(self.connection, "detach-rollback-second")
        for literature_id, tag_id in (
            (first_literature_id, first_tag_id),
            (first_literature_id, second_tag_id),
            (second_literature_id, first_tag_id),
        ):
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        self.connection.execute(
            """
            CREATE TRIGGER force_mapping_delete_failure
            BEFORE DELETE ON literature_tags
            BEGIN
                SELECT RAISE(ABORT, 'forced mapping delete failure');
            END
            """
        )
        self.connection.commit()
        before = self.database_snapshot()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced mapping delete failure",
        ):
            detach_tag_from_literature(
                self.connection,
                first_literature_id,
                first_tag_id,
            )

        self.assertEqual(self.database_snapshot(), before)

    def test_delete_tag_cascades_only_its_mappings_and_keeps_literature(self) -> None:
        first_literature_id = self.add_literature("Delete tag first")
        second_literature_id = self.add_literature("Delete tag second")
        deleted_tag_id = create_tag(self.connection, "deleted-tag")
        kept_tag_id = create_tag(self.connection, "kept-tag")
        for literature_id, tag_id in (
            (first_literature_id, deleted_tag_id),
            (second_literature_id, deleted_tag_id),
            (first_literature_id, kept_tag_id),
            (second_literature_id, kept_tag_id),
        ):
            attach_tag_to_literature(self.connection, literature_id, tag_id)

        self.assertTrue(delete_tag(self.connection, deleted_tag_id))

        self.assertIsNone(get_tag(self.connection, deleted_tag_id))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature_tags WHERE tag_id = ?",
                (deleted_tag_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.mapping_count(first_literature_id, kept_tag_id), 1)
        self.assertEqual(self.mapping_count(second_literature_id, kept_tag_id), 1)
        self.assertIsNotNone(get_literature(self.connection, first_literature_id))
        self.assertIsNotNone(get_literature(self.connection, second_literature_id))

    def test_delete_tag_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(delete_tag(self.connection, 999999))

    def test_delete_tag_failure_rolls_back_tag_and_all_mappings(self) -> None:
        first_literature_id = self.add_literature("Delete rollback first")
        second_literature_id = self.add_literature("Delete rollback second")
        tag_id = create_tag(self.connection, "delete-rollback")
        attach_tag_to_literature(self.connection, first_literature_id, tag_id)
        attach_tag_to_literature(self.connection, second_literature_id, tag_id)
        self.connection.execute(
            """
            CREATE TRIGGER force_tag_delete_failure
            BEFORE DELETE ON tags
            BEGIN
                SELECT RAISE(ABORT, 'forced tag delete failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced tag delete failure",
        ):
            delete_tag(self.connection, tag_id)

        self.assertEqual(
            get_tag(self.connection, tag_id),
            Tag(id=tag_id, name="delete-rollback"),
        )
        self.assertEqual(self.mapping_count(first_literature_id, tag_id), 1)
        self.assertEqual(self.mapping_count(second_literature_id, tag_id), 1)

    def test_delete_tag_cascade_failure_rolls_back_all_records(self) -> None:
        first_literature_id = self.add_literature(
            "Cascade rollback first literature"
        )
        second_literature_id = self.add_literature(
            "Cascade rollback second literature"
        )
        deleted_tag_id = create_tag(self.connection, "cascade-rollback")
        kept_tag_id = create_tag(self.connection, "cascade-kept")
        for literature_id, tag_id in (
            (first_literature_id, deleted_tag_id),
            (second_literature_id, deleted_tag_id),
            (first_literature_id, kept_tag_id),
        ):
            attach_tag_to_literature(self.connection, literature_id, tag_id)
        self.connection.execute(
            """
            CREATE TRIGGER force_cascade_mapping_delete_failure
            BEFORE DELETE ON literature_tags
            BEGIN
                SELECT RAISE(ABORT, 'forced cascade mapping delete failure');
            END
            """
        )
        self.connection.commit()
        before = self.database_snapshot()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced cascade mapping delete failure",
        ):
            delete_tag(self.connection, deleted_tag_id)

        self.assertEqual(self.database_snapshot(), before)

    def test_each_tag_write_commits_for_a_separate_connection(self) -> None:
        literature_id = self.add_literature("Separate connection commits")

        tag_id = create_tag(self.connection, "commit-created")
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT id, name FROM tags WHERE id = ?",
                (tag_id,),
            ),
            [(tag_id, "commit-created")],
        )

        self.assertTrue(rename_tag(self.connection, tag_id, "commit-renamed"))
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT id, name FROM tags WHERE id = ?",
                (tag_id,),
            ),
            [(tag_id, "commit-renamed")],
        )

        self.assertTrue(
            attach_tag_to_literature(
                self.connection,
                literature_id,
                tag_id,
            )
        )
        self.assertEqual(
            self.rows_from_separate_connection(
                """
                SELECT literature_id, tag_id
                FROM literature_tags
                WHERE literature_id = ? AND tag_id = ?
                """,
                (literature_id, tag_id),
            ),
            [(literature_id, tag_id)],
        )

        self.assertTrue(
            detach_tag_from_literature(
                self.connection,
                literature_id,
                tag_id,
            )
        )
        self.assertEqual(
            self.rows_from_separate_connection(
                """
                SELECT literature_id, tag_id
                FROM literature_tags
                WHERE literature_id = ? AND tag_id = ?
                """,
                (literature_id, tag_id),
            ),
            [],
        )

        attach_tag_to_literature(self.connection, literature_id, tag_id)
        self.assertTrue(delete_tag(self.connection, tag_id))
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT id FROM tags WHERE id = ?",
                (tag_id,),
            ),
            [],
        )
        self.assertEqual(
            self.rows_from_separate_connection(
                "SELECT * FROM literature_tags WHERE tag_id = ?",
                (tag_id,),
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
