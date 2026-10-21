# -*- coding: utf-8 -*-
"""ui_fonts（pygame の日本語フォント選択）のテスト。pygame はスタブで代用する。"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import ui_fonts  # noqa: E402


def make_pg(available):
    calls = []
    font = types.SimpleNamespace(
        get_init=lambda: True,
        init=lambda: None,
        match_font=lambda name: available.get(name),
        Font=lambda path, size: ("Font", path, size),
        SysFont=lambda name, size: ("SysFont", name, size),
    )
    return types.SimpleNamespace(font=font), calls


def test_prefers_meiryo():
    pg, _ = make_pg({"msgothic": "C:/msgothic.ttc", "meiryo": "C:/meiryo.ttc"})
    assert ui_fonts.japanese_font(pg, 48) == ("Font", "C:/meiryo.ttc", 48)


def test_explicit_font_path_wins():
    pg, _ = make_pg({"meiryo": "C:/meiryo.ttc"})
    assert ui_fonts.japanese_font(pg, 24, "D:/my.ttf") == ("Font", "D:/my.ttf", 24)


def test_fallback_to_default_when_not_found(monkeypatch):
    monkeypatch.setattr(ui_fonts, "_WINDOWS_FONT_FILES", [])
    pg, _ = make_pg({})
    assert ui_fonts.japanese_font(pg, 24) == ("SysFont", None, 24)
