# PPT Master Commercial Product Requirements

## Purpose

Turn PPT Master from an agent skill and open source workflow into a commercial-facing product that feels as approachable as a modern AI PPT web app, while preserving PPT Master's strongest promise: generating real, editable PowerPoint files instead of image-based slides.

The first commercial version should not attempt to clone a full online editor like JJT. It should deliver a narrower, reliable product:

> Upload source material, answer a few guided questions, wait asynchronously, then download an editable PPTX draft.

## Product Positioning

PPT Master Cloud Generator is an asynchronous AI PPT draft generator for users who want a high-quality editable PowerPoint starting point from existing materials.

It is not an online slide editor in the first release. It is a guided generation and delivery product.

## Target Users

### Primary

- Consultants, analysts, finance professionals, founders, teachers, students, and researchers who already have source material and need a presentable PPTX draft.
- Users who value PowerPoint editability more than instant visual output.

### Secondary

- AI power users who may later adopt the local skill workflow.
- Teams that may want private or local deployment in the future.

## Core User Journey

1. User lands on a commercial homepage.
2. User clicks `Generate PPT Draft`.
3. User uploads a supported file or pastes text.
4. AI Brief Assistant asks focused questions to clarify the task.
5. User confirms the structured brief.
6. Backend creates an asynchronous generation task.
7. Worker runs the PPT Master generation pipeline.
8. User watches progress or leaves the page.
9. User downloads the generated editable PPTX when complete.

## In Scope For MVP

- Commercial landing page inspired by modern AI PPT products.
- Login or lightweight account identity.
- Upload PDF, DOCX, Markdown, TXT, or pasted text.
- AI Brief Assistant that narrows user intent into a structured task brief.
- Fixed scenarios:
  - Executive report
  - Business proposal
  - Research paper reading
  - Course/training deck
  - Product introduction
- Fixed style choices:
  - Executive dark
  - Clean business
  - Academic minimal
  - Data report
  - Creative visual
- Page count choices: 8, 12, 16, 20, or auto.
- Optional speaker notes.
- Async task progress page.
- Generated PPTX download.
- Basic task history.
- Failure state with retry or clear error messaging.

## Out Of Scope For MVP

- Online per-slide editing of generated decks; Create Template has a limited hosted authoring surface for imported template SVGs.
- Multi-turn local slide revision such as "make slide 5 more premium."
- Real-time collaborative editing.
- Template marketplace.
- Full membership/J-points system.
- Complex image generation by default.
- Film-like transition generation by default.
- Enterprise admin features.

## Success Criteria

- A new user understands the product within 10 seconds of opening the homepage.
- A user can create a task without knowing what an IDE agent or skill is.
- The system can generate a downloadable PPTX draft without human operator intervention.
- At least one narrow scenario can complete successfully end to end.
- The generated PPTX opens in PowerPoint or compatible software.
- Failures are captured as task states, not silent crashes.

## Product Principles

- Input can be flexible; output contracts must be structured.
- The assistant should guide and narrow, not chat indefinitely.
- Prefer asynchronous progress over pretending generation is instant.
- Prefer a reliable 60-70% editable draft over a fragile all-purpose agent.
- Keep the local/open-source positioning visible as a trust advantage.

## MVP User-Facing Copy Direction

Hero message:

> Generate editable PowerPoint drafts from real documents.

Supporting copy:

> Upload a report, paper, proposal, or outline. PPT Master turns it into a structured, editable PPTX draft you can download and refine in PowerPoint.

Primary CTA:

> Generate a PPT Draft

Secondary CTA:

> View Examples

Differentiator:

> Not screenshots. Real PowerPoint elements you can keep editing.

## Risks

- Generation speed may be slower than online instant tools.
- First outputs may vary in quality because inputs are unconstrained.
- LLM and image generation costs can grow quickly.
- Current PPT Master flow depends on agent behavior and must be extracted into a command-style pipeline before full automation.

## MVP Constraints

- First release should support a small set of scenarios and styles.
- Every generated task must have size, token, retry, and page-count limits.
- The backend should store intermediate artifacts for debugging.
- The worker should not require VS Code, Cursor, Claude Code, or any GUI IDE on the server.

## Feasibility Note

Local verification confirms that existing PPT Master scripts can render prepared `svg_output/` slides into an editable PPTX without an IDE agent. The key MVP risk is automating the upstream Strategist and Executor phases that currently create `design_spec.md`, `spec_lock.md`, `svg_output/*.svg`, and notes.
