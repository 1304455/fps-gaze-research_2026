fps-gaze-research/           # リポジトリのルート（プロジェクト名）
├── .gitignore
├── README.md
├── CLAUDE.md                # 研究設計の合意事項・不変条件（作業前に読む）
├── requirements.txt         # 実行用の依存関係
├── requirements-dev.txt     # テスト用（pytest）
├── docs/                    # 研究ノート、改良計画、テンプレート、研究室PCでの確認手順
│   ├── IMPROVEMENT_PLAN.md
│   ├── LAB_CHECKLIST.md        # 研究室PCでの実機確認手順（PowerShell）
│   └── templates/              # セッション台帳・除外基準・OBS設定のテンプレート
├── data/                    # 測定データ（生データ・処理後）※Git管理外
│   ├── raw/                    # 測定したままの生ログ（上書きしない）
│   ├── annotations/            # 分析区間の定義（<session_base>_segments.csv）
│   ├── sessions_manifest.csv   # セッション台帳（実験者が記入）
│   └── processed/              # AOI判定後などの解析用データ
├── src/                     # 現在開発・使用しているメインのソースコード
│   ├── test/                   # 検証用
│   ├── gaze_estimation/        # 視線計測・キャリブレーション検証
│   ├── aoi_detector/           # AOI（関心領域）の判定・解析
│   ├── analysis/               # セッション横断の集計・QC
│   └── visualizer/             # データ可視化・区間注釈
├── tests/                   # 解析ロジックのテスト（合成データ）
└── archive/                 # 過去のバージョンのコード（旧バージョン置き場）


## パイプライン（コマンド例は PowerShell）

秘密情報（OBS WebSocket のパスワード、リモート制御 token）はコマンド例・コード・コミットに書かない。
OBS の接続設定は `docs/templates/obs_config_template.json` をリポジトリのルートに `obs_config.json` として
コピーしてパスワードを記入し、`--obs-config` で渡す（`obs_config*.json` は .gitignore 済み）。

```powershell
# 0) 試合前：キャリブレーション＋検証（結果は data/raw/calib_<subject>_<日時>.json）
python .\src\gaze_estimation\calibrate_validate.py --subject P01

# 1) 計測（OBS録画を自動開始・停止。開始・停止は別端末ブラウザから）
python .\src\gaze_estimation\tobii_capture_with_sync_flash_v5.py --subject P01 --trial 1 --skip-conditions-prompt --obs-config .\obs_config.json
#    → data\sessions_manifest.csv に1行記入（docs\templates\sessions_manifest_template.csv 参照）

# 2) 同期（*_sync.json を動画の隣に保存）
python .\src\visualizer\gaze_visualizer_v3.py auto-sync .\data\raw\gaze_P01_1_default_20261020_140358.csv "C:\Users\kawalab\Videos\2026-10-20 14-04-19.mp4"

# 3) 分析区間の注釈（購入フェーズ終了 → 死亡/ラウンド終了）
python .\src\visualizer\segment_annotator.py "C:\Users\kawalab\Videos\2026-10-20 14-04-19.mp4" --session-base gaze_P01_1_default_20261020_140358

# 4) AOI解析（I-DT 注視検出・区間対応）→ data\processed\aoi_result\<session_base>\
python .\src\aoi_detector\aoi_analysis-ver3.py `
    --input .\data\raw\gaze_P01_1_default_20261020_140358.csv `
    --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json `
    --segments .\data\annotations\gaze_P01_1_default_20261020_140358_segments.csv `
    --sync "C:\Users\kawalab\Videos\2026-10-20 14-04-19_sync.json" `
    --manifest .\data\sessions_manifest.csv

# 5) セッション横断の集計と QC レポート → data\processed\aggregate\
python .\src\analysis\aggregate_sessions.py --manifest .\data\sessions_manifest.csv

# AOI設定の確認（視角サイズ表、録画フレーム上への描画）
python .\src\aoi_detector\aoi_check.py --aoi .\src\aoi_detector\valorant_hud_aoi_circular.json --frame .\data\processed\frame_1000.png

# テスト
python -m pytest
```

解析パラメータ（I-DT 閾値、欠測ギャップ、Gaze % の分母など）の既定値は
`src/aoi_detector/analysis_params_default.json`。変えるときはコピーして `--params` で渡す。
使った値はすべて出力の `qc.json` に残る。
