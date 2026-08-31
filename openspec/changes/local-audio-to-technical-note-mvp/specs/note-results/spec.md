## Purpose

定义用户如何查看、复制和导出带时间戳转录及技术课程 Markdown，并保证导出结果包含必要来源、处理和模型元数据。

## ADDED Requirements

### Requirement: 查看和复制转录
系统 SHALL 按时间顺序展示标准化转录，每段显示开始时间，并 SHALL 支持复制全部转录。

#### Scenario: 打开已完成转录
- **WHEN** 用户打开已有标准化转录的任务详情
- **THEN** 系统按句段序号展示开始时间和文本，并提供复制全部操作

### Requirement: 导出转录文本
系统 SHALL 支持将转录导出为纯文本或带时间戳文本，且导出失败不得破坏已保存的转录。

#### Scenario: 导出带时间戳转录
- **WHEN** 用户选择目标路径并导出带时间戳文本
- **THEN** 系统写入按时间顺序排列的时间戳和句段文本

### Requirement: Markdown 源码和预览
系统 SHALL 同时提供 Markdown 源码视图与安全渲染预览，并 SHALL 支持复制完整 Markdown。

#### Scenario: 打开完成的笔记
- **WHEN** 用户打开已生成笔记的任务详情
- **THEN** 用户可以在源码与预览之间切换并复制源码

### Requirement: 导出 Markdown
系统 SHALL 通过用户选择的保存位置导出 `.md` 文件，默认文件名采用“内容标题-技术课程文档.md”，并 SHALL 防止非法文件名字符造成路径越界或覆盖非目标文件。

#### Scenario: 使用默认文件名导出
- **WHEN** 用户导出标题为“Spring Boot 事务”的技术课程笔记
- **THEN** 保存对话框默认建议文件名“Spring Boot 事务-技术课程文档.md”

### Requirement: 笔记元数据
导出的 Markdown SHALL 在头部记录标题、原始文件名、处理时间、预设和实际模型名，并 MUST NOT 包含任何密钥、签名 URL 或本机绝对路径。

#### Scenario: 检查导出文档头部
- **WHEN** 用户打开成功导出的 Markdown
- **THEN** 文档包含可读元数据和正文，且不包含敏感凭据或本地绝对路径
