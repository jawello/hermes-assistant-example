import json

import pytest

from zenmoney_cli import cli


def run(capsys, argv):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_no_token_is_clean_error(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("ZENMONEY_TOKEN", raising=False)
    monkeypatch.setenv("ZENMONEY_STATE_DIR", str(tmp_path))
    code, out = run(capsys, ["sync"])
    assert code == 1
    assert out["ok"] is False
    assert "ZENMONEY_TOKEN" in out["error"]


def test_split_parses_parts():
    assert cli.parse_part("Еда=80.21") == ("Еда", "80.21")
    with pytest.raises(SystemExit):
        cli.parse_part("нет-знака-равно")


def test_state_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("ZENMONEY_STATE_DIR", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert cli.state_dir() == tmp_path / "zenmoney"
