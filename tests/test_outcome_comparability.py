"""Tests for the Phase 2-8 Outcome comparability service."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.comparison_matrix import METHOD_GROUPS
from src.database import connect_database, initialize_database
from src.models import Literature
from src.outcome_comparability import (
    COMPARABILITY_STATUSES,
    DEFAULT_COMPARABILITY_STATUS,
    OUTCOME_CONTEXT_FIELD_ORDER,
    OUTCOME_IDENTITY_FIELD_ORDER,
    build_outcome_comparability_profile,
    list_outcome_choices,
    load_pairwise_literature,
    select_outcome_choice,
    validate_manual_comparability_status,
)
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


class OutcomeComparabilityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "comparability.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)
        self.literature_a = add_literature(
            self.connection, Literature(title="Synthetic Literature A")
        )
        self.literature_b = add_literature(
            self.connection, Literature(title="Synthetic Literature B")
        )

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

    def create_outcome(
        self,
        literature_id: int,
        *,
        name: str = "Synthetic Outcome",
        definition: object = "Synthetic definition",
        unit: str = "%",
        sort_order: int = 0,
        entity_verification: str = "ai_unverified",
        context_values: dict[str, tuple[object, str]] | None = None,
        include_calculation: bool = True,
    ) -> int:
        outcome_id = create_structured_entity(
            self.connection,
            literature_id,
            "outcome",
            sort_order=sort_order,
            verification=entity_verification,
        )
        values = [
            ("name", name, "reported"),
            (
                "definition",
                definition,
                "reported" if definition is not None else "not_extracted",
            ),
            ("unit", unit, "reported"),
        ]
        if include_calculation:
            values.insert(
                2, ("calculation_method", "Stored calculation", "reported")
            )
        for key, value, availability in values:
            create_structured_field(
                self.connection,
                outcome_id,
                key,
                content_role="source_fact",
                value=value,
                availability=availability,
            )
        for key, (value, availability) in (context_values or {}).items():
            create_structured_field(
                self.connection,
                outcome_id,
                key,
                content_role="source_fact",
                value=value,
                availability=availability,
            )
        return outcome_id

    def test_pairwise_literature_requires_two_different_existing_records(self) -> None:
        pair = load_pairwise_literature(
            self.connection, self.literature_b, self.literature_a
        )
        self.assertEqual([item.id for item in pair], [self.literature_b, self.literature_a])
        with self.assertRaisesRegex(ValueError, "重複"):
            load_pairwise_literature(
                self.connection, self.literature_a, self.literature_a
            )
        with self.assertRaisesRegex(ValueError, "存在しません"):
            load_pairwise_literature(self.connection, self.literature_a, 999_999)

    def test_outcome_choices_handle_empty_one_multiple_same_name_and_range(self) -> None:
        empty = list_outcome_choices(self.connection, self.literature_a)
        self.assertEqual(empty.outcomes, ())

        later = self.create_outcome(
            self.literature_a,
            name="Strain",
            definition="Definition B",
            sort_order=5,
            context_values={"context_task": ("Task B", "reported")},
        )
        one = list_outcome_choices(self.connection, self.literature_a)
        self.assertEqual(len(one.outcomes), 1)
        self.assertEqual(one.outcomes[0].entity_id, later)
        earlier = self.create_outcome(
            self.literature_a,
            name="Strain",
            definition="Definition A",
            sort_order=1,
            context_values={"context_task": ("Task A", "reported")},
        )
        choices = list_outcome_choices(self.connection, self.literature_a)

        self.assertEqual([item.entity_id for item in choices.outcomes], [earlier, later])
        self.assertEqual([item.selection_number for item in choices.outcomes], [1, 2])
        self.assertEqual([item.name_text for item in choices.outcomes], ["Strain", "Strain"])
        self.assertEqual(select_outcome_choice(choices.outcomes, 2).entity_id, later)
        self.assertIn("context_task=Task A", choices.outcomes[0].context_text)
        for selection in (0, 3, True, "1"):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                select_outcome_choice(choices.outcomes, selection)

    def test_profile_separates_identity_and_preserves_missing_and_availability(self) -> None:
        outcome_a = self.create_outcome(
            self.literature_a,
            name="Strain",
            definition=None,
            unit="%",
            include_calculation=False,
        )
        outcome_b = self.create_outcome(
            self.literature_b,
            name="Strain",
            definition="Stored definition",
            unit="%",
        )
        profile = build_outcome_comparability_profile(
            self.connection, self.literature_a, 1, self.literature_b, 1
        )
        identity = {field.field_key: field for field in profile.identity}

        self.assertEqual(tuple(identity), OUTCOME_IDENTITY_FIELD_ORDER)
        self.assertEqual(identity["name"].literature_a.display_text, "Strain")
        self.assertEqual(identity["name"].literature_b.display_text, "Strain")
        self.assertEqual(
            identity["definition"].literature_a.display_text, "not_extracted"
        )
        self.assertIsNone(identity["calculation_method"].literature_a)
        self.assertEqual(identity["unit"].literature_a.display_text, "%")
        self.assertEqual(profile.default_status, DEFAULT_COMPARABILITY_STATUS)

    def test_profile_preserves_all_eight_context_states(self) -> None:
        states_a = {
            "context_condition": ("Stored condition", "reported"),
            "context_group": (None, "not_reported"),
            "context_body_position": (None, "not_extracted"),
            "context_task": (None, "unclear"),
            "context_load": (None, "not_applicable"),
        }
        self.create_outcome(self.literature_a, context_values=states_a)
        self.create_outcome(
            self.literature_b,
            context_values={
                "context_region": ("Stored region", "reported"),
                "context_layer": ("Stored layer", "reported"),
                "context_time_point": ("Stored time", "reported"),
            },
        )

        profile = build_outcome_comparability_profile(
            self.connection, self.literature_a, 1, self.literature_b, 1
        )
        context = {field.field_key: field for field in profile.context}

        self.assertEqual(tuple(context), OUTCOME_CONTEXT_FIELD_ORDER)
        self.assertEqual(
            context["context_condition"].literature_a.display_text,
            "Stored condition",
        )
        for key, availability in (
            ("context_group", "not_reported"),
            ("context_body_position", "not_extracted"),
            ("context_task", "unclear"),
            ("context_load", "not_applicable"),
        ):
            self.assertEqual(context[key].literature_a.display_text, availability)
        self.assertIsNone(context["context_region"].literature_a)
        self.assertEqual(
            context["context_time_point"].literature_b.display_text,
            "Stored time",
        )

    def test_methods_keep_all_groups_fields_and_multiple_entities(self) -> None:
        self.create_outcome(self.literature_a)
        self.create_outcome(self.literature_b)
        later = create_structured_entity(
            self.connection,
            self.literature_a,
            "method_body_condition",
            sort_order=4,
        )
        earlier = create_structured_entity(
            self.connection,
            self.literature_a,
            "method_body_condition",
            sort_order=1,
        )
        for entity_id, load in ((later, "90 N"), (earlier, "9.2 kg")):
            create_structured_field(
                self.connection,
                entity_id,
                "load",
                content_role="source_fact",
                value=load,
                availability="reported",
            )
        analysis = create_structured_entity(
            self.connection, self.literature_b, "method_analysis"
        )
        create_structured_field(
            self.connection,
            analysis,
            "tracking_algorithm",
            content_role="source_fact",
            value="Stored algorithm",
            availability="reported",
        )

        profile = build_outcome_comparability_profile(
            self.connection, self.literature_a, 1, self.literature_b, 1
        )

        self.assertEqual(
            tuple(group.name for group in profile.methods_context),
            tuple(group[0] for group in METHOD_GROUPS),
        )
        for actual, expected in zip(profile.methods_context, METHOD_GROUPS):
            self.assertEqual(tuple(field.field_key for field in actual.fields), expected[2])
        load = next(
            field
            for field in profile.methods_context[1].fields
            if field.field_key == "load"
        )
        self.assertEqual(
            [value.entity_id for value in load.literature_a_values],
            [earlier, later],
        )
        self.assertEqual(
            [value.display_text for value in load.literature_a_values],
            ["9.2 kg", "90 N"],
        )
        tracking = next(
            field
            for field in profile.methods_context[3].fields
            if field.field_key == "tracking_algorithm"
        )
        self.assertEqual(
            tracking.literature_b_values[0].display_text, "Stored algorithm"
        )

    def test_entity_field_and_evidence_verification_remain_separate(self) -> None:
        outcome_a = self.create_outcome(
            self.literature_a, entity_verification="ai_unverified"
        )
        self.create_outcome(
            self.literature_b, entity_verification="user_verified"
        )
        validation_field = create_structured_field(
            self.connection,
            outcome_a,
            "validation_information",
            content_role="source_fact",
            value=[{"metric": "stored"}],
            availability="reported",
            verification="ai_unverified",
        )
        ai_evidence = create_evidence_reference(
            self.connection,
            self.literature_a,
            section="Synthetic section",
            verification="ai_unverified",
        )
        verified_evidence = create_evidence_reference(
            self.connection,
            self.literature_a,
            pdf_page=1,
            verification="user_verified",
        )
        attach_evidence_to_entity(self.connection, outcome_a, verified_evidence)
        attach_evidence_to_field(self.connection, validation_field, ai_evidence)
        attach_evidence_to_field(self.connection, validation_field, verified_evidence)
        validation_method = create_structured_entity(
            self.connection,
            self.literature_a,
            "method_validation_statistics",
        )
        create_structured_field(
            self.connection,
            validation_method,
            "icc",
            content_role="source_fact",
            value="ICC(2,1)",
            availability="reported",
            verification="user_verified",
        )

        profile = build_outcome_comparability_profile(
            self.connection, self.literature_a, 1, self.literature_b, 1
        )
        validation = profile.validation_information.literature_a

        self.assertEqual(profile.outcome_a.entity_verification, "ai_unverified")
        self.assertEqual(profile.outcome_a.evidence_count, 1)
        self.assertEqual(profile.outcome_a.user_verified_evidence_count, 1)
        self.assertEqual(validation.verification, "ai_unverified")
        self.assertEqual(validation.evidence_count, 2)
        self.assertEqual(validation.user_verified_evidence_count, 1)
        icc = next(
            field
            for field in profile.methods_context[-1].fields
            if field.field_key == "icc"
        )
        self.assertEqual(icc.literature_a_values[0].verification, "user_verified")
        self.assertEqual(icc.literature_a_values[0].evidence_count, 0)

    def test_default_needs_review_never_changes_from_names_units_or_missing(self) -> None:
        cases = (
            ("Strain", "Strain", "%", "%", "Definition", "Definition"),
            (
                "Inferior translation",
                "Inferior displacement",
                "mm",
                "mm",
                "Definition A",
                "Definition B",
            ),
            ("A", "B", "%", "%", None, "Definition B"),
        )
        for number, (name_a, name_b, unit_a, unit_b, definition_a, definition_b) in enumerate(cases):
            with self.subTest(number=number):
                database_path = Path(self.temporary_directory.name) / f"case-{number}.db"
                initialize_database(database_path)
                connection = connect_database(database_path)
                try:
                    literature_a = add_literature(connection, Literature(title="Case A"))
                    literature_b = add_literature(connection, Literature(title="Case B"))
                    original_connection = self.connection
                    self.connection = connection
                    try:
                        self.create_outcome(
                            literature_a,
                            name=name_a,
                            definition=definition_a,
                            unit=unit_a,
                        )
                        self.create_outcome(
                            literature_b,
                            name=name_b,
                            definition=definition_b,
                            unit=unit_b,
                        )
                    finally:
                        self.connection = original_connection
                    profile = build_outcome_comparability_profile(
                        connection, literature_a, 1, literature_b, 1
                    )
                    self.assertEqual(profile.default_status, "needs review")
                finally:
                    connection.close()

    def test_manual_status_accepts_only_closed_vocabulary_without_persistence(self) -> None:
        before = self.snapshot(self.connection)
        for status in COMPARABILITY_STATUSES:
            with self.subTest(status=status):
                self.assertEqual(
                    validate_manual_comparability_status(status).status, status
                )
        for status in ("comparable", "", None, " directly comparable"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                validate_manual_comparability_status(status)
        self.assertEqual(self.snapshot(self.connection), before)

    def test_profile_is_read_only_and_database_integrity_remains_clean(self) -> None:
        self.create_outcome(self.literature_a)
        self.create_outcome(self.literature_b)
        before = self.snapshot(self.connection)
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)

        try:
            build_outcome_comparability_profile(
                self.connection, self.literature_a, 1, self.literature_b, 1
            )
            validate_manual_comparability_status("directly comparable")
        finally:
            self.connection.set_trace_callback(None)

        self.assertEqual(self.snapshot(self.connection), before)
        write_statements = tuple(
            statement
            for statement in statements
            if statement.lstrip().upper().startswith(
                (
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "REPLACE",
                    "CREATE",
                    "ALTER",
                    "DROP",
                    "BEGIN",
                    "COMMIT",
                    "ROLLBACK",
                )
            )
        )
        self.assertEqual(write_statements, ())
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
            literature_a = add_literature(connection, Literature(title="Active A"))
            literature_b = add_literature(connection, Literature(title="Active B"))
            original_connection = self.connection
            self.connection = connection
            try:
                self.create_outcome(literature_a)
                self.create_outcome(literature_b)
            finally:
                self.connection = original_connection
            connection.commit_calls = 0
            connection.rollback_calls = 0
            connection.close_calls = 0
            marker = connection.execute(
                "INSERT INTO tags (name) VALUES (?)", ("pending-comparability",)
            )

            profile = build_outcome_comparability_profile(
                connection, literature_a, 1, literature_b, 1
            )

            self.assertEqual(profile.default_status, "needs review")
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.commit_calls, 0)
            self.assertEqual(connection.rollback_calls, 0)
            self.assertEqual(connection.close_calls, 0)
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM tags WHERE id = ?", (marker.lastrowid,)
                ).fetchone()[0],
                "pending-comparability",
            )
        finally:
            if connection.in_transaction:
                sqlite3.Connection.rollback(connection)
            sqlite3.Connection.close(connection)


if __name__ == "__main__":
    unittest.main()
