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

## Behavior and limits

- URL downloads are single-item (`--no-playlist`).
- The selector is `bestaudio/worst`: best audio-only first, then the lowest combined-format fallback. It does not request separate streams that need merging.
- The normalized speech file is mono 16 kHz MP3 at 64 kbit/s.
- Timestamp alignment is disabled. The Markdown output contains plain transcript text, and normalized JSON contains `text` plus `metadata` without timestamp segments.
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
