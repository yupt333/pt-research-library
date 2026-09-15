"""Read-only, user-assessed pairwise Outcome comparability profiles."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Optional

from src.comparison_matrix import (
    METHOD_GROUPS,
    OUTCOME_FIELD_ORDER,
    ComparisonLiterature,
    ComparisonMatrixError,
    ComparisonValue,
    OutcomeProfile,
    build_comparison_matrix,
    format_json_value,
    load_comparison_literature,
)
from src.repository import get_literature
from src.structured_repository import (
    list_structured_entities_for_literature,
    list_structured_fields_for_entity,
)


COMPARABILITY_STATUSES = (
    "directly comparable",
    "partially comparable",
    "not directly comparable",
    "needs review",
)
DEFAULT_COMPARABILITY_STATUS = "needs review"
OUTCOME_IDENTITY_FIELD_ORDER = (
    "name",
    "definition",
    "calculation_method",
    "unit",
)
OUTCOME_CONTEXT_FIELD_ORDER = (
    "context_condition",
    "context_group",
    "context_body_position",
    "context_task",
    "context_load",
    "context_region",
    "context_layer",
    "context_time_point",
)
COMPARABILITY_CAUTION = (
    "同じOutcome名・同じ単位・同じ数値でも、直接比較可能とは限りません。"
)
METHODS_CONTEXT_CAUTION = (
    "以下はLiterature全体のMethodsであり、"
    "選択Outcomeとの直接対応を示すものではありません。"
)
NON_PERSISTENCE_NOTICE = "この判断はDBへ保存されません。"


@dataclass(frozen=True)
class OutcomeChoiceValue:
    """One stored field used only to identify an Outcome choice."""

    content_role: str
    value: Any
    availability: Optional[str]

    @property
    def display_text(self) -> str:
        if self.content_role == "source_fact" and self.availability != "reported":
            return str(self.availability)
        return format_json_value(self.value, content_role=self.content_role)


@dataclass(frozen=True)
class OutcomeChoice:
    """One stable, 1-based Outcome selection without exposing its ID as the label."""

    selection_number: int
    entity_id: int
    sort_order: int
    fields: tuple[tuple[str, Optional[OutcomeChoiceValue]], ...]

    def field(self, field_key: str) -> Optional[OutcomeChoiceValue]:
        return dict(self.fields).get(field_key)

    @property
    def name_text(self) -> str:
        value = self.field("name")
        return "未登録" if value is None else value.display_text

    @property
    def definition_text(self) -> str:
        value = self.field("definition")
        return "未登録" if value is None else value.display_text

    @property
    def context_text(self) -> str:
        parts = tuple(
            f"{field_key}={value.display_text}"
            for field_key in OUTCOME_CONTEXT_FIELD_ORDER
            if (value := self.field(field_key)) is not None
        )
        return "未登録" if not parts else " / ".join(parts)


@dataclass(frozen=True)
class LiteratureOutcomeChoices:
    """One Literature and all of its Outcomes in repository selection order."""

    literature: ComparisonLiterature
    outcomes: tuple[OutcomeChoice, ...]


@dataclass(frozen=True)
class ComparabilityField:
    """One Outcome field shown independently on both sides."""

    field_key: str
    literature_a: Optional[ComparisonValue]
    literature_b: Optional[ComparisonValue]


@dataclass(frozen=True)
class MethodsComparabilityField:
    """All stored values for one Literature-level Methods field on each side."""

    field_key: str
    literature_a_values: tuple[ComparisonValue, ...]
    literature_b_values: tuple[ComparisonValue, ...]


@dataclass(frozen=True)
class MethodsContext:
    """One fixed Literature-level Methods group; no Outcome link is implied."""

    name: str
    entity_type: str
    fields: tuple[MethodsComparabilityField, ...]


@dataclass(frozen=True)
class OutcomeComparabilityProfile:
    """Facts needed for a researcher to assess one explicit Outcome pair."""

    literature_a: ComparisonLiterature
    outcome_a: OutcomeProfile
    literature_b: ComparisonLiterature
    outcome_b: OutcomeProfile
    identity: tuple[ComparabilityField, ...]
    context: tuple[ComparabilityField, ...]
    methods_context: tuple[MethodsContext, ...]
    validation_information: ComparabilityField

    @property
    def default_status(self) -> str:
        """Return the only system-provided state: an unreviewed marker."""
        return DEFAULT_COMPARABILITY_STATUS


@dataclass(frozen=True)
class ManualComparabilityAssessment:
    """A session-only user judgment; this object has no persistence operation."""

    status: str


def _comparison_literature(literature: object) -> ComparisonLiterature:
    literature_id = getattr(literature, "id", None)
    if literature_id is None:
        raise ComparisonMatrixError("Literature IDがありません。")
    return ComparisonLiterature(
        id=literature_id,
        title=literature.title,
        publication_year=literature.publication_year,
    )


def load_pairwise_literature(
    connection: sqlite3.Connection,
    literature_a_id: object,
    literature_b_id: object,
) -> tuple[ComparisonLiterature, ComparisonLiterature]:
    """Require exactly two existing, different Literature records."""
    literature = load_comparison_literature(
        connection, (literature_a_id, literature_b_id)
    )
    return literature[0], literature[1]


def _choice_value(field: object) -> OutcomeChoiceValue:
    return OutcomeChoiceValue(
        content_role=field.content_role,
        value=field.value,
        availability=field.availability,
    )


def list_outcome_choices(
    connection: sqlite3.Connection, literature_id: object
) -> LiteratureOutcomeChoices:
    """List all Outcomes for one Literature using stable 1-based ordinals."""
    literature = get_literature(connection, literature_id)
    if literature is None:
        raise ValueError(f"Literature ID {literature_id} は存在しません。")
    entities = list_structured_entities_for_literature(connection, literature_id)
    if entities is None:
        raise ComparisonMatrixError(
            f"Literature ID {literature_id} がOutcome読込中に存在しなくなりました。"
        )

    outcomes: list[OutcomeChoice] = []
    for entity in entities:
        if entity.entity_type != "outcome":
            continue
        if entity.id is None:
            raise ComparisonMatrixError("Outcome entity IDがありません。")
        fields = list_structured_fields_for_entity(connection, entity.id)
        if fields is None:
            raise ComparisonMatrixError(
                f"Outcome entity ID {entity.id} が読込中に存在しなくなりました。"
            )
        fields_by_key = {field.field_key: field for field in fields}
        outcomes.append(
            OutcomeChoice(
                selection_number=len(outcomes) + 1,
                entity_id=entity.id,
                sort_order=entity.sort_order,
                fields=tuple(
                    (
                        field_key,
                        None
                        if field_key not in fields_by_key
                        else _choice_value(fields_by_key[field_key]),
                    )
                    for field_key in OUTCOME_FIELD_ORDER
                ),
            )
        )
    return LiteratureOutcomeChoices(
        literature=_comparison_literature(literature),
        outcomes=tuple(outcomes),
    )


def select_outcome_choice(
    choices: object, selection_number: object
) -> OutcomeChoice:
    """Resolve one 1-based display number without using Outcome names."""
    if (
        isinstance(choices, (str, bytes, bytearray))
        or not isinstance(choices, Sequence)
    ):
        raise ValueError("Outcome choicesは順序付きsequenceで指定してください。")
    if (
        isinstance(selection_number, bool)
        or not isinstance(selection_number, int)
        or selection_number < 1
    ):
        raise ValueError("Outcome選択番号は1以上の整数で指定してください。")
    if selection_number > len(choices):
        raise ValueError("表示範囲外のOutcome選択番号が指定されました。")
    selected = choices[selection_number - 1]
    if not isinstance(selected, OutcomeChoice):
        raise ValueError("Outcome choicesに不正な値があります。")
    if selected.selection_number != selection_number:
        raise ValueError("Outcome choicesの選択番号が不整合です。")
    return selected


def _outcome_field(
    outcome: OutcomeProfile, field_key: str
) -> Optional[ComparisonValue]:
    field = next(
        (item for item in outcome.fields if item.field_key == field_key), None
    )
    if field is None:
        raise ComparisonMatrixError(
            f"Outcome profileにfield {field_key!r} がありません。"
        )
    return field.value


def _comparability_fields(
    outcome_a: OutcomeProfile,
    outcome_b: OutcomeProfile,
    field_order: tuple[str, ...],
) -> tuple[ComparabilityField, ...]:
    return tuple(
        ComparabilityField(
            field_key=field_key,
            literature_a=_outcome_field(outcome_a, field_key),
            literature_b=_outcome_field(outcome_b, field_key),
        )
        for field_key in field_order
    )


def _methods_context(matrix: object) -> tuple[MethodsContext, ...]:
    rows_by_group = {
        group.entity_type: {row.field_key: row for row in group.rows}
        for group in matrix.method_groups
    }
    contexts: list[MethodsContext] = []
    for group_name, entity_type, field_order in METHOD_GROUPS:
        rows = rows_by_group[entity_type]
        fields: list[MethodsComparabilityField] = []
        for field_key in field_order:
            row = rows.get(field_key)
            if row is None:
                side_values = ((), ())
            else:
                if len(row.cells) != 2:
                    raise ComparisonMatrixError(
                        "Outcome比較可能性profileのMethods列数が不正です。"
                    )
                side_values = (row.cells[0].values, row.cells[1].values)
            fields.append(
                MethodsComparabilityField(
                    field_key=field_key,
                    literature_a_values=side_values[0],
                    literature_b_values=side_values[1],
                )
            )
        contexts.append(MethodsContext(group_name, entity_type, tuple(fields)))
    return tuple(contexts)


def build_outcome_comparability_profile(
    connection: sqlite3.Connection,
    literature_a_id: object,
    outcome_a_selection: object,
    literature_b_id: object,
    outcome_b_selection: object,
) -> OutcomeComparabilityProfile:
    """Build a read-only profile for two explicitly selected Outcomes."""
    matrix = build_comparison_matrix(
        connection, (literature_a_id, literature_b_id)
    )
    side_a, side_b = matrix.outcomes_by_literature
    choice_a = select_outcome_choice(
        tuple(
            OutcomeChoice(number, outcome.entity_id, outcome.sort_order, ())
            for number, outcome in enumerate(side_a.outcomes, start=1)
        ),
        outcome_a_selection,
    )
    choice_b = select_outcome_choice(
        tuple(
            OutcomeChoice(number, outcome.entity_id, outcome.sort_order, ())
            for number, outcome in enumerate(side_b.outcomes, start=1)
        ),
        outcome_b_selection,
    )
    outcome_a = next(
        outcome for outcome in side_a.outcomes if outcome.entity_id == choice_a.entity_id
    )
    outcome_b = next(
        outcome for outcome in side_b.outcomes if outcome.entity_id == choice_b.entity_id
    )
    identity = _comparability_fields(
        outcome_a, outcome_b, OUTCOME_IDENTITY_FIELD_ORDER
    )
    context = _comparability_fields(
        outcome_a, outcome_b, OUTCOME_CONTEXT_FIELD_ORDER
    )
    validation_information = _comparability_fields(
        outcome_a, outcome_b, ("validation_information",)
    )[0]
    return OutcomeComparabilityProfile(
        literature_a=matrix.literature_columns[0],
        outcome_a=outcome_a,
        literature_b=matrix.literature_columns[1],
        outcome_b=outcome_b,
        identity=identity,
        context=context,
        methods_context=_methods_context(matrix),
        validation_information=validation_information,
    )


def validate_manual_comparability_status(
    status: object,
) -> ManualComparabilityAssessment:
    """Validate an explicit session-only assessment without storing it."""
    if not isinstance(status, str) or status not in COMPARABILITY_STATUSES:
        raise ValueError(
            "comparability statusはdirectly comparable、partially comparable、"
            "not directly comparable、needs reviewのいずれかで指定してください。"
        )
    return ManualComparabilityAssessment(status=status)
