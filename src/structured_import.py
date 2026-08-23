"""Strict Contract v1 parsing, read-only preview, and atomic import save."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from src import structured_repository
from src.duplicates import (
    TITLE_SIMILARITY_THRESHOLD,
    calculate_title_similarity,
    normalize_doi,
    normalize_pmid,
)
from src.models import Literature
from src.repository import get_literature


CONTRACT_VERSION = "pt_research_library_structured_import_v1"

TOP_LEVEL_KEYS = frozenset(
    {
        "contract_version",
        "analysis_metadata",
        "bibliography",
        "study",
        "methods",
        "outcomes",
        "limitations",
        "concepts",
        "research_relevance",
        "evidence",
    }
)
ANALYSIS_METADATA_KEYS = frozenset(
    {"source_document_name", "analysis_scope", "analysis_warnings"}
)
BIBLIOGRAPHY_KEYS = frozenset(
    {
        "title",
        "authors",
        "journal",
        "publication_year",
        "volume",
        "issue",
        "pages",
        "doi",
        "pmid",
        "url",
        "language",
        "publication_type",
        "abstract",
    }
)
STUDY_KEYS = structured_repository.ENTITY_FIELD_VOCABULARY["study"]
METHOD_GROUPS = {
    "measurement_imaging": "method_measurement_imaging",
    "body_condition": "method_body_condition",
    "task_protocol": "method_task_protocol",
    "analysis": "method_analysis",
    "validation_statistics": "method_validation_statistics",
}
METHOD_KEYS = {
    group: structured_repository.ENTITY_FIELD_VOCABULARY[entity_type]
    for group, entity_type in METHOD_GROUPS.items()
}
OUTCOME_CONTEXT_KEYS = frozenset(
    {
        "condition",
        "group",
        "body_position",
        "task",
        "load",
        "region",
        "layer",
        "time_point",
    }
)
RESEARCH_RELEVANCE_KEYS = structured_repository.ENTITY_FIELD_VOCABULARY[
    "research_relevance"
]
FACT_KEYS = frozenset(
    {"value", "availability", "verification", "evidence_refs", "note"}
)
INTERPRETIVE_KEYS = frozenset(
    {"value", "verification", "evidence_refs", "note"}
)
METRIC_KEYS = frozenset(
    {
        "metric_name",
        "metric_value",
        "unit",
        "model_definition",
        "confidence_interval",
        "condition",
        "verification",
        "evidence_refs",
    }
)
EVIDENCE_LOCATOR_KEYS = (
    "pdf_page",
    "printed_page",
    "section",
    "subsection",
    "table",
    "figure",
    "quote_text",
)


class StructuredImportValidationError(ValueError):
    """A path-aware rejection of unsafe Contract v1 input."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


class _DuplicateObjectKeyError(ValueError):
    pass


@dataclass(frozen=True)
class StructuredImportPayload:
    """A fully validated Contract v1 payload."""

    data: dict[str, Any]
    canonical_json: str


@dataclass(frozen=True)
class PlannedField:
    field_key: str
    content_role: str
    value: Any
    availability: Optional[str]
    verification: str
    note: Optional[str]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class PlannedEntity:
    token: str
    entity_type: str
    parent_token: Optional[str]
    sort_order: int
    verification: str
    fields: tuple[PlannedField, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class PlannedEvidence:
    evidence_id: str
    pdf_page: Optional[int]
    printed_page: Optional[str]
    section: Optional[str]
    subsection: Optional[str]
    table_label: Optional[str]
    figure_label: Optional[str]
    quote_text: Optional[str]
    note: Optional[str]
    verification: str
    locator_availability: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ImportPlan:
    entities: tuple[PlannedEntity, ...]
    evidence: tuple[PlannedEvidence, ...]


@dataclass(frozen=True)
class ImportPreview:
    literature_id: int
    target_literature: Literature
    target_identity_at_preview: tuple[str, Optional[str], Optional[str]]
    payload: StructuredImportPayload
    plan: ImportPlan
    source_document_name: Optional[str]
    analysis_scope: str
    payload_title: Optional[str]
    payload_doi: Optional[str]
    payload_pmid: Optional[str]
    title_similarity: Optional[float]
    doi_state: str
    pmid_state: str
    warnings: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    planned_counts: dict[str, int]
    evidence_locator_availability: tuple[
        tuple[str, tuple[tuple[str, str], ...]], ...
    ]

    @property
    def blocked(self) -> bool:
        return bool(self.blocking_reasons)


@dataclass(frozen=True)
class ImportSaveResult:
    literature_id: int
    planned_counts: dict[str, int]


def _error(path: str, reason: str) -> None:
    raise StructuredImportValidationError(path, reason)


def _object_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateObjectKeyError(key)
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> Any:
    raise StructuredImportValidationError(
        "$", f"non-finite number {value} は許可されていません"
    )


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(path, "objectでなければなりません")
    return value


def _require_array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _error(path, "arrayでなければなりません")
    return value


def _require_exact_keys(
    value: dict[str, Any],
    path: str,
    *,
    required: frozenset[str] = frozenset(),
    allowed: Optional[frozenset[str]] = None,
) -> None:
    permitted = required if allowed is None else allowed
    missing = sorted(required - value.keys())
    if missing:
        _error(path, f"必須fieldがありません: {', '.join(missing)}")
    unknown = sorted(value.keys() - permitted)
    if unknown:
        unknown_path = f"{path}.{unknown[0]}" if path != "$" else f"$.{unknown[0]}"
        _error(unknown_path, "v1で許可されていないfieldです")


def _path_for(parent: str, key: str) -> str:
    return key if parent == "$" else f"{parent}.{key}"


def _reject_user_verified(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = _path_for(path, key)
            if key == "verification" and item == "user_verified":
                _error(
                    item_path,
                    "raw ChatGPT JSONではuser_verifiedを使用できません",
                )
            _reject_user_verified(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_user_verified(item, f"{path}[{index}]")


def _validate_ai_verification(value: Any, path: str) -> None:
    if value != "ai_unverified":
        _error(path, "ai_unverifiedのみ許可されています")


def _validate_evidence_refs(
    value: Any,
    path: str,
    references: list[tuple[str, str]],
    *,
    require_empty: bool = False,
) -> tuple[str, ...]:
    items = _require_array(value, path)
    refs: list[str] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            _error(item_path, "non-empty stringでなければなりません")
        refs.append(item)
        references.append((item_path, item))
    if require_empty and refs:
        _error(path, "Evidence locator内のevidence_refsはempty array必須です")
    return tuple(refs)


def _validate_fact(
    value: Any,
    path: str,
    references: list[tuple[str, str]],
    *,
    evidence_locator: bool = False,
) -> dict[str, Any]:
    fact = _require_object(value, path)
    _require_exact_keys(fact, path, required=FACT_KEYS)
    availability = fact["availability"]
    if (
        not isinstance(availability, str)
        or availability not in structured_repository.AVAILABILITIES
    ):
        _error(f"{path}.availability", "許可されていない値です")
    if availability == "reported" and fact["value"] is None:
        _error(f"{path}.value", "reportedではnon-null valueが必要です")
    if availability != "reported" and fact["value"] is not None:
        _error(
            f"{path}.value",
            "reported以外ではvalueはnullでなければなりません",
        )
    _validate_ai_verification(fact["verification"], f"{path}.verification")
    _validate_evidence_refs(
        fact["evidence_refs"],
        f"{path}.evidence_refs",
        references,
        require_empty=evidence_locator,
    )
    if fact["note"] is not None and not isinstance(fact["note"], str):
        _error(f"{path}.note", "stringまたはnullでなければなりません")
    return fact


def _validate_interpretive(
    value: Any, path: str, references: list[tuple[str, str]]
) -> dict[str, Any]:
    item = _require_object(value, path)
    _require_exact_keys(item, path, required=INTERPRETIVE_KEYS)
    _validate_ai_verification(item["verification"], f"{path}.verification")
    _validate_evidence_refs(
        item["evidence_refs"], f"{path}.evidence_refs", references
    )
    if item["note"] is not None and not isinstance(item["note"], str):
        _error(f"{path}.note", "stringまたはnullでなければなりません")
    return item


def _validate_reported_string(
    fact: dict[str, Any], path: str, *, nonempty: bool = False
) -> None:
    if fact["availability"] != "reported":
        return
    value = fact["value"]
    if not isinstance(value, str):
        _error(f"{path}.value", "reported valueはstringでなければなりません")
    if nonempty and not value.strip():
        _error(f"{path}.value", "non-empty stringでなければなりません")


def _validate_bibliography(
    value: Any, references: list[tuple[str, str]]
) -> None:
    bibliography = _require_object(value, "bibliography")
    _require_exact_keys(bibliography, "bibliography", required=BIBLIOGRAPHY_KEYS)
    facts = {
        key: _validate_fact(
            bibliography[key], f"bibliography.{key}", references
        )
        for key in BIBLIOGRAPHY_KEYS
    }
    _validate_reported_string(facts["title"], "bibliography.title", nonempty=True)
    authors = facts["authors"]
    if authors["availability"] == "reported":
        author_value = authors["value"]
        if isinstance(author_value, str):
            pass
        elif isinstance(author_value, list) and all(
            isinstance(item, str) for item in author_value
        ):
            pass
        else:
            _error(
                "bibliography.authors.value",
                "stringまたはstring arrayでなければなりません",
            )
    for key in BIBLIOGRAPHY_KEYS - {"authors", "publication_year"}:
        _validate_reported_string(
            facts[key], f"bibliography.{key}", nonempty=key in {"doi", "pmid"}
        )
    year = facts["publication_year"]
    if year["availability"] == "reported":
        year_value = year["value"]
        if (
            isinstance(year_value, bool)
            or not isinstance(year_value, int)
            or not 1800 <= year_value <= date.today().year + 1
        ):
            _error(
                "bibliography.publication_year.value",
                f"1800〜{date.today().year + 1}の整数でなければなりません",
            )
    doi = facts["doi"]
    if doi["availability"] == "reported" and normalize_doi(doi["value"]) is None:
        _error("bibliography.doi.value", "空のDOIはreportedにできません")
    pmid = facts["pmid"]
    if pmid["availability"] == "reported":
        try:
            normalized_pmid = normalize_pmid(pmid["value"])
        except ValueError as error:
            _error("bibliography.pmid.value", str(error))
        if normalized_pmid is None:
            _error("bibliography.pmid.value", "空のPMIDはreportedにできません")


def _validate_fact_object_fields(
    value: Any,
    path: str,
    allowed: frozenset[str],
    references: list[tuple[str, str]],
) -> None:
    obj = _require_object(value, path)
    _require_exact_keys(obj, path, allowed=allowed)
    for key, item in obj.items():
        _validate_fact(item, f"{path}.{key}", references)


def _validate_metric(
    value: Any, path: str, references: list[tuple[str, str]]
) -> None:
    metric = _require_object(value, path)
    _require_exact_keys(metric, path, required=METRIC_KEYS)
    for key in (
        "metric_name",
        "metric_value",
        "unit",
        "model_definition",
        "confidence_interval",
        "condition",
    ):
        _validate_fact(metric[key], f"{path}.{key}", references)
    _validate_ai_verification(metric["verification"], f"{path}.verification")
    _validate_evidence_refs(
        metric["evidence_refs"], f"{path}.evidence_refs", references
    )


def _validate_outcomes(
    value: Any,
    references: list[tuple[str, str]],
    identifiers: dict[str, set[str]],
) -> None:
    outcomes = _require_array(value, "outcomes")
    required = frozenset(
        {
            "outcome_id",
            "name",
            "definition",
            "calculation_method",
            "unit",
            "context",
            "results",
            "validation_information",
            "verification",
            "evidence_refs",
        }
    )
    result_required = frozenset(
        {
            "result_id",
            "condition_or_comparison",
            "result",
            "statistics",
            "verification",
            "evidence_refs",
        }
    )
    for outcome_index, value in enumerate(outcomes):
        path = f"outcomes[{outcome_index}]"
        outcome = _require_object(value, path)
        _require_exact_keys(outcome, path, required=required)
        _validate_local_id(
            outcome["outcome_id"], f"{path}.outcome_id", identifiers["outcome"]
        )
        for key in ("name", "definition", "calculation_method", "unit"):
            fact = _validate_fact(outcome[key], f"{path}.{key}", references)
            if key == "name":
                _validate_reported_string(fact, f"{path}.name", nonempty=True)
        _validate_fact_object_fields(
            outcome["context"], f"{path}.context", OUTCOME_CONTEXT_KEYS, references
        )
        _validate_ai_verification(
            outcome["verification"], f"{path}.verification"
        )
        _validate_evidence_refs(
            outcome["evidence_refs"], f"{path}.evidence_refs", references
        )
        metrics = _require_array(
            outcome["validation_information"], f"{path}.validation_information"
        )
        for metric_index, metric in enumerate(metrics):
            _validate_metric(
                metric,
                f"{path}.validation_information[{metric_index}]",
                references,
            )
        results = _require_array(outcome["results"], f"{path}.results")
        for result_index, result_value in enumerate(results):
            result_path = f"{path}.results[{result_index}]"
            result = _require_object(result_value, result_path)
            _require_exact_keys(result, result_path, required=result_required)
            _validate_local_id(
                result["result_id"],
                f"{result_path}.result_id",
                identifiers["result"],
            )
            for key in ("condition_or_comparison", "result"):
                _validate_fact(result[key], f"{result_path}.{key}", references)
            statistics = _require_array(
                result["statistics"], f"{result_path}.statistics"
            )
            for metric_index, metric in enumerate(statistics):
                _validate_metric(
                    metric,
                    f"{result_path}.statistics[{metric_index}]",
                    references,
                )
            _validate_ai_verification(
                result["verification"], f"{result_path}.verification"
            )
            _validate_evidence_refs(
                result["evidence_refs"],
                f"{result_path}.evidence_refs",
                references,
            )


def _validate_local_id(value: Any, path: str, seen: set[str]) -> None:
    if not isinstance(value, str) or not value:
        _error(path, "non-empty stringでなければなりません")
    if value in seen:
        _error(path, f"payload-local ID {value!r} が重複しています")
    seen.add(value)


def _validate_limitations_and_concepts(
    data: dict[str, Any],
    references: list[tuple[str, str]],
    identifiers: dict[str, set[str]],
) -> None:
    limitation_keys = frozenset(
        {"limitation_id", "text", "basis", "verification", "evidence_refs", "note"}
    )
    for index, value in enumerate(_require_array(data["limitations"], "limitations")):
        path = f"limitations[{index}]"
        item = _require_object(value, path)
        _require_exact_keys(item, path, required=limitation_keys)
        _validate_local_id(
            item["limitation_id"], f"{path}.limitation_id", identifiers["limitation"]
        )
        if not isinstance(item["text"], str) or not item["text"].strip():
            _error(f"{path}.text", "non-empty stringでなければなりません")
        if not isinstance(item["basis"], str) or item["basis"] not in {
            "author_reported",
            "ai_inferred",
        }:
            _error(f"{path}.basis", "許可されていない値です")
        _validate_ai_verification(item["verification"], f"{path}.verification")
        _validate_evidence_refs(
            item["evidence_refs"], f"{path}.evidence_refs", references
        )
        if item["note"] is not None and not isinstance(item["note"], str):
            _error(f"{path}.note", "stringまたはnullでなければなりません")

    concept_keys = frozenset(
        {"concept_id", "name", "context", "basis", "verification", "evidence_refs", "note"}
    )
    for index, value in enumerate(_require_array(data["concepts"], "concepts")):
        path = f"concepts[{index}]"
        item = _require_object(value, path)
        _require_exact_keys(item, path, required=concept_keys)
        _validate_local_id(
            item["concept_id"], f"{path}.concept_id", identifiers["concept"]
        )
        if not isinstance(item["name"], str) or not item["name"].strip():
            _error(f"{path}.name", "non-empty stringでなければなりません")
        if not isinstance(item["context"], (str, dict, list, type(None))):
            _error(f"{path}.context", "string、object、array、nullのみ許可されます")
        if not isinstance(item["basis"], str) or item["basis"] not in {
            "source_reported",
            "ai_inferred",
        }:
            _error(f"{path}.basis", "許可されていない値です")
        _validate_ai_verification(item["verification"], f"{path}.verification")
        _validate_evidence_refs(
            item["evidence_refs"], f"{path}.evidence_refs", references
        )
        if item["note"] is not None and not isinstance(item["note"], str):
            _error(f"{path}.note", "stringまたはnullでなければなりません")


def _validate_evidence(
    value: Any,
    references: list[tuple[str, str]],
    identifiers: dict[str, set[str]],
) -> None:
    evidence_array = _require_array(value, "evidence")
    required = frozenset(
        {"evidence_id", *EVIDENCE_LOCATOR_KEYS, "note", "verification"}
    )
    for index, value in enumerate(evidence_array):
        path = f"evidence[{index}]"
        item = _require_object(value, path)
        _require_exact_keys(item, path, required=required)
        _validate_local_id(
            item["evidence_id"], f"{path}.evidence_id", identifiers["evidence"]
        )
        reported_count = 0
        for locator in EVIDENCE_LOCATOR_KEYS:
            fact = _validate_fact(
                item[locator],
                f"{path}.{locator}",
                references,
                evidence_locator=True,
            )
            if fact["availability"] == "reported":
                reported_count += 1
                locator_value = fact["value"]
                if locator == "pdf_page":
                    if (
                        isinstance(locator_value, bool)
                        or not isinstance(locator_value, int)
                        or locator_value <= 0
                    ):
                        _error(
                            f"{path}.pdf_page.value",
                            "1以上の整数でなければなりません",
                        )
                elif not isinstance(locator_value, str) or not locator_value.strip():
                    _error(
                        f"{path}.{locator}.value",
                        "non-empty stringでなければなりません",
                    )
        if reported_count == 0:
            _error(path, "reported locatorまたはquoteを最低1つ必要とします")
        if item["note"] is not None and not isinstance(item["note"], str):
            _error(f"{path}.note", "stringまたはnullでなければなりません")
        _validate_ai_verification(item["verification"], f"{path}.verification")


def parse_structured_import(text: object) -> StructuredImportPayload:
    """Parse and fully validate JSON-only Contract v1 text."""
    if not isinstance(text, str):
        _error("$", "JSON inputはstringでなければなりません")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_object_pairs_hook,
            parse_constant=_reject_nonfinite,
        )
    except _DuplicateObjectKeyError as error:
        _error("$", f"duplicate object keyは許可されません: {error}")
    except StructuredImportValidationError:
        raise
    except json.JSONDecodeError as error:
        _error("$", f"invalid JSONです（line {error.lineno}, column {error.colno}）")

    top = _require_object(data, "$")
    _reject_user_verified(top)
    _require_exact_keys(top, "$", required=TOP_LEVEL_KEYS)
    if top["contract_version"] != CONTRACT_VERSION:
        _error(
            "contract_version",
            f"正式version {CONTRACT_VERSION!r} のみ受け付けます",
        )

    metadata = _require_object(top["analysis_metadata"], "analysis_metadata")
    _require_exact_keys(metadata, "analysis_metadata", required=ANALYSIS_METADATA_KEYS)
    if metadata["source_document_name"] is not None and not isinstance(
        metadata["source_document_name"], str
    ):
        _error(
            "analysis_metadata.source_document_name",
            "stringまたはnullでなければなりません",
        )
    if not isinstance(metadata["analysis_scope"], str) or metadata[
        "analysis_scope"
    ] not in {"full_text", "partial_text", "unknown"}:
        _error("analysis_metadata.analysis_scope", "許可されていない値です")
    warnings = _require_array(
        metadata["analysis_warnings"], "analysis_metadata.analysis_warnings"
    )
    for index, warning in enumerate(warnings):
        if not isinstance(warning, str):
            _error(
                f"analysis_metadata.analysis_warnings[{index}]",
                "stringでなければなりません",
            )

    references: list[tuple[str, str]] = []
    identifiers = {
        "outcome": set(),
        "result": set(),
        "limitation": set(),
        "concept": set(),
        "evidence": set(),
    }
    _validate_bibliography(top["bibliography"], references)
    _validate_fact_object_fields(top["study"], "study", STUDY_KEYS, references)

    methods = _require_object(top["methods"], "methods")
    method_groups = frozenset(METHOD_GROUPS)
    _require_exact_keys(methods, "methods", required=method_groups)
    for group in METHOD_GROUPS:
        _validate_fact_object_fields(
            methods[group], f"methods.{group}", METHOD_KEYS[group], references
        )

    _validate_outcomes(top["outcomes"], references, identifiers)
    _validate_limitations_and_concepts(top, references, identifiers)

    relevance = _require_object(top["research_relevance"], "research_relevance")
    _require_exact_keys(
        relevance, "research_relevance", allowed=RESEARCH_RELEVANCE_KEYS
    )
    for key, value in relevance.items():
        _validate_interpretive(value, f"research_relevance.{key}", references)

    _validate_evidence(top["evidence"], references, identifiers)
    evidence_ids = identifiers["evidence"]
    for path, evidence_id in references:
        if evidence_id not in evidence_ids:
            _error(path, f"存在しないevidence_id {evidence_id!r} を参照しています")
    canonical_json = json.dumps(
        top,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return StructuredImportPayload(data=top, canonical_json=canonical_json)


def _unique_refs(refs: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(refs))


def _planned_fact(field_key: str, fact: dict[str, Any]) -> PlannedField:
    return PlannedField(
        field_key=field_key,
        content_role="source_fact",
        value=fact["value"],
        availability=fact["availability"],
        verification=fact["verification"],
        note=fact["note"],
        evidence_refs=_unique_refs(fact["evidence_refs"]),
    )


def _planned_interpretive(
    field_key: str, item: dict[str, Any]
) -> PlannedField:
    return PlannedField(
        field_key=field_key,
        content_role="interpretation",
        value=item["value"],
        availability=None,
        verification=item["verification"],
        note=item["note"],
        evidence_refs=_unique_refs(item["evidence_refs"]),
    )


def _metric_evidence_refs(metrics: list[dict[str, Any]]) -> tuple[str, ...]:
    refs: list[str] = []
    for metric in metrics:
        refs.extend(metric["evidence_refs"])
        for key in (
            "metric_name",
            "metric_value",
            "unit",
            "model_definition",
            "confidence_interval",
            "condition",
        ):
            refs.extend(metric[key]["evidence_refs"])
    return _unique_refs(refs)


def _canonical_metrics(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove payload-local refs after converting them to canonical links."""
    canonical: list[dict[str, Any]] = []
    for metric in metrics:
        item: dict[str, Any] = {}
        for key in (
            "metric_name",
            "metric_value",
            "unit",
            "model_definition",
            "confidence_interval",
            "condition",
        ):
            fact = metric[key]
            item[key] = {
                "value": fact["value"],
                "availability": fact["availability"],
                "verification": fact["verification"],
                "note": fact["note"],
            }
        item["verification"] = metric["verification"]
        canonical.append(item)
    return canonical


def _build_import_plan(payload: StructuredImportPayload) -> ImportPlan:
    data = payload.data
    entities: list[PlannedEntity] = []
    sort_order = 0
    study_token: Optional[str] = None
    if data["study"]:
        study_token = "canonical:study"
        entities.append(
            PlannedEntity(
                token=study_token,
                entity_type="study",
                parent_token=None,
                sort_order=sort_order,
                verification="ai_unverified",
                fields=tuple(
                    _planned_fact(key, fact) for key, fact in data["study"].items()
                ),
                evidence_refs=(),
            )
        )
        sort_order += 1

    for group, entity_type in METHOD_GROUPS.items():
        group_data = data["methods"][group]
        if not group_data:
            continue
        entities.append(
            PlannedEntity(
                token=f"canonical:method:{group}",
                entity_type=entity_type,
                parent_token=study_token,
                sort_order=sort_order,
                verification="ai_unverified",
                fields=tuple(
                    _planned_fact(key, fact) for key, fact in group_data.items()
                ),
                evidence_refs=(),
            )
        )
        sort_order += 1

    for outcome in data["outcomes"]:
        outcome_token = f"canonical:outcome:{outcome['outcome_id']}"
        fields = [
            _planned_fact(key, outcome[key])
            for key in ("name", "definition", "calculation_method", "unit")
        ]
        fields.extend(
            _planned_fact(f"context_{key}", fact)
            for key, fact in outcome["context"].items()
        )
        metrics = outcome["validation_information"]
        if metrics:
            fields.append(
                PlannedField(
                    field_key="validation_information",
                    content_role="source_fact",
                    value=_canonical_metrics(metrics),
                    availability="reported",
                    verification="ai_unverified",
                    note=None,
                    evidence_refs=_metric_evidence_refs(metrics),
                )
            )
        entities.append(
            PlannedEntity(
                token=outcome_token,
                entity_type="outcome",
                parent_token=study_token,
                sort_order=sort_order,
                verification=outcome["verification"],
                fields=tuple(fields),
                evidence_refs=_unique_refs(outcome["evidence_refs"]),
            )
        )
        sort_order += 1
        for result in outcome["results"]:
            result_fields = [
                _planned_fact("condition_or_comparison", result["condition_or_comparison"]),
                _planned_fact("result", result["result"]),
            ]
            statistics = result["statistics"]
            if statistics:
                result_fields.append(
                    PlannedField(
                        field_key="statistics",
                        content_role="source_fact",
                        value=_canonical_metrics(statistics),
                        availability="reported",
                        verification="ai_unverified",
                        note=None,
                        evidence_refs=_metric_evidence_refs(statistics),
                    )
                )
            entities.append(
                PlannedEntity(
                    token=f"canonical:result:{result['result_id']}",
                    entity_type="result",
                    parent_token=outcome_token,
                    sort_order=sort_order,
                    verification=result["verification"],
                    fields=tuple(result_fields),
                    evidence_refs=_unique_refs(result["evidence_refs"]),
                )
            )
            sort_order += 1

    for limitation in data["limitations"]:
        source_fact = limitation["basis"] == "author_reported"
        entities.append(
            PlannedEntity(
                token=f"canonical:limitation:{limitation['limitation_id']}",
                entity_type="limitation",
                parent_token=study_token,
                sort_order=sort_order,
                verification=limitation["verification"],
                fields=(
                    PlannedField(
                        field_key="text",
                        content_role="source_fact" if source_fact else "interpretation",
                        value=limitation["text"],
                        availability="reported" if source_fact else None,
                        verification=limitation["verification"],
                        note=limitation["note"],
                        evidence_refs=(),
                    ),
                ),
                evidence_refs=_unique_refs(limitation["evidence_refs"]),
            )
        )
        sort_order += 1

    for concept in data["concepts"]:
        source_fact = concept["basis"] == "source_reported"
        role = "source_fact" if source_fact else "interpretation"
        context = concept["context"]
        context_availability = (
            "reported" if source_fact and context is not None else
            "not_extracted" if source_fact else None
        )
        entities.append(
            PlannedEntity(
                token=f"canonical:concept:{concept['concept_id']}",
                entity_type="concept",
                parent_token=study_token,
                sort_order=sort_order,
                verification=concept["verification"],
                fields=(
                    PlannedField(
                        field_key="name",
                        content_role=role,
                        value=concept["name"],
                        availability="reported" if source_fact else None,
                        verification=concept["verification"],
                        note=concept["note"],
                        evidence_refs=(),
                    ),
                    PlannedField(
                        field_key="context",
                        content_role=role,
                        value=context,
                        availability=context_availability,
                        verification=concept["verification"],
                        note=None,
                        evidence_refs=(),
                    ),
                ),
                evidence_refs=_unique_refs(concept["evidence_refs"]),
            )
        )
        sort_order += 1

    if data["research_relevance"]:
        entities.append(
            PlannedEntity(
                token="canonical:research_relevance",
                entity_type="research_relevance",
                parent_token=study_token,
                sort_order=sort_order,
                verification="ai_unverified",
                fields=tuple(
                    _planned_interpretive(key, item)
                    for key, item in data["research_relevance"].items()
                ),
                evidence_refs=(),
            )
        )

    evidence: list[PlannedEvidence] = []
    for item in data["evidence"]:
        reported = {
            key: item[key]["value"] if item[key]["availability"] == "reported" else None
            for key in EVIDENCE_LOCATOR_KEYS
        }
        evidence.append(
            PlannedEvidence(
                evidence_id=item["evidence_id"],
                pdf_page=reported["pdf_page"],
                printed_page=reported["printed_page"],
                section=reported["section"],
                subsection=reported["subsection"],
                table_label=reported["table"],
                figure_label=reported["figure"],
                quote_text=reported["quote_text"],
                note=item["note"],
                verification=item["verification"],
                locator_availability=tuple(
                    (key, item[key]["availability"])
                    for key in EVIDENCE_LOCATOR_KEYS
                ),
            )
        )
    return ImportPlan(entities=tuple(entities), evidence=tuple(evidence))


def _payload_reported_value(data: dict[str, Any], key: str) -> Any:
    fact = data["bibliography"][key]
    return fact["value"] if fact["availability"] == "reported" else None


def _normalize_existing_identifier(
    value: object, normalizer: Any
) -> tuple[Optional[str], bool]:
    try:
        return normalizer(value), False
    except ValueError:
        return None, True


def _identifier_state(
    existing: Optional[str], payload: Optional[str]
) -> str:
    if existing is not None and payload is not None:
        return "match" if existing == payload else "conflict"
    if existing is not None:
        return "existing_only"
    if payload is not None:
        return "payload_only"
    return "missing"


def _contains_not_reported(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("availability") == "not_reported":
            return True
        return any(_contains_not_reported(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_not_reported(item) for item in value)
    return False


def _planned_counts(plan: ImportPlan) -> dict[str, int]:
    entity_types = [entity.entity_type for entity in plan.entities]
    field_count = sum(len(entity.fields) for entity in plan.entities)
    evidence_link_count = sum(
        len(entity.evidence_refs)
        + sum(len(field.evidence_refs) for field in entity.fields)
        for entity in plan.entities
    )
    return {
        "study": entity_types.count("study"),
        "methods": sum(item in structured_repository.METHOD_ENTITY_TYPES for item in entity_types),
        "outcomes": entity_types.count("outcome"),
        "results": entity_types.count("result"),
        "limitations": entity_types.count("limitation"),
        "concepts": entity_types.count("concept"),
        "research_relevance": entity_types.count("research_relevance"),
        "evidence": len(plan.evidence),
        "fields": field_count,
        "evidence_links": evidence_link_count,
    }


def _build_preview(
    connection: sqlite3.Connection,
    literature_id: int,
    payload: StructuredImportPayload,
) -> ImportPreview:
    target = get_literature(connection, literature_id)
    if target is None:
        raise ValueError(f"Literature ID {literature_id} は存在しません。")
    data = payload.data
    plan = _build_import_plan(payload)
    payload_title = _payload_reported_value(data, "title")
    payload_doi_raw = _payload_reported_value(data, "doi")
    payload_pmid_raw = _payload_reported_value(data, "pmid")
    payload_doi = normalize_doi(payload_doi_raw)
    payload_pmid = normalize_pmid(payload_pmid_raw)
    existing_doi, invalid_existing_doi = _normalize_existing_identifier(
        target.doi, normalize_doi
    )
    existing_pmid, invalid_existing_pmid = _normalize_existing_identifier(
        target.pmid, normalize_pmid
    )
    doi_state = _identifier_state(existing_doi, payload_doi)
    pmid_state = _identifier_state(existing_pmid, payload_pmid)
    blocking: list[str] = []
    warnings: list[str] = list(data["analysis_metadata"]["analysis_warnings"])

    if doi_state == "conflict":
        blocking.append("Existing DOIとPayload DOIが不一致のため保存できません。")
    elif doi_state in {"existing_only", "payload_only"}:
        warnings.append("DOIはExisting LiteratureとPayloadの片側だけにあります。")
    if pmid_state == "conflict":
        blocking.append("Existing PMIDとPayload PMIDが不一致のため保存できません。")
    elif pmid_state in {"existing_only", "payload_only"}:
        warnings.append("PMIDはExisting LiteratureとPayloadの片側だけにあります。")
    if invalid_existing_doi:
        blocking.append(
            "Existing LiteratureのDOIを安全に比較できないため保存できません。"
        )
    if invalid_existing_pmid:
        blocking.append(
            "Existing LiteratureのPMIDを安全に比較できないため保存できません。"
        )

    title_similarity: Optional[float]
    if payload_title is None:
        title_similarity = None
        warnings.append("Payload titleがreportedではないためtitle比較できません。")
    else:
        title_similarity = calculate_title_similarity(target.title, payload_title)
        if title_similarity < TITLE_SIMILARITY_THRESHOLD:
            warnings.append(
                "Existing LiteratureとPayloadのtitle類似度が低いため対象を再確認してください。"
            )

    existing_counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM structured_entities WHERE literature_id = ?) AS entity_count,
            (SELECT COUNT(*) FROM evidence_references WHERE literature_id = ?) AS evidence_count
        """,
        (literature_id, literature_id),
    ).fetchone()
    if existing_counts["entity_count"] or existing_counts["evidence_count"]:
        blocking.append(
            "既存structured dataがあるため、自動merge / overwriteせず保存不可です。"
        )

    analysis_scope = data["analysis_metadata"]["analysis_scope"]
    if analysis_scope in {"partial_text", "unknown"}:
        warnings.append(
            f"analysis_scopeが{analysis_scope}です。原著の対象範囲を確認してください。"
        )
        if _contains_not_reported(data):
            warnings.append(
                "partial/unknown sourceでnot_reported判定があります。原著確認を推奨します。"
            )
    warnings.extend(
        (
            "AI情報はai_unverifiedのまま保存されます。",
            "bibliographyとPhase 1 verification_statusは自動更新されません。",
            "analysis_metadataとpayload-local IDはcanonical DBへ保存されません。",
            "Evidence locatorのunavailable状態はimport-only metadataです。",
            "Evidence quoteはAI未確認であり、原著確認済みを意味しません。",
            "既存dataとのmerge / overwriteは行いません。",
        )
    )
    locator_states = tuple(
        (item.evidence_id, item.locator_availability) for item in plan.evidence
    )
    return ImportPreview(
        literature_id=literature_id,
        target_literature=target,
        target_identity_at_preview=(target.title, target.doi, target.pmid),
        payload=payload,
        plan=plan,
        source_document_name=data["analysis_metadata"]["source_document_name"],
        analysis_scope=analysis_scope,
        payload_title=payload_title,
        payload_doi=payload_doi,
        payload_pmid=payload_pmid,
        title_similarity=title_similarity,
        doi_state=doi_state,
        pmid_state=pmid_state,
        warnings=tuple(dict.fromkeys(warnings)),
        blocking_reasons=tuple(dict.fromkeys(blocking)),
        planned_counts=_planned_counts(plan),
        evidence_locator_availability=locator_states,
    )


def build_import_preview(
    connection: sqlite3.Connection,
    literature_id: object,
    payload: StructuredImportPayload,
) -> ImportPreview:
    """Build a read-only target comparison and canonical save plan."""
    if (
        isinstance(literature_id, bool)
        or not isinstance(literature_id, int)
        or literature_id <= 0
    ):
        raise ValueError("literature_idは1以上の整数で指定してください。")
    if not isinstance(payload, StructuredImportPayload):
        raise TypeError("payloadはparse_structured_importの結果を指定してください。")
    validated_payload = parse_structured_import(payload.canonical_json)
    return _build_preview(connection, literature_id, validated_payload)


def _target_identity(literature: Literature) -> tuple[object, object, object]:
    return (literature.title, literature.doi, literature.pmid)


def save_structured_import(
    connection: sqlite3.Connection,
    preview: ImportPreview,
    *,
    confirmed: bool = False,
) -> ImportSaveResult:
    """Atomically save a previewed import only after explicit confirmation."""
    if confirmed is not True:
        raise ValueError("confirmed=Trueの明示確認なしでは保存できません。")
    if not isinstance(preview, ImportPreview):
        raise TypeError("previewはbuild_import_previewの結果を指定してください。")
    if connection.in_transaction:
        raise ValueError("アクティブなトランザクション中はimportを保存できません。")

    try:
        connection.execute("BEGIN IMMEDIATE")
        validated_payload = parse_structured_import(
            preview.payload.canonical_json
        )
        current_preview = _build_preview(
            connection, preview.literature_id, validated_payload
        )
        if (
            _target_identity(current_preview.target_literature)
            != preview.target_identity_at_preview
        ):
            raise ValueError(
                "Preview後にTarget Literatureのtitle/DOI/PMIDが変更されました。再Previewしてください。"
            )
        if current_preview.blocked:
            raise ValueError(" ".join(current_preview.blocking_reasons))

        evidence_ids: dict[str, int] = {}
        for evidence in current_preview.plan.evidence:
            evidence_ids[evidence.evidence_id] = (
                structured_repository.create_evidence_reference(
                    connection,
                    preview.literature_id,
                    pdf_page=evidence.pdf_page,
                    printed_page=evidence.printed_page,
                    section=evidence.section,
                    subsection=evidence.subsection,
                    table_label=evidence.table_label,
                    figure_label=evidence.figure_label,
                    quote_text=evidence.quote_text,
                    note=evidence.note,
                    verification=evidence.verification,
                )
            )

        entity_ids: dict[str, int] = {}
        for entity in current_preview.plan.entities:
            parent_id = (
                None
                if entity.parent_token is None
                else entity_ids[entity.parent_token]
            )
            entity_id = structured_repository.create_structured_entity(
                connection,
                preview.literature_id,
                entity.entity_type,
                parent_entity_id=parent_id,
                sort_order=entity.sort_order,
                verification=entity.verification,
            )
            entity_ids[entity.token] = entity_id
            for field in entity.fields:
                field_id = structured_repository.create_structured_field(
                    connection,
                    entity_id,
                    field.field_key,
                    content_role=field.content_role,
                    value=field.value,
                    availability=field.availability,
                    verification=field.verification,
                    note=field.note,
                )
                for evidence_ref in field.evidence_refs:
                    structured_repository.attach_evidence_to_field(
                        connection, field_id, evidence_ids[evidence_ref]
                    )
            for evidence_ref in entity.evidence_refs:
                structured_repository.attach_evidence_to_entity(
                    connection, entity_id, evidence_ids[evidence_ref]
                )
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        raise
    return ImportSaveResult(
        literature_id=preview.literature_id,
        planned_counts=dict(current_preview.planned_counts),
    )
