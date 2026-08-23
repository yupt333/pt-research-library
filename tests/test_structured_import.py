"""Tests for strict Contract v1 parsing, preview, and atomic save."""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import structured_repository
from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import add_literature, get_literature, update_literature
from src.structured_import import (
    CONTRACT_VERSION,
    StructuredImportValidationError,
    build_import_preview,
    parse_structured_import,
    save_structured_import,
)


class StructuredImportTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.example_path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "examples"
            / "structured_import_v1.example.json"
        )
        cls.example_text = cls.example_path.read_text(encoding="utf-8")
        cls.example_data = json.loads(cls.example_text)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        database_path = Path(self.temporary_directory.name) / "import.db"
        initialize_database(database_path)
        self.connection = connect_database(database_path)
        self.addCleanup(self.connection.close)
        self.literature_id = add_literature(
            self.connection,
            Literature(
                title="Synthetic Literature for Structured Import Contract",
                authors="Existing synthetic author",
                journal="Existing synthetic journal",
                verification_status="一部確認",
                personal_summary="Existing user summary",
            ),
        )

    def parse_data(self, data: dict | None = None):
        return parse_structured_import(
            json.dumps(
                self.example_data if data is None else data,
                ensure_ascii=False,
            )
        )

    def changed_data(self) -> dict:
        return copy.deepcopy(self.example_data)

    def structured_snapshot(self) -> dict[str, list[tuple[object, ...]]]:
        tables = (
            "structured_entities",
            "structured_fields",
            "evidence_references",
            "structured_field_evidence",
            "structured_entity_evidence",
        )
        return {
            table: [
                tuple(row)
                for row in self.connection.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                ).fetchall()
            ]
            for table in tables
        }

    def build_example_preview(self):
        return build_import_preview(
            self.connection, self.literature_id, self.parse_data()
        )

    def test_official_synthetic_example_parses(self) -> None:
        payload = parse_structured_import(self.example_text)
        self.assertEqual(payload.data["contract_version"], CONTRACT_VERSION)
        self.assertEqual(len(payload.data["outcomes"]), 2)
        self.assertEqual(
            [item["name"]["value"] for item in payload.data["outcomes"]],
            ["Synthetic Displacement", "Synthetic Displacement"],
        )

    def test_invalid_json_prose_fence_and_wrong_top_level_are_rejected(self) -> None:
        cases = (
            "{invalid",
            "prose " + self.example_text,
            self.example_text + " trailing prose",
            "```json\n{}\n```",
            "[]",
        )
        for text in cases:
            with self.subTest(text=text[:20]), self.assertRaises(
                StructuredImportValidationError
            ):
                parse_structured_import(text)

    def test_duplicate_object_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            StructuredImportValidationError, "duplicate object key"
        ):
            parse_structured_import('{"contract_version":"a","contract_version":"b"}')

    def test_nan_infinity_and_negative_infinity_are_rejected(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant), self.assertRaisesRegex(
                StructuredImportValidationError, "non-finite"
            ):
                parse_structured_import(f'{{"value":{constant}}}')

    def test_wrong_contract_version_missing_and_unknown_top_level_are_rejected(
        self,
    ) -> None:
        wrong = self.changed_data()
        wrong["contract_version"] = "pt_research_library_structured_import_v2"
        missing = self.changed_data()
        del missing["study"]
        unknown = self.changed_data()
        unknown["literature_id"] = 1
        for data, path in (
            (wrong, "contract_version"),
            (missing, "study"),
            (unknown, "literature_id"),
        ):
            with self.subTest(path=path), self.assertRaisesRegex(
                StructuredImportValidationError, path
            ):
                self.parse_data(data)

    def test_unknown_nested_key_reports_precise_path(self) -> None:
        data = self.changed_data()
        data["outcomes"][0]["unknown_field"] = "unsafe"
        with self.assertRaisesRegex(
            StructuredImportValidationError, r"outcomes\[0\]\.unknown_field"
        ):
            self.parse_data(data)

    def test_user_verified_anywhere_is_rejected(self) -> None:
        data = self.changed_data()
        data["research_relevance"]["summary"]["verification"] = "user_verified"
        with self.assertRaisesRegex(
            StructuredImportValidationError,
            "research_relevance.summary.verification",
        ):
            self.parse_data(data)
        data = self.changed_data()
        data["study"]["group_allocation"]["value"]["verification"] = (
            "user_verified"
        )
        with self.assertRaisesRegex(StructuredImportValidationError, "user_verified"):
            self.parse_data(data)

    def test_invalid_availability_and_value_consistency_are_rejected(self) -> None:
        cases = []
        invalid = self.changed_data()
        invalid["study"]["sample_size"]["availability"] = "missing"
        cases.append(invalid)
        reported_null = self.changed_data()
        reported_null["study"]["sample_size"]["value"] = None
        cases.append(reported_null)
        nonreported_value = self.changed_data()
        nonreported_value["study"]["demographics"]["value"] = "guessed"
        cases.append(nonreported_value)
        for data in cases:
            with self.subTest(), self.assertRaises(StructuredImportValidationError):
                self.parse_data(data)

    def test_bibliography_field_types_and_identifier_validation(self) -> None:
        cases = []
        invalid_title = self.changed_data()
        invalid_title["bibliography"]["title"]["value"] = "  "
        cases.append(invalid_title)
        invalid_authors = self.changed_data()
        invalid_authors["bibliography"]["authors"].update(
            value=["Author", 2], availability="reported"
        )
        cases.append(invalid_authors)
        invalid_year = self.changed_data()
        invalid_year["bibliography"]["publication_year"].update(
            value=True, availability="reported"
        )
        cases.append(invalid_year)
        invalid_pmid = self.changed_data()
        invalid_pmid["bibliography"]["pmid"].update(
            value="PMID: 12A", availability="reported"
        )
        cases.append(invalid_pmid)
        for data in cases:
            with self.subTest(), self.assertRaises(StructuredImportValidationError):
                self.parse_data(data)

    def test_duplicate_local_ids_and_dangling_evidence_refs_are_rejected(self) -> None:
        duplicate_outcome = self.changed_data()
        duplicate_outcome["outcomes"][1]["outcome_id"] = "outcome_1"
        duplicate_result = self.changed_data()
        duplicate_result["outcomes"][1]["results"][0]["result_id"] = "result_1"
        dangling = self.changed_data()
        dangling["outcomes"][0]["name"]["evidence_refs"] = ["missing_evidence"]
        for data in (duplicate_outcome, duplicate_result, dangling):
            with self.subTest(), self.assertRaises(StructuredImportValidationError):
                self.parse_data(data)

    def test_evidence_self_reference_invalid_page_and_empty_evidence_are_rejected(
        self,
    ) -> None:
        self_reference = self.changed_data()
        self_reference["evidence"][0]["pdf_page"]["evidence_refs"] = ["evidence_1"]
        invalid_page = self.changed_data()
        invalid_page["evidence"][0]["pdf_page"]["value"] = 0
        empty = self.changed_data()
        for locator in (
            "pdf_page",
            "printed_page",
            "section",
            "subsection",
            "table",
            "figure",
            "quote_text",
        ):
            empty["evidence"][0][locator].update(
                value=None, availability="not_extracted", evidence_refs=[]
            )
        for data in (self_reference, invalid_page, empty):
            with self.subTest(), self.assertRaises(StructuredImportValidationError):
                self.parse_data(data)

    def test_malformed_outcome_result_metric_and_unknown_scope_are_rejected(self) -> None:
        malformed_outcome = self.changed_data()
        del malformed_outcome["outcomes"][0]["definition"]
        malformed_result = self.changed_data()
        del malformed_result["outcomes"][0]["results"][0]["result"]
        malformed_metric = self.changed_data()
        del malformed_metric["outcomes"][0]["validation_information"][0][
            "metric_name"
        ]
        unknown_scope = self.changed_data()
        unknown_scope["analysis_metadata"]["analysis_scope"] = "abstract_only"
        for data in (
            malformed_outcome,
            malformed_result,
            malformed_metric,
            unknown_scope,
        ):
            with self.subTest(), self.assertRaises(StructuredImportValidationError):
                self.parse_data(data)

    def test_preview_is_read_only_and_planned_counts_are_exact(self) -> None:
        before = self.structured_snapshot()
        literature_before = get_literature(self.connection, self.literature_id)
        preview = self.build_example_preview()
        self.assertEqual(self.structured_snapshot(), before)
        self.assertEqual(get_literature(self.connection, self.literature_id), literature_before)
        self.assertEqual(
            preview.planned_counts,
            {
                "study": 1,
                "methods": 5,
                "outcomes": 2,
                "results": 2,
                "limitations": 2,
                "concepts": 2,
                "research_relevance": 1,
                "evidence": 4,
                "fields": 96,
                "evidence_links": 68,
            },
        )
        self.assertFalse(preview.blocked)

    def test_preview_target_not_found_does_not_write(self) -> None:
        before = self.structured_snapshot()
        with self.assertRaisesRegex(ValueError, "存在しません"):
            build_import_preview(self.connection, 99999, self.parse_data())
        self.assertEqual(self.structured_snapshot(), before)

    def test_matching_and_conflicting_identifiers(self) -> None:
        matching = self.changed_data()
        matching["bibliography"]["doi"].update(
            value="HTTPS://DOI.ORG/10.1000/SYNTH", availability="reported"
        )
        matching["bibliography"]["pmid"].update(
            value="PMID: 00123", availability="reported"
        )
        update_literature(
            self.connection,
            self.literature_id,
            {"doi": "10.1000/synth", "pmid": "00123"},
        )
        preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data(matching)
        )
        self.assertEqual((preview.doi_state, preview.pmid_state), ("match", "match"))
        self.assertFalse(preview.blocked)

        doi_conflict = copy.deepcopy(matching)
        doi_conflict["bibliography"]["doi"]["value"] = "10.1000/other"
        preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data(doi_conflict)
        )
        self.assertEqual(preview.doi_state, "conflict")
        self.assertTrue(preview.blocked)

        pmid_conflict = copy.deepcopy(matching)
        pmid_conflict["bibliography"]["pmid"]["value"] = "99999"
        preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data(pmid_conflict)
        )
        self.assertEqual(preview.pmid_state, "conflict")
        self.assertTrue(preview.blocked)

    def test_title_mismatch_and_payload_only_identifier_are_warnings(self) -> None:
        data = self.changed_data()
        data["bibliography"]["title"]["value"] = "Completely unrelated synthetic title"
        data["bibliography"]["doi"].update(
            value="10.1000/payload-only", availability="reported"
        )
        preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data(data)
        )
        self.assertLess(preview.title_similarity, 0.9)
        self.assertEqual(preview.doi_state, "payload_only")
        self.assertFalse(preview.blocked)
        self.assertTrue(any("title類似度" in warning for warning in preview.warnings))

    def test_invalid_existing_identifier_blocks_unsafe_comparison(self) -> None:
        self.connection.execute(
            "UPDATE literature SET pmid = ? WHERE id = ?",
            ("invalid-pmid", self.literature_id),
        )
        self.connection.commit()
        preview = self.build_example_preview()
        self.assertTrue(preview.blocked)
        self.assertIn(
            "PMIDを安全に比較できない",
            " ".join(preview.blocking_reasons),
        )

    def test_existing_entity_or_evidence_blocks_without_merge_or_overwrite(self) -> None:
        structured_repository.create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        preview = self.build_example_preview()
        self.assertTrue(preview.blocked)
        self.assertIn("merge / overwrite", " ".join(preview.blocking_reasons))

        evidence_only_literature_id = add_literature(
            self.connection,
            Literature(title="Synthetic Literature for Structured Import Contract"),
        )
        structured_repository.create_evidence_reference(
            self.connection, evidence_only_literature_id, pdf_page=1
        )
        evidence_preview = build_import_preview(
            self.connection, evidence_only_literature_id, self.parse_data()
        )
        self.assertTrue(evidence_preview.blocked)
        self.assertIn(
            "merge / overwrite", " ".join(evidence_preview.blocking_reasons)
        )

    def test_partial_scope_and_not_reported_generate_review_warnings(self) -> None:
        preview = self.build_example_preview()
        warning_text = "\n".join(preview.warnings)
        self.assertIn("partial_text", warning_text)
        self.assertIn("not_reported", warning_text)
        self.assertFalse(preview.blocked)

    def test_confirmed_false_never_writes(self) -> None:
        preview = self.build_example_preview()
        before = self.structured_snapshot()
        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            save_structured_import(self.connection, preview)
        self.assertEqual(self.structured_snapshot(), before)

    def test_payload_data_mutation_after_preview_cannot_change_confirmed_save(
        self,
    ) -> None:
        preview = self.build_example_preview()
        preview.payload.data["outcomes"].clear()
        result = save_structured_import(self.connection, preview, confirmed=True)
        self.assertEqual(result.planned_counts["outcomes"], 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM structured_entities WHERE entity_type = 'outcome'"
            ).fetchone()[0],
            2,
        )

    def test_source_reported_concept_null_context_is_preserved_as_not_extracted(
        self,
    ) -> None:
        data = self.changed_data()
        data["concepts"][0]["context"] = None
        preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data(data)
        )
        save_structured_import(self.connection, preview, confirmed=True)
        row = self.connection.execute(
            """
            SELECT f.content_role, f.availability, f.value_json
            FROM structured_entities AS e
            JOIN structured_fields AS f ON f.entity_id = e.id
            WHERE e.entity_type = 'concept' AND f.field_key = 'context'
            ORDER BY e.id
            LIMIT 1
            """
        ).fetchone()
        self.assertEqual(tuple(row), ("source_fact", "not_extracted", None))

    def test_confirmed_save_maps_all_rows_and_preserves_phase1_literature(self) -> None:
        preview = self.build_example_preview()
        literature_before = get_literature(self.connection, self.literature_id)
        result = save_structured_import(self.connection, preview, confirmed=True)
        self.assertEqual(result.planned_counts, preview.planned_counts)
        self.assertEqual(get_literature(self.connection, self.literature_id), literature_before)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_entities").fetchone()[0],
            15,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_fields").fetchone()[0],
            96,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM evidence_references").fetchone()[0],
            4,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_field_evidence").fetchone()[0]
            + self.connection.execute("SELECT COUNT(*) FROM structured_entity_evidence").fetchone()[0],
            68,
        )
        self.assertEqual(
            {row[0] for row in self.connection.execute(
                "SELECT verification FROM structured_entities UNION SELECT verification FROM structured_fields UNION SELECT verification FROM evidence_references"
            ).fetchall()},
            {"ai_unverified"},
        )

    def test_same_name_outcomes_remain_distinct_with_separate_definition_and_context(
        self,
    ) -> None:
        save_structured_import(self.connection, self.build_example_preview(), confirmed=True)
        outcome_rows = self.connection.execute(
            "SELECT id FROM structured_entities WHERE entity_type = 'outcome' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(outcome_rows), 2)
        values = []
        for row in outcome_rows:
            values.append(
                {
                    field["field_key"]: json.loads(field["value_json"])
                    if field["value_json"] is not None
                    else None
                    for field in self.connection.execute(
                        "SELECT field_key, value_json FROM structured_fields WHERE entity_id = ?",
                        (row["id"],),
                    ).fetchall()
                }
            )
        self.assertEqual([item["name"] for item in values], ["Synthetic Displacement"] * 2)
        self.assertEqual(values[0]["context_condition"], "synthetic_condition_a")
        self.assertEqual(values[1]["context_condition"], "synthetic_condition_b")
        self.assertIn("definition", values[0])
        self.assertIn("calculation_method", values[1])

    def test_results_are_parented_to_corresponding_outcomes(self) -> None:
        save_structured_import(self.connection, self.build_example_preview(), confirmed=True)
        rows = self.connection.execute(
            """
            SELECT child.id, parent.entity_type
            FROM structured_entities AS child
            JOIN structured_entities AS parent ON parent.id = child.parent_entity_id
            WHERE child.entity_type = 'result'
            ORDER BY child.id
            """
        ).fetchall()
        self.assertEqual([(row["entity_type"]) for row in rows], ["outcome", "outcome"])

    def test_evidence_locator_and_nested_metric_links_are_mapped(self) -> None:
        save_structured_import(self.connection, self.build_example_preview(), confirmed=True)
        evidence = self.connection.execute(
            "SELECT pdf_page, printed_page, table_label, figure_label FROM evidence_references ORDER BY id"
        ).fetchall()
        self.assertEqual(tuple(evidence[1]), (2, "S2", "Synthetic Table A", None))
        for field_key in ("statistics", "validation_information"):
            rows = self.connection.execute(
                """
                SELECT DISTINCT e.pdf_page
                FROM structured_fields AS f
                JOIN structured_field_evidence AS link ON link.field_id = f.id
                JOIN evidence_references AS e ON e.id = link.evidence_id
                WHERE f.field_key = ?
                ORDER BY e.pdf_page
                """,
                (field_key,),
            ).fetchall()
            self.assertIn(2, [row["pdf_page"] for row in rows])

    def test_payload_local_ids_analysis_metadata_and_bibliography_are_not_persisted(
        self,
    ) -> None:
        save_structured_import(self.connection, self.build_example_preview(), confirmed=True)
        stored_text = "\n".join(
            str(value)
            for table, columns in (
                ("structured_entities", "entity_type"),
                ("structured_fields", "field_key || COALESCE(value_json, '')"),
                ("evidence_references", "COALESCE(section, '') || COALESCE(note, '')"),
            )
            for value in (
                row[0]
                for row in self.connection.execute(f"SELECT {columns} FROM {table}")
            )
        )
        for local_id in ("outcome_1", "result_1", "evidence_1", "limitation_1", "concept_1"):
            self.assertNotIn(local_id, stored_text)
        self.assertNotIn("synthetic_contract_example.pdf", stored_text)
        literature = get_literature(self.connection, self.literature_id)
        self.assertEqual(literature.authors, "Existing synthetic author")
        self.assertEqual(literature.verification_status, "一部確認")

    def test_repeated_full_import_is_blocked(self) -> None:
        preview = self.build_example_preview()
        save_structured_import(self.connection, preview, confirmed=True)
        second_preview = build_import_preview(
            self.connection, self.literature_id, self.parse_data()
        )
        self.assertTrue(second_preview.blocked)
        before = self.structured_snapshot()
        with self.assertRaisesRegex(ValueError, "merge / overwrite"):
            save_structured_import(self.connection, preview, confirmed=True)
        self.assertEqual(self.structured_snapshot(), before)

    def test_active_transaction_blocks_save_without_committing_caller_state(self) -> None:
        preview = self.build_example_preview()
        self.connection.execute("INSERT INTO tags (name) VALUES (?)", ("pending",))
        with self.assertRaisesRegex(ValueError, "アクティブ"):
            save_structured_import(self.connection, preview, confirmed=True)
        self.assertTrue(self.connection.in_transaction)
        self.assertEqual(self.structured_snapshot(), {key: [] for key in self.structured_snapshot()})
        self.connection.rollback()

    def test_save_time_target_identifier_change_requires_new_preview(self) -> None:
        preview = self.build_example_preview()
        update_literature(
            self.connection, self.literature_id, {"doi": "10.1000/changed"}
        )
        with self.assertRaisesRegex(ValueError, "再Preview"):
            save_structured_import(self.connection, preview, confirmed=True)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_entities").fetchone()[0],
            0,
        )

    def test_mid_import_failure_rolls_back_every_structured_row(self) -> None:
        preview = self.build_example_preview()
        literature_before = get_literature(self.connection, self.literature_id)
        original = structured_repository.create_structured_field
        calls = 0

        def fail_during_fields(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise sqlite3.IntegrityError("synthetic mid-import failure")
            return original(*args, **kwargs)

        with (
            patch.object(
                structured_repository,
                "create_structured_field",
                side_effect=fail_during_fields,
            ),
            self.assertRaisesRegex(sqlite3.IntegrityError, "mid-import"),
        ):
            save_structured_import(self.connection, preview, confirmed=True)

        self.assertEqual(self.structured_snapshot(), {key: [] for key in self.structured_snapshot()})
        self.assertEqual(get_literature(self.connection, self.literature_id), literature_before)
        self.assertFalse(self.connection.in_transaction)

    def test_saved_database_integrity_checks_pass(self) -> None:
        save_structured_import(self.connection, self.build_example_preview(), confirmed=True)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
