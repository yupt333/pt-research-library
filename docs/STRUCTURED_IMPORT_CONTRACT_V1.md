# ChatGPT Structured Import Contract v1

## 1. Status and purpose

This document is the normative Phase 2-2 contract for passing a structured literature analysis from ChatGPT to PT Research Library.

The contract version is:

```text
pt_research_library_structured_import_v1
```

The intended semi-automatic workflow is:

```text
ChatGPT
↓
structured JSON
↓
Import Preview
↓
user review and confirmation
↓
PT Research Library
```

The JSON is the machine-readable source of truth for a future import. It is not:

- a database insert command;
- a database update command;
- a SQL schema;
- an internal database representation;
- an instruction to merge or overwrite a record.

ChatGPT does not need to know and must not guess SQLite IDs. Determining and confirming the target Literature record belongs to the Phase 2-4 Import Preview. Automatic target matching, merge, and overwrite are not defined by this contract and are prohibited.

This Step is documentation only. It does not implement a parser, Import Preview, database schema, database normalization, repository operation, OpenAI API, external API, PDF parser, OCR, or UI.

## 2. JSON wire format

The v1 machine-readable format is JSON because Python can read it with the standard-library `json` module, no external dependency is required, ChatGPT can generate it, users can inspect it, and a future parser can validate it.

A raw payload must be:

- UTF-8;
- valid JSON;
- JSON only, without prose before or after it;
- free of JSON comments;
- free of Markdown code fences;
- written with `snake_case` field names;
- identified by the exact v1 `contract_version`.

JSON object order has no semantic meaning. Documentation and examples use the following recommended top-level order:

1. `contract_version`
2. `analysis_metadata`
3. `bibliography`
4. `study`
5. `methods`
6. `outcomes`
7. `limitations`
8. `concepts`
9. `research_relevance`
10. `evidence`

All ten top-level keys are required. An array may be empty and an object may contain no optional facts, but omission of a required top-level key is invalid.

## 3. Top-level contract

| Key | Type | Meaning |
|---|---|---|
| `contract_version` | string | Must equal `pt_research_library_structured_import_v1` |
| `analysis_metadata` | object | Source name, analysis coverage, and warnings |
| `bibliography` | object | Source-oriented bibliographic facts corresponding conceptually to the Phase 1 Literature master |
| `study` | object | Study design, objective, population, eligibility, groups, and setting |
| `methods` | object | Five PT-oriented but modality-independent method subgroups |
| `outcomes` | array | Zero or more logical Outcomes |
| `limitations` | array | Author-reported or explicitly AI-inferred limitations |
| `concepts` | array | Research concepts; not Tags |
| `research_relevance` | object | AI interpretation about relevance to the user's research |
| `evidence` | array | Payload-local source Evidence objects |

This conceptual shape is not a promise about future SQLite tables, columns, foreign keys, or normalization.

## 4. Analysis metadata

`analysis_metadata` has three required fields:

| Field | Type | Rule |
|---|---|---|
| `source_document_name` | string or null | A user-visible document name only. Do not generate a local absolute `pdf_path` |
| `analysis_scope` | string | One of `full_text`, `partial_text`, or `unknown` |
| `analysis_warnings` | array of strings | Extraction limitations; use an empty array only when no warning is known |

`full_text` means ChatGPT could sufficiently inspect the complete text needed for the analysis. It must not be used when only an abstract, selected pages, selected sections, or incomplete OCR was available. `partial_text` declares known incomplete coverage. `unknown` declares that coverage cannot be established.

Warnings should identify risks such as an unreadable Table, unclear Figure, image information that could not be extracted, an uncertain page number, incomplete sections, or incomplete text. Model names and ChatGPT-internal metadata are not required.

## 5. Structured Fact Value

Source-oriented factual fields use a common Structured Fact Value object:

```json
{
  "value": "example",
  "availability": "reported",
  "verification": "ai_unverified",
  "evidence_refs": ["evidence_1"],
  "note": null
}
```

Each of these five keys is required whenever a Structured Fact Value is present.

| Field | Type | Meaning |
|---|---|---|
| `value` | any JSON value or null | Extracted source value; its expected type depends on the containing field |
| `availability` | string | Why a value is present or absent |
| `verification` | string | Whether a user has verified the item; raw ChatGPT output must use `ai_unverified` |
| `evidence_refs` | array of strings | References to Evidence IDs in this payload; empty when no exact Evidence is available |
| `note` | string or null | Extraction caveat or clarification, never a place to hide an unsupported fact |

This object is a contract concept, not a SQLite representation. A single JSON `null` must never be used to infer why information is absent.

### 5.1 Availability vocabulary

The v1 vocabulary is closed:

| Value | Meaning |
|---|---|
| `reported` | ChatGPT extracted a value or statement that it judged to be present in the source document |
| `not_reported` | ChatGPT inspected the relevant source sufficiently and judged that the item was not reported |
| `not_extracted` | The item has not yet been sufficiently extracted or checked |
| `unclear` | Relevant source content may exist, but its meaning or value could not be established |
| `not_applicable` | The concept itself does not apply to this study |

Consistency rules:

- `availability == reported` requires a non-null `value`.
- `availability != reported` requires `value == null`.
- `not_reported` is a substantive AI judgment, not a synonym for missing JSON or `not_extracted`.
- When `analysis_scope` is `partial_text` or `unknown`, `not_reported` may be used only if the relevant source range was nevertheless inspected sufficiently for that specific item. Otherwise use `not_extracted` or `unclear` and add an analysis warning when the coverage gap matters.
- `unclear` must be used instead of choosing one plausible interpretation.
- A `note` may explain uncertainty but cannot override these consistency rules.

### 5.2 Verification vocabulary

Raw ChatGPT output uses:

- `ai_unverified`

A future application may represent:

- `user_verified`

ChatGPT must never self-assert `user_verified`. Only the user may change an item to `user_verified` after checking the source. This applies even when ChatGPT judges an item `not_reported`; that judgment remains `ai_unverified` until user review.

This item-level verification is separate from the Phase 1 Literature-level `verification_status`. Importing structured items must not automatically overwrite the Phase 1 status.

Numeric AI confidence scores such as `0.87` are not required and must not substitute for availability, verification, Evidence, or warnings.

## 6. Facts and interpretations

The following sections are source-oriented:

- `bibliography`;
- `study`;
- `methods`;
- `outcomes` and `results`;
- author-reported entries in `limitations`;
- source-reported entries in `concepts`;
- `evidence`.

The following are interpretive:

- AI-inferred entries in `limitations`;
- AI-inferred entries in `concepts`;
- all fields in `research_relevance`.

An interpretation must be labeled by its basis and must not be presented as a statement made by the paper. Research Relevance means “how this paper may relate to the user's research,” not a factual result of the source.

Interpretive values use this shape:

```json
{
  "value": "AI interpretation",
  "verification": "ai_unverified",
  "evidence_refs": ["evidence_1"],
  "note": null
}
```

Interpretive values do not use `availability: reported`, because their presence does not mean that the paper reported the interpretation. If no interpretation is produced, the optional field may be omitted or its `value` may be null. Evidence references show the source material considered; they do not transform the interpretation into a source fact.

## 7. Bibliography

`bibliography` can represent all Phase 1 Literature master concepts below. In v1 examples and generated payloads, these keys should all be present as Structured Fact Values so that missing reasons remain explicit:

- `title`
- `authors`
- `journal`
- `publication_year`
- `volume`
- `issue`
- `pages`
- `doi`
- `pmid`
- `url`
- `language`
- `publication_type`
- `abstract`

`authors.value` may be an ordered array of exact author strings when the source supports it. Other compound source values may use JSON arrays or objects when necessary; a future parser must validate their field-specific type.

ChatGPT must not invent a Title, Authors, Journal, DOI, PMID, or URL. In particular, it must not create a plausible-looking identifier as a placeholder. If not confirmed, `value` is null and `availability` must distinguish `not_extracted`, `not_reported`, or `unclear`. The Phase 1 `pdf_path` is intentionally excluded because local file paths belong to the Library, not ChatGPT.

## 8. Study

`study` may contain these Structured Fact Values:

- `study_design`
- `research_objective`
- `population`
- `sample_size`
- `demographics`
- `condition_diagnosis`
- `health_status`
- `inclusion_criteria`
- `exclusion_criteria`
- `group_allocation`
- `study_setting`

Not every field is required for every study. A complex population, demographic description, or group allocation may use a JSON object or array in `value`; it must not be compressed into a misleading single string. This flexibility does not define SQL tables.

## 9. Methods

`methods` must retain all five Phase 2-1 subgroups. The five subgroup keys are required; their Structured Fact Value fields are optional where a concept does not need to be represented. The model is PT-specialized but is not ultrasound-only.

### 9.1 `measurement_imaging`

- `measurement`
- `imaging_modality`
- `imaging_condition`
- `device`
- `probe`
- `probe_orientation`
- `frame_rate`
- `sampling_condition`
- `calibration_scale`
- `roi`

### 9.2 `body_condition`

- `body_position`
- `joint_position`
- `joint_angle`
- `limb_position`
- `contraction_type`
- `muscle_activation_condition`
- `load`
- `weight_bearing_condition`

### 9.3 `task_protocol`

- `task`
- `movement`
- `range`
- `speed`
- `repetition`
- `duration`
- `rest`
- `trial_number`

### 9.4 `analysis`

- `analysis_method`
- `tracking_algorithm`
- `preprocessing`
- `reference_frame_baseline`
- `calculation_method`
- `roi_handling`
- `quality_control`

### 9.5 `validation_statistics`

- `validation`
- `reliability_method`
- `statistical_analysis`
- `icc`
- `sem`
- `mdc`
- `mcid`
- `agreement_analysis`
- `other_statistical_method`

Unknown joint angles, loads, frame rates, ROI sizes, tracking algorithms, validation values, and statistical values must not be guessed.

## 10. Outcomes

`outcomes` is an array. One Outcome is one logical unit, not merely one unique name.

Each Outcome has:

| Field | Type | Rule |
|---|---|---|
| `outcome_id` | string | Required payload-local ID, unique across the payload |
| `name` | Structured Fact Value | Required and kept separate from definition |
| `definition` | Structured Fact Value | Required; may explicitly be unavailable |
| `calculation_method` | Structured Fact Value | Required; may explicitly be unavailable |
| `unit` | Structured Fact Value | Required; may explicitly be unavailable |
| `context` | object | Required; context fields are Structured Fact Values |
| `results` | array | Required; zero or more Result objects |
| `validation_information` | array | Required; zero or more validation metric objects |
| `verification` | string | Required; `ai_unverified` in raw ChatGPT output |
| `evidence_refs` | array of strings | Required Outcome-level Evidence summary |

Outcome name, Outcome definition, and calculation method must never be merged:

```text
same outcome name
≠
direct comparability
```

ChatGPT must not merge Outcomes merely because their names match. Conditions, positions, regions, layers, time points, tasks, definitions, or calculations may make same-name Outcomes distinct logical units.

### 10.1 Outcome context

`context` can contain these Structured Fact Values as needed:

- `condition`
- `group`
- `body_position`
- `task`
- `load`
- `region`
- `layer`
- `time_point`

Unknown context must remain explicitly unavailable. It must not be filled from assumptions. Context may contain additional known detail only under the forward-compatibility rules in section 18.

Omission of a context field must not be interpreted as evidence that the condition did not exist. When a comparison-relevant dimension is known to matter but its value is unknown, include the field with `not_extracted` or `unclear` rather than dropping it or merging the Outcome.

## 11. Results

One Outcome may have multiple Results. Each Result has:

| Field | Type | Rule |
|---|---|---|
| `result_id` | string | Required payload-local ID, unique across the entire payload |
| `condition_or_comparison` | Structured Fact Value | Required |
| `result` | Structured Fact Value | Required; never generate a guessed numeric value |
| `statistics` | array | Required; zero or more metric objects |
| `verification` | string | Required; `ai_unverified` in raw ChatGPT output |
| `evidence_refs` | array of strings | Required |

The traceable relationship is:

```text
Outcome
↓
Condition / comparison
↓
Result
↓
Statistics
↓
Evidence
```

## 12. Reliability, validation, and statistical metrics

Each entry in Result `statistics` or Outcome `validation_information` uses the same metric object:

| Field | Type |
|---|---|
| `metric_name` | Structured Fact Value |
| `metric_value` | Structured Fact Value |
| `unit` | Structured Fact Value |
| `model_definition` | Structured Fact Value |
| `confidence_interval` | Structured Fact Value |
| `condition` | Structured Fact Value |
| `verification` | string |
| `evidence_refs` | array of strings |

This preserves a metric name, value, applicable unit, reported model or definition, confidence interval, condition, and Evidence without deciding the final database normalization. For ICC, preserve the reported model/type when present. If it is not available, do not infer it from the number or study design. The same rule applies to SEM, MDC, MCID, agreement statistics, p values, and other metrics.

## 13. Limitations

`limitations` is an array. Each entry has:

- `limitation_id`: required payload-local ID, unique across the payload;
- `text`: required non-empty string;
- `basis`: `author_reported` or `ai_inferred`;
- `verification`: `ai_unverified` in raw ChatGPT output;
- `evidence_refs`: array of payload-local Evidence references;
- `note`: string or null.

`author_reported` means the limitation was explicitly stated by the authors. `ai_inferred` means ChatGPT inferred a methodological concern. An AI inference must remain visibly labeled and must not be rewritten as an author statement. A source-based author-reported limitation should reference Evidence when available.

## 14. Concepts

`concepts` is an array. Each entry has:

- `concept_id`: required payload-local ID, unique across the payload;
- `name`: required non-empty string;
- `context`: string, object, array, or null;
- `basis`: `source_reported` or `ai_inferred`;
- `verification`: `ai_unverified` in raw ChatGPT output;
- `evidence_refs`: array of payload-local Evidence references;
- `note`: string or null.

A Concept represents research knowledge or a meaning relationship. It is not a Tag, which remains a classification and search aid. This contract does not implement a Concept database model.

## 15. Research Relevance

`research_relevance` is an interpretive section, separate from source Results. It can contain these Interpretive Value objects:

- `summary`
- `methodological_relevance`
- `clinical_relevance`
- `protocol_relevance`
- `methodological_cautions`
- `comparison_notes`

All content is AI interpretation in a raw ChatGPT payload, must use `ai_unverified`, and must remain editable and reviewable by the user in a future workflow. It must not be described as the paper's direct statement. Project IDs and Project associations are not part of v1.

## 16. Evidence

`evidence` is an array. Each Evidence object has:

| Field | Type | Rule |
|---|---|---|
| `evidence_id` | string | Required payload-local ID, unique across the payload |
| `pdf_page` | Structured Fact Value | PDF viewer's 1-based page index, not a printed page |
| `printed_page` | Structured Fact Value | Page number or label printed on the article |
| `section` | Structured Fact Value | Source section |
| `subsection` | Structured Fact Value | Source subsection |
| `table` | Structured Fact Value | Exact Table identifier when known |
| `figure` | Structured Fact Value | Exact Figure identifier when known |
| `quote_text` | Structured Fact Value | Exact original text only |
| `note` | string or null | Evidence-specific extraction note, not a substitute quote |
| `verification` | string | `ai_unverified` in raw ChatGPT output |

`pdf_page` and `printed_page` are distinct. `pdf_page` is a 1-based index in the PDF viewer. `printed_page` is the page number or label printed in the article. They must not be assumed equal.

`quote_text.value` may contain only source text that ChatGPT could inspect accurately. A paraphrase, summary, translation, reconstruction, or plausible wording must not be placed in `quote_text`. If exact wording cannot be confirmed, `quote_text.value` must be null and its availability must be `not_extracted` or `unclear`, with a warning where appropriate.

Structured Fact Values nested inside an Evidence object use an empty `evidence_refs` array; Evidence does not self-reference.

## 17. Local IDs and reference integrity

These identifiers are valid only within one payload:

- `outcome_id`
- `result_id`
- `limitation_id`
- `concept_id`
- `evidence_id`

They are not SQLite IDs. Recommended readable forms are `outcome_1`, `result_1`, `limitation_1`, `concept_1`, and `evidence_1`. Each ID type must be unique across the entire payload; `result_id` is not allowed to repeat in different Outcomes.

Every string in any `evidence_refs` array must equal an `evidence_id` present in the payload's `evidence` array. Dangling references are invalid. Evidence linkage is a payload-local reference contract, not a database foreign-key design.

The raw contract must not include a guessed `literature_id`, Outcome database ID, Result database ID, Evidence database ID, or any other Library-internal ID.

## 18. Import target safety and forward compatibility

The payload says “these are extracted facts and interpretations,” not “update this row.” A future Import Preview may show Title, DOI, PMID, and user selection to help confirm a target, but the matching algorithm is intentionally deferred to Phase 2-4.

Unknown fields must never be silently discarded. A future v1 parser must retain enough information to:

- warn that an unknown field exists;
- show the field and value in Import Preview;
- require user confirmation or reject the payload when safe interpretation is impossible.

The exact `contract_version` distinguishes v1 from future versions. A parser must not silently treat a future or unknown version as v1. This rule applies to unknown top-level keys and to unknown nested fields.

## 19. Hallucination prevention

ChatGPT must leave a fact unavailable instead of supplying a plausible value. It must never guess or fabricate:

- DOI, PMID, URL, Title, Authors, or Journal;
- sample size;
- measurement value;
- p value;
- ICC, SEM, MDC, or MCID;
- joint angle or load;
- frame rate or ROI size;
- tracking algorithm;
- PDF or printed page number;
- Table or Figure identifier;
- quote text.

The same rule applies to any other unsupported field. `analysis_scope`, `analysis_warnings`, Structured Fact Value availability, item verification, and Evidence are the safety controls. A confidence score is not a verification control.

ChatGPT must not create or infer the user's local PDF path. Only `analysis_metadata.source_document_name` may identify the analyzed input by a user-visible name.

## 20. Future parser validation requirements

Phase 2-4 must define and implement the parser. At minimum, that parser must validate:

1. valid UTF-8 JSON and no non-JSON wrapper;
2. exact `contract_version` match;
3. all required top-level keys and their types;
4. the closed availability vocabulary;
5. the allowed verification vocabulary;
6. rejection of `user_verified` in a raw ChatGPT payload;
7. Structured Fact Value reported/null consistency;
8. uniqueness of all payload-local IDs by ID type;
9. resolution of every `evidence_refs` entry;
10. required Outcome, Result, and Evidence structure;
11. field-specific types and boundaries, including 1-based positive `pdf_page` values when reported;
12. no silent acceptance of malformed Outcome, Result, metric, or Evidence objects;
13. no silent loss of unknown top-level or nested fields;
14. presence of warnings or user-visible review for partial or uncertain source coverage;
15. no automatic completion or inference of DOI, PMID, URL, or other identifiers.

The parser must not convert raw `ai_unverified` content into `user_verified`, overwrite the Phase 1 Literature verification status, save automatically, merge automatically, or overwrite automatically.

No parser is implemented in Phase 2-2.

## 21. Output Instructions v1

The following short instruction is intended for manual use with ChatGPT. It is not an API prompt.

```text
Return JSON only, with no Markdown code fence or surrounding explanation. Follow PT Research Library contract version pt_research_library_structured_import_v1 exactly. Do not guess or fabricate missing facts, identifiers, numbers, conditions, page locations, Table/Figure labels, or quotations; in particular, never invent DOI, PMID, or URL. Distinguish not_extracted, not_reported, and unclear. Use verification = ai_unverified everywhere in this raw ChatGPT payload and never output user_verified. Keep Outcome name, definition, and calculation_method separate, and do not merge same-name Outcomes when context differs. Add evidence_refs wherever exact supporting Evidence is available, and ensure every reference resolves to an evidence_id in this payload. Put only exact source wording in quote_text. If an exact quote or Evidence location is unknown, leave its value null with the appropriate availability instead of guessing. If the full source was not sufficiently inspected, do not declare analysis_scope = full_text.
```

## 22. Human-readable output and Obsidian Gate

ChatGPT may separately explain an analysis in human-readable prose. That prose is not parser input. The JSON payload is the machine-readable source of truth for import, and a Markdown table is not an import format.

This contract passes the Obsidian Gate because it is not a Markdown note template. It provides a machine-readable, PT Methods-structured, Outcome-definition-aware, Evidence-aware, verification-aware, and comparison-ready transfer format. It does not require the user to hand-maintain Markdown, links, properties, folders, database tables, or internal IDs.

## 23. Normative synthetic example

The valid, entirely synthetic example is [`examples/structured_import_v1.example.json`](examples/structured_import_v1.example.json). It does not model a real publication. Its DOI, PMID, and URL values are null, it contains at least two same-name logical Outcomes with different context, and all raw verification values are `ai_unverified`.

The example illustrates the contract; it is not a database seed, a production record, or permission to fabricate equivalent fields from an actual incomplete paper.
