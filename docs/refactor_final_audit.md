# FilmPipe Refactor Final Audit

Agent 3 completed the cleanup, regression pass, contract sweep, and final
architecture audit for the FilmPipe runtime model.

## Final Business Model

FilmPipe now exposes exactly two independent runtime choices:

```text
Input Processing:
  already_positive
  bw_negative

Restoration:
  off
  telea
  lama
```

`already_positive` means the uploaded source is already the base positive image.
It is decoded into `context.working_positive` without inversion, B&W grayscale
preparation, tone normalization, or a synthetic public `positive` artifact.

`bw_negative` means the uploaded source is a B&W negative. It follows the
existing B&W conversion path and writes a public 16-bit TIFF `positive`
artifact.

Restoration always consumes `context.working_positive` and does not care which
input-processing path produced it.

## Final API Contract

Create job:

```text
POST /jobs
multipart/form-data:
  input_processing=already_positive|bw_negative
  restoration=off|telea|lama
  files=(one or more uploads)
```

Defaults:

```text
input_processing=bw_negative
restoration=off
```

Job response:

```text
id
status
input_processing
restoration
created_at
updated_at
images[]
errors[]
download_url
```

`POST /jobs` performs strict multipart field validation. Unknown form fields are
rejected with HTTP 400 using the same generic validation path for every unknown
field.

## Public Artifact Semantics

| Input Processing | Restoration | Public Artifacts |
| --- | --- | --- |
| `already_positive` | `off` | `original` |
| `already_positive` | `telea` | `original`, `restored` |
| `already_positive` | `lama` | `original`, `restored` |
| `bw_negative` | `off` | `original`, `positive` |
| `bw_negative` | `telea` | `original`, `positive`, `restored` |
| `bw_negative` | `lama` | `original`, `positive`, `restored` |

Current `ArtifactType` values are only:

```text
original
positive
restored
```

Future `colorized` and `creative` artifacts were removed from the active domain
enum because they are not implemented runtime states.

## Execution Plans

```text
already_positive + off
  decode_positive

already_positive + telea
  decode_positive, ai_restoration

already_positive + lama
  decode_positive, ai_restoration

bw_negative + off
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer

bw_negative + telea
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration

bw_negative + lama
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration
```

Absent processors are absent from the execution plan; processors do not act as
runtime no-op switches for disabled stages.

## Regression Matrix

| Case | Verified |
| --- | --- |
| `already_positive + off` | `decode_positive` only; no negative conversion, tone normalization, positive writer, or restoration; public artifact set is `original`. |
| `already_positive + telea` | `decode_positive, ai_restoration`; restoration reads `working_positive`; no public `positive` card/artifact is required. |
| `already_positive + lama` | Same plan shape as TELEA with LaMa selected; optional restoration failure preserves the base result. |
| `bw_negative + off` | B&W decode, inversion, tone normalization, and 16-bit TIFF `positive` artifact still work. |
| `bw_negative + telea` | Conversion runs before TELEA restoration; `positive` and `restored` can coexist. |
| `bw_negative + lama` | Conversion runs before LaMa restoration; unit tests use fake adapters instead of GPU inference. |

## Required Invariants

Verified by tests and audit:

- positive input is not inverted;
- color already-positive input preserves decoded channels and does not go
  through B&W grayscale preparation;
- B&W negative input still produces a technically correct positive;
- restoration reads `context.working_positive`;
- optional restoration failure preserves the base result;
- one failed image does not stop a batch;
- public artifacts are useful user results, not accidental internal stages;
- job/image logs expose `input_processing`, `restoration`, and processor plans;
- frontend sends only `input_processing`, `restoration`, and `files`;
- frontend renders only artifacts returned by the API.

## Contract Sweep Results

Removed or changed:

- request parsing code that recognized removed contract fields by name;
- API tests that asserted removed contract behavior;
- domain enum values `ArtifactType.COLORIZED` and `ArtifactType.CREATIVE`;
- stale root README API and Python examples from the previous contract;
- stale `AGENT_HANDOFF.md` sections describing the previous UI/API contract;
- stale frontend README wording implying TIFF preview limitations instead of
  API PNG previews;
- stale AI integration doc default `restoration=lama`.

Intentionally retained:

- restoration-internal names for mask postprocessing and inference metadata;
- `off` as the current `RestorationMode.OFF` value;
- `positive` and `negative` as domain language for image inputs and artifact
  names;
- historical planning/spec documents that describe earlier phases or future
  non-goals, with Agent 3 notes added to refactor handoff files where current
  behavior changed.

## Documentation Updated

Updated current docs:

- `README.md`
- `AGENT_HANDOFF.md`
- `frontend/README.md`
- `docs/AI_INTEGRATION.md`
- `docs/refactor_handoff_agent1.md`
- `docs/refactor_handoff_agent2.md`

Created:

- `docs/refactor_final_audit.md`

## Verification

Commands run:

```text
.venv/bin/python -m pytest
(cd frontend && npm run build)
.venv/bin/python -m compileall backend/filmpipe
git diff --check
```

Results:

```text
pytest: 56 passed in 0.55s
frontend build: tsc --noEmit success, vite build success in 75ms
compileall: success
git diff --check: success
```

Manual/local HTTP smoke:

- Attempted to start Uvicorn on `127.0.0.1:8123` from `/tmp` so default storage
  would not write into the repo.
- Uvicorn started successfully, but sibling sandboxed exec commands could not
  connect to that loopback port; `ss` was also restricted by sandbox permissions.
- The project already uses an in-process ASGI test harness for this environment,
  and the full API regression suite above covered the request/response cases
  and artifact semantics.

## Known Limitations

- Job registry is still in memory.
- `POST /jobs` is still synchronous.
- FilmPipe does not auto-detect negative versus already-positive inputs.
- TELEA/LaMa restoration require the Microsoft detector runtime; LaMa requires
  prepared model files.
- Unit tests do not run real heavy LaMa inference.
- No committed frontend e2e/browser test suite exists; Agent 2 performed the
  layout/payload browser checks and Agent 3 verified the final frontend build.

## Intentionally Not Implemented

This refactor did not add:

- automatic input detection;
- new colorization;
- creative generation;
- plugin architecture;
- account/auth system;
- persistent database;
- background job queue;
- new restoration algorithms or quality changes.
