# Commercialization Test Strategy

## Purpose

Define how to test the commercial PPT Master product from brief capture through asynchronous PPTX generation and download.

## Test Pyramid

```text
Manual exploratory tests
End-to-end browser tests
Integration tests
Contract/schema tests
Unit tests
```

## Unit Tests

### Brief Assistant

- Infers scenario from user text.
- Tracks missing fields.
- Stops asking questions when required fields are complete.
- Produces valid controlled values.
- Refuses unsupported promises such as instant perfect generation.

### Source Extraction

- Extracts plain text from TXT.
- Extracts sections from Markdown.
- Handles empty or malformed files.
- Returns clear warnings for low-quality extraction.

### Pipeline State

- Allows valid state transitions.
- Rejects invalid transitions.
- Records task events.

### PPTX Validation

- Passes valid PPTX.
- Fails missing file.
- Fails invalid ZIP.
- Fails wrong slide count.

## Contract Tests

Validate JSON schemas for:

- `task_brief.json`
- `extracted_content.json`
- `deck_outline.json`
- `slide_specs/*.json`
- `validation_report.json`

Every LLM-facing step should include schema validation before the next pipeline phase.

## Integration Tests

### Happy Path

Input:

- Small Markdown source
- Scenario: executive report
- Style: clean business
- Page count: 8

Expected:

- Task reaches `completed`.
- Output PPTX exists.
- Validation report passes.
- Download URL is available.

### Failure Path

Input:

- Unsupported file type

Expected:

- Task reaches `failed`.
- User-safe error is stored.
- Worker does not crash globally.

### Retry Path

Simulate malformed LLM JSON on first attempt.

Expected:

- Repair/retry happens once.
- Task either completes or fails with clear diagnostics.

## End-to-End Tests

Browser flow:

1. Visit landing page.
2. Click `Generate PPT Draft`.
3. Upload a sample Markdown or TXT file.
4. Answer AI Brief Assistant questions.
5. Confirm the generated brief.
6. Wait for task completion.
7. Download the PPTX.

Expected:

- No blocking UI errors.
- Progress states are visible.
- Download button appears only when task completes.

## Manual QA Scenarios

- Chinese executive report.
- English business proposal.
- Research paper reading deck.
- Course/training deck.
- Very short input.
- Long input near size limit.
- User provides vague goal.
- User changes mind during brief chat.
- Worker task fails.
- User refreshes task page during generation.

## Performance Targets

MVP targets:

- Brief Assistant response: under 5 seconds for most turns.
- Small 8-slide generation: under 5 minutes.
- 12-16 slide generation: under 15 minutes.
- Task status polling: no more than once every 2-5 seconds from browser.

## Cost Tests

For each scenario, record:

- Input tokens.
- Output tokens.
- Number of LLM calls.
- Render time.
- Total task cost estimate.

The product should block or warn before running tasks that exceed configured limits.

## Acceptance Criteria

The MVP is ready for private beta when:

- One scenario generates valid PPTX end to end.
- Brief Assistant creates valid task briefs consistently.
- Failed tasks do not require server restarts.
- Worker logs and artifacts are sufficient to debug failures.
- The product clearly communicates that generation is asynchronous.

