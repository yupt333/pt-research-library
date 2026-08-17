"""Phase 1 end-to-end tests using only temporary, synthetic data."""

import codecs
import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.app as app_module
import src.cli as cli_module
from src.database import connect_database
from src.repository import (
    get_literature,
    list_tags_for_literature,
    list_usage_history_for_literature,
)


_REGISTRATION_FIELDS = (
    "title",
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
    "ai_summary_status",
    "general_note",
    "key_findings",
    "methods_note",
    "clinical_note",
    "limitation_note",
    "relevance_note",
    "evidence_level",
    "verification_status",
    "adoption_status",
    "exclusion_reason",
    "rating",
)


class _InputFeeder:
    """Provide a complete synthetic CLI session and record every prompt."""

    def __init__(self, actions: list[str]) -> None:
        self._actions = iter(actions)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        try:
            return next(self._actions)
        except StopIteration as error:
            raise EOFError("synthetic Phase 1 input exhausted") from error


class Phase1EndToEndTestCase(unittest.TestCase):
    @staticmethod
    def _registration_values(**fields: str) -> list[str]:
        values = {field: "" for field in _REGISTRATION_FIELDS}
        values.update(fields)
        return [values[field] for field in _REGISTRATION_FIELDS]

    @staticmethod
    def _table_snapshot(
        connection: sqlite3.Connection,
    ) -> dict[str, list[tuple[object, ...]]]:
        orderings = {
            "literature": "id",
            "tags": "id",
            "literature_tags": "literature_id, tag_id",
            "usage_history": "id",
        }
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table, ordering in orderings.items()
        }

    def _run_application_with_real_cli(
        self,
        project_root: Path,
        actions: list[str],
    ) -> tuple[list[str], list[str]]:
        feeder = _InputFeeder(actions)
        outputs: list[str] = []
        runtime_connections: list[sqlite3.Connection] = []

        def run_real_cli(
            connection: sqlite3.Connection,
            *,
            export_directory: Path,
            backup_directory: Path,
        ) -> None:
            runtime_connections.append(connection)
            cli_module.run_cli(
                connection,
                input_func=feeder,
                output_func=outputs.append,
                export_directory=export_directory,
                backup_directory=backup_directory,
            )

        with patch.object(
            app_module,
            "run_cli",
            side_effect=run_real_cli,
        ) as patched_run_cli:
            app_module.run_application(project_root)

        patched_run_cli.assert_called_once()
        self.assertEqual(len(feeder.prompts), len(actions))
        self.assertEqual(len(runtime_connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            runtime_connections[0].execute("SELECT 1")
        return feeder.prompts, outputs

    def test_representative_workflow_persists_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            title = "Synthetic Phase 1 integration literature"
            tag_name = "synthetic-phase1-tag"
            usage_type = "synthetic-phase1-use"
            publication_type = "synthetic-publication-type"

            session_one_actions = [
                "3",
                *self._registration_values(
                    title=title,
                    authors="Synthetic Author A; Synthetic Author B",
                    journal="Synthetic test journal",
                    publication_year="2020",
                    volume="synthetic-volume",
                    issue="synthetic-issue",
                    pages="synthetic-pages",
                    language="synthetic-language",
                    publication_type=publication_type,
                    abstract="Synthetic abstract for Phase 1 testing only.",
                    personal_summary="Synthetic personal summary.",
                    ai_summary="Synthetic manually entered AI summary.",
                    ai_summary_status="未確認",
                    general_note="Synthetic general note.",
                    key_findings="Synthetic key findings.",
                    methods_note="Synthetic methods note.",
                    clinical_note="Synthetic clinical note.",
                    limitation_note="Synthetic limitation note.",
                    relevance_note="Synthetic relevance note.",
                    evidence_level="synthetic-evidence-level",
                    verification_status="一部確認",
                    adoption_status="採用候補",
                    rating="4",
                ),
                "1",
                "6",
                "2",
                tag_name,
                "1",
                "6",
                "1",
                "1",
                "1",
                "0",
                "7",
                "2",
                "1",
                usage_type,
                "Synthetic Phase 1 project",
                "Synthetic usage note.",
                "2026-08-17",
                "1",
                "0",
                "2",
                title,
                "2020",
                tag_name,
                publication_type,
                "一部確認",
                "採用候補",
                "未確認",
                "4",
                usage_type,
                "9",
                "2",
                "0",
                "10",
                "0",
            ]
            _, session_one_outputs = self._run_application_with_real_cli(
                project_root,
                session_one_actions,
            )

            for expected_message in (
                "文献を登録しました。",
                "文献へタグを付与しました。",
                "使用履歴を登録しました。",
                "CSVを出力しました。",
                "データベースをバックアップしました。",
                "CLIを終了します。",
            ):
                self.assertIn(expected_message, session_one_outputs)

            database_path = (
                project_root / "data" / "pt_research_library.sqlite3"
            )
            self.assertTrue(database_path.is_file())
            database_inode = database_path.stat().st_ino
            connection = connect_database(database_path)
            try:
                literature = get_literature(connection, 1)
                self.assertIsNotNone(literature)
                assert literature is not None
                self.assertEqual(literature.id, 1)
                self.assertEqual(literature.title, title)
                self.assertEqual(
                    literature.personal_summary,
                    "Synthetic personal summary.",
                )
                self.assertEqual(
                    literature.ai_summary,
                    "Synthetic manually entered AI summary.",
                )
                self.assertEqual(literature.ai_summary_status, "未確認")
                self.assertEqual(
                    literature.general_note,
                    "Synthetic general note.",
                )
                self.assertEqual(literature.verification_status, "一部確認")
                self.assertEqual(literature.adoption_status, "採用候補")
                self.assertEqual(literature.rating, 4)

                tags = list_tags_for_literature(connection, 1)
                self.assertIsNotNone(tags)
                assert tags is not None
                self.assertEqual([tag.name for tag in tags], [tag_name])
                histories = list_usage_history_for_literature(connection, 1)
                self.assertIsNotNone(histories)
                assert histories is not None
                self.assertEqual(len(histories), 1)
                self.assertEqual(histories[0].usage_type, usage_type)
                self.assertEqual(
                    histories[0].project_name,
                    "Synthetic Phase 1 project",
                )
                self.assertEqual(
                    histories[0].usage_note,
                    "Synthetic usage note.",
                )
                self.assertEqual(histories[0].used_at, "2026-08-17")
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                snapshot_before_restart = self._table_snapshot(connection)
                schema_before_restart = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT type, name, tbl_name, sql
                        FROM sqlite_master
                        ORDER BY type, name
                        """
                    ).fetchall()
                ]
                schema_version_before_restart = connection.execute(
                    "PRAGMA schema_version"
                ).fetchone()[0]
            finally:
                connection.close()

            csv_path = (
                project_root / "exports" / "literature_search_results.csv"
            )
            self.assertTrue(csv_path.is_file())
            self.assertTrue(csv_path.read_bytes().startswith(codecs.BOM_UTF8))
            with csv_path.open(
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as csv_file:
                reader = csv.DictReader(csv_file)
                csv_rows = list(reader)
                csv_columns = reader.fieldnames
            self.assertEqual(len(csv_rows), 1)
            self.assertEqual(csv_rows[0]["id"], "1")
            self.assertEqual(csv_rows[0]["title"], title)
            self.assertEqual(csv_rows[0]["tags"], tag_name)
            self.assertIsNotNone(csv_columns)
            assert csv_columns is not None
            self.assertTrue(
                {"usage_type", "project_name", "usage_note", "used_at"}
                .isdisjoint(csv_columns)
            )

            backup_paths = sorted((project_root / "backups").glob("*.sqlite3"))
            self.assertEqual(len(backup_paths), 1)
            backup_connection = sqlite3.connect(backup_paths[0])
            try:
                self.assertEqual(
                    backup_connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    backup_connection.execute(
                        "SELECT id, title FROM literature"
                    ).fetchall(),
                    [(1, title)],
                )
                self.assertEqual(
                    backup_connection.execute(
                        """
                        SELECT tags.name
                        FROM tags
                        JOIN literature_tags
                            ON literature_tags.tag_id = tags.id
                        WHERE literature_tags.literature_id = ?
                        """,
                        (1,),
                    ).fetchall(),
                    [(tag_name,)],
                )
                self.assertEqual(
                    backup_connection.execute(
                        "SELECT usage_type FROM usage_history"
                    ).fetchall(),
                    [(usage_type,)],
                )
            finally:
                backup_connection.close()

            session_two_actions = [
                "1",
                "8",
                "1",
                "2",
                title,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "0",
            ]
            _, session_two_outputs = self._run_application_with_real_cli(
                project_root,
                session_two_actions,
            )

            session_two_text = "\n".join(session_two_outputs)
            self.assertIn("文献詳細:", session_two_outputs)
            self.assertGreaterEqual(session_two_text.count(f"title: {title}"), 3)
            self.assertIn(f"name: {tag_name}", session_two_text)
            self.assertIn(f"usage_type: {usage_type}", session_two_text)
            self.assertEqual(database_path.stat().st_ino, database_inode)

            connection = connect_database(database_path)
            try:
                self.assertEqual(
                    self._table_snapshot(connection),
                    snapshot_before_restart,
                )
                schema_after_restart = [
                    tuple(row)
                    for row in connection.execute(
                        """
                        SELECT type, name, tbl_name, sql
                        FROM sqlite_master
                        ORDER BY type, name
                        """
                    ).fetchall()
                ]
                self.assertEqual(schema_after_restart, schema_before_restart)
                self.assertEqual(
                    connection.execute("PRAGMA schema_version").fetchone()[0],
                    schema_version_before_restart,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                connection.close()

    def test_duplicate_candidate_can_cancel_without_adding_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            stored_title = "Synthetic duplicate baseline title"
            duplicate_title = "SYNTHETIC DUPLICATE BASELINE TITLE"
            actions = [
                "3",
                *self._registration_values(
                    title=stored_title,
                    authors="Synthetic first author",
                    publication_year="2020",
                ),
                "1",
                "3",
                *self._registration_values(
                    title=duplicate_title,
                    authors="Synthetic second author",
                    publication_year="2021",
                ),
                "0",
                "0",
            ]

            _, outputs = self._run_application_with_real_cli(
                project_root,
                actions,
            )

            output_text = "\n".join(outputs)
            self.assertIn("警告: 重複候補があります。", outputs)
            self.assertIn("一致理由: タイトル類似", output_text)
            self.assertIn("文献登録を中止しました。", outputs)

            connection = connect_database(
                project_root / "data" / "pt_research_library.sqlite3"
            )
            try:
                self.assertEqual(
                    [
                        tuple(row)
                        for row in connection.execute(
                            "SELECT id, title, authors, publication_year "
                            "FROM literature"
                        ).fetchall()
                    ],
                    [(1, stored_title, "Synthetic first author", 2020)],
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
