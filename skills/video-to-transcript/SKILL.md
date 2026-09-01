---
name: video-to-transcript
description: Use when downloading or transcribing a video, podcast, course, or audio source; turning spoken content into a publishable Markdown blog or review article; calling DashScope Paraformer; or safely handling temporary transcription media.
---

# 视频转录与博客重构

使用随附 CLI 完成确定性的媒体处理，再把口语化转录重构为可公开发布、便于复习的 Markdown 博客。安装依赖、配置凭据或诊断转录故障时，完整阅读 [references/configuration.md](references/configuration.md)。

## 前置条件

1. 确认用户有权下载和处理输入来源，并遵守来源站点条款。
2. 接受 `PATH` 中的 `yt-dlp` 或当前 Python 环境中的 `yt_dlp`；只在媒体确实需要时要求 `ffmpeg` 和 `ffprobe`。
3. 要求 `DASHSCOPE_API_KEY`，且不得打印或持久化密钥。默认模型为 `paraformer-v2`，除非用户设置 `DASHSCOPE_MODEL`。

## 在私有暂存目录中转录

在用户指定的最终输出目录内创建一个仅属于本次运行的 `.video-to-blog-stage-<随机值>` 目录，解析并确认它位于该输出目录内，然后运行：

```bash
python scripts/video_to_transcript.py "<URL-or-local-path>" --output-dir "<private-stage-directory>"
```

URL 使用 `bestaudio/worst` 下载单个条目；本地视频只检查和提取音频，原始本地媒体永不删除。除非用户明确要求，不使用 `--keep-media`；只有需要紧凑的 16 kHz 单声道 MP3 时才使用 `--normalize-audio`。

从规范化 JSON 读取不含时间戳的 `text` 和 `metadata.duration_seconds`。标题来源优先级为：用户提供的名称与集数、可用媒体元数据、本地文件名、清理后的来源名；不得虚构标题或集数。

## 重构博客

完整阅读 [references/blog-reconstruction-workflow.md](references/blog-reconstruction-workflow.md)，默认使用其中的 `economy` 模式：

1. 按时长与 Token 预算的较大分片数切分。
2. 每片单次通读，同时生成一句话知识锚点和最小证据胶囊。
3. 只合并压缩材料；仅对标记节点定向回查原文，不做第二次全文读取。
4. 以锚点编号检查覆盖后移除编号，完成博客文风与 Markdown 检查。
5. 先写同目录临时文件，通过门禁后原子发布为 `*.blog.md`。

用户明确要求最高保真，或内容属于高风险且细节无法由定向回查确认时，才使用 `maximum-fidelity` 模式。

## 发布与清理门禁

最终文章首行必须是 `# 《名称》`；系列内容使用 `# 《名称 第X集/期》`。已有同名文件时依次选择 `-2`、`-3`，不得覆盖。

只有同时满足以下条件，才算发布成功：

- 最终文件非空，首行标题合规；
- 所有知识锚点恰好完成覆盖，内部编号、时间戳、日志和处理说明均已移除；
- 最终文件位于用户指定的输出目录，暂存目录仍解析在该目录内；
- 待清理文件均由本次运行创建且位于本次私有暂存目录内。

成功发布后，删除本次暂存目录中的转录 Markdown、规范化 JSON、ASR 原始响应、草稿及其他中间产物，再删除空暂存目录。不得用通配符、未解析变量或宽泛路径执行清理。最终 `*.blog.md` 是唯一交付物，回复中直接给出文章正文，不附开场白、路径清单、执行过程或质检报告。

如果转录、重构、验证或发布失败，不执行成功清理；保留私有暂存目录供重试，并简洁报告失败阶段和目录。失败恢复材料不属于成功交付物。

## 禁止事项

- 不分别请求视频流和音频流，不使用 `worstaudio`，不处理播放列表。
- 不向最终文章加入时间戳、知识清单、锚点编号或处理日志。
- 不为补齐文章虚构观点、经历、数据、代码、案例或引用。
- 不把博客写成任务汇报、会议总结、公文纪要或逐句转录。
- 未使用真实来源和凭据完成云端调用时，不声称已经验证实时转录。
