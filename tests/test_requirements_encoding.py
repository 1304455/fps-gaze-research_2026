# -*- coding: utf-8 -*-
"""
requirements*.txt が UTF-8 で、先頭に coding 宣言があることを確認する。
pip は先頭2行の coding 宣言を見て文字コードを決める。宣言が無いと OS の既定（日本語 Windows では cp932）で
読むため、UTF-8 の日本語コメントがあると研究室PCで失敗し、Shift-JIS にすると他の環境で失敗する。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name", ["requirements.txt", "requirements-dev.txt"])
def test_requirements_are_utf8_with_coding_line(name):
    data = (ROOT / name).read_bytes()
    data.decode("utf-8")  # UTF-8 として読めること
    assert data.split(b"\n", 1)[0].strip() == b"# -*- coding: utf-8 -*-"
