#!/usr/bin/env python3
"""Download or read media and transcribe its speech with Paraformer-v2."""

from __future__ import annotations

import argparse
import bisect
import http.client
import importlib.util
import json
import math
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True)
class Source:
    kind: str
    value: str
    path: Path | None
    created_by_run: bool


@dataclass(frozen=True)
class ProbeInfo:
    has_audio: bool
    has_video: bool
    duration_seconds: float


@dataclass(frozen=True)
class AsrConfig:
    api_key: str
    model: str
    upload_url: str
    asr_url: str
    task_url_template: str
    poll_interval_seconds: float
    timeout_seconds: float


@dataclass(frozen=True)
class TaskState:
    status: str
    transcription_url: str | None
    message: str | None = None


@dataclass(frozen=True)
class Transcript:
    text: str
    segments: list[dict[str, Any]]


@dataclass(frozen=True)
class MediaArtifact:
    path: Path
    created_by_run: bool


@dataclass(frozen=True)
class OutputPaths:
    markdown: Path
    normalized_json: Path
    raw_json: Path
    words_jsonl: Path | None = None
    segment_plan_json: Path | None = None

    def existing(self) -> list[Path]:
        """Return the paths that were actually written by this run.

        The word timeline and chunk plan are conditional artifacts, so cleanup
        and reporting must only consider the files this run really created.
        """
        return [
            path
            for path in (
                self.markdown,
                self.normalized_json,
                self.raw_json,
                self.words_jsonl,
                self.segment_plan_json,
            )
            if path is not None and path.is_file()
        ]


@dataclass(frozen=True)
class AsrResult:
    transcript: Transcript
    raw_payload: dict[str, Any]
    task_id: str


@dataclass(frozen=True)
class PipelineResult:
    outputs: OutputPaths
    deleted_media: list[Path]
    work_dir: Path


class Transport(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def post_multipart(
        self,
        url: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
    ) -> None: ...


class MediaError(RuntimeError):
    """Raised when downloaded or local media cannot be processed."""


class TranscriptionError(RuntimeError):
    """Raised when the ASR task fails or returns unusable output."""


class CleanupError(RuntimeError):
    """Raised when the success cleanup safety invariant is not satisfied."""


class PipelineError(RuntimeError):
    def __init__(self, message: str, work_dir: Path):
        self.work_dir = work_dir
        super().__init__(f"{message}; run artifacts retained at: {work_dir}")


def is_supported_url(value: str) -> bool:
    """Return whether *value* is an HTTP(S) URL that yt-dlp may handle."""
    return urlparse(value).scheme.lower() in {"http", "https"}


def classify_source(value: str) -> Source:
    """Classify a URL or local path without mutating it."""
    if is_supported_url(value):
        return Source("url", value, None, True)
    path = Path(value).expanduser()
    return Source("local", value, path, False)


def build_ytdlp_command(
    url: str,
    work_dir: Path,
    command_prefix: list[str] | None = None,
) -> list[str]:
    """Build a safe argv that prefers a single best-audio format."""
    output_template = str(work_dir / "source-%(id)s.%(ext)s")
    return [
        *(command_prefix or ["yt-dlp"]),
        "--no-playlist",
        "--format",
        "bestaudio/worst",
        "--output",
        output_template,
        "--print",
        "after_move:filepath",
        url,
    ]


def build_ffprobe_command(path: Path) -> list[str]:
    return [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]


def parse_probe(raw_json: str) -> ProbeInfo:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") or []
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    has_video = any(s.get("codec_type") == "video" for s in streams)
    if not audio_streams:
        raise MediaError("media has no audio stream")
    duration_value = audio_streams[0].get("duration")
    if duration_value is None:
        duration_value = (payload.get("format") or {}).get("duration")
    try:
        duration = float(duration_value)
    except (TypeError, ValueError) as exc:
        raise MediaError("audio stream has no valid duration") from exc
    if duration <= 0:
        raise MediaError("audio stream duration must be positive")
    return ProbeInfo(has_audio=True, has_video=has_video, duration_seconds=duration)


def build_ffmpeg_command(source: Path, target: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(target),
    ]


def build_submit_request(
    config: AsrConfig, oss_uri: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
        "X-DashScope-OssResourceResolve": "enable",
    }
    body = {
        "model": config.model,
        "input": {"file_urls": [oss_uri]},
        "parameters": {
            "language_hints": ["zh", "en"],
            "timestamp_alignment_enabled": False,
            "disfluency_removal_enabled": False,
        },
    }
    return config.asr_url, headers, body


def build_upload_form(
    policy: dict[str, Any], audio_path: Path
) -> tuple[str, dict[str, str], str]:
    required = {
        "upload_dir",
        "upload_host",
        "oss_access_key_id",
        "policy",
        "signature",
    }
    missing = sorted(required.difference(policy))
    if missing:
        raise TranscriptionError(
            "upload policy is missing fields: " + ", ".join(missing)
        )
    object_name = f"{uuid4().hex}{audio_path.suffix.lower() or '.mp3'}"
    object_key = f"{str(policy['upload_dir']).rstrip('/')}/{object_name}"
    fields = {
        "OSSAccessKeyId": str(policy["oss_access_key_id"]),
        "Signature": str(policy["signature"]),
        "policy": str(policy["policy"]),
        "key": object_key,
        "x-oss-object-acl": "private",
        "x-oss-forbid-overwrite": "true",
        "success_action_status": "200",
    }
    return str(policy["upload_host"]), fields, f"oss://{object_key}"


def parse_task_state(payload: dict[str, Any]) -> TaskState:
    output = payload.get("output") or {}
    status = str(output.get("task_status") or "UNKNOWN").upper()
    transcription_url = None
    for result in output.get("results") or []:
        if result.get("transcription_url"):
            transcription_url = str(result["transcription_url"])
            break
    message = output.get("message") or payload.get("message")
    return TaskState(status, transcription_url, str(message) if message else None)


def _coerce_non_negative_int(value: Any, field: str, index: int) -> int:
    """Return *value* as a non-negative int, rejecting bools and non-integers."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise TranscriptionError(
            f"sentence {index} has a non-numeric {field}: {value!r}"
        )
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptionError(
            f"sentence {index} has a non-numeric {field}: {value!r}"
        ) from exc
    if number < 0:
        raise TranscriptionError(f"sentence {index} has a negative {field}: {number}")
    return number


def _squash_whitespace(text: str) -> str:
    return "".join(text.split())


def validate_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the sentence-timeline invariants and return *segments* unchanged.

    A malformed timeline is a hard failure rather than a silent downgrade:
    downstream consumers (watch-along seeking, chunk planning) cannot function
    on a timeline they cannot trust.
    """
    if not segments:
        raise TranscriptionError("transcription result has no timestamped sentences")
    previous_end: int | None = None
    for index, segment in enumerate(segments, start=1):
        start = _coerce_non_negative_int(segment.get("start_ms"), "start_ms", index)
        end = _coerce_non_negative_int(segment.get("end_ms"), "end_ms", index)
        if end < start:
            raise TranscriptionError(
                f"sentence {index} ends before it starts: {start} > {end}"
            )
        if previous_end is not None and start < previous_end:
            raise TranscriptionError(
                f"sentence {index} overlaps the previous one: {start} < {previous_end}"
            )
        if not _squash_whitespace(str(segment.get("text") or "")):
            raise TranscriptionError(f"sentence {index} has empty text")
        previous_end = end
    return segments


def extract_words(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten sentence-level `words` into a validated word timeline.

    Each word keeps its owning sentence index so downstream consumers can map a
    word back to its sentence without re-walking the raw payload.
    """
    words: list[dict[str, Any]] = []
    for sentence_index, segment in enumerate(segments):
        raw_words = segment.get("words")
        if not isinstance(raw_words, list):
            continue
        previous_end: int | None = None
        for word_index, word in enumerate(raw_words, start=1):
            if not isinstance(word, dict):
                raise TranscriptionError(
                    f"sentence {sentence_index + 1} word {word_index} is not an object"
                )
            start = _coerce_non_negative_int(
                word.get("begin_time"), "begin_time", sentence_index + 1
            )
            end = _coerce_non_negative_int(
                word.get("end_time"), "end_time", sentence_index + 1
            )
            if end < start:
                raise TranscriptionError(
                    f"sentence {sentence_index + 1} word {word_index} "
                    f"ends before it starts: {start} > {end}"
                )
            if start < int(segment["start_ms"]) or end > int(segment["end_ms"]):
                raise TranscriptionError(
                    f"sentence {sentence_index + 1} word {word_index} "
                    f"falls outside its sentence: [{start}, {end}] not within "
                    f"[{segment['start_ms']}, {segment['end_ms']}]"
                )
            if previous_end is not None and start < previous_end:
                raise TranscriptionError(
                    f"sentence {sentence_index + 1} word {word_index} "
                    f"overlaps the previous word: {start} < {previous_end}"
                )
            previous_end = end
            words.append(
                {
                    "start_ms": start,
                    "end_ms": end,
                    "text": str(word.get("text") or ""),
                    "punctuation": str(word.get("punctuation") or ""),
                    "sentence_index": sentence_index,
                }
            )
    return words


def normalize_transcription(payload: dict[str, Any]) -> Transcript:
    text_parts: list[str] = []
    segments: list[dict[str, Any]] = []
    for transcript in payload.get("transcripts") or []:
        text = str(transcript.get("text") or "").strip()
        if text:
            text_parts.append(text)
        for sentence in transcript.get("sentences") or []:
            sentence_text = str(sentence.get("text") or "").strip()
            if not sentence_text:
                continue
            segments.append(
                {
                    "start_ms": int(sentence.get("begin_time") or 0),
                    "end_ms": int(sentence.get("end_time") or 0),
                    "text": sentence_text,
                    "words": sentence.get("words"),
                }
            )
    full_text = "\n".join(text_parts).strip()
    if not full_text and segments:
        full_text = "".join(segment["text"] for segment in segments).strip()
    if not full_text:
        raise TranscriptionError("transcription result is empty")
    if segments:
        validate_segments(segments)
        joined = "".join(str(segment["text"]) for segment in segments)
        if _squash_whitespace(joined) != _squash_whitespace(full_text):
            raise TranscriptionError(
                "sentence timeline text does not match the transcript text"
            )
    return Transcript(full_text, segments)


def estimate_tokens(text: str) -> int:
    """Conservatively estimate tokens, preferring CJK-accurate counting.

    The real tokenizer is unavailable in the CLI, so this over-estimates rather
    than under-estimates: a slice that is budgeted too large can overflow the
    model context, while one that is too small only costs an extra pass.
    """
    cjk = sum(1 for character in text if "一" <= character <= "鿿")
    other = len(text) - cjk
    return cjk + max(1, math.ceil(other / 3))


def plan_sentence_chunks(
    segments: list[dict[str, Any]],
    *,
    max_duration_ms: int = 3600 * 1000,
    max_tokens: int = 10000,
) -> dict[str, Any]:
    """Group sentences into chunks that never break a sentence apart.

    A target cut point that lands inside a sentence is pulled back to the end of
    the last sentence that fits. A single sentence longer than either budget is
    emitted alone rather than being truncated, because splitting mid-sentence is
    the exact failure this planner exists to prevent.
    """
    if not segments:
        raise TranscriptionError("cannot plan chunks without timestamped sentences")
    if max_duration_ms <= 0 or max_tokens <= 0:
        raise TranscriptionError("chunk budgets must be positive")

    chunks: list[dict[str, Any]] = []
    start_index = 0
    while start_index < len(segments):
        window_start = int(segments[start_index]["start_ms"])
        window_tokens = 0
        end_index = start_index
        last_fitting = start_index
        while end_index < len(segments):
            candidate_end = int(segments[end_index]["end_ms"])
            candidate_tokens = window_tokens + estimate_tokens(
                str(segments[end_index]["text"])
            )
            duration = candidate_end - window_start
            if duration > max_duration_ms or candidate_tokens > max_tokens:
                break
            last_fitting = end_index
            window_tokens = candidate_tokens
            end_index += 1
        # A sentence that busts the budget on its own still gets its own chunk.
        if end_index == start_index:
            end_index = start_index + 1
            last_fitting = start_index
        chunks.append(
            {
                "index": len(chunks) + 1,
                "start_ms": window_start,
                "end_ms": int(segments[last_fitting]["end_ms"]),
                "sentence_start": start_index,
                "sentence_end": last_fitting,
                "sentence_count": last_fitting - start_index + 1,
                "estimated_tokens": sum(
                    estimate_tokens(str(segments[i]["text"]))
                    for i in range(start_index, last_fitting + 1)
                ),
                "over_budget_sentence": last_fitting == start_index
                and (
                    int(segments[start_index]["end_ms"])
                    - int(segments[start_index]["start_ms"])
                    > max_duration_ms
                    or estimate_tokens(str(segments[start_index]["text"])) > max_tokens
                ),
            }
        )
        start_index = last_fitting + 1

    return {
        "strategy": "sentence-bounded",
        "used_sentence_boundaries": True,
        "max_duration_ms": max_duration_ms,
        "max_tokens": max_tokens,
        "sentence_count": len(segments),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def plan_proportional_chunks(
    text: str,
    *,
    max_duration_ms: int = 3600 * 1000,
    max_tokens: int = 10000,
) -> dict[str, Any]:
    """Fallback planner used when no usable sentence timeline exists."""
    if not text.strip():
        raise TranscriptionError("cannot plan chunks without transcript text")
    if max_duration_ms <= 0 or max_tokens <= 0:
        raise TranscriptionError("chunk budgets must be positive")
    total_tokens = estimate_tokens(text)
    count = max(1, math.ceil(total_tokens / max_tokens))
    size = math.ceil(len(text) / count)
    chunks = []
    cursor = 0
    for index in range(count):
        end = len(text) if index == count - 1 else min(len(text), cursor + size)
        piece = text[cursor:end]
        chunks.append(
            {
                "index": index + 1,
                "char_start": cursor,
                "char_end": end,
                "estimated_tokens": estimate_tokens(piece),
            }
        )
        cursor = end
    return {
        "strategy": "proportional",
        "used_sentence_boundaries": False,
        "max_duration_ms": max_duration_ms,
        "max_tokens": max_tokens,
        "estimated_tokens": total_tokens,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def build_segment_plan(
    transcript: Transcript,
    *,
    max_duration_ms: int = 3600 * 1000,
    max_tokens: int = 10000,
) -> dict[str, Any]:
    """Prefer sentence-bounded chunks and fall back to proportional ones."""
    if transcript.segments:
        try:
            validate_segments(transcript.segments)
        except TranscriptionError:
            return plan_proportional_chunks(
                transcript.text,
                max_duration_ms=max_duration_ms,
                max_tokens=max_tokens,
            )
        return plan_sentence_chunks(
            transcript.segments,
            max_duration_ms=max_duration_ms,
            max_tokens=max_tokens,
        )
    return plan_proportional_chunks(
        transcript.text, max_duration_ms=max_duration_ms, max_tokens=max_tokens
    )


@dataclass(frozen=True)
class SeekResult:
    """One sentence returned by a timestamp-anchored retrieval.

    The field names intentionally mirror the persisted segment shape
    (``start_ms``/``end_ms``/``text``) so a consumer can feed the result to
    word-level refinement or cross-sentence splicing without remapping.
    """

    index: int
    start_ms: int
    end_ms: int
    text: str
    word_refined: bool = False


def seek_by_point(
    segments: list[dict[str, Any]],
    point_ms: int,
    *,
    context_before: int = 0,
    context_after: int = 0,
    words: list[dict[str, Any]] | None = None,
) -> list[SeekResult]:
    """Return the sentence covering ``point_ms`` via binary search on segments.

    Segments are persisted sorted by ``begin_time``/``start_ms`` and validated
    non-overlapping, so a bisect on ``start_ms`` locates the containing
    sentence in O(log n). A point that falls in the gap between two sentences
    returns the sentence *after* the gap; a point before the first sentence or
    after the last one returns nothing rather than an out-of-range sentence.
    ``context_before``/``context_after`` widen the hit to adjacent sentences,
    clamped to the timeline boundaries.

    When ``words`` (a word timeline with ``sentence_index``) is supplied,
    sentence boundaries are narrowed to the real word edges and each returned
    sentence reports whether word-level refinement applied.
    """
    if point_ms < 0:
        raise TranscriptionError(f"seek time must be non-negative: {point_ms}")
    segments = validate_segments(segments)
    if not segments:
        raise TranscriptionError("cannot seek a transcript without timestamped sentences")
    starts = [int(segment["start_ms"]) for segment in segments]
    end_ms = int(segments[-1]["end_ms"])
    if point_ms > end_ms:
        return []
    hit = bisect.bisect_right(starts, point_ms) - 1
    if hit >= 0:
        segment = segments[hit]
        if point_ms >= int(segment["end_ms"]):
            # The point sits in the gap after ``segment``; return the next one.
            if hit + 1 < len(segments):
                hit += 1
            else:
                return []
    else:
        # The point precedes the first sentence; there is nothing to return.
        return []
    return _expand_hit(
        segments, hit, context_before, context_after, words=words
    )


def seek_by_range(
    segments: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    *,
    context_before: int = 0,
    context_after: int = 0,
    words: list[dict[str, Any]] | None = None,
) -> list[SeekResult]:
    """Return every sentence overlapping ``[start_ms, end_ms]``, in order.

    Overlap is inclusive of shared boundaries: a sentence whose ``end_ms``
    equals the range start or whose ``start_ms`` equals the range end counts.
    ``context_before``/``context_after`` widen the result by that many adjacent
    sentences on each side, clamped to the timeline. When the range does not
    overlap any sentence, the result is an empty list, not an error.

    When ``words`` (a word timeline with ``sentence_index``) is supplied,
    sentence boundaries are narrowed to the real word edges and each returned
    sentence reports whether word-level refinement applied.
    """
    if start_ms < 0 or end_ms < start_ms:
        raise TranscriptionError(
            f"invalid seek range: [{start_ms}, {end_ms}]"
        )
    segments = validate_segments(segments)
    if not segments:
        raise TranscriptionError("cannot seek a transcript without timestamped sentences")
    if end_ms < int(segments[0]["start_ms"]) or start_ms > int(segments[-1]["end_ms"]):
        return []
    starts = [int(segment["start_ms"]) for segment in segments]
    ends = [int(segment["end_ms"]) for segment in segments]
    # First sentence whose end reaches the range start; last whose start does
    # not pass the range end. Both are clamped so the later expansion reads
    # within bounds.
    first = bisect.bisect_left(ends, start_ms)
    last = bisect.bisect_right(starts, end_ms) - 1
    first = max(0, min(first, len(segments) - 1))
    last = max(first, min(last, len(segments) - 1))
    first = max(0, first - context_before)
    last = min(len(segments) - 1, last + context_after)
    return _expand_hit(segments, (first + last) // 2, 0, 0, words=words, span=(first, last))


def refine_boundaries_with_words(
    segments: list[dict[str, Any]], words: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Narrow each sentence's ``start_ms``/``end_ms`` to its real word edges.

    Word timelines carry ``sentence_index`` plus ``start_ms``/``end_ms`` per
    word, so the refined boundary is the first word's start and the last word's
    end for that sentence. Sentences with no word rows keep their sentence
    bounds. Returned segment dicts are new objects; the input lists are not
    mutated.
    """
    per_sentence: list[list[dict[str, Any]]] = [[] for _ in segments]
    for word in words:
        sentence_index = word.get("sentence_index")
        if isinstance(sentence_index, int) and 0 <= sentence_index < len(per_sentence):
            per_sentence[sentence_index].append(word)
    refined: list[dict[str, Any]] = []
    for segment, word_rows in zip(segments, per_sentence, strict=True):
        if word_rows:
            segment = dict(segment)
            segment["start_ms"] = int(word_rows[0]["start_ms"])
            segment["end_ms"] = int(word_rows[-1]["end_ms"])
        refined.append(segment)
    return refined


def _expand_hit(
    segments: list[dict[str, Any]],
    center: int,
    context_before: int,
    context_after: int,
    *,
    words: list[dict[str, Any]] | None = None,
    span: tuple[int, int] | None = None,
) -> list[SeekResult]:
    word_rows: dict[int, list[dict[str, Any]]] = {}
    if words:
        for word in words:
            sentence_index = word.get("sentence_index")
            if isinstance(sentence_index, int):
                word_rows.setdefault(sentence_index, []).append(word)
    if span is not None:
        first, last = span
    else:
        first = max(0, center - context_before)
        last = min(len(segments) - 1, center + context_after)
    results: list[SeekResult] = []
    for index, segment in enumerate(segments[first : last + 1], start=first):
        refined = bool(word_rows.get(index))
        start_ms = int(segment["start_ms"])
        end_ms = int(segment["end_ms"])
        if refined:
            rows = word_rows[index]
            start_ms = int(rows[0]["start_ms"])
            end_ms = int(rows[-1]["end_ms"])
        results.append(
            SeekResult(
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=str(segment["text"]),
                word_refined=refined,
            )
        )
    return results


def _atomic_write_text(path: Path, content: str) -> None:
    if not content.strip():
        raise TranscriptionError(f"refusing to persist empty output: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _public_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip the raw `words` payload from segments before persisting them.

    Word data lives in its own JSONL artifact, so keeping it inline would
    duplicate a large payload inside the file that every consumer reads.
    """
    return [
        {
            "start_ms": int(segment["start_ms"]),
            "end_ms": int(segment["end_ms"]),
            "text": str(segment["text"]),
        }
        for segment in segments
    ]


def persist_outputs(
    output_dir: Path,
    stem: str,
    transcript: Transcript,
    raw_payload: dict[str, Any],
    metadata: dict[str, Any],
    *,
    include_timestamps: bool = True,
    segment_plan: dict[str, Any] | None = None,
) -> OutputPaths:
    if not transcript.text.strip():
        raise TranscriptionError("refusing to persist an empty transcript")
    safe_stem = "".join(
        character if character.isalnum() or character in "-_." else "-"
        for character in stem
    ).strip("-.") or "transcript"
    paths = OutputPaths(
        output_dir / f"{safe_stem}.transcript.md",
        output_dir / f"{safe_stem}.transcript.json",
        output_dir / f"{safe_stem}.asr.raw.json",
        output_dir / f"{safe_stem}.words.jsonl",
        output_dir / f"{safe_stem}.segments.plan.json",
    )
    markdown_lines = [f"# {safe_stem}", "", transcript.text]
    normalized: dict[str, Any] = {
        "text": transcript.text,
        "metadata": metadata,
    }
    if include_timestamps and transcript.segments:
        normalized["segments"] = _public_segments(transcript.segments)
    _atomic_write_text(paths.markdown, "\n".join(markdown_lines) + "\n")
    _atomic_write_text(
        paths.normalized_json,
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(
        paths.raw_json,
        json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n",
    )
    if include_timestamps and transcript.segments:
        words = extract_words(transcript.segments)
        if words:
            _atomic_write_text(
                paths.words_jsonl,
                "".join(
                    json.dumps(word, ensure_ascii=False) + "\n" for word in words
                ),
            )
        else:
            paths = replace(paths, words_jsonl=None)
    else:
        paths = replace(paths, words_jsonl=None)
    if segment_plan is None:
        paths = replace(paths, segment_plan_json=None)
    else:
        _atomic_write_text(
            paths.segment_plan_json,
            json.dumps(segment_plan, ensure_ascii=False, indent=2) + "\n",
        )
    return paths


def _outputs_are_valid(paths: OutputPaths) -> bool:
    try:
        if any(
            not path.is_file() or path.stat().st_size <= 0
            for path in paths.existing()
        ):
            return False
        normalized = json.loads(paths.normalized_json.read_text(encoding="utf-8"))
        return bool(str(normalized.get("text") or "").strip())
    except (OSError, json.JSONDecodeError):
        return False


def cleanup_created_media(
    artifacts: list[MediaArtifact],
    work_dir: Path,
    outputs: OutputPaths,
    *,
    keep_media: bool = False,
) -> list[Path]:
    if keep_media:
        return []
    if not _outputs_are_valid(outputs):
        raise CleanupError("transcript outputs are not validated; media was retained")
    resolved_work_dir = work_dir.resolve()
    candidates: list[tuple[Path, Path]] = []
    for artifact in artifacts:
        if not artifact.created_by_run:
            continue
        resolved = artifact.path.resolve()
        try:
            resolved.relative_to(resolved_work_dir)
        except ValueError as exc:
            raise CleanupError(
                f"refusing to delete path outside private work directory: {artifact.path}"
            ) from exc
        candidates.append((artifact.path, resolved))
    deleted: list[Path] = []
    for original, resolved in candidates:
        if resolved.exists():
            if not resolved.is_file():
                raise CleanupError(f"refusing to delete non-file media path: {original}")
            resolved.unlink()
            deleted.append(original)
    return deleted


class ParaformerGateway:
    def __init__(
        self,
        config: AsrConfig,
        *,
        transport: Transport,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.transport = transport
        self.sleeper = sleeper
        self.clock = clock

    @property
    def authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def upload_audio(self, audio_path: Path) -> str:
        query = urlencode({"action": "getPolicy", "model": self.config.model})
        separator = "&" if "?" in self.config.upload_url else "?"
        policy_payload = self.transport.request_json(
            "GET",
            self.config.upload_url + separator + query,
            self.authorization_headers,
        )
        policy = policy_payload.get("data")
        if not isinstance(policy, dict):
            raise TranscriptionError("temporary upload policy response has no data")
        upload_host, fields, oss_uri = build_upload_form(policy, audio_path)
        self.transport.post_multipart(upload_host, fields, "file", audio_path)
        return oss_uri

    def submit(self, oss_uri: str) -> str:
        url, headers, body = build_submit_request(self.config, oss_uri)
        payload = self.transport.request_json("POST", url, headers, body)
        output = payload.get("output") or {}
        task_id = str(output.get("task_id") or "").strip()
        if not task_id:
            message = payload.get("message") or "provider returned no task_id"
            raise TranscriptionError(str(message))
        return task_id

    def wait_for_result_url(self, task_id: str) -> str:
        deadline = self.clock() + self.config.timeout_seconds
        task_url = self.config.task_url_template.format(task_id=task_id)
        while self.clock() <= deadline:
            payload = self.transport.request_json(
                "GET", task_url, self.authorization_headers
            )
            state = parse_task_state(payload)
            if state.status == "SUCCEEDED":
                if state.transcription_url:
                    return state.transcription_url
                raise TranscriptionError(
                    "task succeeded but returned no transcription_url"
                )
            if state.status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                detail = state.message or f"task ended with status {state.status}"
                raise TranscriptionError(detail)
            self.sleeper(self.config.poll_interval_seconds)
        raise TranscriptionError(
            f"transcription timed out after {self.config.timeout_seconds:g} seconds"
        )

    def transcribe(self, audio_path: Path) -> AsrResult:
        if not audio_path.is_file() or audio_path.stat().st_size <= 0:
            raise TranscriptionError(f"audio file is missing or empty: {audio_path}")
        oss_uri = self.upload_audio(audio_path)
        task_id = self.submit(oss_uri)
        result_url = self.wait_for_result_url(task_id)
        raw_payload = self.transport.request_json("GET", result_url)
        transcript = normalize_transcription(raw_payload)
        return AsrResult(transcript, raw_payload, task_id)


class UrllibTransport:
    """Small standard-library HTTP transport, including streamed OSS upload."""

    def __init__(self, timeout_seconds: float = 120) -> None:
        self.timeout_seconds = timeout_seconds

    def request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        request_headers = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        request = Request(url, data=data, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise TranscriptionError(
                f"HTTP {exc.code} from transcription service: {detail}"
            ) from exc
        except URLError as exc:
            raise TranscriptionError(
                f"cannot reach transcription service: {exc.reason}"
            ) from exc
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TranscriptionError("transcription service returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise TranscriptionError("transcription service returned non-object JSON")
        return value

    def post_multipart(
        self,
        url: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
    ) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise TranscriptionError(f"invalid upload host: {url}")
        boundary = "----video-to-transcript-" + uuid4().hex
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        safe_filename = file_path.name.replace('"', "-")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{safe_filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        prefix = b"".join(parts)
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        content_length = len(prefix) + file_path.stat().st_size + len(suffix)
        connection_type = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_type(
            parsed.hostname, parsed.port, timeout=self.timeout_seconds
        )
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        try:
            connection.putrequest("POST", target)
            connection.putheader(
                "Content-Type", f"multipart/form-data; boundary={boundary}"
            )
            connection.putheader("Content-Length", str(content_length))
            connection.endheaders()
            connection.send(prefix)
            with file_path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    connection.send(chunk)
            connection.send(suffix)
            response = connection.getresponse()
            response_body = response.read()
            if not 200 <= response.status < 300:
                detail = response_body.decode("utf-8", errors="replace")[:1000]
                raise TranscriptionError(
                    f"temporary audio upload failed with HTTP {response.status}: {detail}"
                )
        except OSError as exc:
            raise TranscriptionError(f"temporary audio upload failed: {exc}") from exc
        finally:
            connection.close()


def load_asr_config(
    environment: dict[str, str] | os._Environ[str] | None = None,
) -> AsrConfig:
    env = os.environ if environment is None else environment
    api_key = str(env.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise TranscriptionError("DASHSCOPE_API_KEY is not set")

    def number(name: str, default: str) -> float:
        try:
            value = float(env.get(name, default))
        except ValueError as exc:
            raise TranscriptionError(f"{name} must be numeric") from exc
        if value <= 0:
            raise TranscriptionError(f"{name} must be positive")
        return value

    return AsrConfig(
        api_key=api_key,
        model=str(env.get("DASHSCOPE_MODEL") or "paraformer-v2"),
        upload_url=str(
            env.get("DASHSCOPE_UPLOAD_URL")
            or "https://dashscope.aliyuncs.com/api/v1/uploads"
        ),
        asr_url=str(
            env.get("DASHSCOPE_ASR_URL")
            or "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
        ),
        task_url_template=str(
            env.get("DASHSCOPE_TASK_URL_TEMPLATE")
            or "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        ),
        poll_interval_seconds=number("DASHSCOPE_POLL_INTERVAL", "5"),
        timeout_seconds=number("DASHSCOPE_TIMEOUT", "14400"),
    )


def run_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise MediaError(f"required program is not installed: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "unknown command failure").strip()
        raise MediaError(f"{command[0]} failed: {detail[-2000:]}") from exc
    return completed.stdout


def require_programs(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise MediaError("missing required programs: " + ", ".join(missing))


def resolve_ytdlp_command_prefix(
    executable_finder: Callable[[str], str | None] = shutil.which,
    module_finder: Callable[[str], Any] = importlib.util.find_spec,
) -> list[str]:
    """Resolve yt-dlp from PATH or from the current Python environment."""
    executable = executable_finder("yt-dlp")
    if executable:
        return [executable]
    try:
        module = module_finder("yt_dlp")
    except (ImportError, AttributeError, ValueError):
        module = None
    if module is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise MediaError(
        "missing required program: yt-dlp; install it with "
        "'python -m pip install --upgrade yt-dlp' or add yt-dlp to PATH"
    )


def _downloaded_path(stdout: str, work_dir: Path) -> Path:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise MediaError("yt-dlp did not report a downloaded file path")
    path = Path(lines[-1]).expanduser().resolve()
    try:
        path.relative_to(work_dir.resolve())
    except ValueError as exc:
        raise MediaError("yt-dlp reported a path outside the private work directory") from exc
    if not path.is_file() or path.stat().st_size <= 0:
        raise MediaError(f"downloaded media is missing or empty: {path}")
    return path


def _choose_output_stem(output_dir: Path, preferred: str) -> str:
    clean = "".join(
        character if character.isalnum() or character in "-_." else "-"
        for character in preferred
    ).strip("-.") or "transcript"
    candidate = clean
    suffix = 2
    while any(
        (output_dir / f"{candidate}{ending}").exists()
        for ending in (
            ".transcript.md",
            ".transcript.json",
            ".asr.raw.json",
            ".words.jsonl",
            ".segments.plan.json",
        )
    ):
        candidate = f"{clean}-{suffix}"
        suffix += 1
    return candidate


def run_pipeline(
    source_value: str,
    output_dir: Path,
    gateway: Any,
    *,
    keep_media: bool = False,
    normalize_audio: bool = False,
    include_timestamps: bool = True,
    include_segment_plan: bool = False,
    command_runner: Callable[[list[str]], str] = run_command,
    dependency_checker: Callable[[list[str]], None] = require_programs,
    ytdlp_resolver: Callable[[], list[str]] = resolve_ytdlp_command_prefix,
) -> PipelineResult:
    output_dir = output_dir.expanduser().resolve()
    source = classify_source(source_value)
    ytdlp_prefix: list[str] | None = None
    media_path: Path | None = None
    probe: ProbeInfo | None = None

    # Complete checks that cannot produce retryable artifacts before creating a
    # private work directory. This keeps simple configuration/input failures clean.
    if source.kind == "url":
        ytdlp_prefix = ytdlp_resolver()
        dependency_checker(["ffprobe"])
    else:
        dependency_checker(["ffprobe"])
        assert source.path is not None
        media_path = source.path.resolve()
        if not media_path.is_file() or media_path.stat().st_size <= 0:
            raise MediaError(f"local media file is missing or empty: {media_path}")
        probe = parse_probe(command_runner(build_ffprobe_command(media_path)))
        if probe.has_video or normalize_audio:
            dependency_checker(["ffmpeg"])

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / f".video-to-transcript-work-{uuid4().hex[:12]}"
    work_dir.mkdir(mode=0o700)
    artifacts: list[MediaArtifact] = []
    try:
        if source.kind == "url":
            assert ytdlp_prefix is not None
            media_path = _downloaded_path(
                command_runner(
                    build_ytdlp_command(source.value, work_dir, ytdlp_prefix)
                ),
                work_dir,
            )
            artifacts.append(MediaArtifact(media_path, True))
            preferred_stem = media_path.stem
            probe = parse_probe(command_runner(build_ffprobe_command(media_path)))
        else:
            assert media_path is not None
            artifacts.append(MediaArtifact(media_path, False))
            preferred_stem = media_path.stem

        assert media_path is not None
        assert probe is not None
        upload_path = media_path
        if probe.has_video or normalize_audio:
            dependency_checker(["ffmpeg"])
            upload_path = work_dir / "speech.mp3"
            command_runner(build_ffmpeg_command(media_path, upload_path))
            if not upload_path.is_file() or upload_path.stat().st_size <= 0:
                raise MediaError("ffmpeg did not create a usable audio file")
            parse_probe(command_runner(build_ffprobe_command(upload_path)))
            artifacts.append(MediaArtifact(upload_path, True))
        if upload_path.stat().st_size > 1024 * 1024 * 1024:
            raise MediaError("audio file exceeds the temporary upload limit of 1 GiB")

        asr_result = gateway.transcribe(upload_path)
        output_stem = _choose_output_stem(output_dir, preferred_stem)
        plan = build_segment_plan(asr_result.transcript) if include_segment_plan else None
        outputs = persist_outputs(
            output_dir,
            output_stem,
            asr_result.transcript,
            asr_result.raw_payload,
            {
                "model": gateway.config.model,
                "task_id": asr_result.task_id,
                "source": source_value,
                "duration_seconds": probe.duration_seconds,
            },
            include_timestamps=include_timestamps,
            segment_plan=plan,
        )
        deleted = cleanup_created_media(
            artifacts, work_dir, outputs, keep_media=keep_media
        )
        if not keep_media and work_dir.exists() and not any(work_dir.iterdir()):
            work_dir.rmdir()
        return PipelineResult(outputs, deleted, work_dir)
    except Exception as exc:
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError(str(exc), work_dir) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download or read media, transcribe it with Paraformer-v2, and delete "
            "only run-created media after the transcript is safely stored. "
            "Omitting a subcommand runs the transcription pipeline; use 'seek' "
            "to retrieve transcript sentences by timestamp without transcribing."
        )
    )
    # Defaults that must exist on the namespace for every mode.
    parser.set_defaults(
        include_timestamps=True,
        include_segment_plan=False,
        keep_media=False,
        normalize_audio=False,
        output_dir=Path("transcripts"),
    )
    subparsers = parser.add_subparsers(
        dest="mode", metavar="{transcribe,seek}"
    )

    transcribe = subparsers.add_parser(
        "transcribe",
        help="download/read media and transcribe it (default mode)",
        description="Download or read media and transcribe its speech with Paraformer-v2.",
    )
    transcribe.add_argument(
        "source", help="yt-dlp-supported URL or local media path"
    )
    transcribe.add_argument(
        "--output-dir", type=Path, default=Path("transcripts"), help="output directory"
    )
    transcribe.add_argument(
        "--keep-media",
        action="store_true",
        help="retain downloaded and extracted media even after success",
    )
    transcribe.add_argument(
        "--normalize-audio",
        action="store_true",
        help="also convert audio-only local input to 16 kHz mono MP3",
    )
    transcribe.add_argument(
        "--no-timestamps",
        dest="include_timestamps",
        action="store_false",
        help="write plain-text transcripts without sentence or word timelines",
    )
    transcribe.add_argument(
        "--segment-plan",
        dest="include_segment_plan",
        action="store_true",
        help="also write a chunk plan whose cut points follow sentence boundaries",
    )

    seek = subparsers.add_parser(
        "seek",
        help="retrieve transcript sentences covering a time point or range",
        description=(
            "Read a persisted *.transcript.json and return the sentences that "
            "cover a time point or range, plus optional adjacent-sentence "
            "context. Pure local lookup: no network call and no transcription."
        ),
    )
    seek.add_argument(
        "point_ms",
        type=int,
        help="video timestamp in milliseconds to locate",
    )
    seek.add_argument(
        "--output-dir",
        type=Path,
        default=Path("transcripts"),
        help="directory holding the transcript artifact (default: transcripts)",
    )
    seek.add_argument(
        "--stem",
        help="artifact stem (e.g. 'course'); resolves <output-dir>/<stem>.transcript.json",
    )
    seek.add_argument(
        "--transcript",
        type=Path,
        help="explicit path to *.transcript.json (overrides --output-dir/--stem resolution)",
    )
    seek.add_argument(
        "--context-before",
        type=int,
        default=1,
        help="number of preceding sentences to include as context (default: 1)",
    )
    seek.add_argument(
        "--context-after",
        type=int,
        default=1,
        help="number of following sentences to include as context (default: 1)",
    )
    return parser


def _format_clock(ms: int) -> str:
    """Render milliseconds as a human-readable mm:ss clock label."""
    total = max(0, int(ms)) // 1000
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def main(argv: list[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if not tokens:
        print("Error: no media source/URL given; run with a source or the "
              "'seek' command.", file=sys.stderr)
        return 2
    if tokens and tokens[0] not in {"transcribe", "seek"}:
        # Legacy invocation: the first token is the source, not a command.
        tokens.insert(0, "transcribe")
    args = build_parser().parse_args(tokens)
    if args.mode != "seek":
        try:
            config = load_asr_config()
            gateway = ParaformerGateway(config, transport=UrllibTransport())
            result = run_pipeline(
                args.source,
                args.output_dir,
                gateway,
                keep_media=args.keep_media,
                normalize_audio=args.normalize_audio,
                include_timestamps=args.include_timestamps,
                include_segment_plan=args.include_segment_plan,
            )
        except (MediaError, TranscriptionError, CleanupError, PipelineError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Transcript: {result.outputs.markdown}")
        print(f"Normalized JSON: {result.outputs.normalized_json}")
        print(f"Raw ASR JSON: {result.outputs.raw_json}")
        for path in result.outputs.existing():
            if path in (result.outputs.words_jsonl, result.outputs.segment_plan_json):
                print(f"{'Word timeline' if path == result.outputs.words_jsonl else 'Segment plan'}: {path}")
        if result.deleted_media:
            print(f"Deleted {len(result.deleted_media)} run-created media file(s).")
        elif args.keep_media:
            print(f"Media retained at: {result.work_dir}")
        return 0

    # Pure local timestamp-anchored retrieval (watch-along mode).
    try:
        segments, words = _load_timeline_artifacts(args)
    except (MediaError, TranscriptionError, CleanupError, PipelineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    hits = seek_by_point(
        segments,
        args.point_ms,
        context_before=args.context_before,
        context_after=args.context_after,
        words=words,
    )
    refined = any(hit.word_refined for hit in hits)
    for hit in hits:
        print(f"[{hit.index}] {_format_clock(hit.start_ms)}–{_format_clock(hit.end_ms)} {hit.text}")
    print(f"{len(hits)} sentence(s) cover {_format_clock(args.point_ms)} "
          f"(±{args.context_before}/+{args.context_after}, "
          f"word_refined={refined}).")
    return 0


def _load_timeline_artifacts(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Locate and read the persisted transcript and optional word timeline.

    Raises a clear error instead of crashing when the artifact is missing, and
    reports the exact path that was expected.
    """
    transcript_path = args.transcript
    if transcript_path is not None:
        transcript_path = transcript_path.expanduser()
    else:
        output_dir = args.output_dir.expanduser().resolve()
        stem = args.stem
        if stem:
            transcript_path = output_dir / f"{stem}.transcript.json"
        else:
            candidates = sorted(output_dir.glob("*.transcript.json"))
            if len(candidates) == 1:
                transcript_path = candidates[0]
            elif not candidates:
                raise TranscriptionError(
                    f"no *.transcript.json artifact found under: {output_dir}"
                )
            else:
                names = ", ".join(path.name for path in candidates)
                raise TranscriptionError(
                    f"multiple transcript artifacts found under {output_dir}; "
                    f"pass --stem to pick one: {names}"
                )
    if transcript_path is None or not transcript_path.is_file():
        raise TranscriptionError(
            f"transcript artifact is missing: {transcript_path}"
        )
    try:
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranscriptionError(
            f"transcript artifact is not readable JSON: {transcript_path}"
        ) from exc
    segments = payload.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise TranscriptionError(
            "该转录无时间轴，不支持按时间检索；它可能由 --no-timestamps 生成。"
            "当前工件由模式二产出，不支持回看。"
        )
    segments = validate_segments(segments)

    words: list[dict[str, Any]] = []
    words_path = transcript_path.with_suffix(".words.jsonl")
    if words_path.is_file():
        for line in words_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                word = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise TranscriptionError(
                    f"word timeline artifact is not valid JSON Lines: {words_path}"
                ) from exc
            if isinstance(word, dict):
                words.append(word)
    return segments, words


if __name__ == "__main__":
    raise SystemExit(main())
