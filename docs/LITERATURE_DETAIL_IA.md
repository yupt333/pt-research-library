# Literature Detail Information Architecture

## 1. Purpose

本文書は、PT Research Libraryの将来の文献詳細画面について、「何を、どの順番で、どの意味単位として見せるか」をPhase 2-1の正式なInformation Architectureとして固定する。

目的は、database fieldsの一覧ではなく、研究者が1本の論文を理解し、方法とOutcomeを確認し、根拠へ戻り、自分の研究との関係を判断する順序で情報を提示することである。本文書は表示概念と意味上のownershipを定めるが、GUI、database schema、API、永続化語彙は定めない。

正式な表示順序は次のとおりである。

1. Header / Status Summary
2. Overview
3. Study
4. Methods
5. Outcomes
6. Evidence
7. Research Relevance
8. Tags / Usage

Header / Status Summaryは独立したresearch data entityではなく、他のsemantic sectionsが所有する重要情報を画面上部で要約する共通領域である。

## 2. Design principles

優先順位は次のとおりとする。

1. 重要情報へ早く到達できる。
2. 情報がどの意味に属するか分かる。
3. AI由来か、利用者確認済みかを判別できる。
4. Outcomeのdefinitionとcalculationを見落とさない。
5. Structured itemからEvidenceへ、EvidenceからOriginal PDFへ戻れる。
6. 文献が自分の研究にどう関係するか分かる。
7. 空のoptional fieldsを大量に表示しない。

ユーザーへdatabase table、column name、foreign key、internal ID構造、Markdown、`[[link]]`、Properties、graph構造の理解や手動管理を要求しない。内部で複数entityを使う場合も、表示上は研究上の意味単位へ統合する。

PT・運動器研究で重要なbody part、diagnosis、position、joint angle、contraction、load、imaging、probe orientation、ROI、frame rate、tracking、Strain、Displacement、Sliding、ICC、SEM、MDC、MCID、reliability、validationを自然に扱える構造とする。一方で、shoulder、Achilles tendon、ultrasoundのいずれか専用にはせず、観察研究、介入研究、信頼性研究、画像研究、運動学研究へ拡張可能にする。

## 3. User mental model

ユーザーのmental modelは「1本の論文を見る」だけである。

ユーザーは、最初に文献のidentityと状態を把握し、次に論文全体、Study、Methods、Outcomesを理解する。その後、必要に応じてEvidenceとOriginal PDFを確認し、自分の研究との関連、Tags、Usageを確認する。この流れのために内部data modelを理解する必要はない。

現在のCLI詳細表示は、Phase 1 fields、tags、usage historyを完全に確認するPhase 1機能として維持する。将来のProduct Detailは同じ情報を失わず、semantic unitsで直感的に提示する別の表示責務を持つ。

## 4. Information hierarchy

3段階のprogressive disclosureを採用する。

| Level | 内容 | 表示上の役割 |
|---|---|---|
| Level 1 | Header / Status Summary | 最重要情報と相互に異なるstatusを常時確認する |
| Level 2 | 7 semantic sectionsの要約 | 各Sectionの要点と情報の有無を短時間で把握する |
| Level 3 | Section内の詳細 | 定義、条件、結果、根拠等を必要なときに確認する |

固定するのは`重要情報 → Section要約 → 詳細情報`という階層である。accordion、tabs、cards等の具体的なGUI方式は固定しない。

Headerでの再掲は`summary mirror`であり、元情報のprimary ownershipを移動させない。同じ長文や同じ詳細値を複数Sectionへ複製しない。

## 5. Header / Status Summary

文献を開いた瞬間に次を確認可能にする。

- Title（最優先のidentity）
- Authors
- Year
- Journal
- Publication type
- DOI / PMIDの存在
- Original PDFの存在
- Verification status
- AI summary status
- Adoption status
- Rating

Authors、Year、Journal、Publication typeの詳細ownershipはOverviewにあり、Headerでは短いsummary mirrorとする。DOI / PMIDは値を列挙することより存在を知らせ、詳細はOverviewで確認する。`pdf_path`はOverviewが所有し、HeaderではOriginal PDFを開けるかどうかと将来のaction入口を示す。

次のstatusは意味が異なるため、1つの総合statusに統合しない。

| Status | 意味 | Primary ownership |
|---|---|---|
| Verification status | 原著・情報全体の確認状態 | Research Relevance。Headerはsummary mirror |
| AI summary status | AI summary本文の作成・確認状態 | Overview。Headerはsummary mirror |
| Adoption status | 文献の採否判断 | Research Relevance。Headerはsummary mirror |
| Rating | 利用者の重要度評価 | Research Relevance。Headerはsummary mirror |

将来のHeader action候補は`Open Original PDF`、`Add to Comparison`、`Link to Research Project`である。Phase 2-1ではactionを実装しない。Comparisonと独立Research Project modelは後続Stepで実装されたが、Literature Detail Headerからの統合actionはPhase 2-11の責務である。

## 6. Overview

Overviewは論文全体を短時間で把握する領域である。

### Bibliographic / publication information

- Authors
- Journal
- Publication year
- Volume
- Issue
- Pages
- DOI
- PMID
- URL
- Language
- Publication type
- Abstract
- Original PDF locator / availability

Headerと重なる情報は、Headerをsummary、Overviewをdetail sourceとする。通常の閲覧でinternal literature IDをidentityとして強調しない。Created / updated timestampsは必要時に確認する低優先のsystem metadataとする。

### Research summary

- Personal summary
- AI summary
- AI summary status
- Key findings
- Limitation note
- General note

Personal summaryとAI summaryは、出所と確認責任が異なるため明確に分離する。AI summaryにはstatusを隣接表示し、AI由来であることを色だけに依存せず示す。`key_findings`はPhase 1の論文全体に対するfree-text総括として維持し、将来のstructured Outcome Resultsとは同一視しない。

## 7. Study

Studyは「誰を、どのような目的と研究デザインで調べたか」を理解する領域である。将来、次をstructured informationとして表示可能にする。

- Study design
- Research objective / aim
- Population
- Sample size
- Age / demographics
- Condition / diagnosis
- Healthy / patient status
- Inclusion criteria
- Exclusion criteria
- Group allocation
- Study setting

全項目を必須にしない。情報がなければfieldごとの空欄を並べず、Section単位の簡潔なempty stateを示す。

## 8. Methods

MethodsはPT Research Libraryの中心的な差別化領域であり、1枚の巨大なfree-textへ戻さない。次のsubgroupsを共通の意味単位として使用する。

### A. Measurement / Imaging

- Measurement
- Imaging modality
- Imaging condition
- Device
- Probe
- Probe orientation
- Frame rate
- Sampling condition
- Calibration / scale
- ROI（撮像・測定対象としての定義）

### B. Participant / Body Condition

- Body position
- Joint position
- Joint angle
- Limb position
- Contraction type
- Muscle activation condition
- Load
- Weight-bearing condition

### C. Task / Protocol

- Task
- Movement
- Range
- Speed
- Repetition
- Duration
- Rest
- Trial number

### D. Analysis

- Analysis method
- Tracking algorithm
- Preprocessing
- Reference frame / baseline
- Calculation method
- ROI handling（解析時の選択・追跡・集約）
- Quality control / QC

### E. Validation / Statistics

- Validation
- Reliability method
- Statistical analysis
- ICC
- SEM
- MDC
- MCID
- Agreement analysis
- Other statistical method

Phase 1の`methods_note`はlegacy / free-text method summaryとしてMethodsに表示する。これはstructured Methodsの代替ではない。将来structured dataが追加された場合も、`methods_note`を消去・上書きせず、出所の異なる情報として併存させる。

## 9. Outcomes

Outcomesは箇条書きの名称一覧ではなく、`1 Outcome = 1 logical unit`として扱う。各logical outcomeは最低限、次を関連付けて表示可能にする。

- Outcome name
- Outcome definition
- Calculation method
- Unit
- Measurement / analysis context
- Main result
- Statistical result
- Validation information
- Evidence availability
- Verification state

### Outcome name / definition principle

Outcome name、Outcome definition、Calculation methodは別の情報として表示し、nameだけを強調してdefinitionを隠さない。

```text
Outcome: Strain
Definition: method-dependent
Calculation: method-dependent
```

これは一般概念の説明であり、実在文献の値ではない。

```text
同じOutcome名 ≠ 直接比較可能
```

Strain、Displacement、Slidingは異なる力学的指標である。同じStrainでもdefinitionまたはcalculationが異なる可能性がある。Comparisonへ渡すときもname、definition、calculationを一体で扱う。

### Multiple logical outcomes with the same name

Outcome nameを1文献内で一意と仮定しない。Condition、body position、region、layer、time point等が異なる場合、同名でも別のlogical outcomeとして表示する。画面上ではnameに加えてcontextを常に識別でき、誤って統合しない構造を前提とする。Phase 2-1ではidentifierやschemaを定めない。

### Results relationship

将来の目標導線は次のとおりである。

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

Main resultsを単一のfree-textだけに依存させない。Phase 1の`key_findings`はOverviewの総括として維持し、将来のstructured Outcome Resultsとは区別する。Result schemaやtableはPhase 2-1で設計しない。

## 10. Evidence

Evidenceは引用一覧ではなく、structured informationを原著根拠へ戻す領域である。Evidence detailでは次を確認可能にする方向とする。

- どのOutcome / Method / Result等を支えるEvidenceか
- PDF page
- Section
- Table
- Figure
- Original text
- Verification status
- Note

Structured value側にもEvidence availabilityと必要なverification indicatorを示す。理想的な双方向の導線は次のとおりである。

```text
Outcome / Method / Result
↓ Evidence marker
Evidence detail
↓ Open Original PDF
Original PDF
```

Evidence未登録でも値そのものを削除しない。概念上、Evidenceあり、Evidence未登録、Evidence確認済み、Evidence未確認を区別可能にする。ただしmachine-readable status vocabularyはPhase 2-2 / 2-5で決定し、Phase 2-1では新しいenumや永続化形式を確定しない。

Evidence database implementationはPhase 2-5、Original PDF navigation implementationはPhase 2-6の責務である。

## 11. Research Relevance

Research Relevanceは「この論文が自分にとって何を意味するか」を扱う。

Phase 1情報として次を確認可能にする。

- Clinical note
- Relevance note
- Evidence level
- Verification status
- Adoption status
- Exclusion reason
- Rating
- General note内の研究関連情報へのcontextual reference

`general_note`のprimary ownershipはOverviewとし、Research Relevanceでは必要に応じて研究関連内容の存在または参照先を示す。同じ本文を不必要に複製しない。

Linked Research ProjectsはPhase 2-9で独立entityとLiterature many-to-manyとして定義された。これは`usage_history.project_name`とは別であり、Project-local ConceptをLiterature Conceptへ自動mergeしない。Literature Detail内への統合表示・actionはPhase 2-11の責務とし、Phase 2-1のSection ownershipを変更しない。Phase 2-9の詳細は[`RESEARCH_PROJECT_MODEL.md`](RESEARCH_PROJECT_MODEL.md)を参照する。

## 12. Tags / Usage

Phase 1のtagsとusage historyをそのまま利用可能にする。

Tagは分類・検索補助であり、将来のConceptは意味関係・研究知識構造である。両者を混同しない。Concept modelは後続Stepの責務である。

Usage historyでは次を確認可能にする。

- Usage type
- Project name
- Usage note
- Used at

`usage_history.project_name`は使用時の文字列であり、将来のResearch Project entityではない。既存値から暗黙にProjectを生成したり、両者を同一視したりしない。

## 13. Phase 1 field mapping

Primary sectionは1箇所とする。Headerへの再掲や他Sectionからの参照はDisplay roleで明示する。

| Current field | Primary section | Display role |
|---|---|---|
| `id` | Overview | Internal record reference。通常の研究閲覧では強調せず、必要なsystem metadataとしてのみ扱う |
| `title` | Header / Status Summary | Primary identity |
| `authors` | Overview | Bibliographic detail。Headerは短いsummary mirror |
| `journal` | Overview | Publication detail。Headerはsummary mirror |
| `publication_year` | Overview | Publication detail。Headerはsummary mirror |
| `volume` | Overview | Bibliographic detail |
| `issue` | Overview | Bibliographic detail |
| `pages` | Overview | Bibliographic detail |
| `doi` | Overview | Identifier detail。HeaderはDOI availabilityのみsummary mirror |
| `pmid` | Overview | Identifier detail。HeaderはPMID availabilityのみsummary mirror |
| `url` | Overview | External publication / database destination |
| `language` | Overview | Publication detail |
| `publication_type` | Overview | Publication detail。Headerはsummary mirror |
| `abstract` | Overview | Source-provided article overview |
| `pdf_path` | Overview | Original PDF locator。Headerはavailabilityと将来のOpen actionのみsummary mirror |
| `personal_summary` | Overview | User-authored summary。AI summaryとは分離する |
| `ai_summary` | Overview | AI-authored / manually stored summary。出所を明示する |
| `ai_summary_status` | Overview | AI summaryの状態。Headerは独立statusとしてsummary mirror |
| `general_note` | Overview | General free-text note。Research Relevanceから必要時にcontextual reference可能 |
| `key_findings` | Overview | Legacy / free-text whole-paper findings summary。Structured Outcome Resultsとは別物 |
| `methods_note` | Methods | Legacy / free-text method summary。Structured Methodsの代替ではない |
| `clinical_note` | Research Relevance | User's clinical interpretation |
| `limitation_note` | Overview | Whole-paper limitation summary |
| `relevance_note` | Research Relevance | Relevance to the user's research |
| `evidence_level` | Research Relevance | User-recorded evidence assessment |
| `verification_status` | Research Relevance | Overall source / literature verification meaning。Headerはsummary mirror |
| `adoption_status` | Research Relevance | User's adoption decision。Headerはsummary mirror |
| `exclusion_reason` | Research Relevance | Conditional explanation when excluded |
| `rating` | Research Relevance | User's importance rating。Headerはsummary mirror |
| `created_at` | Overview | Low-priority system metadata / audit detail |
| `updated_at` | Overview | Low-priority system metadata / audit detail |

関連するPhase 1 dataも次のownershipを持つ。

| Current related data | Primary section | Display role |
|---|---|---|
| Tag name | Tags / Usage | Classification and search aid |
| Usage `usage_type` | Tags / Usage | Type of recorded use |
| Usage `project_name` | Tags / Usage | Historical free-text project label。Research Project entityではない |
| Usage `usage_note` | Tags / Usage | Context or purpose of use |
| Usage `used_at` | Tags / Usage | Date of use |
| Usage `created_at` | Tags / Usage | Low-priority history metadata |

## 14. Future structured field mapping

ここでいうfieldは表示概念であり、table、SQL column、foreign key、JSON propertyを意味しない。

### Study concepts

| Concepts | Primary section | Display role |
|---|---|---|
| Study design; Research objective / aim | Study | Study type and intent |
| Population; Sample size; Age / demographics | Study | Who was studied and how many |
| Condition / diagnosis; Healthy / patient status | Study | Participant clinical context |
| Inclusion criteria; Exclusion criteria | Study | Eligibility definition |
| Group allocation; Study setting | Study | Study organization and environment |

### Methods concepts

| Concepts | Primary section / subgroup | Display role |
|---|---|---|
| Measurement; Imaging modality; Imaging condition; Device; Probe; Probe orientation; Frame rate; Sampling condition; Calibration / scale; ROI | Methods / Measurement & Imaging | What and how data were acquired |
| Body position; Joint position; Joint angle; Limb position; Contraction type; Muscle activation condition; Load; Weight-bearing condition | Methods / Participant & Body Condition | Physical and loading conditions |
| Task; Movement; Range; Speed; Repetition; Duration; Rest; Trial number | Methods / Task & Protocol | Performed procedure and dose |
| Analysis method; Tracking algorithm; Preprocessing; Reference frame / baseline; Calculation method; ROI handling; QC | Methods / Analysis | Processing and calculation |
| Validation; Reliability method; Statistical analysis; ICC; SEM; MDC; MCID; Agreement analysis; Other statistical method | Methods / Validation & Statistics | Validity, reliability, and inference |

### Outcome concepts

| Concept | Primary section | Display role |
|---|---|---|
| Outcome name | Outcomes | Logical outcome label。単独でcomparabilityを示さない |
| Outcome definition | Outcomes | Meaning of the measured quantity。nameと隣接して可視化する |
| Calculation method | Outcomes | Derivation of the outcome。definitionと分離する |
| Unit | Outcomes | Unit belonging to the same logical outcome |
| Measurement / analysis context | Outcomes | Condition、position、region、layer、time point等を識別する |
| Main result; Statistical result | Outcomes | Result belonging to an outcome and context |
| Validation information | Outcomes | Outcome-specific validity / reliability context |
| Evidence availability; Verification state | Outcomes | 根拠と確認状態への入口 |

### Evidence concepts

| Concept | Primary section | Display role |
|---|---|---|
| Supported structured item | Evidence | Outcome / Method / Result等との意味上の関連を示す |
| PDF page; Section; Table; Figure | Evidence | Source location metadata |
| Original text | Evidence | Source excerpt for confirmation |
| Verification status | Evidence | Source confirmation state。overall verificationとは区別する |
| Note | Evidence | Evidence-specific annotation |

### Research Relevance concepts

| Concept | Primary section | Display role |
|---|---|---|
| Linked Research Projects | Research Relevance | Phase 2-9 Project association。Literature Detail統合表示はPhase 2-11へ委ねる |
| Methodological relevance; Clinical relevance; Protocol relevance | Research Relevance | Relevance by decision type |
| Limitations for own use; Methodological cautions | Research Relevance | Constraints on application |
| Comparison notes | Research Relevance | User interpretation supporting future comparison |

## 15. Empty / missing / unverified information

空のoptional fieldsを`未登録`として大量に並べない。Section単位で情報の有無を示し、情報がなければ1つの簡潔なempty stateを使用する。重要statusは値が既定状態でもHeaderで確認可能にする。

少なくとも次の4つは意味が異なるため、視覚的・意味的に区別する。

| Conceptual state | 意味 |
|---|---|
| Not yet extracted / entered | まだ抽出または入力していない |
| Confirmed absent in source | 原著を確認したが記載がない |
| AI-extracted, unverified | AI抽出済みだが利用者未確認 |
| User-confirmed | 利用者が原著等で確認済み |

これはIA上の意味区別であり、canonical vocabulary、JSON representation、database status valuesを確定するものではない。それらはPhase 2-2以降で定める。

Verificationは次の粒度で見落としにくくする。

- Header: 全体verification summary
- Structured item: 必要な箇所にitem-level indicator
- Evidence detail: source confirmation state
- AI information: user-confirmed informationと区別できるtext label等

具体色は定めず、色だけを状態の唯一の手掛かりにしない。画面全体を一律の警告で埋めず、判断に影響する場所へindicatorを隣接させる。

## 16. Evidence navigation concept

Original PDFが存在する場合、原著確認をprimary research actionとして扱う。Headerから`Open Original PDF`へ到達でき、Outcome、Method、Result等のEvidence markerからEvidence detailを経由して該当根拠とOriginal PDFへ戻れる構造を目標とする。

Evidence Sectionからstructured itemへ逆に戻れる必要もある。これによりEvidenceを文献末尾の孤立した引用一覧にしない。Phase 2-1では導線のみを固定し、PDF page jump、viewer、file handlingは実装しない。

## 17. Comparison readiness

Phase 2-7以降の比較画面と意味を共有しやすいlabelと単位を用いる。特に次を一貫した意味で扱う。

- Population
- Sample size
- Position
- Task
- Load
- Imaging
- ROI
- Tracking
- Outcome
- Outcome definition
- QC
- Validation
- Statistical analysis
- Results

Comparisonへ送る単位はOutcome nameだけではなく、definition、calculation、contextを含むlogical outcomeである。同名Outcomeを自動統合しない。Phase 2-1ではComparison UI、比較可能性判定、comparison statusを実装しない。

Phase 2-7で実装するcomparison read modelと表示境界は[`MULTI_LITERATURE_COMPARISON.md`](MULTI_LITERATURE_COMPARISON.md)を参照する。

## 18. Action hierarchy

将来のLiterature Detailにおける優先順位を次のように設計する。

### Primary research actions

1. Original PDFを確認する。
2. Evidenceを確認する。
3. Comparisonへ追加する。
4. Research Projectへ関連付ける。

### Secondary actions

- 文献情報を編集する。
- Summary / noteを編集する。
- Tagsを管理する。
- Usage historyを管理する。
- Exportする。

### Destructive action

- Delete

Deleteをprimary actionへ置かず、他のresearch actionsから視覚的・操作上分離する。Phase 2-1ではいずれのactionも実装しない。特にComparisonとResearch Projectは未実装である。

## 19. Backward compatibility

Phase 1-only literatureを、Phase 2 structured dataがないことを理由に表示不能にしない。

- Phase 1 data only: Literature fields、tags、usage historyを使って正常に詳細表示する。
- Phase 1 + Phase 2 data: 既存情報を保持したまま、対応するstructured sectionsを追加表示する。

Structured dataがないStudy、Outcomes、Evidence等は簡潔なSection empty stateを使う。`methods_note`があればMethodsで表示する。Phase 1 fieldsを新しいstructured fieldsへ暗黙変換せず、既存値の意味を変更しない。

現在のCLI詳細表示はPhase 1の完全情報確認手段として維持する。Phase 2-1では`src/cli.py`を変更せず、本格Product UIの実装は後続Stepとする。

## 20. Low-fidelity wireframes

以下は情報の順序と意味上の関係だけを示す。layout、component、色、icon、frameworkは固定しない。例はすべて明示的な合成例であり、実在する文献、著者、journal、DOI、PMID、URLを表さない。

Wireframe内の状態表現は意味の違いを示すための説明用labelであり、canonical vocabularyや永続化値ではない。

### Case A: Phase 1 existing literature / no structured data

```text
┌ Header / Status Summary ──────────────────────────────┐
│ Synthetic Literature — Phase 1 Example               │
│ Bibliographic summary（登録済みの項目だけ）            │
│ Verification: 未確認 | AI summary: 未作成             │
│ Adoption: 未判定 | Rating: 未評価 | Original PDF: なし │
└───────────────────────────────────────────────────────┘

Overview
  Personal summary（登録済みなら表示）
  Abstract / key findings / limitation / general note（存在分だけ）
  Bibliographic details（存在分だけ）

Study
  構造化されたStudy情報はまだありません

Methods
  Phase 1 methods note（存在する場合）
  構造化されたMethods情報はまだありません

Outcomes
  構造化されたOutcome情報はまだありません

Evidence
  Evidenceはまだ登録されていません

Research Relevance
  Relevance / clinical note / adoption / rating（存在分だけ）

Tags / Usage
  Tags（0件なら短いempty state）
  Usage history（0件なら短いempty state）
```

### Case B: Structured data with Outcomes / Evidence

```text
┌ Header / Status Summary ─────────────────────────────┐
│ Synthetic Literature — Structured Example           │
│ Bibliographic summary（合成placeholder）              │
│ Verification: 一部確認 | AI summary: 確認済み        │
│ Adoption: 採用候補 | Rating: 4 | Original PDF: あり  │
│ [Open Original PDF] [Add to Comparison: 未実装]      │
└──────────────────────────────────────────────────────┘

Overview — summary + publication details

Study — design / objective / population / sample summary

Methods
  A. Measurement / Imaging — imaging, device, probe, ROI
  B. Participant / Body Condition — position, angle, load
  C. Task / Protocol — movement, speed, trials
  D. Analysis — tracking, calculation, QC
  E. Validation / Statistics — reliability, ICC / SEM等

Outcomes
  ┌ Logical Outcome 1 ────────────────────────────────┐
  │ Name: Synthetic Outcome                           │
  │ Definition: 合成例の定義（常に見える）            │
  │ Calculation: 合成例の算出方法                     │
  │ Context: Condition A | Unit: synthetic unit       │
  │ Result / Statistics                               │
  │ Evidence: 2件 | Verification: 確認済み            │
  └───────────────────────────────────────────────────┘
  ┌ Logical Outcome 2 ────────────────────────────────┐
  │ Name: Synthetic Outcome（同名でも別unit）          │
  │ Definition / Calculation                          │
  │ Context: Condition B                              │
  │ Evidence marker → Evidence detail                 │
  └───────────────────────────────────────────────────┘

Evidence
  Supported item → page / section / table / figure
                 → original text / verification / note
                 → Open Original PDF

Research Relevance — methodological / clinical relevance
Tags / Usage — existing tags and usage history
```

### Case C: Partially AI-extracted / unverified information

```text
┌ Header / Status Summary ─────────────────────────────┐
│ Synthetic Literature — AI Review Example            │
│ Verification: AI由来情報に要確認項目あり             │
│ AI summary: 未確認 | Adoption: 未判定 | Rating: 未評価│
│ Original PDF: あり [Open Original PDF]              │
└──────────────────────────────────────────────────────┘

Overview
  Personal summaryは空のため表示しない
  AI summary [AI由来・未確認]

Study
  Population [AI由来・未確認]
  Sample size [まだ抽出していない]
  Study setting [原著確認済み・記載なし]

Methods
  Imaging condition [AI由来・未確認] → Evidence marker
  Position [利用者確認済み]
  その他の空fieldは列挙しない

Outcomes
  Name: Synthetic Outcome [AI由来・未確認]
  Definition: 要確認（nameの近くに表示）
  Calculation: まだ抽出していない
  Evidence: 未登録

Evidence
  AIが示したsource location [未確認] → Original PDF

Research Relevance / Tags / Usage
  登録済み情報だけを表示し、空Sectionは短いempty state
```

## 21. Out of scope for Phase 2-1

Phase 2-1では次を決定または実装しない。

- SQLite table design、SQL schema、foreign key design、migration
- JSON import contract、parser、API、OpenAI API
- GUI framework、SwiftUI、Tkinter、Web frameworkの選定
- Exact colors、icons、typography、pixel layout
- PDF parsing、Evidence database、PDF navigation
- Comparison UI / implementation / automatic comparability judgment
- Research Project model / implementation
- Concept model
- New status enumまたはcanonical persistence vocabulary
- Production code、tests、dependenciesの変更

## 22. Acceptance criteria

- Header / Status Summaryと7 semantic sectionsの順序とownershipが明確である。
- Headerがresearch data entityではなくsummary mirrorである。
- 重要情報、Section summary、detailの3 levelsが定義されている。
- Phase 1 Literatureの全fieldsにPrimary sectionとDisplay roleがある。
- Existing tagsとusage historyが保持され、Project modelと混同されない。
- StudyとMethodsのPT研究向けsemantic structureが定義されている。
- Methodsが5 subgroupsに分かれ、`methods_note`とstructured Methodsが共存できる。
- Outcomeがlogical unitであり、name、definition、calculationが分離される。
- 同名Outcomeを一意または直接比較可能と仮定しない。
- Outcome / condition / result / statistics / Evidenceの将来導線がある。
- Evidence availability、verification、Original PDFへの導線がある。
- 未抽出、原著に記載なし、AI未確認、利用者確認済みの違いが定義されている。
- 色だけに依存せずAI情報と確認済み情報を区別する。
- Empty optional fieldsを大量表示せず、Section empty stateを使う。
- Phase 1-only literatureを正常に表示できる。
- Comparison-ready labelsを共有し、Outcome nameだけで比較しない。
- Action hierarchyでDeleteがprimary actionになっていない。
- 3つのlow-fidelity wireframesがあり、実在しないbibliographic identifiersを生成していない。
- Database structure、Markdown、link syntax、Properties、graph構造をユーザーへ要求しない。
- Obsidian note templateの再実装ではなく、PT Methods、Outcome definition、Evidence、verification、comparison readiness、Research Relevanceが中心である。
- Phase 2-2以降のschema、import、Evidence、Comparison、Project、GUIを先行実装していない。
