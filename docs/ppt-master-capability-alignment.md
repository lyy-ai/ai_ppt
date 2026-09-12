# Best PPT capability alignment

Baseline: upstream `hugohe3/ppt-master` `main` at `de0af38a07706eee03b97f6186d8e0ffba595892` (PPT Master Skill v4.2.0).

## Synchronization status

- Local product branch: `alignment-v4.2-cloud`.
- The pre-alignment branch `feat/cloud-generator-postgres-deep` was 695 commits behind upstream `main` and 2 commits ahead; its work is preserved in `stash@{0}` with message `pre-upstream-v4.2-cloud-alignment`.
- A protected local baseline branch, `upstream-v4.2.0`, now points to the upstream commit above.
- The local cloud generator is an extension product: upstream explicitly treats hosted SaaS as out of scope, so its web workbench, auth, billing, queue, and preview code must remain an adapter layer rather than replace the upstream core.
- The local core now contains the upstream routed Generate/Create Template/Fill Native PPTX/Enhance Native PPTX workflows, native Master/Layout workspaces, staged Confirm UI, native-shape/PPTX structure interfaces, chart recall, narration synchronization, and native PowerPoint video export at the pinned baseline above.

The migration order is: synchronize the upstream core, define the cloud adapter boundary, port cloud-specific behavior onto the new routes, then run the frontend and production-readiness work below.

| Capability | Best PPT status | Notes |
| --- | --- | --- |
| Prompt to PPT | Implemented | Web workbench submits `/brief-assistant` then `/generation-tasks`. |
| PDF / DOCX / text material input | Implemented, needs regression | Uploads are sent as `material_files`; keep testing large PDFs and DOCX. |
| URL / web material input | Implemented, needs regression | URL extraction is passed through `source_urls`; backend behavior should be checked per release. |
| Native editable PPTX | Implemented | Main artifact remains `output/result.pptx`. |
| PPT preview | Implemented | Workbench preview now uses contain-style fitting for varied screens. |
| Speaker notes | Implemented | Notes are shown below preview when artifact metadata includes them. |
| Narrated PPTX | Implemented, needs real TTS regression | `include_audio=true` generates `result_narrated.pptx` when audio succeeds. |
| Animations / transitions | Implemented | Toolbar `Animations` maps to `include_animations`. |
| AI image generation | Implemented, provider-dependent | Toolbar `Generate images` maps to `include_images`; provider config must be available. |
| Template following | Implemented, needs UX regression | Template picker is single-select; imported PPTX files can enter the reusable-template/Generate flow or the staged Fill Native PPTX route. Raw native-shell authoring remains a separate confirmation workflow. |
| History restore | Implemented | Sidebar history restores status, preview and downloads. |
| Membership / credits | Implemented | Plans are `Free / Plus / Pro / Team`; credits remain backend source of truth. |
| Domestic payment | Implemented, needs live YPay test | Aggregator route uses YPay submit URL and notify webhook. |
| Global payment | Reserved | Paddle route and webhook hooks exist but need real merchant config. |

## Core-to-cloud route matrix

| Route | Upstream core | Cloud API | Current contract / next action |
| --- | --- | --- | --- |
| Generate PPTX | Ready | Ready | Uses `cloud_generator.orchestrator.run_pipeline`; keep regression coverage against upstream exporter CLI. |
| Create Template | Ready in core / Hosted authoring in cloud | Staged intake + editable authoring IR + publish | The cloud session runs the upstream PPTX import, exposes sanitized multi-slide SVG authoring with text/fill/font/geometry edits, bounded version history, one-step undo, Shift multi-selection, nudge, alignment, distribution, and basic sizing, materializes `templates/*.svg` through the upstream mirror compiler, accepts structured authoring metadata, and publishes a Generate-consumable package. Collaboration remains a product follow-up. |
| Fill Native PPTX | Ready in core / Staged in cloud | Staged session + confirmed apply | The cloud session performs materialization, slide-library analysis, fill-plan confirmation, OOXML apply, and export delivery; it is not a one-shot Generate task. |
| Enhance Native PPTX | Ready in core / Staged in cloud | Staged session + confirmed apply | The cloud session performs project init, notes/audio confirmation, OOXML apply, validation, and export delivery; it is not a one-shot Generate task. |

The API exposes this matrix at `GET /core-capabilities`, including separate `cloud_available` and `session_available` values. The frontend routes native selections into the staged confirmation modal; only Generate remains a one-shot generation task. No unsupported route is silently downgraded.

## Verification completed on 2026-07-27

- `npm run check`: HTML parse, Python compilation, and 12 unit/integration tests passed.
- `scripts/cloud-generator-regression-matrix.py`: 9 mock API scenarios passed, including route capability reporting, unsupported-route preflight, quota-metered and idempotent native sessions, Create Template publish/listing, published-template consumption by Generate, uploaded material, template parameters, images, narration, and narration-plus-animation export.
- `scripts/cloud-generator-smoke.py`: authentication, payment mock, credits, referral, ownership, retry/refund, team, logout, preview, artifacts, and usage telemetry passed.
- Upstream `pptx_template_import.py --manifest-only`, `template_fill_pptx.py` analyze/scaffold/check-plan, and `native_enhance_pptx.py` init/plan/validate were run against representative editable PPTX files.
- Direct staged-session verification also completed: Fill Native confirmed apply produced an exported PPTX; Enhance Native confirmed apply produced a validated enhanced PPTX; Create Template produced, edited through the hosted authoring surface, published, and listed a named reusable package.
- Interrupted route preparation is recovered from on-disk artifacts when possible and otherwise marked failed for safe retry; route state writes are atomic and stale sessions are covered by cleanup.
- A real temporary Postgres 16 container was initialized from `docker-compose.postgres.yml`; the queue schema and Postgres-backed route-session persist/load path were exercised successfully, then the validation container and volume were removed.
- `scripts/check-cloud-generator-readiness.py` correctly blocks public release in the current local environment because the access token is unset; it also reports the expected local-storage, local route-session state, scanner, cost, payment, and SMTP warnings. Those deployment values cannot be inferred or fabricated in source control.

## Unified backlog

### P0 — required for capability parity and safe release

1. Add collaborative authoring on top of the now-working hosted authoring surface.
2. Enable the new Postgres route-session state store in production and complete artifact/session lifecycle reconciliation; local recovery, quota debit/refund, and idempotent confirmation are implemented and regression-tested.
3. Configure production security gates: access token, required auth, durable artifact storage, upload malware scanning, cost telemetry, real payment, and transactional email.

### P1 — frontend reliability and UX

1. Route/status surface, non-blocking toast feedback, and modal focus/keyboard handling are now present; continue localized copy review during browser QA.
2. Extend the new Chromium smoke test to cover auth handoff, upload limits, template import, retry, history restore, preview, native confirmation, and both download variants.
3. Split the large inline workbench script into testable modules without changing the current payload contract.

### P2 — productization

1. Add durable cloud job retention and cleanup policies with storage lifecycle metrics.
2. Complete live payment/provider onboarding and observability dashboards.
3. Add visual regression snapshots for the landing page, workbench, viewer, and generated preview states.

Regression checklist before shipping:

1. Generate a 5-minute / 8-page deck without optional settings.
2. Generate with uploaded PDF or DOCX.
3. Generate with AI images enabled.
4. Generate with narration enabled and download both PPTX variants.
5. Generate with narration and animations enabled together; verify the narrated export uses valid animation timing configuration.
6. Select exactly one template and confirm payload contains the same template.
7. Restore a historical task and verify preview, notes and downloads.
8. Start Plus checkout on `ppt-cn.aigcstory.site` and confirm YPay redirects correctly.
