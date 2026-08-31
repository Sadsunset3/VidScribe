## Context

参见 `proposal.md` 的 Why。当前目录没有应用代码或既有数据库，只有 V1.0 PRD 和技术课程笔记样例。本变更从零建立 Windows 桌面端最小闭环。它需要协调本地文件读取、远端临时对象、长音频异步 ASR、长上下文 LLM、持久化任务、凭据保护和应用重启恢复，同时保证不修改用户原始音频、不重复计费和不泄露密钥。

本机已有 JDK 21、Maven 3.9.11、Node.js 24 和 OpenSpec 1.10.0，但没有 Rust 工具链。默认云端服务为阿里云 OSS、百炼 `paraformer-v2` 和 `qwen3.8-flash`；模型名称与 Base URL 必须可配置。

## Goals / Non-Goals

**Goals:**

- 交付可安装、可恢复的 Windows 单机桌面应用，并打通本地音频到 Markdown 的完整链路。
- 用稳定的应用层状态机隔离 UI、外部 Provider、持久化和文件生命周期。
- 将云端响应归一化为厂商无关领域结构，使后续替换 ASR 或 LLM 不改变任务编排。
- 在真实云端调用之前覆盖主要失败路径，并确保凭据、签名 URL 和用户原始文件受到保护。

**Non-Goals:**

- 本变更不实现 PRD 中的 URL 下载、本地视频、FFmpeg 转码、媒体提取模式、播客预设或多任务并行。
- 不实现转录编辑、Prompt 编辑、多轮 Agent、转录分块合并、向量库、自动升级和跨平台安装包。
- 不承诺绕过云服务自身的音频大小、时长、地域或模型上下文限制。

## Decisions

### 1. 使用 Java 21、JavaFX 和 Maven 构建单进程桌面应用

应用采用 Java 21 模块化工程，JavaFX 负责页面和状态绑定，Maven 负责依赖与测试，`jlink`/`jpackage` 生成包含运行时的 Windows 安装包。耗时操作只在有界后台执行器中运行，所有 UI 更新通过 JavaFX Application Thread 派发。

选择该方案是因为现有环境已具备 JDK 21 和 Maven，Java 的 HTTP、并发、SQLite 与进程管理能力足以覆盖完整 PRD。Tauri 体积更小但会引入 Rust、MSVC 和 WebView2 构建链；Electron 的 UI 生态成熟但会增加 Chromium 体积和 IPC 安全面。这两种方案均不利于本次最小闭环。

### 2. 按功能边界组织代码，并以应用层状态机作为唯一编排入口

工程划分为 `app`、`task`、`media`、`hosting`、`asr`、`note`、`configuration`、`result` 和 `shared` 功能包。每个外部系统通过端口接口接入；JavaFX Controller 只能调用应用服务，不能直接调用 OSS、ASR、LLM 或 JDBC。

每次任务由持久化状态机串行推进。阶段为 `PENDING`、`VALIDATING_MEDIA`、`UPLOADING_AUDIO`、`TRANSCRIBING`、`NORMALIZING_TRANSCRIPT`、`CLEANING_REMOTE_AUDIO`、`GENERATING_NOTE`、`COMPLETED`，并带有终止状态 `FAILED`、`CANCELLED`、`INTERRUPTED`。数据库事务先记录阶段意图和幂等标识，再执行外部调用，完成后记录结果，避免应用崩溃造成重复提交。

### 3. 使用 SQLite 保存任务事实，使用系统凭据保存秘密

SQLite 保存媒体元数据、任务、阶段事件、冻结的非敏感配置快照、云端任务 ID、OSS 对象键、原始 ASR/LLM 响应、标准化转录和 Markdown。数据库使用显式 schema version 和启动迁移，不使用重量级 ORM；DAO 通过 JDBC 实现并在事务边界内更新状态与结果。

API Key、OSS Access Key Secret 等秘密通过 JNA 写入 Windows Credential Manager。SQLite 只保存稳定的凭据别名，UI 读取配置时只得到 `configured=true/false` 和掩码。日志过滤器移除 Authorization、API Key、签名查询参数和完整临时 URL。

选择 Credential Manager 而非自建主密码加密文件，是因为 MVP 仅支持 Windows，系统凭据可避免应用自行管理密钥加密材料。

### 4. 本地音频只读处理，媒体识别依赖受控探测器

文件选择器只展示 MP3、M4A、WAV，但创建任务前仍读取真实媒体信息验证存在可解码音轨、时长大于零和格式受支持。首版捆绑 `ffprobe` 作为只读探测 sidecar，不调用 FFmpeg 转码，也不创建用户音频工作副本。上传器以只读流读取原文件；任务取消、失败和删除均不得删除或改写该文件。

捆绑探测器比仅依赖扩展名可靠，也为后续视频和 FFmpeg 模块复用同一媒体元数据结构。若安装包中缺少或无法执行探测器，应用在创建云端任务前明确失败。

### 5. 临时托管先实现阿里云 OSS，保留 Provider 接口

`TemporaryAudioHost` 的领域契约只包含上传、创建短期只读 HTTPS URL 和删除。首个适配器使用阿里云 OSS Java SDK，配置 endpoint、region、bucket、对象前缀、Access Key ID 和 Secret。对象键由任务 UUID 与随机值生成，Bucket 必须保持私有；签名 URL 默认有效 6 小时，可配置范围限制在 1 至 24 小时。

上传完成后保存对象键，不持久化签名 URL。ASR 成功且原始响应与标准化转录都提交到 SQLite 后立即删除对象。删除失败形成清理警告并进入延迟重试队列；失败任务的对象最长保留 24 小时，应用启动时扫描并清理过期对象。

相比在桌面端启动公网文件服务器，OSS 能稳定提供云端可访问 HTTPS 地址且不暴露用户本机端口。其他 S3 兼容存储留待后续适配器实现。

### 6. ASR 使用异步 Provider 与可恢复轮询

`AsrProvider` 对外提供提交、查询和结果解析能力。百炼适配器默认模型为 `paraformer-v2`，提交时使用临时签名 URL，并请求句级时间戳。首次提交成功后立即持久化云端任务 ID；后续只按该 ID 轮询，不重新提交。

轮询使用可配置间隔和总超时。HTTP 429、连接失败和 5xx 使用带抖动的指数退避，单次操作最多自动重试三次；401/403、无效参数和不可解析响应立即失败。总超时后任务保留云端 ID，用户的“继续查询”操作只恢复轮询。原始响应与统一的 `TranscriptSegment(index, beginTimeMs, endTimeMs, text, speakerId)` 在同一数据库事务中保存。

### 7. LLM 通过 OpenAI 兼容接口单次生成技术课程笔记

`LlmProvider` 接收冻结的模型配置、课程元数据、顺序化句段和固定技术课程预设。默认调用 `qwen3.8-flash` 的 OpenAI 兼容接口，关闭或降低思考强度。Prompt 要求保持课堂顺序，提取问题、铺垫、转折、误区和理解模型，用 `[回看 HH:MM:SS]` 标出重要节点，并将 AI 补充放入独立区域；不得虚构老师展示过的代码、数据或结论。

MVP 采用一次请求，不做分块和多轮分析。调用前按 UTF-8 文本量进行保守输入预算；超过用户配置的最大上下文预算时停止并保留转录，提示后续版本才支持分块。空响应、仅代码围栏或无法作为 Markdown 展示的结果视为生成失败，原始响应被保存以供排查。

选择单次长上下文调用是为了减少首版编排复杂度和内容顺序重排风险；`qwen3.8-flash` 当前支持长上下文，但具体地域和额度仍由连接测试与运行时错误明确反馈。

### 8. JavaFX 单窗口提供四个最小页面

左侧导航包含新建任务、任务列表、任务详情和设置。任务详情在同一页面提供阶段时间线、错误与可执行动作、转录视图、Markdown 源码和基于 Flexmark 的 HTML 预览。用户可以复制转录或 Markdown，并通过系统保存对话框导出 `.txt` 或 `.md`。

任务执行器一次只运行一个任务；重复点击创建按钮由 UI 禁用和数据库幂等键双重阻止。关闭窗口会停止执行器并把活动任务标为 `INTERRUPTED`。下次启动只提示恢复，不自动产生云端费用。

### 9. 测试以可重复的模拟服务为主，真实服务只做人工冒烟

JUnit 5 单元测试覆盖状态迁移、幂等、时间戳归一化、Prompt、输入预算、文件名清理和日志脱敏。集成测试使用本地 HTTP 模拟服务器与临时 SQLite 数据库，覆盖成功、429、5xx、鉴权失败、轮询超时、异常 JSON、清理失败和应用重启恢复。UI 测试只覆盖关键 Controller/ViewModel 状态，不对像素布局做脆弱断言。

真实 OSS、Paraformer 和 Qwen 测试需要用户凭据且会产生费用，因此作为发布前 Windows 冒烟清单执行，不进入默认 Maven 测试。

## Risks / Trade-offs

- [长音频可能超过模型上下文或请求限制] → 调用前预算并明确失败，保留完整转录；分块生成作为后续独立变更。
- [应用退出时云端 ASR 仍在运行] → 持久化云端任务 ID，恢复时只查询既有任务，不重复提交。
- [OSS 删除失败造成隐私和费用风险] → 记录对象键、显示清理警告、启动时重试并执行 24 小时过期扫描。
- [签名 URL 可能通过异常或调试日志泄露] → 请求日志默认不记录查询串，统一脱敏并只持久化对象键。
- [Windows Credential Manager 增加平台绑定] → 通过 `SecretStore` 接口隔离；这是 Windows-only MVP 的可接受取舍。
- [捆绑 ffprobe 和 Java 运行时增加安装包体积] → 只捆绑 Windows x64 所需文件，并以自包含安装换取一致运行环境。
- [单任务串行降低吞吐] → MVP 优先保证幂等、恢复和费用可预测性；并发队列留待后续版本。

## Migration Plan

1. 首次启动创建应用数据目录、SQLite 数据库和 schema version 1，不扫描或迁移用户其他目录。
2. 用户在设置页逐项保存并测试 OSS、ASR、LLM 配置后才能创建任务。
3. 发布包使用独立应用 ID 与数据目录；回滚时卸载应用不会自动删除用户数据库或导出文件。
4. 若 schema 1 初始化失败，应用不执行云端调用，并提供数据库路径和可复制的脱敏错误。
