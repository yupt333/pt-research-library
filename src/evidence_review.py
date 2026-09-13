"""Safe Evidence review workflows built on the structured repository CRUD."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Mapping, Optional

from src.models import EvidenceReference, StructuredEntity, StructuredField
from src.structured_repository import (
    attach_evidence_to_entity,
    attach_evidence_to_field,
    create_evidence_reference,
    delete_evidence_reference,
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
)


EVIDENCE_EDITABLE_FIELDS = (
    "pdf_page",
    "printed_page",
    "section",
    "subsection",
    "table_label",
    "figure_label",
    "quote_text",
    "note",
)
SUBSTANTIVE_EVIDENCE_FIELDS = frozenset(EVIDENCE_EDITABLE_FIELDS[:-1])
ASSOCIATION_KINDS = frozenset({"entity", "field"})

_ENTITY_TYPE_LABELS = {
    "study": "Study",
    "method_measurement_imaging": "Methods / Measurement & Imaging",
    "method_body_condition": "Methods / Body Condition",
    "method_task_protocol": "Methods / Task & Protocol",
    "method_analysis": "Methods / Analysis",
    "method_validation_statistics": "Methods / Validation & Statistics",
    "outcome": "Outcome",
    "result": "Result",
    "limitation": "Limitation",
    "concept": "Concept",
    "research_relevance": "Research Relevance",
}

_FIELD_LABELS = {
    "condition_or_comparison": "Condition / comparison",
    "research_objective": "Research objective",
    "calculation_method": "Calculation method",
    "context_condition": "Context / condition",
    "context_group": "Context / group",
    "context_body_position": "Context / body position",
    "context_task": "Context / task",
    "context_load": "Context / load",
    "context_region": "Context / region",
    "context_layer": "Context / layer",
    "context_time_point": "Context / time point",
}


@dataclass(frozen=True)
class StructuredItemEvidence:
    """One researcher-facing structured item and its linked Evidence."""

    kind: str
    owner_id: int
    category_label: str
    item_label: str
    value_text: Optional[str]
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class EvidenceDetail:
    """One Evidence row with human-readable structured-item backlinks."""

    evidence: EvidenceReference
    field_links: tuple[StructuredItemEvidence, ...]
    entity_links: tuple[StructuredItemEvidence, ...]


@dataclass(frozen=True)
class EvidenceEditPreview:
    """Immutable preview state used to guard a reviewed Evidence edit."""

    original: EvidenceReference
    updates: tuple[tuple[str, object], ...]
    substantive_change: bool
    verification_after_save: str


def _require_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise ValueError("confirmed=Trueの明示確認なしでは変更できません。")


def _require_standalone_write(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise ValueError(
            "アクティブなトランザクション中はEvidenceを変更できません。"
        )


def _json_value_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(", ", ": "),
    )


def _field_value_text(field: StructuredField) -> str:
    if field.value is not None:
        return _json_value_text(field.value)
    if field.content_role == "interpretation":
        return "null（解釈値）"
    if field.availability is not None:
        return f"availability: {field.availability}"
    return "未登録"


def _field_label(field_key: str) -> str:
    return _FIELD_LABELS.get(field_key, field_key.replace("_", " "))


def _field_by_key(
    fields: tuple[StructuredField, ...], field_key: str
) -> Optional[StructuredField]:
    return next((field for field in fields if field.field_key == field_key), None)


def _reported_field_text(
    fields: tuple[StructuredField, ...], field_key: str
) -> Optional[str]:
    field = _field_by_key(fields, field_key)
    if field is None or field.value is None:
        return None
    return _json_value_text(field.value)


def _entity_item_label(
    entity: StructuredEntity, fields: tuple[StructuredField, ...]
) -> str:
    """Build a label only from stored information, without inferring facts."""
    entity_type = entity.entity_type
    if entity_type == "outcome":
        name = _reported_field_text(fields, "name")
        contexts = tuple(
            text
            for key in (
                "context_condition",
                "context_group",
                "context_body_position",
                "context_task",
                "context_load",
                "context_region",
                "context_layer",
                "context_time_point",
            )
            if (text := _reported_field_text(fields, key)) is not None
        )
        if name is not None and contexts:
            return f"{name} — {' / '.join(contexts)}"
        if name is not None:
            return name
    elif entity_type == "concept":
        name = _reported_field_text(fields, "name")
        if name is not None:
            return name
    elif entity_type == "result":
        condition = _reported_field_text(fields, "condition_or_comparison")
        result = _reported_field_text(fields, "result")
        if condition is not None and result is not None:
            return f"{condition} — {result}"
        if condition is not None or result is not None:
            return condition or result or entity_type
    elif entity_type == "limitation":
        text = _reported_field_text(fields, "text")
        if text is not None:
            return text
    elif entity_type == "research_relevance":
        summary = _reported_field_text(fields, "summary")
        if summary is not None:
            return summary

    return _ENTITY_TYPE_LABELS.get(entity_type, entity_type)


def _disambiguate_entity_labels(
    entities: tuple[StructuredEntity, ...],
    labels: dict[int, str],
    entity_type: str,
) -> None:
    """Add stable ordinals only to colliding labels of one entity type."""
    groups: dict[str, list[StructuredEntity]] = {}
    for entity in entities:
        if entity.entity_type == entity_type and entity.id is not None:
            groups.setdefault(labels[entity.id], []).append(entity)

    reserved_labels = set(groups)
    assigned_labels = {
        label for label, group in groups.items() if len(group) == 1
    }
    for base_label, group in groups.items():
        if len(group) == 1:
            continue
        ordinal = 1
        for entity in group:
            while True:
                candidate = f"{base_label} ({ordinal})"
                ordinal += 1
                if (
                    candidate not in reserved_labels
                    and candidate not in assigned_labels
                ):
                    break
            labels[entity.id] = candidate
            assigned_labels.add(candidate)


def evidence_locator_summary(evidence: EvidenceReference) -> str:
    """Return a concise source locator without inventing missing metadata."""
    parts: list[str] = []
    if evidence.pdf_page is not None:
        parts.append(f"PDF page {evidence.pdf_page}")
    if evidence.printed_page is not None:
        parts.append(f"printed page {evidence.printed_page}")
    if evidence.section is not None:
        parts.append(f"section {evidence.section}")
    if evidence.subsection is not None:
        parts.append(f"subsection {evidence.subsection}")
    if evidence.table_label is not None:
        parts.append(f"table {evidence.table_label}")
    if evidence.figure_label is not None:
        parts.append(f"figure {evidence.figure_label}")
    if evidence.quote_text is not None:
        parts.append("original textあり")
    return " / ".join(parts)


def list_structured_items_with_evidence(
    connection: sqlite3.Connection, literature_id: int
) -> Optional[list[StructuredItemEvidence]]:
    """List every entity and field with its Evidence, preserving DB identity."""
    entities = list_structured_entities_for_literature(connection, literature_id)
    if entities is None:
        return None

    entity_tuple = tuple(entities)
    fields_by_entity: dict[int, tuple[StructuredField, ...]] = {}
    labels: dict[int, str] = {}
    entities_by_id = {
        entity.id: entity for entity in entity_tuple if entity.id is not None
    }
    for entity in entity_tuple:
        if entity.id is None:
            continue
        fields_list = list_structured_fields_for_entity(connection, entity.id)
        fields = tuple(fields_list or ())
        fields_by_entity[entity.id] = fields
        labels[entity.id] = _entity_item_label(entity, fields)

    _disambiguate_entity_labels(entity_tuple, labels, "outcome")

    for entity in entity_tuple:
        if entity.id is None or entity.entity_type != "result":
            continue
        parent = entities_by_id.get(entity.parent_entity_id)
        if parent is not None and parent.entity_type == "outcome":
            labels[entity.id] = (
                f"{labels[parent.id]} — {labels[entity.id]}"
            )
    _disambiguate_entity_labels(entity_tuple, labels, "result")

    items: list[StructuredItemEvidence] = []
    for entity in entity_tuple:
        if entity.id is None:
            continue
        fields = fields_by_entity[entity.id]
        category_label = _ENTITY_TYPE_LABELS.get(
            entity.entity_type, entity.entity_type
        )
        entity_label = labels[entity.id]
        entity_evidence = list_evidence_for_entity(connection, entity.id)
        if entity_evidence is None:
            entity_evidence = []
        items.append(
            StructuredItemEvidence(
                kind="entity",
                owner_id=entity.id,
                category_label=category_label,
                item_label=entity_label,
                value_text=None,
                evidence=tuple(entity_evidence),
            )
        )
        field_category_label = (
            category_label
            if entity_label == category_label
            else f"{category_label} / {entity_label}"
        )
        for field in fields:
            field_evidence = list_evidence_for_field(connection, field.id)
            if field_evidence is None:
                field_evidence = []
            items.append(
                StructuredItemEvidence(
                    kind="field",
                    owner_id=field.id,
                    category_label=field_category_label,
                    item_label=_field_label(field.field_key),
                    value_text=_field_value_text(field),
                    evidence=tuple(field_evidence),
                )
            )
    return items


def get_evidence_detail(
    connection: sqlite3.Connection, evidence_id: int
) -> Optional[EvidenceDetail]:
    """Return one Evidence and the structured items it supports."""
    evidence = get_evidence_reference(connection, evidence_id)
    if evidence is None:
        return None
    items = list_structured_items_with_evidence(
        connection, evidence.literature_id
    )
    if items is None:
        return None
    linked = tuple(
        item
        for item in items
        if any(linked.id == evidence.id for linked in item.evidence)
    )
    return EvidenceDetail(
        evidence=evidence,
        field_links=tuple(item for item in linked if item.kind == "field"),
        entity_links=tuple(item for item in linked if item.kind == "entity"),
    )


def create_review_evidence(
    connection: sqlite3.Connection,
    literature_id: int,
    *,
    pdf_page: object = None,
    printed_page: object = None,
    section: object = None,
    subsection: object = None,
    table_label: object = None,
    figure_label: object = None,
    quote_text: object = None,
    note: object = None,
    confirmed: bool = False,
) -> int:
    """Create manually entered Evidence, always as ``ai_unverified``."""
    _require_confirmation(confirmed)
    _require_standalone_write(connection)
    return create_evidence_reference(
        connection,
        literature_id,
        pdf_page=pdf_page,
        printed_page=printed_page,
        section=section,
        subsection=subsection,
        table_label=table_label,
        figure_label=figure_label,
        quote_text=quote_text,
        note=note,
        verification="ai_unverified",
    )


def build_evidence_edit_preview(
    connection: sqlite3.Connection,
    evidence_id: int,
    updates: Mapping[str, object],
) -> Optional[EvidenceEditPreview]:
    """Prepare an edit and determine whether verification will be reset."""
    if not isinstance(updates, Mapping):
        raise TypeError("updatesはmappingで指定してください。")
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    invalid = set(updates) - set(EVIDENCE_EDITABLE_FIELDS)
    if invalid:
        raise ValueError(f"更新できない項目が指定されました: {sorted(invalid)!r}")
    evidence = get_evidence_reference(connection, evidence_id)
    if evidence is None:
        return None
    substantive_change = any(
        name in SUBSTANTIVE_EVIDENCE_FIELDS
        and value != getattr(evidence, name)
        for name, value in updates.items()
    )
    verification_after = (
        "ai_unverified"
        if evidence.verification == "user_verified" and substantive_change
        else evidence.verification
    )
    return EvidenceEditPreview(
        original=evidence,
        updates=tuple(updates.items()),
        substantive_change=substantive_change,
        verification_after_save=verification_after,
    )


def save_evidence_edit(
    connection: sqlite3.Connection,
    preview: EvidenceEditPreview,
    *,
    confirmed: bool = False,
) -> bool:
    """Save a previewed edit, resetting verified substantive edits only."""
    _require_confirmation(confirmed)
    if not isinstance(preview, EvidenceEditPreview):
        raise TypeError(
            "previewはbuild_evidence_edit_previewの結果を指定してください。"
        )
    _require_standalone_write(connection)
    evidence_id = preview.original.id
    if evidence_id is None:
        raise ValueError("Evidence IDがありません。")
    current = get_evidence_reference(connection, evidence_id)
    if current is None:
        return False
    if current != preview.original:
        raise ValueError(
            "Preview後にEvidenceが変更されました。再度内容を確認してください。"
        )
    updates = dict(preview.updates)
    reviewed_preview = build_evidence_edit_preview(
        connection, evidence_id, updates
    )
    if reviewed_preview is None:
        return False
    if reviewed_preview.verification_after_save != current.verification:
        updates["verification"] = reviewed_preview.verification_after_save
    return update_evidence_reference(connection, evidence_id, updates)


def change_evidence_verification(
    connection: sqlite3.Connection,
    evidence_id: int,
    verification: str,
    *,
    confirmed: bool = False,
) -> bool:
    """Change only one Evidence verification after explicit confirmation."""
    _require_confirmation(confirmed)
    if verification not in {"ai_unverified", "user_verified"}:
        raise ValueError(
            "verificationはai_unverifiedまたはuser_verifiedで指定してください。"
        )
    _require_standalone_write(connection)
    evidence = get_evidence_reference(connection, evidence_id)
    if evidence is None:
        return False
    if evidence.verification == verification:
        return True
    return update_evidence_reference(
        connection, evidence_id, {"verification": verification}
    )


def _association_owners(
    connection: sqlite3.Connection,
    kind: str,
    owner_id: int,
    evidence_id: int,
) -> tuple[StructuredEntity | StructuredField, EvidenceReference]:
    if kind not in ASSOCIATION_KINDS:
        raise ValueError("kindはentityまたはfieldで指定してください。")
    owner = (
        get_structured_entity(connection, owner_id)
        if kind == "entity"
        else get_structured_field(connection, owner_id)
    )
    if owner is None:
        raise ValueError("対象structured itemが存在しません。")
    evidence = get_evidence_reference(connection, evidence_id)
    if evidence is None:
        raise ValueError("対象Evidenceが存在しません。")
    if owner.literature_id != evidence.literature_id:
        raise ValueError("別LiteratureのEvidenceは関連付けできません。")
    return owner, evidence


def attach_evidence(
    connection: sqlite3.Connection,
    kind: str,
    owner_id: int,
    evidence_id: int,
    *,
    confirmed: bool = False,
) -> bool:
    """Attach Evidence to one same-Literature structured item."""
    _require_confirmation(confirmed)
    _require_standalone_write(connection)
    _association_owners(connection, kind, owner_id, evidence_id)
    if kind == "entity":
        return attach_evidence_to_entity(connection, owner_id, evidence_id)
    return attach_evidence_to_field(connection, owner_id, evidence_id)


def detach_evidence(
    connection: sqlite3.Connection,
    kind: str,
    owner_id: int,
    evidence_id: int,
    *,
    confirmed: bool = False,
) -> bool:
    """Detach only one same-Literature link, preserving both owner rows."""
    _require_confirmation(confirmed)
    _require_standalone_write(connection)
    _association_owners(connection, kind, owner_id, evidence_id)
    if kind == "entity":
        return detach_evidence_from_entity(connection, owner_id, evidence_id)
    return detach_evidence_from_field(connection, owner_id, evidence_id)


def delete_review_evidence(
    connection: sqlite3.Connection,
    evidence_id: int,
    *,
    confirmed: bool = False,
) -> bool:
    """Delete one Evidence row; schema cascades only its association rows."""
    _require_confirmation(confirmed)
    _require_standalone_write(connection)
    return delete_evidence_reference(connection, evidence_id)


def list_literature_evidence(
    connection: sqlite3.Connection, literature_id: int
) -> Optional[list[EvidenceReference]]:
    """Expose the deterministic repository read under review terminology."""
    return list_evidence_for_literature(connection, literature_id)
