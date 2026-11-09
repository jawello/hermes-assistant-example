"""Compute a vector for every memory unit with the embedding model named in the environment.

Reads the rows exported by ops/hindsight-change-embedding.sh as JSON lines and writes
"id<TAB>[vector]" lines for COPY. It runs in the hindsight image on a machine with memory to
spare and touches no database. The text it embeds is what upstream embeds: retain's
date and entity augmentation for facts, the plain text for observations.
"""

import asyncio
import json
import math
import sys
from datetime import datetime
from pathlib import Path

BATCH_SIZE = 64


def _readable(value: str) -> str:
    date = datetime.fromisoformat(value)
    return f"{date.strftime('%B')} {date.strftime('%Y')}"


def embedding_text(row: dict) -> str:
    text = row["text"]
    if row["fact_type"] == "observation":
        return text
    start, end = row["occurred_start"], row["occurred_end"]
    date = start or row["mentioned_at"]
    if date is not None:
        if end and end != start:
            text = f"{text} (happened from {_readable(date)} to {_readable(end)})"
        else:
            text = f"{text} (happened in {_readable(date)})"
    if row["entities"]:
        text = f"{text} [{', '.join(row['entities'])}]"
    return text


def format_vector(vector: list[float], dimension: int) -> str:
    if len(vector) != dimension:
        raise ValueError(f"expected dimension {dimension}, got {len(vector)}")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("vector has a non-finite value")
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


class ModelEncoder:
    def __init__(self, embeddings, generate_batch):
        self._embeddings = embeddings
        self._generate_batch = generate_batch

    @property
    def dimension(self) -> int:
        return self._embeddings.dimension

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return await self._generate_batch(self._embeddings, texts)


async def run(source: Path, target: Path, encoder) -> None:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    if not rows:
        raise ValueError("no rows to embed")
    lines = []
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        vectors = await encoder.encode_batch([embedding_text(row) for row in batch])
        if len(vectors) != len(batch):
            raise ValueError(f"asked for {len(batch)} vectors, got {len(vectors)}")
        lines += [f"{row['id']}\t{format_vector(vector, encoder.dimension)}\n" for row, vector in zip(batch, vectors)]
    target.write_text("".join(lines), encoding="utf-8")


async def main(argv: list[str]) -> None:
    from hindsight_api.engine.embeddings import create_embeddings_from_env
    from hindsight_api.engine.retain.embedding_utils import generate_embeddings_batch

    embeddings = create_embeddings_from_env()
    await embeddings.initialize()
    await run(Path(argv[1]), Path(argv[2]), ModelEncoder(embeddings, generate_embeddings_batch))


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
