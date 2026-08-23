# Structured Import Workflow

## 1. Purpose

本文書は、PT Research Library Phase 2-4におけるChatGPT Structured Import Contract v1 JSONのvalidation、Import Preview、canonical SQLite schemaへの保存手順を定める。

目的は、AI抽出結果を自動的な確定情報として扱わず、対象Literature、書誌identifier、抽出範囲、Evidence、保存予定件数を利用者が確認した後だけ、Phase 2-3 schemaへ安全に保存することである。

## 2. Single-user local workflow

本機能は、ユーザー本人1人がMacで使用するlocal applicationを前提とする。account、login、role、permission、multi-user、sync、sharing、serverは導入しない。

```text
既存Literatureを登録
↓
ChatGPTでPDFを解析
↓
Contract v1 JSONをMacへ保存
↓
CLI「11. ChatGPT構造化JSON取込」
↓
Literature IDとJSON fileを指定
↓
Import Preview
↓
利用者確認
↓
local SQLiteへatomic save
```

## 3. Zero-additional-cost policy

Phase 2-4の追加料金は0円である。Python standard libraryとSQLiteだけを使用する。

使用しないもの：

- OpenAI API、Claude API、その他AI API
- PubMed API、Crossref API、その他network access
- cloud database、cloud storage、server、paid SaaS
- external dependency、paid dependency
- automated ChatGPT invocation

## 4. JSON input

入力はUTF-8のJSON file 1つである。正式contract versionは次だけである。

```text
pt_research_library_structured_import_v1
```

JSON fileはread-onlyで扱い、copy、move、delete、rewriteしない。macOS Terminalへのdrag & dropで引用符やescaped spaceを含むpathは、Python standard libraryの`shlex`で1 pathとしてparseする。shell commandは実行しない。

## 5. Parser validation

処理は次へ分離する。

```text
parse
→ validate
→ canonical plan build
→ Import Preview
→ user confirmation
→ save
```

parserは次をrejectする。

- invalid JSON、Markdown code fence、JSON前後のprose
- duplicate object key
- NaN、Infinity、-Infinity
- wrong top-level type、missing required top-level field
- unknown / future contract version
- unknown top-level field、unknown nested field、typo field
- malformed Structured Fact Value / Interpretive Value
- invalid availability / value consistency
- raw `user_verified`
- malformed Outcome、Result、metric、Evidence
- duplicate payload-local ID、dangling Evidence reference
- Evidence self-reference、note-only Evidence、locatorなしEvidence

validation errorは可能な限り`outcomes[0].definition.availability`のようなJSON pathと原因を示す。Parserはmissing fact、DOI、PMID、URL、title、number、condition、page、quote等を補完または修正しない。

## 6. Import target selection

Import targetは利用者が既存Literature IDを明示指定する。Payloadからのautomatic target selectionを行わない。存在しないIDはerrorとする。Payload内の`literature_id`やSQLite internal IDはunknown fieldとしてrejectする。

## 7. Bibliography comparison

Payload bibliographyはtarget確認にだけ使用する。既存`literature` tableを自動updateせず、structured schemaへbibliography copyも保存しない。

最低限、Existing LiteratureとPayloadのtitle、DOI、PMIDを比較する。DOI / PMIDは既存の`normalize_doi` / `normalize_pmid`をread-only comparisonに利用する。外部networkでidentifierの実在性を確認しない。

## 8. Import Preview

Import Previewは最低限、次を日本語で表示する。

- Target LiteratureのID、title、DOI、PMID
- Payloadのsource document name、analysis scope、title、DOI、PMID
- title similarity、DOI state、PMID state、保存可否
- analysis warningsと安全上の重要注意
- Study、Methods、Outcomes、Results、Limitations、Concepts、Research Relevance、Evidence、Fields、Evidence linksの予定件数
- canonical DBへ保存されないimport-only information
- Evidence locator availability metadata

Preview生成はread-onlyであり、structured entity、field、Evidence、linkを作成しない。

## 9. Blocking conditions

次の場合はsaveをblockし、CLIはconfirmation menuへ進まない。

- Existing DOIとPayload DOIが両方存在して不一致
- Existing PMIDとPayload PMIDが両方存在して不一致
- Existing LiteratureのDOIまたはPMIDが正規化できず、安全に比較できない
- 対象Literatureにstructured entityが1件以上ある
- 対象LiteratureにEvidenceが1件以上ある
- save直前にtargetが存在しなくなった
- Preview後にtargetのtitle / DOI / PMIDが変更された
- active transaction中のsave

既存structured dataがある場合は、自動merge、自動overwrite、自動delete-and-replaceを行わない。

## 10. Warning conditions

次はwarningまたはinformationであり、それだけではsaveをblockしない。

- title similarityが0.90未満
- DOI / PMIDがExistingまたはPayloadの片側だけにある
- Payload titleがreportedではなく比較できない
- `analysis_scope`が`partial_text`または`unknown`
- partial / unknown sourceに`not_reported`判断がある
- Payload `analysis_warnings`
- Evidence locatorのunavailable状態がcanonical DBへ1:1保存されない

WarningはAI判断の正しさを保証せず、原著確認を促す。

## 11. JSON to canonical schema mapping

| Contract section | Canonical mapping |
|---|---|
| `study` | `study` entity + source-fact fields |
| `methods.measurement_imaging` | `method_measurement_imaging` entity + fields |
| `methods.body_condition` | `method_body_condition` entity + fields |
| `methods.task_protocol` | `method_task_protocol` entity + fields |
| `methods.analysis` | `method_analysis` entity + fields |
| `methods.validation_statistics` | `method_validation_statistics` entity + fields |
| one Outcome | one `outcome` entity |
| one Result | corresponding Outcome childの`result` entity |
| one Limitation | one `limitation` entity |
| one Concept | one `concept` entity |
| Research Relevance | one `research_relevance` entity + interpretation fields |
| one Evidence | one canonical Evidence row |

Studyが作成される場合、Methods、Outcomes、Limitations、Concepts、Research Relevanceは同じLiteratureのStudyをparentにする。Resultは対応Outcome parentを必須とする。

Outcomeの`name`、`definition`、`calculation_method`、`unit`は別fieldとする。contextは`context_condition`、`context_group`、`context_body_position`、`context_task`、`context_load`、`context_region`、`context_layer`、`context_time_point`へmapする。同名Outcomeを統合しない。

Non-empty `validation_information`と`statistics`はmetric arrayをvalid JSONとして保存する。metricのvalue、availability、verification、noteを保持し、payload-local Evidence refはcanonical linkへ変換してvalue JSONへ残さない。

Limitationは`author_reported`を`source_fact`、`ai_inferred`を`interpretation`として`text` fieldへ保存する。basisはcontent roleから再構成する。

Conceptは`source_reported`を`source_fact`、`ai_inferred`を`interpretation`とし、`name`と`context`を別fieldへ保存する。Concept noteは主fieldである`name`のnoteへ保持する。`ai_inferred + context null`はinterpretationのJSON `null`として保存する。`source_reported + context null`は、原著にないとの推測を避け、wire上でcontext未提供であることをcanonical `not_extracted`として表現する。

Research Relevanceはすべて`interpretation`であり、原著のfactual Resultとして保存しない。

## 12. Local ID mapping

`outcome_id`、`result_id`、`limitation_id`、`concept_id`、`evidence_id`は1 payload内だけで有効である。Import transaction中だけ次のmappingを保持する。

```text
payload-local ID → generated SQLite ID
```

Payload-local ID文字列はcanonical DBへ保存しない。Outcome nameにもunique constraintやmerge keyとして使用しない。

## 13. Evidence mapping

Evidenceのreported locatorだけをcanonical columnへ保存する。

| Contract locator | Canonical destination |
|---|---|
| `pdf_page` | `pdf_page` |
| `printed_page` | `printed_page` |
| `section` | `section` |
| `subsection` | `subsection` |
| `table` | `table_label` |
| `figure` | `figure_label` |
| `quote_text` | `quote_text` |

Fact Value `evidence_refs`はfield Evidence linkへmapする。Outcome / Result / Limitation / Concept object refsはentity Evidence linkへmapする。`statistics` / `validation_information`のmetric objectとnested Fact Value refsは重複なしunionとして対応fieldへlinkする。

Quoteが正確な原文かどうかは機械判定できない。PreviewはEvidenceとverificationを表示するが、quoteの原著確認済みを意味しない。

## 14. Evidence locator availability caveat

Contract内の各Evidence locatorは`reported`、`not_reported`、`not_extracted`、`unclear`、`not_applicable`を持つ。一方、Phase 2-3 canonical Evidence schemaにはlocator単位availability columnがない。

そのため、reported valueだけをcanonical Evidenceへ保存し、unavailable locator stateはImport Previewで`import-only metadata`として表示する。このmetadataはcanonical DBへ1:1保存されない。Schemaを無断変更せず、この境界を利用者に明示することでsilent lossとして扱わない。

## 15. AI verification rule

Raw ChatGPT JSONは全階層で`verification = ai_unverified`だけを許可する。`user_verified`はparserがrejectする。Importはentity、field、Evidenceを`ai_unverified`として保存し、Phase 1 `literature.verification_status`を変更しない。

Repository CRUD自体は、将来の利用者確認用として`user_verified`を許可する。Raw importとcanonical user reviewは別責務である。

## 16. User confirmation

Save APIは`confirmed=False`をdefaultとし、`confirmed=True`が明示されなければwriteしない。CLIは次だけを提示する。

```text
1. この内容で保存する
0. 保存せず戻る
```

`0`ではsave APIを呼ばない。

## 17. Atomic save

Full importは1つの`BEGIN IMMEDIATE` transactionで実行する。Evidence、entities、fields、field links、entity linksの全insertが成功した場合だけcommitする。Phase 1 Literature rowをupdateしない。

## 18. Rollback

途中で1件でも失敗した場合、transaction全体をrollbackする。Entity、field、Evidence、linkのpartial rowsを残さない。Failure後もtarget LiteratureとそのPhase 1 fieldsを保持する。

## 19. Existing structured data handling

Targetにstructured entityまたはEvidenceが1件でも存在する場合、full importをblockする。これはduplicate structured dataとunreviewed overwriteを防ぐためである。既存dataをdeleteして再importする機能はPhase 2-4にない。

## 20. Data intentionally not persisted

次はcanonical DBへ保存しない。

- `contract_version`
- `analysis_metadata`
- bibliography copy
- raw ChatGPT JSON
- Import Preview state / import job
- payload-local IDs
- metric内payload-local Evidence refs（canonical linkへ変換する）
- Evidence locator単位のunavailable metadata

これらは必要に応じてPreviewへ表示し、保存されない境界をsilentにしない。

## 21. No merge / overwrite

Phase 2-4では次を禁止する。

- existing structured data + new importのautomatic merge
- existing structured dataのautomatic delete / replacement
- same-name Outcome merge
- bibliography auto update
- Phase 1 status auto update
- automatic target change

## 22. CLI workflow

Main menu 1〜10と0は維持し、次を追加する。

```text
11. ChatGPT構造化JSON取込
```

11選択後はLiterature ID、JSON file pathを入力し、validation、Preview、blocking checkを行う。保存可能な場合だけconfirmation menuを表示する。Validation errorはpathと原因を表示し、通常画面へtracebackを出さない。

## 23. Out of scope

Phase 2-4では次を実装しない。

- database schema change / migration version 2
- PDF parser、OCR、PDF page navigation
- automatic Evidence navigation
- Comparison matrix、Outcome comparability judgment
- Research Project、natural-language search、GUI
- API、network、cloud、server、account、sync
- automated ChatGPT invocation

## 24. Acceptance criteria

- Contract v1だけをstrict parseし、unknown field、duplicate key、non-finite number、raw `user_verified`をrejectする。
- explicit Literature targetを使用し、DOI / PMID conflictをblockする。
- Preview前後でstructured DB snapshotが一致する。
- bibliographyとPhase 1 statusを変更しない。
- partial / unknown sourceと`not_reported`をwarningする。
- same-name Outcomesを別entityとして保存する。
- payload-local IDをgenerated SQLite IDへmapし、永続化しない。
- Evidence refsをfield / entity粒度でresolveする。
- confirmationなし、cancel、blocked Previewでwriteしない。
- existing structured dataをmerge / overwriteしない。
- atomic saveし、mid-import failureを全rollbackする。
- `PRAGMA foreign_key_check`がempty、`PRAGMA quick_check`が`ok`である。
- Phase 1 regressionを維持する。
- external dependency、network、API、cloud、追加料金を必要としない。
