# -*- coding: utf-8 -*-
"""gaze_pct_fixation_split（予備実験の Gaze % を aoi_fixation / aoi_nonfixation に分けた図）のテスト。

結果フォルダは、合成の視線データに ver3（aoi_analysis-ver3.py）を実際に当てて作る（出力の形式を ver3 とそろえるため）。
"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import aggregate_sessions as agg
import gaze_pct_fixation_split as gps
import idt_threshold_check as itc
from synthetic import CROSSHAIR, MINIMAP, hold, make_df, saccade

ROOT = Path(__file__).resolve().parent.parent
AOI_JSON = ROOT / "src" / "aoi_detector" / "valorant_hud_aoi_circular.json"
ABILITIES = (0.5, 0.94)
HEALTH = (0.33, 0.94)

PRE = 60    # 区間の前（購入フェーズ相当）。ミニマップを見ているが数えてはいけない
POST = 30   # 区間の後（死亡後の観戦相当）


@pytest.fixture(scope="module")
def ver3():
    spec = importlib.util.spec_from_file_location("aoi_analysis_ver3_gps",
                                                  ROOT / "src" / "aoi_detector" / "aoi_analysis-ver3.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def in_segment_points(variant: int):
    """区間内の視線。ミニマップには注視（20サンプル）と、注視にならない短い滞在（4サンプル＝67 ms）の両方がある。"""
    k = 1 + variant
    return (hold(CROSSHAIR, 40) + saccade(3) + hold(MINIMAP, 4) + saccade(3) + hold(MINIMAP, 20 * k)
            + [None] * 3 + hold(CROSSHAIR, 30) + [(1.3, 0.5)] * 2 + saccade(3) + hold(ABILITIES, 12)
            + saccade(3) + hold(HEALTH, 3) + saccade(3) + hold(CROSSHAIR, 25))


def make_session(tmp_path: Path, ver3, session_base: str, participant_id: str, variant: int = 0,
                 out_name: str = "aoi_result") -> Path:
    """視線CSV・区間・台帳を作り、ver3 で解析した結果フォルダを返す。"""
    seg_pts = in_segment_points(variant)
    pts = hold(MINIMAP, PRE) + seg_pts + hold(CROSSHAIR, POST)
    dt = 1.0 / 60.0
    csv = tmp_path / f"{session_base}.csv"
    make_df(pts).to_csv(csv, index=False)
    seg = tmp_path / f"{session_base}_segments.csv"
    pd.DataFrame([{"round": 1, "segment_type": "alive", "video_start_sec": (PRE - 0.5) * dt,
                   "video_end_sec": (PRE + len(seg_pts) - 0.5) * dt, "end_reason": "death", "side": "attack",
                   "notes": "", "start_reason": "buy_end"}]).to_csv(seg, index=False)
    manifest = tmp_path / f"manifest_{session_base}.csv"
    pd.DataFrame([{"session_base": session_base, "participant_id": participant_id, "group": "mid",
                   "game_resolution": "3840x2160", "aspect": "16:9"}]).to_csv(manifest, index=False)
    out_root = tmp_path / out_name
    ver3.main(["--input", str(csv), "--aoi", str(AOI_JSON), "--segments", str(seg), "--offset", "0",
               "--manifest", str(manifest), "--output-dir", str(out_root)])
    return out_root / session_base


@pytest.fixture
def pilot_dirs(tmp_path, ver3):
    return [
        make_session(tmp_path, ver3, "gaze_PL01_1_unrated_20261029_150000", "PL01", variant=0),
        make_session(tmp_path, ver3, "gaze_PL01_1_unrated_20261106_150000", "PL01", variant=1),
        make_session(tmp_path, ver3, "gaze_PL02_1_unrated_20261104_150000", "PL02", variant=2),
    ]


def run(dirs, out, *extra):
    gps.main(["--results", *map(str, dirs), "--out-dir", str(out), "--lang", "en", *extra])


def edit_qc(d: Path, fn):
    p = d / "qc.json"
    q = json.loads(p.read_text(encoding="utf-8"))
    fn(q)
    p.write_text(json.dumps(q, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 積み上げの合計 = gaze_pct
# ---------------------------------------------------------------------------

def test_stacked_total_matches_ver3_gaze_pct(pilot_dirs, tmp_path):
    out = tmp_path / "fig"
    run(pilot_dirs, out)
    t = pd.read_csv(out / "gaze_pct_fixation_split.csv", encoding="utf-8-sig")
    assert (out / "gaze_pct_fixation_split.png").stat().st_size > 0
    assert len(t) == 3 * 3  # セッション × 既定の AOI 3つ
    for d in pilot_dirs:
        stored = pd.read_csv(d / "aoi_metrics_session.csv", encoding="utf-8-sig").set_index("aoi")
        rows = t[t["session_base"] == d.name].set_index("aoi")
        for name in ("minimap", "crosshair"):
            r = rows.loc[name]
            assert r["aoi_fixation_pct"] + r["aoi_nonfixation_pct"] == pytest.approx(stored.loc[name, "gaze_pct"], abs=1e-9)
            assert r["gaze_pct"] == pytest.approx(stored.loc[name, "gaze_pct"], abs=1e-9)
            assert r["gaze_pct_denominator_samples"] == stored.loc[name, "gaze_pct_denominator_samples"]
        # 下部HUDまとめ ＝ 3つの AOI の gaze_pct の和
        lower = rows.loc[itc.LOWER_HUD_LABEL]
        assert lower["gaze_pct"] == pytest.approx(stored.loc[list(itc.LOWER_HUD), "gaze_pct"].sum(), abs=1e-9)
        assert lower["aoi_components"] == "+".join(itc.LOWER_HUD)
    # ミニマップには注視と非注視の両方がある（図の意図どおりの合成データか）
    mm = t[t["aoi"] == "minimap"]
    assert (mm["n_samples_aoi_fixation"] > 0).all() and (mm["n_samples_aoi_nonfixation"] >= 4).all()
    assert ((mm["fixation_share_of_aoi_samples"] > 0) & (mm["fixation_share_of_aoi_samples"] < 1)).all()
    # 横軸のラベル：2セッションの人は月日を付ける
    assert list(dict.fromkeys(t["session_label"])) == ["PL01-1029", "PL01-1106", "PL02"]
    prov = json.loads((out / "gaze_pct_fixation_split_provenance.json").read_text(encoding="utf-8"))
    assert prov["conditions"]["aoi_config_content_sha256"]
    assert [a["aoi"] for a in prov["conditions"]["aois"]] == list(gps.DEFAULT_AOIS)


def test_mismatch_with_ver3_output_stops(pilot_dirs, tmp_path):
    """積み上げの合計が ver3 の gaze_pct とずれたら止める（ここでは保存された gaze_pct をずらす）。"""
    d = pilot_dirs[0]
    m = pd.read_csv(d / "aoi_metrics_session.csv", encoding="utf-8-sig")
    m.loc[m["aoi"] == "minimap", "gaze_pct"] += 0.01
    m.to_csv(d / "aoi_metrics_session.csv", index=False, encoding="utf-8-sig")
    with pytest.raises(SystemExit, match="一致しません"):
        run(pilot_dirs, tmp_path / "fig")
    assert not (tmp_path / "fig" / "gaze_pct_fixation_split.png").exists()


def test_reclassified_sample_stops(pilot_dirs):
    """サンプルの分類が ver3 の AOI 内サンプル数と合わなければ止める。"""
    d = pilot_dirs[0]
    s = pd.read_csv(d / "samples_classified.csv", encoding="utf-8-sig")
    i = s.index[(s["sample_class"] == "aoi_nonfixation") & (s["sample_aoi"] == "minimap")][0]
    s.loc[i, "sample_class"] = "outside"
    s.to_csv(d / "samples_classified.csv", index=False, encoding="utf-8-sig")
    qc = gps.read_qc(d)
    res = gps.load_result(d, qc)
    with pytest.raises(ValueError, match="一致しません"):
        gps.session_rows(res, gps.resolve_aois(["minimap"], res["aoi_names"]))


# ---------------------------------------------------------------------------
# 区間外のサンプル
# ---------------------------------------------------------------------------

def test_out_of_segment_samples_are_not_counted(pilot_dirs, tmp_path):
    d = pilot_dirs[0]
    s = pd.read_csv(d / "samples_classified.csv", encoding="utf-8-sig")
    out_mm = int(((s["sample_class"] == "out_of_segment") & (s["sample_aoi"] == "minimap")).sum())
    in_mm = int(((s["sample_class"] != "out_of_segment") & (s["sample_aoi"] == "minimap")).sum())
    assert out_mm == PRE  # 区間の前のミニマップは out_of_segment になっている
    res = gps.load_result(d, gps.read_qc(d))
    row = gps.session_rows(res, gps.resolve_aois(["minimap"], res["aoi_names"]))[0]
    assert row["n_samples_aoi_fixation"] + row["n_samples_aoi_nonfixation"] == in_mm
    in_seg_valid = int(((s["sample_class"] != "out_of_segment") & s["valid"]).sum())
    assert row["gaze_pct_denominator_samples"] == in_seg_valid  # 分母も区間内の有効サンプルだけ


def test_out_of_segment_inconsistent_with_segment_id_stops(pilot_dirs):
    d = pilot_dirs[0]
    s = pd.read_csv(d / "samples_classified.csv", encoding="utf-8-sig")
    i = s.index[s["sample_class"] == "out_of_segment"][0]
    s.loc[i, "sample_class"] = "aoi_fixation"   # 区間外なのに注視として数えられる形に壊す
    s.to_csv(d / "samples_classified.csv", index=False, encoding="utf-8-sig")
    res = gps.load_result(d, gps.read_qc(d))
    with pytest.raises(ValueError, match="out_of_segment"):
        gps.session_rows(res, gps.resolve_aois(["minimap"], res["aoi_names"]))


# ---------------------------------------------------------------------------
# 止める条件
# ---------------------------------------------------------------------------

def test_different_aoi_hash_stops_and_check_only_lists_sessions(pilot_dirs, tmp_path, capsys):
    edit_qc(pilot_dirs[2], lambda q: q["inputs"]["aoi_config"].update(content_sha256="f" * 64))
    with pytest.raises(SystemExit, match="1枚の図にできません"):
        run(pilot_dirs, tmp_path / "fig")
    capsys.readouterr()
    with pytest.raises(SystemExit, match="そろっていません"):
        run(pilot_dirs, tmp_path / "fig", "--check-only")
    text = capsys.readouterr().out
    assert "⚠ AOI設定（content_sha256）：2種類" in text
    line_f = next(ln for ln in text.splitlines() if ln.strip().startswith("ffffffffffff"))
    assert pilot_dirs[2].name in line_f and pilot_dirs[0].name not in line_f
    assert "どの版とも違う" in line_f
    assert not (tmp_path / "fig").exists()


def test_check_only_passes_when_consistent(pilot_dirs, tmp_path, capsys):
    run(pilot_dirs, tmp_path / "fig", "--check-only")
    text = capsys.readouterr().out
    assert "条件はそろっています" in text
    assert "params.idt_max_dispersion_deg：1種類" in text
    assert not (tmp_path / "fig").exists()  # 図は作らない


@pytest.mark.parametrize("edit, word", [
    (lambda q: q["script"].update(version="3.2.0"), "script.version"),
    (lambda q: q["params"].update(idt_max_dispersion_deg=1.5), "idt_max_dispersion_deg"),
    (lambda q: q["params"].update(max_gap_ms=200.0, visit_merge_max_gap_ms=200.0), "max_gap_ms"),
])
def test_different_version_or_params_stop(pilot_dirs, tmp_path, capsys, edit, word):
    edit_qc(pilot_dirs[1], edit)
    with pytest.raises(SystemExit):
        run(pilot_dirs, tmp_path / "fig")
    assert word in capsys.readouterr().out


def test_non_pilot_participant_is_rejected(tmp_path, ver3, pilot_dirs):
    s01 = make_session(tmp_path, ver3, "gaze_S01_1_unrated_20261110_150000", "S01")
    with pytest.raises(SystemExit, match="予備実験"):
        run(pilot_dirs + [s01], tmp_path / "fig")
    with pytest.raises(SystemExit, match="予備実験"):
        run([s01], tmp_path / "fig", "--check-only")
    assert not (tmp_path / "fig").exists()
    # 抜け道の引数が無いこと
    opts = {a.dest for a in gps.build_arg_parser()._actions}
    assert not any("pilot" in o for o in opts)


def test_missing_participant_id_is_rejected(pilot_dirs, tmp_path):
    edit_qc(pilot_dirs[0], lambda q: q.update(manifest_row=None))
    with pytest.raises(SystemExit, match="予備実験"):
        run(pilot_dirs, tmp_path / "fig")


def test_whole_session_result_is_rejected(pilot_dirs, tmp_path):
    edit_qc(pilot_dirs[0], lambda q: q["inputs"].update(segments_file=None))
    with pytest.raises(SystemExit, match="区間ファイルなし"):
        run(pilot_dirs, tmp_path / "fig")


def test_duplicate_session_is_rejected(pilot_dirs, tmp_path):
    with pytest.raises(SystemExit, match="2回"):
        run([pilot_dirs[0], pilot_dirs[0]], tmp_path / "fig")


# ---------------------------------------------------------------------------
# AOI の指定
# ---------------------------------------------------------------------------

def test_lower_hud_uses_primary_analysis_definition():
    names = [a["name"] for a in json.loads(AOI_JSON.read_text(encoding="utf-8"))["aois"]]
    resolved = dict(gps.resolve_aois(list(gps.DEFAULT_AOIS), names))
    assert resolved[itc.LOWER_HUD_LABEL] == itc.LOWER_HUD == ("health_armor", "abilities", "ammo_weapon")
    assert "credits" not in resolved[itc.LOWER_HUD_LABEL]


def test_aoi_option_and_refusals():
    names = [a["name"] for a in json.loads(AOI_JSON.read_text(encoding="utf-8"))["aois"]]
    assert [n for n, _ in gps.resolve_aois(["round_timer", "abilities"], names)] == ["round_timer", "abilities"]
    with pytest.raises(ValueError, match="credits"):
        gps.resolve_aois(["credits"], names)
    with pytest.raises(ValueError, match="ありません"):
        gps.resolve_aois(["outside"], names)
    with pytest.raises(ValueError, match="2回"):
        gps.resolve_aois(["minimap", "minimap"], names)


def test_custom_aois_end_to_end(pilot_dirs, tmp_path):
    out = tmp_path / "fig"
    run(pilot_dirs, out, "--aois", "abilities", "health_armor", "round_timer")
    t = pd.read_csv(out / "gaze_pct_fixation_split.csv", encoding="utf-8-sig")
    assert set(t["aoi"]) == {"abilities", "health_armor", "round_timer"}
    # 0件の AOI（round_timer）も行が残る
    rt = t[t["aoi"] == "round_timer"]
    assert len(rt) == 3 and (rt["gaze_pct"] == 0).all()
    assert rt["fixation_share_of_aoi_samples"].isna().all()


# ---------------------------------------------------------------------------
# 出力・上書き防止・文字コード
# ---------------------------------------------------------------------------

def test_overwrite_protection(pilot_dirs, tmp_path):
    out = tmp_path / "fig"
    run(pilot_dirs, out)
    run(pilot_dirs, out)  # 同じ条件なら作り直してよい
    with pytest.raises(SystemExit, match="条件の違う"):
        run(pilot_dirs[:2], out)  # セッションが違う
    with pytest.raises(SystemExit, match="条件の違う"):
        run(pilot_dirs, out, "--aois", "minimap")
    run(pilot_dirs, out, "--aois", "minimap", "--overwrite")
    t = pd.read_csv(out / "gaze_pct_fixation_split.csv", encoding="utf-8-sig")
    assert set(t["aoi"]) == {"minimap"}


def test_existing_output_without_provenance_stops(pilot_dirs, tmp_path):
    out = tmp_path / "fig"
    out.mkdir()
    (out / "gaze_pct_fixation_split.png").write_bytes(b"x")
    with pytest.raises(SystemExit, match="provenance"):
        run(pilot_dirs, out)


def test_output_csv_is_utf8_with_bom(pilot_dirs, tmp_path):
    """Excel（研究室PC）で文字化けしないよう、出力 CSV は BOM 付き UTF-8（既存の出力と同じ）。"""
    out = tmp_path / "fig"
    run(pilot_dirs, out)
    data = (out / "gaze_pct_fixation_split.csv").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    data.decode("utf-8")
    (out / "gaze_pct_fixation_split_provenance.json").read_bytes().decode("utf-8")


def test_low_dpi_is_rejected(pilot_dirs, tmp_path):
    with pytest.raises(SystemExit, match="150"):
        run(pilot_dirs, tmp_path / "fig", "--dpi", "100")


def test_japanese_falls_back_to_english_without_font(monkeypatch):
    monkeypatch.setattr(gps, "find_japanese_font", lambda: None)
    assert gps.choose_language("auto") == ("en", None)
    assert gps.choose_language("ja") == ("en", None)
    assert gps.choose_language("en") == ("en", None)


def test_footer_has_commit_hash_and_sessions():
    t = pd.DataFrame({"session_base": ["gaze_PL01_a", "gaze_PL02_b"]})
    text = gps.footer_text(t, {"aoi_config_content_sha256": "0123456789abcdef", "aoi_analysis_version": "3.3.0"},
                           {"commit": "abcdef1234567890", "dirty": False})
    assert "abcdef123456" in text and "abcdef1234567" not in text
    assert "AOI 0123456789ab " in text
    assert "gaze_PL01_a" in text and "gaze_PL02_b" in text


# ---------------------------------------------------------------------------
# aggregate_sessions の一覧（--check-only と集計の中止時の表示）
# ---------------------------------------------------------------------------

def test_condition_breakdown_groups_sessions():
    qcs = [{"session_base": f"s{i}", "script": {"version": "3.3.0"}, "params": dict(itc.gm.DEFAULT_PARAMS),
            "inputs": {"aoi_config": {"content_sha256": h}}} for i, h in enumerate(["a" * 64, "a" * 64, "b" * 64])]
    items = {it["item"]: it for it in agg.condition_breakdown(qcs)}
    aoi = items["AOI設定（content_sha256）"]
    assert aoi["differs"] and aoi["values"] == {"a" * 64: ["s0", "s1"], "b" * 64: ["s2"]}
    assert not items["aoi_analysis の版（script.version）"]["differs"]
    text = agg.format_condition_breakdown(list(items.values()), only_differing=True,
                                          hash_history={"a" * 64: ("6133137", "2026-10-21")})
    assert "6133137（2026-10-21）と同じ" in text and "s2" in text
    assert "params." not in text  # そろっている項目は出さない
    # 判定そのものは check_params_consistent と同じ
    assert any("AOI設定" in p for p in agg.check_params_consistent(qcs))


def test_aoi_hash_history_knows_current_aoi_json():
    """リポジトリの現在の AOI JSON のハッシュが履歴に入っている（git が使える環境のみ）。"""
    from aoi_geometry import aoi_config_content_sha256
    hist = agg.aoi_hash_history()
    if not hist:
        pytest.skip("git の履歴を読めない環境")
    current = aoi_config_content_sha256(json.loads(AOI_JSON.read_text(encoding="utf-8")))
    committed = agg.subprocess.run(["git", "diff", "--quiet", "HEAD", "--", agg.AOI_CONFIG_REPO_PATH], cwd=ROOT)
    if committed.returncode != 0:
        pytest.skip("AOI JSON に未コミットの変更がある")
    assert current in hist


def test_aggregate_prints_breakdown_on_mismatch(tmp_path, capsys):
    from test_aggregate_sessions import write_session
    res = tmp_path / "res"
    write_session(res, "s_a", hold(CROSSHAIR, 30))
    write_session(res, "s_b", hold(CROSSHAIR, 30))
    p = res / "s_b" / "qc.json"
    q = json.loads(p.read_text(encoding="utf-8"))
    q["inputs"]["aoi_config"]["sha256"] = "y"
    p.write_text(json.dumps(q), encoding="utf-8")
    manifest = tmp_path / "m.csv"
    pd.DataFrame([{"session_base": "s_a", "participant_id": "P1", "group": "low"},
                  {"session_base": "s_b", "participant_id": "P1", "group": "low"}]).to_csv(manifest, index=False)
    with pytest.raises(SystemExit):
        agg.main(["--results-dir", str(res), "--manifest", str(manifest), "--out-dir", str(tmp_path / "out")])
    text = capsys.readouterr().out
    assert "⚠ AOI設定（sha256）：2種類" in text
    assert "s_a" in text and "s_b" in text
