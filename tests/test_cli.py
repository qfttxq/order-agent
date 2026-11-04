"""CLI 入口测试。

这个文件之前完全不存在，所以 `cli.py` 的行覆盖率是 0%——而它恰好是用户唯一的入口。
GBK 崩溃（A2）就藏在这里：不是逻辑错，是「最高频的真实输入没人测」。
"""

from __future__ import annotations

import pytest

from order_agent import cli


def test_reads_utf8_file(tmp_path):
    path = tmp_path / "req.txt"
    path.write_text("想做知识库", encoding="utf-8")
    assert cli._read_file(str(path)) == "想做知识库"


def test_reads_gbk_file(tmp_path):
    """记事本「另存为 ANSI」就是 GBK。Windows 中文环境下客户发来的需求基本都是这种。"""
    path = tmp_path / "req.txt"
    path.write_bytes("我们有 300 份产品手册，想做知识库".encode("gbk"))
    assert cli._read_file(str(path)) == "我们有 300 份产品手册，想做知识库"


def test_gbk_file_analyzes_end_to_end(tmp_path, capsys):
    path = tmp_path / "req.txt"
    path.write_bytes("我们有 300 份产品手册，客服查资料找不到，想做个知识库。".encode("gbk"))

    assert cli.main(["analyze", "-f", str(path), "--no-llm", "--json"]) == 0
    assert '"kind": "K"' in capsys.readouterr().out


def test_binary_file_gets_readable_message(tmp_path):
    """GB18030 能解码几乎任意字节，不设防线的话 docx 会被当成文本分析出离谱结论。"""
    path = tmp_path / "req.docx"
    path.write_bytes(b"PK\x03\x04\x00\x00\x00")

    with pytest.raises(SystemExit) as excinfo:
        cli._read_file(str(path))
    assert "不是文本文件" in str(excinfo.value)


def test_missing_file_does_not_dump_traceback(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli._read_file(str(tmp_path / "nope.txt"))
    assert "读不到文件" in str(excinfo.value)


def test_missing_file_exits_nonzero(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["analyze", "-f", str(tmp_path / "nope.txt"), "--no-llm"])
    assert excinfo.value.code != 0
