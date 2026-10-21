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
| 欠測（瞬目）の扱い（2026-10-09 合意） | 瞬目を再訪としてカウントしない。欠測の長さ（有効サンプルの時刻差 − 1サンプル間隔）が 200 ms 以下なら訪問・遷移はつながっているとみなす（同一AOIなら同じ訪問）。200 ms 超は追跡ロスとして訪問を切る。**座標の補間はしない**：注視（I-DT）は 75 ms 超の欠測で分割したまま。閾値はパラメータ（`max_gap_ms` / `visit_merge_max_gap_ms`）で、予備実験の欠測長の分布（qc.json の `missing_gaps`）で確認する |
| サンプル分類 | AOI内の視線位置 ／ AOIへの注視 ／ 非注視のAOI内サンプル ／ 無効・判定不能。「見ただけ」という用語は使わない。「AOIへの注視」（`aoi_fixation`）は、サンプルの AOI と、そのサンプルが属する注視の AOI（重心で判定）が同じ場合だけ（2026-10-13 合意） |
| 時刻の基準（2026-10-13 合意、決定事項4） | 注視時間と欠測ギャップの判定は `system_time_stamp_us`（デバイス側の時刻）。`pc_time_sec` はホストのコールバック到着時刻で、PC 側の受信遅延を欠測と誤認し注視・訪問を切るため（5分間テスト：受信遅延 30件中27件はデータ欠落なし、時刻間隔の SD は pc 2.5 ms / system 0.54 ms、欠測件数 194 → 48 件）。区間付与と動画同期は `pc_time_sec` のまま |
| 眼−画面距離（2026-10-13 合意） | 650 mm は**基準**であり固定ではない（顎台なしで頭部は固定できない）。主解析はセッションごとの実測値＝分析区間内の有効な gaze_origin z（眼−トラッカー距離）の中央値を視角換算に使う（`eye_screen_distance_source: "measured"`、既定）。650 mm 固定は感度分析（`analysis_params_sensitivity_fixed650.json`）。5分間テストでは較正時 約645 mm → 視聴中 約575 mm。Gaze % は距離に依存しない（注視系の指標のみ影響） |
| 記録する条件 | マップ、攻守、エージェント（ロール）、ミニマップ設定、ゲーム解像度、眼−画面距離 |
| 実験環境 | 眼−画面距離 約65 cm（基準）、顎台なし、椅子・モニター・Spark位置を床/机のテープで固定、各セッション前にキャリブレーション。**Spark の角度は固定し、測定ごとに動かさない**（2026-10-13。動かすと ETM の画面設定とずれ、上下端で中央寄りの誤差が出る）。個人差は椅子の高さと頭部位置ガイド（`--reference` に標準姿勢）で合わせる |
| ETM の画面設定（2026-10-20 合意） | **screen angle 5 / eye tracker angle 17（Spark と画面の相対角度 12°）**、画面 596.7×335.6 mm。以後変えない。相対角度 3/12/20° を比較して決めた（20° は上下端の中央寄りが再現して除外、3° と 12° は釣り合いを取った順番で各4回比べ、前のめり姿勢の精度と上部 HUD の縦ずれで 12° が良かった。詳細は LAB_CHECKLIST 2c）。外装の角度（分度器：画面 5°、Spark 約 3°）はセンサー軸と一致しないので ETM の値の根拠にしない。calib JSON の `display_angle_deg` で毎回 12.0 を確認する |
| 検証で分かった限界（2026-10-20） | 下のバーはどの角度でも約 0.4〜0.7° 上（中央）向きにずれる。前のめり姿勢（約 585 mm）では標準姿勢より平均 accuracy が約 0.2〜0.4° 悪く、HUD 点の最大誤差は 1.5° を超える（credits、minimap、enemy_team_status）。卒論の限界として記載する |
| credits の扱い（2026-10-21 合意） | **主指標の群間比較から外し、探索指標として報告する**。出力からは消さない（ゼロ件 AOI も行を出す原則は維持）。理由：短辺 0.84° で測定精度（約 0.5〜1°）に対して小さすぎる（aoi_check の `flag_below_min_size`）うえ、主に購入フェーズ（分析区間外）で見る要素のため。LAB_CHECKLIST 4b |
| 決定事項2：offscreen の分母（2026-10-21 合意） | **Gaze % の分母に画面外の有効サンプル（offscreen）を含める**（`offscreen_in_denominator: true`、既定のまま）。Gaze % は「有効な視線全体のうち、その AOI を見ていた割合」。10/08 の「分母＝有効サンプル数」と一貫し、画面外を見た時間を消すと HUD の割合が見かけ上大きくなるため。offscreen が 1% を超える参加者が出たら、含めない場合を感度分析として併記する（10/20 の通し確認では 0%） |
| 除外基準（2026-10-21 合意） | 区間内の有効率 ≥ 0.7、同期が confirmed_based かつ期待範囲内、較正が合格、解像度が一致、実測の眼−画面距離と基準 650 mm の差 ≤ 100 mm（`docs/templates/qc_criteria_template.json`）。**未決**：分析時間の下限（ゲームモードが決まってから）、区間の coverage の下限（予備実験の分布を見てから）。台帳の巻尺の距離との差は使わない（台帳の距離の欄は空欄の運用）。較正の合格ライン（全点平均 ≤ 1.0°、HUD 各点 ≤ 1.5°）は案のまま |
| crosshair の半径（2026-10-21 合意） | **`radius_deg: 2.0`**（設計距離 650 mm で換算、正規化半径 0.0380 / 0.0676）。旧値 0.0351（135 px）は「60 cm で半径 2°」由来で、65 cm では 1.85° と根拠が説明しにくかったため。根拠：中心窩（約 2°）に測定精度（0.4〜1°）の余裕を含む大きさ。AOI の中身が変わるので、10/21 より前の ver3 の結果は `--overwrite` で解析し直す |
| ゲームモード（2026-10-21 再確認） | **本実験はアンレートのまま進める**。参加者が割ける時間は最大 約100分の見込みで、アンレート1試合（35〜50分）＋説明・較正・事後検証で収まる。スイフトプレイ1試合は生存中の分析時間が約 263 s（10/20 実測）で、上部 HUD のサンプルが 0.5 秒分しかなく少なすぎる。予備実験でアンレート1試合を通して所要時間と分析時間を確かめ、分析時間の下限を決める |

スケジュール：10/19–25 予備実験（取得→録画→同期→分析の通し確認）、10/26–31 条件確定、11月 本実験。
**本実験の前に計測コードを凍結する**。それ以降の計測側の変更は不具合修正のみ。

---

## 3. 現在のパイプライン

1セッションを通すコマンドの手順は `docs/RUN_PIPELINE.md`（研究室PC用、PowerShell）。

```
[較正] src/gaze_estimation/calibrate_validate.py  (1.3.0) 試合前に9点キャリブレーション＋検証 → data/raw/calib_<subject>_<日時>.json（SDK 較正の失敗は不合格）
[計測] src/gaze_estimation/tobii_capture_with_sync_flash_v5.py   (SCRIPT_VERSION 5.1.1-eye-origin-pupil。v4.1 に眼位置z・瞳孔の列を末尾追加。5.0.1 でログからパスワード・token を除去。5.1.0 で眼位置 x/y を末尾追加。5.1.1 で meta に解析の時刻列を記録)
         + src/obs_controller_v2.py  (obsws-python / OBS WebSocket v5 で録画を自動開始・停止)
         → data/raw/gaze_<subject>_<trial>_<condition>_<YYYYMMDD_HHMMSS>.csv / _meta.json / ログ
         開始・停止は同一LAN上の別端末ブラウザから HTTP（既定 port 8765、token 付き）
[同期] src/visualizer/gaze_visualizer_v3.py auto-sync <csv> <mp4>
         offset_sec = start_sync.delta_sec_confirmed_based + data_quality.first_sample.pc_time_sec
         gaze_time(pc_time_sec) = video_time + offset_sec
[可視化] gaze_visualizer_v3.py render <csv> <mp4> --modes beeswarm scanpath heatmap
[区間]   src/visualizer/segment_annotator.py <mp4> --session-base <session_base>
         → data/annotations/<session_base>_segments.csv（動画時刻。行削除による区間除外は廃止）
[AOI解析] src/aoi_detector/aoi_analysis-ver3.py (3.2.0-idt-segments) --input <csv> --aoi ... --segments ... --sync <*_sync.json> --manifest ...
         → data/processed/aoi_result/<session_base>/（I-DT 注視検出。ロジック本体は gaze_metrics.py / aoi_geometry.py）
[集計]   src/analysis/aggregate_sessions.py --manifest data/sessions_manifest.csv → data/processed/aggregate/
         （感度分析は --results-dir aoi_result_fixed650 --out-dir aggregate_fixed650。区間なしの結果は既定で合算しない）
[台帳]   data/sessions_manifest.csv（実験者が記入。テンプレートは docs/templates/）
```

### 視線CSVの列（`FIELDNAMES`）
`wall_timestamp_local, wall_timestamp_utc, pc_time_sec, device_time_stamp_us, system_time_stamp_us, left_x, left_y, right_x, right_y, center_x, center_y, left_gaze_point_validity, right_gaze_point_validity, gaze_missing`
（v5 で末尾に追加）`left_gaze_origin_z_mm, right_gaze_origin_z_mm, left_gaze_origin_validity, right_gaze_origin_validity, left_pupil_diameter_mm, right_pupil_diameter_mm, left_pupil_validity, right_pupil_validity`
（v5.1.0 で末尾に追加）`left_gaze_origin_x_mm, left_gaze_origin_y_mm, right_gaze_origin_x_mm, right_gaze_origin_y_mm`（眼位置 UCS の x, y [mm]）

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
- 区間除外は「行の削除」ではなく区間定義ファイルで行う。注視・訪問・遷移が区間境界をまたいで連結されてはならない。欠測については、注視は 75 ms 超の欠測をまたがず、訪問・遷移は 200 ms 以下の欠測（瞬目相当）だけをまたいでつなぐ（上の合意事項）
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
- **検証結果と決定事項は必ず記録に残す**（2026-10-20 ユーザー指示）。後で卒論の方法・限界の章や再確認に使うため、会話の中だけで終わらせない
  - 研究上の決定 → この CLAUDE.md の §2 の表に日付付きで追記（何を、なぜ、根拠の検証はどこか）
  - 実機での検証結果 → `docs/LAB_CHECKLIST.md` の該当節に `> YYYY-MM-DD の結果` として、条件・回数・主要な数値・結論・弱点を書く
  - タスクの進捗 → `docs/IMPROVEMENT_PLAN.md` 冒頭の「進捗」表

---

## 7. 既知の問題（要約。詳細と対応方針は docs/IMPROVEMENT_PLAN.md）

2026-10-09 時点の対応状況は IMPROVEMENT_PLAN.md 冒頭の「進捗」を参照。1・2・3・7・8 はコード上は対応済み、
4・5・6 は計測・判断待ち（実機確認は docs/LAB_CHECKLIST.md）。

1. **注視の定義が近似**：`aoi_analysis-ver2.py` の `fixation_count` は「同一AOIに属する連続サンプル区間（run）の数」で、注視検出をしていない。60 Hzの揺らぎでAOI境界付近の run が水増しされ、平均注視時間が短く出る（特にクロスヘア）
2. **区間除外が手作業で行削除**：削除後に run を計算するため、死亡前と次ラウンドのサンプルが1つの run に連結されうる。欠測（瞬目）をまたいだ連結も同様
3. **ゼロ件AOIが消える**（ver2）。※ 調べたところ、リポジトリの `aoi_analysis-ver2.py` の中身は docstring が「ver.3」の改良版で、ゼロ件AOIの修正は入っていた（ファイル名だけ ver2 のままだった）。現在は `archive/aoi_analysis-ver2.py`
4. **画面端の精度低下**：上下端のHUDを見ても領域外に判定される現象を観察済み。ゼロ件AOI（round_timer, enemy_team_status, ammo_weapon, credits）は「見ていない」のか「測れていない」のかを区別できていない
5. **眼−画面距離を記録していない**：視角ベースのAOI・I-DT閾値の換算に必要（→ v5 で眼位置 z を記録、ver3 は既定でセッションごとの実測中央値を使う。2026-10-13）
6. **AOI設定の数値の根拠が不一致**：crosshair 半径 0.0351（=135 px）は「60 cmで半径2°」に相当し、実験条件の65 cmでは約1.85°。半径か直径かも明記されていない。`screen_name` が `1366x768` のまま（→ 2026-10-21 に `radius_deg: 2.0`（半径）に定義し直した。`screen_name` は修正済み）
7. **requirements.txt が古い**：`keyboard`（使用禁止）が残り、`pandas` / `obsws-python` / `PyQt5` が無い
8. **README に OBS WebSocket パスワードが平文で載っている**（公開リポジトリ、git履歴にも残る）

---

## 8. 作業開始時のチェック

1. `docs/IMPROVEMENT_PLAN.md` を読み、今回どのタスクに当たるかを確認する
2. `aoi_analysis-ver3.py` がローカルにあるかユーザーに確認する。あればまずコミットしてもらい、それを起点にする
3. 計測コードに触る場合、それが本実験前の凍結期限（10月末）より前かを確認する
