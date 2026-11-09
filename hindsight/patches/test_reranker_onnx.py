"""Tests for the int8 ONNX reranker patch.

The patch must install the adapter next to a file shaped like upstream's, load the
named ONNX file instead of torch's model when asked, leave the upstream path alone
otherwise, and refuse a file it does not recognise. onnxruntime, transformers and
huggingface_hub are replaced with fakes, because CI has none of them; the real ones
are exercised by running the built image against the bench.
"""
import asyncio
import importlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import reranker_onnx as patch

ONNX_FILE = "onnx/model_quint8_avx2.onnx"
MODEL = "org/multilingual-reranker"

# Shaped like hindsight_api/engine/cross_encoder.py, hindsight-all 0.10.2: the
# blocks the patch replaces are verbatim, the helpers are stubs that record calls.
CROSS_ENCODER = '''\
from concurrent.futures import ThreadPoolExecutor

CALLS = []


def align_local_model_weights(model, label):
    CALLS.append("align")


def resolve_model_device_type(model):
    CALLS.append("resolve")
    return "torch-device"


def assert_finite_local_output(output, label):
    CALLS.append(("probe", list(output)))


class LocalSTCrossEncoder:
    def __init__(self, model_name):
        self.model_name = model_name
        self.trust_remote_code = False
        self._model = None
        self._device_type = "unset"

    async def initialize(self):
        from sentence_transformers import CrossEncoder

        device = "cpu"
        try:
            try:
                self._model = CrossEncoder(
                    self.model_name,
                    device=device,
                    model_kwargs={"low_cpu_mem_usage": False},
                    trust_remote_code=self.trust_remote_code,
                )
            finally:
                pass
        finally:
            pass

        # Safetensors weights are mapped zero-copy.
        align_local_model_weights(self._model.model, label=f"Reranker[{self.model_name}]")

        self._device_type = resolve_model_device_type(self._model)

        assert_finite_local_output(
            self._model.predict([("hindsight startup probe", "hindsight startup probe")]),
            label=f"Reranker[{self.model_name}]",
        )
'''


class FakeTorchCrossEncoder:
    created = []

    def __init__(self, name, **kwargs):
        FakeTorchCrossEncoder.created.append(name)
        self.model = object()

    def predict(self, pairs):
        return [0.5 for _ in pairs]


class FakeTokenizer:
    """Encodes each pair's query length, as int32 like the real tokenizer's numpy output."""

    def __call__(self, queries, documents, padding, truncation, return_tensors):
        assert padding and truncation and return_tensors == "np"
        ids = np.array([[len(q), len(d)] for q, d in zip(queries, documents)], dtype=np.int32)
        return {
            "input_ids": ids,
            "attention_mask": np.ones_like(ids),
            "token_type_ids": np.zeros_like(ids),
        }


class FakeSession:
    """Scores a pair by its first token, in the (batch, 1) shape a reranker export returns."""

    def __init__(self, path, options, providers):
        self.path = path
        self.options = options
        self.providers = providers
        self.feeds = []

    def get_inputs(self):
        return [types.SimpleNamespace(name="input_ids"), types.SimpleNamespace(name="attention_mask")]

    def run(self, outputs, feed):
        self.feeds.append(feed)
        return [feed["input_ids"][:, 0].astype(np.float32).reshape(-1, 1)]


@pytest.fixture
def fakes(monkeypatch):
    """The heavy imports the adapter and upstream need, as recording fakes."""
    FakeTorchCrossEncoder.created = []
    downloads = []
    tokenizers = []
    sessions = []

    def hf_hub_download(repo, filename):
        downloads.append((repo, filename))
        return f"/cache/{repo}/{filename}"

    def from_pretrained(name):
        tokenizers.append(name)
        return FakeTokenizer()

    def inference_session(path, options, providers):
        sessions.append(FakeSession(path, options, providers))
        return sessions[-1]

    modules = {
        "sentence_transformers": types.SimpleNamespace(CrossEncoder=FakeTorchCrossEncoder),
        "huggingface_hub": types.SimpleNamespace(hf_hub_download=hf_hub_download),
        "transformers": types.SimpleNamespace(
            AutoTokenizer=types.SimpleNamespace(from_pretrained=from_pretrained)
        ),
        "onnxruntime": types.SimpleNamespace(SessionOptions=types.SimpleNamespace, InferenceSession=inference_session),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(downloads=downloads, tokenizers=tokenizers, sessions=sessions)


@pytest.fixture
def engine(tmp_path, monkeypatch, fakes):
    """A package shaped like hindsight_api/engine, patched by the script's main()."""
    package = "patched_engine"
    directory = tmp_path / package
    directory.mkdir()
    (directory / "__init__.py").write_text("")
    (directory / "cross_encoder.py").write_text(CROSS_ENCODER)
    assert patch.main(["reranker_onnx.py", str(directory)]) == 0
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield importlib.import_module(f"{package}.cross_encoder")
    for name in [name for name in sys.modules if name.split(".")[0] == package]:
        del sys.modules[name]


def initialized(engine):
    encoder = engine.LocalSTCrossEncoder(MODEL)
    asyncio.run(encoder.initialize())
    return encoder


def test_unset_variable_keeps_the_upstream_model(engine, monkeypatch, fakes):
    monkeypatch.delenv(patch.ENV_VAR, raising=False)
    encoder = initialized(engine)
    assert isinstance(encoder._model, FakeTorchCrossEncoder)
    assert encoder._device_type == "torch-device"
    assert "align" in engine.CALLS and "resolve" in engine.CALLS
    assert fakes.sessions == []


def test_set_variable_loads_the_onnx_file_and_not_torch(engine, monkeypatch, fakes):
    monkeypatch.setenv(patch.ENV_VAR, ONNX_FILE)
    encoder = initialized(engine)
    assert FakeTorchCrossEncoder.created == []
    assert fakes.downloads == [(MODEL, ONNX_FILE)]
    assert fakes.tokenizers == [MODEL]
    assert fakes.sessions[0].path == f"/cache/{MODEL}/{ONNX_FILE}"


def test_onnx_model_skips_the_torch_helpers_and_still_runs_the_startup_probe(engine, monkeypatch):
    monkeypatch.setenv(patch.ENV_VAR, ONNX_FILE)
    encoder = initialized(engine)
    assert encoder._device_type == "cpu"
    assert "align" not in engine.CALLS and "resolve" not in engine.CALLS
    probes = [call for call in engine.CALLS if call[0] == "probe"]
    assert len(probes) == 1 and len(probes[0][1]) == 1


def adapter(engine, monkeypatch):
    monkeypatch.setenv(patch.ENV_VAR, ONNX_FILE)
    return initialized(engine)._model


def test_scores_come_back_flat_and_in_input_order_across_batches(engine, monkeypatch, fakes):
    model = adapter(engine, monkeypatch)
    fakes.sessions[0].feeds.clear()
    pairs = [("q" * length, "doc") for length in (3, 1, 5, 2, 4)]
    scores = model.predict(pairs, batch_size=2, show_progress_bar=False)
    assert scores.tolist() == [3.0, 1.0, 5.0, 2.0, 4.0]
    assert [len(feed["input_ids"]) for feed in fakes.sessions[0].feeds] == [2, 2, 1]


def test_only_declared_inputs_are_fed_and_as_int64(engine, monkeypatch, fakes):
    model = adapter(engine, monkeypatch)
    fakes.sessions[0].feeds.clear()
    model.predict([("query", "doc")], batch_size=8)
    feed = fakes.sessions[0].feeds[0]
    assert set(feed) == {"input_ids", "attention_mask"}
    assert {array.dtype for array in feed.values()} == {np.dtype("int64")}


def test_no_pairs_give_no_scores(engine, monkeypatch):
    assert len(adapter(engine, monkeypatch).predict([], batch_size=8)) == 0


def test_session_keeps_memory_and_threads_within_the_measured_limits(engine, monkeypatch, fakes):
    adapter(engine, monkeypatch)
    session = fakes.sessions[0]
    # Measured under the 1536m limit with these; the arena keeps peak allocations.
    assert session.options.enable_cpu_mem_arena is False
    assert session.options.intra_op_num_threads == 2
    assert session.options.inter_op_num_threads == 1
    assert session.providers == ["CPUExecutionProvider"]


def test_patch_is_idempotent():
    once = patch.patched_cross_encoder(CROSS_ENCODER)
    assert patch.patched_cross_encoder(once) == once


@pytest.mark.parametrize(
    "old, new",
    [
        ("self._model = CrossEncoder(", "self._model = Other("),
        ("        self._device_type = resolve_model_device_type(self._model)\n", ""),
    ],
)
def test_patch_refuses_an_unknown_file(old, new):
    assert old in CROSS_ENCODER
    with pytest.raises(patch.PatchError):
        patch.patched_cross_encoder(CROSS_ENCODER.replace(old, new))


def test_main_fails_on_an_unknown_file_and_installs_nothing(tmp_path):
    (tmp_path / "cross_encoder.py").write_text("class Other:\n    pass\n")
    with pytest.raises(patch.PatchError):
        patch.main(["reranker_onnx.py", str(tmp_path)])
    assert not (tmp_path / "onnx_cross_encoder.py").exists()


def test_main_reruns_cleanly_on_a_patched_directory(tmp_path):
    (tmp_path / "cross_encoder.py").write_text(CROSS_ENCODER)
    assert patch.main(["reranker_onnx.py", str(tmp_path)]) == 0
    first = (tmp_path / "cross_encoder.py").read_text()
    assert patch.main(["reranker_onnx.py", str(tmp_path)]) == 0
    assert (tmp_path / "cross_encoder.py").read_text() == first
    assert (tmp_path / "onnx_cross_encoder.py").read_text() == Path(patch.__file__).with_name(
        "onnx_cross_encoder.py"
    ).read_text()
