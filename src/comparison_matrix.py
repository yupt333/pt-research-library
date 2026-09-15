"""Read-only construction of deterministic multi-Literature comparisons."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional

from src.models import Literature, StructuredEntity, StructuredField
from src.repository import get_literature
from src.structured_repository import (
    list_evidence_for_entity,
    list_evidence_for_field,
    list_structured_entities_for_literature,
    list_structured_fields_for_entity,
)


SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
UNREGISTERED_STATUS = "unregistered"
REGISTERED_STATUS = "registered"

STUDY_FIELD_ORDER = (
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
)

METHOD_GROUPS = (
    (
        "Measurement / Imaging",
        "method_measurement_imaging",
        (
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
        ),
    ),
    (
        "Body Condition",
        "method_body_condition",
        (
            "body_position",
            "joint_position",
            "joint_angle",
            "limb_position",
            "contraction_type",
            "muscle_activation_condition",
            "load",
            "weight_bearing_condition",
        ),
    ),
    (
        "Task / Protocol",
        "method_task_protocol",
        (
            "task",
            "movement",
            "range",
            "speed",
            "repetition",
            "duration",
            "rest",
            "trial_number",
        ),
    ),
    (
        "Analysis",
        "method_analysis",
        (
            "analysis_method",
            "tracking_algorithm",
            "preprocessing",
            "reference_frame_baseline",
            "calculation_method",
            "roi_handling",
            "quality_control",
        ),
    ),
    (
        "Validation / Statistics",
        "method_validation_statistics",
        (
            "validation",
            "reliability_method",
            "statistical_analysis",
            "icc",
            "sem",
            "mdc",
            "mcid",
            "agreement_analysis",
            "other_statistical_method",
        ),
    ),
)

OUTCOME_FIELD_ORDER = (
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
)

RESULT_FIELD_ORDER = (
    "condition_or_comparison",
    "result",
    "statistics",
)


class ComparisonMatrixError(RuntimeError):
    """Raised when a complete comparison cannot be built safely."""


@dataclass(frozen=True)
class ComparisonLiterature:
    """One comparison column, in the user-selected order."""

    id: int
    title: str
    publication_year: Optional[int]


@dataclass(frozen=True)
class ComparisonValue:
    """One stored structured field and its independent review metadata."""

    entity_id: int
    field_id: int
    content_role: str
    value: Any
    availability: Optional[str]
    verification: str
    evidence_count: int
    user_verified_evidence_count: int

    @property
    def status(self) -> str:
        if self.content_role == "source_fact":
            if self.availability is None:
                raise ComparisonMatrixError(
                    f"structured field ID {self.field_id} のavailabilityがありません。"
                )
            return self.availability
        return "interpretation"

    @property
    def display_text(self) -> str:
        if self.content_role == "source_fact" and self.availability != "reported":
            return str(self.availability)
        return format_json_value(self.value, content_role=self.content_role)


@dataclass(frozen=True)
class ComparisonCell:
    """All values for one Literature in one fixed comparison row."""

    literature: ComparisonLiterature
    values: tuple[ComparisonValue, ...]

    @property
    def status(self) -> str:
        return REGISTERED_STATUS if self.values else UNREGISTERED_STATUS


@dataclass(frozen=True)
class ComparisonRow:
    """One field row whose cells always follow the selected Literature order."""

    section: str
    subgroup: Optional[str]
    entity_type: str
    field_key: str
    cells: tuple[ComparisonCell, ...]


@dataclass(frozen=True)
class ComparisonMethodGroup:
    """One fixed Methods subgroup and its non-empty rows."""

    name: str
    entity_type: str
    rows: tuple[ComparisonRow, ...]


@dataclass(frozen=True)
class OutcomeField:
    """One fixed Outcome or Result field, including an unregistered state."""

    field_key: str
    value: Optional[ComparisonValue]


@dataclass(frozen=True)
class ResultProfile:
    """One stored child Result, kept separate from every other Result."""

    entity_id: int
    sort_order: int
    entity_verification: str
    evidence_count: int
    user_verified_evidence_count: int
    fields: tuple[OutcomeField, ...]


@dataclass(frozen=True)
class OutcomeProfile:
    """One stored logical Outcome; names are never used as merge keys."""

    entity_id: int
    sort_order: int
    entity_verification: str
    evidence_count: int
    user_verified_evidence_count: int
    fields: tuple[OutcomeField, ...]
    results: tuple[ResultProfile, ...]


@dataclass(frozen=True)
class LiteratureOutcomes:
    """All logical Outcomes for one selected Literature."""

    literature: ComparisonLiterature
    outcomes: tuple[OutcomeProfile, ...]


@dataclass(frozen=True)
class ComparisonMatrix:
    """Complete semantic comparison read model."""

    literature_columns: tuple[ComparisonLiterature, ...]
    study_rows: tuple[ComparisonRow, ...]
    method_groups: tuple[ComparisonMethodGroup, ...]
    outcomes_by_literature: tuple[LiteratureOutcomes, ...]


def format_json_value(value: object, *, content_role: str) -> str:
    """Render one decoded JSON value without normalization or inference."""
    if value is None and content_role == "interpretation":
        return "null（解釈値）"
    if isinstance(value, str):
        return value
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(", ", ": "),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ComparisonMatrixError(
            "structured field valueをdeterministic JSONとして表示できません。"
        ) from error


def _validate_ordered_ids(
    values: object,
    *,
    item_name: str,
    minimum_count: int,
) -> tuple[int, ...]:
    if (
        isinstance(values, (str, bytes, bytearray))
        or not isinstance(values, Sequence)
    ):
        raise ValueError(f"{item_name}は順序付きsequenceで指定してください。")
    ids = tuple(values)
    if len(ids) < minimum_count:
        raise ValueError(f"比較には{item_name}を2件以上指定してください。")
    checked: list[int] = []
    seen: set[int] = set()
    for value in ids:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= SQLITE_MAX_INTEGER
        ):
            raise ValueError(
                f"{item_name}は1〜{SQLITE_MAX_INTEGER}の整数で指定してください。"
            )
        if value in seen:
            raise ValueError(f"同じ{item_name}を重複指定できません。")
        seen.add(value)
        checked.append(value)
    return tuple(checked)


def parse_literature_id_input(raw_value: object) -> tuple[int, ...]:
    """Parse a strict comma-separated manual Literature ID selection."""
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError("Literature IDを2件以上入力してください。")
    parts = raw_value.strip().split(",")
    ids: list[int] = []
    for part in parts:
        token = part.strip()
        if not token or not all("0" <= character <= "9" for character in token):
            raise ValueError(
                "Literature IDはASCII数字とカンマだけで入力してください。"
            )
        ids.append(int(token))
    return _validate_ordered_ids(
        ids,
        item_name="Literature ID",
        minimum_count=2,
    )


def select_search_result_ids(
    search_result_ids: object,
    raw_selection: object,
) -> tuple[int, ...]:
    """Map display selection numbers to the last search's Literature IDs."""
    if (
        isinstance(search_result_ids, (str, bytes, bytearray))
        or not isinstance(search_result_ids, Sequence)
    ):
        raise ValueError("直前の検索結果がありません。")
    available_ids = tuple(search_result_ids)
    if not available_ids:
        raise ValueError("直前の検索結果がありません。")
    if not isinstance(raw_selection, str) or not raw_selection.strip():
        raise ValueError("比較する選択番号を2件以上入力してください。")

    if raw_selection.strip() == "all":
        selection_numbers = tuple(range(1, len(available_ids) + 1))
    else:
        parts = raw_selection.strip().split(",")
        parsed: list[int] = []
        for part in parts:
            token = part.strip()
            if not token or not all(
                "0" <= character <= "9" for character in token
            ):
                raise ValueError(
                    "選択番号はall、またはASCII数字とカンマで入力してください。"
                )
            parsed.append(int(token))
        selection_numbers = _validate_ordered_ids(
            parsed,
            item_name="選択番号",
            minimum_count=2,
        )

    if len(selection_numbers) < 2:
        raise ValueError("比較には選択番号を2件以上指定してください。")
    if len(set(available_ids)) != len(available_ids):
        raise ValueError("直前の検索結果に重複Literatureがあります。")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= SQLITE_MAX_INTEGER
        for value in available_ids
    ):
        raise ValueError("直前の検索結果に不正なLiterature IDがあります。")
    if any(number > len(available_ids) for number in selection_numbers):
        raise ValueError("表示範囲外の選択番号が指定されました。")
    selected = tuple(available_ids[number - 1] for number in selection_numbers)
    return _validate_ordered_ids(
        selected,
        item_name="Literature ID",
        minimum_count=2,
    )


def _comparison_literature(literature: Literature) -> ComparisonLiterature:
    if literature.id is None:
        raise ComparisonMatrixError("Literature IDがありません。")
    return ComparisonLiterature(
        id=literature.id,
        title=literature.title,
        publication_year=literature.publication_year,
    )


def load_comparison_literature(
    connection: sqlite3.Connection,
    literature_ids: object,
) -> tuple[ComparisonLiterature, ...]:
    """Validate and load all comparison columns before matrix construction."""
    checked_ids = _validate_ordered_ids(
        literature_ids,
        item_name="Literature ID",
        minimum_count=2,
    )
    literature_records: list[Literature] = []
    for literature_id in checked_ids:
        literature = get_literature(connection, literature_id)
        if literature is None:
            raise ValueError(f"Literature ID {literature_id} は存在しません。")
        literature_records.append(literature)
    return tuple(_comparison_literature(item) for item in literature_records)


def _required_entity_id(entity: StructuredEntity) -> int:
    if entity.id is None:
        raise ComparisonMatrixError("structured entity IDがありません。")
    return entity.id


def _required_field_id(field: StructuredField) -> int:
    if field.id is None:
        raise ComparisonMatrixError("structured field IDがありません。")
    return field.id


def _evidence_counts(evidence: object, *, owner_description: str) -> tuple[int, int]:
    if evidence is None:
        raise ComparisonMatrixError(
            f"{owner_description}が比較読込中に存在しなくなりました。"
        )
    items = tuple(evidence)
    return (
        len(items),
        sum(item.verification == "user_verified" for item in items),
    )


def _comparison_value(
    connection: sqlite3.Connection, field: StructuredField
) -> ComparisonValue:
    field_id = _required_field_id(field)
    evidence_count, user_verified_count = _evidence_counts(
        list_evidence_for_field(connection, field_id),
        owner_description=f"structured field ID {field_id}",
    )
    return ComparisonValue(
        entity_id=field.entity_id,
        field_id=field_id,
        content_role=field.content_role,
        value=field.value,
        availability=field.availability,
        verification=field.verification,
        evidence_count=evidence_count,
        user_verified_evidence_count=user_verified_count,
    )


def _load_structured_data(
    connection: sqlite3.Connection,
    literature: ComparisonLiterature,
) -> tuple[
    tuple[StructuredEntity, ...],
    dict[int, tuple[StructuredField, ...]],
]:
    entities = list_structured_entities_for_literature(connection, literature.id)
    if entities is None:
        raise ComparisonMatrixError(
            f"Literature ID {literature.id} が比較読込中に存在しなくなりました。"
        )
    fields_by_entity: dict[int, tuple[StructuredField, ...]] = {}
    for entity in entities:
        entity_id = _required_entity_id(entity)
        fields = list_structured_fields_for_entity(connection, entity_id)
        if fields is None:
            raise ComparisonMatrixError(
                f"structured entity ID {entity_id} が比較読込中に存在しなくなりました。"
            )
        fields_by_entity[entity_id] = tuple(fields)
    return tuple(entities), fields_by_entity


def _build_rows(
    connection: sqlite3.Connection,
    *,
    section: str,
    subgroup: Optional[str],
    entity_type: str,
    field_order: tuple[str, ...],
    literature_columns: tuple[ComparisonLiterature, ...],
    entities_by_literature: dict[int, tuple[StructuredEntity, ...]],
    fields_by_literature: dict[int, dict[int, tuple[StructuredField, ...]]],
) -> tuple[ComparisonRow, ...]:
    rows: list[ComparisonRow] = []
    for field_key in field_order:
        cells: list[ComparisonCell] = []
        for literature in literature_columns:
            values: list[ComparisonValue] = []
            for entity in entities_by_literature[literature.id]:
                if entity.entity_type != entity_type:
                    continue
                entity_id = _required_entity_id(entity)
                for field in fields_by_literature[literature.id][entity_id]:
                    if field.field_key == field_key:
                        values.append(_comparison_value(connection, field))
            cells.append(ComparisonCell(literature, tuple(values)))
        if any(cell.values for cell in cells):
            rows.append(
                ComparisonRow(
                    section=section,
                    subgroup=subgroup,
                    entity_type=entity_type,
                    field_key=field_key,
                    cells=tuple(cells),
                )
            )
    return tuple(rows)


def _profile_fields(
    connection: sqlite3.Connection,
    fields: tuple[StructuredField, ...],
    field_order: tuple[str, ...],
) -> tuple[OutcomeField, ...]:
    by_key = {field.field_key: field for field in fields}
    return tuple(
        OutcomeField(
            field_key=field_key,
            value=(
                None
                if field_key not in by_key
                else _comparison_value(connection, by_key[field_key])
            ),
        )
        for field_key in field_order
    )


def _entity_evidence_counts(
    connection: sqlite3.Connection, entity_id: int
) -> tuple[int, int]:
    return _evidence_counts(
        list_evidence_for_entity(connection, entity_id),
        owner_description=f"structured entity ID {entity_id}",
    )


def _build_outcomes(
    connection: sqlite3.Connection,
    literature_columns: tuple[ComparisonLiterature, ...],
    entities_by_literature: dict[int, tuple[StructuredEntity, ...]],
    fields_by_literature: dict[int, dict[int, tuple[StructuredField, ...]]],
) -> tuple[LiteratureOutcomes, ...]:
    grouped: list[LiteratureOutcomes] = []
    for literature in literature_columns:
        entities = entities_by_literature[literature.id]
        outcome_profiles: list[OutcomeProfile] = []
        for outcome in entities:
            if outcome.entity_type != "outcome":
                continue
            outcome_id = _required_entity_id(outcome)
            outcome_evidence = _entity_evidence_counts(connection, outcome_id)
            results: list[ResultProfile] = []
            for result in entities:
                if (
                    result.entity_type != "result"
                    or result.parent_entity_id != outcome_id
                ):
                    continue
                result_id = _required_entity_id(result)
                result_evidence = _entity_evidence_counts(connection, result_id)
                results.append(
                    ResultProfile(
                        entity_id=result_id,
                        sort_order=result.sort_order,
                        entity_verification=result.verification,
                        evidence_count=result_evidence[0],
                        user_verified_evidence_count=result_evidence[1],
                        fields=_profile_fields(
                            connection,
                            fields_by_literature[literature.id][result_id],
                            RESULT_FIELD_ORDER,
                        ),
                    )
                )
            outcome_profiles.append(
                OutcomeProfile(
                    entity_id=outcome_id,
                    sort_order=outcome.sort_order,
                    entity_verification=outcome.verification,
                    evidence_count=outcome_evidence[0],
                    user_verified_evidence_count=outcome_evidence[1],
                    fields=_profile_fields(
                        connection,
                        fields_by_literature[literature.id][outcome_id],
                        OUTCOME_FIELD_ORDER,
                    ),
                    results=tuple(results),
                )
            )
        grouped.append(LiteratureOutcomes(literature, tuple(outcome_profiles)))
    return tuple(grouped)


def build_comparison_matrix(
    connection: sqlite3.Connection,
    literature_ids: object,
) -> ComparisonMatrix:
    """Build a complete read-only matrix without changing caller transaction state."""
    literature_columns = load_comparison_literature(connection, literature_ids)
    entities_by_literature: dict[int, tuple[StructuredEntity, ...]] = {}
    fields_by_literature: dict[int, dict[int, tuple[StructuredField, ...]]] = {}
    for literature in literature_columns:
        entities, fields = _load_structured_data(connection, literature)
        entities_by_literature[literature.id] = entities
        fields_by_literature[literature.id] = fields

    study_rows = _build_rows(
        connection,
        section="Study",
        subgroup=None,
        entity_type="study",
        field_order=STUDY_FIELD_ORDER,
        literature_columns=literature_columns,
        entities_by_literature=entities_by_literature,
        fields_by_literature=fields_by_literature,
    )
    method_groups = tuple(
        ComparisonMethodGroup(
            name=group_name,
            entity_type=entity_type,
            rows=_build_rows(
                connection,
                section="Methods",
                subgroup=group_name,
                entity_type=entity_type,
                field_order=field_order,
                literature_columns=literature_columns,
                entities_by_literature=entities_by_literature,
                fields_by_literature=fields_by_literature,
            ),
        )
        for group_name, entity_type, field_order in METHOD_GROUPS
    )
    outcomes = _build_outcomes(
        connection,
        literature_columns,
        entities_by_literature,
        fields_by_literature,
    )
    return ComparisonMatrix(
        literature_columns=literature_columns,
        study_rows=study_rows,
        method_groups=method_groups,
        outcomes_by_literature=outcomes,
    )
