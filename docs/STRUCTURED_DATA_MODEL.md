# Structured Research Data Model

## 1. Purpose

本文書は、PT Research Library Phase 2-3のcanonical SQLite structured research data modelとmigration safetyを定める。

目的は、Phase 1の文献libraryを維持したまま、将来Study、Methods、Outcomes、Results、Limitations、Concepts、Research Relevance、Evidenceを保存・比較できるadditiveな基盤を追加することである。本Stepはschema、migration、database integrity、schema documentationまでを対象とする。

## 2. Phase 1 preservation

次のPhase 1テーブルはcolumn、constraint、意味を変更しない。

- `literature`
- `tags`
- `literature_tags`
- `usage_history`

Migration 1は`DROP TABLE`、`ALTER TABLE`、`DELETE`、既存rowへの`UPDATE`を行わない。既存IDを再生成せず、既存free-textをstructured dataへ変換またはcopyしない。Phase 1 literatureがmigration後もstructured data 0件であることは正常である。

## 3. Why the JSON contract is not copied 1:1

Phase 2-2 JSON contractはChatGPTからImport Previewへ解析結果を安全に受け渡すwire formatであり、database schemaではない。payloadのnesting、local ID、analysis coverage、bibliographyは、canonical research dataとは異なるlifecycleを持つ。

Databaseでは、複数Studyや同名Outcome、OutcomeとResultのhierarchy、field/entityとEvidenceのmany-to-many relationship、source factとinterpretationの区別を正規化して保持する。これによりJSON object全体やraw payloadを永続化せず、将来の比較とreferential integrityに必要な構造だけを保存する。

## 4. Schema overview

current databaseのuser tablesは次の10テーブルである。

| Group | Table | Responsibility |
|---|---|---|
| Phase 1 | `literature` | Bibliography、Phase 1 summaries、notes、statusesのmaster |
| Phase 1 | `tags` | Classification/search tags |
| Phase 1 | `literature_tags` | LiteratureとTagのmany-to-many relationship |
| Phase 1 | `usage_history` | Literature usage history |
| Migration | `schema_migrations` | Structured schema migration history |
| Structured | `structured_entities` | Study、Methods、Outcome、Result等のlogical unit |
| Structured | `structured_fields` | Entity内のsource factまたはinterpretation field |
| Evidence | `evidence_references` | Source location、exact quote、verification |
| Evidence link | `structured_field_evidence` | FieldとEvidenceのmany-to-many relationship |
| Evidence link | `structured_entity_evidence` | EntityとEvidenceのmany-to-many relationship |

新しいbibliography table、analysis metadata table、import job table、raw payload table、Research Project tableは作成しない。

## 5. `schema_migrations`

structured schema version管理には`PRAGMA user_version`を使用せず、`schema_migrations`を使用する。

| Column | Rule |
|---|---|
| `version` | `INTEGER PRIMARY KEY` |
| `applied_at` | `TEXT NOT NULL`、UTC ISO timestamp |

current structured schema versionは1である。current migration levelは`MAX(version)`とし、fresh databaseとlegacy migrationの両方でversion 1を1行だけ記録する。

`PRAGMA user_version`はPhase 1でopaque valueとして保持されるため、migration前後、fresh作成、current database再起動のいずれでも変更しない。

## 6. `structured_entities`

`structured_entities`は1 Literature内のlogical research unitを表す。

| Column | Rule |
|---|---|
| `id` | generated SQLite ID |
| `literature_id` | required Literature FK |
| `entity_type` | required closed vocabulary |
| `parent_entity_id` | optional parent entity |
| `sort_order` | non-negative integer、default 0 |
| `verification` | `ai_unverified`または`user_verified` |
| `created_at` / `updated_at` | UTC ISO timestamp |

`entity_type`のclosed vocabularyは次のとおりである。

- `study`
- `method_measurement_imaging`
- `method_body_condition`
- `method_task_protocol`
- `method_analysis`
- `method_validation_statistics`
- `outcome`
- `result`
- `limitation`
- `concept`
- `research_relevance`

同じLiteratureに同じ`entity_type`を複数保存できる。複数Study、複数Outcome、同名Outcomeを許可する。

## 7. Entity hierarchy

`parent_entity_id`はStudy別Methods、Study別Outcome、Outcome配下のResult等を表現する。`result` entityはDB constraintによりparentを必須とする。

`structured_entities`は`(id, literature_id)`をunique keyとして持ち、`(parent_entity_id, literature_id)`から同じkeyへcomposite foreign keyを張る。このため、別Literatureのentityをparentにすることはできない。

DBはparentの`entity_type`が実際に`outcome`か、hierarchyにcycleがないかまでは判断しない。これらのsemantic validationはPhase 2-4 repository層の責務である。

## 8. `structured_fields`

`structured_fields`はentity内のnamed fieldを保存する。

| Column | Rule |
|---|---|
| `id` | generated SQLite ID |
| `literature_id` | required Literature boundary |
| `entity_id` | required same-Literature entity FK |
| `field_key` | required non-blank field name |
| `content_role` | `source_fact`または`interpretation` |
| `value_json` | nullable JSON value text |
| `availability` | source fact availability、またはinterpretationのNULL |
| `verification` | `ai_unverified`または`user_verified` |
| `note` | optional field annotation |
| `created_at` / `updated_at` | UTC ISO timestamp |

同一entity内の`field_key`は`UNIQUE(entity_id, field_key)`とする。複数値が必要なfieldは将来JSON array/objectとして1 fieldに保存できる。Outcomeを複数表現する場合はfieldを重複させず、別Outcome entityを作る。

`field_key`はDBの巨大なclosed vocabularyへ固定しない。`population`、`sample_size`、`body_position`、`tracking_algorithm`、`name`、`definition`、`calculation_method`、`unit`、`condition`、`result`、`statistics`等の正確なvocabularyはPhase 2-4で検証する。このgeneric designがsilent typoをDB単独では拒否しない点は既知の制約である。

## 9. Source fact vs interpretation

`content_role`により原著由来の事実と研究的解釈を分離する。

- `source_fact`: 原著が報告した内容または原著内でのavailability判断。
- `interpretation`: AIまたはuserによる研究的解釈。

Research RelevanceやAI-inferred limitation/conceptをsource factとして保存しない。Evidenceを関連付けても、interpretationが原著の直接記載へ変わるわけではない。

## 10. Availability and verification

`source_fact`のavailabilityは次のclosed vocabularyである。

- `reported`
- `not_reported`
- `not_extracted`
- `unclear`
- `not_applicable`

DBは次のconsistencyを保証する。

| Role/state | `availability` | `value_json` |
|---|---|---|
| `source_fact` + `reported` | required | SQL NULL禁止 |
| `source_fact` + other availability | required | SQL NULL |
| `interpretation` | SQL NULL | SQL NULL禁止 |

structured entity、structured field、Evidenceのverification vocabularyは`ai_unverified`と`user_verified`だけである。これはPhase 1 `literature.verification_status`とは別であり、structured importまたはuser verificationによってPhase 1 statusを自動変更しない。

raw ChatGPT payloadで`user_verified`を受け付けない規則はPhase 2-4 parserの責務である。DBはuser確認後のcanonical保存先として`user_verified`を許可する。

## 11. `value_json`

`value_json`はPython standard library `json`でserializeしたJSON value textを将来保存するためのcolumnである。string、number、boolean、array、objectをflat textへ潰さず保持できる。

SQLite JSON1 extensionには依存しない。schemaは`json_valid()`、`json_extract()`等を使用しない。DBはSQL NULLとnon-NULL textの整合だけを保証し、JSON textとしてdecode可能か、field固有のsemantic typeか、JSON文字列`"null"`が適切かはPhase 2-4で検証する。

## 12. Outcomes and Results

Outcomeは`entity_type = outcome`の独立entityとして保存し、`name`、`definition`、`calculation_method`、`unit`、context等を別々のstructured fieldとして保持する。

Resultは`entity_type = result`とし、`parent_entity_id`でOutcome等の親entityへ関連付ける。Result fieldにはcondition/comparison、result、statistics等を保存できる。DBはResultにparentがあることと同一Literature境界を保証し、parent typeのsemantic correctnessはPhase 2-4で検証する。

## 13. Same-name Outcome handling

Outcome nameにはUNIQUE constraintを置かない。同じLiteratureに、同じ`name` field valueを持つ複数Outcome entityを保存できる。

```text
same Outcome name
!=
direct comparability
```

Condition、body position、region、layer、time point、definition、calculation等が異なる同名Outcomeは別entityとして保持する。Outcome AとOutcome Bの両方が`name = "Synthetic Outcome"`を持つ場合も、各entity内のcontext fieldにより区別できる。

## 14. Evidence

`evidence_references`はstructured informationを原著根拠へ戻すsource referenceを表す。

| Column | Meaning/rule |
|---|---|
| `id` | generated SQLite ID |
| `literature_id` | required Literature FK |
| `pdf_page` | PDF viewer上の1-based integer、NULL可 |
| `printed_page` | 誌面page label、TEXT、NULL可 |
| `section` / `subsection` | source location |
| `table_label` / `figure_label` | exact source label |
| `quote_text` | exact original textの保存先 |
| `note` | Evidence-specific annotation |
| `verification` | `ai_unverified`または`user_verified` |
| `created_at` / `updated_at` | UTC ISO timestamp |

`pdf_page`は0以下またはnon-integerを許可しない。`printed_page`と`pdf_page`を同一と仮定しない。

Evidence rowは`pdf_page`、`printed_page`、`section`、`subsection`、`table_label`、`figure_label`、`quote_text`の最低1つを必要とする。text locatorが空白だけの場合はEvidenceありとみなさず、noteだけのEvidenceも禁止する。DBはquoteの正確性を判断できないため、quote semantic validationはPhase 2-4へ委ねる。

## 15. Evidence link tables

`structured_field_evidence`は次の3 columnsを持つ。

- `literature_id`
- `field_id`
- `evidence_id`

Primary keyは`(field_id, evidence_id)`である。

`structured_entity_evidence`は次の3 columnsを持つ。

- `literature_id`
- `entity_id`
- `evidence_id`

Primary keyは`(entity_id, evidence_id)`である。

Field-level linkは特定値の根拠、entity-level linkはOutcome全体、Result全体、Limitation、Concept等の根拠に使用できる。同じlinkを重複保存しない。

## 16. Referential integrity

各structured tableは`literature_id`を持ち、Literature boundaryを明示する。次のcomposite foreign keysによりcross-Literature referenceをDB levelで禁止する。

- child entity `(parent_entity_id, literature_id)` -> parent entity `(id, literature_id)`
- field `(entity_id, literature_id)` -> entity `(id, literature_id)`
- field link `(field_id, literature_id)` -> field `(id, literature_id)`
- field link `(evidence_id, literature_id)` -> Evidence `(id, literature_id)`
- entity link `(entity_id, literature_id)` -> entity `(id, literature_id)`
- entity link `(evidence_id, literature_id)` -> Evidence `(id, literature_id)`

SQLite connectionは常に`PRAGMA foreign_keys = ON`で開く。Migration後はtestsで`PRAGMA foreign_key_check`が空、`PRAGMA quick_check`が`ok`であることを確認する。

## 17. Cascade behavior

Literature削除時は、対象Literatureのstructured entities、fields、Evidence、link rowsをcascade deleteする。別Literatureのstructured data、Evidence、IDは保持する。

Entity削除時は、子entity、entity内fields、entity Evidence links、および削除fieldに属するfield Evidence linksをcascade deleteする。Evidence本体は保持する。

Evidence削除時はfield/entity link rowsだけをcascade deleteし、entityとfield本体は保持する。

この動作は、Literature削除時に既存`literature_tags`と`usage_history`をcascade deleteするPhase 1 behaviorと整合する。Tag本体や外部PDF fileは従来どおり削除しない。

## 18. Migration strategy

`initialize_database`はnon-internal user tableと`schema_migrations.MAX(version)`をread-onlyで確認してdatabaseを分類する。

Migration 1はPhase 2 structured schema additionであり、structured 6 tablesの`CREATE TABLE IF NOT EXISTS`とversion 1 metadataの`INSERT`だけを単一transaction内で行う。Phase 1 tableを再作成、変更、削除しない。

Migration成功前にrequired current tablesとversion metadataをtransaction内で確認する。SQLまたはintegrity確認が失敗した場合はtransaction全体をrollbackし、version rowやpartially created tableを残さない。

## 19. Pre-migration backup

Legacy Phase 1 databaseは、schema変更より先に`src.backup.create_database_backup`でverified SQLite backupを作成する。

```text
Legacy Phase 1 DB
-> verified backup
-> migration transaction
-> current DB
```

`migration_backup_directory`が指定されない場合、migrationを拒否しschema/dataを変更しない。backup失敗はcallerへ伝播し、migration SQLを開始しない。backup成功後にmigrationが失敗した場合、source DBはrollbackされるが、migration前のverified backupは残す。

通常のapplication startupは`data/`、`exports/`、`backups/`を作成した後、`backups/`を`migration_backup_directory`として渡す。Fresh/current DBではmigration backupを作成しない。

## 20. Fresh, legacy, current, future, and inconsistent DB behavior

| Classification | Detection | Behavior |
|---|---|---|
| Fresh | user table 0件 | current 10 tablesとversion 1を1 transactionで作成。backupなし |
| Legacy Phase 1 | Phase 1必須4 tablesがありversion 1未適用 | backup必須。成功後にMigration 1 |
| Current | version 1適用済み | required 10 tablesを確認。変更・backupなし |
| Future | `MAX(version) > 1` | downgradeせず明確なerror。変更・backupなし |
| Inconsistent current | version 1だがrequired table欠落 | silent repairせず明確なerror。変更・backupなし |
| Unknown existing | user tableはあるがPhase 1必須table欠落 | legacyとみなさず明確なerror。変更・backupなし |

Current databaseのrepeated initializationはschema、data、IDs、migration row、`PRAGMA user_version`、backup file countを変えない。

## 21. JSON contract to schema mapping

Phase 2-4 importerが実装される場合のconceptual mappingは次のとおりである。Phase 2-3はparserやinsertを実装しない。

| JSON contract section | Canonical destination |
|---|---|
| `bibliography` | existing `literature` master。自動overwriteしない |
| `study` | `structured_entities` + `structured_fields` |
| `methods` | 5 method entity types + fields |
| `outcomes` | outcome entities + fields |
| `results` | child result entities + fields |
| `limitations` | limitation entities + source fact/interpretation fields |
| `concepts` | concept entities + source fact/interpretation fields |
| `research_relevance` | research_relevance entity + interpretation fields |
| `evidence` | `evidence_references` + field/entity link tables |
| `analysis_metadata` | Phase 2-3 canonical DBへ永続化しない |
| payload-local IDs | generated SQLite IDsへPhase 2-4でmapし、そのまま保存しない |

Bibliography title、authors、journal、DOI、PMID等をstructured schemaへ二重保存しない。Target Literature matchingとuser confirmationはPhase 2-4 Import Previewの責務である。

## 22. IA to schema mapping

Phase 2-1 Literature Detail IAとの対応は次のとおりである。

| IA area | Data ownership |
|---|---|
| Overview bibliography | existing `literature` |
| Study | study entities/fields |
| Methods | 5 method entity types/fields。既存`methods_note`は別のfree-textとして保持 |
| Outcomes | outcome entities、child result entities、fields |
| Evidence | `evidence_references`とlink tables |
| Research Relevance | research_relevance interpretation fieldsと既存Phase 1 user fields |
| Tags / Usage | existing `tags`、`literature_tags`、`usage_history` |

Header / Status Summaryは独立entityではなく、各ownerから表示するsummary mirrorである。

## 23. Data intentionally not persisted

Phase 2-3では次をcanonical structured schemaへ保存しない。

- bibliographyのduplicate copy
- `analysis_metadata`
- raw ChatGPT JSON payload
- import jobsまたはImport Preview state
- payload-local `outcome_1`、`result_1`、`evidence_1`等
- model confidence score
- Research Project
- guessed identifier、page、quote、fact

保存が必要と後続Stepで判明した場合は、理由、data lifecycle、migration impactを説明し、別のschema変更承認を得る。

## 24. Phase 1 backward compatibility

Phase 1 repository、search、duplicate detection、CSV、backup、CLI、application entrypointはcurrent 10-table database上で既存behaviorを維持する。既存4 tablesのrow、NULL、IDs、column definitions、constraints、foreign keys、indexes、`PRAGMA user_version`をlegacy migration前後で比較するtestsを持つ。

Phase 1-only Literatureをstructured data不足としてinvalidにしない。`key_findings`、`methods_note`、`limitation_note`、`relevance_note`等からstructured rowを推測生成せず、既存fieldの意味を変更しない。

## 25. Known constraints deferred to Phase 2-4

次はDB structural constraintsだけでは安全かつ柔軟に判定できないため、Phase 2-4 parser/repositoryへ意図的に委ねる。

- exact contract version、required JSON shape、unknown field handling
- `json.loads`可能性とfield-specific semantic type
- JSON text `null`とavailabilityのsemantic consistency
- exact `field_key` vocabularyとtypo rejection
- raw ChatGPT payloadでの`user_verified` rejection
- payload-local ID uniquenessとEvidence reference resolution
- Result parentが正しいOutcomeか、hierarchy cycleがないか
- author-reportedとAI-inferred limitation/conceptの正しいrole mapping
- Evidence quoteが正確な原文か
- target Literature matching、overwrite/merge prevention、Import Preview
- user confirmation後だけsaveするCRUD transaction

DB schemaだけでinvalid JSON textやquote accuracyを完全検証したとは扱わない。

## 26. Acceptance criteria

Phase 2-3は次を満たす。

- fresh DBが正確な10 user tablesとmigration version 1を原子的に作成する。
- legacy Phase 1 DBをbackupなしでmigrationしない。
- verified backupがschema変更より先に作成される。
- backup failure時にschema/dataを変更しない。
- migration failure時にpartial tableとversion rowをrollbackし、backupを保持する。
- Phase 1 rows、IDs、table definitions、constraints、`PRAGMA user_version`を保持する。
- current DBのrepeated initializationがidempotentでbackupを増やさない。
- future versionとinconsistent current schemaを変更せず拒否する。
- entity type、verification、field role、availability/value consistency、Evidence page/emptinessをDBで制約する。
- Result parent、same-Literature parent、field/entity Evidence same-Literature linksを保証する。
- 同名Outcomeを別logical entityとして保存できる。
- Literature、Entity、Evidence deletionのcascadeが対象Literatureへ限定される。
- JSON1 extensionと外部dependencyを必要としない。
- protected production dataへ触れず、testsはtemporary SQLite databasesだけを使用する。
- structured CRUD、parser、Import Preview、CLI/GUI、Comparison、Research Project、APIを先行実装しない。
