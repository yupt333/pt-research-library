"""Tests for the minimal literature repository operations."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import src.repository as repository_module
from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import (
    add_literature,
    delete_literature,
    get_literature,
    get_literature_related_counts,
    list_literature,
    update_literature,
)


class LiteratureRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "repository.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    @staticmethod
    def make_populated_literature(title: str = "Populated literature") -> Literature:
        return Literature(
            title=title,
            authors="Author Alpha; Author Beta",
            journal="Journal of Identifiable Values",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="101-112",
            doi="10.1234/identifiable.2025.001",
            pmid="12345678",
            url="https://example.test/literature/1",
            language="en",
            publication_type="Original Article",
            abstract="Identifiable abstract",
            pdf_path="/tmp/identifiable-literature.pdf",
            personal_summary="Identifiable personal summary",
            ai_summary="Identifiable manually entered AI summary",
            ai_summary_status="確認済み",
            general_note="Identifiable general note",
            key_findings="Identifiable key findings",
            methods_note="Identifiable methods note",
            clinical_note="Identifiable clinical note",
            limitation_note="Identifiable limitation note",
            relevance_note="Identifiable relevance note",
            evidence_level="Level II",
            verification_status="確認済み",
            adoption_status="採用",
            exclusion_reason="Identifiable exclusion reason",
            rating=5,
        )

    def get_raw_literature_row(self, literature_id: int) -> dict[str, object]:
        row = self.connection.execute(
            "SELECT * FROM literature WHERE id = ?",
            (literature_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return dict(row)

    def set_updated_at(self, literature_id: int, updated_at: str) -> None:
        self.connection.execute(
            "UPDATE literature SET updated_at = ? WHERE id = ?",
            (updated_at, literature_id),
        )
        self.connection.commit()

    def assert_utc_timestamp(self, timestamp: str) -> datetime:
        self.assertTrue(timestamp.endswith("Z"))
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))
        return parsed

    def test_add_literature_with_only_title(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Shoulder ultrasound reliability"),
        )

        self.assertIsInstance(literature_id, int)
        self.assertGreater(literature_id, 0)

    def test_get_literature_returns_registered_record(self) -> None:
        title = "Acromiohumeral distance measurement"
        literature_id = add_literature(self.connection, Literature(title=title))

        stored = get_literature(self.connection, literature_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.id, literature_id)
        self.assertEqual(stored.title, title)

    def test_state_fields_have_specified_defaults(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Default state test"),
        )

        stored = get_literature(self.connection, literature_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.ai_summary_status, "未作成")
        self.assertEqual(stored.verification_status, "未確認")
        self.assertEqual(stored.adoption_status, "未判定")
        self.assertIsNone(stored.rating)

    def test_timestamps_are_utc_iso_8601(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Timestamp test"),
        )

        stored = get_literature(self.connection, literature_id)

        self.assertIsNotNone(stored)
        assert stored is not None
        for timestamp in (stored.created_at, stored.updated_at):
            self.assertIsNotNone(timestamp)
            assert timestamp is not None
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))

    def test_empty_or_whitespace_title_cannot_be_registered(self) -> None:
        for title in ("", "   ", "\t\n"):
            with self.subTest(title=repr(title)):
                with self.assertRaises(ValueError):
                    add_literature(self.connection, Literature(title=title))

    def test_literature_accepts_valid_ratings(self) -> None:
        for rating in (None, 1, 5):
            with self.subTest(rating=rating):
                literature = Literature(title="Valid rating", rating=rating)

                self.assertEqual(literature.rating, rating)

    def test_literature_rejects_invalid_ratings(self) -> None:
        for rating in (0, 6, 1.5, "1", True, False):
            with self.subTest(rating=repr(rating)):
                with self.assertRaisesRegex(
                    ValueError,
                    "ratingはNoneまたは1〜5の整数",
                ):
                    Literature(title="Invalid rating", rating=rating)

    def test_get_literature_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(get_literature(self.connection, 999999))

    def test_list_literature_returns_empty_list_when_no_records_exist(self) -> None:
        self.assertEqual(list_literature(self.connection), [])

    def test_list_literature_orders_multiple_records_by_ascending_id(self) -> None:
        first_id = add_literature(
            self.connection,
            Literature(title="First literature"),
        )
        second_id = add_literature(
            self.connection,
            Literature(title="Second literature"),
        )
        third_id = add_literature(
            self.connection,
            Literature(title="Third literature"),
        )

        listed = list_literature(self.connection)

        self.assertEqual(
            [literature.id for literature in listed],
            [first_id, second_id, third_id],
        )

    def test_list_literature_returns_literature_objects(self) -> None:
        add_literature(self.connection, Literature(title="First literature"))
        add_literature(self.connection, Literature(title="Second literature"))

        listed = list_literature(self.connection)

        self.assertTrue(all(isinstance(item, Literature) for item in listed))

    def test_list_literature_restores_all_fields_nulls_and_id_order(self) -> None:
        populated_input = self.make_populated_literature("All fields literature")
        populated_id = add_literature(self.connection, populated_input)
        null_input = Literature(title="Null optional fields literature")
        null_id = add_literature(self.connection, null_input)

        populated_registered = get_literature(self.connection, populated_id)
        null_registered = get_literature(self.connection, null_id)
        self.assertIsNotNone(populated_registered)
        self.assertIsNotNone(null_registered)
        assert populated_registered is not None
        assert null_registered is not None

        expected_populated = Literature(
            **{
                **vars(populated_input),
                "id": populated_id,
                "created_at": populated_registered.created_at,
                "updated_at": populated_registered.updated_at,
            }
        )
        expected_null = Literature(
            **{
                **vars(null_input),
                "id": null_id,
                "created_at": null_registered.created_at,
                "updated_at": null_registered.updated_at,
            }
        )

        listed = list_literature(self.connection)

        self.assertEqual(listed, [expected_populated, expected_null])
        self.assertEqual(
            [literature.id for literature in listed],
            [populated_id, null_id],
        )
        nullable_fields = (
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
            "general_note",
            "key_findings",
            "methods_note",
            "clinical_note",
            "limitation_note",
            "relevance_note",
            "evidence_level",
            "exclusion_reason",
            "rating",
        )
        for field in nullable_fields:
            with self.subTest(field=field):
                self.assertIsNone(getattr(listed[1], field))

    def test_update_literature_can_update_title(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Original title"),
        )

        updated = update_literature(
            self.connection,
            literature_id,
            {"title": "Updated title"},
        )

        self.assertTrue(updated)
        stored = get_literature(self.connection, literature_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.title, "Updated title")

    def test_update_literature_can_update_general_note(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="General note test"),
        )

        updated = update_literature(
            self.connection,
            literature_id,
            {"general_note": "Updated general note"},
        )

        self.assertTrue(updated)
        stored = get_literature(self.connection, literature_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.general_note, "Updated general note")

    def test_update_literature_can_update_multiple_fields(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Multiple field update"),
        )

        updated = update_literature(
            self.connection,
            literature_id,
            {
                "authors": "Updated Author",
                "journal": "Updated Journal",
                "rating": 4,
            },
        )

        self.assertTrue(updated)
        stored = get_literature(self.connection, literature_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.authors, "Updated Author")
        self.assertEqual(stored.journal, "Updated Journal")
        self.assertEqual(stored.rating, 4)

    def test_title_only_update_preserves_every_other_field(self) -> None:
        literature_id = add_literature(
            self.connection,
            self.make_populated_literature("Original populated title"),
        )
        before = get_literature(self.connection, literature_id)
        self.assertIsNotNone(before)
        assert before is not None
        assert before.updated_at is not None
        current = self.assert_utc_timestamp(before.updated_at) + timedelta(seconds=1)

        with patch.object(repository_module, "_utc_now", return_value=current):
            self.assertTrue(
                update_literature(
                    self.connection,
                    literature_id,
                    {"title": "Updated populated title"},
                )
            )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        assert after.updated_at is not None
        expected = vars(before).copy()
        expected["title"] = "Updated populated title"
        expected["updated_at"] = after.updated_at
        self.assertEqual(vars(after), expected)
        self.assertGreater(
            self.assert_utc_timestamp(after.updated_at),
            self.assert_utc_timestamp(before.updated_at),
        )

    def test_general_note_only_update_preserves_every_other_field(self) -> None:
        literature_id = add_literature(
            self.connection,
            self.make_populated_literature("General note preservation"),
        )
        before = get_literature(self.connection, literature_id)
        self.assertIsNotNone(before)
        assert before is not None
        assert before.updated_at is not None
        current = self.assert_utc_timestamp(before.updated_at) + timedelta(seconds=1)

        with patch.object(repository_module, "_utc_now", return_value=current):
            self.assertTrue(
                update_literature(
                    self.connection,
                    literature_id,
                    {"general_note": "Only this general note changed"},
                )
            )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        assert after.updated_at is not None
        expected = vars(before).copy()
        expected["general_note"] = "Only this general note changed"
        expected["updated_at"] = after.updated_at
        self.assertEqual(vars(after), expected)
        self.assertGreater(
            self.assert_utc_timestamp(after.updated_at),
            self.assert_utc_timestamp(before.updated_at),
        )

    def test_update_literature_does_not_change_created_at(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Created timestamp test"),
        )
        before = get_literature(self.connection, literature_id)
        self.assertIsNotNone(before)
        assert before is not None

        update_literature(
            self.connection,
            literature_id,
            {"general_note": "Timestamp update"},
        )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        self.assertEqual(after.created_at, before.created_at)

    def test_update_literature_uses_later_current_time_with_millisecond_previous(
        self,
    ) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Updated timestamp test"),
        )
        previous_timestamp = "2026-01-02T03:04:05.123Z"
        current = datetime(
            2026,
            1,
            2,
            3,
            4,
            6,
            456789,
            tzinfo=timezone.utc,
        )
        self.set_updated_at(literature_id, previous_timestamp)

        with patch.object(repository_module, "_utc_now", return_value=current):
            update_literature(
                self.connection,
                literature_id,
                {"general_note": "Timestamp update"},
            )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        assert after.updated_at is not None
        before_datetime = self.assert_utc_timestamp(previous_timestamp)
        after_datetime = self.assert_utc_timestamp(after.updated_at)
        self.assertGreater(after_datetime, before_datetime)
        self.assertEqual(after_datetime, current)

    def test_update_literature_advances_equal_microsecond_previous_time(
        self,
    ) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Equal timestamp test"),
        )
        previous_timestamp = "2026-02-03T04:05:06.123456Z"
        previous_datetime = self.assert_utc_timestamp(previous_timestamp)
        self.set_updated_at(literature_id, previous_timestamp)

        with patch.object(
            repository_module,
            "_utc_now",
            return_value=previous_datetime,
        ):
            update_literature(
                self.connection,
                literature_id,
                {"general_note": "Equal timestamp update"},
            )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        assert after.updated_at is not None
        after_datetime = self.assert_utc_timestamp(after.updated_at)
        self.assertGreater(after_datetime, previous_datetime)
        self.assertEqual(
            after_datetime,
            previous_datetime + timedelta(microseconds=1),
        )

    def test_update_literature_advances_when_current_time_is_earlier(
        self,
    ) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Future timestamp test"),
        )
        previous_timestamp = "2099-03-04T05:06:07.654321Z"
        previous_datetime = self.assert_utc_timestamp(previous_timestamp)
        earlier_current = datetime(
            2026,
            3,
            4,
            5,
            6,
            7,
            654321,
            tzinfo=timezone.utc,
        )
        self.set_updated_at(literature_id, previous_timestamp)

        with patch.object(
            repository_module,
            "_utc_now",
            return_value=earlier_current,
        ):
            update_literature(
                self.connection,
                literature_id,
                {"general_note": "Future timestamp update"},
            )

        after = get_literature(self.connection, literature_id)
        self.assertIsNotNone(after)
        assert after is not None
        assert after.updated_at is not None
        after_datetime = self.assert_utc_timestamp(after.updated_at)
        self.assertGreater(after_datetime, previous_datetime)
        self.assertEqual(
            after_datetime,
            previous_datetime + timedelta(microseconds=1),
        )

    def test_update_literature_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(
            update_literature(
                self.connection,
                999999,
                {"title": "Unknown literature"},
            )
        )

    def test_update_literature_rejects_empty_or_whitespace_title(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Valid title"),
        )

        for title in ("", "   ", "\t\n"):
            with self.subTest(title=repr(title)):
                with self.assertRaisesRegex(ValueError, "タイトルは必須"):
                    update_literature(
                        self.connection,
                        literature_id,
                        {"title": title},
                    )

        stored = get_literature(self.connection, literature_id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.title, "Valid title")

    def test_update_literature_accepts_valid_ratings(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Valid rating update"),
        )

        for rating in (1, 5, None):
            with self.subTest(rating=rating):
                self.assertTrue(
                    update_literature(
                        self.connection,
                        literature_id,
                        {"rating": rating},
                    )
                )
                stored = get_literature(self.connection, literature_id)
                self.assertIsNotNone(stored)
                assert stored is not None
                self.assertEqual(stored.rating, rating)

    def test_update_literature_rejects_invalid_ratings(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Invalid rating update"),
        )

        for rating in (0, 6, 1.5, "1", True, False):
            with self.subTest(rating=repr(rating)):
                with self.assertRaisesRegex(
                    ValueError,
                    "ratingはNoneまたは1〜5の整数",
                ):
                    update_literature(
                        self.connection,
                        literature_id,
                        {"rating": rating},
                    )

    def test_update_literature_rejects_non_updatable_field(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Disallowed field update"),
        )

        for field in ("id", "created_at", "updated_at", "unknown_field"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "更新できない項目"):
                    update_literature(
                        self.connection,
                        literature_id,
                        {field: "not allowed"},
                    )

    def test_update_literature_rejects_empty_updates(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Empty update"),
        )

        with self.assertRaisesRegex(ValueError, "更新対象を1項目以上"):
            update_literature(self.connection, literature_id, {})

    def test_update_literature_rejects_sql_injection_column_name(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="SQL injection prevention"),
        )

        with self.assertRaisesRegex(ValueError, "更新できない項目"):
            update_literature(
                self.connection,
                literature_id,
                {"title = ?; DROP TABLE literature; --": "Injected"},
            )

        self.assertIsNotNone(get_literature(self.connection, literature_id))

    def test_failed_updates_preserve_entire_row_including_updated_at(self) -> None:
        literature_id = add_literature(
            self.connection,
            self.make_populated_literature("Failed update preservation"),
        )
        before = self.get_raw_literature_row(literature_id)
        invalid_updates = (
            ("title is None", {"title": None}),
            ("title is empty", {"title": ""}),
            ("title is whitespace", {"title": "   "}),
            ("rating is zero", {"rating": 0}),
            ("rating is six", {"rating": 6}),
            ("rating is decimal", {"rating": 1.5}),
            ("rating is string", {"rating": "3"}),
            ("rating is boolean", {"rating": True}),
            ("column is not allowed", {"unknown_field": "not allowed"}),
            (
                "column attempts SQL injection",
                {"title = ?; DROP TABLE literature; --": "Injected"},
            ),
        )

        for case, updates in invalid_updates:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    update_literature(self.connection, literature_id, updates)
                self.assertEqual(
                    self.get_raw_literature_row(literature_id),
                    before,
                )

    def test_sql_update_exception_rolls_back_entire_row(self) -> None:
        literature_id = add_literature(
            self.connection,
            self.make_populated_literature("SQL update rollback"),
        )
        before = self.get_raw_literature_row(literature_id)
        self.connection.execute(
            """
            CREATE TRIGGER force_literature_update_failure
            BEFORE UPDATE ON literature
            BEGIN
                SELECT RAISE(ABORT, 'forced update failure');
            END
            """
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced update failure",
        ):
            update_literature(
                self.connection,
                literature_id,
                {"general_note": "This change must be rolled back"},
            )

        self.assertEqual(
            self.get_raw_literature_row(literature_id),
            before,
        )

    def test_related_counts_are_zero_when_no_related_records_exist(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="No related records"),
        )

        counts = get_literature_related_counts(self.connection, literature_id)

        self.assertEqual(
            counts,
            {"tag_count": 0, "usage_history_count": 0},
        )

    def test_related_counts_include_multiple_tags_and_usage_history_rows(
        self,
    ) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Multiple related records"),
        )
        first_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("ultrasound",),
        ).lastrowid
        second_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("reliability",),
        ).lastrowid
        self.connection.executemany(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (
                (literature_id, first_tag_id),
                (literature_id, second_tag_id),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (
                (literature_id, "note"),
                (literature_id, "大学院研究"),
                (literature_id, "学会発表"),
            ),
        )
        self.connection.commit()

        counts = get_literature_related_counts(self.connection, literature_id)

        self.assertEqual(
            counts,
            {"tag_count": 2, "usage_history_count": 3},
        )

    def test_related_counts_are_isolated_between_literature_records(self) -> None:
        first_literature_id = add_literature(
            self.connection,
            Literature(title="First related counts"),
        )
        second_literature_id = add_literature(
            self.connection,
            Literature(title="Second related counts"),
        )
        tag_ids = [
            self.connection.execute(
                "INSERT INTO tags (name) VALUES (?)",
                (tag_name,),
            ).lastrowid
            for tag_name in ("first-tag-1", "first-tag-2", "second-tag-1")
        ]
        self.connection.executemany(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (
                (first_literature_id, tag_ids[0]),
                (first_literature_id, tag_ids[1]),
                (second_literature_id, tag_ids[2]),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (
                (first_literature_id, "note"),
                (second_literature_id, "大学院研究"),
                (second_literature_id, "学会発表"),
                (second_literature_id, "論文"),
            ),
        )
        self.connection.commit()

        first_counts = get_literature_related_counts(
            self.connection,
            first_literature_id,
        )
        second_counts = get_literature_related_counts(
            self.connection,
            second_literature_id,
        )

        self.assertEqual(
            first_counts,
            {"tag_count": 2, "usage_history_count": 1},
        )
        self.assertEqual(
            second_counts,
            {"tag_count": 1, "usage_history_count": 3},
        )
        self.assertNotEqual(
            first_counts,
            {"tag_count": 3, "usage_history_count": 4},
        )
        self.assertNotEqual(
            second_counts,
            {"tag_count": 3, "usage_history_count": 4},
        )

    def test_related_counts_return_none_for_unknown_id(self) -> None:
        self.assertIsNone(
            get_literature_related_counts(self.connection, 999999)
        )

    def test_delete_literature_deletes_existing_record(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Delete existing record"),
        )

        self.assertTrue(delete_literature(self.connection, literature_id))

    def test_deleted_literature_cannot_be_retrieved(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Deleted record lookup"),
        )

        delete_literature(self.connection, literature_id)

        self.assertIsNone(get_literature(self.connection, literature_id))

    def test_delete_literature_returns_false_for_unknown_id(self) -> None:
        self.assertFalse(delete_literature(self.connection, 999999))

    def test_delete_literature_cascades_to_literature_tags(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Cascade tag mapping"),
        )
        tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("cascade-tag",),
        ).lastrowid
        self.connection.execute(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (literature_id, tag_id),
        )
        self.connection.commit()

        delete_literature(self.connection, literature_id)

        mapping_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM literature_tags
            WHERE literature_id = ?
            """,
            (literature_id,),
        ).fetchone()[0]
        self.assertEqual(mapping_count, 0)

    def test_delete_literature_cascades_to_usage_history(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Cascade usage history"),
        )
        self.connection.execute(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (literature_id, "note"),
        )
        self.connection.commit()

        delete_literature(self.connection, literature_id)

        history_count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM usage_history
            WHERE literature_id = ?
            """,
            (literature_id,),
        ).fetchone()[0]
        self.assertEqual(history_count, 0)

    def test_delete_literature_keeps_tag_record(self) -> None:
        literature_id = add_literature(
            self.connection,
            Literature(title="Keep tag record"),
        )
        tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("persistent-tag",),
        ).lastrowid
        self.connection.execute(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (literature_id, tag_id),
        )
        self.connection.commit()

        delete_literature(self.connection, literature_id)

        tag_count = self.connection.execute(
            "SELECT COUNT(*) FROM tags WHERE id = ?",
            (tag_id,),
        ).fetchone()[0]
        self.assertEqual(tag_count, 1)

    def test_delete_literature_isolated_cascade_keeps_other_record_and_tags(
        self,
    ) -> None:
        literature_a_id = add_literature(
            self.connection,
            Literature(title="Literature A for isolated delete"),
        )
        literature_b_id = add_literature(
            self.connection,
            Literature(title="Literature B for isolated delete"),
        )
        shared_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("shared-delete-tag",),
        ).lastrowid
        literature_a_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("literature-a-only-tag",),
        ).lastrowid
        literature_b_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("literature-b-only-tag",),
        ).lastrowid
        self.connection.executemany(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (
                (literature_a_id, shared_tag_id),
                (literature_a_id, literature_a_tag_id),
                (literature_b_id, shared_tag_id),
                (literature_b_id, literature_b_tag_id),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (
                (literature_a_id, "note"),
                (literature_a_id, "学会発表"),
                (literature_b_id, "大学院研究"),
            ),
        )
        self.connection.commit()

        self.assertTrue(delete_literature(self.connection, literature_a_id))

        literature_a_count = self.connection.execute(
            "SELECT COUNT(*) FROM literature WHERE id = ?",
            (literature_a_id,),
        ).fetchone()[0]
        literature_a_tag_count = self.connection.execute(
            "SELECT COUNT(*) FROM literature_tags WHERE literature_id = ?",
            (literature_a_id,),
        ).fetchone()[0]
        literature_a_usage_count = self.connection.execute(
            "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
            (literature_a_id,),
        ).fetchone()[0]
        literature_b_count = self.connection.execute(
            "SELECT COUNT(*) FROM literature WHERE id = ?",
            (literature_b_id,),
        ).fetchone()[0]
        literature_b_tag_ids = {
            row["tag_id"]
            for row in self.connection.execute(
                """
                SELECT tag_id
                FROM literature_tags
                WHERE literature_id = ?
                """,
                (literature_b_id,),
            ).fetchall()
        }
        literature_b_usage_count = self.connection.execute(
            "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
            (literature_b_id,),
        ).fetchone()[0]
        remaining_tag_ids = {
            row["id"]
            for row in self.connection.execute(
                "SELECT id FROM tags"
            ).fetchall()
        }

        self.assertEqual(literature_a_count, 0)
        self.assertEqual(literature_a_tag_count, 0)
        self.assertEqual(literature_a_usage_count, 0)
        self.assertEqual(literature_b_count, 1)
        self.assertEqual(
            literature_b_tag_ids,
            {shared_tag_id, literature_b_tag_id},
        )
        self.assertEqual(literature_b_usage_count, 1)
        self.assertEqual(
            remaining_tag_ids,
            {shared_tag_id, literature_a_tag_id, literature_b_tag_id},
        )

    def test_delete_literature_exception_rolls_back_without_cross_record_effects(
        self,
    ) -> None:
        literature_a_id = add_literature(
            self.connection,
            Literature(title="Literature A for delete rollback"),
        )
        literature_b_id = add_literature(
            self.connection,
            Literature(title="Literature B for delete rollback"),
        )
        shared_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("shared-rollback-tag",),
        ).lastrowid
        literature_a_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("literature-a-rollback-tag",),
        ).lastrowid
        literature_b_tag_id = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("literature-b-rollback-tag",),
        ).lastrowid
        self.connection.executemany(
            """
            INSERT INTO literature_tags (literature_id, tag_id)
            VALUES (?, ?)
            """,
            (
                (literature_a_id, shared_tag_id),
                (literature_a_id, literature_a_tag_id),
                (literature_b_id, shared_tag_id),
                (literature_b_id, literature_b_tag_id),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO usage_history (literature_id, usage_type)
            VALUES (?, ?)
            """,
            (
                (literature_a_id, "note"),
                (literature_b_id, "大学院研究"),
            ),
        )
        self.connection.execute(
            """
            CREATE TRIGGER force_literature_delete_failure
            BEFORE DELETE ON literature
            BEGIN
                SELECT RAISE(ABORT, 'forced delete failure');
            END
            """
        )
        self.connection.commit()
        before_rows = {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1, 2"
                ).fetchall()
            ]
            for table in (
                "literature",
                "tags",
                "literature_tags",
                "usage_history",
            )
        }

        with self.assertRaisesRegex(
            sqlite3.IntegrityError,
            "forced delete failure",
        ):
            delete_literature(self.connection, literature_a_id)

        after_rows = {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1, 2"
                ).fetchall()
            ]
            for table in (
                "literature",
                "tags",
                "literature_tags",
                "usage_history",
            )
        }
        self.assertEqual(after_rows, before_rows)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature WHERE id IN (?, ?)",
                (literature_a_id, literature_b_id),
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM literature_tags WHERE literature_id = ?",
                (literature_a_id,),
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
                (literature_a_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            {
                row["tag_id"]
                for row in self.connection.execute(
                    """
                    SELECT tag_id
                    FROM literature_tags
                    WHERE literature_id = ?
                    """,
                    (literature_b_id,),
                ).fetchall()
            },
            {shared_tag_id, literature_b_tag_id},
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM usage_history WHERE literature_id = ?",
                (literature_b_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            {
                row["id"]
                for row in self.connection.execute(
                    "SELECT id FROM tags"
                ).fetchall()
            },
            {shared_tag_id, literature_a_tag_id, literature_b_tag_id},
        )


if __name__ == "__main__":
    unittest.main()
