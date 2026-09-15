# Multi-Literature Comparison Matrix

## 1. Purpose

本文書は、PT Research Library Phase 2-7における複数文献比較のselection、read model、表示、research integrityを定める。目的は、複数Literatureの保存済みStudy、Methods、Outcomesを同じfield単位で確認し、特にPT研究のMethods条件を横断して見やすくすることである。

## 2. Scope

Phase 2-7は、複数Literature選択、Study / Methodsのsemantic matrix、Literature別Outcome logical unit、missing / availability表示、field / Evidence verification summary、CLI menu 13を対象とする。

比較は保存値の表示と横並びに限定する。Research Relevance、comparability判断、推薦、ranking、単位変換、同義語統合、schema変更は対象外である。

## 3. User workflow

Main menuの`13. 複数文献比較`から次のsubmenuを使用する。

```text
複数文献比較

1. 直前の検索結果から選択
2. Literature IDを指定
0. メインメニューへ戻る
```

選択後、比較文献のTitle / Year、Study rows、Methodsの5 subgroups、Literature別Outcomesを表示する。比較完了後もsubmenuへ戻り、直前の検索結果stateは保持する。

## 4. Literature selection

比較対象は最低2 Literatureとし、同じLiteratureを重複指定できない。指定順をmatrix column orderとして保持し、内部IDによる並べ替えを行わない。1件でも存在しないLiteratureがあればmatrixを返さず、partial comparisonを行わない。

直前の検索結果ではTitle / Yearと1-basedの選択番号を表示し、`all`または`1,2,4`形式を受け付ける。`all`は検索結果順、subsetは入力順を保持する。検索未実行または0件なら先に検索するよう案内する。範囲外、duplicate、1件だけ、malformed inputは拒否する。

Manual fallbackは`1,4,7,9`形式のASCII数字とカンマだけを受け付ける。各token前後のspaceはtrimする。空、1件、duplicate、0、negative、非ASCII digit、空token、malformed comma listを拒否する。

## 5. Matrix data model

Study / Methodsは`ComparisonMatrix → ComparisonRow → ComparisonCell → ComparisonValue`のread modelを使用する。各rowのcellsは常にLiterature選択順であり、cellは同一Literature内の該当fieldを0件以上保持する。

`ComparisonValue`は保存値、content role、availability、field verification、fieldへ直接linkされたEvidence件数、user-verified Evidence件数を別々に保持する。Matrixは表示専用であり、canonical schemaの代替ではない。

## 6. Study comparison

Study row orderは次で固定し、alphabetical sortしない。

1. `study_design`
2. `research_objective`
3. `population`
4. `sample_size`
5. `demographics`
6. `condition_diagnosis`
7. `health_status`
8. `inclusion_criteria`
9. `exclusion_criteria`
10. `group_allocation`
11. `study_setting`

## 7. Methods comparison

Methodsは既存の5 subgroupsと次のfield orderを維持する。

- Measurement / Imaging: `measurement`, `imaging_modality`, `imaging_condition`, `device`, `probe`, `probe_orientation`, `frame_rate`, `sampling_condition`, `calibration_scale`, `roi`
- Body Condition: `body_position`, `joint_position`, `joint_angle`, `limb_position`, `contraction_type`, `muscle_activation_condition`, `load`, `weight_bearing_condition`
- Task / Protocol: `task`, `movement`, `range`, `speed`, `repetition`, `duration`, `rest`, `trial_number`
- Analysis: `analysis_method`, `tracking_algorithm`, `preprocessing`, `reference_frame_baseline`, `calculation_method`, `roi_handling`, `quality_control`
- Validation / Statistics: `validation`, `reliability_method`, `statistical_analysis`, `icc`, `sem`, `mdc`, `mcid`, `agreement_analysis`, `other_statistical_method`

## 8. Outcome presentation

Outcomeは文献間でmatrix rowへalignせず、Literatureごとのlogical unitとして表示する。各Outcomeは`name`、`definition`、`calculation_method`、`unit`、8種類の保存済みcontext field、`validation_information`を分離する。

Child Resultは親Outcomeの下で`condition_or_comparison`、`result`、`statistics`を分離して表示する。同名Outcomeを同一文献内でも文献間でもmergeせず、entityごとに保持する。Outcome名をcomparison keyにしない。

## 9. Missing / availability semantics

Structured fieldが存在しないcellは`未登録`と表示する。Fieldが存在する場合は、`not_reported`、`not_extracted`、`unclear`、`not_applicable`をそのまま表示し、`未登録`と混同しない。`reported`は保存値を表示する。

全Literatureでfield自体が未登録のrowは既定で非表示にする。Unavailable fieldは存在するfieldであるため、全cellが`not_reported`等でもrowを表示する。

Stringは保存文字列をそのまま表示し、number / boolean / object / arrayはPython標準libraryによるdeterministic JSON表示とする。Object keyは表示安定性のためsortする。JSON nullを持つinterpretationは`null（解釈値）`とし、SQL NULLや未登録と混同しない。NaN / Infinityを生成しない。

## 10. Verification / Evidence presentation

各registered fieldはfield verificationと、fieldへ直接linkされたEvidence countを別metadataとして保持・表示する。Evidence countを表示するときは`Evidence: N`と`user_verified Evidence: M/N`を分離する。

Field verification、entity verification、Evidence verification、Phase 1 Literature verificationは別状態である。Evidenceがuser-verifiedでもfieldを自動的にuser-verifiedと表示せず、Evidence 0件でもfieldを削除または非表示にしない。

Outcome / Resultではentity verificationとentity-level Evidence summaryもfield metadataから分離して表示する。

## 11. Multiple entity behavior

同じLiteratureに同じentity typeが複数あっても1件だけを選ばない。すべてのentityを`sort_order ASC, id ASC`で保持する。同じrowに複数値がある場合はordinal付きで別々に表示し、値が同じでもmergeまたはdeduplicateしない。

## 12. Deterministic ordering

Orderingは次の順に固定する。

1. Literature columns: user selection order
2. Sections: Study、Methods、Outcomes
3. Methods subgroups: 本文書7節の順
4. Study / Methods fields: 固定field order
5. Same-type entities: `sort_order ASC, id ASC`
6. Outcome child Results: `sort_order ASC, id ASC`
7. Linked Evidence: existing repositoryのID昇順

Orderingのためにsetを表示順のsourceとして使用しない。

## 13. Read-only / transaction behavior

Comparison serviceは既存Literature repository、structured repository、Evidence read APIだけを使用し、INSERT、UPDATE、DELETE、schema statementを実行しない。Comparisonのsuccess、validation error、empty stateのいずれでもcaller connectionをcommit、rollback、closeしない。

Callerにactive transactionがある場合も、そのtransactionと未確定rowを保持したままreadする。Comparisonによってliterature、structured entities / fields、Evidence / links、tags、usage history、status、rating、`pdf_path`を変更しない。

## 14. Research integrity

保存済み値だけを表示し、bibliography、method、Outcome、result、unit、missing valueを推測または補完しない。AI抽出fieldを確定値に見せず、field verificationを値の近くに表示する。最終的な解釈、採否、原著確認は利用者が行う。

## 15. Zero-cost/local policy

Phase 2-7はPython standard libraryとlocal SQLiteだけを使用する。Network、OpenAI / Claude / PubMed / Crossrefその他のAPI、cloud、server、account、external dependency、追加料金を導入しない。

Testsはtemporary directory、temporary SQLite、synthetic Literature / structured dataだけを使用し、production DB、実PDF、実研究dataへ触れない。

## 16. Boundary with Phase 2-8

Phase 2-7は表示と横並びまでである。次の判断はPhase 2-8の責務であり、自動生成しない。
Phase 2-8のpairwise manual assessment境界は[`OUTCOME_COMPARABILITY.md`](OUTCOME_COMPARABILITY.md)を参照する。

- `directly comparable`
- `partially comparable`
- `not directly comparable`
- `needs review`
- 同一測定法、同一Outcome、同義の判定
- 優劣、推薦、採用判断

同じ文字列、同じOutcome名、近い数値をsame conceptまたは比較可能とみなさない。`90 N`と`9.2 kg`を換算しない。

## 17. Intentionally out of scope

- Outcome alignmentまたはcomparability status
- Synonym normalization、unit / load conversion、numeric normalization
- Statistical interpretation、ranking、averaging、recommendation
- Research Relevance comparison
- Research Project、Own Protocol comparison
- External search、DOI / PMID verification、PDF parsing、OCR
- GUI、Web UI、API、network、cloud、external dependency
- Schema change、migration、schema version change
- Comparison resultの保存、export、usage history記録
