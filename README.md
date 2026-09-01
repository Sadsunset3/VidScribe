# VidScribe

VidScribe 是一个面向视频、播客和课程音频的可移植 Agent Skill。它在私有暂存目录中完成无时间戳转录，再以节省 Token 的长文本工作流重构成便于阅读和复习的中文 Markdown 总结。

项目由两个边界明确的部分组成：

- Python CLI 负责媒体探测、音频分离、上传、异步语音识别、结果持久化和安全清理。
- Agent Skill 负责指导兼容的编程代理调用 CLI，并基于转录结果生成自然、完整的中文 Markdown 总结。

CLI 本身不会调用通用大模型改写内容。总结重构与翻译属于 Skill 编排阶段，因此直接执行 Python 脚本仍会得到三类转录工件；通过 Skill 成功执行时，这些文件位于本次私有暂存目录，并在最终总结发布后清理。

## 核心能力

- 接受 yt-dlp 支持的单个视频 URL，以及本地音频或视频文件。
- URL 下载优先选择最佳音频流，并以最低清晰度的合并格式作为回退，避免无意义的视频与音频合并。
- 仅在视频需要分离音频或调用方要求音频标准化时使用 FFmpeg。
- 使用阿里云百炼 DashScope `paraformer-v2` 异步语音识别接口。
- 输出不包含时间戳的 Markdown 与标准化 JSON，同时保留服务商原始响应用于审计。
- 每个输出文件通过临时文件加原子替换提交；正常单进程运行会为同名结果选择 `-2`、`-3` 等后缀。
- 本地输入永不删除；只有本次运行创建且位于私有工作目录中的媒体才可能被清理。
- 支持时长与 Token 双重切片、知识锚点、证据胶囊、按需回查、非中文来源的中文化，以及成功后唯一总结交付。

## 处理流程

```text
URL / local media
        │
        ▼
dependency preflight ──► media probe
        │
        ├─ audio input ────────────────┐
        └─ video input ─► FFmpeg audio │
                         extraction    │
                                       ▼
                         DashScope temporary upload
                                       │
                                       ▼
                         Paraformer async transcription
                                       │
                                       ▼
                   sequential per-file atomic persistence
                                       │
                       validated success / failure gate
                              │                  │
                              ▼                  ▼
                   safe media cleanup     retain work directory
                              │
                              ▼
                   agent summary reconstruction workflow
                               │
                               ▼
             publish 中文标题.summary.md ─► clean private stage
```

预检在创建工作目录之前执行。媒体处理开始后，如果下载、转换、上传、识别、解析或写入失败，工作目录会保留以便诊断和重试。

## 适用范围

当前实现适合个人开发、受控批处理和 Agent 辅助的音视频知识整理，不应直接作为高并发生产转录服务部署。CLI 使用百炼临时上传接口；阿里云官方说明临时 URL 有效期为 48 小时，上传凭证接口按“主账号 + 模型”限制为 100 QPS，且该路径不适用于生产、高并发或压测场景。

生产集成至少需要替换为自有 OSS 或其他稳定存储，并补充并发锁、幂等键、退避重试、任务回调、指标、告警和配额治理。相关限制参见[阿里云百炼临时文件文档](https://help.aliyun.com/zh/model-studio/get-temporary-file-url/)。

## 系统要求

- Python 3.10 或更高版本。
- URL 输入需要 `yt-dlp` 命令，或者当前 Python 环境中的 `yt_dlp` 模块。
- 媒体探测需要 `ffprobe`。
- 视频音频分离和可选标准化需要 `ffmpeg`。
- DashScope API Key，以及可访问对应百炼服务端点的网络环境。

安装 yt-dlp：

```bash
python -m pip install --upgrade yt-dlp
```

CLI 会优先使用 `PATH` 中的 `yt-dlp`，找不到时自动回退到 `python -m yt_dlp`。

macOS：

```bash
brew install ffmpeg
```

Ubuntu 或 Debian：

```bash
sudo apt-get update
sudo apt-get install ffmpeg
```

Windows 推荐安装包含 `ffmpeg` 和 `ffprobe` 的轻量 Essentials 构建：

```powershell
winget install --id Gyan.FFmpeg.Essentials --exact
```

不需要克隆或编译 FFmpeg 源码，也不要用同名 Python 包代替 FFmpeg 可执行文件。

验证依赖：

```bash
python --version
python -m yt_dlp --version
ffmpeg -version
ffprobe -version
```

## 安装

克隆仓库：

```bash
git clone https://github.com/Sadsunset3/VidScribe.git
cd VidScribe
```

### 作为独立 CLI 使用

无需安装 Python 包，直接从仓库根目录执行脚本：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py --help
```

### 作为 Agent Skill 使用

将 `skills/video-to-transcript` 复制或链接到编程代理能够发现的 Skill 目录。Claude Code、OpenCode、ZCode、Codex 等运行时的全局目录和项目级目录可能不同，应以对应运行时的 Agent Skills 文档与本地配置为准。

该目录就是完整的通用 Skill，只包含跨代理运行所需的 `SKILL.md`、`scripts/` 和 `references/`。

支持 `.agents/skills` 约定的运行时可以使用以下项目级路径：

```text
<project>/.agents/skills/video-to-transcript
```

开发环境推荐使用目录链接，使仓库中的修改立即反映到代理加载目录。Windows PowerShell 示例：

```powershell
New-Item -ItemType Directory -Force ".\.agents\skills" | Out-Null
New-Item -ItemType Junction `
  -Path ".\.agents\skills\video-to-transcript" `
  -Target (Resolve-Path ".\skills\video-to-transcript")
```

目标路径已存在时命令会失败，从而避免静默覆盖已有 Skill。若运行时不读取 `.agents/skills`，请把链接目标调整为该运行时配置的 Skill 搜索目录。Skill 的核心行为定义在标准 `SKILL.md` 及相对路径资源中，不依赖某一个代理的专有工作区路径。

## 配置 DashScope

唯一必需的环境变量是 `DASHSCOPE_API_KEY`。

Linux 或 macOS：

```bash
export DASHSCOPE_API_KEY="<your-key>"
```

Windows PowerShell：

```powershell
$env:DASHSCOPE_API_KEY = "<your-key>"
```

可选配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DASHSCOPE_MODEL` | `paraformer-v2` | 兼容当前 Paraformer 请求参数的模型名称 |
| `DASHSCOPE_UPLOAD_URL` | `https://dashscope.aliyuncs.com/api/v1/uploads` | 临时 OSS 上传策略端点 |
| `DASHSCOPE_ASR_URL` | `https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription` | 异步识别提交端点 |
| `DASHSCOPE_TASK_URL_TEMPLATE` | `https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}` | 任务轮询端点，必须保留 `{task_id}` |
| `DASHSCOPE_POLL_INTERVAL` | `5` | 轮询间隔，单位为秒 |
| `DASHSCOPE_TIMEOUT` | `14400` | 最长等待时间，单位为秒 |

提交参数固定包含中英文提示 `language_hints=["zh", "en"]`，并关闭时间戳对齐、保留语气词。`DASHSCOPE_MODEL` 仅用于兼容这些参数的 Paraformer 模型；只修改模型名称不能把该 CLI 迁移到请求结构不同的 ASR 模型。

自定义端点、模型和 API Key 必须属于兼容的区域、工作空间与账号。密钥只从进程环境读取，不应写入仓库、命令输出或转录工件。

## 快速开始

以下命令均从仓库根目录执行。

转录单个 URL：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py "<yt-dlp-supported-video-url>" --output-dir ./transcripts
```

尖括号内容是占位符，必须替换为调用方有权处理的真实媒体地址。URL 下载固定使用单项模式。选择器为 `bestaudio/worst`：优先最佳独立音频格式，没有独立音频格式时回退到最低清晰度的合并格式。

转录本地视频：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py "/path/to/course.mp4" --output-dir ./transcripts
```

本地视频会在私有工作目录中分离为单声道、16 kHz、64 kbit/s MP3；原始视频保持不变。

转录本地音频：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py "/path/to/course.mp3" --output-dir ./transcripts
```

通过 FFprobe 验证的本地音频默认直接上传，不进行无意义的重复编码。

标准化本地音频：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py "/path/to/course.wav" --normalize-audio --output-dir ./transcripts
```

当源音频的编码或容器可能不被目标端点接受时，可以使用 `--normalize-audio` 转换为单声道 16 kHz MP3。重新编码会增加处理时间，并可能造成有损压缩。

保留本次运行生成的媒体：

```bash
python skills/video-to-transcript/scripts/video_to_transcript.py "<URL-or-local-path>" --keep-media --output-dir ./transcripts
```

`--keep-media` 适用于调试或复用下载结果。使用该选项意味着需要由调用方自行管理磁盘空间和敏感媒体。

## CLI 接口

```text
usage: video_to_transcript.py [-h] [--output-dir OUTPUT_DIR]
                              [--keep-media] [--normalize-audio]
                              source
```

| 参数 | 必需 | 说明 |
| --- | --- | --- |
| `source` | 是 | yt-dlp 支持的 URL 或本地媒体路径 |
| `--output-dir` | 否 | 输出目录，默认 `transcripts` |
| `--keep-media` | 否 | 成功后仍保留本次运行创建的媒体 |
| `--normalize-audio` | 否 | 对本地纯音频也执行 16 kHz 单声道 MP3 标准化 |

一次调用只处理一个输入，不会自动展开播放列表。

## 输出契约

CLI 成功后分别以单文件原子替换的方式写入三类文件：

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `*.transcript.md` | 无时间戳的可读转录文本 | 阅读、编辑和后续整理 |
| `*.transcript.json` | `text` 与模型、任务 ID、来源、时长等 `metadata` | 稳定的机器处理接口 |
| `*.asr.raw.json` | 未修改的服务商响应 | 审计、排错和未来重新处理 |

通过兼容编程代理执行完整 Skill 后，最终输出目录只保留：

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `中文标题.summary.md` | 无时间戳、首行与文件名语义标题一致的中文总结 | 阅读、发布或随时复习 |

正常单进程运行会为已有同名结果选择下一个可用后缀，例如 `lesson-2.transcript.md`。三个文件按顺序分别提交，不构成跨文件事务；写入中断时可能留下不完整的一组。多个进程也不应共享同一输出目录，因为当前命名检查没有文件锁，并发任务可能选择相同名称。

## 中文总结重构

默认 `economy` 模式避免第二次全文读取，工作流遵守以下约束：

1. 分片数取时长上限与 Token 预算计算结果的较大值；70 分钟至少均分为两个约 35 分钟片段。
2. 每片只完整阅读一次，同时提取一句话知识锚点和最小证据胶囊。
3. 后续只消费压缩材料；参数缺失、步骤断裂、冲突或低置信度节点才定向回查。
4. 锚点编号用于机械检查信息覆盖，发布前全部移除。
5. 最终文档按内容自然组织，并依据实际内容生成清理后的中文语义标题；同一值用于正文首行和 `.summary.md` 文件名，不使用随机来源 ID。避碰序号单独登记，`-2`、`-3` 不进入正文标题。
6. 来源主要为英文或其他非中文语言时，在事实与结构核对后执行完整的中文翻译与润色，并再次检查锚点覆盖。

完整规则参见 [`skills/video-to-transcript/references/summary-reconstruction-workflow.md`](skills/video-to-transcript/references/summary-reconstruction-workflow.md)。设计背景参见 [`docs/summary-reconstruction-workflow-design.md`](docs/summary-reconstruction-workflow-design.md)。

不同代理的调用语法并不统一。安装后应通过自然语言明确指定 Skill、输入和输出目录，例如：

```text
使用 video-to-transcript skill 处理这个课程视频：<URL>。
输出到 ./articles，转录不要时间戳，并继续整理成中文总结。
```

预期结果是一个按实际内容命名的 `中文标题.summary.md`。三个 CLI 转录工件只存在于本次私有暂存目录，最终总结通过中文化、覆盖与格式门禁后才会清理；失败时保留该目录供恢复。

## 安全模型

清理操作必须同时满足以下条件：

1. 三个 CLI 输出文件均已存在并通过验证。
2. 标准化 JSON 中包含非空转录文本。
3. 待删除媒体由当前运行创建。
4. 待删除媒体解析后的路径位于当前私有工作目录中。

因此，本地输入文件不会被删除，失败任务创建的媒体也不会在通用 `finally` 清理中消失。私有暂存目录中的 `.run-manifest.json` 以原子写入方式持久化运行 ID、所有精确路径、清理后的语义标题、避碰序号、发布候选的稳定实体身份、正文哈希和发布/清理状态。草稿、恢复材料和发布候选始终位于私有暂存目录；清单先登记尚未创建的候选路径、目标和哈希，候选再以 exclusive-create 创建并落盘，因此最终目录不会出现未登记临时文件。随后使用保留实体身份、目标存在即失败的原子 `no-clobber` 操作从暂存目录发布。崩溃恢复以设备/inode 或卷/file ID 证明创建归属，哈希只验证内容；另一进程即使发布了相同内容，只要实体不同也按碰撞选择下一个名称，绝不覆盖。Skill 的第二层清理门禁只在最终总结非空、中文标题和文件名合规、必要的翻译已完成、锚点覆盖完成且暂存路径归属验证成功后，删除本次暂存目录中的转录与重构中间产物。总结已发布但清理失败时根据清单保留总结并报告残留精确路径，不宣称完整成功；只有残留全部清除、清单和空暂存目录删除后才完成唯一文档交付。清单缺失、损坏、实体身份不可靠或归属校验失败时停止自动发布与清理，绝不猜测路径。

DashScope 临时 OSS 对象由服务商的临时存储生命周期管理。临时 URL 有效期为 48 小时，上传策略没有向本项目提供对象删除凭据，因此 CLI 只承诺清理本地运行工件。音频会离开本机并上传到云服务；调用方需要评估数据分类、所在地合规、服务条款和按量计费。

标准化 JSON 会记录原始 `source` 值。不要把包含签名参数、访问令牌或其他凭据的 URL 直接作为输入；如无法避免，应在保存或共享结果前清理该字段。本地输入还可能暴露绝对路径，因此转录工件不应默认公开。

公开仓库默认忽略 `.env`、媒体文件、转录结果、ASR 原始响应和私有工作目录。提交前仍应使用专门的密钥扫描工具检查暂存区和提交历史。

## 限制

- 单个待上传音频的大小上限为 1 GiB。
- 三个 CLI 文件不是事务性工件组，并发任务不能安全地共享同一输出目录。
- URL 能否下载取决于站点支持、访问权限、区域限制和 yt-dlp 兼容性。
- 受 DRM 保护、需要额外授权或没有音轨的媒体不在支持范围内。
- 音频质量、噪声、口音、专业术语和模型能力都会影响识别准确率。
- 时间戳对齐被明确禁用；需要字幕定位或逐句时间轴的场景应使用其他工作流。
- 使用者必须拥有下载和处理源内容的权限，并遵守来源站点及云服务条款。

## 故障处理

| 错误或现象 | 检查方向 |
| --- | --- |
| `missing required programs` | 安装提示中的可执行文件，并检查 `PATH`；yt-dlp 也可通过当前 Python 模块提供 |
| `media has no audio stream` | 确认源文件包含音轨，并检查站点是否暴露了独立或受保护的媒体流 |
| `temporary upload policy response has no data` | 检查账号权限、API Key、区域和上传策略端点 |
| `task succeeded but returned no transcription_url` | 检查服务商控制台中的任务；此失败发生在最终结果下载之前，因此不会生成本次运行的 `*.asr.raw.json` |
| `transcription result is empty` | 检查音轨是否有效；空结果不会触发成功清理 |
| 任务超时 | 调整 `DASHSCOPE_TIMEOUT`，检查网络和服务端任务状态 |

失败信息会指出保留的 `.video-to-transcript-work-*` 目录。完成诊断或重试前不要删除该目录。

## 项目结构

```text
VidScribe/
├─ skills/video-to-transcript/
│  ├─ SKILL.md                  # Agent 工作流与安全约束
│  ├─ scripts/                  # 确定性转录 CLI
│  └─ references/               # 配置与总结重构规则
├─ docs/                        # 设计与实施记录
└─ transcripts/                 # 默认本地输出；不进入版本控制
```

## 许可证

仓库当前未包含开源许可证。公开可读不等于授予复制、修改、分发或商用许可；在许可证明确之前，默认保留全部权利。维护者若希望接受外部复用和贡献，应先选择并提交适合项目目标的许可证。
