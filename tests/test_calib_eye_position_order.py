# -*- coding: utf-8 -*-
"""
calibrate_validate 1.4.0 の追加分のテスト（F1・F6。SDK・画面は偽物で代用する）。
- F1：検証点ごとの眼の位置（eye_xyz_mm）と、解析と同じ作り方の両眼平均の視線の指標（binocular_avg）
- F6：各測定の提示の通し番号（presentation_index）と段階（presentation_phase）
"""
import math

import numpy as np
import pytest

from calibrate_validate import (
    binocular_avg_metrics,
    display_point_to_ucs,
    eye_position,
    merge_retry,
    point_metrics,
    run_validation,
)

DA = {"top_left": (-300.0, 360.0, 50.0), "top_right": (300.0, 360.0, 50.0), "bottom_left": (-300.0, 20.0, 50.0)}
LEFT_EYE = np.array([-32.0, 190.0, 700.0])
RIGHT_EYE = np.array([32.0, 190.0, 700.0])
NAN3 = (math.nan,) * 3


def _sample(target_xy, left_off_mm=(0.0, 0.0), right_off_mm=(0.0, 0.0), left_ok=True, right_ok=True,
            left_eye=LEFT_EYE, right_eye=RIGHT_EYE):
    """目標点から画面上で (dx, dy) mm ずれた点を見ているサンプル。dy は画面の下向きが正。"""
    t = display_point_to_ucs(*target_xy, DA)
    s = {}
    for side, off, ok, eye in (("left", left_off_mm, left_ok, left_eye), ("right", right_off_mm, right_ok, right_eye)):
        g = t + np.array([off[0], -off[1], 0.0])
        s.update({
            f"{side}_gaze_point_validity": int(ok), f"{side}_gaze_origin_validity": int(ok),
            f"{side}_gaze_origin_in_user_coordinate_system": tuple(eye) if ok else NAN3,
            f"{side}_gaze_point_in_user_coordinate_system": tuple(g) if ok else NAN3,
            f"{side}_gaze_point_on_display_area": ((target_xy[0] + off[0] / 600.0, target_xy[1] + off[1] / 340.0)
                                                   if ok else (math.nan, math.nan)),
        })
    return s


def test_binocular_avg_cancels_opposite_eye_errors():
    # 左眼は右へ 8 mm、右眼は左へ 8 mm：左右それぞれの誤差は約 0.7° だが、平均した視線は目標と一致する
    xy = (0.5, 0.5)
    m = point_metrics([_sample(xy, (8.0, 0.0), (-8.0, 0.0))] * 10, xy, DA)
    assert m["accuracy_deg"] == pytest.approx(math.degrees(math.atan(8.0 / 650.0)), rel=0.05)
    assert m["binocular_avg"]["accuracy_deg"] == pytest.approx(0.0, abs=1e-6)
    assert m["binocular_avg"]["bias_x_norm"] == pytest.approx(0.0, abs=1e-9)
    assert m["binocular_avg"]["n_samples"] == 10


def test_binocular_avg_uses_single_eye_when_other_invalid():
    # 解析（v5 の center_x）と同じく、片眼だけ有効ならその眼の視線を使う
    xy = (0.3, 0.2)
    m = binocular_avg_metrics([_sample(xy, (0.0, 6.8), right_ok=False)] * 4, xy, DA)
    assert m["n_samples"] == 4
    assert m["bias_y_norm"] == pytest.approx(6.8 / 340.0)
    assert m["accuracy_deg"] > 0.5


def test_binocular_avg_without_valid_samples():
    m = binocular_avg_metrics([_sample((0.5, 0.5), left_ok=False, right_ok=False)] * 3, (0.5, 0.5), DA)
    assert m["n_samples"] == 0 and m["accuracy_deg"] is None and m["bias_x_norm"] is None


def test_eye_position_midpoint_and_single_eye():
    xy = (0.5, 0.5)
    both = [_sample(xy)] * 3
    assert eye_position(both) == pytest.approx([0.0, 190.0, 700.0])
    # 片眼のみのサンプルはその眼の位置（head_position_guide.summarize_position と同じ定義）
    mixed = [_sample(xy), _sample(xy, right_ok=False)]
    assert eye_position(mixed) == pytest.approx([-16.0, 190.0, 700.0])
    assert eye_position([_sample(xy, left_ok=False, right_ok=False)]) is None
    assert point_metrics(both, xy, DA)["eye_xyz_mm"] == pytest.approx([0.0, 190.0, 700.0])


def test_eye_position_matches_head_position_guide():
    from head_position_guide import summarize_position
    xy = (0.5, 0.5)
    near = (np.array([-30.0, 180.0, 600.0]), np.array([34.0, 182.0, 610.0]))
    samples = [_sample(xy), _sample(xy, left_eye=near[0], right_eye=near[1]), _sample(xy, left_ok=False)]
    pos = summarize_position(samples)
    assert eye_position(samples) == pytest.approx([pos["x_mid_mm"], pos["y_mid_mm"], pos["z_mid_mm"]])


def test_existing_keys_unchanged():
    # 1.3.0 までのキーと値は変えない（追加のみ）
    xy = (0.5, 0.5)
    m = point_metrics([_sample(xy, (0.0, 6.8))] * 5, xy, DA)
    for k in ("target_x", "target_y", "n_samples_window", "left", "right", "accuracy_deg", "precision_rms_s2s_deg"):
        assert k in m
    assert m["left"]["bias_y_norm"] == pytest.approx(6.8 / 340.0)
    assert m["left"]["valid_rate"] == 1.0


def test_merge_retry_keeps_presentation_and_eye_position():
    first = {"label": "p", "accuracy_deg": 3.0, "precision_rms_s2s_deg": 1.2, "presentation_index": 4,
             "presentation_phase": "first", "eye_xyz_mm": [0.0, -45.0, 660.0], "binocular_avg": {"accuracy_deg": 2.9},
             "left": {"valid_rate": 1.0}, "right": {"valid_rate": 1.0}}
    retry = {**first, "accuracy_deg": 0.5, "precision_rms_s2s_deg": 0.2, "presentation_index": 17,
             "presentation_phase": "retry", "eye_xyz_mm": [0.0, -60.0, 620.0], "binocular_avg": {"accuracy_deg": 0.4}}
    m = merge_retry(first, retry)
    assert m["selected_attempt"] == "retry" and m["presentation_index"] == 17
    assert m["first_attempt"]["presentation_index"] == 4 and m["first_attempt"]["presentation_phase"] == "first"
    assert m["retry_attempt"]["eye_xyz_mm"] == [0.0, -60.0, 620.0]
    assert m["first_attempt"]["binocular_avg_accuracy_deg"] == 2.9


# ---------------------------------------------------------------------------
# run_validation を偽の SDK・画面で動かし、提示順の記録を確かめる
# ---------------------------------------------------------------------------

class _FakeTr:
    EYETRACKER_GAZE_DATA = "gaze"


class _FakeEyetracker:
    def __init__(self):
        self.callback = None

    def subscribe_to(self, kind, callback, as_dictionary=True):
        self.callback = callback

    def unsubscribe_from(self, kind, callback):
        self.callback = None


class _FakeScreen:
    """
    target() で目標を覚え、hold() の間に視線サンプルを流す。bad の点は1回目だけ右へ大きくずらし、揺らす
    （区間内で目が動いた想定。precision が悪いので、測り直しの採否はデータ品質で測り直し側になる）。
    """

    def __init__(self, eyetracker, bad_labels_xy):
        self.et = eyetracker
        self.bad = set(bad_labels_xy)
        self.current = None
        self.shown = []

    def target(self, x, y, shrink_sec):
        self.current = (x, y)
        self.shown.append((x, y))
        return True

    def hold(self, sec):
        xy = self.current
        moving = xy in self.bad and self.shown.count(xy) == 1
        for i in range(5):
            off = (80.0 + (10.0 if i % 2 else -10.0), 0.0) if moving else (0.0, 0.0)
            self.et.callback(_sample(xy, off, off))
        return True


def test_run_validation_records_presentation_order():
    targets = [{"label": f"t{i}", "kind": "grid", "x": x, "y": y}
               for i, (x, y) in enumerate([(0.1, 0.1), (0.5, 0.1), (0.9, 0.1), (0.5, 0.5)])]
    et = _FakeEyetracker()
    screen = _FakeScreen(et, bad_labels_xy=[(0.5, 0.1), (0.5, 0.5)])
    res = run_validation(_FakeTr(), et, screen, targets, DA, shrink_sec=0.0, settle_ms=0.0, window_ms=1000.0,
                         retry_threshold_deg=1.5)
    # 1周目は 0〜3、測り直しは失敗した点を index 順に 4, 5
    assert [r["presentation_phase"] for r in res] == ["first", "retry", "first", "retry"]
    assert res[0]["presentation_index"] == 0 and res[2]["presentation_index"] == 2
    assert res[1]["first_attempt"]["presentation_index"] == 1 and res[1]["retry_attempt"]["presentation_index"] == 4
    assert res[3]["first_attempt"]["presentation_index"] == 3 and res[3]["retry_attempt"]["presentation_index"] == 5
    assert res[1]["presentation_index"] == 4 and res[1]["accuracy_deg"] < 0.1
    assert all(r["eye_xyz_mm"] == pytest.approx([0.0, 190.0, 700.0]) for r in res)
    assert et.callback is None  # 購読を解除している
