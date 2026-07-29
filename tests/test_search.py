"""Tests for read-only literature search and filtering."""

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
    get_literature,
)
from src.search import search_literature


class LiteratureSearchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "search.db"
        )
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    def add_record(self, title: str, **values: object) -> int:
        return add_literature(
            self.connection,
            Literature(title=title, **values),
        )

    def populate_standard_records(self) -> tuple[int, int]:
        matching_id = self.add_record(
            "title-marker compound-keyword multi-match",
            authors="authors-marker",
            journal="journal-marker",
            publication_year=2025,
            volume="volume-marker",
            issue="issue-marker",
            pages="pages-marker",
            doi="doi-marker",
            pmid="987654321012345",
            url="https://example.test/url-marker",
            language="language-marker",
            publication_type="Original-marker",
            abstract="abstract-marker multi-match",
            pdf_path="/tmp/pdf-path-marker.pdf",
            personal_summary="personal-summary-marker",
            ai_summary="ai-summary-marker",
            ai_summary_status="修正済み",
            general_note="general-note-marker",
            key_findings="key-findings-marker",
            methods_note="methods-note-marker",
            clinical_note="clinical-note-marker 肩関節日本語",
            limitation_note="limitation-note-marker",
            relevance_note="relevance-note-marker",
            evidence_level="evidence-level-marker",
            verification_status="一部確認",
            adoption_status="採用候補",
            exclusion_reason="exclusion-reason-marker",
            rating=5,
        )
        other_id = self.add_record(
            "Other literature",
            publication_year=2024,
            publication_type="Review-marker",
            rating=2,
        )

        self.connection.execute(
            """
            UPDATE literature
            SET created_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "2031-created-markerZ",
                "2031-updated-markerZ",
                matching_id,
            ),
        )
        self.connection.commit()

        for tag_name in ("AHD", "tag-marker", "multi-match-tag"):
            tag_id = create_tag(self.connection, tag_name)
            attach_tag_to_literature(
                self.connection,
                matching_id,
                tag_id,
            )
        other_tag_id = create_tag(self.connection, "other-tag")
        attach_tag_to_literature(
            self.connection,
            other_id,
            other_tag_id,
        )

        create_usage_history(
            self.connection,
            matching_id,
            "conference-marker",
            project_name="project-name-marker",
            usage_note="usage-note-marker multi-match",
        )
        create_usage_history(
            self.connection,
            matching_id,
            "conference-marker",
            project_name="second project",
            usage_note="multi-match second history",
        )
        create_usage_history(
            self.connection,
            other_id,
            "other-usage",
            project_name="other project",
            usage_note="other note",
        )
        return matching_id, other_id

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

    def assert_status_values_are_searchable(
        self,
        field_name: str,
        values: tuple[str, ...],
    ) -> None:
        ids: dict[str, int] = {}
        for value in values:
            ids[value] = self.add_record(
                f"{field_name} {value}",
                **{field_name: value},
            )

        for value in values:
            with self.subTest(field=field_name, value=value):
                result = search_literature(
                    self.connection,
                    **{field_name: value},
                )
                self.assertEqual(
                    [literature.id for literature in result],
                    [ids[value]],
                )
                self.assertEqual(getattr(result[0], field_name), value)

    def test_no_literature_returns_empty_list(self) -> None:
        self.assertEqual(search_literature(self.connection), [])

    def test_no_conditions_returns_all_literature_in_ascending_id_order(
        self,
    ) -> None:
        first_id = self.add_record("First")
        second_id = self.add_record("Second")
        third_id = self.add_record("Third")

        result = search_literature(self.connection)

        self.assertEqual(
            [literature.id for literature in result],
            [first_id, second_id, third_id],
        )

    def test_results_restore_all_fields_nulls_and_literature_objects(
        self,
    ) -> None:
        populated_id, null_id = self.populate_standard_records()
        expected_populated = get_literature(self.connection, populated_id)
        expected_null = get_literature(self.connection, null_id)

        result = search_literature(self.connection)

        self.assertEqual(result, [expected_populated, expected_null])
        self.assertTrue(all(isinstance(item, Literature) for item in result))
        self.assertIsNone(result[1].authors)
        self.assertIsNone(result[1].abstract)
        self.assertIsNone(result[1].personal_summary)
        self.assertIsNone(result[1].ai_summary)
        self.assertIsNone(result[1].general_note)
        self.assertIsNone(result[1].exclusion_reason)
        self.assertNotEqual(result[0].title, result[1].title)
        self.assertNotEqual(result[0].rating, result[1].rating)

    def test_keyword_searches_every_literature_text_column(self) -> None:
        matching_id, _ = self.populate_standard_records()
        search_values = {
            "title": "title-marker",
            "authors": "authors-marker",
            "journal": "journal-marker",
            "volume": "volume-marker",
            "issue": "issue-marker",
            "pages": "pages-marker",
            "doi": "doi-marker",
            "pmid": "987654321012345",
            "url": "url-marker",
            "language": "language-marker",
            "publication_type": "Original-marker",
            "abstract": "abstract-marker",
            "pdf_path": "pdf-path-marker",
            "personal_summary": "personal-summary-marker",
            "ai_summary": "ai-summary-marker",
            "ai_summary_status": "修正済み",
            "general_note": "general-note-marker",
            "key_findings": "key-findings-marker",
            "methods_note": "methods-note-marker",
            "clinical_note": "clinical-note-marker",
            "limitation_note": "limitation-note-marker",
            "relevance_note": "relevance-note-marker",
            "evidence_level": "evidence-level-marker",
            "verification_status": "一部確認",
            "adoption_status": "採用候補",
            "exclusion_reason": "exclusion-reason-marker",
            "created_at": "created-marker",
            "updated_at": "updated-marker",
        }

        for field_name, keyword in search_values.items():
            with self.subTest(field=field_name):
                result = search_literature(
                    self.connection,
                    keyword=keyword,
                )
                self.assertEqual(
                    [literature.id for literature in result],
                    [matching_id],
                )

    def test_keyword_searches_tag_name_and_usage_text_columns(self) -> None:
        matching_id, _ = self.populate_standard_records()

        for source, keyword in (
            ("tag name", "tag-marker"),
            ("usage type", "conference-marker"),
            ("project name", "project-name-marker"),
            ("usage note", "usage-note-marker"),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            keyword=keyword,
                        )
                    ],
                    [matching_id],
                )

    def test_keyword_is_partial_case_insensitive_and_supports_japanese(
        self,
    ) -> None:
        self.connection.execute("PRAGMA case_sensitive_like = OFF")
        self.assertEqual(
            self.connection.execute(
                "SELECT 'lowercase' LIKE 'LOWERCASE'"
            ).fetchone()[0],
            1,
        )
        matching_id, _ = self.populate_standard_records()

        for keyword in ("THORS-MARK", "肩関節"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            keyword=keyword,
                        )
                    ],
                    [matching_id],
                )

    def test_keyword_is_pragma_independent_when_case_sensitive_like_is_on(
        self,
    ) -> None:
        self.connection.execute("PRAGMA case_sensitive_like = ON")
        self.assertEqual(
            self.connection.execute(
                "SELECT 'lowercase' LIKE 'LOWERCASE'"
            ).fetchone()[0],
            0,
        )
        matching_id, _ = self.populate_standard_records()
        self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-case-sensitive-like",),
        )
        before = self.database_snapshot()
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            for source, keyword in (
                ("title", "TITLE-MARKER"),
                ("authors", "AUTHORS-MARKER"),
                ("tag name", "TAG-MARKER"),
                ("usage type", "CONFERENCE-MARKER"),
                ("project name", "PROJECT-NAME-MARKER"),
                ("usage note", "USAGE-NOTE-MARKER"),
                ("Japanese text", "肩関節"),
                ("multiple sources", "MULTI-MATCH"),
            ):
                with self.subTest(source=source):
                    self.assertEqual(
                        [
                            item.id
                            for item in search_literature(
                                self.connection,
                                keyword=keyword,
                            )
                        ],
                        [matching_id],
                    )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(self.database_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    ("PRAGMA", "COMMIT", "ROLLBACK")
                )
                for statement in statements
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT 'lowercase' LIKE 'LOWERCASE'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.connection.rollback()

    def test_keyword_trims_whitespace_and_no_match_returns_empty(self) -> None:
        matching_id, _ = self.populate_standard_records()

        self.assertEqual(
            [
                item.id
                for item in search_literature(
                    self.connection,
                    keyword="  title-marker \n",
                )
            ],
            [matching_id],
        )
        self.assertEqual(
            search_literature(self.connection, keyword="missing-keyword"),
            [],
        )

    def test_none_empty_and_whitespace_keyword_mean_no_condition(self) -> None:
        matching_id, other_id = self.populate_standard_records()

        for keyword in (None, "", "  ", "\t\n"):
            with self.subTest(keyword=repr(keyword)):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            keyword=keyword,
                        )
                    ],
                    [matching_id, other_id],
                )

    def test_non_string_keyword_is_rejected(self) -> None:
        for keyword in (1, 1.5, True, False, [], {}):
            with self.subTest(keyword=repr(keyword)):
                with self.assertRaisesRegex(ValueError, "keyword"):
                    search_literature(
                        self.connection,
                        keyword=keyword,
                    )

    def test_like_metacharacters_and_backslash_are_literal(self) -> None:
        literal_id = self.add_record(
            r"percent % marker under_score slash \ marker"
        )
        self.add_record("percent X marker underZscore slash X marker")

        for keyword in ("%", "_", "\\"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            keyword=keyword,
                        )
                    ],
                    [literal_id],
                )

    def test_like_special_character_boundaries_are_literal_in_both_modes(
        self,
    ) -> None:
        cases = (
            ("%", "literal percent % end", "literal percent X end"),
            ("_", "literal underscore _ end", "literal underscore X end"),
            ("\\", "literal slash \\ end", "literal slash X end"),
            ("100%", "ratio 100% complete", "ratio 100X complete"),
            ("a_b", "code a_b end", "code aXb end"),
            (
                "C:\\folder",
                "path C:\\folder end",
                "path C:folder end",
            ),
            ("\\%", "pair \\% end", "pair % end"),
            ("\\_", "pair \\_ end", "pair _ end"),
            ("%_\\", "combo %_\\", "wildcard X%"),
        )

        for case_sensitive_like in (False, True):
            for case_number, (keyword, target, decoy) in enumerate(cases):
                with self.subTest(
                    case_sensitive_like=case_sensitive_like,
                    keyword=keyword,
                ):
                    database_path = (
                        Path(self.temporary_directory.name)
                        / (
                            "like-boundary-"
                            f"{int(case_sensitive_like)}-{case_number}.db"
                        )
                    )
                    initialize_database(database_path)
                    connection = connect_database(database_path)
                    try:
                        mode = "ON" if case_sensitive_like else "OFF"
                        connection.execute(
                            f"PRAGMA case_sensitive_like = {mode}"
                        )
                        target_id = add_literature(
                            connection,
                            Literature(title=target),
                        )
                        decoy_id = add_literature(
                            connection,
                            Literature(title=decoy),
                        )

                        result = search_literature(
                            connection,
                            keyword=keyword,
                        )

                        self.assertEqual(
                            [literature.id for literature in result],
                            [target_id],
                        )
                        self.assertNotIn(
                            decoy_id,
                            [literature.id for literature in result],
                        )
                        expected_mode_result = (
                            0 if case_sensitive_like else 1
                        )
                        self.assertEqual(
                            connection.execute(
                                "SELECT 'lowercase' LIKE 'LOWERCASE'"
                            ).fetchone()[0],
                            expected_mode_result,
                        )
                    finally:
                        connection.close()

    def test_keyword_sql_injection_text_is_safe(self) -> None:
        self.populate_standard_records()
        self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)",
            ("pending-injection-transaction",),
        )
        before = self.database_snapshot()
        payloads = (
            "' OR 1=1 --",
            "'; DROP TABLE literature; --",
            '%" OR "1"="1',
            "shoulder'); DELETE FROM tags; --",
        )
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(
                        search_literature(
                            self.connection,
                            keyword=payload,
                        ),
                        [],
                    )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(self.database_snapshot(), before)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(
            {
                row["name"]
                for row in self.connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                ).fetchall()
            },
            {"literature", "tags", "literature_tags", "usage_history"},
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "DROP",
                        "ALTER",
                        "CREATE",
                        "REPLACE",
                        "COMMIT",
                        "ROLLBACK",
                        "PRAGMA",
                    )
                )
                for statement in statements
            )
        )
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        observer = connect_database(self.database_path)
        try:
            self.assertEqual(
                observer.execute(
                    "SELECT COUNT(*) FROM tags WHERE name = ?",
                    ("pending-injection-transaction",),
                ).fetchone()[0],
                0,
            )
        finally:
            observer.close()
        self.connection.rollback()

    def test_search_conditions_are_keyword_only_arguments(self) -> None:
        matching_id = self.add_record("keyword-only contract")

        with self.assertRaises(TypeError):
            search_literature(self.connection, "keyword")

        self.assertEqual(
            [
                literature.id
                for literature in search_literature(
                    self.connection,
                    keyword="keyword",
                )
            ],
            [matching_id],
        )

    def test_multiple_keyword_matches_return_each_literature_once(self) -> None:
        matching_id, _ = self.populate_standard_records()

        result = search_literature(
            self.connection,
            keyword="multi-match",
        )

        self.assertEqual([item.id for item in result], [matching_id])

    def test_year_filter_matches_normal_and_boundary_values(self) -> None:
        normal_id = self.add_record("Normal year", publication_year=2025)
        lower_id = self.add_record("Lower year", publication_year=1800)
        upper_year = date.today().year + 1
        upper_id = self.add_record(
            "Upper year",
            publication_year=upper_year,
        )

        for year, expected_id in (
            (2025, normal_id),
            (1800, lower_id),
            (upper_year, upper_id),
        ):
            with self.subTest(year=year):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            year=year,
                        )
                    ],
                    [expected_id],
                )
        self.assertEqual(
            search_literature(self.connection, year=1900),
            [],
        )

    def test_year_filter_rejects_invalid_values(self) -> None:
        invalid_values = (
            1799,
            date.today().year + 2,
            2025.0,
            "2025",
            True,
            False,
        )

        for year in invalid_values:
            with self.subTest(year=repr(year)):
                with self.assertRaisesRegex(ValueError, "year"):
                    search_literature(self.connection, year=year)

    def test_tag_filter_is_trimmed_case_insensitive_exact_and_isolated(
        self,
    ) -> None:
        matching_id, _ = self.populate_standard_records()

        for tag in ("AHD", "ahd", "  aHd \n"):
            with self.subTest(tag=tag):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            tag=tag,
                        )
                    ],
                    [matching_id],
                )
        self.assertEqual(
            search_literature(self.connection, tag="HD"),
            [],
        )
        self.assertEqual(
            search_literature(self.connection, tag="missing-tag"),
            [],
        )

    def test_tag_filter_rejects_empty_and_non_string_values(self) -> None:
        for tag in ("", "  ", "\t\n", 1, True, [], {}):
            with self.subTest(tag=repr(tag)):
                with self.assertRaisesRegex(ValueError, "tag"):
                    search_literature(self.connection, tag=tag)

    def test_publication_type_filter_is_trimmed_exact_and_isolated(
        self,
    ) -> None:
        matching_id, _ = self.populate_standard_records()

        self.assertEqual(
            [
                item.id
                for item in search_literature(
                    self.connection,
                    publication_type="  Original-marker ",
                )
            ],
            [matching_id],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                publication_type="original-marker",
            ),
            [],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                publication_type="Missing type",
            ),
            [],
        )

    def test_publication_type_rejects_empty_and_non_string_values(self) -> None:
        for publication_type in ("", " ", "\t\n", 1, False, []):
            with self.subTest(publication_type=repr(publication_type)):
                with self.assertRaisesRegex(ValueError, "publication_type"):
                    search_literature(
                        self.connection,
                        publication_type=publication_type,
                    )

    def test_every_verification_status_is_searchable(self) -> None:
        self.assert_status_values_are_searchable(
            "verification_status",
            ("未確認", "一部確認", "確認済み", "要確認"),
        )

    def test_every_adoption_status_is_searchable(self) -> None:
        self.assert_status_values_are_searchable(
            "adoption_status",
            ("未判定", "採用候補", "採用", "除外"),
        )

    def test_every_ai_summary_status_is_searchable(self) -> None:
        self.assert_status_values_are_searchable(
            "ai_summary_status",
            ("未作成", "未確認", "確認済み", "修正済み"),
        )

    def test_status_filters_reject_invalid_empty_and_non_string_values(
        self,
    ) -> None:
        for field_name in (
            "verification_status",
            "adoption_status",
            "ai_summary_status",
        ):
            for value in ("invalid", "", "  ", "\t\n", 1, True, []):
                with self.subTest(field=field_name, value=repr(value)):
                    with self.assertRaisesRegex(ValueError, field_name):
                        search_literature(
                            self.connection,
                            **{field_name: value},
                        )

    def test_valid_status_filters_return_empty_when_no_record_matches(
        self,
    ) -> None:
        self.assertEqual(
            search_literature(
                self.connection,
                verification_status="確認済み",
            ),
            [],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                adoption_status="採用",
            ),
            [],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                ai_summary_status="修正済み",
            ),
            [],
        )

    def test_status_filters_trim_whitespace(self) -> None:
        literature_id = self.add_record(
            "Trimmed statuses",
            verification_status="要確認",
            adoption_status="除外",
            ai_summary_status="確認済み",
        )

        result = search_literature(
            self.connection,
            verification_status=" 要確認 ",
            adoption_status="\n除外\t",
            ai_summary_status="  確認済み ",
        )

        self.assertEqual([item.id for item in result], [literature_id])

    def test_rating_filter_accepts_one_and_five_and_returns_no_nulls(
        self,
    ) -> None:
        one_id = self.add_record("Rating one", rating=1)
        five_id = self.add_record("Rating five", rating=5)
        self.add_record("Rating null")

        self.assertEqual(
            [
                item.id
                for item in search_literature(
                    self.connection,
                    rating=1,
                )
            ],
            [one_id],
        )
        self.assertEqual(
            [
                item.id
                for item in search_literature(
                    self.connection,
                    rating=5,
                )
            ],
            [five_id],
        )
        self.assertEqual(
            search_literature(self.connection, rating=3),
            [],
        )

    def test_rating_filter_rejects_invalid_values(self) -> None:
        for rating in (0, 6, 1.5, "1", True, False):
            with self.subTest(rating=repr(rating)):
                with self.assertRaisesRegex(ValueError, "rating"):
                    search_literature(
                        self.connection,
                        rating=rating,
                    )

    def test_usage_type_filter_is_trimmed_exact_deduplicated_and_isolated(
        self,
    ) -> None:
        matching_id, _ = self.populate_standard_records()

        self.assertEqual(
            [
                item.id
                for item in search_literature(
                    self.connection,
                    usage_type="  conference-marker ",
                )
            ],
            [matching_id],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                usage_type="CONFERENCE-MARKER",
            ),
            [],
        )
        self.assertEqual(
            search_literature(
                self.connection,
                usage_type="missing-usage",
            ),
            [],
        )

    def test_usage_type_rejects_empty_and_non_string_values(self) -> None:
        for usage_type in ("", " ", "\t\n", 1, True, [], {}):
            with self.subTest(usage_type=repr(usage_type)):
                with self.assertRaisesRegex(ValueError, "usage_type"):
                    search_literature(
                        self.connection,
                        usage_type=usage_type,
                    )

    def test_combined_filters_use_and_for_required_combinations(self) -> None:
        matching_id, _ = self.populate_standard_records()
        cases = (
            {
                "keyword": "compound-keyword",
                "year": 2025,
            },
            {
                "keyword": "compound-keyword",
                "tag": "ahd",
            },
            {
                "tag": "AHD",
                "rating": 5,
            },
            {
                "year": 2025,
                "publication_type": "Original-marker",
                "verification_status": "一部確認",
            },
            {
                "keyword": "compound-keyword",
                "usage_type": "conference-marker",
                "adoption_status": "採用候補",
            },
        )

        for conditions in cases:
            with self.subTest(conditions=conditions):
                self.assertEqual(
                    [
                        item.id
                        for item in search_literature(
                            self.connection,
                            **conditions,
                        )
                    ],
                    [matching_id],
                )

    def test_all_filters_can_be_applied_together(self) -> None:
        matching_id, _ = self.populate_standard_records()

        result = search_literature(
            self.connection,
            keyword="compound-keyword",
            year=2025,
            tag="ahd",
            publication_type="Original-marker",
            verification_status="一部確認",
            adoption_status="採用候補",
            ai_summary_status="修正済み",
            rating=5,
            usage_type="conference-marker",
        )

        self.assertEqual([item.id for item in result], [matching_id])

    def test_one_mismatching_condition_makes_combination_empty(self) -> None:
        self.populate_standard_records()

        result = search_literature(
            self.connection,
            keyword="compound-keyword",
            year=2025,
            tag="ahd",
            publication_type="Original-marker",
            verification_status="一部確認",
            adoption_status="採用候補",
            ai_summary_status="修正済み",
            rating=4,
            usage_type="conference-marker",
        )

        self.assertEqual(result, [])

    def test_search_does_not_change_any_table_or_close_connection(self) -> None:
        matching_id, _ = self.populate_standard_records()
        before = self.database_snapshot()

        result = search_literature(
            self.connection,
            keyword="multi-match",
            tag="AHD",
            usage_type="conference-marker",
        )

        self.assertEqual([item.id for item in result], [matching_id])
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_search_does_not_commit_callers_pending_transaction(self) -> None:
        cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending literature",),
        )
        pending_id = cursor.lastrowid
        self.assertTrue(self.connection.in_transaction)

        result = search_literature(
            self.connection,
            keyword="Pending",
        )

        self.assertEqual([item.id for item in result], [pending_id])
        self.assertTrue(self.connection.in_transaction)
        observer = connect_database(self.database_path)
        try:
            self.assertEqual(
                observer.execute(
                    "SELECT COUNT(*) FROM literature WHERE id = ?",
                    (pending_id,),
                ).fetchone()[0],
                0,
            )
        finally:
            observer.close()
        self.connection.rollback()
        self.assertIsNone(get_literature(self.connection, pending_id))

    def test_search_executes_no_commit_statement(self) -> None:
        self.populate_standard_records()
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        try:
            search_literature(
                self.connection,
                keyword="compound-keyword",
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertFalse(
            any(
                statement.lstrip().upper().startswith("COMMIT")
                for statement in statements
            )
        )

    def test_invalid_filters_are_rejected_before_search_sql_executes(
        self,
    ) -> None:
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)
        try:
            with self.assertRaises(ValueError):
                search_literature(
                    self.connection,
                    year=date.today().year + 2,
                    tag="AHD",
                )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(statements, [])


if __name__ == "__main__":
    unittest.main()
