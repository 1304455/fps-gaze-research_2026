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

- [ ] OBS →「ツール」→「WebSocket サーバー設定」で新しいパスワードを生成
- [ ] `Copy-Item .\docs\templates\obs_config_template.json .\obs_config.json` → `obs_password` を記入
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

## 5. フレーム上の AOI 位置（T5）

```powershell
python .\src\test\extract_frames.py   # 試合中のフレームを1枚抽出（スクリプト内のパスを編集）
python .\src\aoi_detector\aoi_check.py --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --frame .\data\processed\<抽出した画像>.png
```
- [ ] `data\processed\aoi_check\*_aoi.png` で、各AOIが実際のHUD要素に重なっている（特に画面端）
