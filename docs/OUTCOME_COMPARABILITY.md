# Outcome Comparability

## 1. Purpose

本文書は、PT Research Library Phase 2-8におけるpairwise Outcome comparability workflowを定める。目的は、異なるLiteratureに属する2つのOutcomeを直接比較してよいかを利用者が判断できるよう、保存済みのOutcome定義、context、Methods、validation、Evidence metadataを一画面に整理して提示することである。

## 2. Scope

Phase 2-8は、異なる2件のLiteratureの検証、各Literature内のOutcome一覧と明示選択、Outcome Comparability Profile、manual comparability status、CLI main menu 13のsubmenu option 3を対象とする。

既存schema version 1、Literature repository、structured repository、Evidence read API、Phase 2-7 comparison read modelをread-onlyで利用する。Comparability判断の保存、schema変更、Outcome alignment、単位変換は行わない。

## 3. Responsibility boundary

責務は次のように分離する。

```text
PT Research Library: 保存済み事実と独立したverification / Evidence metadataを提示
↓
利用者: 研究目的と条件を踏まえてcomparability statusを明示判断
```

システムは、Outcome名、定義、単位、数値、Methods、Evidence、欠損状態からcomparabilityを決定しない。最終判断は利用者に属する。

## 4. Pairwise Outcome selection

比較は必ず`Literature A / Outcome A`と`Literature B / Outcome B`の1対1とする。Literature AとBは別recordでなければならず、unknownまたは同一Literatureを拒否する。

各LiteratureのOutcomeは`sort_order ASC, id ASC`で一覧表示し、1-basedの表示番号で明示選択する。画面はOutcome番号、保存済みname、definition概要、context概要を使用し、internal entity IDを選択UIの中心にしない。同名Outcomeを別々に保持し、名前一致で自動選択しない。Outcome 0件は明確なempty stateとし、表示範囲外番号を拒否する。

## 5. Outcome Identity

Outcome Identityは次の保存fieldを左右別々に表示する。

- `name`
- `definition`
- `calculation_method`
- `unit`

Identity fieldは互いに独立しており、同じ保存文字列であってもsame Outcomeまたはdirect comparabilityを意味しない。Semantic equivalenceや同義語は推測しない。

## 6. Outcome Context

Outcome Contextは次の8 fieldを固定順で左右表示する。

- `context_condition`
- `context_group`
- `context_body_position`
- `context_task`
- `context_load`
- `context_region`
- `context_layer`
- `context_time_point`

保存値をそのまま表示し、`90 N`と`9.2 kg`の換算や条件一致判定を行わない。

## 7. Literature-level Methods context

Comparability判断に必要なMethodsは、Phase 2-7と同じ5 subgroupとfixed field orderを使用する。

- Measurement / Imaging
- Body Condition
- Task / Protocol
- Analysis
- Validation / Statistics

同一Literatureに同じMethods entity typeが複数あれば、`sort_order ASC, id ASC`ですべて保持する。値が同じでもmerge、deduplicate、1件への絞り込みを行わない。

Current schemaにはOutcomeから特定Method entityへのdirect linkがない。したがって表示は常に`Literature-level Methods context（Outcomeへの直接関連付けは未登録）`とし、次を固定表示する。

> 以下はLiterature全体のMethodsであり、選択Outcomeとの直接対応を示すものではありません。

## 8. Validation / Evidence

次を別情報として保持・表示する。

- Outcome entity verification
- Outcome entityへ直接linkされたEvidence件数
- Outcome entity Evidenceのうち`user_verified`の件数
- 各Outcome field verification
- 各Outcome fieldへ直接linkされたEvidence件数
- 各Outcome field Evidenceのうち`user_verified`の件数
- `validation_information` field
- Literature-level Validation / Statistics Methods

Evidenceが`user_verified`であることをOutcome fieldまたはentityの`user_verified`へ伝播しない。Evidence 0件をfield未登録とみなさず、entity、field、Evidence、Phase 1 Literature verificationを1つの「検証済み」状態へ統合しない。

Child Resultは参考情報として表示できるが、結果値、平均、p値、効果量、統計値の近さからcomparabilityを評価しない。

## 9. Manual comparability states

利用者が選択できる状態は次の4つだけとする。

- `directly comparable`: 利用者が目的と条件を踏まえて直接比較可能と判断
- `partially comparable`: 共通性はあるが重要な方法・条件差の考慮が必要と判断
- `not directly comparable`: 数値等の直接対比は不適切と判断
- `needs review`: 未判断

Unknown statusを拒否する。最初の3状態は、利用者が明示選択した場合だけ`ユーザー判断`として表示する。

## 10. `needs review` semantics

Profileの初期状態は常に`needs review`とする。これは比較困難またはnot comparableを意味せず、利用者がまだ判断していないことだけを表す。

同じOutcome名、異なるOutcome名、同じdefinition、同じunit、missing definitionのいずれでも初期状態を変更しない。Missing informationがあっても利用者による任意のmanual status選択を妨げない。

## 11. No automatic inference

Phase 2-8では次を実装しない。

- AI semantic similarity
- fuzzy matching
- synonym mapping
- automatic Outcome alignment
- unitまたはload conversion
- numerical tolerance comparison
- automatic comparability status
- statistical interpretationによるstatus変更
- ranking、recommendation、優劣判断
- Literature adoptionまたはEvidence verification変更

画面には次の固定注意書きを表示する。

> 同じOutcome名・同じ単位・同じ数値でも、直接比較可能とは限りません。

## 12. Missing / availability semantics

Structured field自体が存在しない状態は`未登録`と表示する。存在するsource factの`reported`、`not_reported`、`not_extracted`、`unclear`、`not_applicable`はcanonical stateをそのまま保持し、`未登録`と混同しない。

`reported`は保存値を表示する。Stringはそのまま、number、boolean、object、arrayはPhase 2-7と同じdeterministic JSON表現を用いる。InterpretationのJSON null、SQL NULL、field未登録を同一視しない。Missing状態を補完または推測しない。

## 13. Read-only / transaction behavior

Outcome list、selection validation、profile construction、manual status validationはINSERT、UPDATE、DELETE、schema statementを実行しない。Success、empty state、validation error、cancel、status選択のいずれでもconnectionをcommit、rollback、closeしない。

Callerにactive transactionがある場合、そのtransactionと未確定rowを保持したままreadする。Comparison前後でLiterature、structured entities / fields、Evidence / links、tags、literature tags、usage history、schema migrationsを変更しない。

## 14. Research integrity

保存済み事実だけを表示し、Outcome、definition、context、method、unit、result、locator、bibliographyを推測または補完しない。AI由来fieldを利用者確認済みに見せず、field verificationとEvidence verificationを分離する。

同名Outcome、同一unit、近い数値はdirect comparabilityの根拠として自動採用しない。異なる名前またはmissing dataもnot directly comparableの根拠として自動採用しない。比較可能性、原著確認、文献採否の最終判断は利用者が行う。

## 15. Non-persistence rationale

Current schemaにはcomparability judgmentの正式な保存先がなく、判断はResearch Projectや研究目的との関係を持ち得る。このためPhase 2-8のmanual assessmentは現在のCLI session内だけに存在し、次を固定表示する。

> この判断はDBへ保存されません。

Statusまたは理由を`usage_history`、structured field、Evidence、noteその他の既存tableへ流用しない。Comparability table、column、migrationを追加しない。

## 16. Zero-cost/local policy

Phase 2-8はPython standard library、local SQLite、既存repositoryだけを使用する。Network、OpenAI / Claude / PubMed / Crossrefその他のAPI、cloud、server、account、external dependency、追加料金を導入しない。

Testsはtemporary directory、temporary SQLite、synthetic Literature / Outcome / Methods / Evidenceだけを使用する。Production database、実文献、実PDF、実研究dataへ触れない。

## 17. Boundary with Phase 2-9

Phase 2-8は2 Literature間のsession-only Outcome判断に限定する。Phase 2-9のResearch Project entity、ProjectとLiteratureのmany-to-many relationship、project-specific objective / status / unresolved issue / next action / protocol note、comparability judgment persistenceを先取りしない。

## 18. Intentionally out of scope

- Comparability statusまたは判断メモのDB保存、export、履歴化
- Schema migration、schema version change、新規tableまたはcolumn
- Automatic Outcome / Method alignment、same concept判定
- Semantic similarity、fuzzy matching、synonym dictionary
- Unit / load / numeric conversionまたはtolerance判定
- 統計的解釈、aggregation、ranking、recommendation
- Research Project、Own Protocol comparison、Research Relevance comparison
- Literature adoption、Evidence / field / entity verification変更
- PDF parsing、OCR、external literature search
- GUI、Web UI、API、network、cloud、external dependency
- Production database、real literature、real PDF、real research dataの処理
