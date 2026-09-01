# Transcript-to-Technical-Document Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `video-to-transcript` so a successful transcription is followed by a fact-grounded, timestamp-free `*.technical.md` document instead of stopping at raw text.

**Architecture:** Keep the Python CLI responsible for deterministic media and ASR work. Put document organization, long-transcript chunking, fact checking, and Humanizer-compatible editing in a focused Skill reference that the agent follows after the CLI succeeds. Preserve all three transcript artifacts as audit inputs and publish the technical document as a fourth, non-overwriting output.

**Tech Stack:** Agent Skill Markdown, Python 3 standard-library `unittest`, existing Paraformer CLI, Markdown output.

**Spec:** `C:\Users\人员\.codex\skills\video-to-transcript-workspace\docs\2026-08-31-technical-document-workflow-design.md`

## Global Constraints

- Priority is exactly `用户任务 > 事实准确 > 技术完整性 > 风格约束`.
- Final documents contain no transcript timestamps.
- No style-switch interface or style variants.
- The CLI does not call a second LLM and keeps its current transcription responsibilities.
- The installed skill must not depend on `C:\Users\人员\Downloads` or another machine-specific path.
- Humanizer guidance may remove empty AI-like phrasing but may not invent facts, code, numbers, citations, experiences, or author opinions.
- Technical documents use the order 背景、定义、组成、过程、实例、限制、总结.
- Existing transcript and raw ASR files are never overwritten or deleted by document-generation failure.
- Existing `C:\Users\人员\.codex\skills\video-to-transcript-workspace\skill-snapshot` remains the pre-change baseline.
- The target skill workspace is not a Git repository; verification checkpoints replace commit steps.

---

## File Map

- Create `C:\Users\人员\.codex\skills\video-to-transcript\references\technical-document-workflow.md`: complete portable contract for facts, structure, long-text chunking, Humanizer adaptation, publishing, and QA.
- Modify `C:\Users\人员\.codex\skills\video-to-transcript\SKILL.md`: route every successful transcription into the technical-document phase and report the fourth output.
- Modify `C:\Users\人员\.codex\skills\video-to-transcript\tests\test_video_to_transcript.py`: regression tests for workflow continuation and reference requirements.
- Modify `C:\Users\人员\.codex\skills\video-to-transcript\agents\openai.yaml`: describe the end-to-end result instead of transcription-only behavior.
- Create `D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.technical.md`: live validation artifact generated from the existing normalized transcript.

## Task 1: Lock the End-to-End Skill Contract with Failing Tests

**Files:**
- Modify: `C:\Users\人员\.codex\skills\video-to-transcript\tests\test_video_to_transcript.py`
- Test: `C:\Users\人员\.codex\skills\video-to-transcript\tests\test_video_to_transcript.py`

**Interfaces:**
- Consumes: installed Skill files as UTF-8 text.
- Produces: documentation-contract tests used by Tasks 2 and 3.

- [ ] **Step 1: Add a reference loader to `DocumentationTests`**

```python
def _skill_text(self, relative_path):
    skill_root = Path(__file__).resolve().parents[1]
    return (skill_root / relative_path).read_text(encoding="utf-8")
```

- [ ] **Step 2: Add a failing workflow-continuation test**

```python
def test_skill_continues_from_transcript_to_technical_document(self):
    instructions = self._skill_text("SKILL.md")

    self.assertIn("references/technical-document-workflow.md", instructions)
    self.assertIn("*.technical.md", instructions)
    self.assertNotIn("note generation is a separate downstream step", instructions)
```

- [ ] **Step 3: Add failing priority and structure tests**

```python
def test_technical_document_reference_has_fixed_priority_and_structure(self):
    reference = self._skill_text("references/technical-document-workflow.md")

    self.assertIn("用户任务 > 事实准确 > 技术完整性 > 风格约束", reference)
    positions = [reference.index(name) for name in (
        "背景", "定义", "组成", "过程", "实例", "限制", "总结"
    )]
    self.assertEqual(positions, sorted(positions))
```

- [ ] **Step 4: Add failing long-transcript tests**

```python
def test_long_transcript_contract_preserves_semantic_units(self):
    reference = self._skill_text("references/technical-document-workflow.md")

    for required in (
        "6000～8000", "代码块", "命令", "操作步骤",
        "主题状态", "事实卡", "文本块编号", "原文段落编号",
    ):
        self.assertIn(required, reference)
    self.assertIn("不机械复制固定长度的重叠文本", reference)
```

- [ ] **Step 5: Add failing Humanizer conflict tests**

```python
def test_humanizer_rules_cannot_override_technical_accuracy(self):
    reference = self._skill_text("references/technical-document-workflow.md")

    for forbidden_behavior in (
        "不得虚构作者立场", "不得虚构经历", "不强制禁用技术标点",
        "不输出 AI 味等级", "不提供文风切换",
    ):
        self.assertIn(forbidden_behavior, reference)
```

- [ ] **Step 6: Add failing publishing and partial-success tests**

```python
def test_document_publishing_preserves_transcript_artifacts(self):
    instructions = self._skill_text("SKILL.md")

    self.assertIn("-2", instructions)
    self.assertIn("partial success", instructions)
    self.assertIn("Never delete or overwrite transcript outputs", instructions)
```

- [ ] **Step 7: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest discover -s "C:\Users\人员\.codex\skills\video-to-transcript\tests" -v
```

Expected: the new tests fail because `references/technical-document-workflow.md`, `*.technical.md`, and the continuation contract do not exist. Existing media and ASR tests continue to pass.

## Task 2: Add the Portable Technical-Writing Reference

**Files:**
- Create: `C:\Users\人员\.codex\skills\video-to-transcript\references\technical-document-workflow.md`
- Test: `C:\Users\人员\.codex\skills\video-to-transcript\tests\test_video_to_transcript.py`

**Interfaces:**
- Consumes: normalized JSON with top-level `text` and `metadata`.
- Produces: a complete instruction contract for generating `*.technical.md`.

- [ ] **Step 1: Create the reference with the fixed priority and source boundary**

Start the file with this exact contract:

```markdown
# Technical document workflow

## Priority and source boundary

Apply this order when requirements conflict:

`用户任务 > 事实准确 > 技术完整性 > 风格约束`

Use normalized transcript `text` as the primary source. Distinguish direct
statements, speaker opinions, verified facts, and editor inferences. Verify
version-sensitive commands, APIs, parameters, and product behavior against
official primary documentation when available. Mark necessary but unresolved
material as `待核对`; never make uncertainty sound certain.
```

- [ ] **Step 2: Add the seven-section document recipe**

The reference must define, in order:

```markdown
1. 背景：说明读者的问题，以及旧方式为什么不够。
2. 定义：先用一句白话解释，再给出正式术语、英文和缩写。
3. 组成：说明关键模块、角色及其关系。
4. 过程：复杂流程用编号步骤，每一步只完成一个动作。
5. 实例：只使用转写或可靠资料支持的实例、代码和命令。
6. 限制：说明适用条件、缺点、风险和版本边界。
7. 总结：收束核心理解，并给出一个可继续尝试的方向。
```

Immediately after the recipe, require every code block or command to be followed by explanations of key parameters, expected or observed result, and practical meaning. Use “observed” only when execution evidence exists.

- [ ] **Step 3: Add the long-transcript chunking contract**

Specify these exact behaviors:

```markdown
- Enable chunking above about 8000 Chinese characters or one third of usable context.
- Target 6000～8000 Chinese characters per block.
- Prefer paragraph, sentence, topic, and chapter boundaries.
- Never split a sentence, fenced code block, command, or continuous operation steps.
- Do not mechanically copy fixed overlap text.
- Carry a compact 主题状态 between blocks.
- Extract a 事实卡 per block before drafting.
- Track source as 文本块编号 + 原文段落编号.
- Merge fact cards by topic, reconnect steps by dependency, preserve conflicts as 待核对, then back-check important claims against source.
```

List the fact-card fields from the spec: reader problem, old-method limitation, concepts, components, steps, code/commands/parameters/results, examples, constraints/risks, speaker opinions, unresolved items, and source location.

- [ ] **Step 4: Add the adapted Humanizer pass**

The positive editing pass must say to remove empty introductions, fake authority, unsupported numbers, false “不是 A 而是 B” contrasts, mechanical connectors, repeated adverbs, promotional adjectives, and vague tool names.

The conflict exclusions must contain these exact statements:

```markdown
- 不提供文风切换。
- 不得虚构作者立场。
- 不得虚构经历、数据、代码、引用或例子。
- 不强制禁用技术标点、列表、小标题或必要的三项结构。
- 不为了“活人感”加入网络口头禅、粗俗表达或故意混乱。
- 不输出 AI 味等级、修改统计或 Humanizer 质检报告。
```

- [ ] **Step 5: Add publishing and QA instructions**

Require deriving `source-id.technical.md` from `source-id.transcript.json`, checking for collisions, and selecting `source-id-2.technical.md`, `source-id-3.technical.md`, and so on. Require writing a sibling temporary file and replacing the chosen path only after the draft is non-empty.

Include all six user checks plus checks for speaker-opinion attribution and unexecuted command-result claims. State that checks revise the body and do not appear as a separate report.

- [ ] **Step 6: Run focused tests and verify the reference tests pass**

Run:

```powershell
python -m unittest discover -s "C:\Users\人员\.codex\skills\video-to-transcript\tests" -v
```

Expected: reference-specific tests pass; workflow-continuation tests still fail until Task 3.

## Task 3: Continue the Skill Workflow Through Document Publication

**Files:**
- Modify: `C:\Users\人员\.codex\skills\video-to-transcript\SKILL.md`
- Modify: `C:\Users\人员\.codex\skills\video-to-transcript\agents\openai.yaml`
- Test: `C:\Users\人员\.codex\skills\video-to-transcript\tests\test_video_to_transcript.py`

**Interfaces:**
- Consumes: CLI paths for Markdown transcript, normalized JSON, and raw ASR JSON.
- Produces: published `*.technical.md` plus a four-path final report.

- [ ] **Step 1: Update frontmatter discovery text**

Use a trigger-only description that includes course-to-document requests without summarizing implementation details:

```yaml
description: Use when a user asks to download or transcribe a course video, convert video or audio into technical documentation, call Bailian/DashScope Paraformer, or safely process temporary transcription media.
```

- [ ] **Step 2: Replace the transcript-only stopping rule**

After CLI success, require the agent to:

```markdown
1. Read `references/technical-document-workflow.md` completely.
2. Load `text` and `metadata` from normalized JSON.
3. Use direct organization for short text or the reference's chunk/fact-card workflow for long text.
4. Verify unstable technical claims with official primary sources when access is available.
5. Draft, self-check, and publish the non-overwriting `*.technical.md` file.
6. Keep the three transcript artifacts unchanged.
```

Remove the sentence saying note generation is a separate downstream step.

- [ ] **Step 3: Define failure boundaries**

Add this contract:

```markdown
If transcription fails, retain run-created media under the existing deletion gate.
If transcription succeeds but document organization fails, report partial success,
keep all transcript outputs, and retain any document draft for retry. Never delete
or overwrite transcript outputs during document organization.
```

- [ ] **Step 4: Update output reporting**

Return the technical document first, followed by readable transcript, normalized JSON, and raw ASR JSON. Explain that a pre-existing technical document causes `-2`, `-3`, and later suffixes rather than overwrite.

- [ ] **Step 5: Update `agents/openai.yaml`**

Use:

```yaml
interface:
  display_name: "Video to Technical Document"
  short_description: "将课程音视频转写并整理为事实可靠、无时间戳的技术文档"
  default_prompt: "Use $video-to-transcript to transcribe this media without timestamps, organize it into a fact-grounded technical document, and safely clean run-created media after success."
```

Preserve `policy.allow_implicit_invocation: true`.

- [ ] **Step 6: Run the complete unit suite and verify GREEN**

Run:

```powershell
python -m unittest discover -s "C:\Users\人员\.codex\skills\video-to-transcript\tests" -v
```

Expected: all existing and new tests pass with zero failures and zero errors.

## Task 4: Generate and Review a Real Vue 3 Technical Document

**Files:**
- Read: `D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.transcript.json`
- Read when needed: `D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.asr.raw.json`
- Create: `D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.technical.md`

**Interfaces:**
- Consumes: verified normalized transcript from the live Paraformer run.
- Produces: the first user-visible technical document proving the extended workflow.

- [ ] **Step 1: Confirm source validity before drafting**

Run:

```powershell
$path = 'D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.transcript.json'
$data = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace([string]$data.text)) { throw 'empty transcript' }
"chars=$(([string]$data.text).Length)"
"source=$($data.metadata.source)"
```

Expected: non-zero character count and the authorized Bilibili page-2 URL.

- [ ] **Step 2: Apply the short- or long-text route**

If the transcript is at most 8000 Chinese characters, organize it directly. Otherwise follow the chunk, topic-state, fact-card, merge, and source-backcheck sequence from `technical-document-workflow.md`.

- [ ] **Step 3: Draft all seven sections**

Write a Markdown document that begins with the practical problem from the lesson, explains each first-use technical term in plain language before its formal name, preserves only source-supported commands or code, describes limitations, and ends with one next experiment.

- [ ] **Step 4: Publish without overwrite**

Check whether the planned target exists. If it does, select the next numeric suffix. Write the complete draft to `<target>.tmp`, verify it is non-empty, then replace only the chosen target path.

- [ ] **Step 5: Run content assertions**

Run:

```powershell
$doc = 'D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.technical.md'
$text = Get-Content -LiteralPath $doc -Raw
foreach ($heading in '背景','定义','组成','过程','实例','限制','总结') {
  if ($text -notmatch [regex]::Escape($heading)) { throw "missing section: $heading" }
}
if ($text -match '(?m)^\[\d{2}:\d{2}:\d{2}\]') { throw 'timestamp found' }
if ($text -match 'AI味检测报告|修改统计|质检报告') { throw 'internal style report leaked' }
if ([string]::IsNullOrWhiteSpace($text)) { throw 'empty technical document' }
'technical-document-structure-valid=True'
```

Expected: `technical-document-structure-valid=True`.

- [ ] **Step 6: Manually back-check technical claims**

For every command, code block, numeric parameter, product name, and important conclusion, identify supporting transcript text or an official primary source. Replace unsupported material with `待核对` or remove it. Confirm each important conclusion includes its condition, limitation, or risk.

## Task 5: Final Skill and Regression Verification

**Files:**
- Verify: `C:\Users\人员\.codex\skills\video-to-transcript`
- Verify: `D:\develop\javaproject\VidScribe\transcripts\source-BV1Za4y1r7KE_p2-2.technical.md`

**Interfaces:**
- Consumes: all implemented Skill files and the live document.
- Produces: deployment evidence.

- [ ] **Step 1: Compile the unchanged transcription CLI**

Run:

```powershell
python -m py_compile "C:\Users\人员\.codex\skills\video-to-transcript\scripts\video_to_transcript.py"
```

Expected: exit code 0 and no output.

- [ ] **Step 2: Run all regression tests**

Run:

```powershell
python -m unittest discover -s "C:\Users\人员\.codex\skills\video-to-transcript\tests" -q
```

Expected: all tests pass.

- [ ] **Step 3: Validate the Skill package structure**

Run:

```powershell
python "C:\Users\人员\.agents\skills\skill-creator\scripts\quick_validate.py" "C:\Users\人员\.codex\skills\video-to-transcript"
```

Expected: `Skill is valid!`.

- [ ] **Step 4: Confirm no machine-specific ZIP dependency leaked into the installed Skill**

Run:

```powershell
rg -n "Downloads|unclecheng-reduce-ai-perception-v2-1.0.5.zip|--style|文风切换" "C:\Users\人员\.codex\skills\video-to-transcript"
```

Expected: no matches. Tests may mention “文风切换” only as a negative assertion; if so, verify the installed runtime instructions and references have no switch interface.

- [ ] **Step 5: Review the final diff against the baseline snapshot**

Run:

```powershell
git diff --no-index --stat -- "C:\Users\人员\.codex\skills\video-to-transcript-workspace\skill-snapshot" "C:\Users\人员\.codex\skills\video-to-transcript"
```

Expected: changes limited to Skill instructions, technical-writing reference, interface metadata, tests, and the already-approved transcription fixes. Binary `__pycache__` differences are ignored during review.

