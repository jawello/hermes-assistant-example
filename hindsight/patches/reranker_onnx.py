#!/usr/bin/env python3
"""Let Hindsight's local reranker load an int8 ONNX file.

**The bug.** The default reranker is English: its tokenizer splits Cyrillic into
single letters, so Russian recall ranks badly and slowly. Of 30 questions on a copy
of the bank, 4 had the answer first, at 7.5 s per recall (2026-11-03). The full
multilingual model is OOM-killed at load under the 1536m limit, and the local
provider has no way to load its int8 ONNX file.

**The fix.** `LocalSTCrossEncoder.initialize` loads `OnnxCrossEncoder` when
`HINDSIGHT_RERANKER_ONNX_FILE` names a file in the model's repo. With the variable
unset the upstream path runs. The same questions then had 24 answers first, at
1.9 s, within the limit.

The patch fails the build if `initialize` changes shape, so an unpatched image
cannot ship quietly.

Design: design notes not published

Usage: `python reranker_onnx.py <hindsight_api/engine dir>`
"""
import shutil
import sys
from pathlib import Path

ENV_VAR = "HINDSIGHT_RERANKER_ONNX_FILE"
ADAPTER = "onnx_cross_encoder.py"


class PatchError(Exception):
    """The target file is not the one this patch was written against."""


UPSTREAM_LOAD = (
    "                self._model = CrossEncoder(\n"
    "                    self.model_name,\n"
    "                    device=device,\n"
    '                    model_kwargs={"low_cpu_mem_usage": False},\n'
    "                    trust_remote_code=self.trust_remote_code,\n"
    "                )\n"
)
PATCHED_LOAD = (
    "                # Int8 ONNX when the variable names a file in the model's repo\n"
    "                # (patched in by personal-assistant's reranker_onnx.py).\n"
    "                import os\n"
    "\n"
    f'                onnx_file = os.environ.get("{ENV_VAR}")\n'
    "                if onnx_file:\n"
    "                    from .onnx_cross_encoder import OnnxCrossEncoder\n"
    "\n"
    "                    self._model = OnnxCrossEncoder(self.model_name, onnx_file)\n"
    "                else:\n"
    "                    self._model = CrossEncoder(\n"
    "                        self.model_name,\n"
    "                        device=device,\n"
    '                        model_kwargs={"low_cpu_mem_usage": False},\n'
    "                        trust_remote_code=self.trust_remote_code,\n"
    "                    )\n"
)
UPSTREAM_DEVICE = (
    '        align_local_model_weights(self._model.model, label=f"Reranker[{self.model_name}]")\n'
    "\n"
    "        self._device_type = resolve_model_device_type(self._model)\n"
)
PATCHED_DEVICE = (
    "        if onnx_file:\n"
    '            self._device_type = "cpu"\n'
    "        else:\n"
    '            align_local_model_weights(self._model.model, label=f"Reranker[{self.model_name}]")\n'
    "\n"
    "            self._device_type = resolve_model_device_type(self._model)\n"
)


def patched_cross_encoder(source: str) -> str:
    """cross_encoder.py whose local reranker can load an ONNX file."""
    if PATCHED_LOAD in source and PATCHED_DEVICE in source:
        return source
    for upstream in (UPSTREAM_LOAD, UPSTREAM_DEVICE):
        found = source.count(upstream)
        if found != 1:
            raise PatchError(
                "expected exactly one of each patched block in "
                f"hindsight_api/engine/cross_encoder.py, found {found} of:\n{upstream}"
                "upstream changed LocalSTCrossEncoder.initialize; re-check this patch"
            )
    return source.replace(UPSTREAM_LOAD, PATCHED_LOAD, 1).replace(UPSTREAM_DEVICE, PATCHED_DEVICE, 1)


def main(argv: list[str]) -> int:
    engine = Path(argv[1])
    target = engine / "cross_encoder.py"
    source = target.read_text()
    patched = patched_cross_encoder(source)
    shutil.copyfile(Path(__file__).with_name(ADAPTER), engine / ADAPTER)
    if patched == source:
        print(f"{target}: already patched")
    else:
        target.write_text(patched)
        print(f"{target}: the local reranker can now load an ONNX file")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
