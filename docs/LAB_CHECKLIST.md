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

## 3. 計測スクリプト v5 の5分間テスト（T1）

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

## 4. 予備実験データで新旧の解析を比べる（T3・T4）

予備実験の1試合（`_proc.csv` を作ってあるもの）で：

```powershell
python .\src\visualizer\segment_annotator.py "<動画>.mp4" --session-base <session_base>
python .\src\aoi_detector\aoi_analysis-ver3.py --input .\data\raw\<session_base>.csv --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --segments .\data\annotations\<session_base>_segments.csv --sync "<動画>_sync.json" --compare-proc .\data\processed\<session_base>_proc.csv
```
- [ ] 1試合（約13ラウンド）の区間付けが、動画を1回通して見る程度の時間で終わる
- [ ] `qc.json` の `compare_with_proc.jaccard` が 1 に近い（ずれていれば、どのラウンドの境界かを確認）
- [ ] 同じ区間で、`gaze_pct` が旧版（archive\aoi_analysis-ver2.py を `_proc.csv` に適用）とほぼ一致する
- [ ] `qc.json` の `time_base_comparison.interval_difference_sd_ms` を控える（決定事項4の材料）
- [ ] `fixations.csv` の `duration_sec` の分布を見る（I-DT の閾値が自分のデータで妥当かの確認）

## 5. フレーム上の AOI 位置（T5）

```powershell
python .\src\test\extract_frames.py   # 試合中のフレームを1枚抽出（スクリプト内のパスを編集）
python .\src\aoi_detector\aoi_check.py --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --frame .\data\processed\<抽出した画像>.png
```
- [ ] `data\processed\aoi_check\*_aoi.png` で、各AOIが実際のHUD要素に重なっている（特に画面端）
