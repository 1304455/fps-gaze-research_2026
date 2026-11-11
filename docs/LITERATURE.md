# 文献台帳（引用の段階）

- 状態：確定版（2026-11-10）。2026-11-11 更新：PDF を入手した6本を段階1に上げ、本文で確かめた注意を §4 に足した
- 基準の commit：`82c6aae`（main に統合済み。`docs/GLOSSARY.md` と同じ）
- 置き場所：`docs/LITERATURE.md`（公開リポジトリ）。**入れるのは書誌・ページ・段階・短い引用・注意だけ**。長い抜き書きと PDF は claude.ai の Project に置く
- この台帳に無い文献は、卒論で引用しない。足すときは、書誌を確認してから、まずここに行を足す

## 段階の定義

| 段階 | 条件 | 卒論での扱い |
|---|---|---|
| 1 | Project に PDF がある | 引用できる。該当ページを確認して書く |
| 2 | PDF は無いが、Project のメモに原文の引用とページがある | 引用できる。本文中に【原典未確認】を付ける |
| 3 | 書誌だけ、または要約だけ | 本文中に【要出典・原典確認】を付ける。内容を根拠にした文は書かない |

**柱になる主張**の文献は、提出前に段階1まで上げる。段階2・3のままでは提出しない。書籍は、該当するページの写しを Project に入れ、ページ番号を台帳に書いた時点で段階1とする。

---

## 1. 柱になる主張と、その出典

文献の役割と、自分のデータの役割を分ける。

| 柱 | 文献が担う部分 | 自分のデータが担う部分 | 段階 |
|---|---|---|---|
| 主要比較の区分（AOI1〜6）、新規性の比較対象 | Furukado & Hagiwara (2023) | — | **1** |
| 並べ替え検定の方法 | Edgington & Onghena (2007) | — | 3 |
| 並べ替え検定の p 値の式 p =（k + 1）/（N + 1） | Phipson & Smyth (2010) | — | **1** |
| ρ の信頼区間の標準誤差 | Bonett & Wright (2000) | — | 3 |
| 多重比較の補正 | Holm (1979) | — | 3 |
| 注視の定義（I-DT の方法） | Salvucci & Goldberg (2000) | — | **1** |
| I-DT の閾値（最短 100 ms、分散 1°） | 目安の範囲：Salvucci & Goldberg (2000)。閾値で注視の数・時間が変わること：Blignaut (2009)（**閾値の範囲の根拠には使えない**。§4） | 予備実験（PL01〜PL03）での妥当性の確認（`docs/LAB_CHECKLIST.md` 4。目安は結果を見る前に記録、2026-11-11 に実行・決定） | **1** |
| 注視を分ける欠測 75 ms（主解析の `max_gap_ms`） | Olsen (2012)〔メーカー文書・非査読〕：Tobii I-VT の gap fill-in の既定値 | 参加者ごとの欠測の頻度（`missing_gaps`）と、200 ms の感度分析 | 3 |
| クロスヘアの半径 2.0° | 中心窩の大きさ（約 2°）：Holmqvist et al. (2011) | 正確度の余裕（0.4〜1°）：自分の較正の結果（`docs/LAB_CHECKLIST.md` 2c） | 3 |
| accuracy と precision の定義 | Holmqvist et al. (2011) | — | 3 |

### 柱の文献の書誌

書誌は 2026-11-10 にウェブで確認した。Furukado & Hagiwara、Phipson & Smyth、Salvucci & Goldberg、Blignaut は PDF を入手し、本文で確かめた（2026-11-11）。ほかは PDF・本文を読んでいない。

| 文献 | 段階を上げるためにやること |
|---|---|
| Furukado, R., & Hagiwara, G. (2023). Gaze and electroencephalography (EEG) parameters in esports: Examinations considering genres and skill levels. *2023 International Workshop on Smart Info-Media Systems in Asia (SISA 2023)*, 107–112. https://doi.org/10.34385/proc.77.SS-2 | 済み（`22_SS-2_28.pdf`）。引用時の注意は §4 |
| Edgington, E. S., & Onghena, P. (2007). *Randomization tests* (4th ed.). Chapman & Hall/CRC. ISBN 978-1-58488-589-4 | 相関の並べ替え検定を扱う章とページを確認し、写しを Project に入れる |
| Phipson, B., & Smyth, G. K. (2010). Permutation P-values should never be zero: Calculating exact P-values when permutations are randomly drawn. *Statistical Applications in Genetics and Molecular Biology, 9*(1), Article 39. https://doi.org/10.2202/1544-6115.1585 | 済み（`1603.05766v1.pdf`、arXiv 版。**ページは雑誌版と違う**）。引用時の注意は §4 |
| Bonett, D. G., & Wright, T. A. (2000). Sample size requirements for estimating Pearson, Kendall and Spearman correlations. *Psychometrika, 65*(1), 23–28. https://doi.org/10.1007/BF02294183 | PDF を Project に入れる。標準誤差 √((1 + ρ²/2)/(n − 3)) が論文のどの式かを確認する |
| Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian Journal of Statistics, 6*(2), 65–70. https://www.jstor.org/stable/4615733 | PDF を Project に入れる |
| Salvucci, D. D., & Goldberg, J. H. (2000). Identifying fixations and saccades in eye-tracking protocols. In *Proceedings of the 2000 Symposium on Eye Tracking Research & Applications (ETRA '00)* (pp. 71–78). ACM. https://doi.org/10.1145/355017.355028 | 済み（`Salvucci-ETRA00.pdf`）。引用時の注意は §4 |
| Blignaut, P. (2009). Fixation identification: The optimum threshold for a dispersion algorithm. *Attention, Perception, & Psychophysics, 71*(4), 881–895. https://doi.org/10.3758/APP.71.4.881 | 済み（`APP.71.4.881.pdf`）。比べている分散の測り方は5通り。**この研究の測り方（x の範囲 ＋ y の範囲）に近いのは Slv だが、Slv は範囲の「平均」で、この研究の値の半分に当たる**。引用時の注意は §4 |
| Olsen, A. (2012). *The Tobii I-VT fixation filter*. Tobii Technology.〔メーカー文書・非査読〕 | 文書を入手して Project に入れる。正式な題・版・ページ数と、gap fill-in の既定値 75 ms の記載箇所を確認する（題と値は、今回は二次的な記載でしか確認していない） |
| Holmqvist, K., Nyström, M., Andersson, R., Dewhurst, R., Jarodzka, H., & van de Weijer, J. (2011). *Eye tracking: A comprehensive guide to methods and measures*. Oxford University Press. ISBN 978-0-19-969708-3 | 中心窩の大きさ、accuracy・precision の定義のページを確認し、写しを Project に入れる |

### 取り違えに注意する文献

- **Holmqvist et al. (2011) の教科書と、撤回された Holmqvist et al. (2023) は別物**。撤回されたのは "Eye tracking: empirical foundations for a minimal reporting guideline"（*Behavior Research Methods, 55*(1), 364–416）。撤回通知は *Behavior Research Methods, 56*(1), 511–512（2024）、https://doi.org/10.3758/s13428-023-02285-0 。2023 年の論文は引かない。報告の基準を引くなら Dunn et al. (2024)（段階3）。
- **Blignaut (2009) と Blignaut & Beelders (2009) は別の論文**。後者は *Journal of Eye Movement Research, 2*(5) で、台帳に無い。
- **Olsen (2012) と Olsen & Matos (2012, ETRA) は別の文献**。後者は台帳に無い。
- Brunner & Munzel (2000)、Neubert & Brunner (2007) は、主検定が変わったので柱ではない。計画を変えた経緯を書く場合だけ使う（段階3）。

---

## 2. 段階1（PDF あり）：9本

書誌は、PDF の表記と出版社・PubMed Central などのページで確認した（下の3本は 2026-11-11）。

| 文献 | PDF | 使いどころ | 注意 |
|---|---|---|---|
| Furukado & Hagiwara (2023) | `22_SS-2_28.pdf` | §1 | §4 |
| Phipson & Smyth (2010) | `1603.05766v1.pdf` | §1 | §4 |
| Salvucci & Goldberg (2000) | `Salvucci-ETRA00.pdf` | §1 | §4 |
| Blignaut (2009) | `APP.71.4.881.pdf` | §1 | §4 |
| Joyce, J. M., Campbell, M. J., Hojaji, F., & Toth, A. J. (2024). Less is more: Higher-skilled sim racers allocate significantly less attention to the track relative to the display features than lower-skilled sim racers. *Vision, 8*(2), 27. https://doi.org/10.3390/vision8020027 | `vision-08-00027-v3.pdf` | 熟練度で HUD とそれ以外への注視の配分を比べた例（シムレーシング）。周辺視・潜在的注意は測れないという限界 | §4 |
| Jeong, I., Nakagawa, K., Osu, R., & Kanosue, K. (2022). Difference in gaze control ability between low and high skill players of a real-time strategy game in esports. *PLOS ONE, 17*(3), e0265526. https://doi.org/10.1371/journal.pone.0265526 | `journal.pone.0265526.pdf` | RTS（StarCraft）で熟練者はミニマップの領域を多く見た。実プレイ中の熟練度の比較の例 | §4 |
| Montolio-Vila, A., Argilés, M., Sunyer-Grau, B., Quevedo, L., & Erickson, G. (2024). Effect of action video games in eye movement behavior: A systematic review. *Journal of Eye Movement Research, 17*(3), 6. https://doi.org/10.16910/jemr.17.3.6 | `jemr-17-03-f.pdf` | 対象13研究のうちゲームプレイ中に熟練度で比べたのは2本（p.16）。眼球運動の指標は課題に依存する（p.17）。ランクで群分けした例と自己選択バイアス（p.15–16） | §4 |
| Jeong, I., Nobuto, T., Kaneko, N., Kato, T., & Nakazawa, K. (2023). Investigating the gaze control ability of VALORANT players using a Python-based tool. arXiv:2310.15542 | `2310.15542v1.pdf` | VALORANT の実プレイでの ROI 別の割合。区分の対応（Information 1・2） | **査読前のプレプリント**。本文に [Preprint] と書く。§4 |
| Luo, Y., Chen, Y., Cho, J., Yan, C., & Seo, J. (2025). Differences in eye movement characteristics between expert and non-expert eSports players: A systematic review and meta-analysis. *Scientific Reports, 15*, 30185. https://doi.org/10.1038/s41598-025-12101-8 | `Differences_in_eye_movement_characteristics_betwee.pdf` | eスポーツの視線研究の現状。含まれた7研究に FPS が無いこと | §4 |

## 3. 段階2（メモに原文の引用とページ）：5本

引用とページは Project の `claude/文献抽出_HUD注視研究_2026-10.md` にある。

| 文献 | 使いどころ | 注意 |
|---|---|---|
| Yoon, J.-S., Moon, H.-S., Boudaoud, B., Spjut, J., Frosio, I., Lee, B., & Kim, J. (2025). Modeling visually-guided aim-and-shoot behavior in first-person shooters. | ランクで熟練度を定義した例、無効データの除外の例 | プレプリント。出版版の有無を確認する |
| Holm, S. K. (2023). *Preferences, emotions, and visual attention in the first-person shooter game experience*. University of Turku（博士論文） | FPS の視線研究は各群4〜10名、AOI 解析は今後の課題 | 孫引き（Choi & Kim 2018 など）は原典を見るまで引かない |
| Jin, P., Ge, Z., & Fan, T. (2023). Research on visual search behaviors of basketball players at different levels of sports expertise. *Scientific Reports, 13*, 1406 | 注視の多い・少ないは文献で割れる、AOI を分ける意義 | — |
| Chia, J. S., Burns, S. F., Barrett, L. A., & Chow, J. Y. (2017). Increased complexities in visual search behavior in skilled players for a self-paced aiming task. *Frontiers in Psychology, 8*, 987 | 時間の制約で熟練者の注視が変わる | 補助的（バドミントン） |
| Dahl, M., Tryding, M., Heckler, A., & Nyström, M. (2021). Quiet eye and computerized precision tasks in first-person shooter perspective esport games. *Frontiers in Psychology, 12*, 676591 | 据え置き型の装置の妥当性、注視の検出は方法に依存、較正の採用基準 | — |

## 4. 段階1の文献を引くときの注意（PDF で確認）

長い抜き書きは Project の `claude/2026-11-11_文献メモ_I-DT閾値と並べ替え検定.md`（Phipson & Smyth、Salvucci & Goldberg、Blignaut）、`claude/2026-10-09_先行研究抽出ノート_Joyce・Jeong2022ほか（11-11精査）.md`、`claude/2026-10-09_先行研究引用ノート_Montolio-Vilaほか（11-11精査）.md` にある。

**Furukado & Hagiwara (2023)**
- 対象は VALORANT（アンレート）と LoL。FPS は熟練群6名（アセンダント1〜イモータル3）と準熟練群6名（**未ランク〜シルバー2**）（p.108、Table 1）。この研究は未ランク者を除外する。
- 装置は Gazepoint GP3 HD（150 Hz）、**顎台あり**（p.108）。この研究は顎台なし。
- 一部のエージェントを使用禁止にしている（p.108）。この研究は制限していない。
- 生存中のデータだけを使っている（p.109）。
- 検定は対応のない t 検定（p.109）。多重比較の補正についての記載は無い。AOI1 は p = .005、d = −2.05（準熟練群の方が多い）。AOI6 は p = .007。AOI5 は p = .063（p.109、Table 2）。
- **ミニマップ（AOI5）の差は有意でない**（p = .063、d = 1.21）。要旨（p.107）は「熟練群はミニマップをより多く見た」と書き、Joyce et al. (2024, p.10) は「有意に多く」と引いている。どちらも Table 2 と合わない。ミニマップについては Table 2 から直接引く。
- **Table 2 の見出しは「%」だが、値は 0.668 のような割合の形**。数値を引くときは単位を確かめる。
- 指標は「1試合あたりの注視時間の割合」で、分母の定義は書かれていない（p.109）。この研究の Gaze % と同じ計算とは言えない。
- AOI1 の差を要素ごと（弾数・HP）に解釈しているが、AOI は分けていない（p.110）。

**Jeong et al. (2023)**
- **要旨と本文で相関の向きが食い違う**。要旨は正の相関、結果は Information 1 と KDA に負の相関（ρ = −0.608、p = .002、p.5、Fig. 7E）。引くのは結果の節。
- ROI の割合と視線分布に、中位群と上位群の有意差は無い（p.6、Fig. 8）。
- 装置は Tobii 4C（90 Hz）、視距離は約 100 cm、録画に焼き込んだ視線の点を画像処理で読み取る方式（p.2–3）。

**Luo et al. (2025)**
- 含まれた7研究のタイトルは Dota 2、Gran Turismo、FIFA 19、StarCraft、LoL、FIFA 21、Assetto Corsa Competizione（p.4、Table 1）。「FPS が含まれない」はこの表から言える。
- 検索は英語の査読論文だけ、2024-07-20 まで（p.2–3）。「eスポーツ全体で研究が無い」とは言えない。
- 注視回数の信頼区間の表記（要旨と p.5 の「−0.55, −0.99」）は、p = 0.58 と合わない。**注視回数の信頼区間は引かない**。
- AOI の結果は統合されておらず、記述だけ（p.6）。

**Phipson & Smyth (2010)**（ページは arXiv 版 `1603.05766v1.pdf`）
- ランダムに抜き出した並べ替えでの正確な p 値は (b + 1)/(m + 1)（p.6）。ANALYSIS_PLAN §4 の式 p = (k + 1)/(100,000 + 1) はこの形。
- 並べ替えを重複ありで抜き出すと、この式は妥当だが保守的になる。保守的さは、並べ替えの総数が小さいほど大きい（p.8–9）。
- 全部数え上げる場合の式（p.7）は、統計量の値がすべて異なる前提で導いている。この研究はランダムに抜き出すので使わない。
- 雑誌版を引用するときは、ページを雑誌版で確かめ直す。

**Salvucci & Goldberg (2000)**（誌面にページの印刷が無いので、PDF の1枚目を p.71 として数えた）
- I-DT の分散は D = [max(x) − min(x)] + [max(y) − min(y)]（p.74）。この研究の実装と同じ。
- 分散の閾値は、眼−画面距離が分かっていれば視角 1/2°〜1° を含むように設定できる、最短時間は通常 100〜200 ms（p.74）。**目安であって、実証した値ではない**。
- 注視が 100 ms 未満になることはまれ（p.72）。
- アルゴリズムの比較は定性的で、1つの課題のデータだけ（p.76）。「最適な閾値を実証した論文」としては引かない。

**Blignaut (2009)**
- **Slv は x と y の範囲の「平均」**で、Salvucci & Goldberg の「合計」とは違う、と本人が書いている（p.886）。推奨の「1°」は**半径**の値（p.881、887、894）。
- 表1（p.887）の回帰は「半径 = 傾き × 分散 + 切片」。Slv は傾き 0.9976、**切片 −0.0797**（テキストを取り出すと負号が落ちる）。半径 1.0° は Slv 1.08°（p.887）。
- この研究の値に直すと、主解析の 1.0°（Slv 0.5°）は半径 約0.42°、感度分析の 1.5°（Slv 0.75°）は半径 約0.67°。許容範囲の半径 0.7〜1.3°（p.894）より、どちらも小さい。
- 換算の係数は Tobii 1750（50 Hz）とチェス盤の静止画で出したもので、ほかの条件で使えるかは未確認、と本人が書いている（p.894）。目安としてだけ使う。
- 閾値が小さすぎると、ふるえの大きい人の注視が検出から漏れる（p.884）。注視数は半径 0.7〜1° 付近で最大になり、それより小さいと減る（p.890、図9）。
- **「閾値 1° は Blignaut に従った」とは書けない**。閾値で注視の数・時間が変わることの根拠（p.884、894）としては引ける。方法の章での位置づけは未決。

**Joyce et al. (2024)**
- 「熟練者ほど HUD に相対的に多く配分した」は、**予測（H3、p.2）と逆向きの結果**。著者自身が「仮説と逆向き」と書いている（p.9）。
- 比は**注視の回数**の比（FC_TRACK : FC_HUD、式4、p.5）。両群とも比が AOI の面積比より上で、どちらの群も HUD を面積以上には見ていない、と著者が書いている（p.10）。
- **表の p は片側検定の値**（t と df から計算し直すと一致する）。TRACK:HUD は t(24.618) = −2.946、d = 0.969 で、両側の p は約 .007。仮説と逆向きの差に片側の p は使えないので、引くときは t・df・d と両側の p を書く。
- 各群 22 名を選んだが、外れ値で低熟練群の3名を除き、分析は 19 名と 22 名（p.6）。
- 装置は Tobii Pro Glasses 3（眼鏡型、50 Hz）、55インチ画面、1点較正（p.3）。注視は Tobii の I-VT Attention フィルタ（付録B、p.12）。注視の数・時間をこの研究と比べない。
- Furukado & Hagiwara (2023) の引き方は誤り（上の Furukado & Hagiwara の項）。

**Jeong et al. (2022)**
- 熟練者が多く見たのは Area i（ミニマップ、p < .001）と Area iii（コマンド、p = .02）。ほかの UI（Area ii、iv、vi）とプレイ画面（Area v）には差が無い（p.9）。「HUD 全体を多く見た」とは言えない。
- AOI の比率は「その領域の注視時間 ÷ 全領域の注視時間」（p.8）。**この研究の Gaze % とは定義が違う**（注視ベース、分母も違う）。
- 「注視の割合が低熟練者で高い（F = 4.70）」は、I-HMM で分類した眼球運動のうち注視が占める割合で、AOI の割合ではない（p.8–9）。
- 反復測定 ANOVA と書いてあるが、F と偏η² から逆算した誤差の自由度は約 43〜54 で、参加者 16 名より大きい（3課題を独立に扱った可能性）。p は控えめに扱う。
- 装置は Pupil Core（頭部装着型）、画面は参加者が選んだ 24〜34 インチ、注視の分類は I-HMM（p.3、7–8）。

**Montolio-Vila et al. (2024)**
- 比べているのは主に「アクションゲームをする人／しない人」で、同じゲームの中での熟練度の比較ではない（ゲームプレイ中に熟練度で比べたのは Jeong et al. 2022 と Koposov et al. 2020 だけ、p.16）。
- 「注視が少ないほど熟練」（p.15）はゲームの研究の結果ではなく、別分野の文献（Arthur et al., 2016 など）を引いた記述。同じ段落で「ゲームで注視時間が短くなるとは言えない」とも書いている。この仮説の根拠には使わない。
- Jeong et al. (2022) の要約（p.13）は、Jeong et al. (2022) から直接引く。
- まとめの表（p.14）の "Peters et al. (2019)" は、本文と対象研究の表では Peters et al. (2021)。
- 検索は PubMed と Scopus、2010〜2022 年（p.1）。

## 5. 段階3（書誌・要約だけ）

**Project の `claude/追加文献調査_理論背景の補強.md` の 28 本（A1〜D6）と予備候補 12 本**
- 要約と使いどころはあるが、原文の引用とページが無い。メモ自身も多くを「一部未確認」「内容要確認」としている。使う文献は、書誌を確認してからこの台帳に1行ずつ移す。
- B2（Furukado & Hagiwara）と B1（Luo et al.）だけは PDF があり、段階1。
- Velichkovsky et al. (2019) を「熟練者ほど注視が短い」と引かない（平均・中央値に群差は無い、とメモにある）。
- このメモは注視検出を I-VT の前提で書いている箇所がある。この研究は I-DT なので、メモの文案をそのまま使わない。

**その他**

| 文献 | 出てくる場所 | 備考 |
|---|---|---|
| Dunn et al. (2024), *Behavior Research Methods, 56*(5), 4351–4357 | 追加文献調査 D1 | 報告の基準。撤回された 2023 年の論文の代わり |
| Brunner & Munzel (2000)、Neubert & Brunner (2007) | 10/22 議事録 | 旧の主検定。経緯を書く場合だけ |
| 芳賀・水上 (1996)、NASA-TLX 日本語版 | 研究経緯 10/26 | 事後アンケートを卒論で扱う場合 |
| Jeong et al. (2024, *PLOS ONE*)、Holm et al. (2021, *JEMR*)、Cazzato et al. (2020)、Yang et al. (2025) | 研究ノート | 読書メモだけ。ページつきの引用なし（Jeong et al. 2022 と Montolio-Vila et al. 2024 は 2026-11-11 に段階1へ移した） |
| AOI の最小サイズについての引用（PMC12913287） | 研究ノート 8/27 | 著者・誌名・ページが控えられていない |

`中間発表.pptx` の参考文献は確認していない。

## 6. 新規性の主張の範囲

- 「下部HUD（HP・アビリティ・弾数）を分けて熟練度との関係を見た研究は、**手元の文献では**見つかっていない」。調べた範囲はこの台帳の文献だけ。
- 未調査：CS:GO・Apex・Overwatch などの研究、日本語の論文、arXiv:2607.17151（機能別 AOI と遷移）。
- 卒論では、調べた範囲を限界として書く。
