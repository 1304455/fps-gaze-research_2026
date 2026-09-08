fps-gaze-research/           # リポジトリのルート（プロジェクト名）
├── .gitignore
├── README.md
├── docs/                    # 研究ノート、論文用の図、仕様書など
├── data/                    # 測定データ（生データ・処理後）※Git管理外推奨
│   ├── raw/                    # 測定したままの生ログ
│   └── processed/              # AOI判定後などの解析用データ
├── src/                     # 現在開発・使用しているメインのソースコード
│   ├── test/                   # 検証用
│   ├── gaze_estimation/        # 視線推定
│   └── aoi_detector/           # AOI（関心領域）の判定・マッピング処理
|   └── visualizer/             # データ可視化
└── archive/                 # 過去のバージョンのコード（旧バージョン置き場）@@


プログラムのコマンドメモ
python .\src\gaze_estimation\tobii_capture_with_sync_flash_v4.py --skip-conditions-prompt --obs-password g2UhGsjGCG3Hy42H