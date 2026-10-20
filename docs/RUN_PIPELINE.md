# パイプラインの通し手順（較正 → 計測 → 同期 → 区間 → AOI解析 → 集計）

1セッションを最初から最後まで通すためのコマンド集（2026-10-20 作成）。
研究室PC（Windows + PowerShell）で、**同じ PowerShell ウィンドウで上から順に**実行する前提。
途中で作る変数（`$csv` など）を後の手順で使うので、ウィンドウを閉じたら手順3からやり直す。

対応するバージョン：calibrate_validate 1.3.0 / v5 5.1.1 / aoi_analysis-ver3 3.2.0。
ETM の画面設定などの決定事項は CLAUDE.md §2、実機での検証結果は LAB_CHECKLIST.md を参照。

---

## 0. 準備（毎回）

```powershell
cd C:\Users\kawalab\shoda\fps-gaze-research_2026
git pull
# venv を使っている場合はここで有効化（例: .\.venv\Scripts\Activate.ps1）
$subj = "P02"                   # 参加者ID。台帳の participant_id と同じにする
```

- [ ] **ETM の画面設定**が screen angle 5 / eye tracker angle 17（相対角度 12°）になっている
- [ ] 椅子・Spark・モニターがテープの位置にある（Spark は触らない）
- PowerShell では `${subj}_` のように**中かっこで囲む**。`$subj_` と書くと「subj_」という別の変数になり、値が空になる

## 1. 較正＋検証（試合の直前）

```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject $subj --calib-margin 0.1 --reference .\data\raw\headpos_STANDARD_20261013_132612.json
```
- ディスプレイ番号を聞かれたら、ゲームを映すモニターの番号を入れる
- 頭部位置ガイドが OK になってから SPACE

確認：
```powershell
$calib = (Get-ChildItem .\data\raw\calib_${subj}_*.json | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
python .\src\analysis\calib_compare.py $calib --group-by none
```
- [ ] `passed` が True
- [ ] **`display_angle_deg` が 12.0**
- 不合格なら休憩してから手順1をやり直す

## 2. 計測（較正の後、頭と Spark を動かさずに続けて）

```powershell
python .\src\gaze_estimation\tobii_capture_with_sync_flash_v5.py --subject $subj --trial 1 --obs-config .\obs_config.json --max-duration 3600
```
- **`--calibrate` は付けない**（手順1の較正が上書きされる）
- 起動すると実験条件（マップ・エージェントなど）を聞かれるので、そのまま答える
- 開始・停止は、別端末のブラウザから、コンソールに出る制御 URL で行う
- `--max-duration 3600` は止め忘れの安全装置（1時間）
- [ ] 終了後、`data\raw` に `gaze_<subj>_1_default_<日時>.csv` / `_meta.json` / ログがある

## 3. ファイルの場所を変数にまとめる

```powershell
$csv  = (Get-ChildItem .\data\raw\gaze_${subj}_*.csv | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
$base = [IO.Path]::GetFileNameWithoutExtension($csv)
$mp4  = (Get-ChildItem "$env:USERPROFILE\Videos\*.mp4" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
$sync = Join-Path (Split-Path $mp4) ((Get-Item $mp4).BaseName + "_sync.json")
$calib, $csv, $base, $mp4, $sync   # 5つとも今回のセッションのものか目で確認する
```
- OBS の録画の保存先がビデオフォルダ以外なら `$mp4` の行のパスを書き換える

## 4. 同期

```powershell
python .\src\visualizer\gaze_visualizer_v3.py auto-sync $csv $mp4
```
- 動画の隣に `*_sync.json` ができる
- [ ] `method` が **confirmed_based**
- [ ] delta が 6.3〜31.3 ms の範囲（これまでの実測は 22〜26 ms）。範囲外ならそのセッションに印を付ける
- 目で確認したいときは `--verify` を付けると確認画面が開く

（任意）動画に視線を描き込む（時間がかかるので後回しでもよい。出力は `data\processed\`）：
```powershell
python .\src\visualizer\gaze_visualizer_v3.py render $csv $mp4 --modes beeswarm scanpath heatmap
```

## 5. 台帳に1行書く（ver3 が読むので解析の前に）

初回だけテンプレートをコピーする：
```powershell
if (-not (Test-Path .\data\sessions_manifest.csv)) { Copy-Item .\docs\templates\sessions_manifest_template.csv .\data\sessions_manifest.csv }
$base; $calib; $mp4              # この3つを台帳に貼り付ける
```
- テンプレートの**記入例の行は削除**する
- 書く列：
  - `session_base`：`$base` の値
  - `participant_id`
  - `group`：low / high
  - `game_resolution`：`3840x2160`
  - `aspect`：`16:9`
  - `calibration_file`：`$calib` の値（`data/raw/calib_...json` の形でよい）
  - `video_path`：`$mp4` の値
  - `notes`：`ETM 相対角度 12°`
  - その他：マップ・エージェント・攻守など
- Excel で保存するときは「**CSV UTF-8**」形式を選ぶ
- 空欄の行は集計で無視される

## 6. 分析区間の注釈

```powershell
python .\src\visualizer\segment_annotator.py $mp4 --session-base $base --start-side attack
```

| キー | 動作 |
|---|---|
| `b` | 購入フェーズ終了（区間の開始） |
| `k` | 死亡（区間の終了） |
| `e` | ラウンド終了（区間の終了） |
| `t` | 次の区間の攻守を切り替える（ハーフが変わったとき） |
| `u` | 直前の操作を取り消す |
| `s` | 保存 |
| `q` | 保存して終了 |
| `SPACE` | 再生／一時停止 |
| `d` / `a` | 1フレーム進む／戻る |
| `l` / `j` | 5秒進む／戻る（大文字の `L` / `J` は30秒） |
| `]` / `[` | 再生速度を変える |

- 出力は `data\annotations\<base>_segments.csv`
- 途中で閉じても、次の起動で続きから再開できる
- 攻撃側から始まらなかった試合は `--start-side defense` を付ける

## 7. AOI 解析（I-DT）

主解析（眼の距離はセッションごとの実測値）：
```powershell
python .\src\aoi_detector\aoi_analysis-ver3.py `
    --input $csv `
    --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json `
    --segments ".\data\annotations\${base}_segments.csv" `
    --sync $sync `
    --manifest .\data\sessions_manifest.csv
```

感度分析（眼の距離を 650 mm に固定。出力先を分ける）：
```powershell
python .\src\aoi_detector\aoi_analysis-ver3.py `
    --input $csv `
    --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json `
    --segments ".\data\annotations\${base}_segments.csv" `
    --sync $sync `
    --manifest .\data\sessions_manifest.csv `
    --params .\src\aoi_detector\analysis_params_sensitivity_fixed650.json `
    --output-dir .\data\processed\aoi_result_fixed650
```

- 行末のバッククォート（`` ` ``）は行を続ける記号。**後ろに空白を入れない**
- 出力先：`data\processed\aoi_result\<base>\`
  - `samples_classified.csv`：サンプルの分類
  - `fixations.csv`：注視
  - `visits.csv`：訪問
  - `aoi_metrics_session.csv` / `aoi_metrics_by_segment.csv`：AOI ごとの指標
  - `transitions_*`：AOI 間の遷移
  - `qc.json`：品質チェックの記録
- `qc.json` で確認すること：
  - [ ] `eye_distance_used`：試合中の実際の眼の距離（10/20 の確認項目：前のめり姿勢 580 mm での検証が代表的か）
  - [ ] `min_segment_coverage`：0.9 未満なら、区間の一部が記録の範囲外にはみ出している
  - [ ] `missing_gaps`：欠測の長さの分布
- 「出力先に条件の違う結果がある」と止まったら、何が違うのかを確認してから `--overwrite` を付けて実行し直す

## 8. 集計

```powershell
python .\src\analysis\aggregate_sessions.py --manifest .\data\sessions_manifest.csv
python .\src\analysis\aggregate_sessions.py --manifest .\data\sessions_manifest.csv --results-dir .\data\processed\aoi_result_fixed650 --out-dir .\data\processed\aggregate_fixed650
```
- 出力先：`data\processed\aggregate\`
- [ ] `qc_report` で、今回のセッションが `has_result=True`、`calibration_passed` が埋まっている
- 除外基準（`--qc-criteria`）はまだ合意していないので付けない（付けなければ除外の判定はしない）

---

## よくある失敗

- 手順1と2の間に ETM で較正し直したり、v5 を `--calibrate` 付きで起動したりすると、calib JSON の精度と、実際に測定に使った較正が食い違う
- 台帳（手順5）を ver3（手順7）より後に書くと、ver3 で解像度の確認ができない
- 止まったところがあれば、エラーの全文を残す

## 終わったら

次のファイルを Claude に渡し、結果を LAB_CHECKLIST に記録する（CLAUDE.md §6 の記録ルール）：
- `qc.json`
- v5 のログ
- auto-sync の出力
- 集計の `qc_report`
