# Original PDF Evidence Navigation

## 1. Purpose

本文書は、PT Research Library Phase 2-6における、保存済みEvidenceからローカル原著PDFへ安全に戻るworkflowを定める。目的は、利用者がEvidence locatorを確認したうえで原著を明示的に開き、該当箇所を自分で確認できるようにすることである。

```text
Structured item
↓
Evidence
↓
Literature.pdf_path
↓
Original PDF
↓
Evidence locator
↓
原著の該当箇所
```

## 2. Scope

Phase 2-6 v1は、Evidenceの選択、Literatureとのownership確認、`pdf_path`の使用時validation、locator Preview、明示確認後のmacOS `open`、open後のlocator再表示を対象とする。

既存のLiterature repository、Evidence review / structured repositoryをread-onlyで利用する。Evidence CRUD、link管理、verification変更はPhase 2-5の既存workflowを維持し、作り直さない。Structured schema versionは1のままである。

## 3. User workflow

CLI main menu `12. Evidence確認・管理`のsubmenuで`10. Evidenceから原著PDFを開く`を選択する。

1. Literature IDを入力する。
2. Literature titleと、そのLiteratureに属するEvidence一覧を確認する。
3. DB IDではなく画面上のEvidence選択番号を入力する。
4. Literature title、stored PDF path、Evidence verification、全locatorのNavigation Previewを確認する。
5. `1. 原著PDFを開く / 0. 中止`で明示選択する。
6. `1`の場合だけmacOS `open`を実行する。
7. open成功後もlocatorを再表示し、`pdf_page`がある場合だけPreviewでの手動page移動方法を案内する。

対象LiteratureにEvidenceが0件の場合は「このLiteratureにはEvidenceが登録されていません。」と表示し、PDFを開かない。EvidenceなしLiteratureを直接開く別機能は追加しない。

## 4. `pdf_path` resolution

既存`literature.pdf_path`の保存仕様は変更せず、使用時だけ次の順序で解決・検証する。

- `None`、空文字、空白だけ: `pdf_path未登録`として、文献編集で登録できることを案内する。
- `~`で始まるpath: user homeを展開する。
- absolute path: そのpathを使用する。
- relative path: current working directoryではなくapplication project rootを基準に解決する。
- symlink: resolveし、targetがexisting regular PDF fileであることを確認する。
- nonexistent path: stored pathを表示したまま「PDFファイルが見つかりません」と通知する。
- directory: fileとして扱わず拒否する。
- `.pdf`以外: 拒否する。suffix判定はcase-insensitiveであり、`.PDF`は受け付ける。

Pathを自動修正、DBへwrite back、同名file検索、copy、move、renameしない。

## 5. Evidence ownership validation

Navigation target構築時にLiteratureとEvidenceをcurrent rowから取得し、`evidence.literature_id`が明示選択されたLiterature IDと一致することを必須とする。Cross-Literature Evidenceはtargetにできない。

明示確認後、open command直前にもLiterature、Evidence ownership、stored path、resolved pathを再確認する。Evidenceが削除された場合、ownerが一致しない場合、またはPreview後にPDF pathやsymlink targetが変わった場合はopenしない。Verificationやlocatorだけの変更はopenを不必要にblockせず、current locatorをopen後に表示する。

## 6. Locator presentation

Navigation Previewとopen成功後の「確認位置」には、次をそれぞれ独立して表示する。

- PDF page
- Printed page
- Section
- Subsection
- Table
- Figure

未登録値は`未登録`と表示する。`pdf_page`はPDF viewer上の1-based page indexであり、`printed_page`は誌面page labelである。両者を自動変換、同一視、補完しない。Internal Literature ID / Evidence IDは処理に使うが、表示の中心にしない。

## 7. macOS open behavior

PDFはPython standard libraryの`subprocess`からmacOS標準`open`を呼んで開く。commandは必ず次のようなargv listとする。

```text
["open", "/resolved/local/path.pdf"]
```

`shell=False`を指定し、`shell=True`、command文字列連結、`os.system`、shell interpolationを使用しない。Spaces、日本語、quotes、semicolon、ampersand、dollar sign、backticksを含むpathも1つのargv要素として渡す。Runnerはdependency injection可能とし、testsではfake runnerだけを使用する。

## 8. Explicit confirmation

Navigation Previewとlocal path validationが成功した後だけ、次を表示する。

```text
1. 原著PDFを開く
0. 中止
```

`1`以外ではopenしない。`0`、EOF、KeyboardInterruptではopenerを呼ばない。不正な選択は再入力を求め、それだけでopenしない。

## 9. Failure handling

`open`のreturn codeが0でない場合、またはrunnerが例外を送出した場合は成功表示を出さず、「原著PDFを開けませんでした。」と表示する。Captured stderrをそのまま大量表示しない。Path validation failure、stale Evidence、ownership mismatch、Preview後のpath変更も安全に通知し、open commandを実行しない。

通常のfailureでCLI全体を不必要に終了させず、Evidence submenuへ戻る。Database errorは既存CLI方針に従う。

## 10. Transaction behavior

Navigation target構築、open直前の再確認、locator表示はread-onlyである。Callerにactive SQLite transactionがあっても、navigationは`commit`、`rollback`、`close`を呼ばず、transactionとconnectionを保持する。

PDF openはfilesystem/application side effectであるがDB writeではない。成功、失敗、cancelのいずれでもdatabase row、schema、pragmaを変更しない。

## 11. Research integrity

原著PDFを開くことと、原著の該当箇所を利用者が確認することは別である。PDF openだけでは次を変更しない。

- Evidence verification
- Structured field verification
- Structured entity verification
- Literature verification status
- AI summary status
- Adoption status

`user_verified`への変更は、既存のEvidence確認状態変更menuで利用者が明示操作した場合だけ行う。Locatorやbibliographyを推測・生成しない。

## 12. Filesystem safety

Phase 2-6はPDFをread/open targetとして扱うだけである。PDFや関連fileのdelete、overwrite、copy、move、rename、chmod、metadata変更、content writeを行わない。Stored `pdf_path`も変更しない。

Testsはtemporary directory、temporary SQLite、synthetic Literature / Evidence、内容を検証しないsynthetic `.pdf` placeholder、fake openerだけを使用する。Production DB、実文献PDF、File Library、研究dataには触れない。

## 13. Zero-cost/local policy

Phase 2-6はPython standard library、local SQLite、macOS標準`open`だけを使用する。External dependency、network、OpenAI / Claude / PubMed / Crossrefその他のAPI、cloud、server、account、login、継続費用を追加しない。

## 14. Page-navigation limitation

Phase 2-6 v1はautomatic Preview UI scriptingを行わない。`osascript`によるSystem Events、menu click、keystroke送信、Accessibility permission、GUI automation、sleep / delay依存のpage jumpを使用しない。

Open後も`pdf_page`を明示表示し、値がある場合は次の手動操作を案内する。

```text
PreviewでPDF page Nへ移動:
⌘⌥G → N
```

`pdf_page`が未登録ならこの案内を出さない。`printed_page`は別のlocatorとして表示し、page jump番号へ変換しない。GUI framework、PDFKit等は今回導入しない。

## 15. Boundary with Phase 2-7

Phase 2-6は1つのLiteratureに属する1つのEvidenceから原著へ戻るlocal navigationに限定する。Phase 2-7の複数文献選択、side-by-side comparison、comparison matrix、PT Methods横断比較を先取りしない。

## 16. Intentionally out of scope

- Automatic PDF parsing、OCR、locator / quote抽出
- Preview UI scripting、automatic page jump、PDFKit integration
- PDF content validationまたはannotation write
- EvidenceなしLiterature専用のPDF open action
- Evidence verificationの自動変更
- Structured field / entityまたはLiterature statusの自動変更
- PDF pathの自動修正、探索、DB write back、file管理
- Schema change、migration、schema version change
- Multi-Literature Comparison、Outcome Comparability、Research Project
- GUI、Web UI、network、API、cloud、external dependency
