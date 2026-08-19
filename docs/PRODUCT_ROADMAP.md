# PT Research Library — Product Direction and Roadmap

## 1. 文書の位置付け

本文書は、Phase 2以降の製品方向、設計原則、既定の開発順序を定める。

- Phase 1: completed and pushed
- Phase 1 completion assessment: GO
- Phase 1 final regression: 504 tests passed
- Current phase: Phase 2
- Current step: Phase 2-2 ChatGPT Structured Import Contract v1
- Current step status: in progress

Phase 2-0とPhase 2-1はcompleted and pushedである。Phase 2-2は文書化だけを行うStepであり、production code、データベーススキーマ、parser、Import UI、API、テストは変更せず、Phase 2-3以降の機能を実装しない。

## 2. Product Goal

PT Research Libraryは、単なるPDF管理・AI要約アプリではない。

最終目標は、次の研究支援システムである。

> 理学療法・運動器研究に必要な文献整理、比較、根拠確認、研究プロジェクトとの関連付けを、できるだけ少ない操作で行える研究支援システム

肩関節、超音波画像、Acromiohumeral Distance（AHD）、棘上筋、speckle tracking、measurement reliabilityなど、理学療法・運動器研究で重要となる方法、条件、定義、検証情報を扱えることを製品の専門性とする。

## 3. Differentiation

中心となる差別化は次の6点である。

1. 少ない操作
2. PT・運動器研究向けの構造化
3. 根拠付きAI抽出
4. 方法・定義を考慮した文献比較
5. 自分の研究との関連付け
6. 原著へすぐ戻れること

次の機能だけでは差別化とみなさない。

- PDF保存
- AI要約
- タグ
- Markdown
- ChatGPT連携だけ

新機能は、汎用ツールの再実装ではなく、PT研究で手作業が複雑、面倒、または間違いやすい部分を減らす方向で評価する。

## 4. ChatGPTとアプリケーションの役割分担

初期・中期段階では、AIをアプリ内へ直接内蔵しない。

### 4.1 ChatGPTの責務

ChatGPTは「読む・考える」を担当する。

- PDF読解
- 文献要約
- 書誌確認
- 研究目的抽出
- Population / Methods / Results整理
- Limitations抽出
- Concepts抽出
- 複数文献の解釈
- 自分の研究との関連検討

### 4.2 PT Research Libraryの責務

PT Research Libraryは「整理する・比較する・根拠を保持する・研究につなげる」を担当する。

- PDF管理
- 構造化保存
- ChatGPT解析結果取り込み
- 検索・絞り込み
- 比較
- Evidence provenance保持
- Project関連付け
- 文献・概念・研究の関係管理
- 現在地・未解決課題・次のaction管理

この分担により、AIの回答自体を確定情報とせず、利用者が確認可能な研究資産として整理する。

## 5. Cost / API Policy

### 5.1 Phase 2の基本方針

Phase 2ではOpenAI APIを導入しない。基本フローは次のとおりとする。

```text
PDF
↓
ChatGPTで解析
↓
統一フォーマット
↓
Import Preview
↓
利用者確認
↓
PT Research Libraryへ保存
```

追加OpenAI API費用は0円を基本とする。

ユーザーの明示承認なしに、次を導入しない。

- OpenAI API
- Claude API
- その他有料AI API
- cloud database
- 有料server
- 有料SaaS
- 有料dependency
- その他継続費用が発生するサービス

### 5.2 Cost Gate

追加費用が発生する選択肢は、実装前に次を提示し、ユーザーの明示承認を得る。

1. 費用
2. 必要性
3. 無料代替案
4. 想定利用量
5. 採算性

将来AI料金設計を行う場合は、次の残額が負にならないことを条件とする。

```text
販売価格
- store手数料
- AI API費
- server費
- その他変動費
```

安易なAI無制限プランを前提にしない。

## 6. Obsidian Gate

すべての新機能について、実装前に次を確認する。

> これはChatGPT＋Obsidianですでに簡単に実現できないか？

数操作で簡単に実現でき、PT Research Library固有の価値が小さい場合は、原則として自作の優先度を下げる。

優先するのは、次のいずれかに該当する機能である。

- 手作業では複雑、面倒、または間違いやすい
- PT研究特有の構造を扱う
- Evidence provenanceが必要
- 方法または定義の比較が必要
- 複数文献横断が必要
- 研究プロジェクトとの関連が必要

Obsidianからは、文献・Concept・Projectの関係、backlinks的関連、Properties的構造データ、Research Hub、local file managementを参考にする。ただし、ユーザーにMarkdown記述、`[[link]]`手入力、Properties手動管理、folder設計、graph手動構築、plugin依存操作を要求しない。内部構造をユーザーに意識させない。

## 7. Phase 1 Preservation

Phase 1のproduction behaviorはPhase 2以降の基盤として保護する。

保護対象は次のとおりである。

- `literature`
- `tags`
- `literature_tags`
- `usage_history`
- repository validation
- search
- duplicate detection
- CSV
- SQLite backup
- CLI
- application entrypoint

Phase 2では、既存構造を壊さず、新しい構造を追加することを原則とする。

次を禁止する。

- destructive migration
- 既存`literature`データの自動削除
- 既存IDの再生成
- Phase 1 fieldsの意味変更
- 既存AI statusの意味変更
- backupなしのschema migration
- migration testなしのschema変更

将来スキーマを変更するStepでは、backward compatibility、existing data preservation、migration test、foreign key integrity、backup strategy、rollback / failure behaviorを必ず検証する。

## 8. Structured Data Principles

現在の`literature`を文献マスターとして維持する。PT研究用情報を`literature` tableへ大量のflat columnsとして無制限に追加しない。

次を独立構造として扱う方向を固定する。これはconceptual modelであり、テーブル名やスキーマをPhase 2-0で確定または実装するものではない。すべての論文に全項目を要求せず、存在する情報だけを構造化可能とする。

### A. Literature master

- Title
- Authors
- Year
- Journal
- DOI
- PMID
- Abstract
- Original PDF
- 既存Phase 1情報

### B. Study information

- Study design
- Population
- Sample size
- Inclusion criteria
- Exclusion criteria

### C. Methods / conditions

- Measurement
- Imaging condition
- Body position
- Task
- Joint angle
- Contraction type
- Load
- Ultrasound device
- Probe orientation
- ROI
- Frame rate
- Analysis method
- Tracking algorithm
- QC
- Validation
- Statistical analysis

### D. Outcomes

- Outcome name
- Outcome definition
- Calculation method
- Unit
- Main result
- Statistical result
- Validation information

### E. Evidence references

- PDF page
- Section
- Table
- Figure
- Original quote / text
- Verification status
- Note

### F. Concepts

- AHD
- Strain
- Displacement
- Sliding
- ICC
- SEM
- MDC
- Reliability
- Validation
- その他の研究概念

### G. Research projects

- Project name
- Research objective
- Current status
- Literature
- Concepts
- Unresolved questions
- Next actions
- Protocol / method notes

## 9. Outcome Definition Principle

`Outcome name`と`Outcome definition / calculation method`は必ず分離して管理する。

例えばOutcome名が`Strain`であっても、2点間距離変化、displacement gradient、affine deformation、speckle block間相対変位は同一の測定量とは限らない。

```text
同じOutcome名 ≠ 直接比較可能
```

Displacement、Strain、Slidingも異なる力学的指標として扱う。将来の比較機能は数値の近さだけを比較基準にせず、最低限、measurement condition、definition、calculation、imaging condition、analysis algorithm、validationを確認して比較可能性を判断する。

## 10. Evidence Provenance

AI抽出値は、可能な限り原著根拠へ戻れるようにする。目標となる追跡経路は次のとおりである。

```text
Structured value
↓
Evidence reference
↓
PDF page
↓
Section
↓
Table / Figure
↓
Original text
```

例えば`Strain = 3.2%`という値だけで終わらせず、ページ、Resultsなどのsection、Table / Figure、original textを関連付けられる構造を目指す。

AI回答を無条件に確定値にしない。AIの重要な役割は、原著のどこを確認すべきかを案内することである。AI抽出情報にはverification状態を持たせ、利用者が確認済みかどうかを区別する。

## 11. Multi-Literature Comparison

複数文献を選択し、同一項目を横並びに比較できる機能をPhase 2の主要機能とする。

代表比較項目は次のとおりである。

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
- Main results

比較可能性状態として、`directly comparable`、`partially comparable`、`not directly comparable`、`needs review`を将来導入する方向とする。ただしPhase 2初期ではAIによる自動判定を必須とせず、構造化情報を正しく横並びにできることを優先する。

## 12. Research Projects and Own Research

### 12.1 Research Project

現在の`usage_history.project_name`は使用履歴の文字列として維持する。Research Projectの代替として拡張せず、将来のResearch Projectは独立エンティティとして扱う。

1文献は複数Projectへ関連可能とする。Projectは、research objective、current status、linked literature、important concepts、unresolved questions、next actions、protocol、methodological decisionsを管理可能にする。

### 12.2 Own Protocol vs Literature

登録済みResearch Project / Protocolと論文Methodsを比較できる方向とする。比較対象は、一致点、相違点、direct comparability、methodological cautions、usable elements、changes to considerである。

先行研究値に近いという理由だけで解析方法や研究方法を採用せず、方法、定義、撮像条件、解析方法、Validationを優先して判断する。

## 13. UI / UX Principles

最優先原則は、ユーザーに内部構造を意識させないことである。理想操作は次のとおりとする。

```text
PDFを登録
↓
ChatGPTで解析
↓
解析結果を確認
↓
取り込む
↓
比較
↓
Research Projectへ関連付け
↓
必要ならEvidenceから原著へ戻る
```

ユーザーにMarkdown、database table理解、internal link syntax、folder構築、graph構築、property設定を原則として要求しない。

Phase 2-1ではGUI frameworkを導入せず、文献詳細を次の意味単位へ整理するinformation architectureを固定する。

- Header / Status Summary（独立したresearch data entityではなく、画面上部のsummary領域）
- Overview
- Study
- Methods
- Outcomes
- Evidence
- Research relevance
- Tags / Usage

表示階層は`重要情報 → Section要約 → 詳細情報`とする。内部のdatabase table、column、foreign key、ID構造をユーザーへ露出せず、Outcome nameとdefinition / calculation、Evidence availability、AI情報のverification、未抽出・原著に記載なし・AI未確認・利用者確認済みの違いを見落としにくくする。Phase 1 fieldsだけを持つ文献も正常に表示できなければならない。詳細は[`LITERATURE_DETAIL_IA.md`](LITERATURE_DETAIL_IA.md)を参照する。

本格GUIはStructured Data、Evidence、Comparisonのデータ構造が固まった後に実装する。CLIを最終製品UIとはみなさない。

## 14. Phase 2 Roadmap

Phase 2では次の順序を既定の開発順序として固定する。Stepを飛ばして後段機能を先行実装しない。

### Phase 2-0: Product direction / roadmap freeze

Status: completed and pushed

- 製品方向、設計原則、ロードマップの文書化
- docs / specification / workflowのみ
- production変更なし

### Phase 2-1: Literature Detail Information Architecture

Status: completed and pushed

- 文献詳細の意味単位設計
- Header / Status Summary + Overview / Study / Methods / Outcomes / Evidence / Research Relevance / Tags / Usage
- Phase 1全fieldsと将来structured fieldsの表示mapping
- progressive disclosure、empty state、verification、Evidence / Original PDF導線
- Phase 1-only文献とのbackward compatibility
- 3ケースのlow-fidelity detail wireframe
- UI表示要件の固定
- documentation onlyのschema実装前設計Step

### Phase 2-2: ChatGPT Structured Import Contract v1

Status: current step, in progress

- contract version付きJSON v1によるChatGPT出力の統一
- bibliography / study / methods / outcomes / results / limitations / concepts / research relevance / evidence
- availabilityとverificationを分離したmissing / unverified semantics
- payload-local IDsとEvidence reference integrity
- 完全合成のvalid JSON exampleと手動ChatGPT出力指示
- documentation only。OpenAI API、database schema、parser、Import Preview implementationなし

### Phase 2-3: Structured Research Data Model

Status: planned

- Study / Methods / Outcomes / Evidenceとの関連
- conceptual modelをSQLite schemaへ落とす
- migration設計
- Phase 1 data preservation
- migration tests必須

### Phase 2-4: Structured Data Repository + Import Preview

Status: planned

- 新構造のCRUD
- ChatGPT結果parser
- validation
- Import Preview
- user confirmation後にsave
- AI情報は未確認として保存
- APIなし

### Phase 2-5: Evidence Reference

Status: planned

- page / section / table / figure / original text / verification
- structured itemとevidenceの関連付け

### Phase 2-6: Original PDF Evidence Navigation

Status: planned

- `pdf_path`とEvidenceを接続
- page等から原著確認へ戻れる導線
- PDF解析の自動化はまだ行わない

### Phase 2-7: Multi-Literature Comparison Matrix

Status: planned

- 複数文献選択
- 同一structured fieldsの横並び
- PT研究Methods中心の比較

### Phase 2-8: Outcome Comparability

Status: planned

- Outcome nameとdefinitionを分離した比較
- calculation / imaging / algorithm / validationを表示
- directly / partially / not directly / needs reviewの概念導入を検討
- AIによる自動判定は必須にしない

### Phase 2-9: Research Project Model

Status: planned

- Project独立管理
- Literatureとのmany-to-many
- Concepts / objective / current status / unresolved issues / next action / protocol notes

### Phase 2-10: Own Protocol vs Literature Comparison

Status: planned

- 自分の研究条件と先行研究のstructured comparison
- 一致点 / 相違点 / methodological caution / direct comparability

### Phase 2-11: Integrated Research Workflow

Status: planned

- Literature Detail / Import / Evidence / Comparison / Projectを一連の操作として統合
- 操作数を減らす
- Phase 2 UI workflow validation

### Phase 2-12: Phase 2 Integration / E2E / Completion Gate

Status: planned

- migration persistence
- backward compatibility
- E2E
- regression
- data safety
- Phase 2 completion assessment

## 15. Phase 3 Roadmap

Phase 3は「製品UI・知識関係・AI assisted workflow」を中心とする。順序はPhase 2終了時に再評価できるが、大枠は次のとおりとする。

### Phase 3-1: Local GUI / Product UI v1

- 初心者が内部構造を意識しないUI
- Literature / Compare / Projects / Evidenceを中心とする

### Phase 3-2: Concept Relations / Backlinks / Research Hub

- 文献・Concept・Projectの関係
- 自動関連表示
- ユーザーによるMarkdown link不要

### Phase 3-3: ChatGPT Comparison Bridge

- 選択文献のstructured contextをChatGPTへ渡しやすい形式で出力
- ChatGPT回答を必要に応じて再取り込み
- APIなしを基本とする

### Phase 3-4: Natural-Language Research Assistance

- 自然言語検索
- AI-assisted comparison
- Research question support
- APIなしで可能な範囲を先に検証

### Phase 3-5: Optional Export

- Markdown / Obsidian export
- 必要性が確認できた場合のみ

## 16. Phase 4 Automation Gate

完全自動化はPhase 4以降の候補とする。

検討候補は、PubMed integration、Crossref integration、automatic bibliographic retrieval、PDF parsing、automatic AI analysis、OpenAI API、cloud servicesである。

「自動化できるから導入する」ことは禁止する。Phase 2 / 3の実利用により、次のすべてを確認できた場合だけ検討する。

- 手作業がボトルネックである
- automationによる価値が十分である
- costが許容可能である
- free alternativeでは不足する

有料サービスは、ユーザーの明示承認なしに導入しない。

## 17. Roadmap Change Rules

本文書のロードマップを今後のdefault development planとする。検証結果により変更が必要な場合は、変更前に次を説明する。

1. なぜ変更が必要か
2. 既存仕様への影響
3. データへの影響
4. テストへの影響
5. 費用への影響

ユーザーの明示承認なしにPhase全体の優先順位を大きく変更しない。Stepを飛ばして後段機能を先行実装しない。
