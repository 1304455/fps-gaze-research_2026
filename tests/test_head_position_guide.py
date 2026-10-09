# -*- coding: utf-8 -*-
"""頭部位置ガイドと、キャリブレーション手順の追加機能（較正点の位置・測り直し）のテスト。"""
import os
import threading
import time
import types

import pytest

from calibrate_validate import calibration_points, merge_retry, points_to_retry
from head_position_guide import judge_position, reference_from_calib, summarize_position


def sample(lx, rx, y=200.0, z=650.0, lvalid=1, rvalid=1):
    return {
        "left_gaze_origin_in_user_coordinate_system": (lx, y, z), "left_gaze_origin_validity": lvalid,
        "right_gaze_origin_in_user_coordinate_system": (rx, y, z), "right_gaze_origin_validity": rvalid,
    }


class TestPosition:
    def test_summarize_midpoint(self):
        pos = summarize_position([sample(-32, 32, z=640), sample(-30, 34, z=660)])
        assert pos["x_mid_mm"] == pytest.approx(1.0)
        assert pos["z_mid_mm"] == pytest.approx(650.0)
        assert pos["left_valid_rate"] == 1.0

    def test_one_eye_only_uses_that_eye(self):
        pos = summarize_position([sample(-32, 32, rvalid=0)])
        assert pos["x_mid_mm"] == pytest.approx(-32.0)
        assert pos["right_valid_rate"] == 0.0

    def test_judge_ok_and_messages(self):
        ok = judge_position(summarize_position([sample(-32, 32, z=660)] * 10), target_z_mm=650, tol_z_mm=30)
        assert ok["ok"]
        near = judge_position(summarize_position([sample(-32, 32, z=600)] * 10), target_z_mm=650, tol_z_mm=30)
        assert not near["ok_z"] and "後ろへ" in near["messages"][0]
        right = judge_position(summarize_position([sample(18, 82)] * 10), target_z_mm=650, tol_z_mm=30)
        assert not right["ok_x"] and "左へ" in right["messages"][0]  # x>0 はユーザーから見て右 → 左へ
        flipped = judge_position(summarize_position([sample(18, 82)] * 10), target_z_mm=650, tol_z_mm=30, flip_x=True)
        assert "右へ" in flipped["messages"][0]

    def test_y_is_judged_only_with_reference(self):
        pos = summarize_position([sample(-32, 32, y=260)] * 10)
        assert judge_position(pos, target_z_mm=650, tol_z_mm=30)["ok_y"] is None
        v = judge_position(pos, target_z_mm=650, tol_z_mm=30, target_y_mm=200)
        assert v["ok_y"] is False and "下へ" in v["messages"][0]

    def test_no_eyes(self):
        v = judge_position(summarize_position([sample(0, 0, lvalid=0, rvalid=0)]), target_z_mm=650, tol_z_mm=30)
        assert not v["ok"]

    def test_reference_from_calib(self):
        calib = {"head_position": {"recorded": {"x_mid_mm": 1.0, "y_mid_mm": 2.0, "z_mid_mm": 640.0}}}
        assert reference_from_calib(calib) == {"x_mid_mm": 1.0, "y_mid_mm": 2.0, "z_mid_mm": 640.0}
        assert reference_from_calib({}) is None


class TestCalibrationProcedure:
    def test_calibration_points_margin(self):
        assert calibration_points()[0] == (0.5, 0.5)
        pts = calibration_points(0.05)
        assert min(p[1] for p in pts) == 0.05 and max(p[1] for p in pts) == 0.95
        with pytest.raises(ValueError):
            calibration_points(0.6)

    def test_retry_selection_and_merge(self):
        pts = [{"label": "a", "accuracy_deg": 0.5}, {"label": "b", "accuracy_deg": 3.36},
               {"label": "c", "accuracy_deg": None}]
        assert points_to_retry(pts, 1.5) == [1, 2]
        def att(acc, prec, lrate, rrate=1.0):
            return {"label": "b", "accuracy_deg": acc, "precision_rms_s2s_deg": prec, "n_samples_window": 30,
                    "left": {"valid_rate": lrate, "bias_y_norm": 0.08}, "right": {"valid_rate": rrate}}
        # 品質が同じなら測り直しを採用
        m = merge_retry(att(3.36, 0.2, 1.0), att(0.7, 0.2, 1.0))
        assert m["accuracy_deg"] == 0.7 and m["retried"] and m["selected_attempt"] == "retry"
        assert m["first_attempt"]["accuracy_deg"] == 3.36
        assert m["first_attempt"]["left_bias_y_norm"] == 0.08
        # 研究室PCの実例：測り直しで左眼を見失い（有効率 0.67）、precision も悪化 → 1回目を採用
        m = merge_retry(att(1.71, 0.29, 1.0), att(2.74, 0.48, 0.67))
        assert m["selected_attempt"] == "first" and m["accuracy_deg"] == 1.71
        assert m["retry_attempt"]["accuracy_deg"] == 2.74
        # accuracy が良くても、データ品質が悪い方は採らない（結果で選別しない）
        m = merge_retry(att(2.0, 0.2, 1.0), att(0.5, 0.9, 1.0))
        assert m["selected_attempt"] == "first"


def test_position_guide_screen_runs_and_records(monkeypatch):
    """pygame のダミー画面で位置ガイドを実際に描画し、SPACE で確定して記録されることを確認する。"""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    pygame = pytest.importorskip("pygame")
    from calibrate_validate import TargetScreen
    from head_position_guide import run_position_guide

    class FakeTracker:
        def subscribe_to(self, kind, cb, as_dictionary=True):
            self.stop = False

            def feed():
                while not self.stop:
                    cb(sample(-30, 34, z=655))
                    time.sleep(1 / 60)
            self.th = threading.Thread(target=feed, daemon=True)
            self.th.start()

        def unsubscribe_from(self, kind, cb):
            self.stop = True

    monitor = types.SimpleNamespace(x=0, y=0, width=960, height=540)
    screen = TargetScreen(monitor, pygame)

    def press_space():
        time.sleep(1.2)
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    threading.Thread(target=press_space, daemon=True).start()
    tr = types.SimpleNamespace(EYETRACKER_GAZE_DATA="gaze")
    try:
        res = run_position_guide(tr, FakeTracker(), screen, target_z_mm=650, tol_z_mm=30, tol_x_mm=30)
    finally:
        screen.close()
    assert res["ok"]
    assert res["recorded"]["z_mid_mm"] == pytest.approx(655.0)
    assert res["recorded"]["x_mid_mm"] == pytest.approx(2.0)
