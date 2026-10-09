# CLAUDE.md — fps-gaze-research_2026

このファイルは Claude Code が毎セッション最初に読む前提のコンテキストです。
具体的な改良タスクは `docs/IMPROVEMENT_PLAN.md` にあります。作業を始める前に必ず読んでください。

---

## 1. このリポジトリは何か

卒業研究「VALORANTにおけるHUD内情報への注意配分と熟練度の関係 ― AOI解析によるHUD要素別注視行動の分析 ―」の計測・解析コード。

- Tobii Pro Spark（60 Hz、モニター下部中央に固定）で視線を取得し、OBS Studio の録画と時刻同期する
- 固定位置のHUD要素（ミニマップ、HP、アビリティ、弾数など）をAOIとして定義し、熟練度（ランク）群間で注視行動を比較する
- 研究の種類は **実験的検証**（ツール開発研究ではない）。敵モデルなどの動的AOIは対象外

**コードの優先順位**：機能の多さより「指標の定義が正しい・説明できる・再現できる」こと。卒論の方法の章にそのまま書ける処理であることを基準に判断する。

---

## 2. 研究設計の合意事項（2026-10-08 時点）

| 項目 | 決定内容 |
|---|---|
| 群分け | 低ランク群：アイアン1〜シルバー3 ／ 高ランク群：アセンダント1以上。各10名目標。未ランク者は原則除外 |
| ゲームモード | 本実験：アンレート。予備実験：スイフトプレイ（同一参加者・5試合で実施済み） |
| 分析区間 | 購入フェーズ終了 → 死亡またはラウンド終了（＝生存中）。購入中・死亡後の観戦・試合開始前は主解析から除外 |
| 主指標 | AOI別 Gaze %（**有効サンプルベース**。分母＝分析区間内の有効サンプル数。無効サンプルは分母から外す） |
| 副指標 | Fixation Count（有効分析時間1分あたりも出す）、Average Fixation Duration、Revisit Count |
| 探索指標 | TTFF、AOI間遷移 |
| 注視検出 | I-DT（分散閾値法）。開始案：最短100 ms、分散閾値1°（x範囲＋y範囲の和、視角）。閾値は自分のデータで妥当性を確認する |
| 処理順 | 有効性判定 → 注視検出 → 注視へのAOI割り当て → 訪問・再訪の集計 |
| サンプル分類 | AOI内の視線位置 ／ AOIへの注視 ／ 非注視のAOI内サンプル ／ 無効・判定不能。「見ただけ」という用語は使わない |
| 記録する条件 | マップ、攻守、エージェント（ロール）、ミニマップ設定、ゲーム解像度、眼−画面距離 |
| 実験環境 | 眼−画面距離 約65 cm、顎台なし、椅子・モニター・Spark位置を床/机のテープで固定、各セッション前にキャリブレーション |

スケジュール：10/19–25 予備実験（取得→録画→同期→分析の通し確認）、10/26–31 条件確定、11月 本実験。
**本実験の前に計測コードを凍結する**。それ以降の計測側の変更は不具合修正のみ。

---

## 3. 現在のパイプライン

```
[較正] src/gaze_estimation/calibrate_validate.py  試合前に9点キャリブレーション＋検証 → data/raw/calib_<subject>_<日時>.json
[計測] src/gaze_estimation/tobii_capture_with_sync_flash_v5.py   (SCRIPT_VERSION 5.0.0-eye-origin-pupil。v4.1 に眼位置z・瞳孔の列を末尾追加)
         + src/obs_controller_v2.py  (obsws-python / OBS WebSocket v5 で録画を自動開始・停止)
         → data/raw/gaze_<subject>_<trial>_<condition>_<YYYYMMDD_HHMMSS>.csv / _meta.json / ログ
         開始・停止は同一LAN上の別端末ブラウザから HTTP（既定 port 8765、token 付き）
[同期] src/visualizer/gaze_visualizer_v3.py auto-sync <csv> <mp4>
         offset_sec = start_sync.delta_sec_confirmed_based + data_quality.first_sample.pc_time_sec
         gaze_time(pc_time_sec) = video_time + offset_sec
[可視化] gaze_visualizer_v3.py render <csv> <mp4> --modes beeswarm scanpath heatmap
[区間]   src/visualizer/segment_annotator.py <mp4> --session-base <session_base>
         → data/annotations/<session_base>_segments.csv（動画時刻。行削除による区間除外は廃止）
[AOI解析] src/aoi_detector/aoi_analysis-ver3.py --input <csv> --aoi ... --segments ... --sync <*_sync.json> --manifest ...
         → data/processed/aoi_result/<session_base>/（I-DT 注視検出。ロジック本体は gaze_metrics.py / aoi_geometry.py）
[集計]   src/analysis/aggregate_sessions.py --manifest data/sessions_manifest.csv → data/processed/aggregate/
[台帳]   data/sessions_manifest.csv（実験者が記入。テンプレートは docs/templates/）
```

### 視線CSVの列（`FIELDNAMES`）
`wall_timestamp_local, wall_timestamp_utc, pc_time_sec, device_time_stamp_us, system_time_stamp_us, left_x, left_y, right_x, right_y, center_x, center_y, left_gaze_point_validity, right_gaze_point_validity, gaze_missing`
（v5 で末尾に追加）`left_gaze_origin_z_mm, right_gaze_origin_z_mm, left_gaze_origin_validity, right_gaze_origin_validity, left_pupil_diameter_mm, right_pupil_diameter_mm, left_pupil_validity, right_pupil_validity`

- 座標は Tobii display area の正規化座標（左上 (0,0)、右下 (1,1)）。画面外では 0 未満・1 超もあり得る
- 欠測は文字列 `"NaN"`。`gaze_missing` は `True/False` 文字列
- `pc_time_sec` は **購読開始（gaze_subscribe_call）からの経過秒で、ホストのコールバック到着時刻**（`time.perf_counter()`）。デバイス取得時刻ではない

### AOI設定
- `src/aoi_detector/valorant_hud_aoi_circular.json` が正。正規化座標。`minimap` と `crosshair` は `type: circle`（`radius_x/radius_y` は正規化半径。`radius_deg` で視角指定も可）
- 同 JSON の `display`（物理寸法 mm・解像度）と `viewing_distance_mm` が視角換算の基準。`aoi_check.py` で各AOIの視角サイズを確認できる
- 対象モニター：EIZO FlexScan EV2740X（27型、3840×2160、16:9）
- AOIは「この画面・この解像度・16:9・このHUD設定」でしか成立しない。解像度や4:3ストレッチが変わるとHUD位置が変わる

---

## 4. 守るべき不変条件（破ると研究データが壊れる）

**同期**
- 同期基準は `delta_sec_confirmed_based`。`request_based` は卒論の集計に使わない（`--allow-request-based-fallback` 明示時のみ）
- `first_sample.pc_time_sec` の補正項は省略しない（小さいから無視してよい値ではない）
- `KNOWN_MEAN_SEC=0.0188` / `KNOWN_SD_SEC=0.0025` は 2026-07-29/30 の較正10セッション由来。変える場合は根拠データを添える
- 残差バイアス 平均約4.1 ms（最大約7.8 ms）は卒論の限界として記載する前提

**Vanguard（VALORANTのカーネルモード・アンチチート）**
- ゲームPC上で OS全体のキーボード/マウスフックを使わない（`keyboard` パッケージ等は禁止。BANリスクが被験者アカウントに及ぶ）
- 試合中に最前面オーバーレイ窓を出さない（抑制されるうえ、ウィンドウフルスクリーンの遅延最適化を壊す）。`aoi_overlay*.py`（PyQt5）は試合中に起動しない
- OBSは「画面キャプチャ（ディスプレイキャプチャ）」、VALORANTはウィンドウフルスクリーン、VALORANT実行ファイルの全画面最適化は無効

**解析**
- ゼロ件のAOIも出力から消さない（明示的に0の行を出す）。`value_counts()` / `groupby()` による暗黙の欠落に注意。「本当に見ていない」と「出力に無い」を区別できなくなる
- `data/raw` を上書きしない。派生物は `data/processed/` へ
- 区間除外は「行の削除」ではなく区間定義ファイルで行う。注視・訪問・遷移が区間境界や欠測ギャップをまたいで連結されてはならない
- 指標の定義・閾値を変えたら、出力に定義とパラメータを残す（provenance）
- 既存CSV・`*_meta.json` の列やキーは削除・改名しない。追加のみ（過去セッションとの後方互換）

---

## 5. Claude Code の実行環境上の制約

- 計測系（Tobii SDK、OBS、VALORANT、pywin32、pygame表示）はこの環境では動かない。計測コードの変更は **最小限かつ追加的** にし、ユーザーが研究室PCで確認する手順を必ず添える
- 解析系（pandas / numpy）は合成データで検証できる。解析ロジックを変えたら pytest で検証する
- `data/` は git 管理外。実データは手元にない前提で、テスト用データは合成する
- ユーザーの実行環境は Windows + PowerShell（研究室PC raytrek、`C:\Users\kawalab\...`）。コマンド例は PowerShell 形式で書く。Windows PowerShell 5.1 では `&&` が使えないので、連続実行は `; if ($?) { ... }` を使う
- 秘密情報（OBS WebSocketパスワード、制御token）をコード・README・コミットに書かない

---

## 6. 進め方・コーディング規約

- コメント、docstring、CLIメッセージは日本語
- 実装の前に「診断（何が問題か）→ 仮説 → 変更方針」を短く示す。変更で **新たに生じるリスクやバグの可能性を先に指摘する**
- 研究上の定義（閾値、分母、区間、除外基準）に関わる判断は勝手に決めず、選択肢と推奨を示してユーザーに確認する
- コードを提示するときは関数を省略しない（`...` による省略は統合ミスの原因になるので禁止）
- 接続/開始/停止のような複数フェーズは、結果を別フィールドで記録する（共有フィールドの上書き禁止）
- ファイルはバージョン付き（`_v4`, `-ver2` など）で運用している。新版は新ファイルで作り、**旧版は同じコミットで `archive/` に移す**。生きている版を2つ残さない。import 先も同時に更新する
- ブランチ：`main` は動作確認済みのみ。作業は `feature/xxx` / `fix/xxx`。main に直接コミットしない
- コミットメッセージは日本語で、何を・なぜ変えたかを書く

---

## 7. 既知の問題（要約。詳細と対応方針は docs/IMPROVEMENT_PLAN.md）

2026-10-09 時点の対応状況は IMPROVEMENT_PLAN.md 冒頭の「進捗」を参照。1・2・3・7・8 はコード上は対応済み、
4・5・6 は計測・判断待ち（実機確認は docs/LAB_CHECKLIST.md）。

1. **注視の定義が近似**：`aoi_analysis-ver2.py` の `fixation_count` は「同一AOIに属する連続サンプル区間（run）の数」で、注視検出をしていない。60 Hzの揺らぎでAOI境界付近の run が水増しされ、平均注視時間が短く出る（特にクロスヘア）
2. **区間除外が手作業で行削除**：削除後に run を計算するため、死亡前と次ラウンドのサンプルが1つの run に連結されうる。欠測（瞬目）をまたいだ連結も同様
3. **ゼロ件AOIが消える**（ver2）。※ 調べたところ、リポジトリの `aoi_analysis-ver2.py` の中身は docstring が「ver.3」の改良版で、ゼロ件AOIの修正は入っていた（ファイル名だけ ver2 のままだった）。現在は `archive/aoi_analysis-ver2.py`
4. **画面端の精度低下**：上下端のHUDを見ても領域外に判定される現象を観察済み。ゼロ件AOI（round_timer, enemy_team_status, ammo_weapon, credits）は「見ていない」のか「測れていない」のかを区別できていない
5. **眼−画面距離を記録していない**：視角ベースのAOI・I-DT閾値の換算に必要
6. **AOI設定の数値の根拠が不一致**：crosshair 半径 0.0351（=135 px）は「60 cmで半径2°」に相当し、実験条件の65 cmでは約1.85°。半径か直径かも明記されていない。`screen_name` が `1366x768` のまま
7. **requirements.txt が古い**：`keyboard`（使用禁止）が残り、`pandas` / `obsws-python` / `PyQt5` が無い
8. **README に OBS WebSocket パスワードが平文で載っている**（公開リポジトリ、git履歴にも残る）

---

## 8. 作業開始時のチェック

1. `docs/IMPROVEMENT_PLAN.md` を読み、今回どのタスクに当たるかを確認する
2. `aoi_analysis-ver3.py` がローカルにあるかユーザーに確認する。あればまずコミットしてもらい、それを起点にする
3. 計測コードに触る場合、それが本実験前の凍結期限（10月末）より前かを確認する
