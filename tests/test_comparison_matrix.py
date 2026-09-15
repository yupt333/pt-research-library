"""Tests for the Phase 2-7 read-only comparison matrix service."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.comparison_matrix import (
    METHOD_GROUPS,
    OUTCOME_FIELD_ORDER,
    RESULT_FIELD_ORDER,
    STUDY_FIELD_ORDER,
    build_comparison_matrix,
    format_json_value,
    load_comparison_literature,
    parse_literature_id_input,
    select_search_result_ids,
)
from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import add_literature
from src.structured_repository import (
    attach_evidence_to_entity,
    attach_evidence_to_field,
    create_evidence_reference,
    create_structured_entity,
    create_structured_field,
)


class TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs) -> None:
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


class ComparisonMatrixTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "comparison.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)
        self.literature_ids = tuple(
            add_literature(
                self.connection,
                Literature(
                    title=f"Synthetic comparison literature {number}",
                    publication_year=2020 + number,
                ),
            )
            for number in range(1, 6)
        )

    def create_fact(
        self,
        literature_id: int,
        entity_type: str,
        field_key: str,
        value: object,
        *,
        availability: str = "reported",
        entity_sort_order: int = 0,
        field_verification: str = "ai_unverified",
        entity_verification: str = "ai_unverified",
        parent_entity_id: int | None = None,
    ) -> tuple[int, int]:
        entity_id = create_structured_entity(
            self.connection,
            literature_id,
            entity_type,
            parent_entity_id=parent_entity_id,
            sort_order=entity_sort_order,
            verification=entity_verification,
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            field_key,
            content_role="source_fact",
            value=value,
            availability=availability,
            verification=field_verification,
        )
        return entity_id, field_id

    @staticmethod
    def snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
        order_by = {
            "literature": "id",
            "tags": "id",
            "literature_tags": "literature_id, tag_id",
            "usage_history": "id",
            "schema_migrations": "version",
            "structured_entities": "id",
            "structured_fields": "id",
            "evidence_references": "id",
            "structured_field_evidence": "field_id, evidence_id",
            "structured_entity_evidence": "entity_id, evidence_id",
        }
        return {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ordering}"
                ).fetchall()
            ]
            for table, ordering in order_by.items()
        }

    def test_manual_selection_accepts_two_and_five_and_preserves_order(self) -> None:
        self.assertEqual(parse_literature_id_input(" 5, 2 "), (5, 2))
        self.assertEqual(
            parse_literature_id_input("5,1,4,2,3"),
            (5, 1, 4, 2, 3),
        )
        columns = load_comparison_literature(
            self.connection,
            (self.literature_ids[4], self.literature_ids[1]),
        )
        self.assertEqual(
            [column.id for column in columns],
            [self.literature_ids[4], self.literature_ids[1]],
        )
        matrix = build_comparison_matrix(
            self.connection, tuple(reversed(self.literature_ids))
        )
        self.assertEqual(
            [column.id for column in matrix.literature_columns],
            list(reversed(self.literature_ids)),
        )

    def test_manual_selection_rejects_one_duplicate_unknown_and_malformed(self) -> None:
        invalid = (
            "",
            "1",
            "1,1",
            "0,2",
            "-1,2",
            "１,2",
            "1,,2",
            ",1,2",
            "1,2,",
            "1.0,2",
        )
        for raw_value in invalid:
            with self.subTest(raw_value=raw_value), self.assertRaises(ValueError):
                parse_literature_id_input(raw_value)
        with self.assertRaisesRegex(ValueError, "存在しません"):
            load_comparison_literature(
                self.connection,
                (self.literature_ids[0], 999_999),
            )

    def test_last_search_selection_all_subset_order_and_errors(self) -> None:
        search_ids = (
            self.literature_ids[2],
            self.literature_ids[0],
            self.literature_ids[4],
        )
        self.assertEqual(select_search_result_ids(search_ids, "all"), search_ids)
        self.assertEqual(
            select_search_result_ids(search_ids, "3, 1"),
            (self.literature_ids[4], self.literature_ids[2]),
        )
        for raw_value in ("1", "1,1", "0,2", "4,1", "１,2", "1,,2"):
            with self.subTest(raw_value=raw_value), self.assertRaises(ValueError):
                select_search_result_ids(search_ids, raw_value)
        with self.assertRaisesRegex(ValueError, "直前の検索結果"):
            select_search_result_ids((), "all")

    def test_study_and_methods_use_fixed_row_and_literature_order(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        first_study = create_structured_entity(self.connection, first_id, "study")
        create_structured_field(
            self.connection,
            first_study,
            "sample_size",
            content_role="source_fact",
            value=12,
            availability="reported",
        )
        create_structured_field(
            self.connection,
            first_study,
            "population",
            content_role="source_fact",
            value="Synthetic population",
            availability="reported",
        )
        second_study = create_structured_entity(self.connection, second_id, "study")
        create_structured_field(
            self.connection,
            second_study,
            "study_design",
            content_role="source_fact",
            value="Synthetic design",
            availability="reported",
        )
        analysis = create_structured_entity(
            self.connection, first_id, "method_analysis"
        )
        create_structured_field(
            self.connection,
            analysis,
            "quality_control",
            content_role="source_fact",
            value=True,
            availability="reported",
        )
        create_structured_field(
            self.connection,
            analysis,
            "analysis_method",
            content_role="source_fact",
            value="Stored analysis",
            availability="reported",
        )

        matrix = build_comparison_matrix(self.connection, (second_id, first_id))

        self.assertEqual(
            tuple(row.field_key for row in matrix.study_rows),
            tuple(
                key
                for key in STUDY_FIELD_ORDER
                if key in {"study_design", "population", "sample_size"}
            ),
        )
        self.assertTrue(
            all(
                [cell.literature.id for cell in row.cells]
                == [second_id, first_id]
                for row in matrix.study_rows
            )
        )
        self.assertEqual(
            tuple(group.name for group in matrix.method_groups),
            tuple(group[0] for group in METHOD_GROUPS),
        )
        analysis_group = matrix.method_groups[3]
        self.assertEqual(
            [row.field_key for row in analysis_group.rows],
            ["analysis_method", "quality_control"],
        )

    def test_missing_availability_and_empty_row_semantics_are_distinct(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        first_study = create_structured_entity(self.connection, first_id, "study")
        second_study = create_structured_entity(self.connection, second_id, "study")
        for entity_id in (first_study, second_study):
            create_structured_field(
                self.connection,
                entity_id,
                "study_design",
                content_role="source_fact",
                value=None,
                availability="not_reported",
            )
        create_structured_field(
            self.connection,
            first_study,
            "population",
            content_role="source_fact",
            value="Synthetic",
            availability="reported",
        )

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        rows = {row.field_key: row for row in matrix.study_rows}

        self.assertNotIn("sample_size", rows)
        self.assertEqual(
            [cell.status for cell in rows["population"].cells],
            ["registered", "unregistered"],
        )
        self.assertEqual(
            [cell.values[0].status for cell in rows["study_design"].cells],
            ["not_reported", "not_reported"],
        )
        self.assertEqual(
            [cell.values[0].display_text for cell in rows["study_design"].cells],
            ["not_reported", "not_reported"],
        )

    def test_all_availability_states_and_json_values_are_preserved(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        entity = create_structured_entity(self.connection, first_id, "study")
        reported_values = {
            "study_design": "Synthetic string",
            "sample_size": 0,
            "demographics": False,
            "condition_diagnosis": {"z": 1, "a": None},
            "inclusion_criteria": ["A", 2, True, None],
        }
        for key, value in reported_values.items():
            create_structured_field(
                self.connection,
                entity,
                key,
                content_role="source_fact",
                value=value,
                availability="reported",
            )
        other = create_structured_entity(self.connection, second_id, "study")
        for key, availability in zip(
            (
                "study_design",
                "population",
                "sample_size",
                "demographics",
            ),
            ("not_reported", "not_extracted", "unclear", "not_applicable"),
        ):
            create_structured_field(
                self.connection,
                other,
                key,
                content_role="source_fact",
                value=None,
                availability=availability,
            )

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        rows = {row.field_key: row for row in matrix.study_rows}

        self.assertEqual(rows["study_design"].cells[0].values[0].display_text, "Synthetic string")
        self.assertEqual(rows["sample_size"].cells[0].values[0].display_text, "0")
        self.assertEqual(rows["demographics"].cells[0].values[0].display_text, "false")
        self.assertEqual(
            rows["condition_diagnosis"].cells[0].values[0].display_text,
            '{"a": null, "z": 1}',
        )
        self.assertEqual(
            rows["inclusion_criteria"].cells[0].values[0].display_text,
            '["A", 2, true, null]',
        )
        self.assertEqual(format_json_value(None, content_role="interpretation"), "null（解釈値）")
        self.assertEqual(rows["population"].cells[1].values[0].status, "not_extracted")
        self.assertEqual(rows["sample_size"].cells[1].values[0].status, "unclear")
        self.assertEqual(rows["demographics"].cells[1].values[0].status, "not_applicable")

    def test_multiple_entities_and_values_are_not_discarded(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        later_entity, _ = self.create_fact(
            first_id,
            "method_body_condition",
            "load",
            "90 N",
            entity_sort_order=5,
        )
        earlier_entity, _ = self.create_fact(
            first_id,
            "method_body_condition",
            "load",
            "9.2 kg",
            entity_sort_order=1,
        )
        self.create_fact(
            second_id,
            "method_body_condition",
            "load",
            [0, 30, 60, 90],
        )

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        load_row = next(
            row
            for row in matrix.method_groups[1].rows
            if row.field_key == "load"
        )

        self.assertEqual(
            [value.entity_id for value in load_row.cells[0].values],
            [earlier_entity, later_entity],
        )
        self.assertEqual(
            [value.display_text for value in load_row.cells[0].values],
            ["9.2 kg", "90 N"],
        )
        self.assertEqual(load_row.cells[1].values[0].display_text, "[0, 30, 60, 90]")

    def test_field_and_evidence_verification_remain_separate(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        _, field_id = self.create_fact(
            first_id,
            "method_body_condition",
            "load",
            "90 N",
            field_verification="ai_unverified",
        )
        ai_evidence = create_evidence_reference(
            self.connection,
            first_id,
            section="Synthetic Methods",
            verification="ai_unverified",
        )
        verified_evidence = create_evidence_reference(
            self.connection,
            first_id,
            pdf_page=4,
            verification="user_verified",
        )
        attach_evidence_to_field(self.connection, field_id, ai_evidence)
        attach_evidence_to_field(self.connection, field_id, verified_evidence)
        self.create_fact(
            second_id,
            "method_body_condition",
            "load",
            "Stored load without Evidence",
            field_verification="user_verified",
        )

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        load_row = next(row for row in matrix.method_groups[1].rows if row.field_key == "load")
        first_value = load_row.cells[0].values[0]
        second_value = load_row.cells[1].values[0]

        self.assertEqual(first_value.verification, "ai_unverified")
        self.assertEqual(first_value.evidence_count, 2)
        self.assertEqual(first_value.user_verified_evidence_count, 1)
        self.assertEqual(second_value.verification, "user_verified")
        self.assertEqual(second_value.evidence_count, 0)
        self.assertEqual(second_value.user_verified_evidence_count, 0)

    def test_outcomes_results_context_and_same_names_remain_separate(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        first_outcome = create_structured_entity(
            self.connection, first_id, "outcome", sort_order=2
        )
        second_outcome = create_structured_entity(
            self.connection, first_id, "outcome", sort_order=1
        )
        other_outcome = create_structured_entity(
            self.connection, second_id, "outcome", sort_order=0
        )
        for outcome_id, context, definition, calculation, unit in (
            (first_outcome, "Condition B", "Definition B", "Calculation B", "%"),
            (second_outcome, "Condition A", "Definition A", "Calculation A", "%"),
            (other_outcome, "Condition C", "Definition C", "Calculation C", "mm"),
        ):
            for key, value in (
                ("name", "Synthetic same name"),
                ("definition", definition),
                ("calculation_method", calculation),
                ("unit", unit),
                ("context_condition", context),
                ("validation_information", [{"metric": "stored"}]),
            ):
                create_structured_field(
                    self.connection,
                    outcome_id,
                    key,
                    content_role="source_fact",
                    value=value,
                    availability="reported",
                )
        result_id = create_structured_entity(
            self.connection,
            first_id,
            "result",
            parent_entity_id=second_outcome,
            sort_order=4,
            verification="user_verified",
        )
        for key, value in (
            ("condition_or_comparison", "Stored comparison"),
            ("result", 3.2),
            ("statistics", {"icc": 0.9}),
        ):
            create_structured_field(
                self.connection,
                result_id,
                key,
                content_role="source_fact",
                value=value,
                availability="reported",
            )
        entity_evidence = create_evidence_reference(
            self.connection,
            first_id,
            section="Synthetic Results",
            verification="user_verified",
        )
        attach_evidence_to_entity(self.connection, second_outcome, entity_evidence)

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        first_profiles = matrix.outcomes_by_literature[0].outcomes
        other_profiles = matrix.outcomes_by_literature[1].outcomes

        self.assertEqual(
            [profile.entity_id for profile in first_profiles],
            [second_outcome, first_outcome],
        )
        self.assertEqual(len(first_profiles), 2)
        self.assertEqual(len(other_profiles), 1)
        self.assertEqual(
            tuple(field.field_key for field in first_profiles[0].fields),
            OUTCOME_FIELD_ORDER,
        )
        first_values = {
            field.field_key: field.value.display_text if field.value else None
            for field in first_profiles[0].fields
        }
        self.assertEqual(first_values["name"], "Synthetic same name")
        self.assertEqual(first_values["definition"], "Definition A")
        self.assertEqual(first_values["calculation_method"], "Calculation A")
        self.assertEqual(first_values["unit"], "%")
        self.assertEqual(first_values["context_condition"], "Condition A")
        self.assertEqual(first_profiles[0].evidence_count, 1)
        self.assertEqual(first_profiles[0].user_verified_evidence_count, 1)
        self.assertEqual(len(first_profiles[0].results), 1)
        result = first_profiles[0].results[0]
        self.assertEqual(tuple(field.field_key for field in result.fields), RESULT_FIELD_ORDER)
        result_values = {
            field.field_key: field.value.display_text if field.value else None
            for field in result.fields
        }
        self.assertEqual(result_values["condition_or_comparison"], "Stored comparison")
        self.assertEqual(result_values["result"], "3.2")
        self.assertEqual(result_values["statistics"], '{"icc": 0.9}')
        self.assertEqual(result.entity_verification, "user_verified")

    def test_comparison_is_read_only_and_integrity_checks_remain_clean(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        self.create_fact(first_id, "study", "population", "Synthetic A")
        self.create_fact(second_id, "study", "population", "Synthetic B")
        before = self.snapshot(self.connection)

        build_comparison_matrix(self.connection, (second_id, first_id))

        self.assertEqual(self.snapshot(self.connection), before)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        self.assertFalse(self.connection.in_transaction)

    def test_active_transaction_is_not_committed_rolled_back_or_closed(self) -> None:
        database_path = Path(self.temporary_directory.name) / "tracking.db"
        initialize_database(database_path)
        connection = sqlite3.connect(database_path, factory=TrackingConnection)
        connection.row_factory = sqlite3.Row
        sqlite3.Connection.execute(connection, "PRAGMA foreign_keys = ON")
        try:
            first_id = add_literature(connection, Literature(title="Synthetic active A"))
            second_id = add_literature(connection, Literature(title="Synthetic active B"))
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)", ("pending-comparison",)
            )
            self.assertTrue(connection.in_transaction)

            matrix = build_comparison_matrix(connection, (second_id, first_id))

            self.assertEqual([item.id for item in matrix.literature_columns], [second_id, first_id])
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                connection.execute("SELECT name FROM tags WHERE id = ?", (marker.lastrowid,)).fetchone()[0],
                "pending-comparison",
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)

    def test_no_inference_conversion_alignment_or_judgment_is_generated(self) -> None:
        first_id, second_id = self.literature_ids[:2]
        self.create_fact(first_id, "method_body_condition", "load", "90 N")
        self.create_fact(second_id, "method_body_condition", "load", "9.2 kg")

        matrix = build_comparison_matrix(self.connection, (first_id, second_id))
        load_row = next(row for row in matrix.method_groups[1].rows if row.field_key == "load")
        self.assertEqual(
            [cell.values[0].display_text for cell in load_row.cells],
            ["90 N", "9.2 kg"],
        )
        rendered = repr(matrix).lower()
        for forbidden in (
            "directly comparable",
            "partially comparable",
            "not directly comparable",
            "needs review",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
