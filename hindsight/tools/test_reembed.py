"""Tests for hindsight/tools/reembed.py. They use a fake encoder: the real model only exists in the image."""

import asyncio
import json
import math

import pytest

import reembed


def row(**fields):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "text": "Факт",
        "fact_type": "world",
        "occurred_start": None,
        "occurred_end": None,
        "mentioned_at": None,
        "entities": [],
    }
    return {**base, **fields}


class TestEmbeddingText:
    def test_fact_without_dates_or_entities_is_its_text(self):
        assert reembed.embedding_text(row()) == "Факт"

    def test_occurred_start_gives_month_and_year(self):
        text = reembed.embedding_text(row(occurred_start="2026-06-15T10:00:00+00:00"))
        assert text == "Факт (happened in June 2026)"

    def test_mentioned_at_is_the_fallback_date(self):
        text = reembed.embedding_text(row(mentioned_at="2026-03-01T00:00:00+00:00"))
        assert text == "Факт (happened in March 2026)"

    def test_occurred_start_wins_over_mentioned_at(self):
        text = reembed.embedding_text(
            row(occurred_start="2026-06-15T10:00:00+00:00", mentioned_at="2026-03-01T00:00:00+00:00")
        )
        assert text == "Факт (happened in June 2026)"

    def test_different_end_gives_a_range(self):
        text = reembed.embedding_text(
            row(occurred_start="2026-06-15T10:00:00+00:00", occurred_end="2026-08-01T10:00:00+00:00")
        )
        assert text == "Факт (happened from June 2026 to August 2026)"

    def test_end_equal_to_start_is_not_a_range(self):
        text = reembed.embedding_text(
            row(occurred_start="2026-06-15T10:00:00+00:00", occurred_end="2026-06-15T10:00:00+00:00")
        )
        assert text == "Факт (happened in June 2026)"

    def test_entities_go_after_the_date(self):
        text = reembed.embedding_text(
            row(occurred_start="2026-06-15T10:00:00+00:00", entities=["Париж", "Рим"])
        )
        assert text == "Факт (happened in June 2026) [Париж, Рим]"

    def test_observation_is_only_its_text(self):
        text = reembed.embedding_text(
            row(fact_type="observation", occurred_start="2026-06-15T10:00:00+00:00", entities=["Париж"])
        )
        assert text == "Факт"


class TestFormatVector:
    def test_uses_pgvector_text_form(self):
        assert reembed.format_vector([0.5, -0.25], 2) == "[0.50000000,-0.25000000]"

    def test_refuses_wrong_dimension(self):
        with pytest.raises(ValueError, match="dimension"):
            reembed.format_vector([0.5], 2)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_refuses_non_finite(self, bad):
        with pytest.raises(ValueError, match="finite"):
            reembed.format_vector([0.5, bad], 2)


class FakeEncoder:
    """Returns a vector that encodes the text's length, so a mixed-up order shows in the output."""

    dimension = 2

    def __init__(self):
        self.batches = []

    async def encode_batch(self, texts):
        self.batches.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]


def write_rows(path, rows):
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows), encoding="utf-8")


class TestRun:
    def test_writes_one_line_per_row_in_input_order(self, tmp_path):
        source = tmp_path / "rows.jsonl"
        target = tmp_path / "vectors.tsv"
        write_rows(source, [row(id="a", text="x"), row(id="b", text="xxx")])

        asyncio.run(reembed.run(source, target, FakeEncoder()))

        assert target.read_text().splitlines() == [
            "a\t[1.00000000,1.00000000]",
            "b\t[3.00000000,1.00000000]",
        ]

    def test_encodes_the_augmented_text(self, tmp_path):
        source = tmp_path / "rows.jsonl"
        write_rows(source, [row(occurred_start="2026-06-15T10:00:00+00:00")])
        encoder = FakeEncoder()

        asyncio.run(reembed.run(source, tmp_path / "vectors.tsv", encoder))

        assert encoder.batches == [["Факт (happened in June 2026)"]]

    def test_splits_rows_into_batches(self, tmp_path):
        source = tmp_path / "rows.jsonl"
        write_rows(source, [row(id=str(number)) for number in range(reembed.BATCH_SIZE + 1)])
        encoder = FakeEncoder()

        asyncio.run(reembed.run(source, tmp_path / "vectors.tsv", encoder))

        assert [len(batch) for batch in encoder.batches] == [reembed.BATCH_SIZE, 1]

    def test_refuses_an_empty_source_and_writes_nothing(self, tmp_path):
        source = tmp_path / "rows.jsonl"
        source.write_text("")
        target = tmp_path / "vectors.tsv"

        with pytest.raises(ValueError, match="no rows"):
            asyncio.run(reembed.run(source, target, FakeEncoder()))

        assert not target.exists()

    def test_a_short_encoder_answer_writes_nothing(self, tmp_path):
        class ShortEncoder(FakeEncoder):
            async def encode_batch(self, texts):
                return []

        source = tmp_path / "rows.jsonl"
        write_rows(source, [row()])
        target = tmp_path / "vectors.tsv"

        with pytest.raises(ValueError, match="vectors"):
            asyncio.run(reembed.run(source, target, ShortEncoder()))

        assert not target.exists()
