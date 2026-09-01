---
name: video-to-transcript
description: Use when a user asks to download or transcribe a course video, convert video or audio into technical documentation, call Bailian/DashScope Paraformer, or safely process temporary transcription media.
---

# Video to Transcript

Run the bundled CLI for deterministic media handling and deletion safety. Read [references/configuration.md](references/configuration.md) when installing dependencies, setting credentials, changing Bailian URLs/model, or diagnosing a failure.

## Preconditions

1. Confirm the user is authorized to download/process the source and comply with the source site's terms.
2. Accept `yt-dlp` either on `PATH` or as the current Python environment's `yt_dlp` module. Treat `ffmpeg` and `ffprobe` as executable dependencies only when the selected media requires them. Do not clone or compile their GitHub repositories unless the user explicitly wants a source build.
3. Require `DASHSCOPE_API_KEY`. Never print or persist the key.
4. Use the built-in `paraformer-v2` default unless the user supplies `DASHSCOPE_MODEL`.

## Execute

From this Skill directory, run:

```bash
python scripts/video_to_transcript.py "<URL-or-local-path>" --output-dir "<output-directory>"
```

Default routing:

- URL: download one item with `bestaudio/worst`, then inspect it. This prefers the best audio-only format and falls back to the lowest combined format without downloading separate video and audio streams for merging.
- Local video: inspect the original in place, extract speech to a private work directory, and never delete the original.
- Local audio or downloaded audio-only file: upload it directly; add `--normalize-audio` only when a compact 16 kHz mono MP3 is preferred.
- Video audio: extract to mono 16 kHz MP3 at 64 kbit/s. Preserve silence and teaching rhythm.

Use `--keep-media` when the user wants downloaded/extracted media retained even after success.

## Organize and publish the technical document

After CLI success:

1. Read `references/technical-document-workflow.md` completely.
2. Load `text` and `metadata` from normalized JSON.
3. Use direct organization for short text or the reference's chunk/fact-card workflow for long text.
4. Verify unstable technical claims with official primary sources when access is available.
5. Draft, self-check, and publish the non-overwriting `*.technical.md` file.
6. Keep the three transcript artifacts unchanged.

If transcription fails, retain run-created media under the existing deletion gate.
If transcription succeeds but document organization fails, report partial success
and keep all transcript outputs. Never delete or overwrite transcript outputs
during document organization, and retain any document draft for retry.

## Report outputs

On success, return these paths in this order:

- `*.technical.md`: published technical document without timestamps;
- `*.transcript.md`: readable transcript text without timestamps;
- `*.transcript.json`: normalized `text` plus `metadata` containing model, task ID, source, and duration;
- `*.asr.raw.json`: unmodified provider result for audit or future reprocessing.

If a technical document already exists, publish `-2`, `-3`, and later available
suffixes rather than overwriting it.

Clarify cleanup scope when relevant: the CLI deletes only local media created by the run. The Bailian temporary OSS upload is provider-managed under its temporary-storage lifecycle; this CLI receives no delete credential for that object.

## Enforce the deletion gate

Never delete a local input file. Never delete media in a general cleanup or `finally` block.

Allow cleanup only after all three output files exist, the normalized JSON contains non-whitespace text, and every deletion target both:

- was created by this run; and
- resolves inside this run's private work directory.

The script enforces these checks. Preflight failures occur before a work directory is created. After media creation starts, download, conversion, upload, provider, parsing, persistence, or validation failures retain run-created media and report the private work directory for retry/debugging.

## Avoid

- Do not request separate video and audio formats; transcription is audio-first and does not need a merge step.
- Do not use `worstaudio`; ASR accuracy depends on audio quality.
- Do not add `--yes-playlist`; one invocation processes one item.
- Do not request timestamp alignment or add timestamps to normalized outputs.
- Do not remove pauses or silence by default; they matter for teaching rhythm.
- Do not claim a live cloud transcription was verified unless credentials and a real source were actually used.
