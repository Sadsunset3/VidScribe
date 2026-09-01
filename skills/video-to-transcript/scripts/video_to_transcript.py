#!/usr/bin/env python3
"""Download or read media and transcribe its speech with Paraformer-v2."""

from __future__ import annotations

import argparse
import http.client
import importlib.util
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
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
                }
            )
    full_text = "\n".join(text_parts).strip()
    if not full_text and segments:
        full_text = "".join(segment["text"] for segment in segments).strip()
    if not full_text:
        raise TranscriptionError("transcription result is empty")
    return Transcript(full_text, segments)


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


def persist_outputs(
    output_dir: Path,
    stem: str,
    transcript: Transcript,
    raw_payload: dict[str, Any],
    metadata: dict[str, Any],
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
    )
    markdown_lines = [f"# {safe_stem}", "", transcript.text]
    normalized = {
        "text": transcript.text,
        "metadata": metadata,
    }
    _atomic_write_text(paths.markdown, "\n".join(markdown_lines) + "\n")
    _atomic_write_text(
        paths.normalized_json,
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(
        paths.raw_json,
        json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n",
    )
    return paths


def _outputs_are_valid(paths: OutputPaths) -> bool:
    try:
        if any(not path.is_file() or path.stat().st_size <= 0 for path in paths.__dict__.values()):
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
        for ending in (".transcript.md", ".transcript.json", ".asr.raw.json")
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
            "only run-created media after the transcript is safely stored."
        )
    )
    parser.add_argument("source", help="yt-dlp-supported URL or local media path")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("transcripts"), help="output directory"
    )
    parser.add_argument(
        "--keep-media",
        action="store_true",
        help="retain downloaded and extracted media even after success",
    )
    parser.add_argument(
        "--normalize-audio",
        action="store_true",
        help="also convert audio-only local input to 16 kHz mono MP3",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_asr_config()
        gateway = ParaformerGateway(config, transport=UrllibTransport())
        result = run_pipeline(
            args.source,
            args.output_dir,
            gateway,
            keep_media=args.keep_media,
            normalize_audio=args.normalize_audio,
        )
    except (MediaError, TranscriptionError, CleanupError, PipelineError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Transcript: {result.outputs.markdown}")
    print(f"Normalized JSON: {result.outputs.normalized_json}")
    print(f"Raw ASR JSON: {result.outputs.raw_json}")
    if result.deleted_media:
        print(f"Deleted {len(result.deleted_media)} run-created media file(s).")
    elif args.keep_media:
        print(f"Media retained at: {result.work_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
