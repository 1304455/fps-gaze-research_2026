# -*- coding: utf-8 -*-
"""
pygame で日本語を表示するためのフォント選択。

pygame.font.SysFont(None, ...) は pygame 同梱のフォント（freesansbold）を使い、日本語の字形を
持たないため、日本語が表示されない（英数字だけ出る）。Windows に標準で入っている日本語フォントを
優先順に探して使う。見つからなければ従来どおり既定フォントにフォールバックする。
"""

from __future__ import annotations

from pathlib import Path

# pygame.font.match_font に渡す名前（小文字・空白なし）。研究室PC（Windows）の標準フォントを先頭に置く
JAPANESE_FONT_CANDIDATES = [
    "meiryo", "yugothic", "yugothicui", "msgothic", "mspgothic",   # Windows
    "hiraginosans", "hiraginokakugothicpron",                       # macOS
    "notosanscjkjp", "notosansjp", "ipagothic", "ipaexgothic", "takaogothic",  # Linux 等
]

# match_font で見つからない場合に直接探すファイル（Windows）
_WINDOWS_FONT_FILES = ["meiryo.ttc", "YuGothM.ttc", "msgothic.ttc"]


def find_japanese_font_path(pygame_mod) -> "str | None":
    """日本語を表示できるフォントファイルのパスを返す。見つからなければ None。"""
    for name in JAPANESE_FONT_CANDIDATES:
        try:
            path = pygame_mod.font.match_font(name)
        except Exception:
            path = None
        if path:
            return path
    for fname in _WINDOWS_FONT_FILES:
        p = Path("C:/Windows/Fonts") / fname
        if p.exists():
            return str(p)
    return None


def japanese_font(pygame_mod, size: int, font_path: "str | None" = None):
    """
    日本語を表示できる pygame.font.Font を返す。
    font_path を指定すればそれを使う（--font 引数用）。見つからない場合は警告を出して既定フォントを返す。
    """
    if not pygame_mod.font.get_init():
        pygame_mod.font.init()
    path = font_path or find_japanese_font_path(pygame_mod)
    if path:
        try:
            return pygame_mod.font.Font(path, size)
        except Exception as e:
            print(f"⚠ 警告: フォント {path} を読み込めませんでした（{e}）。既定フォントを使います。")
    else:
        print("⚠ 警告: 日本語フォントが見つかりません。日本語が表示されない可能性があります（--font で指定可）。")
    return pygame_mod.font.SysFont(None, size)
