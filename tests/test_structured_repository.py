"""Tests for validated Phase 2 structured-data repository CRUD."""

import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.database import connect_database, initialize_database
from src.models import Literature
from src.repository import add_literature
from src.structured_repository import (
    StructuredDataError,
    attach_evidence_to_entity,
    attach_evidence_to_field,
    create_evidence_reference,
    create_structured_entity,
    create_structured_field,
    delete_evidence_reference,
    delete_structured_entity,
    delete_structured_field,
    detach_evidence_from_entity,
    detach_evidence_from_field,
    get_evidence_reference,
    get_structured_entity,
    get_structured_field,
    list_evidence_for_entity,
    list_evidence_for_field,
    list_evidence_for_literature,
    list_structured_entities_for_literature,
    list_structured_fields_for_entity,
    update_evidence_reference,
    update_structured_entity,
    update_structured_field,
)


class StructuredRepositoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        database_path = Path(self.temporary_directory.name) / "structured.db"
        initialize_database(database_path)
        self.connection = connect_database(database_path)
        self.addCleanup(self.connection.close)
        self.literature_id = add_literature(
            self.connection, Literature(title="Synthetic structured target")
        )
        self.other_literature_id = add_literature(
            self.connection, Literature(title="Synthetic other target")
        )

    def test_entity_crud_and_list_for_literature(self) -> None:
        entity_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        entity = get_structured_entity(self.connection, entity_id)
        self.assertIsNotNone(entity)
        assert entity is not None
        self.assertEqual(entity.entity_type, "study")
        self.assertTrue(
            update_structured_entity(
                self.connection,
                entity_id,
                {"sort_order": 4, "verification": "user_verified"},
            )
        )
        listed = list_structured_entities_for_literature(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(listed)
        assert listed is not None
        self.assertEqual([(item.id, item.sort_order) for item in listed], [(entity_id, 4)])
        self.assertTrue(delete_structured_entity(self.connection, entity_id))
        self.assertIsNone(get_structured_entity(self.connection, entity_id))
        self.assertFalse(delete_structured_entity(self.connection, entity_id))

    def test_nonexistent_literature_and_invalid_entity_type_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "存在しません"):
            create_structured_entity(self.connection, 99999, "study")
        with self.assertRaisesRegex(ValueError, "entity_type"):
            create_structured_entity(
                self.connection, self.literature_id, "future_entity"
            )
        with self.assertRaises(ValueError):
            create_structured_entity(self.connection, self.literature_id, [])

    def test_parent_semantics_result_and_cross_literature(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        outcome_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "outcome",
            parent_entity_id=study_id,
        )
        result_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "result",
            parent_entity_id=outcome_id,
        )
        self.assertEqual(
            get_structured_entity(self.connection, result_id).parent_entity_id,
            outcome_id,
        )
        with self.assertRaisesRegex(ValueError, "Outcome parent"):
            create_structured_entity(self.connection, self.literature_id, "result")
        with self.assertRaisesRegex(ValueError, "parent"):
            create_structured_entity(
                self.connection,
                self.literature_id,
                "result",
                parent_entity_id=study_id,
            )
        other_study = create_structured_entity(
            self.connection, self.other_literature_id, "study"
        )
        with self.assertRaisesRegex(ValueError, "別Literature"):
            create_structured_entity(
                self.connection,
                self.literature_id,
                "outcome",
                parent_entity_id=other_study,
            )

    def test_self_parent_and_cycle_attempt_are_rejected(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        outcome_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "outcome",
            parent_entity_id=study_id,
        )
        result_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "result",
            parent_entity_id=outcome_id,
        )
        with self.assertRaisesRegex(ValueError, "self-parent"):
            update_structured_entity(
                self.connection, outcome_id, {"parent_entity_id": outcome_id}
            )
        with self.assertRaises(ValueError):
            update_structured_entity(
                self.connection, study_id, {"parent_entity_id": outcome_id}
            )
        self.assertIsNone(get_structured_entity(self.connection, study_id).parent_entity_id)
        self.connection.execute(
            "UPDATE structured_entities SET parent_entity_id = ? WHERE id = ?",
            (result_id, study_id),
        )
        self.connection.commit()
        with self.assertRaisesRegex(ValueError, "ancestor cycle"):
            update_structured_entity(
                self.connection, result_id, {"sort_order": 1}
            )

    def test_field_crud_decodes_python_values_and_preserves_null_interpretation(
        self,
    ) -> None:
        entity_id = create_structured_entity(
            self.connection, self.literature_id, "research_relevance"
        )
        field_id = create_structured_field(
            self.connection,
            entity_id,
            "summary",
            content_role="interpretation",
            value=None,
            verification="ai_unverified",
            note="Synthetic null interpretation",
        )
        field = get_structured_field(self.connection, field_id)
        self.assertIsNotNone(field)
        assert field is not None
        self.assertIsNone(field.value)
        self.assertEqual(
            self.connection.execute(
                "SELECT value_json FROM structured_fields WHERE id = ?", (field_id,)
            ).fetchone()[0],
            "null",
        )
        self.assertTrue(
            update_structured_field(
                self.connection,
                field_id,
                {"value": {"synthetic": [1, True, None]}, "verification": "user_verified"},
            )
        )
        self.assertEqual(
            get_structured_field(self.connection, field_id).value,
            {"synthetic": [1, True, None]},
        )
        self.assertEqual(len(list_structured_fields_for_entity(self.connection, entity_id)), 1)
        self.assertTrue(delete_structured_field(self.connection, field_id))

    def test_field_vocabulary_role_and_consistency_are_validated(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        cases = (
            {
                "field_key": "sample_szie",
                "content_role": "source_fact",
                "value": 12,
                "availability": "reported",
            },
            {
                "field_key": "sample_size",
                "content_role": "interpretation",
                "value": 12,
                "availability": None,
            },
            {
                "field_key": "sample_size",
                "content_role": "source_fact",
                "value": None,
                "availability": "reported",
            },
            {
                "field_key": "sample_size",
                "content_role": "source_fact",
                "value": 12,
                "availability": "not_reported",
            },
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                create_structured_field(self.connection, study_id, **values)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM structured_fields").fetchone()[0],
            0,
        )

    def test_concept_fields_cannot_mix_basis_roles(self) -> None:
        concept_id = create_structured_entity(
            self.connection, self.literature_id, "concept"
        )
        name_id = create_structured_field(
            self.connection,
            concept_id,
            "name",
            content_role="source_fact",
            value="Synthetic concept",
            availability="reported",
        )
        with self.assertRaisesRegex(ValueError, "同じcontent_role"):
            create_structured_field(
                self.connection,
                concept_id,
                "context",
                content_role="interpretation",
                value="Synthetic interpretation",
            )
        context_id = create_structured_field(
            self.connection,
            concept_id,
            "context",
            content_role="source_fact",
            value="Synthetic source context",
            availability="reported",
        )
        with self.assertRaisesRegex(ValueError, "同じcontent_role"):
            update_structured_field(
                self.connection,
                context_id,
                {"content_role": "interpretation", "availability": None},
            )
        self.assertEqual(
            get_structured_field(self.connection, name_id).content_role,
            "source_fact",
        )

    def test_invalid_verification_nonserializable_nan_and_infinity_are_rejected(
        self,
    ) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        for value in ({1, 2}, math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                create_structured_field(
                    self.connection,
                    study_id,
                    "sample_size",
                    content_role="source_fact",
                    value=value,
                    availability="reported",
                )
        with self.assertRaises(ValueError):
            create_structured_entity(
                self.connection,
                self.literature_id,
                "study",
                verification="spoofed",
            )

    def test_duplicate_field_is_rejected_without_changing_existing_field(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        field_id = create_structured_field(
            self.connection,
            study_id,
            "population",
            content_role="source_fact",
            value="Synthetic population",
            availability="reported",
        )
        with self.assertRaisesRegex(ValueError, "既に存在"):
            create_structured_field(
                self.connection,
                study_id,
                "population",
                content_role="source_fact",
                value="Other",
                availability="reported",
            )
        self.assertEqual(get_structured_field(self.connection, field_id).value, "Synthetic population")

    def test_invalid_stored_json_raises_structured_data_error(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        field_id = create_structured_field(
            self.connection,
            study_id,
            "population",
            content_role="source_fact",
            value="Synthetic",
            availability="reported",
        )
        self.connection.execute(
            "UPDATE structured_fields SET value_json = ? WHERE id = ?",
            ("{invalid", field_id),
        )
        self.connection.commit()
        with self.assertRaises(StructuredDataError):
            get_structured_field(self.connection, field_id)

    def test_evidence_crud_and_empty_evidence_validation(self) -> None:
        evidence_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            pdf_page=1,
            section="Synthetic Results",
            verification="ai_unverified",
        )
        self.assertEqual(get_evidence_reference(self.connection, evidence_id).pdf_page, 1)
        self.assertTrue(
            update_evidence_reference(
                self.connection,
                evidence_id,
                {"pdf_page": 2, "verification": "user_verified"},
            )
        )
        self.assertEqual(list_evidence_for_literature(self.connection, self.literature_id)[0].pdf_page, 2)
        with self.assertRaises(ValueError):
            create_evidence_reference(
                self.connection, self.literature_id, note="note only"
            )
        with self.assertRaises(ValueError):
            create_evidence_reference(
                self.connection,
                self.literature_id,
                pdf_page=1,
                section="   ",
            )
        with self.assertRaises(ValueError):
            create_evidence_reference(self.connection, self.literature_id, pdf_page=True)
        self.assertTrue(delete_evidence_reference(self.connection, evidence_id))

    def test_field_link_attach_detach_list_and_duplicate(self) -> None:
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        field_id = create_structured_field(
            self.connection,
            study_id,
            "study_design",
            content_role="source_fact",
            value="Synthetic design",
            availability="reported",
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Methods"
        )
        other_evidence_id = create_evidence_reference(
            self.connection, self.other_literature_id, section="Other Methods"
        )
        self.assertTrue(attach_evidence_to_field(self.connection, field_id, evidence_id))
        self.assertFalse(attach_evidence_to_field(self.connection, field_id, evidence_id))
        with self.assertRaisesRegex(ValueError, "別Literature"):
            attach_evidence_to_field(
                self.connection, field_id, other_evidence_id
            )
        self.assertEqual([item.id for item in list_evidence_for_field(self.connection, field_id)], [evidence_id])
        self.assertTrue(detach_evidence_from_field(self.connection, field_id, evidence_id))
        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])

    def test_entity_link_attach_detach_list_and_cross_literature_rejection(self) -> None:
        outcome_id = create_structured_entity(
            self.connection, self.literature_id, "outcome"
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, quote_text="Synthetic quote"
        )
        other_evidence_id = create_evidence_reference(
            self.connection, self.other_literature_id, section="Other"
        )
        self.assertTrue(attach_evidence_to_entity(self.connection, outcome_id, evidence_id))
        self.assertEqual([item.id for item in list_evidence_for_entity(self.connection, outcome_id)], [evidence_id])
        with self.assertRaisesRegex(ValueError, "別Literature"):
            attach_evidence_to_entity(self.connection, outcome_id, other_evidence_id)
        self.assertTrue(detach_evidence_from_entity(self.connection, outcome_id, evidence_id))

    def test_entity_delete_cascades_child_fields_and_links_but_keeps_evidence(self) -> None:
        outcome_id = create_structured_entity(
            self.connection, self.literature_id, "outcome"
        )
        result_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "result",
            parent_entity_id=outcome_id,
        )
        field_id = create_structured_field(
            self.connection,
            result_id,
            "result",
            content_role="source_fact",
            value=1,
            availability="reported",
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=1
        )
        attach_evidence_to_field(self.connection, field_id, evidence_id)
        attach_evidence_to_entity(self.connection, result_id, evidence_id)
        self.assertTrue(delete_structured_entity(self.connection, outcome_id))
        self.assertIsNone(get_structured_entity(self.connection, result_id))
        self.assertIsNone(get_structured_field(self.connection, field_id))
        self.assertIsNotNone(get_evidence_reference(self.connection, evidence_id))
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(), []
        )

    def test_reads_for_unknown_owners_return_none(self) -> None:
        self.assertIsNone(list_structured_entities_for_literature(self.connection, 99999))
        self.assertIsNone(list_evidence_for_literature(self.connection, 99999))
        self.assertIsNone(list_structured_fields_for_entity(self.connection, 99999))
        self.assertIsNone(list_evidence_for_field(self.connection, 99999))
        self.assertIsNone(list_evidence_for_entity(self.connection, 99999))

    def test_standalone_crud_commits_and_explicit_transaction_is_not_committed(
        self,
    ) -> None:
        entity_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        self.assertFalse(self.connection.in_transaction)
        self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)", ("pending-marker",)
        )
        create_structured_field(
            self.connection,
            entity_id,
            "sample_size",
            content_role="source_fact",
            value=12,
            availability="reported",
        )
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM structured_fields WHERE entity_id = ?", (entity_id,)
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
