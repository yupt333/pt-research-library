# Research Project Model

## 1. Purpose

本文書は、PT Research Library Phase 2-9のResearch Project model、SQLite schema version 2、repository、CLI、migration safety、research integrityを定める。

目的は、文献の保存とは独立して、利用者自身の研究をProject単位で整理し、研究目的、現在の状態、関連文献、重要概念、未解決課題、次のaction、protocol noteをlocal SQLiteへ安全に保持することである。

## 2. Scope

Phase 2-9の対象は次のとおりである。

- 独立したResearch Project CRUD
- ProjectとLiteratureのmany-to-many relationship
- Project-local Concept、Unresolved Question、Next Action
- free-text objective、current status、protocol note、general note
- additive schema migration 1から2
- migration前backup、failure rollback、inconsistent schema rejection
- CLI main menu 14とProject管理submenu

## 3. Research Project mental model

Research Projectは「利用者自身の1つの研究」を表す。例としてAHD研究、アキレス腱Speckle Tracking、サルカス定量化等を管理できるが、これらは説明用の研究名であり、文献書誌情報ではない。

Projectは永続的なIDを持ち、名称を変更しても同じProjectとして存続する。1つのProjectは複数Literatureと関連でき、1つのLiteratureも複数Projectへ関連できる。

## 4. Difference from `usage_history.project_name`

`usage_history.project_name`はPhase 1から存在する「その使用履歴に保存されたfree-text project name」である。Research Projectとは別entityであり、次を行わない。

- migration、rename、semantic change
- Research Project IDの格納またはforeign key化
- 既存文字列からのProject自動生成
- name一致によるauto-linkまたはauto-conversion

両方に同じ文字列があっても、同じentityであるとは扱わない。

## 5. Schema v2

正式なschema metadataは引き続き`schema_migrations`で管理する。`CURRENT_SCHEMA_VERSION`は2であり、fresh databaseはversion historyとして1と2を記録する。`PRAGMA user_version`は使用・変更しない。

Version 2はversion 1の10テーブルを維持し、次の3テーブルをadditiveに追加する。

- `research_projects`
- `research_project_literature`
- `research_project_items`

Current databaseは合計13 user tablesを持つ。

## 6. Project fields

`research_projects`は次を保持する。

| Field | Rule |
|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `name` | required、trim後に空文字禁止、ASCII英字case-insensitive unique |
| `objective` | nullable free text |
| `current_status` | nullable free text。status enumを使用しない |
| `protocol_note` | nullable free text |
| `general_note` | nullable free text |
| `created_at` / `updated_at` | required UTC ISO timestamp |

Repositoryはnameをtrimして保存し、optional textは`None`または`str`だけを受け付ける。

## 7. Literature many-to-many

`research_project_literature`は`project_id`、`literature_id`、`created_at`を持ち、`(project_id, literature_id)`をprimary keyとする。同一pairは1行だけである。

両foreign keyは`ON DELETE CASCADE`である。

- Project削除: linkだけを削除し、Literature本体を保持する。
- Literature削除: linkだけを削除し、Project本体を保持する。

関連付けではLiteratureのverification、adoption、usage history、structured dataを変更しない。

## 8. Project items

`research_project_items`は次を保持する。

| Field | Rule |
|---|---|
| `id` | generated persistent ID |
| `project_id` | required Project FK、`ON DELETE CASCADE` |
| `item_type` | `concept`、`unresolved_question`、`next_action`のいずれか |
| `content` | required、trim後に空文字禁止 |
| `note` | nullable text |
| `sort_order` | non-negative integer、bool禁止 |
| `created_at` / `updated_at` | required UTC ISO timestamp |

新規itemの`sort_order`は、同じProject・同じitem type内の最大値の次をrepositoryが自動採番できる。表示順はtypeごとに`sort_order ASC, id ASC`である。

## 9. Project-local Concepts

`research_project_items.item_type = concept`は、そのResearch Projectで利用者が重要と考えるProject-local Conceptである。

`structured_entities.entity_type = concept`はLiteratureに属するstructured Conceptであり、別entityである。Phase 2-9では名称一致によるmerge、link、backfill、自動関連付けを行わない。両者の関係は将来のConcept Relations / Backlinksの責務である。

## 10. Protocol note boundary

Phase 2-9のown protocolは`research_projects.protocol_note`というfree textだけである。Structured protocol field、methods matching、comparability、protocol recommendationは追加しない。

## 11. CRUD semantics

Projectは作成、ID取得、ID順一覧、部分編集、削除が可能である。Project nameの同値・case-only duplicateは拒否する。Project itemは作成、取得、group表示、content/note/order編集、単独削除が可能であり、Phase 2-9ではitem typeを編集しない。type変更はdelete後にcreateする。

CLIの作成・編集・関連付け・解除・item変更はPreviewと明示確認後だけ保存する。Project削除はimpact表示、続行確認、Project ID再入力の二段階確認を必要とする。

## 12. Delete cascade semantics

Project削除で削除できるのは次だけである。

- 対象`research_projects` row
- 対象Projectの`research_project_literature` links
- 対象Projectの`research_project_items`

Literature、structured entities/fields、Evidence、tags、literature tags、usage history、PDF、他Project、他Projectのlinks/itemsは削除または変更しない。

## 13. Transaction behavior

Project repositoryの全write APIは、呼出開始時に`connection.in_transaction`がtrueなら、安全に`ValueError`で拒否する。caller transactionをcommit、rollback、closeしない。

Read APIはactive caller transactionでも使用でき、commit、rollback、closeしない。通常writeはconnection transaction contextでatomicに実行し、SQLite failure時はrollbackする。

## 14. Migration / backup

Fresh databaseはbackupなしでcurrent 13 tablesをatomicに作成し、migration history 1、2を記録する。

Clean version 1 databaseは、Project tableが存在しないこととversion 1の必須table/historyを確認し、`create_database_backup()`によるverified backupを1回作成した後だけmigration 2を開始する。Migration 2は3 table作成とversion 2 metadataを1 transactionで行う。

Legacy Phase 1 databaseはschema変更前に1回backupし、migration 1をcommit後、migration 2へ進む。Migration 2が失敗した場合、migration 1が成功済みなら有効なversion 1として残り、次回安全に1から2を再試行できる。

Migration 2 failureではpartial Project tableとversion 2 metadataをrollbackする。Backup directory未指定、backup failure、future schema、versionとrequired table/historyの不一致、version 1なのにProject tableが存在する状態は変更せず`DatabaseSchemaError`等で停止する。Silent repairとmetadata-only repairを行わない。

## 15. Research integrity

Project objective、status、note、itemは利用者自身の研究整理情報であり、文献のfact、Evidence、原著記載として扱わない。Project入力からbibliography、DOI、PMID、Evidence、structured field、verification、adoptionを生成または変更しない。

Literatureの採否、原著確認、研究解釈の最終判断は利用者に属する。

## 16. Zero-cost/local policy

Phase 2-9はPython standard libraryとlocal SQLiteだけを使用する。Network、OpenAI / Claude / PubMed / Crossrefその他のAPI、cloud、server、account、external dependency、追加料金を導入しない。

Testsはtemporary directory、temporary SQLite、synthetic Literature / structured data / Projectだけを使用し、production database、実文献、実PDF、実研究dataへ触れない。

## 17. Boundary with Phase 2-10

Phase 2-10はOwn Protocol vs Literature Comparisonを扱う。Phase 2-9では次を先取りしない。

- Own Protocol structured fields
- Project protocolとLiterature Methodsのautomatic comparison
- match / difference / methodological caution生成
- direct comparability判断または保存
- methods recommendation、AI suggestion、自動next action

## 18. Intentionally out of scope

- Project ConceptとLiterature Conceptの自動link / merge
- `usage_history.project_name`との変換または同期
- Project自動作成、自動Literature link
- Protocolのstructured schema
- Project vs Literature comparison
- AI suggestions、recommendation、automatic action
- GUI、Web UI、API、network、cloud、external dependency
- Production database、real literature、real PDF、real research dataの処理
