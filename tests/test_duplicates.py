"""Tests for read-only duplicate-candidate detection."""

import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import src.duplicates as duplicates_module
from src.database import connect_database, initialize_database
from src.duplicates import (
    TITLE_SIMILARITY_THRESHOLD,
    DuplicateCandidate,
    calculate_title_similarity,
    find_duplicate_candidates,
    normalize_doi,
    normalize_pmid,
    normalize_title,
)
from src.models import Literature
from src.repository import (
    add_literature,
    attach_tag_to_literature,
    create_tag,
    create_usage_history,
    get_literature,
)


class NormalizationTestCase(unittest.TestCase):
    def test_normalize_doi_handles_empty_values(self) -> None:
        for value in (None, "", "   ", "\t\n"):
            with self.subTest(value=repr(value)):
                self.assertIsNone(normalize_doi(value))

    def test_normalize_doi_applies_nfkc_trim_and_lowercase(self) -> None:
        self.assertEqual(
            normalize_doi("  １０．１０００／ＡＢＣ  "),
            "10.1000/abc",
        )

    def test_normalize_doi_removes_each_supported_prefix_case_insensitively(
        self,
    ) -> None:
        prefixes = (
            "doi:",
            "DOI:",
            "https://doi.org/",
            "HTTP://DOI.ORG/",
            "https://dx.doi.org/",
            "HTTP://DX.DOI.ORG/",
        )

        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    normalize_doi(f" {prefix} 10.1000/ABC "),
                    "10.1000/abc",
                )

    def test_normalize_doi_returns_none_after_prefix_removal(self) -> None:
        for value in ("doi:", "DOI:  ", "https://doi.org/   "):
            with self.subTest(value=value):
                self.assertIsNone(normalize_doi(value))

    def test_normalize_doi_rejects_non_strings(self) -> None:
        for value in (1, 1.5, True, False, [], {}):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "doi"):
                    normalize_doi(value)

    def test_normalize_doi_preserves_unspecified_internal_characters(self) -> None:
        self.assertEqual(
            normalize_doi(" DOI:10.1000/A_B (C) "),
            "10.1000/a_b (c)",
        )

    def test_normalize_doi_removes_only_a_leading_prefix(self) -> None:
        self.assertEqual(
            normalize_doi("prefix-doi:10.1000/abc"),
            "prefix-doi:10.1000/abc",
        )
        self.assertEqual(
            normalize_doi("10.1000/doi:test"),
            "10.1000/doi:test",
        )

    def test_normalize_pmid_handles_empty_values(self) -> None:
        for value in (None, "", "   ", "\t\n", "PMID:", "pmid: \t"):
            with self.subTest(value=repr(value)):
                self.assertIsNone(normalize_pmid(value))

    def test_normalize_pmid_removes_prefix_and_all_internal_whitespace(self) -> None:
        for value in (
            "PMID: 12345678",
            "pmid:12 345 678",
            " 12\t345\n678 ",
        ):
            with self.subTest(value=repr(value)):
                self.assertEqual(normalize_pmid(value), "12345678")

    def test_normalize_pmid_applies_nfkc_and_preserves_leading_zeroes(
        self,
    ) -> None:
        self.assertEqual(normalize_pmid("ＰＭＩＤ： ００１２３"), "00123")

    def test_normalize_pmid_accepts_only_ascii_digits_after_nfkc(self) -> None:
        for value in ("١٢٣٤", "۱۲۳۴", "१२३४", "12٣4"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "pmid"):
                    normalize_pmid(value)

        self.assertEqual(normalize_pmid("００１２３"), "00123")
        self.assertEqual(normalize_pmid("000123"), "000123")

    def test_normalize_pmid_removes_only_a_leading_prefix(self) -> None:
        self.assertEqual(normalize_pmid("PMID:123"), "123")
        with self.assertRaisesRegex(ValueError, "pmid"):
            normalize_pmid("text PMID:123")

    def test_normalize_pmid_rejects_non_digit_formats(self) -> None:
        for value in ("PMID:12A34", "123.45", "12-34", "+123", "12/34"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "pmid"):
                    normalize_pmid(value)

    def test_normalize_pmid_rejects_non_strings(self) -> None:
        for value in (1, 1.5, True, False, [], {}):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "pmid"):
                    normalize_pmid(value)

    def test_normalize_title_applies_case_whitespace_and_nfkc(self) -> None:
        self.assertEqual(
            normalize_title("  ＳＨＯＵＬＤＥＲ\tStudy\n２０２６  "),
            "shoulder study 2026",
        )

    def test_normalize_title_replaces_english_and_japanese_punctuation(
        self,
    ) -> None:
        self.assertEqual(
            normalize_title("Shoulder—Ultrasound: Study, Part-2"),
            "shoulder ultrasound study part 2",
        )
        self.assertEqual(
            normalize_title("肩関節：超音波による評価。"),
            "肩関節 超音波による評価",
        )

    def test_normalize_title_preserves_characters_numbers_and_word_order(
        self,
    ) -> None:
        self.assertEqual(
            normalize_title("棘上筋 2 + 肩関節"),
            "棘上筋 2 + 肩関節",
        )
        self.assertEqual(
            normalize_title("Third First Second"),
            "third first second",
        )

    def test_normalize_title_rejects_empty_or_punctuation_only_values(
        self,
    ) -> None:
        for value in ("", "   ", "\t\n", "...", "：—、。"):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "title"):
                    normalize_title(value)

    def test_normalize_title_rejects_non_strings(self) -> None:
        for value in (None, 1, 1.5, True, False, [], {}):
            with self.subTest(value=repr(value)):
                with self.assertRaisesRegex(ValueError, "title"):
                    normalize_title(value)

    def test_title_similarity_normalizes_both_titles(self) -> None:
        self.assertEqual(
            calculate_title_similarity(
                " Shoulder—Ultrasound: Study ",
                "shoulder ultrasound study",
            ),
            1.0,
        )

    def test_title_similarity_has_known_boundary_values(self) -> None:
        self.assertEqual(
            calculate_title_similarity("1234567890", "123456789x"),
            0.9,
        )
        self.assertLess(
            calculate_title_similarity("1234567890", "12345678xy"),
            0.9,
        )

    def test_title_similarity_for_different_titles_is_bounded_below_one(
        self,
    ) -> None:
        similarity = calculate_title_similarity(
            "shoulder ultrasound",
            "measurement reliability",
        )

        self.assertGreaterEqual(similarity, 0.0)
        self.assertLess(similarity, 1.0)
        self.assertLessEqual(similarity, 1.0)

    def test_title_similarity_rejects_invalid_titles(self) -> None:
        for title_a, title_b in (("", "valid"), ("valid", "..."), (1, "valid")):
            with self.subTest(title_a=repr(title_a), title_b=repr(title_b)):
                with self.assertRaises(ValueError):
                    calculate_title_similarity(title_a, title_b)


class DuplicateCandidateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "duplicates.db"
        )
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)

    def add_record(self, title: str, **values: object) -> int:
        return add_literature(
            self.connection,
            Literature(title=title, **values),
        )

    def insert_legacy_record(
        self,
        title: str,
        *,
        doi: object = None,
        pmid: object = None,
    ) -> int:
        """Insert legacy/corrupt identifiers without the repository write path."""
        cursor = self.connection.execute(
            """
            INSERT INTO literature (title, doi, pmid)
            VALUES (?, ?, ?)
            """,
            (title, doi, pmid),
        )
        self.connection.commit()
        self.assertIsNotNone(cursor.lastrowid)
        assert cursor.lastrowid is not None
        return cursor.lastrowid

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

    def test_empty_database_and_no_match_return_empty_lists(self) -> None:
        self.assertEqual(
            find_duplicate_candidates(self.connection, title="Incoming title"),
            [],
        )
        self.add_record(
            "Stored unrelated title",
            doi="10.1000/stored",
            pmid="11111111",
        )

        self.assertEqual(
            find_duplicate_candidates(
                self.connection,
                title="Incoming distinct work",
                doi="10.1000/incoming",
                pmid="22222222",
            ),
            [],
        )

    def test_candidate_restores_literature_all_fields_and_nulls(self) -> None:
        populated_id = self.add_record(
            "Shared candidate title",
            authors="Synthetic Author",
            journal="Synthetic Journal",
            publication_year=2025,
            volume="12",
            issue="3",
            pages="10-20",
            doi="10.1000/shared",
            pmid="00123456",
            url="https://example.test/record",
            language="en",
            publication_type="Synthetic Type",
            abstract="Synthetic abstract",
            pdf_path="/tmp/synthetic.pdf",
            personal_summary="Synthetic personal summary",
            ai_summary="Synthetic AI summary",
            ai_summary_status="確認済み",
            general_note="Synthetic general note",
            key_findings="Synthetic findings",
            methods_note="Synthetic methods",
            clinical_note="Synthetic clinical note",
            limitation_note="Synthetic limitation",
            relevance_note="Synthetic relevance",
            evidence_level="Synthetic level",
            verification_status="確認済み",
            adoption_status="採用",
            exclusion_reason="Synthetic reason",
            rating=5,
        )
        null_id = self.add_record("Shared candidate title")
        expected_populated = get_literature(self.connection, populated_id)
        expected_null = get_literature(self.connection, null_id)

        result = find_duplicate_candidates(
            self.connection,
            title="shared candidate title",
        )

        self.assertEqual(
            [candidate.literature for candidate in result],
            [expected_populated, expected_null],
        )
        self.assertTrue(
            all(isinstance(candidate, DuplicateCandidate) for candidate in result)
        )
        self.assertIsNone(result[1].literature.doi)
        self.assertIsNone(result[1].literature.pmid)
        self.assertIsNone(result[1].literature.abstract)
        self.assertIsNone(result[1].literature.rating)

    def test_non_string_stored_title_row_is_skipped_safely_and_read_only(
        self,
    ) -> None:
        normal_id = self.add_record(
            "Normal candidate with preserved fields",
            authors="Preserved Author",
            journal="Preserved Journal",
            publication_year=2026,
            volume="7",
            issue="2",
            pages="20-30",
            doi="10.1000/corrupt-title",
            pmid="001234",
            url="https://example.test/preserved",
            language="en",
            publication_type="Preserved Type",
            abstract=None,
            pdf_path="/tmp/preserved.pdf",
            personal_summary="Preserved personal summary",
            ai_summary=None,
            ai_summary_status="未確認",
            general_note="Preserved general note",
            key_findings="Preserved findings",
            methods_note=None,
            clinical_note="Preserved clinical note",
            limitation_note=None,
            relevance_note="Preserved relevance",
            evidence_level=None,
            verification_status="一部確認",
            adoption_status="採用候補",
            exclusion_reason=None,
            rating=4,
        )
        corrupt_doi_cursor = self.connection.execute(
            """
            INSERT INTO literature (title, doi, pmid)
            VALUES (?, ?, ?)
            """,
            (
                sqlite3.Binary(b"corrupt-doi-title"),
                "10.1000/corrupt-title",
                "999999",
            ),
        )
        corrupt_pmid_cursor = self.connection.execute(
            """
            INSERT INTO literature (title, doi, pmid)
            VALUES (?, ?, ?)
            """,
            (
                sqlite3.Binary(b"corrupt-pmid-title"),
                "10.1000/other",
                "001234",
            ),
        )
        corrupt_doi_id = corrupt_doi_cursor.lastrowid
        corrupt_pmid_id = corrupt_pmid_cursor.lastrowid
        self.connection.commit()
        self.assertIsNotNone(corrupt_doi_id)
        self.assertIsNotNone(corrupt_pmid_id)
        assert corrupt_doi_id is not None
        assert corrupt_pmid_id is not None

        tag_id = create_tag(self.connection, "corrupt-title-safety")
        attach_tag_to_literature(self.connection, normal_id, tag_id)
        attach_tag_to_literature(self.connection, corrupt_doi_id, tag_id)
        attach_tag_to_literature(self.connection, corrupt_pmid_id, tag_id)
        create_usage_history(self.connection, normal_id, "normal-use")
        create_usage_history(self.connection, corrupt_doi_id, "corrupt-doi-use")
        create_usage_history(
            self.connection,
            corrupt_pmid_id,
            "corrupt-pmid-use",
        )
        expected_normal = get_literature(self.connection, normal_id)
        before = self.database_snapshot()
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            result = find_duplicate_candidates(
                self.connection,
                title="Normal candidate with preserved fields",
                doi="doi:10.1000/CORRUPT-TITLE",
                pmid="PMID: 001234",
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual([item.literature.id for item in result], [normal_id])
        self.assertEqual(result[0].literature, expected_normal)
        self.assertEqual(
            result[0].match_reasons,
            ("doi", "pmid", "title"),
        )
        self.assertIsNone(result[0].literature.abstract)
        self.assertIsNone(result[0].literature.ai_summary)
        self.assertIsNone(result[0].literature.methods_note)
        self.assertIsNone(result[0].literature.exclusion_reason)
        result_ids = [item.literature.id for item in result]
        self.assertNotIn(corrupt_doi_id, result_ids)
        self.assertNotIn(corrupt_pmid_id, result_ids)
        self.assertEqual(self.database_snapshot(), before)
        corrupt_titles = self.connection.execute(
            """
            SELECT id, title
            FROM literature
            WHERE id IN (?, ?)
            ORDER BY id ASC
            """,
            (corrupt_doi_id, corrupt_pmid_id),
        ).fetchall()
        self.assertEqual(
            [(row["id"], row["title"]) for row in corrupt_titles],
            [
                (corrupt_doi_id, b"corrupt-doi-title"),
                (corrupt_pmid_id, b"corrupt-pmid-title"),
            ],
        )
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(
            sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            ),
            1,
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "COMMIT",
                        "ROLLBACK",
                        "ALTER",
                        "DROP",
                    )
                )
                for statement in statements
            )
        )

    def test_duplicate_candidate_is_frozen_and_search_does_not_mutate_record(
        self,
    ) -> None:
        literature_id = self.add_record(
            "Immutable candidate",
            authors="Stored Author",
        )
        before = get_literature(self.connection, literature_id)

        candidate = find_duplicate_candidates(
            self.connection,
            title="Immutable candidate",
        )[0]

        self.assertEqual(candidate.literature, before)
        with self.assertRaises(FrozenInstanceError):
            candidate.title_similarity = 0.0  # type: ignore[misc]
        self.assertEqual(get_literature(self.connection, literature_id), before)

    def test_doi_matches_across_case_and_supported_formats(self) -> None:
        literature_id = self.insert_legacy_record(
            "Stored DOI candidate",
            doi="DOI:10.1000/ABC",
        )

        for incoming_doi in (
            "10.1000/abc",
            "doi:10.1000/abc",
            "https://doi.org/10.1000/ABC",
            "http://dx.doi.org/10.1000/ABC",
        ):
            with self.subTest(incoming_doi=incoming_doi):
                result = find_duplicate_candidates(
                    self.connection,
                    title="Incoming unrelated title",
                    doi=incoming_doi,
                )
                self.assertEqual(
                    [(item.literature.id, item.match_reasons) for item in result],
                    [(literature_id, ("doi",))],
                )

    def test_doi_none_or_different_values_do_not_match(self) -> None:
        self.add_record("Stored DOI record", doi="10.1000/stored")
        self.add_record("Stored null DOI")

        for incoming_doi in (None, "10.1000/different"):
            with self.subTest(incoming_doi=incoming_doi):
                self.assertEqual(
                    find_duplicate_candidates(
                        self.connection,
                        title="Unrelated incoming record",
                        doi=incoming_doi,
                    ),
                    [],
                )

    def test_pmid_matches_across_prefix_and_internal_whitespace(self) -> None:
        literature_id = self.insert_legacy_record(
            "Stored PMID candidate",
            pmid="PMID: 12 345 678",
        )

        for incoming_pmid in ("12345678", "pmid:12345678", "12 345 678"):
            with self.subTest(incoming_pmid=incoming_pmid):
                result = find_duplicate_candidates(
                    self.connection,
                    title="Incoming unrelated title",
                    pmid=incoming_pmid,
                )
                self.assertEqual(
                    [(item.literature.id, item.match_reasons) for item in result],
                    [(literature_id, ("pmid",))],
                )

    def test_pmid_none_or_different_values_do_not_match(self) -> None:
        self.add_record("Stored PMID record", pmid="12345678")
        self.add_record("Stored null PMID")

        for incoming_pmid in (None, "87654321"):
            with self.subTest(incoming_pmid=incoming_pmid):
                self.assertEqual(
                    find_duplicate_candidates(
                        self.connection,
                        title="Unrelated incoming record",
                        pmid=incoming_pmid,
                    ),
                    [],
                )

    def test_invalid_incoming_pmid_fails_before_database_query(self) -> None:
        literature_id = self.add_record("Preserved invalid-input record")
        tag_id = create_tag(self.connection, "preserved-invalid-input-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        create_usage_history(self.connection, literature_id, "preserved-use")
        before = self.database_snapshot()
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            for pmid in ("١٢٣٤", "۱۲۳۴", "१२३४"):
                with self.subTest(pmid=pmid):
                    with self.assertRaisesRegex(ValueError, "pmid"):
                        find_duplicate_candidates(
                            self.connection,
                            title="Valid incoming title",
                            pmid=pmid,
                        )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(statements, [])
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)

    def test_invalid_stored_identifiers_are_ignored_without_stopping_other_matches(
        self,
    ) -> None:
        doi_id = self.insert_legacy_record(
            "Stored invalid PMID",
            doi="doi:10.1000/safe",
            pmid="12A34",
        )
        title_id = self.insert_legacy_record(
            "Shared normalized title",
            doi=sqlite3.Binary(b"invalid"),
            pmid=sqlite3.Binary(b"invalid"),
        )

        result = find_duplicate_candidates(
            self.connection,
            title="Shared normalized title",
            doi="https://doi.org/10.1000/SAFE",
            pmid="99999999",
        )

        self.assertEqual(
            [(item.literature.id, item.match_reasons) for item in result],
            [(doi_id, ("doi",)), (title_id, ("title",))],
        )

    def test_non_ascii_stored_pmids_are_ignored_but_other_matches_continue(
        self,
    ) -> None:
        ignored_pmid_id = self.insert_legacy_record(
            "Stored non-ASCII PMID only",
            pmid="١٢٣٤",
        )
        doi_id = self.insert_legacy_record(
            "Stored non-ASCII PMID with DOI",
            doi="10.1000/non-ascii-pmid",
            pmid="۱۲۳۴",
        )
        title_id = self.insert_legacy_record(
            "Shared title despite non-ASCII PMID",
            pmid="१२३४",
        )

        result = find_duplicate_candidates(
            self.connection,
            title="Shared title despite non-ASCII PMID",
            doi="doi:10.1000/NON-ASCII-PMID",
            pmid="1234",
        )

        self.assertEqual(
            [(item.literature.id, item.match_reasons) for item in result],
            [(doi_id, ("doi",)), (title_id, ("title",))],
        )
        self.assertNotIn(
            ignored_pmid_id,
            [item.literature.id for item in result],
        )

    def test_title_matches_normalization_variants_including_japanese(self) -> None:
        english_id = self.add_record("Shoulder—Ultrasound: Study ２０２６")
        japanese_id = self.add_record("肩関節：超音波による評価")

        english_result = find_duplicate_candidates(
            self.connection,
            title=" shoulder ultrasound study 2026 ",
        )
        japanese_result = find_duplicate_candidates(
            self.connection,
            title="肩関節 超音波による評価。",
        )

        self.assertEqual(
            [(item.literature.id, item.match_reasons) for item in english_result],
            [(english_id, ("title",))],
        )
        self.assertEqual(english_result[0].title_similarity, 1.0)
        self.assertEqual(
            [(item.literature.id, item.match_reasons) for item in japanese_result],
            [(japanese_id, ("title",))],
        )
        self.assertEqual(japanese_result[0].title_similarity, 1.0)

    def test_title_similarity_threshold_includes_exactly_point_nine(self) -> None:
        included_id = self.add_record("123456789x")
        self.add_record("12345678xy")

        result = find_duplicate_candidates(
            self.connection,
            title="1234567890",
        )

        self.assertEqual(TITLE_SIMILARITY_THRESHOLD, 0.90)
        self.assertEqual([item.literature.id for item in result], [included_id])
        self.assertEqual(result[0].title_similarity, 0.90)
        self.assertEqual(result[0].match_reasons, ("title",))

    def test_search_normalizes_each_new_and_stored_title_once(self) -> None:
        first_id = self.add_record("Shared normalized title")
        self.add_record("Different stored title")

        with patch.object(
            duplicates_module,
            "normalize_title",
            wraps=normalize_title,
        ) as normalize:
            result = find_duplicate_candidates(
                self.connection,
                title="Shared normalized title",
            )

        self.assertEqual([item.literature.id for item in result], [first_id])
        self.assertEqual(
            [call.args for call in normalize.call_args_list],
            [
                ("Shared normalized title",),
                ("Shared normalized title",),
                ("Different stored title",),
            ],
        )

    def test_invalid_incoming_title_is_rejected(self) -> None:
        for title in ("", "  ", "...", None, 1):
            with self.subTest(title=repr(title)):
                with self.assertRaisesRegex(ValueError, "title"):
                    find_duplicate_candidates(self.connection, title=title)

    def test_multiple_match_reasons_are_combined_once_in_fixed_order(
        self,
    ) -> None:
        all_id = self.add_record(
            "Shared incoming title",
            doi="10.1000/all",
            pmid="11111111",
        )
        doi_title_id = self.add_record(
            "Shared incoming title",
            doi="10.1000/all",
            pmid="22222222",
        )
        pmid_title_id = self.add_record(
            "Shared incoming title",
            doi="10.1000/other",
            pmid="11111111",
        )

        result = find_duplicate_candidates(
            self.connection,
            title="Shared incoming title",
            doi="doi:10.1000/ALL",
            pmid="PMID: 11111111",
        )

        self.assertEqual(
            [(item.literature.id, item.match_reasons) for item in result],
            [
                (all_id, ("doi", "pmid", "title")),
                (doi_title_id, ("doi", "title")),
                (pmid_title_id, ("pmid", "title")),
            ],
        )
        self.assertEqual(len({item.literature.id for item in result}), 3)
        self.assertTrue(all(item.title_similarity == 1.0 for item in result))

    def test_identifier_only_candidate_still_keeps_title_similarity(self) -> None:
        literature_id = self.add_record(
            "Stored title for similarity",
            doi="10.1000/identifier",
        )
        expected_similarity = calculate_title_similarity(
            "Incoming different title",
            "Stored title for similarity",
        )

        result = find_duplicate_candidates(
            self.connection,
            title="Incoming different title",
            doi="10.1000/identifier",
        )

        self.assertEqual(result[0].literature.id, literature_id)
        self.assertEqual(result[0].match_reasons, ("doi",))
        self.assertEqual(result[0].title_similarity, expected_similarity)

    def test_unusable_stored_title_does_not_hide_identifier_match(self) -> None:
        literature_id = self.add_record("...", doi="10.1000/punctuation")

        result = find_duplicate_candidates(
            self.connection,
            title="Incoming valid title",
            doi="10.1000/punctuation",
        )

        self.assertEqual(result[0].literature.id, literature_id)
        self.assertEqual(result[0].match_reasons, ("doi",))
        self.assertEqual(result[0].title_similarity, 0.0)

    def test_results_sort_by_identifier_priority_similarity_then_id(self) -> None:
        title_only_low_id = self.add_record("123456789x")
        pmid_only_id = self.add_record(
            "PMID distinct title",
            pmid="12345678",
        )
        doi_low_similarity_id = self.add_record(
            "DOI distinct title",
            doi="10.1000/order",
        )
        doi_high_similarity_id = self.add_record(
            "123456789x",
            doi="10.1000/order",
        )
        same_similarity_first_id = self.add_record("123456789y")
        same_similarity_second_id = self.add_record("123456789z")

        result = find_duplicate_candidates(
            self.connection,
            title="1234567890",
            doi="10.1000/order",
            pmid="12345678",
        )

        self.assertEqual(
            [item.literature.id for item in result],
            [
                doi_high_similarity_id,
                doi_low_similarity_id,
                pmid_only_id,
                title_only_low_id,
                same_similarity_first_id,
                same_similarity_second_id,
            ],
        )

    def test_results_with_same_reason_sort_by_similarity_descending(self) -> None:
        lower_similarity_id = self.add_record(
            "1234567xyz",
            doi="10.1000/same-reason",
        )
        higher_similarity_id = self.add_record(
            "12345678xy",
            doi="10.1000/same-reason",
        )

        result = find_duplicate_candidates(
            self.connection,
            title="1234567890",
            doi="10.1000/same-reason",
        )

        self.assertEqual(
            [item.literature.id for item in result],
            [higher_similarity_id, lower_similarity_id],
        )
        self.assertTrue(
            all(item.match_reasons == ("doi",) for item in result)
        )

    def test_search_is_read_only_and_keeps_connection_usable(self) -> None:
        literature_id = self.add_record("Read-only candidate")
        tag_id = create_tag(self.connection, "read-only-tag")
        attach_tag_to_literature(self.connection, literature_id, tag_id)
        create_usage_history(self.connection, literature_id, "test-use")
        before = self.database_snapshot()
        statements: list[str] = []

        self.connection.set_trace_callback(statements.append)
        try:
            result = find_duplicate_candidates(
                self.connection,
                title="Read-only candidate",
            )
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual([item.literature.id for item in result], [literature_id])
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.connection.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(
            sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            ),
            1,
        )
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "COMMIT",
                        "ROLLBACK",
                        "ALTER",
                        "DROP",
                    )
                )
                for statement in statements
            )
        )

    def test_search_preserves_callers_uncommitted_transaction_visibility(
        self,
    ) -> None:
        cursor = self.connection.execute(
            "INSERT INTO literature (title) VALUES (?)",
            ("Pending duplicate candidate",),
        )
        pending_id = cursor.lastrowid
        before = self.database_snapshot()
        self.assertTrue(self.connection.in_transaction)

        result = find_duplicate_candidates(
            self.connection,
            title="Pending duplicate candidate",
        )

        self.assertEqual([item.literature.id for item in result], [pending_id])
        self.assertEqual(self.database_snapshot(), before)
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

    def test_public_search_arguments_after_connection_are_keyword_only(
        self,
    ) -> None:
        literature_id = self.add_record("Keyword-only duplicate")

        with self.assertRaises(TypeError):
            find_duplicate_candidates(self.connection, "Keyword-only duplicate")

        result = find_duplicate_candidates(
            self.connection,
            title="Keyword-only duplicate",
        )
        self.assertEqual([item.literature.id for item in result], [literature_id])


if __name__ == "__main__":
    unittest.main()
