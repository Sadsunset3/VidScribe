# Technical document workflow

## Priority and source boundary

Apply this order when requirements conflict:

`用户任务 > 事实准确 > 技术完整性 > 风格约束`

Use normalized transcript `text` as the primary source. Distinguish direct
statements, speaker opinions, verified facts, and editor inferences. Verify
version-sensitive commands, APIs, parameters, and product behavior against
official primary documentation when available. Mark necessary but unresolved
material as `待核对`; never make uncertainty sound certain.

Consume normalized JSON with top-level `text` and `metadata`. Preserve the
transcript and raw ASR artifacts as the factual audit trail; generate a
complete timestamp-free `*.technical.md` only from supported material. Do not
include timestamps in the final technical-document body.

## Document recipe

Organize the document in this exact reader-understanding order:

1. 背景：说明读者的问题，以及旧方式为什么不够。
2. 定义：先用一句白话解释，再给出正式术语、英文和缩写。
3. 组成：说明关键模块、角色及其关系。
4. 过程：复杂流程用编号步骤，每一步只完成一个动作。
5. 实例：只使用转写或可靠资料支持的实例、代码和命令。
6. 限制：说明适用条件、缺点、风险和版本边界。
7. 总结：收束核心理解，并给出一个可继续尝试的方向。

Every code block or command must be followed immediately by an explanation of
its key parameters, expected or observed result, and practical meaning. Use
“observed” only when execution evidence exists; otherwise describe an
unverified outcome as an expected result.

## Long-transcript chunking contract

- Enable chunking above about 8000 Chinese characters or one third of usable context.
- Target 6000～8000 Chinese characters per block, but cap the target at one third of usable context when that bound is smaller.
- Prefer paragraph, sentence, topic, and chapter boundaries.
- Never split a sentence, fenced code block（代码块）, command（命令）, or continuous operation steps（操作步骤）.
- 不机械复制固定长度的重叠文本。
- Carry a compact 主题状态 between blocks.
- Extract a 事实卡 per block before drafting.
- Track source as 文本块编号 + 原文段落编号.
- Merge fact cards by topic, reconnect steps by dependency, preserve conflicts as 待核对, then back-check important claims against source.

The compact 主题状态 records the current topic, the current process step,
unexplained terms, and unresolved questions. It provides continuity but never
replaces source text.

Before drafting, extract a 事实卡 for each block with these fields:

- 读者问题
- 旧方式不足
- 专业概念及原文定义
- 组成部分和相互关系
- 操作步骤及先后依赖
- 代码、命令、参数和讲者描述的结果
- 实例
- 适用条件、限制和风险
- 讲者观点
- 待核对事项
- 来源位置（文本块编号 + 原文段落编号）

Merge fact cards by topic rather than concatenating summaries. Reconnect
steps according to dependencies while preserving operation order. Retain every
independent parameter, condition, and exception. When sources conflict,
preserve the conflict as `待核对`; do not silently decide it. Back-check each
important claim, parameter, command, and code block against its recorded
source location.

## Technical Humanizer pass

Perform a minimal positive editing pass that preserves meaning and technical
detail. Remove empty introductions, fake authority, unsupported numbers, false
“不是 A 而是 B” contrasts, mechanical connectors, repeated adverbs,
promotional adjectives, and vague tool names. Prefer specific tools,
parameters, actions, results, and supported reasoning over decorative wording.

The following exclusions resolve any conflict with the editing pass:

- 不提供文风切换。
- 不得虚构作者立场。
- 不得虚构经历、数据、代码、引用或例子。
- 不强制禁用技术标点、列表、小标题或必要的三项结构。
- 不为了“活人感”加入网络口头禅、粗俗表达或故意混乱。
- 不输出 AI 味等级、修改统计或 Humanizer 质检报告。

Do not add first-person emotion, anecdotes, self-deprecation, or a speaker
position that the source does not establish. Do not alter numbers for impact.
Do not remove necessary punctuation, lists, headings, or technical detail just
to make prose feel less regular.

## Publishing and QA

Derive `source-id.technical.md` from `source-id.transcript.json`. Check for a
collision before publishing. If the chosen path exists, select
`source-id-2.technical.md`, then `source-id-3.technical.md`, and continue with
the next available numeric suffix; never overwrite an existing document.

Write the draft to a sibling temporary file. Replace the selected final path
only after the temporary draft is non-empty. If drafting fails, retain all
transcript artifacts and report a partial-success state rather than deleting
or overwriting them.

Before publishing, revise the body using these user-facing checks:

- Does the opening establish the reader problem before introducing concepts?
- Does every first-use technical term include a plain-language explanation and then its formal term, English name, or abbreviation when applicable?
- Does the document advance in the required reader-understanding order?
- Does each complex process use numbered steps with one action per step?
- Does the document state applicable conditions, limits, risks, or version boundaries rather than only advantages?
- Does the conclusion restate the core understanding and give a concrete next direction to try?
- Are speaker opinions attributed as opinions instead of presented as established facts?
- Does every command-result claim avoid declaring success unless execution evidence supports an observed result?

These checks revise the document body. Do not emit them as a separate report,
AI-quality rating, modification statistic, or Humanizer QA report.
