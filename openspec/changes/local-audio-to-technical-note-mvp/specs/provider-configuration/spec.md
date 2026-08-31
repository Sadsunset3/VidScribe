## Purpose

定义 OSS、ASR 和 LLM 服务配置、连接测试、任务配置快照与敏感凭据保护，使用户可以替换服务地址和模型而不泄露长期密钥。

## ADDED Requirements

### Requirement: 配置三类云端服务
系统 SHALL 提供 OSS、ASR 和 LLM 配置，并 SHALL 允许用户修改各自的服务地址、凭据和相关模型或存储参数。

#### Scenario: 保存完整配置
- **WHEN** 用户填写有效的 OSS endpoint、bucket 和凭据，ASR Base URL、API Key、模型名，以及 LLM Base URL、API Key、模型名和生成参数
- **THEN** 系统保存非敏感配置并将秘密保存到系统安全凭据存储

### Requirement: 默认模型值
ASR 模型名 SHALL 默认为 `paraformer-v2`，LLM 模型名 SHALL 默认为 `qwen3.8-flash`，用户 MUST 能在设置中修改这两个值。

#### Scenario: 首次打开设置
- **WHEN** 应用尚未保存模型配置
- **THEN** 设置页显示默认模型名且不预填任何 API Key

### Requirement: 连接测试
系统 SHALL 为 OSS、ASR 和 LLM 分别提供不会创建正式处理任务的连接测试，并 SHALL 返回脱敏的成功或失败说明。

#### Scenario: 测试有效配置
- **WHEN** 用户触发某项服务的连接测试且服务可访问、凭据有效
- **THEN** 系统显示该服务连接成功，不创建音频对象、ASR 任务或笔记任务

#### Scenario: 测试失败
- **WHEN** 服务不可达或凭据无效
- **THEN** 系统显示可操作的脱敏错误且不回显请求密钥

### Requirement: 密钥不可回显和记录
系统 MUST NOT 在设置页、普通日志、错误堆栈、任务数据库或导出数据中返回完整 API Key、Access Key Secret、Authorization 请求头或完整签名 URL。

#### Scenario: 重新打开已保存配置
- **WHEN** 用户保存密钥后再次打开设置页
- **THEN** 页面只显示已配置状态和掩码，不显示可恢复的明文

#### Scenario: 云端请求失败并记录日志
- **WHEN** 带凭据的云端请求产生异常
- **THEN** 日志保留请求类型和请求 ID，但敏感字段被移除或掩码

### Requirement: 缺失配置时阻止任务调用
系统 SHALL 在创建或恢复任务前校验所需服务配置，缺少必要配置时 MUST 在任何计费调用前停止。

#### Scenario: 未配置 OSS 凭据
- **WHEN** 用户尝试创建笔记任务但 OSS 凭据不存在
- **THEN** 系统引导用户完成设置且不读取上传音频、不调用 ASR 或 LLM
