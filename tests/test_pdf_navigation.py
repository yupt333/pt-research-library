"""Tests for Phase 2-6 local original-PDF Evidence navigation."""

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.database import connect_database, initialize_database
from src.models import Literature
from src.pdf_navigation import (
    PDF_FILE_NOT_FOUND_MESSAGE,
    PDF_OPEN_FAILURE_MESSAGE,
    PDF_PATH_DIRECTORY_MESSAGE,
    PDF_PATH_NOT_PDF_MESSAGE,
    PDF_PATH_UNREGISTERED_MESSAGE,
    PdfNavigationError,
    build_navigation_target,
    format_locator_summary,
    open_evidence_pdf,
)
from src.repository import add_literature, get_literature, update_literature
from src.structured_repository import (
    attach_evidence_to_entity,
    attach_evidence_to_field,
    create_evidence_reference,
    create_structured_entity,
    create_structured_field,
    delete_evidence_reference,
    get_evidence_reference,
    get_structured_entity,
    get_structured_field,
    update_evidence_reference,
)


class TrackingConnection(sqlite3.Connection):
    """Record lifecycle calls that read-only navigation must not make."""

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


class PdfNavigationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project_root = Path(self.temporary_directory.name)
        self.database_path = self.project_root / "navigation.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

        self.pdf_path = self.project_root / "pdfs" / "synthetic source.pdf"
        self.pdf_path.parent.mkdir()
        self.pdf_path.write_bytes(b"synthetic test-only PDF placeholder")
        self.literature_id = add_literature(
            self.connection,
            Literature(
                title="Synthetic PDF navigation target",
                pdf_path=str(self.pdf_path),
                verification_status="要確認",
                ai_summary_status="未確認",
                adoption_status="採用候補",
            ),
        )
        self.evidence_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            pdf_page=5,
            printed_page="164",
            section="Results",
            subsection="Primary outcome",
            table_label="Table 1",
            figure_label="Figure 2",
            verification="ai_unverified",
        )
        self.other_literature_id = add_literature(
            self.connection,
            Literature(
                title="Synthetic other Literature",
                pdf_path=str(self.pdf_path),
            ),
        )

    def add_target(self, pdf_path: str | None) -> tuple[int, int]:
        literature_id = add_literature(
            self.connection,
            Literature(title="Synthetic path case", pdf_path=pdf_path),
        )
        evidence_id = create_evidence_reference(
            self.connection, literature_id, section="Methods"
        )
        return literature_id, evidence_id

    def build(self):
        return build_navigation_target(
            self.connection,
            self.literature_id,
            self.evidence_id,
            project_root=self.project_root,
        )

    @staticmethod
    def table_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
        tables = (
            "literature",
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
        )
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            ]
            for table in tables
        }

    def test_builds_valid_target_and_keeps_all_locator_meanings_separate(self) -> None:
        target = self.build()

        self.assertEqual(target.literature_id, self.literature_id)
        self.assertEqual(target.literature_title, "Synthetic PDF navigation target")
        self.assertEqual(target.stored_pdf_path, str(self.pdf_path))
        self.assertEqual(target.resolved_pdf_path, self.pdf_path.resolve())
        self.assertEqual(target.evidence_id, self.evidence_id)
        self.assertEqual(target.pdf_page, 5)
        self.assertEqual(target.printed_page, "164")
        self.assertEqual(target.section, "Results")
        self.assertEqual(target.subsection, "Primary outcome")
        self.assertEqual(target.table_label, "Table 1")
        self.assertEqual(target.figure_label, "Figure 2")
        self.assertEqual(target.verification, "ai_unverified")
        self.assertTrue(target.can_open)

        summary = format_locator_summary(target)
        self.assertIn("PDF page: 5", summary)
        self.assertIn("Printed page: 164", summary)
        self.assertNotIn("PDF page: 164", summary)
        for text in (
            "Section: Results",
            "Subsection: Primary outcome",
            "Table: Table 1",
            "Figure: Figure 2",
        ):
            self.assertIn(text, summary)

    def test_unknown_literature_evidence_and_cross_literature_are_rejected(self) -> None:
        with self.assertRaisesRegex(PdfNavigationError, "Literature"):
            build_navigation_target(
                self.connection,
                999999,
                self.evidence_id,
                project_root=self.project_root,
            )
        with self.assertRaisesRegex(PdfNavigationError, "Evidence"):
            build_navigation_target(
                self.connection,
                self.literature_id,
                999999,
                project_root=self.project_root,
            )
        with self.assertRaisesRegex(PdfNavigationError, "属していません"):
            build_navigation_target(
                self.connection,
                self.other_literature_id,
                self.evidence_id,
                project_root=self.project_root,
            )

    def test_none_blank_and_whitespace_pdf_path_are_unregistered(self) -> None:
        for stored_path in (None, "", "   \t"):
            with self.subTest(stored_path=stored_path):
                literature_id, evidence_id = self.add_target(stored_path)
                target = build_navigation_target(
                    self.connection,
                    literature_id,
                    evidence_id,
                    project_root=self.project_root,
                )
                self.assertEqual(target.stored_pdf_path, stored_path)
                self.assertIsNone(target.resolved_pdf_path)
                self.assertEqual(target.path_error, PDF_PATH_UNREGISTERED_MESSAGE)
                self.assertFalse(target.can_open)

    def test_absolute_relative_tilde_uppercase_and_symlink_paths(self) -> None:
        uppercase_path = self.project_root / "pdfs" / "uppercase.PDF"
        uppercase_path.write_bytes(b"synthetic")
        symlink_path = self.project_root / "pdfs" / "linked.pdf"
        symlink_path.symlink_to(uppercase_path)

        cases = (
            (str(self.pdf_path), self.pdf_path.resolve()),
            ("pdfs/synthetic source.pdf", self.pdf_path.resolve()),
            (str(uppercase_path), uppercase_path.resolve()),
            (str(symlink_path), uppercase_path.resolve()),
        )
        unrelated_cwd = self.project_root / "unrelated-cwd"
        unrelated_cwd.mkdir()
        original_cwd = Path.cwd()
        try:
            os.chdir(unrelated_cwd)
            for stored_path, expected in cases:
                with self.subTest(stored_path=stored_path):
                    literature_id, evidence_id = self.add_target(stored_path)
                    target = build_navigation_target(
                        self.connection,
                        literature_id,
                        evidence_id,
                        project_root=self.project_root,
                    )
                    self.assertEqual(target.resolved_pdf_path, expected)
                    self.assertTrue(target.can_open)
        finally:
            os.chdir(original_cwd)

        tilde_path = self.project_root / "tilde.PDF"
        tilde_path.write_bytes(b"synthetic")
        literature_id, evidence_id = self.add_target("~/tilde.PDF")
        with patch.dict(os.environ, {"HOME": str(self.project_root)}):
            target = build_navigation_target(
                self.connection,
                literature_id,
                evidence_id,
                project_root=self.project_root / "not-used",
            )
        self.assertEqual(target.resolved_pdf_path, tilde_path.resolve())
        self.assertTrue(target.can_open)

        nested_root = self.project_root / "nested-application"
        nested_root.mkdir()
        outside_pdf = self.project_root / "legitimate-outside.pdf"
        outside_pdf.write_bytes(b"synthetic")
        literature_id, evidence_id = self.add_target("../legitimate-outside.pdf")
        target = build_navigation_target(
            self.connection,
            literature_id,
            evidence_id,
            project_root=nested_root,
        )
        self.assertEqual(target.resolved_pdf_path, outside_pdf.resolve())
        self.assertTrue(target.can_open)

    def test_nonexistent_directory_and_non_pdf_paths_are_rejected(self) -> None:
        directory_path = self.project_root / "directory.pdf"
        directory_path.mkdir()
        text_path = self.project_root / "source.txt"
        text_path.write_text("synthetic", encoding="utf-8")
        disguised_symlink = self.project_root / "disguised.pdf"
        disguised_symlink.symlink_to(text_path)
        dangling_symlink = self.project_root / "dangling.pdf"
        dangling_symlink.symlink_to(self.project_root / "absent.PDF")
        cases = (
            ("missing.pdf", PDF_FILE_NOT_FOUND_MESSAGE),
            (str(directory_path), PDF_PATH_DIRECTORY_MESSAGE),
            (str(text_path), PDF_PATH_NOT_PDF_MESSAGE),
            (str(disguised_symlink), PDF_PATH_NOT_PDF_MESSAGE),
            (str(dangling_symlink), PDF_FILE_NOT_FOUND_MESSAGE),
        )
        for stored_path, expected_error in cases:
            with self.subTest(stored_path=stored_path):
                literature_id, evidence_id = self.add_target(stored_path)
                target = build_navigation_target(
                    self.connection,
                    literature_id,
                    evidence_id,
                    project_root=self.project_root,
                )
                self.assertEqual(target.stored_pdf_path, stored_path)
                self.assertEqual(target.path_error, expected_error)
                self.assertFalse(target.can_open)

    def test_missing_locators_are_displayed_without_inference(self) -> None:
        literature_id, evidence_id = self.add_target(str(self.pdf_path))
        target = build_navigation_target(
            self.connection,
            literature_id,
            evidence_id,
            project_root=self.project_root,
        )
        summary = format_locator_summary(target)
        self.assertIn("PDF page: 未登録", summary)
        self.assertIn("Printed page: 未登録", summary)
        self.assertIn("Section: Methods", summary)
        self.assertIn("Subsection: 未登録", summary)
        self.assertIn("Table: 未登録", summary)
        self.assertIn("Figure: 未登録", summary)

    def test_open_uses_one_argv_path_and_never_a_shell_for_special_names(self) -> None:
        names = (
            "space name.pdf",
            "日本語.pdf",
            "semi;colon.pdf",
            "amp&ersand.pdf",
            'quo"te.pdf',
            "single'quote.pdf",
            "dollar$.pdf",
            "back`tick.pdf",
        )
        for name in names:
            with self.subTest(name=name):
                path = self.project_root / "pdfs" / name
                path.write_bytes(b"synthetic")
                update_literature(
                    self.connection, self.literature_id, {"pdf_path": str(path)}
                )
                target = self.build()
                calls: list[tuple[tuple, dict]] = []

                def runner(*args: object, **kwargs: object) -> object:
                    calls.append((args, kwargs))
                    return SimpleNamespace(returncode=0, stderr="")

                result = open_evidence_pdf(
                    self.connection,
                    target,
                    project_root=self.project_root,
                    runner=runner,
                )

                self.assertTrue(result.success)
                self.assertEqual(len(calls), 1)
                args, kwargs = calls[0]
                self.assertEqual(args, (["open", str(path.resolve())],))
                self.assertIs(kwargs["shell"], False)
                self.assertIs(kwargs["capture_output"], True)
                self.assertIs(kwargs["text"], True)
                self.assertIs(kwargs["check"], False)

    def test_nonzero_and_runner_exception_fail_without_stderr_or_retry(self) -> None:
        target = self.build()
        calls = []

        def nonzero_runner(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=7, stderr="sensitive synthetic detail")

        nonzero = open_evidence_pdf(
            self.connection,
            target,
            project_root=self.project_root,
            runner=nonzero_runner,
        )
        self.assertFalse(nonzero.success)
        self.assertEqual(nonzero.error_message, PDF_OPEN_FAILURE_MESSAGE)
        self.assertNotIn("sensitive", nonzero.error_message)
        self.assertEqual(len(calls), 1)

        def raising_runner(*args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            raise OSError("synthetic open failure detail")

        raised = open_evidence_pdf(
            self.connection,
            target,
            project_root=self.project_root,
            runner=raising_runner,
        )
        self.assertFalse(raised.success)
        self.assertEqual(raised.error_message, PDF_OPEN_FAILURE_MESSAGE)
        self.assertEqual(len(calls), 2)

    def test_invalid_path_never_calls_runner(self) -> None:
        literature_id, evidence_id = self.add_target("missing.pdf")
        target = build_navigation_target(
            self.connection,
            literature_id,
            evidence_id,
            project_root=self.project_root,
        )
        calls = []
        result = open_evidence_pdf(
            self.connection,
            target,
            project_root=self.project_root,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_message, PDF_FILE_NOT_FOUND_MESSAGE)
        self.assertEqual(calls, [])

    def test_open_changes_no_verification_status_or_database_rows(self) -> None:
        entity_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "outcome",
            verification="user_verified",
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            "name",
            content_role="source_fact",
            value="Synthetic outcome",
            availability="reported",
            verification="user_verified",
        )
        attach_evidence_to_entity(self.connection, entity_id, self.evidence_id)
        attach_evidence_to_field(self.connection, field_id, self.evidence_id)
        before = self.table_snapshot(self.connection)

        result = open_evidence_pdf(
            self.connection,
            self.build(),
            project_root=self.project_root,
            runner=lambda *args, **kwargs: SimpleNamespace(returncode=0),
        )

        self.assertTrue(result.success)
        self.assertEqual(self.table_snapshot(self.connection), before)
        self.assertEqual(
            get_evidence_reference(self.connection, self.evidence_id).verification,
            "ai_unverified",
        )
        self.assertEqual(
            get_structured_entity(self.connection, entity_id).verification,
            "user_verified",
        )
        self.assertEqual(
            get_structured_field(self.connection, field_id).verification,
            "user_verified",
        )
        literature = get_literature(self.connection, self.literature_id)
        self.assertEqual(literature.verification_status, "要確認")
        self.assertEqual(literature.ai_summary_status, "未確認")
        self.assertEqual(literature.adoption_status, "採用候補")

    def test_active_transaction_is_preserved_without_lifecycle_calls(self) -> None:
        tracking_path = self.project_root / "tracking.db"
        initialize_database(tracking_path)
        connection = sqlite3.connect(tracking_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            literature_id = add_literature(
                connection,
                Literature(title="Synthetic transaction", pdf_path=str(self.pdf_path)),
            )
            evidence_id = create_evidence_reference(
                connection, literature_id, pdf_page=2
            )
            target = build_navigation_target(
                connection,
                literature_id,
                evidence_id,
                project_root=self.project_root,
            )
            marker_id = connection.execute(
                "INSERT INTO tags (name) VALUES (?)", ("pending-navigation",)
            ).lastrowid
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            before = self.table_snapshot(connection)

            result = open_evidence_pdf(
                connection,
                target,
                project_root=self.project_root,
                runner=lambda *args, **kwargs: SimpleNamespace(returncode=0),
            )

            self.assertTrue(result.success)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(self.table_snapshot(connection), before)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM tags WHERE id = ?", (marker_id,)
                ).fetchone()[0],
                1,
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_stale_deleted_or_cross_owner_evidence_never_opens(self) -> None:
        target = self.build()
        self.assertTrue(delete_evidence_reference(self.connection, self.evidence_id))
        calls = []
        deleted = open_evidence_pdf(
            self.connection,
            target,
            project_root=self.project_root,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        self.assertFalse(deleted.success)
        self.assertIn("Evidence", deleted.error_message)
        self.assertEqual(calls, [])

        replacement_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=5
        )
        replacement_target = build_navigation_target(
            self.connection,
            self.literature_id,
            replacement_id,
            project_root=self.project_root,
        )
        current = get_evidence_reference(self.connection, replacement_id)
        with patch(
            "src.pdf_navigation.get_evidence_reference",
            return_value=replace(current, literature_id=self.other_literature_id),
        ):
            cross_owner = open_evidence_pdf(
                self.connection,
                replacement_target,
                project_root=self.project_root,
                runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            )
        self.assertFalse(cross_owner.success)
        self.assertIn("属していません", cross_owner.error_message)
        self.assertEqual(calls, [])

    def test_path_change_is_rejected_but_locator_and_verification_refresh(self) -> None:
        target = self.build()
        second_pdf = self.project_root / "pdfs" / "second.pdf"
        second_pdf.write_bytes(b"synthetic")
        update_literature(
            self.connection,
            self.literature_id,
            {"pdf_path": str(second_pdf)},
        )
        calls = []
        changed_path = open_evidence_pdf(
            self.connection,
            target,
            project_root=self.project_root,
            runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        self.assertFalse(changed_path.success)
        self.assertIn("Preview後", changed_path.error_message)
        self.assertEqual(calls, [])

        update_literature(
            self.connection,
            self.literature_id,
            {"pdf_path": str(self.pdf_path)},
        )
        refreshed_preview = self.build()
        update_evidence_reference(
            self.connection,
            self.evidence_id,
            {"pdf_page": 9, "printed_page": "S3", "verification": "user_verified"},
        )
        refreshed = open_evidence_pdf(
            self.connection,
            refreshed_preview,
            project_root=self.project_root,
            runner=lambda *args, **kwargs: SimpleNamespace(returncode=0),
        )
        self.assertTrue(refreshed.success)
        self.assertEqual(refreshed.target.pdf_page, 9)
        self.assertEqual(refreshed.target.printed_page, "S3")
        self.assertEqual(refreshed.target.verification, "user_verified")

    def test_integrity_pragmas_remain_clean(self) -> None:
        self.build()
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            self.connection.execute("PRAGMA quick_check").fetchone()[0], "ok"
        )


if __name__ == "__main__":
    unittest.main()
