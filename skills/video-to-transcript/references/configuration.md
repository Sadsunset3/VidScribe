# Configuration and operation

## Install executables

You need yt-dlp for URL input and FFprobe for media inspection. FFmpeg is needed only when speech must be separated or normalized. Install maintained binaries/packages; cloning the source repositories is unnecessary.

Official projects:

- https://github.com/yt-dlp/yt-dlp
- https://github.com/FFmpeg/FFmpeg

Common options:

```bash
# macOS
brew install yt-dlp ffmpeg

# Ubuntu/Debian (yt-dlp from pipx stays newer than many distro packages)
sudo apt-get install ffmpeg pipx
pipx install yt-dlp

# Cross-platform Python install for yt-dlp
python -m pip install -U yt-dlp
```

The CLI finds `yt-dlp` on `PATH` first and otherwise runs the current interpreter's `yt_dlp` module. This makes a normal `python -m pip install -U yt-dlp` usable even when the Python scripts directory is not on `PATH`.

On Windows, prefer the smaller Essentials build, which includes the required `ffmpeg` and `ffprobe` commands:

```powershell
winget install --id Gyan.FFmpeg.Essentials --exact
```

Add manually installed FFmpeg binaries to `PATH`. A full FFmpeg build is unnecessary for this workflow.

Verify:

```bash
yt-dlp --version
python -m yt_dlp --version  # fallback when yt-dlp is not on PATH
ffmpeg -version
ffprobe -version
```

Do not install the unrelated Python package named `ffmpeg` as a replacement for the FFmpeg executable.

## Configure Bailian / DashScope

Set the key in the process environment:

```bash
export DASHSCOPE_API_KEY="<your-key>"
```

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DASHSCOPE_MODEL` | `paraformer-v2` | ASR model name |
| `DASHSCOPE_UPLOAD_URL` | `https://dashscope.aliyuncs.com/api/v1/uploads` | Temporary OSS policy endpoint |
| `DASHSCOPE_ASR_URL` | `https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription` | Async ASR submission endpoint |
| `DASHSCOPE_TASK_URL_TEMPLATE` | `https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}` | Polling endpoint; keep `{task_id}` |
| `DASHSCOPE_POLL_INTERVAL` | `5` | Seconds between task checks |
| `DASHSCOPE_TIMEOUT` | `14400` | Maximum task wait in seconds |

These variables support a custom compatible URL, key, and model without editing the script. Keep region/workspace endpoints consistent with the API key's account and deployment.

## Use

URL input:

```bash
python scripts/video_to_transcript.py "https://example.com/video" --output-dir ./transcripts
```

Local video input:

```bash
python scripts/video_to_transcript.py "/path/to/course.mp4" --output-dir ./transcripts
```

Local audio input (no separation/conversion by default):

```bash
python scripts/video_to_transcript.py "/path/to/course.mp3" --output-dir ./transcripts
```

Force local audio normalization:

```bash
python scripts/video_to_transcript.py "/path/to/course.wav" --normalize-audio --output-dir ./transcripts
```

Retain downloaded/extracted media after successful transcription:

```bash
python scripts/video_to_transcript.py "<source>" --keep-media --output-dir ./transcripts
```

Omit the sentence/word timelines and write plain text only:

```bash
python scripts/video_to_transcript.py "<source>" --no-timestamps --output-dir ./transcripts
```

Also write a chunk plan whose cut points follow sentence boundaries:

```bash
python scripts/video_to_transcript.py "<source>" --segment-plan --output-dir ./transcripts
```

Retrieve the sentence covering a time point without transcribing (watch-along):

```bash
python scripts/video_to_transcript.py seek <milliseconds> --stem "<stem>" --output-dir ./transcripts
```

`seek` reads the persisted `*.transcript.json` `segments[]` only (binary search, no network call, no extra index document). Pass `--context-before`/`--context-after` to widen to adjacent sentences; a transcript produced with `--no-timestamps` reports that it has no timeline and cannot be sought.

## 时间戳工件

Paraformer 的异步识别结果**无条件**携带句级与词级时间戳（单位为毫秒）。`timestamp_alignment_enabled` 控制的是长音频上的**时间戳校准**（让识别结果与播放进度同步），而**不是**响应中是否返回时间戳；把它设为 `false` 不会去掉时间戳。因此本 CLI 保持该参数为 `false` 也能获得完整时间轴。

句级时间轴内联在 `*.transcript.json` 的 `segments` 数组中；词级时间轴单独写入 `*.words.jsonl`，每行一个词对象，含 `start_ms`、`end_ms`、`text`、`punctuation` 与 `sentence_index`。词级数据量大（一小时播客约两万个词），独立存放以免撑大每次都要读取的规范化 JSON。

三者默认都输出，`--no-timestamps` 可退回纯文本；`--segment-plan` 默认关闭，启用后额外输出 `*.segments.plan.json`。

时间轴需通过以下校验，任一条不满足即视为转录失败并保留媒体，绝不静默降级为无时间戳输出：句子非空且按时间递增不重叠、句子文本拼接与全文一致、每个词的区间落在其所属句子区间内且同句内不重叠。

时间戳只存在于转录工件中。最终总结仍不得包含时间戳。

## Behavior and limits

- URL downloads are single-item (`--no-playlist`).
- The selector is `bestaudio/worst`: best audio-only first, then the lowest combined-format fallback. It does not request separate streams that need merging.
- The normalized speech file is mono 16 kHz MP3 at 64 kbit/s.
- Timestamp alignment stays off, which only skips the long-audio calibration pass; the sentence and word timelines are still returned and persisted by default. Use `--no-timestamps` when a plain transcript is wanted.
- The temporary upload path is limited by this script to 1 GiB.
- Audio-only input is uploaded directly after FFprobe validation. Use `--normalize-audio` if its codec/container may not be accepted by the configured Paraformer-compatible endpoint.
- The CLI deletes local run-created media only. Bailian's temporary OSS object is governed by the provider's temporary-storage TTL; the temporary policy does not give this CLI an object-delete credential.
- Provider work is asynchronous; the script polls until success, failure, or timeout.
- Success outputs are written atomically and existing names receive `-2`, `-3`, and so on instead of being overwritten.
- Local inputs are never deleted.
- Failed runs retain their private `.video-to-transcript-work-*` directory. Remove it manually only after inspecting/retrying.

## Troubleshooting

`missing required programs`: install the named executable and confirm it is on `PATH`. For yt-dlp, either `yt-dlp --version` or `python -m yt_dlp --version` may succeed.

`media has no audio stream`: verify the source contains audible media; some yt-dlp sites expose separate or protected streams.

`temporary upload policy response has no data`: verify the endpoint region, key, model, and account permissions.

`task succeeded but returned no transcription_url`: inspect `*.asr.raw.json` if present, or the retained work directory and provider console.

`transcription result is empty`: the script intentionally keeps the media and does not treat an empty provider response as success.

`transcription result has no timestamped sentences`、`overlaps`、`ends before it starts`、`outside its sentence`、`does not match`: the ASR response returned a malformed timeline. The script keeps the media and the private work directory instead of silently dropping the timestamps. Retry, or run with `--no-timestamps` if only the plain text is needed.
