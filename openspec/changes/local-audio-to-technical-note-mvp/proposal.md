## Why

当前仓库只有产品需求和目标笔记样例，尚无可运行的软件。首个变更需要先打通“本地课程音频 → 云端句级转录 → 教学脉络 Markdown”的最小价值闭环，以验证云端服务集成、笔记质量、失败重试和临时音频清理这些最高风险环节。

## What Changes

- 新建 Windows 桌面端应用，允许用户选择本地 MP3、M4A 或 WAV 音频并创建技术课程笔记任务。
- 使用临时对象存储向云端 ASR 提供短期 HTTPS 音频地址，并在转录结果持久化后清理远端对象。
- 默认通过阿里云百炼 `paraformer-v2` 完成长音频异步转写，保存原始响应和统一的句级时间戳转录。
- 默认通过 `qwen3.8-flash` 生成遵循原始讲解顺序的技术课程 Markdown 笔记。
- 提供任务列表、阶段进度、失败原因、从失败阶段重试、转录查看、Markdown 源码/预览和 `.md` 导出。
- 提供 OSS、ASR 和 LLM 配置与连接测试；敏感凭据使用 Windows 安全凭据存储，界面和日志仅显示掩码。
- 首版只串行执行任务，不包含 URL 下载、本地视频、FFmpeg 转码、仅提取媒体、播客预设和自定义预设。

## Capabilities

### New Capabilities

- `local-audio-intake`: 选择、检测并安全使用受支持的本地音频，不修改用户原文件。
- `temporary-audio-hosting`: 上传、授权访问并可预测地清理供 ASR 使用的远端临时音频。
- `cloud-transcription`: 提交和轮询长音频 ASR，持久化原始响应并生成统一句级转录。
- `technical-note-generation`: 根据课程元数据和带时间戳转录生成教学脉络 Markdown。
- `processing-task-management`: 持久化任务阶段、展示进度、处理中断并从失败阶段重试。
- `provider-configuration`: 配置并测试 OSS、ASR 和 LLM 服务，同时安全管理敏感凭据。
- `note-results`: 查看、复制和导出转录及 Markdown 结果。

### Modified Capabilities

无。当前项目尚无已发布的 OpenSpec capability。

## Impact

- 新增 Java 21、JavaFX、Maven、SQLite、Java HTTP Client、Jackson、Flexmark、JNA 和 JUnit 5 技术栈。
- 新增本地 SQLite 数据库、应用工作目录和 Windows Credential Manager 凭据项。
- 集成兼容 S3/OSS 的临时对象存储、阿里云百炼长音频 ASR 和 OpenAI 兼容 LLM API。
- 首个安装包仅支持 Windows，并通过 `jpackage` 生成自包含应用镜像或 `.exe`。
