"""An int8 ONNX cross-encoder with the part of the CrossEncoder API the reranker uses.

`reranker_onnx.py` installs this file next to `hindsight_api/engine/cross_encoder.py`.
It is imported only when `HINDSIGHT_RERANKER_ONNX_FILE` is set.

Design: design notes not published
"""
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer


class OnnxCrossEncoder:
    def __init__(self, model_name: str, file_name: str):
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        # The arena keeps peak allocations for the life of the process, and the
        # container runs close to its memory limit.
        options.enable_cpu_mem_arena = False
        self._session = ort.InferenceSession(
            hf_hub_download(model_name, file_name), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {item.name for item in self._session.get_inputs()}

    def predict(self, pairs, batch_size: int = 32, show_progress_bar: bool = False):
        """Score (query, document) pairs; the result is a flat array in input order."""
        scores = []
        for start in range(0, len(pairs), batch_size):
            batch = pairs[start : start + batch_size]
            encoded = self._tokenizer(
                [pair[0] for pair in batch],
                [pair[1] for pair in batch],
                padding=True,
                truncation=True,
                return_tensors="np",
            )
            feed = {name: array.astype("int64") for name, array in encoded.items() if name in self._inputs}
            scores.append(self._session.run(None, feed)[0].reshape(-1))
        return np.concatenate(scores) if scores else np.array([])
