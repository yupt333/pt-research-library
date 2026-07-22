# PT Research Library — 仕様書

## 1. プロジェクト名

**PT Research Library**

理学療法研究に使用する文献情報を、長期的に蓄積・検索・再利用するための個人用文献管理システム。

---

## 2. 目的

本システムは、以下の研究活動を支援する。

- 肩関節
- 超音波
- Acromiohumeral Distance（AHD）
- 棘上筋および腱板
- Ultrasound Speckle Tracking
- Tendon displacement
- Tendon strain
- Inter-layer sliding
- 測定信頼性
- ICC、SEM、MDC
- note記事作成
- 大学院研究
- 学会発表
- 論文執筆

単なる書誌情報の保存ではなく、**自分の要約、タグ、研究上のメモ、使用履歴を一体的に管理できる研究ライブラリ**を構築する。

---

## 3. 基本方針

### 3.1 正確性

- 正確性を最優先する。
- 推測と確認済み事実を区別する。
- 実在しない論文、DOI、PMID、URLを生成しない。
- 確認できない情報は「要確認」と記録する。
- 論文タイトルを必ず保存・表示する。
- DOIまたはPMIDが確認できる場合は保存する。
- AIが生成した要約は「未確認」として扱う。
- 文献の採否および解釈は利用者が最終判断する。

### 3.2 開発方針

- Pythonを使用する。
- データベースはSQLiteを使用する。
- macOS環境を前提とする。
- 既存仕様を不用意に変更しない。
- コード修正時は変更点を明示する。
- 指定された部分以外は原則として変更しない。
- データ消失を防ぐため、バックアップ機能を持たせる。
- 大規模機能を一度に作らず、段階的に実装する。

---

## 4. 想定利用者

現時点では、システム所有者本人のみが使用する。

- 複数ユーザー対応は不要
- ログイン機能は不要
- インターネット接続なしでも基本機能を使える構成を優先する

---

## 5. Phase 1 の実装範囲

Phase 1では、外部APIやAI連携を行わず、ローカル環境で安定して動作する基本機能を完成させる。

### 5.1 必須機能

1. 文献の新規登録
2. 文献一覧表示
3. 文献詳細表示
4. 文献情報の編集
5. 文献の削除
6. キーワード検索
7. タグ登録
8. メモ登録
9. 自分の要約登録
10. AI要約確認状態の管理
11. 使用履歴の登録
12. 重複候補の検出
13. CSV出力
14. SQLiteデータベースのバックアップ
15. 基本テスト

### 5.2 Phase 1では実装しない機能

- PubMed API連携
- Crossref API連携
- DOIからの自動書誌取得
- PDF本文解析
- AIによる自動要約
- note記事の自動生成
- Webアプリ化
- クラウド同期
- 複数端末リアルタイム同期

これらはPhase 2以降で検討する。

---

## 6. 文献データ項目

### 6.1 基本書誌情報

| 項目 | 型 | 必須 | 説明 |
|---|---|---:|---|
| id | INTEGER | 自動 | 文献ID |
| title | TEXT | 必須 | 論文タイトル |
| authors | TEXT | 任意 | 著者名 |
| journal | TEXT | 任意 | 雑誌名 |
| publication_year | INTEGER | 任意 | 出版年 |
| volume | TEXT | 任意 | 巻 |
| issue | TEXT | 任意 | 号 |
| pages | TEXT | 任意 | ページ |
| doi | TEXT | 任意 | DOI |
| pmid | TEXT | 任意 | PMID |
| url | TEXT | 任意 | 出版社またはデータベースURL |
| language | TEXT | 任意 | 言語 |
| publication_type | TEXT | 任意 | 原著、レビュー、ガイドライン等 |
| abstract | TEXT | 任意 | 抄録 |
| pdf_path | TEXT | 任意 | ローカルPDFパス |
| created_at | DATETIME | 自動 | 登録日時 |
| updated_at | DATETIME | 自動 | 更新日時 |

### 6.2 自分の研究用情報

| 項目 | 型 | 必須 | 説明 |
|---|---|---:|---|
| personal_summary | TEXT | 任意 | 自分の要約 |
| ai_summary | TEXT | 任意 | AIが作成した要約 |
| ai_summary_status | TEXT | 必須 | 未作成、未確認、確認済み、修正済み |
| key_findings | TEXT | 任意 | 主要な結果 |
| methods_note | TEXT | 任意 | 方法の要点 |
| clinical_note | TEXT | 任意 | 臨床的解釈 |
| limitation_note | TEXT | 任意 | 限界 |
| relevance_note | TEXT | 任意 | 自分の研究との関連 |
| evidence_level | TEXT | 任意 | エビデンスレベル |
| verification_status | TEXT | 必須 | 未確認、一部確認、確認済み、要確認 |
| adoption_status | TEXT | 必須 | 未判定、採用候補、採用、除外 |
| exclusion_reason | TEXT | 任意 | 除外理由 |
| rating | INTEGER | 任意 | 重要度評価、1〜5 |

---

## 7. タグ管理

文献には複数のタグを付けられる。

### 7.1 初期タグ候補

#### 解剖・部位

- shoulder
- rotator_cuff
- supraspinatus
- achilles_tendon
- scapula
- humerus

#### 画像・測定法

- ultrasound
- b_mode
- speckle_tracking
- optical_flow
- ncc
- mri
- xray

#### 指標

- ahd
- displacement
- strain
- sliding
- reliability
- icc
- sem
- mdc
- validity

#### 研究用途

- note
- graduate_school
- conference
- thesis
- protocol
- statistics
- review

タグは将来的に追加・編集・削除できるものとする。

---

## 8. 使用履歴

1つの文献を、どの目的で使用したか記録する。

### 8.1 保存項目

| 項目 | 説明 |
|---|---|
| id | 使用履歴ID |
| literature_id | 対象文献ID |
| usage_type | note、大学院研究、学会発表、論文、研究計画等 |
| project_name | 使用した研究・記事・発表名 |
| usage_note | 使用箇所や目的 |
| used_at | 使用日 |
| created_at | 登録日時 |

### 8.2 使用例

- AHD正常値の記事で引用
- 研究計画書の測定方法に使用
- 学会発表スライド5枚目で使用
- speckle trackingのアルゴリズム比較に使用

---

## 9. 重複検出

文献登録時に、既存文献との重複候補を確認する。

### 9.1 優先判定

1. DOI完全一致
2. PMID完全一致
3. タイトル正規化後の完全一致
4. タイトル類似度
5. 著者名と出版年の組み合わせ

### 9.2 処理方針

- 自動削除・自動統合は行わない。
- 重複候補として利用者に提示する。
- 最終的な登録・統合判断は利用者が行う。

---

## 10. 検索機能

最低限、以下を検索対象とする。

- タイトル
- 著者
- 雑誌名
- DOI
- PMID
- 抄録
- 自分の要約
- メモ
- タグ
- 研究との関連
- 使用履歴

### 10.1 絞り込み候補

- 出版年
- タグ
- publication type
- verification status
- adoption status
- AI要約確認状態
- 重要度
- 使用用途

---

## 11. CSV出力

文献一覧をCSV形式で出力できる。

### 11.1 目的

- Excelでの確認
- 統計処理前の整理
- 学会・大学院資料作成
- 外部バックアップ
- 他システムへの移行

### 11.2 文字コード

macOSおよびExcelでの利用を考慮し、原則としてUTF-8 with BOMを使用する。

---

## 12. バックアップ

### 12.1 対象

- SQLiteデータベース
- 設定ファイル
- 必要に応じてCSVエクスポート

### 12.2 方針

- 元データベースを直接上書きしない。
- 日時付きファイル名で保存する。
- バックアップ先は `backups/` とする。
- GitHubには実データベースを原則保存しない。

ファイル名例：

```text
pt_research_library_2026-07-22_120000.db
```

---

## 13. ディレクトリ構成

Phase 1では以下を基本構成とする。

```text
pt-research-library/
├── README.md
├── SPECIFICATION.md
├── .gitignore
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── repository.py
│   ├── search.py
│   ├── duplicate_check.py
│   ├── export_csv.py
│   └── backup.py
├── data/
│   └── .gitkeep
├── backups/
│   └── .gitkeep
├── exports/
│   └── .gitkeep
├── docs/
│   └── database_schema.md
└── tests/
    ├── test_database.py
    ├── test_repository.py
    ├── test_search.py
    └── test_duplicate_check.py
```

---

## 14. GitHubに保存するもの

### 保存する

- Pythonコード
- README
- SPECIFICATION
- テストコード
- requirements.txt
- データベース設計資料
- 空フォルダ維持用の `.gitkeep`

### 原則として保存しない

- SQLite実データベース
- PDF原文
- 超音波動画
- 超音波画像
- 個人情報
- 研究参加者データ
- CSV出力結果
- バックアップファイル
- APIキー
- パスワード

---

## 15. `.gitignore` に含める項目

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/

# macOS
.DS_Store

# Database
*.db
*.sqlite
*.sqlite3

# Research files
*.pdf
*.mp4
*.mov
*.avi
*.dcm

# Output and backup
exports/*
!exports/.gitkeep
backups/*
!backups/.gitkeep
data/*
!data/.gitkeep

# Environment and secrets
.env
*.key
```

---

## 16. 初期データベース構成

最低限、以下のテーブルを作成する。

1. `literature`
2. `tags`
3. `literature_tags`
4. `usage_history`

### 16.1 literature

文献の基本書誌情報、自分の要約、メモ、確認状態を保存する。

### 16.2 tags

タグ名を保存する。

### 16.3 literature_tags

文献とタグの多対多関係を保存する。

### 16.4 usage_history

文献をどの研究、記事、発表で使用したか保存する。

---

## 17. 操作方法

Phase 1では、まずコマンドラインで操作できる構成を許容する。

想定メニュー：

```text
1. 文献を登録
2. 文献一覧
3. 文献を検索
4. 文献詳細
5. 文献を編集
6. 文献を削除
7. タグ管理
8. 使用履歴を登録
9. CSV出力
10. バックアップ
0. 終了
```

GUI化は基本機能の安定後に検討する。

---

## 18. テスト要件

最低限、以下を自動テストする。

- データベース初期化
- 文献登録
- 文献取得
- 文献更新
- 文献削除
- DOI重複検出
- PMID重複検出
- タイトル重複検出
- キーワード検索
- タグ登録
- CSV出力
- バックアップ作成

テストでは本番データベースを使用しない。

---

## 19. エラー処理

- 必須項目不足時は登録しない。
- DOI、PMID、URLが不明でも文献登録は可能とする。
- 出版年が不正な形式の場合は警告する。
- データベース操作失敗時は原因を表示する。
- 削除前に確認を求める。
- バックアップ失敗時は元データを変更しない。
- 重複候補があっても自動的には削除しない。

---

## 20. Phase 2以降の候補

### Phase 2

- DOI入力による書誌情報取得
- PubMed検索
- Crossref検索
- PMID・DOI相互補完
- 書誌情報の自動整形
- PDFファイルとの関連付け強化

### Phase 3

- AI要約作成
- AI要約の未確認・確認済み管理
- 原文引用箇所の保存
- 英文と日本語訳の対照表示
- 研究テーマ別の文献比較表作成

### Phase 4

- note記事用参考文献一覧
- 学会発表用文献一覧
- 大学院研究計画用文献一覧
- 引用形式の自動生成
- 研究テーマ別エクスポート

### Phase 5

- GUIまたはローカルWebアプリ
- 高度な全文検索
- 文献間リンク
- 研究プロジェクト管理
- GitHub Actionsやクラウド連携の検討

---

## 21. 完了条件

Phase 1は、以下を満たした時点で完了とする。

- 新規文献をSQLiteへ登録できる
- 登録内容を一覧・詳細表示できる
- 編集・削除できる
- タイトル、著者、DOI、PMID、タグ、メモから検索できる
- 重複候補を検出できる
- タグを管理できる
- 自分の要約とAI要約確認状態を保存できる
- 使用履歴を保存できる
- CSVへ出力できる
- データベースをバックアップできる
- 自動テストが通る
- READMEに起動方法が記載されている
- 実データ、PDF、動画、個人情報がGitHubへ送信されない

---

## 22. Codexへの実装指示

Codexは以下を厳守する。

1. 本仕様書を最初に読む。
2. Phase 1の範囲だけを実装する。
3. 外部APIやAI機能を勝手に追加しない。
4. Python標準ライブラリを優先する。
5. SQLiteを使用する。
6. macOSで実行できるようにする。
7. 既存コードを変更する場合は、変更内容を明示する。
8. 本番データを削除する処理を自動実行しない。
9. `.gitignore` を適切に設定する。
10. テストコードを作成する。
11. READMEへセットアップ方法と使用方法を記載する。
12. 不明点がある場合は、推測で仕様を変更せず確認する。

---

## 23. 現時点での位置付け

この仕様書は初版であり、Phase 1の実装開始前に利用者が確認する。

仕様を変更する場合は、変更理由と変更箇所を明示し、既存データとの互換性を確認する。
