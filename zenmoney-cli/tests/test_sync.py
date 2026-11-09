from zenmoney_cli import ops
from zenmoney_cli.state import State


def test_sync_sends_stored_timestamp_and_applies(tmp_path, client, fake_zen):
    fake_zen.responses.append({
        "serverTimestamp": 500,
        "transaction": [{"id": "t1", "outcome": 9.5}],
    })
    st = State.load(tmp_path)
    result = ops.sync(client, st)
    sent = fake_zen.requests[0]
    assert sent["serverTimestamp"] == 0
    assert isinstance(sent["currentClientTimestamp"], int)
    assert result["serverTimestamp"] == 500
    assert State.load(tmp_path).entities["transaction"]["t1"]["outcome"] == 9.5


def test_sync_incremental_uses_new_timestamp(tmp_path, client, fake_zen):
    fake_zen.responses.append({"serverTimestamp": 500})
    fake_zen.responses.append({"serverTimestamp": 600})
    st = State.load(tmp_path)
    ops.sync(client, st)
    ops.sync(client, st)
    assert fake_zen.requests[1]["serverTimestamp"] == 500
