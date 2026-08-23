"""Validated CRUD for canonical structured research data and Evidence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Mapping, Optional

from src.models import EvidenceReference, StructuredEntity, StructuredField


class StructuredDataError(RuntimeError):
    """Raised when canonical structured data cannot be decoded safely."""


ENTITY_FIELD_VOCABULARY = {
    "study": frozenset(
        {
            "study_design",
            "research_objective",
            "population",
            "sample_size",
            "demographics",
            "condition_diagnosis",
            "health_status",
            "inclusion_criteria",
            "exclusion_criteria",
            "group_allocation",
            "study_setting",
        }
    ),
    "method_measurement_imaging": frozenset(
        {
            "measurement",
            "imaging_modality",
            "imaging_condition",
            "device",
            "probe",
            "probe_orientation",
            "frame_rate",
            "sampling_condition",
            "calibration_scale",
            "roi",
        }
    ),
    "method_body_condition": frozenset(
        {
            "body_position",
            "joint_position",
            "joint_angle",
            "limb_position",
            "contraction_type",
            "muscle_activation_condition",
            "load",
            "weight_bearing_condition",
        }
    ),
    "method_task_protocol": frozenset(
        {
            "task",
            "movement",
            "range",
            "speed",
            "repetition",
            "duration",
            "rest",
            "trial_number",
        }
    ),
    "method_analysis": frozenset(
        {
            "analysis_method",
            "tracking_algorithm",
            "preprocessing",
            "reference_frame_baseline",
            "calculation_method",
            "roi_handling",
            "quality_control",
        }
    ),
    "method_validation_statistics": frozenset(
        {
            "validation",
            "reliability_method",
            "statistical_analysis",
            "icc",
            "sem",
            "mdc",
            "mcid",
            "agreement_analysis",
            "other_statistical_method",
        }
    ),
    "outcome": frozenset(
        {
            "name",
            "definition",
            "calculation_method",
            "unit",
            "context_condition",
            "context_group",
            "context_body_position",
            "context_task",
            "context_load",
            "context_region",
            "context_layer",
            "context_time_point",
            "validation_information",
        }
    ),
    "result": frozenset(
        {"condition_or_comparison", "result", "statistics"}
    ),
    "limitation": frozenset({"text"}),
    "concept": frozenset({"name", "context"}),
    "research_relevance": frozenset(
        {
            "summary",
            "methodological_relevance",
            "clinical_relevance",
            "protocol_relevance",
            "methodological_cautions",
            "comparison_notes",
        }
    ),
}

ENTITY_TYPES = frozenset(ENTITY_FIELD_VOCABULARY)
METHOD_ENTITY_TYPES = frozenset(
    entity_type
    for entity_type in ENTITY_TYPES
    if entity_type.startswith("method_")
)
AVAILABILITIES = frozenset(
    {
        "reported",
        "not_reported",
        "not_extracted",
        "unclear",
        "not_applicable",
    }
)
VERIFICATIONS = frozenset({"ai_unverified", "user_verified"})
CONTENT_ROLES = frozenset({"source_fact", "interpretation"})

_ENTITY_SELECT_COLUMNS = (
    "id",
    "literature_id",
    "entity_type",
    "parent_entity_id",
    "sort_order",
    "verification",
    "created_at",
    "updated_at",
)
_FIELD_SELECT_COLUMNS = (
    "id",
    "literature_id",
    "entity_id",
    "field_key",
    "content_role",
    "value_json",
    "availability",
    "verification",
    "note",
    "created_at",
    "updated_at",
)
_EVIDENCE_SELECT_COLUMNS = (
    "id",
    "literature_id",
    "pdf_page",
    "printed_page",
    "section",
    "subsection",
    "table_label",
    "figure_label",
    "quote_text",
    "note",
    "verification",
    "created_at",
    "updated_at",
)


def _validate_positive_id(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name}は1以上の整数で指定してください。")
    return value


def _validate_verification(value: object) -> str:
    if not isinstance(value, str) or value not in VERIFICATIONS:
        raise ValueError(
            "verificationはai_unverifiedまたはuser_verifiedで指定してください。"
        )
    return str(value)


def _validate_optional_text(name: str, value: object) -> Optional[str]:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name}はNoneまたは文字列で指定してください。")
    return value


def _validate_optional_locator_text(
    name: str, value: object
) -> Optional[str]:
    checked = _validate_optional_text(name, value)
    if checked is not None and not checked.strip():
        raise ValueError(f"{name}はNoneまたは空白以外を含む文字列で指定してください。")
    return checked


def _validate_sort_order(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sort_orderは0以上の整数で指定してください。")
    return value


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_updated_at(previous_updated_at: str) -> str:
    previous = datetime.fromisoformat(
        previous_updated_at.replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    current = _utc_now()
    value = current if current > previous else previous + timedelta(microseconds=1)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@contextmanager
def _write_scope(connection: sqlite3.Connection) -> Iterator[None]:
    """Commit standalone CRUD while joining an explicit caller transaction."""
    if connection.in_transaction:
        yield
    else:
        with connection:
            yield


def _literature_exists(connection: sqlite3.Connection, literature_id: int) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM literature WHERE id = ?", (literature_id,)
        ).fetchone()
        is not None
    )


def _entity_row(
    connection: sqlite3.Connection, entity_id: int
) -> Optional[sqlite3.Row]:
    columns = ", ".join(_ENTITY_SELECT_COLUMNS)
    return connection.execute(
        f"SELECT {columns} FROM structured_entities WHERE id = ?",
        (entity_id,),
    ).fetchone()


def _allowed_parent_types(entity_type: str) -> Optional[frozenset[str]]:
    if entity_type == "study":
        return frozenset()
    if entity_type == "result":
        return frozenset({"outcome"})
    return frozenset({"study"})


def _validate_entity_type(value: object) -> str:
    if not isinstance(value, str) or value not in ENTITY_TYPES:
        raise ValueError(f"許可されていないentity_typeです: {value!r}")
    return str(value)


def _validate_parent(
    connection: sqlite3.Connection,
    *,
    literature_id: int,
    entity_type: str,
    parent_entity_id: object,
    entity_id: Optional[int] = None,
) -> Optional[int]:
    allowed_parent_types = _allowed_parent_types(entity_type)
    assert allowed_parent_types is not None

    if parent_entity_id is None:
        if entity_type == "result":
            raise ValueError("resultにはOutcome parentが必須です。")
        return None

    parent_id = _validate_positive_id("parent_entity_id", parent_entity_id)
    if entity_id is not None and parent_id == entity_id:
        raise ValueError("structured entityをself-parentにはできません。")

    parent = _entity_row(connection, parent_id)
    if parent is None:
        raise ValueError(f"parent entity ID {parent_id} は存在しません。")
    if parent["literature_id"] != literature_id:
        raise ValueError("別Literatureのentityをparentにはできません。")
    if parent["entity_type"] not in allowed_parent_types:
        allowed = "、".join(sorted(allowed_parent_types)) or "parentなし"
        raise ValueError(
            f"{entity_type}のparentは{allowed}でなければなりません。"
        )

    if entity_id is not None:
        visited: set[int] = set()
        ancestor_id: Optional[int] = parent_id
        while ancestor_id is not None:
            if ancestor_id == entity_id:
                raise ValueError("ancestor cycleになるparentは指定できません。")
            if ancestor_id in visited:
                raise StructuredDataError(
                    "既存structured entity hierarchyにcycleがあります。"
                )
            visited.add(ancestor_id)
            ancestor = _entity_row(connection, ancestor_id)
            if ancestor is None:
                raise StructuredDataError(
                    "既存structured entity hierarchyのparentが見つかりません。"
                )
            ancestor_id = ancestor["parent_entity_id"]
    return parent_id


def _row_to_entity(row: sqlite3.Row) -> StructuredEntity:
    return StructuredEntity(**dict(row))


def create_structured_entity(
    connection: sqlite3.Connection,
    literature_id: object,
    entity_type: object,
    *,
    parent_entity_id: object = None,
    sort_order: object = 0,
    verification: object = "ai_unverified",
) -> int:
    """Create a validated entity and return its generated SQLite ID."""
    validated_literature_id = _validate_positive_id(
        "literature_id", literature_id
    )
    validated_type = _validate_entity_type(entity_type)
    validated_sort_order = _validate_sort_order(sort_order)
    validated_verification = _validate_verification(verification)
    if not _literature_exists(connection, validated_literature_id):
        raise ValueError(
            f"Literature ID {validated_literature_id} は存在しません。"
        )
    validated_parent_id = _validate_parent(
        connection,
        literature_id=validated_literature_id,
        entity_type=validated_type,
        parent_entity_id=parent_entity_id,
    )

    with _write_scope(connection):
        cursor = connection.execute(
            """
            INSERT INTO structured_entities (
                literature_id, entity_type, parent_entity_id,
                sort_order, verification
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                validated_literature_id,
                validated_type,
                validated_parent_id,
                validated_sort_order,
                validated_verification,
            ),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("structured entity IDを取得できませんでした。")
    return cursor.lastrowid


def get_structured_entity(
    connection: sqlite3.Connection, entity_id: object
) -> Optional[StructuredEntity]:
    """Return an entity, or None when its ID is unknown."""
    validated_id = _validate_positive_id("entity_id", entity_id)
    row = _entity_row(connection, validated_id)
    return None if row is None else _row_to_entity(row)


def list_structured_entities_for_literature(
    connection: sqlite3.Connection, literature_id: object
) -> Optional[list[StructuredEntity]]:
    """List one Literature's entities, or None for an unknown Literature."""
    validated_id = _validate_positive_id("literature_id", literature_id)
    if not _literature_exists(connection, validated_id):
        return None
    columns = ", ".join(_ENTITY_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM structured_entities
        WHERE literature_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (validated_id,),
    ).fetchall()
    return [_row_to_entity(row) for row in rows]


def update_structured_entity(
    connection: sqlite3.Connection,
    entity_id: object,
    updates: Mapping[str, object],
) -> bool:
    """Update mutable entity attributes after hierarchy validation."""
    validated_id = _validate_positive_id("entity_id", entity_id)
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    allowed = {"entity_type", "parent_entity_id", "sort_order", "verification"}
    invalid = set(updates) - allowed
    if invalid:
        raise ValueError(f"更新できない項目が指定されました: {sorted(invalid)!r}")
    row = _entity_row(connection, validated_id)
    if row is None:
        return False

    candidate = dict(row)
    candidate.update(updates)
    entity_type = _validate_entity_type(candidate["entity_type"])
    parent_id = _validate_parent(
        connection,
        literature_id=row["literature_id"],
        entity_type=entity_type,
        parent_entity_id=candidate["parent_entity_id"],
        entity_id=validated_id,
    )
    sort_order = _validate_sort_order(candidate["sort_order"])
    verification = _validate_verification(candidate["verification"])

    children = connection.execute(
        """
        SELECT entity_type
        FROM structured_entities
        WHERE parent_entity_id = ?
        """,
        (validated_id,),
    ).fetchall()
    for child in children:
        if entity_type not in _allowed_parent_types(child["entity_type"]):
            raise ValueError(
                "entity_type変更により既存childのparent規則が壊れます。"
            )
    existing_fields = connection.execute(
        """
        SELECT field_key, content_role
        FROM structured_fields
        WHERE entity_id = ?
        """,
        (validated_id,),
    ).fetchall()
    for field in existing_fields:
        if field["field_key"] not in ENTITY_FIELD_VOCABULARY[entity_type]:
            raise ValueError(
                "entity_type変更により既存fieldのvocabularyが不正になります。"
            )
        _validate_field_role(entity_type, field["content_role"])

    values = {
        "entity_type": entity_type,
        "parent_entity_id": parent_id,
        "sort_order": sort_order,
        "verification": verification,
    }
    columns = tuple(updates)
    assignments = ", ".join(f"{column} = ?" for column in columns)
    updated_at = _next_updated_at(row["updated_at"])
    with _write_scope(connection):
        cursor = connection.execute(
            f"""
            UPDATE structured_entities
            SET {assignments}, updated_at = ?
            WHERE id = ?
            """,
            tuple(values[column] for column in columns)
            + (updated_at, validated_id),
        )
    return cursor.rowcount == 1


def delete_structured_entity(
    connection: sqlite3.Connection, entity_id: object
) -> bool:
    """Delete one entity and its schema-defined descendants."""
    validated_id = _validate_positive_id("entity_id", entity_id)
    with _write_scope(connection):
        cursor = connection.execute(
            "DELETE FROM structured_entities WHERE id = ?", (validated_id,)
        )
    return cursor.rowcount == 1


def _reject_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON number is invalid: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _serialize_value(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError(
            "valueはfinite numberだけを含むJSON serializable値で指定してください。"
        ) from error


def _decode_value(value_json: str, field_id: int) -> object:
    try:
        return json.loads(
            value_json,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise StructuredDataError(
            f"structured field ID {field_id} のvalue_jsonがvalid JSONではありません。"
        ) from error


def _validate_field_role(entity_type: str, content_role: object) -> str:
    if not isinstance(content_role, str) or content_role not in CONTENT_ROLES:
        raise ValueError(
            "content_roleはsource_factまたはinterpretationで指定してください。"
        )
    role = str(content_role)
    if entity_type == "research_relevance" and role != "interpretation":
        raise ValueError("research_relevance fieldはinterpretationのみです。")
    if (
        entity_type not in {"research_relevance", "limitation", "concept"}
        and role != "source_fact"
    ):
        raise ValueError(f"{entity_type} fieldはsource_factのみです。")
    return role


def _validate_entity_basis_roles(
    connection: sqlite3.Connection,
    *,
    entity_id: int,
    entity_type: str,
    candidate_role: Optional[str] = None,
    exclude_field_id: Optional[int] = None,
) -> None:
    """Keep basis-selected limitation/concept fields in one content role."""
    if entity_type not in {"limitation", "concept"}:
        return
    query = """
        SELECT DISTINCT content_role
        FROM structured_fields
        WHERE entity_id = ?
    """
    parameters: tuple[object, ...] = (entity_id,)
    if exclude_field_id is not None:
        query += " AND id != ?"
        parameters += (exclude_field_id,)
    roles = {
        row["content_role"]
        for row in connection.execute(query, parameters).fetchall()
    }
    if candidate_role is not None:
        roles.add(candidate_role)
    if len(roles) > 1:
        raise ValueError(
            f"{entity_type} entity内のfieldは同じcontent_roleでなければなりません。"
        )


def _validate_field_values(
    *,
    entity_type: str,
    field_key: object,
    content_role: object,
    value: object,
    availability: object,
    verification: object,
    note: object,
) -> tuple[str, str, object, Optional[str], str, Optional[str], str | None]:
    if not isinstance(field_key, str) or field_key not in ENTITY_FIELD_VOCABULARY[
        entity_type
    ]:
        raise ValueError(
            f"{entity_type}で許可されていないfield_keyです: {field_key!r}"
        )
    role = _validate_field_role(entity_type, content_role)
    checked_verification = _validate_verification(verification)
    checked_note = _validate_optional_text("note", note)

    if role == "source_fact":
        if not isinstance(availability, str) or availability not in AVAILABILITIES:
            raise ValueError("source_factのavailabilityが許可値ではありません。")
        checked_availability = str(availability)
        if checked_availability == "reported":
            if value is None:
                raise ValueError("reported source_factにはnon-null valueが必要です。")
            value_json = _serialize_value(value)
        else:
            if value is not None:
                raise ValueError(
                    "reported以外のsource_factのvalueはNoneでなければなりません。"
                )
            value_json = None
    else:
        if availability is not None:
            raise ValueError("interpretationのavailabilityはNoneでなければなりません。")
        checked_availability = None
        value_json = _serialize_value(value)

    return (
        field_key,
        role,
        value,
        checked_availability,
        checked_verification,
        checked_note,
        value_json,
    )


def _field_row(
    connection: sqlite3.Connection, field_id: int
) -> Optional[sqlite3.Row]:
    columns = ", ".join(_FIELD_SELECT_COLUMNS)
    return connection.execute(
        f"SELECT {columns} FROM structured_fields WHERE id = ?", (field_id,)
    ).fetchone()


def _row_to_field(row: sqlite3.Row) -> StructuredField:
    value = (
        None
        if row["value_json"] is None
        else _decode_value(row["value_json"], row["id"])
    )
    values = dict(row)
    del values["value_json"]
    values["value"] = value
    return StructuredField(**values)


def create_structured_field(
    connection: sqlite3.Connection,
    entity_id: object,
    field_key: object,
    *,
    content_role: object,
    value: object,
    availability: object = None,
    verification: object = "ai_unverified",
    note: object = None,
) -> int:
    """Create one vocabulary-checked field from a Python JSON value."""
    validated_entity_id = _validate_positive_id("entity_id", entity_id)
    entity = _entity_row(connection, validated_entity_id)
    if entity is None:
        raise ValueError(f"entity ID {validated_entity_id} は存在しません。")
    (
        checked_key,
        checked_role,
        _,
        checked_availability,
        checked_verification,
        checked_note,
        value_json,
    ) = _validate_field_values(
        entity_type=entity["entity_type"],
        field_key=field_key,
        content_role=content_role,
        value=value,
        availability=availability,
        verification=verification,
        note=note,
    )
    _validate_entity_basis_roles(
        connection,
        entity_id=validated_entity_id,
        entity_type=entity["entity_type"],
        candidate_role=checked_role,
    )
    try:
        with _write_scope(connection):
            cursor = connection.execute(
                """
                INSERT INTO structured_fields (
                    literature_id, entity_id, field_key, content_role,
                    value_json, availability, verification, note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entity["literature_id"],
                    validated_entity_id,
                    checked_key,
                    checked_role,
                    value_json,
                    checked_availability,
                    checked_verification,
                    checked_note,
                ),
            )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed: structured_fields.entity_id" in str(error):
            raise ValueError(
                f"entity内にfield_key {checked_key!r} が既に存在します。"
            ) from error
        raise
    if cursor.lastrowid is None:
        raise RuntimeError("structured field IDを取得できませんでした。")
    return cursor.lastrowid


def get_structured_field(
    connection: sqlite3.Connection, field_id: object
) -> Optional[StructuredField]:
    """Return one decoded field, rejecting invalid stored JSON."""
    validated_id = _validate_positive_id("field_id", field_id)
    row = _field_row(connection, validated_id)
    return None if row is None else _row_to_field(row)


def list_structured_fields_for_entity(
    connection: sqlite3.Connection, entity_id: object
) -> Optional[list[StructuredField]]:
    """List decoded fields, or None for an unknown entity."""
    validated_id = _validate_positive_id("entity_id", entity_id)
    if _entity_row(connection, validated_id) is None:
        return None
    columns = ", ".join(_FIELD_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM structured_fields
        WHERE entity_id = ?
        ORDER BY id ASC
        """,
        (validated_id,),
    ).fetchall()
    return [_row_to_field(row) for row in rows]


def update_structured_field(
    connection: sqlite3.Connection,
    field_id: object,
    updates: Mapping[str, object],
) -> bool:
    """Update a field without permitting raw value_json input."""
    validated_id = _validate_positive_id("field_id", field_id)
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    allowed = {
        "field_key",
        "content_role",
        "value",
        "availability",
        "verification",
        "note",
    }
    invalid = set(updates) - allowed
    if invalid:
        raise ValueError(f"更新できない項目が指定されました: {sorted(invalid)!r}")
    row = _field_row(connection, validated_id)
    if row is None:
        return False
    entity = _entity_row(connection, row["entity_id"])
    if entity is None:
        raise StructuredDataError("structured fieldのowner entityが見つかりません。")
    current_value = (
        None
        if row["value_json"] is None
        else _decode_value(row["value_json"], row["id"])
    )
    candidate = {
        "field_key": row["field_key"],
        "content_role": row["content_role"],
        "value": current_value,
        "availability": row["availability"],
        "verification": row["verification"],
        "note": row["note"],
    }
    candidate.update(updates)
    (
        checked_key,
        checked_role,
        _,
        checked_availability,
        checked_verification,
        checked_note,
        value_json,
    ) = _validate_field_values(
        entity_type=entity["entity_type"], **candidate
    )
    _validate_entity_basis_roles(
        connection,
        entity_id=row["entity_id"],
        entity_type=entity["entity_type"],
        candidate_role=checked_role,
        exclude_field_id=validated_id,
    )
    values = {
        "field_key": checked_key,
        "content_role": checked_role,
        "value_json": value_json,
        "availability": checked_availability,
        "verification": checked_verification,
        "note": checked_note,
    }
    requested_columns = tuple(
        "value_json" if column == "value" else column for column in updates
    )
    columns = tuple(dict.fromkeys(requested_columns))
    assignments = ", ".join(f"{column} = ?" for column in columns)
    updated_at = _next_updated_at(row["updated_at"])
    try:
        with _write_scope(connection):
            cursor = connection.execute(
                f"""
                UPDATE structured_fields
                SET {assignments}, updated_at = ?
                WHERE id = ?
                """,
                tuple(values[column] for column in columns)
                + (updated_at, validated_id),
            )
    except sqlite3.IntegrityError as error:
        if "UNIQUE constraint failed: structured_fields.entity_id" in str(error):
            raise ValueError(
                f"entity内にfield_key {checked_key!r} が既に存在します。"
            ) from error
        raise
    return cursor.rowcount == 1


def delete_structured_field(
    connection: sqlite3.Connection, field_id: object
) -> bool:
    """Delete one field and its Evidence links."""
    validated_id = _validate_positive_id("field_id", field_id)
    with _write_scope(connection):
        cursor = connection.execute(
            "DELETE FROM structured_fields WHERE id = ?", (validated_id,)
        )
    return cursor.rowcount == 1


def _evidence_row(
    connection: sqlite3.Connection, evidence_id: int
) -> Optional[sqlite3.Row]:
    columns = ", ".join(_EVIDENCE_SELECT_COLUMNS)
    return connection.execute(
        f"SELECT {columns} FROM evidence_references WHERE id = ?",
        (evidence_id,),
    ).fetchone()


def _row_to_evidence(row: sqlite3.Row) -> EvidenceReference:
    return EvidenceReference(**dict(row))


def _validate_evidence_values(
    *,
    pdf_page: object,
    printed_page: object,
    section: object,
    subsection: object,
    table_label: object,
    figure_label: object,
    quote_text: object,
    note: object,
    verification: object,
) -> dict[str, object]:
    if pdf_page is not None and (
        isinstance(pdf_page, bool)
        or not isinstance(pdf_page, int)
        or pdf_page <= 0
    ):
        raise ValueError("pdf_pageはNoneまたは1以上の整数で指定してください。")
    values = {
        "pdf_page": pdf_page,
        "printed_page": _validate_optional_locator_text(
            "printed_page", printed_page
        ),
        "section": _validate_optional_locator_text("section", section),
        "subsection": _validate_optional_locator_text(
            "subsection", subsection
        ),
        "table_label": _validate_optional_locator_text(
            "table_label", table_label
        ),
        "figure_label": _validate_optional_locator_text(
            "figure_label", figure_label
        ),
        "quote_text": _validate_optional_locator_text(
            "quote_text", quote_text
        ),
        "note": _validate_optional_text("note", note),
        "verification": _validate_verification(verification),
    }
    locator_names = (
        "printed_page",
        "section",
        "subsection",
        "table_label",
        "figure_label",
        "quote_text",
    )
    if pdf_page is None and not any(
        isinstance(values[name], str) and values[name].strip()
        for name in locator_names
    ):
        raise ValueError("Evidenceにはnote以外のlocatorまたはquoteが必要です。")
    return values


def create_evidence_reference(
    connection: sqlite3.Connection,
    literature_id: object,
    *,
    pdf_page: object = None,
    printed_page: object = None,
    section: object = None,
    subsection: object = None,
    table_label: object = None,
    figure_label: object = None,
    quote_text: object = None,
    note: object = None,
    verification: object = "ai_unverified",
) -> int:
    """Create one validated Evidence row."""
    validated_literature_id = _validate_positive_id(
        "literature_id", literature_id
    )
    if not _literature_exists(connection, validated_literature_id):
        raise ValueError(
            f"Literature ID {validated_literature_id} は存在しません。"
        )
    values = _validate_evidence_values(
        pdf_page=pdf_page,
        printed_page=printed_page,
        section=section,
        subsection=subsection,
        table_label=table_label,
        figure_label=figure_label,
        quote_text=quote_text,
        note=note,
        verification=verification,
    )
    columns = tuple(values)
    with _write_scope(connection):
        cursor = connection.execute(
            f"""
            INSERT INTO evidence_references (
                literature_id, {', '.join(columns)}
            )
            VALUES (?, {', '.join('?' for _ in columns)})
            """,
            (validated_literature_id,)
            + tuple(values[column] for column in columns),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("Evidence IDを取得できませんでした。")
    return cursor.lastrowid


def get_evidence_reference(
    connection: sqlite3.Connection, evidence_id: object
) -> Optional[EvidenceReference]:
    """Return one Evidence reference, or None for an unknown ID."""
    validated_id = _validate_positive_id("evidence_id", evidence_id)
    row = _evidence_row(connection, validated_id)
    return None if row is None else _row_to_evidence(row)


def list_evidence_for_literature(
    connection: sqlite3.Connection, literature_id: object
) -> Optional[list[EvidenceReference]]:
    """List Evidence for one Literature, or None when it does not exist."""
    validated_id = _validate_positive_id("literature_id", literature_id)
    if not _literature_exists(connection, validated_id):
        return None
    columns = ", ".join(_EVIDENCE_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM evidence_references
        WHERE literature_id = ?
        ORDER BY id ASC
        """,
        (validated_id,),
    ).fetchall()
    return [_row_to_evidence(row) for row in rows]


def list_evidence_references_for_literature(
    connection: sqlite3.Connection, literature_id: object
) -> Optional[list[EvidenceReference]]:
    """Compatibility name matching EvidenceReference CRUD terminology."""
    return list_evidence_for_literature(connection, literature_id)


def update_evidence_reference(
    connection: sqlite3.Connection,
    evidence_id: object,
    updates: Mapping[str, object],
) -> bool:
    """Update Evidence locators, note, or verification."""
    validated_id = _validate_positive_id("evidence_id", evidence_id)
    if not updates:
        raise ValueError("更新対象を1項目以上指定してください。")
    allowed = {
        "pdf_page",
        "printed_page",
        "section",
        "subsection",
        "table_label",
        "figure_label",
        "quote_text",
        "note",
        "verification",
    }
    invalid = set(updates) - allowed
    if invalid:
        raise ValueError(f"更新できない項目が指定されました: {sorted(invalid)!r}")
    row = _evidence_row(connection, validated_id)
    if row is None:
        return False
    candidate = {name: row[name] for name in allowed}
    candidate.update(updates)
    values = _validate_evidence_values(**candidate)
    columns = tuple(updates)
    assignments = ", ".join(f"{column} = ?" for column in columns)
    updated_at = _next_updated_at(row["updated_at"])
    with _write_scope(connection):
        cursor = connection.execute(
            f"""
            UPDATE evidence_references
            SET {assignments}, updated_at = ?
            WHERE id = ?
            """,
            tuple(values[column] for column in columns)
            + (updated_at, validated_id),
        )
    return cursor.rowcount == 1


def delete_evidence_reference(
    connection: sqlite3.Connection, evidence_id: object
) -> bool:
    """Delete one Evidence row and only its link rows."""
    validated_id = _validate_positive_id("evidence_id", evidence_id)
    with _write_scope(connection):
        cursor = connection.execute(
            "DELETE FROM evidence_references WHERE id = ?", (validated_id,)
        )
    return cursor.rowcount == 1


def _link_owners(
    connection: sqlite3.Connection,
    *,
    owner_table: str,
    owner_id: int,
    evidence_id: int,
) -> tuple[int, int]:
    owner = connection.execute(
        f"SELECT literature_id FROM {owner_table} WHERE id = ?", (owner_id,)
    ).fetchone()
    if owner is None:
        raise ValueError(f"link owner ID {owner_id} は存在しません。")
    evidence = _evidence_row(connection, evidence_id)
    if evidence is None:
        raise ValueError(f"Evidence ID {evidence_id} は存在しません。")
    if owner["literature_id"] != evidence["literature_id"]:
        raise ValueError("別LiteratureのEvidenceはlinkできません。")
    return owner["literature_id"], evidence["literature_id"]


def _attach_evidence(
    connection: sqlite3.Connection,
    *,
    owner_table: str,
    link_table: str,
    owner_column: str,
    owner_id: object,
    evidence_id: object,
) -> bool:
    checked_owner_id = _validate_positive_id(owner_column, owner_id)
    checked_evidence_id = _validate_positive_id("evidence_id", evidence_id)
    literature_id, _ = _link_owners(
        connection,
        owner_table=owner_table,
        owner_id=checked_owner_id,
        evidence_id=checked_evidence_id,
    )
    with _write_scope(connection):
        cursor = connection.execute(
            f"""
            INSERT INTO {link_table} (literature_id, {owner_column}, evidence_id)
            VALUES (?, ?, ?)
            ON CONFLICT({owner_column}, evidence_id) DO NOTHING
            """,
            (literature_id, checked_owner_id, checked_evidence_id),
        )
    return cursor.rowcount == 1


def _detach_evidence(
    connection: sqlite3.Connection,
    *,
    link_table: str,
    owner_column: str,
    owner_id: object,
    evidence_id: object,
) -> bool:
    checked_owner_id = _validate_positive_id(owner_column, owner_id)
    checked_evidence_id = _validate_positive_id("evidence_id", evidence_id)
    with _write_scope(connection):
        cursor = connection.execute(
            f"""
            DELETE FROM {link_table}
            WHERE {owner_column} = ? AND evidence_id = ?
            """,
            (checked_owner_id, checked_evidence_id),
        )
    return cursor.rowcount == 1


def _list_linked_evidence(
    connection: sqlite3.Connection,
    *,
    owner_table: str,
    link_table: str,
    owner_column: str,
    owner_id: object,
) -> Optional[list[EvidenceReference]]:
    checked_owner_id = _validate_positive_id(owner_column, owner_id)
    if (
        connection.execute(
            f"SELECT 1 FROM {owner_table} WHERE id = ?", (checked_owner_id,)
        ).fetchone()
        is None
    ):
        return None
    columns = ", ".join(f"e.{name}" for name in _EVIDENCE_SELECT_COLUMNS)
    rows = connection.execute(
        f"""
        SELECT {columns}
        FROM {link_table} AS link
        JOIN evidence_references AS e ON e.id = link.evidence_id
        WHERE link.{owner_column} = ?
        ORDER BY e.id ASC
        """,
        (checked_owner_id,),
    ).fetchall()
    return [_row_to_evidence(row) for row in rows]


def attach_evidence_to_field(
    connection: sqlite3.Connection, field_id: object, evidence_id: object
) -> bool:
    return _attach_evidence(
        connection,
        owner_table="structured_fields",
        link_table="structured_field_evidence",
        owner_column="field_id",
        owner_id=field_id,
        evidence_id=evidence_id,
    )


def detach_evidence_from_field(
    connection: sqlite3.Connection, field_id: object, evidence_id: object
) -> bool:
    return _detach_evidence(
        connection,
        link_table="structured_field_evidence",
        owner_column="field_id",
        owner_id=field_id,
        evidence_id=evidence_id,
    )


def list_evidence_for_field(
    connection: sqlite3.Connection, field_id: object
) -> Optional[list[EvidenceReference]]:
    return _list_linked_evidence(
        connection,
        owner_table="structured_fields",
        link_table="structured_field_evidence",
        owner_column="field_id",
        owner_id=field_id,
    )


def attach_evidence_to_entity(
    connection: sqlite3.Connection, entity_id: object, evidence_id: object
) -> bool:
    return _attach_evidence(
        connection,
        owner_table="structured_entities",
        link_table="structured_entity_evidence",
        owner_column="entity_id",
        owner_id=entity_id,
        evidence_id=evidence_id,
    )


def detach_evidence_from_entity(
    connection: sqlite3.Connection, entity_id: object, evidence_id: object
) -> bool:
    return _detach_evidence(
        connection,
        link_table="structured_entity_evidence",
        owner_column="entity_id",
        owner_id=entity_id,
        evidence_id=evidence_id,
    )


def list_evidence_for_entity(
    connection: sqlite3.Connection, entity_id: object
) -> Optional[list[EvidenceReference]]:
    return _list_linked_evidence(
        connection,
        owner_table="structured_entities",
        link_table="structured_entity_evidence",
        owner_column="entity_id",
        owner_id=entity_id,
    )
