# Phone Harness 専用チャット移行 引継ぎ資料

作成日: 2026-08-20

## 0. この資料の目的

この資料は、現在の Phone Harness / AliExpress Merge Boss 実機学習・プレイ・Monitor / Human Teaching・Aegis Gate 連携作業を、別の専用チャットへ移行してそのまま再開するための技術引継ぎ資料である。

単なる会話要約ではない。次チャットは、ここに記載した canonical なローカル状態・ファイル・実装済み機能・既知問題を確認し、不要な聞き直しをせずに作業を継続すること。

## 1. 現行 Phone Harness 作業場所

- Worktree:
  `%USERPROFILE%\Documents\Intelligence Works\.workbridge\worktrees\phone-harness-7e2e5c83`
- Branch:
  `feature/phone-harness-mcp-modern`
- 現在の HEAD:
  `8818cdb fix: fully stop phone-harness runtime`
- Git 状態:
  大量の未コミット変更・未追跡ファイルが存在する。
  これらは現在進行中の Phone Harness / Merge Boss / Monitor / Human Teaching / visual pipeline 実装であり、勝手に reset / clean / checkout / discard しないこと。
- Git 作業方針:
  既存 worktree を継続使用する。別作業を行う場合も、原則専用 worktree を使う。

## 2. 起動・停止

### 一括起動

Phone Harness の通常起動は worktree 直下から次を使用する。

```powershell
.\tools\start_phone_harness.ps1
```

この wrapper は、現在の運用では概ね次を一括起動する。

1. `pymobiledevice3 tunneld`
2. phone-harness MCP
3. Secure MCP Tunnel client
4. 必要な health / OAuth metadata 確認

Monitor Web は状況により別起動になることがあるため、17678 listener を確認すること。

### 一括停止

```powershell
.\tools\stop_phone_harness.ps1
```

P0 で完全停止保証を実装済み。

停止対象:

- phone-harness MCP
- Secure MCP Tunnel
- `pymobiledevice3 tunneld`
- WDA runner
- Python bridge
- Web / Desktop Monitor
- device-side WDA runner の best-effort cleanup

停止後に以下を確認する。

- TCP 17677 clear
- TCP 17678 clear
- TCP 49151 clear
- WDA / bridge / Monitor 残留なし

P0 の commit:

`8818cdb fix: fully stop phone-harness runtime`

## 3. 現在のローカル稼働状態

2026-08-20 の最終 Workbridge 確認では:

- MCP 17677: LISTEN
- Monitor 17678: LISTEN
- tunneld 49151: LISTEN
- MCP `/healthz`: `{"ok":true,"name":"phone-harness-mcp"}`
- tunneld には現在 iPhone が再出現済み
  - device id: `<DEVICE_UDID>`
  - interface: `<LAN_IP>`

重要:

Monitor の最後の PhoneRuntime status trace は `connection_state=no-device` のまま残っている。
これは Aegis Builder が 2026-08-20 22:30 頃に確認した時点では実際に tunneld device_count=0 だったためである。
その後、Workbridge から tunneld を再確認した時点では実機が戻っている。

したがって次チャットでは、古い Monitor trace の `no-device` を現在状態と決めつけず、最初に fresh `phone_status` / `phone_observe` で再確認すること。

## 4. USB / Wi-Fi / WDA

通常運用は USB 不要。Wi-Fi で実機制御する。

期待構成:

ChatGPT
→ Secure MCP Tunnel
→ local phone-harness MCP 17677
→ Python bridge / PhoneRuntime
→ `pymobiledevice3 tunneld` / RSD over Wi-Fi
→ WDA accessibility/input
→ iPhone

USB が必要になる代表例:

- 初回/再ペアリング
- 復元後の特殊な再設定
- Developer Mode / DDI / WDA 周辺の復旧

通常の Merge Boss play / Monitor / Human Teaching は Wi-Fi でよい。

WDA は lazy start。`wda_state=not_started` 自体は起動直後なら異常ではない。

P0 後に、host-side WDA を止めても device-side WDA が残留して fresh start を妨げるケースを実機で検出したため、停止処理へ device-side WDA cleanup を追加済み。

## 5. Monitor Web

### URL / Port

- Port: 17678
- Bind: `0.0.0.0`
- LAN only
- 直近の preferred LAN URL:
  `http://<LAN_IP>:17678/`
- localhost:
  `http://127.0.0.1:17678/`

### Windows Firewall

最小 Firewall rule 設定済み。

- DisplayName: `Phone Harness Monitor LAN`
- Enabled: True
- Profile: Private
- Direction: Inbound
- Action: Allow
- Protocol: TCP
- LocalPort: 17678
- RemoteAddress: LocalSubnet
- Program: Phone Harness venv の `pythonw.exe`

Public/Internet 全体には開いていない。

### 実装済み Monitor 機能

- Current Frame / capture preview
- Trace / Timeline
- Decision 表示
- Failure Judgment 専用表示
- Runtime / transport 状態
- Capture / Recognition / Decision / Action / Learning の Timing 集計
- OCR / semantic summary
- Human Teaching
- Human Teaching Inbox
- AI Question / Human Answer conversation history
- Human present / absent
- Host Activity

### Failure Judgment

単なる error log ではなく、失敗判断を独立表示する。

想定項目:

- reason
- phase
- expected
- actual
- next action

例: unknown order item visual / perception failure / stale observation 等。

### Timing

工程別に概ね以下を表示する。

- latest
- average
- p95
- total
- count

対象:

- Capture
- Recognition / Perception
- Decision
- Action
- Learning

注意:
工程が重なる可能性があるため、各 total の単純合計を turn の実時間とはみなさない。

### 現在の UI 改善残

ユーザー要望として未完了:

- 現状は情報量が多く、UI バランスがまだ悪い
- 縦長 capture を活かしたい
- 全 section を常時表示する必要はない
- Tab / focus view 等で情報密度を整理したい

機能を削除せず、情報配置のみ再設計すること。

## 6. Human Teaching

### AI → Human 質問

`ask_operator` は最大 3600 秒（1時間）の待機が可能。

1時間で十分というユーザー判断。変更不要。

### Human → AI 任意 Teaching Inbox

AI が質問した時だけでなく、人間が任意のタイミングでメッセージを送れる Inbox を実装済み。

要件:

- text only 可
- image only 可
- text + image 可
- message / image / timestamp を同一 Teaching event として保持
- runtime の安全な区切りで検出
- 必要に応じて knowledge 更新 / 再計画 / AI reply
- AI reply は履歴に残す

画像は PNG / JPEG / WebP を想定。

### Human presence

Monitor で human present / absent を切替可能。

- present:
  AI → Human Teaching 質問を許可
- absent:
  質問待ちで workflow を止めず、その時点の情報で自律継続

Human から任意に Inbox へ送信する機能は absent 中でも維持する。

直近の presence は `present=false`。

### Host Activity

Monitor に以下を表示する設計を実装済み。

- WORKING
- WAITING HUMAN
- COMPLETED
- PAUSED
- stale / interrupted 推定

現在 Monitor に残っている explicit marker は `PAUSED` で、古い note:

`ChatGPT App connector resource must be reconnected after MCP schema rebuild before Merge Boss play can continue`

が残っている。

これは現在のローカル実機接続状態そのものではない。次の実作業開始時に activity marker を fresh に更新すること。

## 7. Merge Boss 基本ルール

### 盤面

- 7 × 9
- 生産機は右上に電気/producer badge のような印がある
- 生産機は merge 対象ではない
- 生産機 tap で merge item を排出
- 空きセルとエネルギーがあれば 1 loop 内に複数排出してよい

### Merge

- 同一 identity / 同一 level の item 同士を merge
- 見た目が似ているだけでは merge しない
- 1 loop で複数 merge / chain merge してよい
- OCR や固定座標だけに依存しない

### Orders

- 上部に約 5〜6 人の注文者
- 各注文は 1〜2 item
- 注文者 strip は横 scroll
- item が盤面に存在すると checkmark
- 完成すると card 下部に `完成` ボタン
- 納品すると盤面 item が消費される

### `i` アイコン

重要な Human Teaching:

item 等の右下にある `i` は、ヒント / 詳細情報表示入口として扱う。

これは Merge Boss 固有というより、汎用 UI affordance として `docs/game-operations/common/visual-affordances.md` に保存済み。

未知注文 item / producer output 等を見つけた場合、まず `i` から family / level chain / producer output 情報を取得して学習する。

### Order priority

ユーザー指定:

注文 level だけで優先順位を決めず、現在盤面にある同 family item から注文完成までの merge 距離を加味する。

例:

- board Lv3 → order Lv5
- board Lv5 → order Lv9

なら前者の方が完成距離が短いので優先度を高くする。

この priority_distance 戦略は実装・実機発火確認済み。

### Producer strategy

重要な最終ユーザー方針:

注文と無関係そうな producer を生成しても問題ない。

理由:

- 注文者は任意に入れ替わる
- その時点で不要な item でも最終的には何らかの注文で使う
- 適当な生産でも効率差は小さい
- producer / output 学習が不足しているなら、実際に生産しながら出た item を観測して学習してよい

したがって:

- order-related producer を優先してよい
- 見つからなくても bounded arbitrary production を許可
- 未学習 producer も bounded exploration 可
- 無関係 producer fallback 自体を failure 扱いしない

## 8. Merge Boss 学習済みデータ

主要ファイル:

- `docs/game-operations/aliexpress-merge-boss/calibration.json`
- `docs/game-operations/aliexpress-merge-boss/catalog.json`
- `docs/game-operations/aliexpress-merge-boss/knowledge.mbk`
- `docs/game-operations/aliexpress-merge-boss/visual.mbv`
- `docs/game-operations/aliexpress-merge-boss/rules.md`

Builder が 2026-08-20 に確認した時点の概要:

- catalog version: 2
- item families: 7
  - tennis-goods
  - cheering-spectator
  - viewing-equipment
  - fishing-supplies
  - fish-plush
  - book
  - romeo-juliet-props
- producer records: 4
- merge transitions: 4
- visual.mbv: 95 lines
- knowledge.mbk: 45 lines

### 今回追加・補強された既知項目

- tennis-goods Lv10 order visual
- Romeo and Juliet props Lv1〜10 visual
- Romeo and Juliet props Lv10 order visual
- book Lv6 order visual
- cheering-spectator Lv10 order visual
- tennis-goods Lv5 order visual
- board-item visual context
- producer board-context recognition

### Producer outputs

4 producer のうち、今回の学習で vertical film camera の placeholder output を Romeo/Juliet props へ解決した。

その時点では `outputs_complete=false` が残っていた producer は主に `highest print` 系 1台まで減っていた。

ただし次チャットでは `catalog.json` を canonical source として再確認すること。

## 9. Visual / recognition 改善状況

以前は board reader の known item が 1 前後まで落ちることがあった。

原因:

- hint screen template と board sprite の描画差
- producer glow / animation / background variant

対策:

- `item`: hint 由来 template
- `order-item`: order card 由来 template
- `board-item`: board 上の実 sprite template
- producer は item と confidence 条件を分離

実機では known_item_count が 14 程度まで改善したケースを確認。

producer は 8/8 認識まで改善したケースあり。

単純な global threshold 緩和は誤認増加のため避ける。

## 10. 直近の実機 play 成功例

P1 回帰で、Wi-Fi 実機に対して `merge_boss_turn` が複数 cycle 完走している。

例:

- cycle 0: produce ×2
- cycle 1: merge ×2
- recovery: 0

別 turn では:

- produce ×2
- produce ×2
- produce ×2
- merge ×2
- recovery: 0

Order scan は 3 pages / 5 customers を fresh scan できている。

直近 Monitor trace の一例:

- board 45 items
- free 10
- producer 8
- known producer 8
- decision: produce
- priority_distance: `[1]`

## 11. 性能の目安

直近 Monitor timing 例:

- Capture:
  - average 約 247 ms
  - p95 約 455 ms
- Recognition:
  - average 約 791 ms
  - p95 約 1508 ms
- Decision:
  - average 約 4.8 ms
- Action:
  - average 約 1058 ms
  - p95 約 2963 ms
- Learning:
  - average 約 4.5 ms

一方、OCR を含む semantic full observation が約 17 秒かかった例もある。

性能改善時は、画像 capture 自体より OCR / semantic analysis が支配的になるケースを分離して考えること。

## 12. Aegis Gate 連携

### 最終方針

重要:

**Codex は使わない。Builder-only とする。**

以前、指示を逆に解釈して Codex 使用 Goal を作ってしまったが、それは採用しない。

現在の Builder-only Goal:

`goal-phone-harness-merge-boss-builder-only-20260820`

Aegis worktree:

専用 Goal worktree を作成済み。Aegis source fix commit:

`e1aca6d fix: support builder-only Aegis recovery`

### 今回 Aegis で直した主な問題

1. Builder-only Mission なのに Codex executable / capability を要求していた
2. Goal-scoped Reviewer / Builder 契約が新 Goal family に適用されていなかった
3. Builder-only initial execution に旧 `continuation requirements` 前提が残っていた
4. ChatGPT rate-limit modal により送信が遮断される
5. rate-limit modal が paste 中にも干渉し本文欠損する
6. ordinary Recovery が無い v7 型 progress-stopped で `not-dispatched` recovery が扱えない
7. Builder result の Git 検証が Aegis worktree を見ており、別 executor workspace を検証できなかった
8. Builder result に URL / absolute path があると canonical work_result 保存に失敗
9. late Builder failure result を handoff invalid と誤分類する経路

### rate-limit modal 対応

対象 modal:

- id: `modal-conversation-history-rate-limit`
- 文言: `リクエストが多すぎます`
- button: `了解`

実装:

1. paste 前に特定 modal を検出
2. 約 60 秒待機
3. modal 内の唯一の有効 button を 1 回押す
4. paste を最初から行う
5. send 直前に再表示された場合も同様に 1 回処理

Reviewer / Builder の両方で live 確認済み。

`rate_limit_modal_recoveries: handled=true, wait_ms=60000`

本文 canonical match / post_confirmed まで確認済み。

### Aegis 現在の Mission 結果

最初の Builder Mission は、実行時点で Phone Harness MCP は reachable だったが tunneld device_count=0 だったため、canonical Stop Condition に従い実機操作せず終了した。

この結果は最終的に:

- executor: builder
- status: failed
- exit_classification: execution_failure

として `EXECUTOR_RESULT_COMMITTED` まで正規 commit 済み。

Codex は実行されていない。

ただし現在は Workbridge から tunneld device が戻っていることを確認済みなので、同じ attempt の再実行ではなく、**新しい bounded Mission / replan** として再開するのが正しい。

### Aegis 修正テスト

最終まとめ suite:

`195 tests PASS`

Aegis の Goal 固有 `aegis_gate_config.json` に Phone Harness executor path を設定したローカル差分は、汎用 source commit には含めていない。

## 13. 現在の重要な未完了事項

優先度順。

### P1 継続: fresh 実機状態確認

現在 tunneld device は戻っている。

次チャット開始時に:

1. fresh `phone_status`
2. `phone_observe`
3. Merge Boss board 画面確認
4. energy 現在値確認

を行う。

古い `no-device` trace を現在値として使わない。

### Merge Boss play 継続

ユーザーの直近意図:

- まず通常チャットで energy 残り約 50 まで学習・play する案があった
- その後 Aegis へ渡す予定だった
- 途中で Aegis 修復優先に変更された

現在は Aegis source fix が完了し、実機 Wi-Fi path も戻ったため、次チャットでは fresh 状態を見てどちらから再開するか判断できる。

基本的には、人間が不在でも止まらない自律 loop を維持する。

### Monitor UI 再設計

機能はかなり揃ったが UI の情報密度整理は未完了。

### ChatGPT App connector

過去に MCP schema rebuild 後、ChatGPT 側の App/tool resource が stale / disabled になったことがある。

ローカル MCP health が正常でも `phone-harness tool has been disabled` / resource not found になる場合:

1. Workbridge で 17677 / health / Secure Tunnel を確認
2. ローカルが生きていれば ChatGPT App を再接続
3. `phone_status` を再確認

ローカルプロセスを闇雲に再起動しない。

## 14. 次チャットで最初に行う推奨チェック

1. Workbridge で既存 Phone Harness worktree を開く
2. `git status --short --branch` を確認
3. 17677 / 17678 / 49151 listener 確認
4. `/healthz` 確認
5. tunneld device_count / RSD 確認
6. ChatGPT 側 `phone_status`
7. `phone_observe` で current screen を fresh capture
8. Merge Boss なら current energy / board / orders を確認
9. Human Teaching Inbox pending を確認
10. presence が absent の場合は質問待ちせず自律継続

## 15. 変更時のルール

- 推測で source 修正しない
- まず実機またはテストで原因再現
- 最小修正
- test → typecheck → build を通す
- generic runtime と Merge Boss 固有 logic を分離
- game knowledge は `docs/game-operations/aliexpress-merge-boss/` に蓄積
- generic UI affordance は `docs/game-operations/common/` に置く
- 未知 item / producer は実プレイを止めすぎず、bounded exploration で学習
- 90% 程度の確度がある一般的なゲーム挙動で毎回人間確認しない
- 課金 / 購入 / アカウント変更等は自動実行しない
- Phone Harness の大量 dirty baseline を勝手に整理・破棄しない

## 16. 専用チャットへの開始メッセージ例

次チャットではこのファイルを添付または Workbridge で直接参照し、例えば次のように開始する。

> Phone Harness 専用チャットです。  
> `docs/handoffs/20260820-phone-harness-dedicated-chat-handoff.md` を確認し、現行 worktree / runtime / Merge Boss knowledge / Monitor / Human Teaching / Aegis 状態を fresh に再確認して、そのまま作業を継続してください。  
> 過去 trace の `no-device` を現在状態と決めつけず、最初に fresh `phone_status` / `phone_observe` を実施してください。

