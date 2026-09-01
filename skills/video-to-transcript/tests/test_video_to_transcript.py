import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import video_to_transcript as vtt


class MediaRoutingTests(unittest.TestCase):
    def test_url_source_prefers_best_audio_without_downloading_video(self):
        work_dir = Path("/tmp/job")

        command = vtt.build_ytdlp_command(
            "https://example.com/watch?v=abc", work_dir
        )

        self.assertEqual(command[0], "yt-dlp")
        self.assertIn("--no-playlist", command)
        self.assertEqual(
            command[command.index("--format") + 1],
            "bestaudio/worst",
        )
        self.assertIn("after_move:filepath", command)
        self.assertEqual(command[-1], "https://example.com/watch?v=abc")

    def test_ytdlp_falls_back_to_the_current_python_module_when_not_on_path(self):
        self.assertTrue(hasattr(vtt, "resolve_ytdlp_command_prefix"))

        prefix = vtt.resolve_ytdlp_command_prefix(
            executable_finder=lambda _: None,
            module_finder=lambda _: object(),
        )

        self.assertEqual(prefix, [sys.executable, "-m", "yt_dlp"])

    def test_ytdlp_prefers_an_executable_already_on_path(self):
        self.assertTrue(hasattr(vtt, "resolve_ytdlp_command_prefix"))

        prefix = vtt.resolve_ytdlp_command_prefix(
            executable_finder=lambda _: "/tools/yt-dlp",
            module_finder=lambda _: None,
        )

        self.assertEqual(prefix, ["/tools/yt-dlp"])

    def test_local_source_is_never_owned_by_the_run(self):
        source = Path("/courses/vue3.mp4")

        classified = vtt.classify_source(str(source))

        self.assertEqual(classified.kind, "local")
        self.assertEqual(classified.path, source)
        self.assertFalse(classified.created_by_run)

    def test_only_http_and_https_are_treated_as_urls(self):
        self.assertTrue(vtt.is_supported_url("https://example.com/video"))
        self.assertTrue(vtt.is_supported_url("http://example.com/video"))
        self.assertFalse(vtt.is_supported_url("file:///tmp/video.mp4"))
        self.assertFalse(vtt.is_supported_url("/tmp/video.mp4"))


class AudioPreparationTests(unittest.TestCase):
    def test_probe_requires_a_positive_duration_audio_stream(self):
        payload = {
            "streams": [{"codec_type": "audio", "duration": "12.75"}],
            "format": {"duration": "12.75"},
        }

        info = vtt.parse_probe(json.dumps(payload))

        self.assertTrue(info.has_audio)
        self.assertFalse(info.has_video)
        self.assertAlmostEqual(info.duration_seconds, 12.75)

    def test_probe_rejects_media_without_audio(self):
        payload = {
            "streams": [{"codec_type": "video"}],
            "format": {"duration": "20"},
        }

        with self.assertRaisesRegex(vtt.MediaError, "audio stream"):
            vtt.parse_probe(json.dumps(payload))

    def test_ffmpeg_normalizes_to_compact_speech_mp3(self):
        command = vtt.build_ffmpeg_command(
            Path("/tmp/job/source.webm"), Path("/tmp/job/speech.mp3")
        )

        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-vn", command)
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-b:a") + 1], "64k")
        self.assertEqual(Path(command[-1]), Path("/tmp/job/speech.mp3"))


class ParaformerTests(unittest.TestCase):
    def setUp(self):
        self.config = vtt.AsrConfig(
            api_key="secret",
            model="paraformer-v2",
            upload_url="https://dashscope.example/api/v1/uploads",
            asr_url="https://dashscope.example/api/v1/services/audio/asr/transcription",
            task_url_template="https://dashscope.example/api/v1/tasks/{task_id}",
            poll_interval_seconds=0,
            timeout_seconds=30,
        )

    def test_submit_request_enables_async_and_oss_resolution(self):
        url, headers, body = vtt.build_submit_request(
            self.config, "oss://bucket/audio.mp3"
        )

        self.assertEqual(url, self.config.asr_url)
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["X-DashScope-Async"], "enable")
        self.assertEqual(headers["X-DashScope-OssResourceResolve"], "enable")
        self.assertEqual(body["model"], "paraformer-v2")
        self.assertEqual(body["input"]["file_urls"], ["oss://bucket/audio.mp3"])
        self.assertFalse(body["parameters"]["timestamp_alignment_enabled"])
        self.assertFalse(body["parameters"]["disfluency_removal_enabled"])

    def test_upload_form_uses_policy_fields_and_private_object(self):
        policy = {
            "upload_dir": "tmp/abc",
            "upload_host": "https://bucket.oss-cn-beijing.aliyuncs.com",
            "oss_access_key_id": "access",
            "policy": "encoded-policy",
            "signature": "signature",
        }

        upload_host, fields, oss_uri = vtt.build_upload_form(
            policy, Path("/tmp/lesson speech.mp3")
        )

        self.assertEqual(upload_host, policy["upload_host"])
        self.assertEqual(fields["OSSAccessKeyId"], "access")
        self.assertEqual(fields["x-oss-object-acl"], "private")
        self.assertEqual(fields["success_action_status"], "200")
        self.assertTrue(fields["key"].startswith("tmp/abc/"))
        self.assertEqual(oss_uri, "oss://" + fields["key"])

    def test_poll_state_extracts_transcription_url(self):
        payload = {
            "output": {
                "task_id": "task-1",
                "task_status": "SUCCEEDED",
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "https://result.example/result.json",
                    }
                ],
            }
        }

        state = vtt.parse_task_state(payload)

        self.assertEqual(state.status, "SUCCEEDED")
        self.assertEqual(
            state.transcription_url, "https://result.example/result.json"
        )

    def test_normalizes_text_and_timestamped_sentences(self):
        payload = {
            "transcripts": [
                {
                    "text": "先创建项目。然后安装 Vue。",
                    "sentences": [
                        {"begin_time": 0, "end_time": 1200, "text": "先创建项目。"},
                        {
                            "begin_time": 1200,
                            "end_time": 2500,
                            "text": "然后安装 Vue。",
                        },
                    ],
                }
            ]
        }

        transcript = vtt.normalize_transcription(payload)

        self.assertEqual(transcript.text, "先创建项目。然后安装 Vue。")
        self.assertEqual(len(transcript.segments), 2)
        self.assertEqual(transcript.segments[1]["start_ms"], 1200)

    def test_rejects_success_payload_without_nonempty_text(self):
        with self.assertRaisesRegex(vtt.TranscriptionError, "empty"):
            vtt.normalize_transcription({"transcripts": [{"text": "  "}]})

    def test_gateway_uploads_submits_polls_and_fetches_result(self):
        transport = FakeTransport(
            json_responses=[
                {
                    "data": {
                        "upload_dir": "tmp/abc",
                        "upload_host": "https://bucket.example",
                        "oss_access_key_id": "access",
                        "policy": "policy",
                        "signature": "signature",
                    }
                },
                {"output": {"task_id": "task-1", "task_status": "PENDING"}},
                {"output": {"task_id": "task-1", "task_status": "RUNNING"}},
                {
                    "output": {
                        "task_id": "task-1",
                        "task_status": "SUCCEEDED",
                        "results": [
                            {
                                "subtask_status": "SUCCEEDED",
                                "transcription_url": "https://result.example/result.json",
                            }
                        ],
                    }
                },
                {"transcripts": [{"text": "转录完成。", "sentences": []}]},
            ]
        )
        gateway = vtt.ParaformerGateway(
            self.config, transport=transport, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "speech.mp3"
            audio.write_bytes(b"audio")

            result = gateway.transcribe(audio)

        self.assertEqual(result.transcript.text, "转录完成。")
        self.assertEqual(result.task_id, "task-1")
        self.assertEqual(transport.multipart_calls[0][0], "https://bucket.example")
        submit = transport.json_calls[1]
        self.assertEqual(submit[0], "POST")
        self.assertEqual(submit[3]["model"], "paraformer-v2")
        self.assertIn("action=getPolicy", transport.json_calls[0][1])

    def test_gateway_retains_provider_failure_as_error(self):
        transport = FakeTransport(
            json_responses=[
                {
                    "data": {
                        "upload_dir": "tmp/abc",
                        "upload_host": "https://bucket.example",
                        "oss_access_key_id": "access",
                        "policy": "policy",
                        "signature": "signature",
                    }
                },
                {"output": {"task_id": "task-1", "task_status": "PENDING"}},
                {
                    "output": {
                        "task_id": "task-1",
                        "task_status": "FAILED",
                        "message": "unsupported audio",
                    }
                },
            ]
        )
        gateway = vtt.ParaformerGateway(
            self.config, transport=transport, sleeper=lambda _: None
        )
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "speech.mp3"
            audio.write_bytes(b"audio")

            with self.assertRaisesRegex(vtt.TranscriptionError, "unsupported audio"):
                gateway.transcribe(audio)

    def test_config_accepts_custom_key_model_and_urls(self):
        config = vtt.load_asr_config(
            {
                "DASHSCOPE_API_KEY": "custom-key",
                "DASHSCOPE_MODEL": "custom-asr",
                "DASHSCOPE_UPLOAD_URL": "https://custom.example/uploads",
                "DASHSCOPE_ASR_URL": "https://custom.example/asr",
                "DASHSCOPE_TASK_URL_TEMPLATE": "https://custom.example/tasks/{task_id}",
            }
        )

        self.assertEqual(config.api_key, "custom-key")
        self.assertEqual(config.model, "custom-asr")
        self.assertEqual(config.asr_url, "https://custom.example/asr")

    def test_config_requires_api_key(self):
        with self.assertRaisesRegex(vtt.TranscriptionError, "DASHSCOPE_API_KEY"):
            vtt.load_asr_config({})


class PersistenceAndCleanupTests(unittest.TestCase):
    def test_persists_nonempty_transcript_atomically(self):
        transcript = vtt.Transcript(
            "先创建项目。",
            [{"start_ms": 0, "end_ms": 1200, "text": "先创建项目。"}],
        )
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)

            outputs = vtt.persist_outputs(
                output_dir=output_dir,
                stem="vue3-course",
                transcript=transcript,
                raw_payload={"transcripts": [{"text": transcript.text}]},
                metadata={"model": "paraformer-v2"},
            )

            self.assertTrue(outputs.markdown.is_file())
            self.assertTrue(outputs.normalized_json.is_file())
            self.assertTrue(outputs.raw_json.is_file())
            markdown = outputs.markdown.read_text(encoding="utf-8")
            self.assertNotIn("[00:00:00]", markdown)
            self.assertIn(transcript.text, markdown)
            normalized = json.loads(outputs.normalized_json.read_text(encoding="utf-8"))
            self.assertEqual(set(normalized), {"text", "metadata"})
            self.assertFalse(list(output_dir.glob("*.tmp")))

    def test_cleanup_deletes_only_created_media_after_outputs_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work_dir = root / "work"
            output_dir = root / "out"
            work_dir.mkdir()
            downloaded = work_dir / "source.mp4"
            audio = work_dir / "speech.mp3"
            local_original = root / "original.mp4"
            for path in (downloaded, audio, local_original):
                path.write_bytes(b"media")
            outputs = vtt.persist_outputs(
                output_dir,
                "lesson",
                vtt.Transcript("有效文本", []),
                {"transcripts": [{"text": "有效文本"}]},
                {"model": "paraformer-v2"},
            )

            deleted = vtt.cleanup_created_media(
                [
                    vtt.MediaArtifact(downloaded, True),
                    vtt.MediaArtifact(audio, True),
                    vtt.MediaArtifact(local_original, False),
                ],
                work_dir,
                outputs,
            )

            self.assertEqual(set(deleted), {downloaded, audio})
            self.assertFalse(downloaded.exists())
            self.assertFalse(audio.exists())
            self.assertTrue(local_original.exists())

    def test_cleanup_refuses_created_path_outside_private_work_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work_dir = root / "work"
            output_dir = root / "out"
            work_dir.mkdir()
            outside = root / "outside.mp4"
            outside.write_bytes(b"media")
            outputs = vtt.persist_outputs(
                output_dir,
                "lesson",
                vtt.Transcript("有效文本", []),
                {"transcripts": [{"text": "有效文本"}]},
                {"model": "paraformer-v2"},
            )

            with self.assertRaisesRegex(vtt.CleanupError, "outside"):
                vtt.cleanup_created_media(
                    [vtt.MediaArtifact(outside, True)], work_dir, outputs
                )

            self.assertTrue(outside.exists())

    def test_cleanup_refuses_missing_or_empty_transcript_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            work_dir = root / "work"
            work_dir.mkdir()
            downloaded = work_dir / "source.mp4"
            downloaded.write_bytes(b"media")
            outputs = vtt.OutputPaths(
                root / "missing.md", root / "missing.json", root / "missing-raw.json"
            )

            with self.assertRaisesRegex(vtt.CleanupError, "validated"):
                vtt.cleanup_created_media(
                    [vtt.MediaArtifact(downloaded, True)], work_dir, outputs
                )

            self.assertTrue(downloaded.exists())


class PipelineTests(unittest.TestCase):
    def test_preflight_failure_does_not_create_an_empty_work_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "missing.mp3"
            output_dir = root / "out"

            with self.assertRaisesRegex(vtt.MediaError, "missing or empty"):
                vtt.run_pipeline(
                    str(source),
                    output_dir,
                    FakeGateway(),
                    dependency_checker=lambda names: None,
                )

            self.assertFalse(output_dir.exists())

    def test_local_video_is_transcribed_but_original_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "vue3.mp4"
            source.write_bytes(b"local-original")
            output_dir = root / "out"
            runner = FakeCommandRunner(has_video=True)
            gateway = FakeGateway()

            result = vtt.run_pipeline(
                str(source),
                output_dir,
                gateway,
                command_runner=runner,
                dependency_checker=lambda names: None,
            )

            self.assertTrue(source.exists())
            self.assertTrue(result.outputs.markdown.exists())
            self.assertFalse(any(path.name == "speech.mp3" for path in root.rglob("*")))
            self.assertFalse(result.work_dir.exists())

    def test_url_download_and_extracted_audio_are_deleted_after_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_dir = root / "out"
            runner = FakeCommandRunner(has_video=True)
            gateway = FakeGateway()
            resolved_prefix = ["fake-python", "-m", "yt_dlp"]

            result = vtt.run_pipeline(
                "https://example.com/watch?v=abc",
                output_dir,
                gateway,
                command_runner=runner,
                dependency_checker=lambda names: None,
                ytdlp_resolver=lambda: resolved_prefix,
            )

            self.assertEqual(runner.calls[0][:3], resolved_prefix)
            self.assertTrue(result.outputs.normalized_json.exists())
            self.assertGreaterEqual(len(result.deleted_media), 2)
            self.assertTrue(all(not path.exists() for path in result.deleted_media))

    def test_failure_retains_created_media_for_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_dir = root / "out"
            runner = FakeCommandRunner(has_video=True)
            gateway = FakeGateway(error=vtt.TranscriptionError("provider failed"))
            resolved_prefix = ["fake-python", "-m", "yt_dlp"]

            with self.assertRaisesRegex(vtt.PipelineError, "retained") as raised:
                vtt.run_pipeline(
                    "https://example.com/watch?v=abc",
                    output_dir,
                    gateway,
                    command_runner=runner,
                    dependency_checker=lambda names: None,
                    ytdlp_resolver=lambda: resolved_prefix,
                )

            self.assertEqual(runner.calls[0][:3], resolved_prefix)
            work_dir = raised.exception.work_dir
            self.assertTrue(work_dir.is_dir())
            self.assertTrue(any(path.is_file() for path in work_dir.iterdir()))

    def test_local_audio_skips_ffmpeg_and_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "course.mp3"
            source.write_bytes(b"local-audio")
            runner = FakeCommandRunner(has_video=False)

            result = vtt.run_pipeline(
                str(source),
                root / "out",
                FakeGateway(),
                command_runner=runner,
                dependency_checker=lambda names: None,
            )

            self.assertTrue(source.exists())
            self.assertFalse(any(call[0] == "ffmpeg" for call in runner.calls))
            self.assertTrue(result.outputs.markdown.exists())


class FakeTransport:
    def __init__(self, json_responses):
        self.json_responses = list(json_responses)
        self.json_calls = []
        self.multipart_calls = []

    def request_json(self, method, url, headers=None, payload=None):
        self.json_calls.append((method, url, headers or {}, payload))
        if not self.json_responses:
            raise AssertionError("unexpected JSON request")
        return self.json_responses.pop(0)

    def post_multipart(self, url, fields, file_field, file_path):
        self.multipart_calls.append((url, fields, file_field, file_path))


class FakeCommandRunner:
    def __init__(self, has_video):
        self.has_video = has_video
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        if "--output" in command and "after_move:filepath" in command:
            output_template = Path(command[command.index("--output") + 1])
            downloaded = output_template.parent / "source-abc.mp4"
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(b"downloaded-video")
            return str(downloaded) + "\n"
        if command[0] == "ffprobe":
            streams = [{"codec_type": "audio", "duration": "30"}]
            if self.has_video:
                streams.append({"codec_type": "video", "duration": "30"})
            return json.dumps({"streams": streams, "format": {"duration": "30"}})
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"normalized-audio")
            return ""
        raise AssertionError(f"unexpected command: {command}")


class DocumentationTests(unittest.TestCase):
    def _skill_text(self, relative_path):
        skill_root = Path(__file__).resolve().parents[1]
        return (skill_root / relative_path).read_text(encoding="utf-8")

    def test_windows_installation_recommends_the_smaller_ffmpeg_build(self):
        skill_root = Path(__file__).resolve().parents[1]
        configuration = (skill_root / "references" / "configuration.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Gyan.FFmpeg.Essentials", configuration)
        self.assertIn("python -m yt_dlp", configuration)

    def test_skill_promises_plain_transcript_text_without_timestamps(self):
        skill_root = Path(__file__).resolve().parents[1]
        instructions = (skill_root / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("without timestamps", instructions)

    def test_skill_continues_from_transcript_to_technical_document(self):
        instructions = self._skill_text("SKILL.md")

        self.assertIn("references/technical-document-workflow.md", instructions)
        self.assertIn("*.technical.md", instructions)
        self.assertNotIn("note generation is a separate downstream step", instructions)

    def test_technical_document_reference_has_fixed_priority_and_structure(self):
        reference = self._skill_text("references/technical-document-workflow.md")

        self.assertIn("用户任务 > 事实准确 > 技术完整性 > 风格约束", reference)
        positions = [reference.index(name) for name in (
            "背景", "定义", "组成", "过程", "实例", "限制", "总结"
        )]
        self.assertEqual(positions, sorted(positions))

    def test_long_transcript_contract_preserves_semantic_units(self):
        reference = self._skill_text("references/technical-document-workflow.md")

        for required in (
            "6000～8000", "代码块", "命令", "操作步骤",
            "主题状态", "事实卡", "文本块编号", "原文段落编号",
        ):
            self.assertIn(required, reference)
        self.assertIn("不机械复制固定长度的重叠文本", reference)
        self.assertIn(
            "cap the target at one third of usable context when that bound is smaller",
            reference,
        )
        for precise_fact_card_field in (
            "原文定义", "相互关系", "先后依赖", "讲者描述的结果", "适用条件",
        ):
            self.assertIn(precise_fact_card_field, reference)

    def test_static_long_transcript_evaluation_preserves_workflow_invariants(self):
        skill_root = Path(__file__).resolve().parents[1]
        evaluator = skill_root / "evals" / "long-transcript-static" / "run_evaluation.py"

        completed = subprocess.run(
            [sys.executable, "-B", "-S", str(evaluator), "--json"],
            cwd=skill_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        report = json.loads(completed.stdout)
        self.assertEqual(report["evaluation_kind"], "static_fixture")
        self.assertFalse(report["model_execution_claimed"])
        self.assertGreater(report["fixture_characters"], 8000)
        self.assertGreaterEqual(report["block_count"], 2)
        for invariant in (
            "source_coverage_exact",
            "chunk_fact_card_workflow_complete",
            "fenced_code_not_split",
            "cross_block_steps_ordered_and_connected",
            "unique_source_facts_exactly_once_after_merge",
        ):
            self.assertTrue(report["invariants"][invariant], invariant)

    def test_humanizer_rules_cannot_override_technical_accuracy(self):
        reference = self._skill_text("references/technical-document-workflow.md")

        for forbidden_behavior in (
            "不得虚构作者立场", "不得虚构经历", "不强制禁用技术标点",
            "不输出 AI 味等级", "不提供文风切换",
        ):
            self.assertIn(forbidden_behavior, reference)

    def test_document_publishing_preserves_transcript_artifacts(self):
        instructions = self._skill_text("SKILL.md")

        self.assertIn("-2", instructions)
        self.assertIn("partial success", instructions)
        self.assertIn("Never delete or overwrite transcript outputs", instructions)


class FakeGateway:
    def __init__(self, error=None):
        self.error = error
        self.config = type("Config", (), {"model": "paraformer-v2"})()

    def transcribe(self, audio_path):
        if self.error:
            raise self.error
        return vtt.AsrResult(
            vtt.Transcript(
                "课程转录文本。",
                [{"start_ms": 0, "end_ms": 1000, "text": "课程转录文本。"}],
            ),
            {"transcripts": [{"text": "课程转录文本。"}]},
            "task-1",
        )


if __name__ == "__main__":
    unittest.main()
