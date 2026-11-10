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


def test_share_parses_like_a_part():
    assert cli.parse_part("Маша=30.74") == ("Маша", "30.74")
    for bad in ("=30", "Маша", "Маша="):
        with pytest.raises(SystemExit):
            cli.parse_part(bad)


def test_split_with_shares_end_to_end(capsys, monkeypatch, tmp_path,
                                      client, fake_zen, full_tx):
    from zenmoney_cli.state import State
    st = State.load(tmp_path)
    st.apply({"serverTimestamp": 1,
              "tag": [{"id": "food", "title": "Еда", "parent": None}],
              "transaction": [full_tx]})
    st.save()
    monkeypatch.setenv("ZENMONEY_TOKEN", "tok")
    monkeypatch.setenv("ZENMONEY_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "ZenMoneyClient", lambda token: client)
    code, out = run(capsys, ["split", "t1", "Еда=100.00",
                             "--share", "Маша=20.00", "--share", "Петя=18.68"])
    assert code == 0, out
    assert [(p["tag"], p["payee"]) for p in out["parts"]] == [
        ("Еда", "Supermarket"), (None, "Маша"), (None, "Петя")]


def test_split_help_names_share(capsys):
    with pytest.raises(SystemExit):
        cli.main(["split", "--help"])
    assert "--share" in capsys.readouterr().out
