"""Tests for Phase 2-5 Evidence review safety and read models."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.database import connect_database, initialize_database
from src.evidence_review import (
    attach_evidence,
    build_evidence_edit_preview,
    change_evidence_verification,
    create_review_evidence,
    delete_review_evidence,
    detach_evidence,
    EvidenceEditPreview,
    evidence_locator_summary,
    get_evidence_detail,
    list_literature_evidence,
    list_structured_items_with_evidence,
    save_evidence_edit,
)
from src.models import Literature
from src.repository import add_literature, get_literature
from src.structured_repository import (
    create_evidence_reference,
    create_structured_entity,
    create_structured_field,
    get_evidence_reference,
    get_structured_entity,
    get_structured_field,
    list_evidence_for_entity,
    list_evidence_for_field,
    update_evidence_reference,
)


class EvidenceReviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "review.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.addCleanup(self.connection.close)
        self.literature_id = add_literature(
            self.connection,
            Literature(
                title="Synthetic Evidence review target",
                verification_status="一部確認",
                ai_summary_status="未確認",
            ),
        )
        self.other_literature_id = add_literature(
            self.connection, Literature(title="Synthetic other target")
        )

    def create_outcome(
        self, name: str, context: str
    ) -> tuple[int, int, int]:
        entity_id = create_structured_entity(
            self.connection,
            self.literature_id,
            "outcome",
            verification="user_verified",
        )
        name_id = create_structured_field(
            self.connection,
            entity_id,
            "name",
            content_role="source_fact",
            value=name,
            availability="reported",
            verification="user_verified",
        )
        context_id = create_structured_field(
            self.connection,
            entity_id,
            "context_condition",
            content_role="source_fact",
            value=context,
            availability="reported",
            verification="user_verified",
        )
        return entity_id, name_id, context_id

    def test_list_unknown_zero_multiple_and_all_locator_fields(self) -> None:
        self.assertIsNone(list_literature_evidence(self.connection, 999999))
        self.assertEqual(
            list_literature_evidence(self.connection, self.literature_id), []
        )
        first_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            pdf_page=3,
            printed_page="S12",
            section="Synthetic Results",
            subsection="Synthetic subsection",
            table_label="Table S1",
            figure_label="Figure 2",
            quote_text="Synthetic exact source text",
            note="Synthetic review note",
            verification="ai_unverified",
        )
        second_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            pdf_page=4,
            verification="user_verified",
        )

        listed = list_literature_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(listed)
        assert listed is not None
        self.assertEqual([item.id for item in listed], [first_id, second_id])
        self.assertEqual(listed[0].printed_page, "S12")
        self.assertEqual(listed[0].section, "Synthetic Results")
        self.assertEqual(listed[0].subsection, "Synthetic subsection")
        self.assertEqual(listed[0].table_label, "Table S1")
        self.assertEqual(listed[0].figure_label, "Figure 2")
        self.assertEqual(listed[0].quote_text, "Synthetic exact source text")
        self.assertEqual(listed[0].note, "Synthetic review note")
        self.assertEqual(
            [item.verification for item in listed],
            ["ai_unverified", "user_verified"],
        )
        summary = evidence_locator_summary(listed[0])
        for expected in (
            "PDF page 3",
            "printed page S12",
            "section Synthetic Results",
            "subsection Synthetic subsection",
            "table Table S1",
            "figure Figure 2",
            "original textあり",
        ):
            self.assertIn(expected, summary)

    def test_backlinks_are_human_readable_keep_same_name_outcomes_separate(self) -> None:
        first_entity, first_name, _ = self.create_outcome(
            "Synthetic displacement", "Condition A"
        )
        second_entity, second_name, _ = self.create_outcome(
            "Synthetic displacement", "Condition B"
        )
        method_entity = create_structured_entity(
            self.connection, self.literature_id, "method_body_condition"
        )
        load_field = create_structured_field(
            self.connection,
            method_entity,
            "load",
            content_role="source_fact",
            value={"inferior_force_N": 90},
            availability="reported",
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Methods"
        )
        second_evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Results"
        )
        attach_evidence(
            self.connection,
            "entity",
            first_entity,
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            first_name,
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            load_field,
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            load_field,
            second_evidence_id,
            confirmed=True,
        )

        items = list_structured_items_with_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(items)
        assert items is not None
        outcomes = [
            item
            for item in items
            if item.kind == "entity" and item.category_label == "Outcome"
        ]
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(
            [item.owner_id for item in outcomes],
            [first_entity, second_entity],
        )
        self.assertEqual(
            [item.item_label for item in outcomes],
            [
                "Synthetic displacement — Condition A",
                "Synthetic displacement — Condition B",
            ],
        )
        self.assertEqual(
            next(item for item in items if item.owner_id == second_name).evidence,
            (),
        )
        load_item = next(item for item in items if item.owner_id == load_field)
        self.assertEqual(load_item.category_label, "Methods / Body Condition")
        self.assertEqual(load_item.item_label, "load")
        self.assertEqual(load_item.value_text, '{"inferior_force_N": 90}')
        self.assertEqual(
            [item.id for item in load_item.evidence],
            [evidence_id, second_evidence_id],
        )

        detail = get_evidence_detail(self.connection, evidence_id)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(
            [item.owner_id for item in detail.entity_links], [first_entity]
        )
        self.assertEqual(
            {item.owner_id for item in detail.field_links},
            {first_name, load_field},
        )
        self.assertNotIn(second_entity, [item.owner_id for item in detail.entity_links])
        self.assertIsNone(get_evidence_detail(self.connection, 999999))

    def test_colliding_outcome_and_result_labels_are_stable_and_parent_aware(
        self,
    ) -> None:
        outcome_ids: list[int] = []
        name_ids: list[int] = []
        result_ids: list[int] = []
        for sort_order in (0, 1):
            outcome_id = create_structured_entity(
                self.connection,
                self.literature_id,
                "outcome",
                sort_order=sort_order,
            )
            outcome_ids.append(outcome_id)
            name_ids.append(
                create_structured_field(
                    self.connection,
                    outcome_id,
                    "name",
                    content_role="source_fact",
                    value="Strain",
                    availability="reported",
                )
            )
            result_id = create_structured_entity(
                self.connection,
                self.literature_id,
                "result",
                parent_entity_id=outcome_id,
                sort_order=sort_order + 2,
            )
            result_ids.append(result_id)
            create_structured_field(
                self.connection,
                result_id,
                "condition_or_comparison",
                content_role="source_fact",
                value="Baseline",
                availability="reported",
            )
            create_structured_field(
                self.connection,
                result_id,
                "result",
                content_role="source_fact",
                value="No significant difference",
                availability="reported",
            )

        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Results"
        )
        attach_evidence(
            self.connection,
            "entity",
            outcome_ids[1],
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "entity",
            result_ids[1],
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            name_ids[1],
            evidence_id,
            confirmed=True,
        )

        items = list_structured_items_with_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(items)
        assert items is not None
        entity_labels = {
            item.owner_id: item.item_label
            for item in items
            if item.kind == "entity"
        }
        self.assertEqual(
            [entity_labels[item] for item in outcome_ids],
            ["Strain (1)", "Strain (2)"],
        )
        self.assertEqual(
            [entity_labels[item] for item in result_ids],
            [
                "Strain (1) — Baseline — No significant difference",
                "Strain (2) — Baseline — No significant difference",
            ],
        )
        field_categories = {
            item.owner_id: item.category_label
            for item in items
            if item.kind == "field"
        }
        self.assertEqual(field_categories[name_ids[0]], "Outcome / Strain (1)")
        self.assertEqual(field_categories[name_ids[1]], "Outcome / Strain (2)")

        detail = get_evidence_detail(self.connection, evidence_id)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(
            [item.item_label for item in detail.entity_links],
            [
                "Strain (2)",
                "Strain (2) — Baseline — No significant difference",
            ],
        )
        self.assertEqual(
            detail.field_links[0].category_label,
            "Outcome / Strain (2)",
        )

        restarted = connect_database(self.database_path)
        try:
            restarted_items = list_structured_items_with_evidence(
                restarted, self.literature_id
            )
            self.assertIsNotNone(restarted_items)
            assert restarted_items is not None
            self.assertEqual(
                [
                    (item.kind, item.owner_id, item.category_label, item.item_label)
                    for item in restarted_items
                ],
                [
                    (item.kind, item.owner_id, item.category_label, item.item_label)
                    for item in items
                ],
            )
        finally:
            restarted.close()

    def test_distinct_outcome_contexts_need_no_ordinal(self) -> None:
        first_entity, _, _ = self.create_outcome("Strain", "superficial")
        second_entity, _, _ = self.create_outcome("Strain", "deep")

        items = list_structured_items_with_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(items)
        assert items is not None
        labels = {
            item.owner_id: item.item_label
            for item in items
            if item.kind == "entity"
        }
        self.assertEqual(labels[first_entity], "Strain — superficial")
        self.assertEqual(labels[second_entity], "Strain — deep")
        self.assertNotIn("(1)", labels[first_entity])
        self.assertNotIn("(2)", labels[second_entity])

    def test_interpretation_json_null_is_explicit_and_source_fact_is_unchanged(
        self,
    ) -> None:
        relevance_id = create_structured_entity(
            self.connection, self.literature_id, "research_relevance"
        )
        interpretation_id = create_structured_field(
            self.connection,
            relevance_id,
            "summary",
            content_role="interpretation",
            value=None,
        )
        study_id = create_structured_entity(
            self.connection, self.literature_id, "study"
        )
        source_fact_id = create_structured_field(
            self.connection,
            study_id,
            "population",
            content_role="source_fact",
            value=None,
            availability="not_reported",
        )

        items = list_structured_items_with_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(items)
        assert items is not None
        values = {
            item.owner_id: item.value_text
            for item in items
            if item.kind == "field"
        }
        self.assertEqual(values[interpretation_id], "null（解釈値）")
        self.assertNotEqual(values[interpretation_id], "未登録")
        self.assertEqual(
            values[source_fact_id], "availability: not_reported"
        )

    def test_manual_create_forces_unverified_and_validates_without_partial_write(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            create_review_evidence(
                self.connection,
                self.literature_id,
                pdf_page=1,
            )
        self.assertEqual(
            list_literature_evidence(self.connection, self.literature_id), []
        )

        evidence_id = create_review_evidence(
            self.connection,
            self.literature_id,
            pdf_page=2,
            section="Synthetic Methods",
            table_label="Table 1",
            figure_label="Figure 1",
            quote_text="Synthetic exact text",
            note="Synthetic note",
            confirmed=True,
        )
        evidence = get_evidence_reference(self.connection, evidence_id)
        self.assertIsNotNone(evidence)
        assert evidence is not None
        self.assertEqual(evidence.verification, "ai_unverified")
        self.assertEqual(evidence.pdf_page, 2)

        before = self.connection.execute(
            "SELECT COUNT(*) FROM evidence_references"
        ).fetchone()[0]
        invalid_values = (
            {"note": "note only"},
            {"pdf_page": 0},
            {"section": 123},
            {"quote_text": "   "},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                create_review_evidence(
                    self.connection,
                    self.literature_id,
                    **values,
                    confirmed=True,
                )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM evidence_references"
            ).fetchone()[0],
            before,
        )

    def test_edit_reset_rule_note_quote_locator_unchanged_cancel_and_invalid(self) -> None:
        evidence_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            pdf_page=5,
            section="Synthetic Results",
            quote_text="Original synthetic quote",
            note="Old note",
            verification="user_verified",
        )

        note_preview = build_evidence_edit_preview(
            self.connection, evidence_id, {"note": "New note"}
        )
        self.assertIsNotNone(note_preview)
        assert note_preview is not None
        self.assertFalse(note_preview.substantive_change)
        self.assertEqual(note_preview.verification_after_save, "user_verified")
        self.assertTrue(
            save_evidence_edit(
                self.connection, note_preview, confirmed=True
            )
        )
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "user_verified",
        )

        unchanged_preview = build_evidence_edit_preview(
            self.connection, evidence_id, {"pdf_page": 5}
        )
        self.assertIsNotNone(unchanged_preview)
        assert unchanged_preview is not None
        self.assertFalse(unchanged_preview.substantive_change)
        self.assertEqual(
            unchanged_preview.verification_after_save, "user_verified"
        )
        self.assertTrue(
            save_evidence_edit(
                self.connection, unchanged_preview, confirmed=True
            )
        )

        quote_preview = build_evidence_edit_preview(
            self.connection,
            evidence_id,
            {"quote_text": "Changed synthetic quote"},
        )
        self.assertIsNotNone(quote_preview)
        assert quote_preview is not None
        self.assertTrue(quote_preview.substantive_change)
        self.assertEqual(quote_preview.verification_after_save, "ai_unverified")
        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            save_evidence_edit(self.connection, quote_preview)
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).quote_text,
            "Original synthetic quote",
        )
        self.assertTrue(
            save_evidence_edit(
                self.connection, quote_preview, confirmed=True
            )
        )
        updated = get_evidence_reference(self.connection, evidence_id)
        self.assertEqual(updated.quote_text, "Changed synthetic quote")
        self.assertEqual(updated.verification, "ai_unverified")

        change_evidence_verification(
            self.connection,
            evidence_id,
            "user_verified",
            confirmed=True,
        )
        locator_preview = build_evidence_edit_preview(
            self.connection, evidence_id, {"section": "Discussion"}
        )
        self.assertIsNotNone(locator_preview)
        assert locator_preview is not None
        self.assertTrue(
            save_evidence_edit(
                self.connection, locator_preview, confirmed=True
            )
        )
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "ai_unverified",
        )

        original = get_evidence_reference(self.connection, evidence_id)
        invalid_preview = build_evidence_edit_preview(
            self.connection, evidence_id, {"pdf_page": 0}
        )
        self.assertIsNotNone(invalid_preview)
        with self.assertRaises(ValueError):
            save_evidence_edit(
                self.connection, invalid_preview, confirmed=True
            )
        self.assertEqual(get_evidence_reference(self.connection, evidence_id), original)

    def test_edit_detects_preview_race_and_preserves_newer_data(self) -> None:
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Before"
        )
        preview = build_evidence_edit_preview(
            self.connection, evidence_id, {"section": "Preview value"}
        )
        self.assertIsNotNone(preview)
        update_evidence_reference(
            self.connection, evidence_id, {"section": "Concurrent value"}
        )
        with self.assertRaisesRegex(ValueError, "Preview後"):
            save_evidence_edit(self.connection, preview, confirmed=True)
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).section,
            "Concurrent value",
        )

    def test_forged_edit_preview_cannot_create_user_verified(self) -> None:
        evidence_id = create_evidence_reference(
            self.connection,
            self.literature_id,
            section="Synthetic unverified source",
            verification="ai_unverified",
        )
        original = get_evidence_reference(self.connection, evidence_id)
        self.assertIsNotNone(original)
        assert original is not None
        forged = EvidenceEditPreview(
            original=original,
            updates=(("note", "Synthetic note"),),
            substantive_change=False,
            verification_after_save="user_verified",
        )

        self.assertTrue(
            save_evidence_edit(self.connection, forged, confirmed=True)
        )
        updated = get_evidence_reference(self.connection, evidence_id)
        self.assertEqual(updated.note, "Synthetic note")
        self.assertEqual(updated.verification, "ai_unverified")

    def test_verification_is_explicit_reversible_and_does_not_cascade(self) -> None:
        entity_id, field_id, _ = self.create_outcome(
            "Synthetic verified outcome", "Synthetic context"
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=7
        )
        attach_evidence(
            self.connection,
            "entity",
            entity_id,
            evidence_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            field_id,
            evidence_id,
            confirmed=True,
        )
        before_literature = get_literature(self.connection, self.literature_id)
        before_entity = get_structured_entity(self.connection, entity_id)
        before_field = get_structured_field(self.connection, field_id)

        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            change_evidence_verification(
                self.connection, evidence_id, "user_verified"
            )
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "ai_unverified",
        )
        self.assertTrue(
            change_evidence_verification(
                self.connection,
                evidence_id,
                "user_verified",
                confirmed=True,
            )
        )
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            "user_verified",
        )
        self.assertTrue(
            change_evidence_verification(
                self.connection,
                evidence_id,
                "ai_unverified",
                confirmed=True,
            )
        )
        self.assertEqual(get_literature(self.connection, self.literature_id), before_literature)
        self.assertEqual(get_structured_entity(self.connection, entity_id), before_entity)
        self.assertEqual(get_structured_field(self.connection, field_id), before_field)
        with self.assertRaises(ValueError):
            change_evidence_verification(
                self.connection, evidence_id, "verified", confirmed=True
            )

    def test_attach_detach_duplicate_cross_literature_and_cancellation(self) -> None:
        entity_id, field_id, _ = self.create_outcome(
            "Synthetic association outcome", "Synthetic condition"
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, section="Results"
        )
        other_evidence_id = create_evidence_reference(
            self.connection, self.other_literature_id, section="Other"
        )
        before_verification = get_evidence_reference(
            self.connection, evidence_id
        ).verification

        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            attach_evidence(
                self.connection, "field", field_id, evidence_id
            )
        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])
        self.assertTrue(
            attach_evidence(
                self.connection,
                "field",
                field_id,
                evidence_id,
                confirmed=True,
            )
        )
        self.assertFalse(
            attach_evidence(
                self.connection,
                "field",
                field_id,
                evidence_id,
                confirmed=True,
            )
        )
        self.assertTrue(
            attach_evidence(
                self.connection,
                "entity",
                entity_id,
                evidence_id,
                confirmed=True,
            )
        )
        with self.assertRaisesRegex(ValueError, "別Literature"):
            attach_evidence(
                self.connection,
                "field",
                field_id,
                other_evidence_id,
                confirmed=True,
            )
        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            detach_evidence(
                self.connection, "field", field_id, evidence_id
            )
        self.assertTrue(
            detach_evidence(
                self.connection,
                "field",
                field_id,
                evidence_id,
                confirmed=True,
            )
        )
        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])
        self.assertIsNotNone(get_evidence_reference(self.connection, evidence_id))
        self.assertIsNotNone(get_structured_field(self.connection, field_id))
        self.assertEqual(
            get_evidence_reference(self.connection, evidence_id).verification,
            before_verification,
        )

    def test_delete_removes_only_target_evidence_and_links(self) -> None:
        entity_id, field_id, _ = self.create_outcome(
            "Synthetic delete outcome", "Synthetic delete condition"
        )
        target_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=8
        )
        unrelated_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=9
        )
        attach_evidence(
            self.connection,
            "entity",
            entity_id,
            target_id,
            confirmed=True,
        )
        attach_evidence(
            self.connection,
            "field",
            field_id,
            target_id,
            confirmed=True,
        )
        with self.assertRaisesRegex(ValueError, "confirmed=True"):
            delete_review_evidence(self.connection, target_id)
        self.assertIsNotNone(get_evidence_reference(self.connection, target_id))

        self.assertTrue(
            delete_review_evidence(
                self.connection, target_id, confirmed=True
            )
        )
        self.assertIsNone(get_evidence_reference(self.connection, target_id))
        self.assertIsNotNone(get_evidence_reference(self.connection, unrelated_id))
        self.assertIsNotNone(get_literature(self.connection, self.literature_id))
        self.assertIsNotNone(get_structured_entity(self.connection, entity_id))
        self.assertIsNotNone(get_structured_field(self.connection, field_id))
        self.assertEqual(list_evidence_for_entity(self.connection, entity_id), [])
        self.assertEqual(list_evidence_for_field(self.connection, field_id), [])

    def test_transaction_guards_reads_integrity_and_failure_isolation(self) -> None:
        entity_id, field_id, _ = self.create_outcome(
            "Synthetic transaction outcome", "Synthetic transaction context"
        )
        evidence_id = create_evidence_reference(
            self.connection, self.literature_id, pdf_page=10
        )
        marker = self.connection.execute(
            "INSERT INTO tags (name) VALUES (?)", ("pending-evidence-marker",)
        )
        marker_id = marker.lastrowid
        self.assertTrue(self.connection.in_transaction)
        detail = get_evidence_detail(self.connection, evidence_id)
        items = list_structured_items_with_evidence(
            self.connection, self.literature_id
        )
        self.assertIsNotNone(detail)
        self.assertIsNotNone(items)
        self.assertTrue(self.connection.in_transaction)
        with self.assertRaisesRegex(ValueError, "アクティブ"):
            create_review_evidence(
                self.connection,
                self.literature_id,
                pdf_page=11,
                confirmed=True,
            )
        with self.assertRaisesRegex(ValueError, "アクティブ"):
            attach_evidence(
                self.connection,
                "field",
                field_id,
                evidence_id,
                confirmed=True,
            )
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM tags WHERE id = ?", (marker_id,)
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(get_evidence_reference(self.connection, evidence_id))
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(
            self.connection.execute("PRAGMA quick_check").fetchone()[0], "ok"
        )


if __name__ == "__main__":
    unittest.main()
