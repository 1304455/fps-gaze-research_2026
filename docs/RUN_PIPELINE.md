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

## 2b. 試合後の検証（較正はしない。試合が終わったら席を立つ前にすぐ）

試合中に姿勢や Spark との位置関係がずれて精度がどれだけ落ちたかを記録する（2026-10-21 合意）。
**姿勢を直さず、試合を終えたときの座り方のまま**行う。位置ガイドは姿勢の記録だけに使うので、許容幅を広げてすぐ OK になるようにする。

```powershell
python .\src\gaze_estimation\calibrate_validate.py --subject "${subj}-post" --validate-only --tolerance-z-mm 200 --tolerance-x-mm 200
$post = (Get-ChildItem ".\data\raw\calib_${subj}-post_*.json" | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
python .\src\analysis\calib_compare.py $calib $post --group-by none
```
- subject は `<参加者ID>-post` にする（`calib_${subj}_*.json` に混ざらず、手順1の `$calib` を取り違えない）
- `--reference` は付けない（標準姿勢に合わせ直すと、試合中の姿勢での精度にならない）
- 合否（`passed`）は記録だけで、除外基準には使わない。卒論では「試合前 → 試合後の精度の変化」と、試合後の眼の距離（`head_z_mm`）を報告する
- `calib_compare` の2行（試合前・試合後）で `mean_accuracy_deg`、`hud_max_accuracy_deg`、上下のバーのずれ、`head_z_mm` を比べる

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

（任意）動画に視線を描き込む（出力は `data\processed\`）：
```powershell
python .\src\visualizer\gaze_visualizer_v3.py render $csv $mp4 --modes heatmap --no-audio
```
- **時間がかかる**：3モードで録画の約8倍（13分の試合で約100分。10/20 の実測）。解析には不要なので、
  全手順が終わってから、確認したいセッションだけ、必要なモードだけ描く
- ffmpeg が無いと音声の合成に失敗して無音で出力される（警告が出るだけ）。最初から `--no-audio` を付ければ警告は出ない

## 5. 台帳に1行書く（ver3 が読むので解析の前に）

**手で編集せず、変数から行を組み立てて追記する**（10/20〜21 に、記入例の消し忘れ・行の書き忘れ・Excel による `16:9`→`16:09` の変換・
メモ帳での列ずれが続いたため）。マップ・エージェントなど後から分かる列だけ、あとでメモ帳で足す。

```powershell
$participant = "P02"            # 参加者ID（$PID は PowerShell の予約変数なので使わない）
$group = "low"                  # low / high
$side  = "attack"               # 最初のラウンドの攻守（attack / defense）。手順6の注釈でも同じ値を使う
$notes = "ETM相対角度12°;post=" + (Split-Path $post -Leaf)   # 試合後の検証のファイル名（手順2b）
$m = ".\data\sessions_manifest.csv"
if (-not (Test-Path $m)) { Set-Content $m -Value (Get-Content .\docs\templates\sessions_manifest_template.csv -TotalCount 1 -Encoding UTF8) -Encoding UTF8 }
$lines = @(Get-Content $m -Encoding UTF8)
if ($lines -match "^$base,") { Write-Warning "台帳に $base の行がもうあります。追記しません。" } else {
    $row = "$base,$participant,$group,,,,,,$side,3840x2160,16:9,,,$calib,$mp4,$notes"
    Set-Content $m -Value ($lines + $row) -Encoding UTF8
}
Import-Csv $m -Encoding UTF8 | Select-Object -Last 1 | Format-List
```
- 初回は、テンプレートの**見出し行だけ**で台帳を作る（記入例の行は入らない）
- 列の順：`session_base, participant_id, group, rank_current, rank_peak, map, agent, role, start_side, game_resolution, aspect,
  minimap_settings, eye_screen_distance_cm, calibration_file, video_path, notes`（16列。空欄はカンマだけ）
- `eye_screen_distance_cm` は巻尺で実際に測ったときだけ書く（解析の距離は眼位置の実測値を使うので、空欄でよい）
- 値にカンマを入れない（`notes` は `;` で区切る）
- 後から列を足すときは `notepad $m` で編集する。**Excel で開いて保存しない**（`16:9` が `16:09` に変わる。ver3 の解像度チェックで止まる。
  止まったら `--allow-resolution-mismatch` で通さず台帳を直す）
- **集計の前に、台帳・qc_report を Excel で開いていたら閉じる**（開いたままだと集計の書き出しが `PermissionError` で止まる。10/21 に発生）

確認（**今回の `$base` の行が出て、列がずれていないことを見てから手順6に進む**）：
- [ ] `session_base` が `$base`、`participant_id` / `group` / `start_side` / `aspect: 16:9` / `calibration_file` が正しい列に入っている
- 台帳に行が無いと、ver3 は警告だけ出して解像度の確認を飛ばし、集計ではそのセッションが参加者集計から外れる（10/20 の通し確認で発生）

## 6. 分析区間の注釈

```powershell
python .\src\visualizer\segment_annotator.py $mp4 --session-base $base --start-side $side
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
- 守備側から始まった試合は、手順5で `$side = "defense"` にする（綴りは米国式の `defense`。`defence` はエラーになる）。台帳の `start_side` と注釈がそろう

### 6a. 区間の切り出しは手作業（2026-10-28 決定）

生存区間は、録画を見ながら上のキーで実験者が記録する（自動検出は余力があれば後で作る）。
コードが自動でやること・人がやることの分担：

| 担当 | 内容 |
|---|---|
| **人** | 各ラウンドの「購入フェーズ終了」「死亡」「ラウンド終了」のフレームを見つけてキーを押す。ハーフが変わったら `t` で攻守を切り替える。蘇生など例外の扱い（下の定義） |
| `segment_annotator.py` | 録画の再生・コマ送り。押したイベントを `<base>_segments_events.json` に**押すたびに保存**（途中で閉じても再開できる）。イベントから区間 CSV を作る。閉じていない区間・終了が開始より前の区間を警告して出力しない。手で編集した区間 CSV は退避してから上書きし、events JSON の無い手作りの CSV は上書きしないよう起動しない |
| ver3（`gaze_metrics.segments_from_video_time`） | 区間 CSV の検査（必須列、時刻の空欄、終了 ≤ 開始、区間の重なり → エラーで止まる）。動画時刻を視線時刻に変換（`gaze_time = video_time + offset_sec`） |
| ver3（解析） | 区間 `[開始, 終了)` に入るサンプルに `segment_id` を付ける。区間外は行を消さず `out_of_segment` として残す。注視・訪問・遷移は区間の境界で切る |
| ver3（QC） | `qc.json` に区間数、分析時間（`analysis_time_sec`）、区間ごとの有効率と coverage（区間が記録の外にはみ出していないか）、サンプル0件の区間を出す |

#### イベントの定義（案。卒論の方法の章に書く。決めたら CLAUDE.md §2 に移す）
- **購入フェーズ終了（`b`）**：画面上部のタイマーが、購入フェーズのカウントダウンからラウンドの残り時間に切り替わった**最初のフレーム**（バリアが消える時点）
- **死亡（`k`）**：自分が倒され、画面が死亡時の表示に切り替わった**最初のフレーム**
- **ラウンド終了（`e`）**：生き残ったままラウンドが終わったとき、ラウンドの勝敗の表示が出た**最初のフレーム**（その後に動ける時間は使わない）
- **蘇生されたとき**（セージのリザレクション、クローブの自己蘇生など）：**区間は最初の死亡で終える**。蘇生後の時間は使わない
  （ツールは1ラウンド1区間が前提で、蘇生後に `b` を押すとラウンド番号が1つ進んでしまう）
- 切断・再接続などで録画が乱れたラウンドは区間を付けず、台帳の notes にラウンド番号を書く
- 見つけ方：`l` / `L`（5秒・30秒送り）で近くまで行き、`a` / `d`（1コマ）で切り替わりのフレームに合わせてからキーを押す

#### 1試合の流れ
1. 手順5で `$side` を決めてから起動する（台帳の `start_side` と同じ）
2. ラウンドごとに `b` → `k` または `e`。13ラウンド目（ハーフの交代）の `b` を押す前に `t`
3. 最後に `q`（保存して終了）。コンソールの「保存しました（N区間）」の N が、生存区間のあるラウンド数と合っているか確かめる
4. 手順7の ver3 の出力で、区間数・分析時間・`min_segment_coverage` を確認する

#### 作業時間の見積もり（未計測。アンレートの予備実験で測る）
- 1ラウンドあたり：購入フェーズ終了の位置合わせ 約15〜20秒、生存中の早送り 約20〜30秒、死亡またはラウンド終了の位置合わせ 約15〜20秒 → 約1〜1.5分
- アンレート1試合（20〜24ラウンド）：**約30〜45分**。初回や確認を含めると **約40〜60分**
- 注意：録画は 4K（3840×2160）なので、PC によってはツールの再生が等速より遅くなる（再生中のコマ落ちではなく、表示が遅れる）。
  その場合は早送り再生（`]`）より、`l` / `L` で飛ばす方が速い
- 予備実験では、起動から `q` までの時間を台帳の notes（例：`annot_min=42`）か LAB_CHECKLIST に残す

#### ツールを使わずに区間 CSV を作る場合（非推奨）
ver3 が読む形式は次のとおり（`data\annotations\<base>_segments.csv`、UTF-8）。

```
round,segment_type,video_start_sec,video_end_sec,end_reason,side,notes
1,alive,95.4333,131.0167,death,attack,
2,alive,201.2000,262.5500,round_end,attack,
```
- 時刻は**動画時刻の秒**（ツールと同じ `フレーム番号 ÷ fps`）。動画プレーヤーの表示時刻はこれとずれることがあるので使わない
- `segment_type` は `alive`、`end_reason` は `death` か `round_end`、`side` は `attack` か `defense`
- 区間が重なる・終了 ≤ 開始・時刻が空欄だと ver3 がエラーで止まる
- **Excel で開いて保存しない**（10/21 に `16:9` が `16:09` に変わった失敗と同じことが起きうる）
- events JSON が無いので、この CSV があると `segment_annotator.py` は起動しない（手作りの区間を消さないため）。ツールで付け直すときは CSV を別名に移す

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

- 行末のバッククォート（`` ` ``）は行を続ける記号。**前に空白を1つ入れ、後ろには何も入れない**。
  `--overwrite`` ` のように前の空白が無いと、改行が引数に入って `unrecognized arguments` や `Invalid argument` になる（10/21 に発生）。
  迷ったら1行で書く（下の「1行版」）
- 1行版（主解析と感度分析。やり直しのときは末尾に ` --overwrite` を足す）：
  ```powershell
  python .\src\aoi_detector\aoi_analysis-ver3.py --input $csv --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --segments ".\data\annotations\${base}_segments.csv" --sync $sync --manifest .\data\sessions_manifest.csv
  python .\src\aoi_detector\aoi_analysis-ver3.py --input $csv --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --segments ".\data\annotations\${base}_segments.csv" --sync $sync --manifest .\data\sessions_manifest.csv --params .\src\aoi_detector\analysis_params_sensitivity_fixed650.json --output-dir .\data\processed\aoi_result_fixed650
  ```
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
- 主解析の `qc.json` は `data\processed\aoi_result\<base>\`、感度分析のものは `data\processed\aoi_result_fixed650\<base>\`。
  眼の距離の実測値（`eye_distance_used.mm`、`source: measured`）は主解析の方にしか出ない

### 注視0件の AOI の確認

ver3 が「注視0件のAOI」を出したら、「見ていない」のか「測れていない」のかを次で切り分ける（`$base` は手順3の変数）：
```powershell
$OutputEncoding = [System.Text.Encoding]::UTF8   # これが無いと python に渡すコードの日本語が「??」になる
@'
import sys
import pandas as pd
d = sys.argv[1]
f = pd.read_csv(d + r"\fixations.csv", encoding="utf-8-sig")
s = pd.read_csv(d + r"\samples_classified.csv", encoding="utf-8-sig", low_memory=False)
s = s[s["sample_class"] != "out_of_segment"]
print("== AOI ごとのサンプル分類（区間内）==")
print(pd.crosstab(s["sample_aoi"].fillna("(invalid)"), s["sample_class"]))
print("\n== 下部（重心 y >= 0.80）の注視の y の分布。下のバーの AOI は y 0.895 から ==")
b = f[f["centroid_y"] >= 0.80]
print(pd.cut(b["centroid_y"], [0.80, 0.85, 0.87, 0.895, 0.92, 0.95, 1.0]).value_counts().sort_index())
print("\n== 重心 y が最も下の注視 10 件 ==")
print(f.nlargest(10, "centroid_y")[["segment_id", "start_sync_sec", "duration_sec", "centroid_x", "centroid_y", "aoi"]])
'@ | python - ".\data\processed\aoi_result\$base"
```
読み方：
- AOI 内のサンプルが `aoi_nonfixation` だけある → 視線は通ったが 100 ms 以上とどまっていない（ちらっと見た／通過）
- 注視の重心が y 0.85〜0.895（AOI のすぐ上）に集まっている → 下のバーの上向きのずれ（約 0.4〜0.7° ≒ 正規化で 0.015〜0.025）で AOI の外に出ている疑い（測れていない）
- y 0.85 より下に注視もサンプルもほとんど無い → 区間内（生存中）に下のバーを見ていない

## 8. 集計

初回だけ、合意した除外基準をコピーする：
```powershell
if (-not (Test-Path .\data\qc_criteria.json)) { Copy-Item .\docs\templates\qc_criteria_template.json .\data\qc_criteria.json }
```
```powershell
python .\src\analysis\aggregate_sessions.py --manifest .\data\sessions_manifest.csv --qc-criteria .\data\qc_criteria.json
python .\src\analysis\aggregate_sessions.py --manifest .\data\sessions_manifest.csv --qc-criteria .\data\qc_criteria.json --results-dir .\data\processed\aoi_result_fixed650 --out-dir .\data\processed\aggregate_fixed650
```
- 出力先：`data\processed\aggregate\`
- [ ] `qc_report` で、今回のセッションが `has_result=True`、`calibration_passed` が埋まっている
- 除外基準は 10/21 に合意した分だけ評価される（分析時間・coverage の下限は未決で null）。`qc_report` の `exclude_candidate` と `flag_*` 列、`n_criteria_evaluated`（6 になる）を見る
- テンプレートの基準を変えたら（未決の値が決まったら）`data\qc_criteria.json` にもコピーし直す

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
