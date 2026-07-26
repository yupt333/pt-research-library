"""Tests for the minimal literature repository operations."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import add_literature, get_literature


class LiteratureRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "repository.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

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


if __name__ == "__main__":
    unittest.main()
