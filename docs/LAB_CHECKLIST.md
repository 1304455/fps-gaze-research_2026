# 研究室PCでの確認手順（2026-10-09 の改良分）

Claude Code の環境では Tobii SDK・OBS・VALORANT・画面表示を動かせないため、計測側の変更は
研究室PC（Windows + PowerShell）で以下を確認してから main に入れる。
Windows PowerShell 5.1 では `&&` が使えないので、連続実行は `; if ($?) { ... }` を使う。

## 0. 環境とテスト（5分）

```powershell
cd C:\Users\kawalab\...\fps-gaze-research_2026
python -m venv .venv-check
.\.venv-check\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt; if ($?) { python -m pytest }
```
- [ ] `pip install` がエラーなく通る（T0 受け入れ基準）
- [ ] `python -m pytest` が全件 passed

## 1. OBS パスワードの変更（必須・T0）

旧 README に OBS WebSocket のパスワードが平文で載っており、公開リポジトリの **git 履歴に残っている**。
削除だけでは不十分なので、パスワードを変える。

> 2026-10-21：パスワードは変更済み（ユーザー確認）。10/20 の計測で obs_config.json から OBS に接続できている。

- [x] OBS →「ツール」→「WebSocket サーバー設定」で新しいパスワードを生成
- [x] `Copy-Item .\docs\templates\obs_config_template.json .\obs_config.json` → `obs_password` を記入
- [ ] `git status` に obs_config.json が出ない（.gitignore 済み）

## 2. キャリブレーション＋検証ツール（T2）

```powershell
python .\src\gaze_estimation\calibrate_validate.py --check-only
```
- [ ] 「enter/leave_calibration_mode: 成功」と出る（失敗なら以降は `--validate-only` を使う）

```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject TEST
```
- [ ] 全画面に点が順に出て、注視するとキャリブレーション → 検証に進む
- [ ] `data\raw\calib_TEST_*.json` に点ごとの `accuracy_deg` / `precision_rms_s2s_deg` が入っている
- [ ] 画面端の点（`aoi_credits`, `aoi_round_timer`, `aoi_ammo_weapon` など）の accuracy を控える

```powershell
python .\src\aoi_detector\aoi_check.py --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --calib (Get-ChildItem .\data\raw\calib_TEST_*.json | Select-Object -Last 1).FullName
```
- [ ] 「測定精度に対して小さすぎるAOI」の一覧を確認する

### 2b. 頭部位置ガイドと較正の改良（2026-10-09 追加）

`calibrate_validate.py` は、キャリブレーションの前に頭部位置ガイドを表示するようになった
（省略は `--skip-position-guide`、ガイドだけなら `--position-only`）。

```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --position-only
```
- [ ] **左右の向きの確認**：頭をゆっくり自分の右へ動かすと、画面の円と＋印も右へ動く
      （逆なら以降すべてのコマンドに `--flip-x` を付け、結果を Claude に伝える）
- [ ] 前後に動くと距離の線が動き、650 mm 付近で緑の範囲に入る
- [ ] `data\raw\headpos_TEST_*.json` に `head_position.recorded`（x/y/z）が保存される

較正点の位置の比較（同じ人・同じ姿勢で、順番を入れ替えて各2回）：
```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --calib-margin 0.1
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --calib-margin 0.05
```
- [ ] 上下のバー（ally/enemy_team_status, health_armor, abilities）の accuracy と `aoi_check.py` の
      `bias_y_deg` を比べる。0.05 の方が上下端のずれ（上のバーは下向き、下のバーは上向き）が小さければ 0.05 を採用
- [ ] 誤差が 1.5° を超えた点は自動で1回測り直される。`summary.retried_points` と各点の `first_attempt` を確認

> 2026-10-09 の結果（各1回）：0.05 は全体平均が同等（0.81°）で、HUD 9点中6点が悪化し、測り直しも4点（0.1 は1点）。
> **0.1 を継続**。上下端の中央向きのずれ（上 +0.3〜1.2°、下 −0.4〜0.9°）はどちらでも残った。

画面の物理設定の確認（上下端のずれの原因候補）：
`aoi_check.py --calib ...` が「画面の設定（トラッカー）」を表示する。2026-10-09 の calib では
トラッカー側が 607.0×341.4 mm・Spark との相対角度 20.0°（ETM の設定値。画面そのものの傾きではない）、AOI JSON は 596.7×335.6 mm だった。
- [ ] 画面の表示領域（黒枠の内側、映像が出る範囲）の幅・高さを巻尺で実測する
- [ ] ~~モニターの傾きを実測する~~ → 10/13：ETM の角度は Spark と画面の相対角度で、外装の角度とは直接比べられないと判明（2c を参照）
- [ ] Eye Tracker Manager のディスプレイ設定（Spark の取り付け・画面サイズ）と実測値を比べる。
      違っていれば設定し直して、キャリブレーション＋検証をやり直す
- [ ] AOI JSON の `display.width_mm / height_mm` も実測値に合わせる（結果を Claude に伝える）

複数回の結果の比較（2026-10-13 追加。画面設定や margin を変えたときに同じ物差しで比べる）：
```powershell
python .\src\analysis\calib_compare.py ".\data\raw\calib_TEST_20261013_*.json"
python .\src\analysis\calib_compare.py ".\data\raw\calib_TEST_*.json" --group-by display_angle_deg
```
- [ ] `vertical_scale_pct`（縦の中央寄り%。0 が理想）と `top_bar_bias_y_deg` / `bottom_bar_bias_y_deg` を条件ごとに比べる
- [ ] `display_angle_deg` / `display_width_mm` が混ざっていると警告が出る（条件比較では画面設定を揃える）

> 2026-10-13 の結果（今日の4回）：全回合格（平均 0.76〜0.99°）。縦の中央寄りは 2.1〜4.9%、横は約 0〜1%。
> 10/09 と同じパターンで、画面寸法の設定を変えても残った → 寸法ではなく角度の設定が原因の本命。
> Spark の角度を測定ごとに手で調整していたことが判明（ETM の角度設定は Regular setup の 20° のまま）。

前回の位置に合わせる（2回目以降のセッション）：
```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --reference (Get-ChildItem .\data\raw\calib_TEST_*.json | Select-Object -Last 1).FullName
```
- [ ] 上下の位置も判定に加わり、前回とほぼ同じ姿勢で OK になる

### 2c. Spark の角度の固定と測定（2026-10-13 追加）

Spark の角度を測定ごとに手で変えると、トラッカーが想定する画面（ETM の設定）と実際の画面がずれ、
上下端で中央寄りのずれが出る。**角度は一度決めたら固定し、個人差は椅子の高さで合わせる。**

1. 標準の椅子の高さで普段どおり座り、`--position-only` でガイドの中央（y ≈ 0）に眼が来るよう Spark の角度を決める
2. 机と Spark にテープで印を付け、以後触らない（触ったら手順 4〜5 をやり直す）
3. 分度器で角度を測る（各3回の平均。写真も残す）
   - 分度器の底辺をミニ水平器で水平に合わせ、利用者側から見た角度を読む
   - 画面：画面の表面に定規（または硬いカード）を当て、机からの角度 θ_screen を読む（後ろに倒れていれば 90° より大きい）
   - Spark：前面（黒いセンサー面）にカードを当て、同じ向きで θ_spark を読む
   - 相対角度 = θ_spark − θ_screen
   - 確かめ（任意）：画面上端から糸＋重り（5円玉など）を垂らし、上端と下端での画面表面と糸の距離の差 d と
     表示領域の高さ h から、画面の傾き = atan(d / h)
4. ETM で相対角度（と画面寸法 596.7×335.6 mm）を入力し直し、較正＋検証を2回
5. `calib_compare.py` で `display_angle_deg` が入力値になっていること、`vertical_scale_pct` が 1〜2% 以下に下がることを確認
6. 標準姿勢を記録：`calibrate_validate.py --subject STANDARD --position-only` → 以後の全参加者は
   `--reference .\data\raw\headpos_STANDARD_<日時>.json` で同じ位置に合わせる（椅子の高さで調整）

> 2026-10-13：標準姿勢を記録（`headpos_STANDARD_20261013_132612.json`、眼位置 UCS x −1.7 / y −44.8 / z 661.4 mm）。
> 眼位置は ETM の画面設定に依存しないので、角度設定を変えた後もこの記録を使える。

#### 2026-10-20 の結果：ETM の相対角度の決定（→ 12° を採用）

採用設定：**ETM screen angle 5 / eye tracker angle 17（相対角度 12°）**、画面 596.7×335.6 mm。以後変えない。

物理的な寸法（標準姿勢）：眼の高さ 118〜119 cm（床から）、机の上面 71.6 cm、机から Spark 20.8 cm。
分度器（外装に定規を当てて鉛直からの傾き）：画面 5°、Spark 約 3°（2回測って同じ）。
- 外装の角度はセンサー軸と一致しない（10/13 の結論どおり）。ETM に 5 / 8 を入れると相対角度は 3° になったが、
  これは外装の値で、根拠にならない。ETM の値は下の比較で**データから決めた**
- ETM の2つの角度はどちらも鉛直基準で、トラッカーが使うのは差（calib JSON の `display_angle_deg` で確認できる）

手順（各回）：標準姿勢で較正＋検証 → 続けて前のめり姿勢で検証のみ
```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --calib-margin 0.1 --reference .\data\raw\headpos_STANDARD_20261013_132612.json
python .\src\gaze_estimation\calibrate_validate.py --subject TEST --validate-only --target-distance-mm 580
```

比較1（3 / 12 / 20°、各2回、順番 3→12→20 ×2。順番の釣り合いなし）：
- 20°：標準姿勢で `vertical_scale_pct` 6.5〜7.8%、上のバー +0.44〜+1.43°（下向き）、合格 0/2 → 10/13 と同じ症状が再現。**除外**
- 3° と 12°：標準姿勢の平均 accuracy は同等。前のめり姿勢の HUD 最大誤差（1.73 vs 1.34°）と上のバーのずれ（−1.03 vs −0.23°）で 12° が良い

比較2（3° vs 12°、各4回、順番 3→12→12→3→3→12→12→3 で釣り合いを取る。判定ルールは測定前に決めた）：

| 指標（平均） | 3° | 12° | 隣り合う組で 12° が良かった数 |
|---|---|---|---|
| ① 標準姿勢 HUD 最大誤差 | 1.57° | 1.38° | 2/4 |
| ② 前のめり HUD 最大誤差 | 1.89° | 1.75° | 3/4 |
| ③ 前のめり 上のバーのずれ（絶対値） | 0.89° | 0.32° | 4/4 |
| 前のめり 平均 accuracy | 1.29° | 1.06° | 4/4 |
| 標準姿勢 平均 accuracy | 0.84° | 0.83° | 1/4 |
| 標準姿勢 `vertical_scale_pct` | 1.1% | 3.6% | — |

判定ルール（①〜③の平均がすべて良く、うち2つ以上で4組中3組以上勝つ）を 12° が満たした → **12° を採用**。

解釈と弱点：
- 較正した位置（標準姿勢）では角度の設定の差を較正が吸収する。差が出るのは頭が動いたとき（前のめり姿勢）で、試合中に近いのはこちら
- ① は組ごとの差が +0.44〜−0.91° とばらつき、単独では決め手にならない
- 12° では上のバーがわずかに下向き（+0.3°）、3° では上向き。ずれが 0 になる角度は 3〜12° の間にありそうだが、
  下のバーの一定のずれより小さいので追い込まない
- 標準姿勢の `vertical_scale_pct` は 12° の方が大きい（全検証点の直線近似で、HUD 位置の誤差とは一致しない）。卒論では併記する
- 被験者は1人（実験者本人）

限界として記録すること（角度によらない）：
- 下のバーは約 0.4〜0.7° 上（画面の中央）向きにずれる
- 前のめり姿勢（約 585 mm）では平均 accuracy が標準姿勢より約 0.2〜0.4° 悪く、HUD 最大誤差は 1.5° を超える
  （credits、minimap、enemy_team_status）。左上隅（grid_0.1_0.1）の有効率が 0.2〜0.7 に下がり、測り直しが増える
- [ ] 予備実験のデータで試合中の実際の眼の距離（ver3 の `eye_distance_used`）を確認し、580 mm の検証が代表的かを確かめる
- [ ] 以後の各セッションの calib JSON で `display_angle_deg` が 12.0 であることを確認する
- 注意：P01（`calib_P01_20261020_105724.json`）は相対角度 3° で測った。台帳の notes に記入済みにすること

#### 2026-10-28 の結果（途中）：第三者がモニターと Spark を動かした

> 症状：較正がうまく働かなくなった。いつ動かされたかは不明。ETM の設定と表示の設定は変わっていない（`display_angle_deg` 12.0）。
>
> | 項目 | 10/20（基準） | 10/28（発見時） |
> |---|---|---|
> | 机から Spark | 20.8 cm | 19.8 cm |
> | 分度器（鉛直から）画面 / Spark | 5° / 約 3° | 3° / 約 8° |
> | 眼位置（いつもの椅子の高さ） | 標準姿勢 y −44.8 / z 661.4 mm | 標準姿勢から大きく外れる |
> | 較正＋検証 平均 accuracy | 0.68° | 0.78° / 0.72°（2回） |
> | HUD 最大誤差 | 0.99° | 1.29° / 1.15° |
> | `vertical_scale_pct` | 2.7% | 1.89% / 0.33% |
>
> 解釈（途中）：Spark の位置と角度の両方が変わっている（眼位置の UCS はトラッカー基準なので、ずれは Spark が動いた証拠）。
> 較正＋検証は合格ライン内だが、10/20 より HUD 最大誤差が約 0.2〜0.3° 悪い。また較正した姿勢では角度設定の差を較正が吸収するため
> （10/20 の比較）、この結果だけでは 12° の設定が新しい配置で妥当かは言えない。
> 方針：配置を 10/20 の値（20.8 cm、画面 5°、Spark 約 3°）に戻し、標準姿勢の眼位置が再現することを確認してから、
> 標準姿勢の較正＋検証と前のめり姿勢の検証を行い、10/20 の 12° の値と比べる。
> - [ ] 配置を戻した後の眼位置・較正＋検証・前のめり検証の結果を追記する
> - [ ] 10/20〜10/28 に取ったセッションがあれば、配置が変わっていた可能性を台帳の notes に記入する

## 3. 計測スクリプト v5 の5分間テスト（T1）

> 2026-10-13 実施（過去の試合映像を視聴）：60.1 Hz、欠測率 0.7%、start_sync（confirmed）23 ms（既知 18.8±2.5 ms の範囲内）。
> 新8列に値あり。**眼−トラッカー距離の中央値は 575 mm**（較正時は約 645 mm）。視聴中に約 7 cm 前に出ていた。
> 視線CSVの「sample_gap」警告 30 件中 27 件は PC 側の受信遅延（デバイス時刻の間隔は 16〜17 ms）で、データ欠落ではない。
> ログに OBS のパスワードが出ていた（obsws-python の INFO ログ）→ v5.0.1 で除去。パスワードは変更すること。

```powershell
python .\src\gaze_estimation\tobii_capture_with_sync_flash_v5.py --subject TEST --trial 1 --skip-conditions-prompt --obs-config .\obs_config.json --max-duration 300
```
- [ ] 起動・録画開始・停止が v4 と同じように動く（別端末ブラウザから開始/停止）
- [ ] `--subject` を付けずに起動すると NA の警告が出る（止まらない）

```powershell
$csv = (Get-ChildItem .\data\raw\gaze_TEST_*.csv | Select-Object -Last 1).FullName
python -c "import pandas as pd, sys; d=pd.read_csv(sys.argv[1]); print(d[['left_gaze_origin_z_mm','right_gaze_origin_z_mm','left_pupil_diameter_mm','right_pupil_diameter_mm']].describe())" $csv
```
- [ ] 新しい8列に値が入っている（z は 600〜700 mm 程度、瞳孔は 2〜8 mm 程度）
- [ ] z の中央値と、巻尺で測った眼−画面距離の差を控える（卒論で「近似」として書く材料）
- [ ] `gaze_visualizer_v3.py auto-sync` と `render` がこの CSV でそのまま動く

> 2026-10-13：v5.1.0 で眼位置 x/y の4列を末尾に追加した。`git pull` 後に短い計測（`--max-duration 60`）で
> 列が増えていること、コンソールに制御 URL（token 付き）が出てログファイルでは `token=***` になることを確認する。
> 同じ日の 5.1.1 で meta.json の `time_reference_notes` に `analysis_time_columns` を追加した（CSV は変わらない）。

- [ ] 短い計測の `_meta.json` に `time_reference_notes.analysis_time_columns` がある
- [ ] calibrate_validate（1.3.0）：ディスプレイ番号に `0` を入れると入力し直しになる（以前は最後の画面が選ばれていた）
- [ ] calibrate_validate：保存された calib JSON の `summary.calibration_ok` が `true`（SDK 較正）／`null`（`--validate-only`）。
      較正が失敗した回は `false` で、精度によらず不合格になる

## 4. 予備実験データで新旧の解析を比べる（T3・T4）

予備実験の1試合（`_proc.csv` を作ってあるもの）で：

> 台帳・区間ファイルを Excel で編集したら『CSV UTF-8（コンマ区切り）』で保存する（cp932 は警告付きで読む）。
> `--compare-proc` は時刻の最近傍で照合するので、Excel で丸めた `_proc.csv` でも突き合わせられる（`n_unmatched_old` が 0 に近いこと）。

```powershell
python .\src\visualizer\segment_annotator.py "<動画>.mp4" --session-base <session_base>
python .\src\aoi_detector\aoi_analysis-ver3.py --input .\data\raw\<session_base>.csv --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --segments .\data\annotations\<session_base>_segments.csv --sync "<動画>_sync.json" --compare-proc .\data\processed\<session_base>_proc.csv
```
- [ ] 1試合（約13ラウンド）の区間付けが、動画を1回通して見る程度の時間で終わる
- [ ] `qc.json` の `compare_with_proc.jaccard` が 1 に近い（ずれていれば、どのラウンドの境界かを確認）
- [ ] 同じ区間で、`gaze_pct` が旧版（archive\aoi_analysis-ver2.py を `_proc.csv` に適用）とほぼ一致する
- [ ] `qc.json` の `time_base_comparison.interval_difference_sd_ms` を控える（決定事項4は 10/13 に system_time で合意済み。実プレイでも同じ傾向かの確認）
- [ ] `fixations.csv` の `duration_sec` の分布を見る（I-DT の閾値が自分のデータで妥当かの確認）
- [ ] 感度分析（距離 650 mm 固定）は出力先を分けて実行する（同じ出力先では条件の違う結果の上書きを拒否する）：
      `... --params .\src\aoi_detector\analysis_params_sensitivity_fixed650.json --output-dir .\data\processed\aoi_result_fixed650`。
      集計も `--results-dir .\data\processed\aoi_result_fixed650 --out-dir .\data\processed\aggregate_fixed650` で分ける
- [ ] 区間を直して解析し直すときは `--overwrite` が要る（区間ファイル・同期オフセットの違いも上書き防止の対象）
- [ ] coverage の警告（区間の行数が区間長の 0.9 未満）が出ないこと。出たら、その round が記録の範囲外にはみ出していないか確認する
- [ ] `qc.json` の `data_quality.missing_gaps` を見る。瞬目は通常 100〜300 ms 程度なので、
      `200-300ms` の件数が多ければ `visit_merge_max_gap_ms` を 300 にするか検討する。
      `n_same_aoi_visit_breaks_by_long_gap`（長い欠測で切れた同一AOIの訪問数）が再訪数に対して大きくないかも確認

### 4b. パイプラインの通し確認（`docs/RUN_PIPELINE.md` の手順）

> 2026-10-20 の結果（`gaze_TEST_player_1_default_20261020_180551`、スイフトプレイ1試合、録画 12分53秒、生存区間 8、分析時間 263 s）：
> **較正 → 計測 → 同期 → 区間 → ver3（主解析・感度分析）→ 集計 → render がエラーなく最後まで動いた。**ただし台帳の行を書かなかったので、
> 台帳と結合する部分（解像度の確認、`calibration_passed`、参加者集計）は未確認。
>
> | 段階 | 結果 |
> |---|---|
> | 較正（calibrate_validate 1.3.0） | 合格。平均 0.68°、HUD 最大 0.99°、**`display_angle_deg` 12.0**、`vertical_scale_pct` 2.7%、上のバー +0.48°・下のバー −0.44°、測り直し1点 |
> | 計測（v5 5.1.1） | 46486 サンプル、欠測率 3.4%（区間内の有効率 99.5%。欠測は購入・死亡後に集中）、sample_gap 警告2件（55・68 ms） |
> | 同期 | confirmed_based **27 ms**（期待範囲 6.3〜31.3 ms の内側だが上寄り。これまでの実測 22〜26 ms）、method=metadata_confirmed_based |
> | 区間 | 8区間、coverage 1.003〜1.004（はみ出しなし） |
> | ver3 | 注視 925 件（距離 650 mm 固定では 929 件）、offscreen 0。**注視0件の AOI：health_armor、abilities、ammo_weapon、credits（下のバー4つすべて）** |
> | 時刻 | 間隔の SD：pc 1.18 ms / system 0.75 ms |
> | 集計 | 動いたが、セッションが台帳に無いので参加者集計から外れた。台帳にテンプレートの記入例の行（P01）が残っていた |
> | render | 3モードで約 6100 秒（録画の約8倍の時間）。ffmpeg が無く音声なしで出力 |
>
> **試合中の眼の距離（10/20 の確認項目）**：分析区間内の中央値 **633 mm**（5〜95%：608〜658 mm）、セッション全体 643 mm。
> 較正時（663 mm）より 3 cm 前に出ただけで、10/20 の前のめり姿勢での検証（約 585 mm）や 5分間の視聴テスト（575 mm）ほど前には出ていない。
> 前のめり姿勢での精度低下は、この1試合については上限側の見積もりになる（1人・1試合なので、予備実験の他の参加者でも確認する）。
>
> **欠測の長さ**：区間内の欠測 32 件のうち 31 件が 75 ms 以下、1 件が 500 ms 超で、**75〜500 ms は0件**。
> 200 ms の閾値（訪問をつなぐか）はこの試合では結果に影響していない（`n_same_aoi_visit_breaks_by_long_gap` 0）。瞬目が 75 ms 以下の欠測として
> 現れているか、プレイ中は瞬目が少ないか（どちらかはこのデータだけでは区別できない）。閾値の妥当性は予備実験の他のデータで引き続き確認する。

> 2026-10-21 の再実行：台帳の行を書いて主解析を再実行 → `qc.json` に `manifest_row` が入り、**解像度チェックが動いた**（`checked: true`）。
> ただし Excel が `aspect` の `16:9` を時刻 `16:09` に変えていて不一致と判定された（チェックは正しく働いた）。
> `--allow-resolution-mismatch` で通していたので、台帳を直して付けずに再実行が必要。`participant_id` も空だった。
> 感度分析は、行末の `` ` `` の前に空白が無く `--overwrite` に改行が入って失敗（手順書に注意と1行版を追加）。

> 2026-10-21 の再々実行：メモ帳で直した台帳は列が1つずれていた（`session_base` が `TEST_player` に）→ ver3・集計とも警告で検出。
> 台帳を変数から書き出し直して実行 → **主解析・集計で警告なし**。qc_report：`in_manifest` True、`resolution_ok` True、
> `calibration_passed` True（平均 0.68°）、`eye_screen_distance_source` measured（633 mm）、感度分析は fixed（650 mm）。
> 台帳と解析・集計の結合（解像度チェック・較正の合否・参加者集計）が実データで動くことを確認した。
> 感度分析の集計は、`aggregate_fixed650\qc_report.csv` を Excel で開いていたため `PermissionError` で書き出せなかった（閉じて再実行）。
> 台帳の `start_side` は attack と書いたが、注釈は defense で開始していた → 台帳を defense に直す。
> 手順書の台帳の手順は、手編集をやめて変数から追記する方法に変えた（`$side` で注釈と台帳の攻守もそろえる）。
> qc_report の `eye_screen_distance_measured_mm` は台帳の `eye_screen_distance_cm`（巻尺の値）× 10 で、眼位置の実測ではない（今回の 650 は台帳に書いた 65 cm）。

> 2026-10-21：感度分析の集計も Excel を閉じて再実行し成功（fixed 650 mm、注視 929 件、`resolution_ok` / `calibration_passed` True）。
>
> `aoi_check.py --calib calib_TEST_player_20261020_180419.json`（距離 650 mm で換算）の下のバー：
>
> | AOI | 高さ | accuracy | bias_y（負＝上向き） | 中心を見たとき AOI 外 |
> |---|---|---|---|---|
> | health_armor | 2.53° | 0.62° | −0.48° | False |
> | abilities | 2.81° | 0.98° | −0.69° | False |
> | ammo_weapon | 2.53° | 0.89° | −0.29° | False |
> | credits | **0.84°**（1° 未満） | 0.47° | −0.24° | False |
>
> 解釈：health_armor・abilities・ammo_weapon は高さ 2.5〜2.8° に対してずれが 0.3〜0.7° で、中心を見れば AOI 内に入る。
> 上向きのずれで外れるのは AOI の上端付近を見たときだけなので、**「測れていない」だけで注視0件は説明しにくい**（見ていない／短く見ただけの可能性が高い。
> 切り分けは「注視0件の AOI の確認」スクリプトで行う）。**credits は短辺 0.84° で精度に対して小さすぎ**、
> しかも主に購入フェーズ（分析区間外）で見る要素なので、主解析での扱いを決める必要がある（研究上の判断。下のチェック項目）。
> 上部の enemy_team_status も「短辺/2 < accuracy」の警告が出た（高さ 1.82°、accuracy 0.99°）が、注視はあった。

- [x] 台帳の `start_side` を defense に直し、感度分析の集計を Excel を閉じてから再実行する（10/21）
> 2026-10-21「注視0件の AOI の確認」スクリプトの結果：分析区間内で下のバー4 AOI に入ったサンプルは**0件**（`aoi_nonfixation` も無い）。
> 重心 y ≥ 0.80 の注視は 925 件中2件だけで、どちらも左下（x 0.10・0.19、y 0.91・0.95。下のバーの AOI の外）。それ以外の注視は y 0.68 より上。
> AOI のすぐ上（y 0.85〜0.895）に注視が集まる様子も無い → **この試合では、生存中に下のバーを見ていない**（測定のずれで外れたのではない）。
> 1人・1試合なので、予備実験の他の参加者で同じかを確認する。下のバーが全員0件に近いなら、群間比較は床効果になる（購入フェーズを副解析にするかは要検討）。
> 注：PowerShell からスクリプトを渡したとき日本語の見出しが「??」になった（`$OutputEncoding` が ASCII のため）。手順書に1行追加した。

- [x] 「注視0件の AOI の確認」スクリプト（RUN_PIPELINE.md）の出力で、下のバーを見ていないのか、短く見ただけなのかを確かめる（10/21：見ていない）
- [x] credits の扱いを決める → 10/21：探索指標に回す（CLAUDE.md §2）
> 2026-10-21 参考：9/9 の予備実験1試合目（`gaze_NA_1_default_20260909_135707.csv`、v4、スイフトプレイ、16分、ETM 20° の頃）を区間なし（記録全体）・650 mm 固定で ver3 にかけた。
> 注視 2790 件。下のバーは abilities 29 件、health_armor 2 件、ammo_weapon 2 件、credits 0 件。offscreen は有効の 1.0%（下 320・上 122・左 107）。
> 記録全体（購入フェーズ・観戦を含む）なので生存中との比較はできないが、10/20 の通し確認（生存中は下のバー0件）と合わせると、
> **下のバーは主に購入フェーズなど生存中以外に見られている**可能性がある。動画が残っていれば区間を付けて確かめる。

- [ ] 予備実験の他の参加者でも、下のバーの注視が0件に近いか確認する
- [ ] （保留 10/21）9/9 の予備実験の動画が残っていれば、区間を付けて下のバーを「生存中」と「それ以外」に分けて確かめる
- [ ] （保留 10/21）奇数・偶数ラウンドで Gaze % を比べる信頼性の確認スクリプト（1試合で値が安定するか）
- [ ] アンレートの予備実験を1試合通す：所要時間、生存中の分析時間（分析時間の下限を決める）、区間付けの時間、試合後の検証（RUN_PIPELINE 2b）による精度の変化
- [ ] 10/21 に crosshair を `radius_deg: 2.0` に変えたので、それより前に解析したセッションを ver3 の `--overwrite` 付きで解析し直す（AOI の中身の違いで上書き防止が働く）。集計は `--qc-criteria .\data\qc_criteria.json` を付ける（RUN_PIPELINE 手順8）
- [x] 台帳の記入例の行を削除し、このセッションの行を書いて、ver3 と集計を `--overwrite` 付きで実行し直す（10/21、主解析）
      （台帳の行が `qc.json` の `manifest_row` と `resolution_check` に入り、`qc_report` の `calibration_passed` が埋まることを確認）
- [ ] 下のバーの注視0件が「見ていない」のか「測れていない」のかを確かめる（RUN_PIPELINE.md の「注視0件の AOI の確認」）
- [ ] 同期の delta（confirmed_based）を予備実験のセッションごとに控える。25〜30 ms が続くなら、期待範囲の見直しを根拠データ付きで検討する

## 5. フレーム上の AOI 位置（T5）

```powershell
python .\src\test\extract_frames.py   # 試合中のフレームを1枚抽出（スクリプト内のパスを編集）
python .\src\aoi_detector\aoi_check.py --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --frame .\data\processed\<抽出した画像>.png
```
- [ ] `data\processed\aoi_check\*_aoi.png` で、各AOIが実際のHUD要素に重なっている（特に画面端）
