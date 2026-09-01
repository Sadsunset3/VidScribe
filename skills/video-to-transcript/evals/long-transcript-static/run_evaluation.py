#!/usr/bin/env python3
"""Reproduce the static long-transcript workflow evaluation with stdlib only."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "simulated-one-hour-transcript.md"
EVIDENCE = ROOT / "evidence" / "workflow.json"
MERGED_OUTPUT = ROOT / "output" / "merged-technical.md"


def _chunk_texts(fixture: str, chunks: list[dict[str, object]]) -> list[str]:
    texts: list[str] = []
    cursor = 0
    for index, chunk in enumerate(chunks):
        marker = chunk.get("end_after")
        if index == len(chunks) - 1:
            end = len(fixture)
        else:
            if not isinstance(marker, str) or not marker:
                raise ValueError(f"chunk {chunk.get('id')} has no end_after marker")
            marker_start = fixture.find(marker, cursor)
            if marker_start < 0:
                raise ValueError(f"chunk marker not found: {marker}")
            end = marker_start + len(marker)
        texts.append(fixture[cursor:end])
        cursor = end
    return texts


def evaluate() -> dict[str, object]:
    fixture = FIXTURE.read_text(encoding="utf-8")
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    merged = MERGED_OUTPUT.read_text(encoding="utf-8")
    chunks = evidence["chunks"]
    chunk_texts = _chunk_texts(fixture, chunks)
    block_lookup = {
        chunk["id"]: text for chunk, text in zip(chunks, chunk_texts, strict=True)
    }

    usable_context = int(evidence["usable_context_characters"])
    target_block_max = min(8000, usable_context // 3)
    source_coverage_exact = "".join(chunk_texts) == fixture
    chunk_lengths_bounded = all(
        0 < len(text) <= target_block_max for text in chunk_texts
    )

    card_ids: list[str] = []
    cards_complete = True
    required_state = {
        "current_topic", "current_step", "unexplained_terms", "unresolved_questions"
    }
    for chunk, text in zip(chunks, chunk_texts, strict=True):
        state = chunk.get("theme_state", {})
        cards = chunk.get("fact_cards", [])
        if set(state) != required_state or not cards:
            cards_complete = False
        for card in cards:
            card_ids.append(card["id"])
            if (
                not card.get("field")
                or not card.get("source_paragraph")
                or fixture.count(card["source_quote"]) != 1
                or card["source_quote"] not in text
                or merged.count(card["merged_text"]) != 1
            ):
                cards_complete = False

    fence_counts = [len(re.findall(r"(?m)^```", text)) for text in chunk_texts]
    fenced_code_not_split = sum(fence_counts) >= 2 and all(
        count % 2 == 0 for count in fence_counts
    )

    steps = evidence["steps"]
    step_positions: list[int] = []
    step_blocks: list[str] = []
    steps_connected = True
    for index, step in enumerate(steps):
        expected_dependencies = [] if index == 0 else [steps[index - 1]["id"]]
        block_text = block_lookup[step["source_block"]]
        position = merged.find(step["merged_text"])
        if (
            step["depends_on"] != expected_dependencies
            or fixture.count(step["source_quote"]) != 1
            or step["source_quote"] not in block_text
            or merged.count(step["merged_text"]) != 1
            or position < 0
        ):
            steps_connected = False
        step_positions.append(position)
        step_blocks.append(step["source_block"])
    cross_block_steps = len(set(step_blocks)) >= 2
    steps_ordered = step_positions == sorted(step_positions)

    expected_fact_ids = evidence["unique_fact_ids"]
    unique_facts_once = (
        len(card_ids) == len(set(card_ids))
        and set(card_ids) == set(expected_fact_ids)
        and cards_complete
    )

    invariants = {
        "source_coverage_exact": source_coverage_exact,
        "chunk_fact_card_workflow_complete": (
            chunk_lengths_bounded and cards_complete
        ),
        "fenced_code_not_split": fenced_code_not_split,
        "cross_block_steps_ordered_and_connected": (
            steps_connected and steps_ordered and cross_block_steps
        ),
        "unique_source_facts_exactly_once_after_merge": unique_facts_once,
    }
    return {
        "evaluation_kind": evidence["evaluation_kind"],
        "model_execution_claimed": evidence["model_execution_claimed"],
        "fixture_characters": len(fixture),
        "block_count": len(chunks),
        "chunk_characters": [len(text) for text in chunk_texts],
        "target_block_max_characters": target_block_max,
        "fact_card_count": len(card_ids),
        "step_count": len(steps),
        "invariants": invariants,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate()
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(report["invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
