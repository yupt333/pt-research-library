# Evidence Reference Workflow

## 1. Purpose

本文書は、PT Research Library Phase 2-5におけるEvidence metadataの確認・管理workflowを定める。目的は、保存済みstructured informationから原著の根拠位置とoriginal textへ戻るためのmetadataを、利用者が安全に確認、修正、検証できるようにすることである。

## 2. Scope

Phase 2-5はLiterature別Evidence list/detail、manual create/edit、Evidence verification、structured item backlink、structured itemからEvidenceへのread、same-Literature attach/detach、安全なEvidence削除、CLI menu 12、Literature DetailのEvidence件数summaryを対象とする。

既存`evidence_references`、`structured_field_evidence`、`structured_entity_evidence`とPhase 2-4 repository CRUDを利用する。schema versionは1のまま維持し、新しいtable、column、migrationを追加しない。

## 3. Evidence mental model

利用者が確認する経路は次のとおりである。

```text
Structured information
↓
Evidence
↓
PDF page
↓
Section / subsection
↓
Table / Figure
↓
Original text
↓
verification
```

Evidenceは孤立した引用一覧ではなく、どのOutcome、Result、Method、Concept等を支えるかを双方向に確認するsource referenceである。画面ではinternal Evidence ID、field ID、entity IDではなく、1、2、3の選択番号と研究上のlabelを優先する。

## 4. Evidence fields

Evidenceは`pdf_page`、`printed_page`、`section`、`subsection`、`table_label`、`figure_label`、`quote_text`、`note`、`verification`、created / updated timestampを持つ。

`pdf_page`はPDF viewer上の1-based integerまたは未登録である。`printed_page`と同じ値であるとは仮定しない。note以外のlocatorまたはquoteを最低1つ必要とし、noteだけまたは空白だけのEvidenceは作成できない。locator、page、quoteを推測または自動補完しない。

## 5. Evidence list/detail workflow

利用者はLiteratureを明示選択し、そのLiteratureに属するEvidenceをDB IDではない表示番号で選ぶ。一覧はpdf page、printed page、section、subsection、Table、Figure、quoteの有無、verification、noteの有無を決定的な順序で表示する。

Unknown Literatureは安全に通知し、Evidence 0件は明確なempty stateを表示する。Detailは全Evidence fieldsと低優先のtimestampsを表示し、さらにこのEvidenceが支えるfield/entityを表示する。

## 6. Structured item backlink

Evidence detailは既存link tablesを読み、field linkとentity linkを分けて表示する。Outcomeはnameと保存済みcontext、Conceptはname、Resultはcondition / comparisonとresult、Method fieldはMethods subgroup、field label、stored valueを使用する。情報不足時はentity type等をfallbackにする。

Labelは保存済み情報だけから作り、研究情報を推測しない。同名Outcomeは別entityとして保持し、同名だけを理由に統合しない。Structured item一覧ではentityとfieldごとにlinked Evidence countとlocator summaryを表示し、itemからEvidenceへの逆方向readも可能にする。

## 7. Attach/detach semantics

Manual attach/detachは画面上のEvidence選択番号とstructured item選択番号を使用する。Attachは同一Literature内だけを許可し、cross-Literature linkを拒否する。同一linkのduplicate attachは重複rowを作成しない。

Detachはlink rowだけを削除する。Evidence本体、structured entity、structured field、Literatureは保持する。Attach/detachはEvidence、field、entityのverificationを変更しない。どちらも保存前に明示確認を必要とする。

## 8. Verification workflow

Manual createは必ず`ai_unverified`で保存し、作成時に`user_verified`を選ばせない。

Verification変更は一般編集と分離した専用操作とする。Evidence detailと「原著の該当箇所を確認した場合のみ実行する」警告を表示し、明示確認後だけ`ai_unverified`から`user_verified`へ変更する。`user_verified`から`ai_unverified`へ戻す操作も同じく明示的に行う。

Evidence verificationはEvidence rowだけの状態である。次を自動変更しない。

- `structured_fields.verification`
- `structured_entities.verification`
- `literature.verification_status`
- `literature.ai_summary_status`

## 9. Substantive edit reset rule

現在`user_verified`のEvidenceについて、次のいずれかを実質変更する場合、保存時のverificationを`ai_unverified`へ戻す。

- `pdf_page`
- `printed_page`
- `section`
- `subsection`
- `table_label`
- `figure_label`
- `quote_text`

Previewには「根拠位置または原文を変更するため、確認状態はai_unverifiedへ戻ります」と表示する。noteだけの変更または保存値と同じ値の入力ではverificationを変更しない。

この規則はPhase 2-5 service layerが適用する。既存`structured_repository.update_evidence_reference`のpublic behaviorは変更しない。Preview後にEvidenceが変化していれば保存を拒否し、再確認を求める。

## 10. Deletion safety

削除前にLiterature title、Evidence locator、verification、field link count、entity link countを表示する。次を明示する。

- Evidence本体は削除される。
- Evidence linkも削除される。
- structured entity / field本体は削除されない。
- Literature本体は削除されない。
- PDF fileは削除されない。

確認は、削除手続きを続ける選択とEvidence表示番号の再入力による2段階とする。削除時のschema cascadeは対象Evidenceのlink rowsだけに作用し、unrelated Evidenceを保持する。

## 11. Transaction behavior

List、detail、backlink、structured itemからEvidenceへのreadはconnectionのactive transactionをcommit、rollback、closeしない。

Create、edit、verification change、attach、detach、deleteはcallerのactive transaction中に拒否する。Caller transactionを勝手にcommitまたはrollbackしない。Standalone writeは既存repositoryのvalidationとtransaction behaviorを利用し、validation failure時にpartial writeを残さない。

## 12. Research integrity

`ai_unverified`と`user_verified`を混同しない。`user_verified`は利用者の明示操作なしに生成しない。Evidenceの存在またはverificationだけで論文の採否、structured itemの正しさ、AI summaryの確認状態を決めない。

Locator、quote、bibliographyを推測・生成せず、文献のbibliography、adoption status、Phase 1 statusを自動変更しない。AI由来情報を原著確認済みと判断しない。最終的な文献採否と根拠確認は利用者が行う。

## 13. Zero-cost/local policy

Phase 2-5はPython standard libraryとlocal SQLiteだけを使用し、追加料金は0円である。Network、OpenAI API、Claude API、PubMed / Crossref API、cloud、server、account、login、external dependencyを使用しない。

## 14. Boundary with Phase 2-6

Phase 2-5はEvidence metadataを確認・管理するStepである。`pdf_path`からPDFを開くPhase 2-6 Original PDF Evidence Navigationの詳細は[`ORIGINAL_PDF_EVIDENCE_NAVIGATION.md`](ORIGINAL_PDF_EVIDENCE_NAVIGATION.md)を参照する。Phase 2-6 v1はautomatic page jumpやPDF viewer UI scriptingを行わず、locator表示と利用者による手動page移動案内を扱う。

## 15. Intentionally out of scope

Phase 2-5では次を実装しない。

- PDF open、`pdf_path` navigation、PDF page jump
- PDF parsing、OCR、automatic locator / quote extraction
- ChatGPT automatic invocation、OpenAI / Claude API
- PubMed / Crossref API、network、cloud、server
- account、login、GUI、Web UI
- multi-literature comparison、Outcome comparability judgment
- Research Project、Own Protocol comparison
- schema migration、schema version change、external dependency
- production databaseまたはreal research dataを使うtest

Phase 2-6以降の機能を先行実装しない。
