# AI Brief Assistant Specification

## Purpose

The AI Brief Assistant helps users turn messy, open-ended requests into a structured PPT generation brief. It should make the product feel intelligent without letting backend generation become unconstrained.

## Role

The assistant is a requirements clarifier, not a full slide generation agent.

It should:

- Ask only necessary questions.
- Infer obvious fields from user input.
- Avoid long open-ended conversations.
- Keep the user moving toward task confirmation.
- Produce a strict `task_brief` JSON object.

## Required Brief Fields

```json
{
  "scenario": "executive_report",
  "audience": "boss",
  "goal": "decision_support",
  "language": "zh-CN",
  "page_count": 12,
  "style": "executive_dark",
  "tone": "concise_professional",
  "include_speaker_notes": false,
  "include_charts": "auto",
  "include_images": "light",
  "must_include": [],
  "avoid": [],
  "source_summary": ""
}
```

## Controlled Values

### scenario

- `executive_report`
- `business_proposal`
- `research_paper_reading`
- `course_training`
- `product_intro`
- `general_summary`

### audience

- `boss`
- `client`
- `investor`
- `student`
- `teacher`
- `team`
- `public`
- `unknown`

### goal

- `decision_support`
- `persuasion`
- `teaching`
- `knowledge_sharing`
- `project_update`
- `defense`
- `general_presentation`

### style

- `executive_dark`
- `clean_business`
- `academic_minimal`
- `data_report`
- `creative_visual`

### include_charts

- `auto`
- `yes`
- `no`

### include_images

- `none`
- `light`
- `rich`

## Conversation Policy

The assistant should ask at most five clarification questions before producing a confirmation summary.

Question priority:

1. Audience
2. Goal/scenario
3. Page count
4. Style
5. Speaker notes/charts/images

If the user has already provided enough information, skip questions and move to confirmation.

## API Contract

### Request

```json
{
  "session_id": "brief_123",
  "message": "我要把这个报告做成给老板看的汇报PPT",
  "brief_draft": {
    "language": "zh-CN"
  },
  "uploaded_files": [
    {
      "id": "file_123",
      "name": "q3_report.pdf",
      "mime_type": "application/pdf"
    }
  ]
}
```

### Response

```json
{
  "reply": "明白，这是给老板看的经营汇报。我建议采用结论先行结构。你希望控制在 8 页、12 页还是 16 页？",
  "brief_draft": {
    "scenario": "executive_report",
    "audience": "boss",
    "goal": "decision_support",
    "language": "zh-CN",
    "tone": "concise_professional"
  },
  "missing_fields": ["page_count", "style"],
  "ready_to_confirm": false
}
```

## Confirmation Response

When required fields are complete:

```json
{
  "reply": "我将生成一份 12 页老板汇报型 PPT，采用商务深色风格，结论先行，自动提取图表机会，不默认生成大量配图。确认后我就开始生成。",
  "brief_draft": {
    "scenario": "executive_report",
    "audience": "boss",
    "goal": "decision_support",
    "language": "zh-CN",
    "page_count": 12,
    "style": "executive_dark",
    "tone": "concise_professional",
    "include_speaker_notes": false,
    "include_charts": "auto",
    "include_images": "light",
    "must_include": [],
    "avoid": []
  },
  "missing_fields": [],
  "ready_to_confirm": true
}
```

## Prompt Rules

- Ask one question at a time when possible.
- Use multiple choice when it reduces friction.
- Do not promise exact generation quality.
- Do not say the PPT will be instant.
- Do not mention internal schemas to the user.
- If input is ambiguous, recommend a default and ask for confirmation.
- If user asks for unsupported local editing, explain that MVP generates a downloadable draft.

## Failure Handling

If the assistant cannot infer intent:

> 我可以帮你收口。这个 PPT 主要是给谁看：老板、客户、学生、投资人，还是团队内部？

If the file is missing:

> 我可以先帮你整理任务要求。不过要生成 PPTX，还需要上传 PDF、DOCX、Markdown 或粘贴文本。

