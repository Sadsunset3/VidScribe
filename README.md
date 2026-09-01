# VidScribe

VidScribe 将课程视频或本地音视频转换为无时间戳转录，并继续整理成面向读者的技术文档。

## 目录结构

```text
VidScribe/
├─ skills/
│  └─ video-to-transcript/   # Git 管理的 Skill 源码
├─ docs/
│  ├─ technical-document-workflow-design.md
│  └─ plans/
│     └─ technical-document-workflow.md
├─ transcripts/              # 本地生成结果，不提交到 Git
├─ .agents/                  # 项目级 Agent/Skill 配置
├─ .gitignore
└─ README.md
```

`skills/video-to-transcript` 是唯一应当修改和提交的 Skill 源码目录。Codex 实际加载的目录通常是：

```text
%USERPROFILE%\.codex\skills\video-to-transcript
```

修改源码并通过测试后，再将源码同步到安装目录。不要反向把安装目录当作长期维护的主副本，否则容易出现项目源码与实际运行版本不一致的问题。

## 验证 Skill

在仓库根目录执行：

```powershell
python -B -m unittest discover -s .\skills\video-to-transcript\tests -q
```

`-B` 禁止生成 Python 字节码缓存；`discover` 会查找并运行 Skill 的全部单元测试。成功时会显示测试数量和 `OK`。

继续运行长文本静态评估：

```powershell
python -B -S .\skills\video-to-transcript\evals\long-transcript-static\run_evaluation.py --json
```

`-S` 隔离第三方站点包影响，`--json` 输出便于检查的结构化结果。该评估验证固定测试工件的切块、事实卡、代码块完整性、跨块步骤和事实去重，不代表开放式模型质量基准。

## 本地输出

转录、ASR 原始响应和技术文档默认保存在 `transcripts/`。这些文件可能体积较大，也可能包含课程内容，因此已被 `.gitignore` 排除，不会随公开仓库推送。

