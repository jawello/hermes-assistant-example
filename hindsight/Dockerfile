FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

# Dedicated Hindsight memory service (knowledge graph + embedded PostgreSQL).
# Runs as a NON-ROOT user because Hindsight's embedded PostgreSQL (pg0) runs
# `initdb`, which refuses to run as root. hermes talks to this over an internal
# network in `local_external` mode; this container is never exposed to the host.
#
# hindsight-all is pinned in lockstep with the hermes plugin's client
# (hermes/Dockerfile): the plugin needs a >=0.10.1 API, and an unpinned
# rebuild must not move the server, whose start-up runs DB migrations, on its
# own. cryptography follows hermes' pin (hindsight-api-slim needs >=50).

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# torch from the CPU index first: the PyPI wheel drags in ~4 GB of CUDA and
# triton this GPU-less VPS never uses, and an 11 GB image ran the host out of
# disk on deploy (2026-10-05). hindsight-all then finds torch satisfied.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1" \
    && pip install --no-cache-dir "hindsight-all==0.10.2" "cryptography==50.0.0"

# The default reranker splits Cyrillic into single letters, and the full multilingual
# model is OOM-killed at load under the 1536m limit, so the local provider gets a way
# to load an int8 ONNX file. The patch fails the build on a shape change, and the
# imports fail it if a dependency of hindsight-all stops bringing them.
COPY hindsight/patches/reranker_onnx.py hindsight/patches/onnx_cross_encoder.py /tmp/reranker_patch/
RUN python /tmp/reranker_patch/reranker_onnx.py \
        "$(python -c "import importlib.util as u; print(u.find_spec('hindsight_api').submodule_search_locations[0])")/engine" \
    && rm -rf /tmp/reranker_patch \
    && python -c "import numpy, onnxruntime, huggingface_hub, transformers"

RUN useradd -m -u 1000 hs

COPY hindsight/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

USER hs
# TIKTOKEN_CACHE_DIR on the volume so the encoding is fetched once (first boot)
# and reused; the HF embeddings model caches under ~/.cache (also the volume).
ENV HOME=/home/hs \
    HINDSIGHT_API_HOST=0.0.0.0 \
    HINDSIGHT_API_PORT=8888 \
    TIKTOKEN_CACHE_DIR=/home/hs/.cache/tiktoken \
    PYTHONUNBUFFERED=1

# The daemon's data (bundled PostgreSQL under ~/.pg0 + graph state under
# ~/.hindsight) persists on a volume mounted at /home/hs.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
