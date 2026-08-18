# FilmPipe - Agent Handoff

## Current MVP State

FilmPipe supports a local synchronous processing flow:

```text
multipart upload
-> input processing
-> optional restoration
-> per-image status/errors/artifacts
-> browser-friendly artifact preview/download
-> batch ZIP download for generated artifacts
```

The current runtime model has exactly two independent user choices:

```text
input_processing = already_positive | bw_negative
restoration      = off | telea | lama
```

Job creation accepts only the current contract fields: `files`,
`input_processing`, and `restoration`. Unknown form fields are rejected with a
generic HTTP 400 response.

## Architecture

Dependency map:

```text
Frontend / React UI
  -> HTTP API
  -> Application / JobService + InMemoryJobRegistry
  -> Processing Engine
  -> ProcessingPipeline
  -> Processor contract and concrete processors

Application / Processing
  -> ArtifactStore protocol
  -> FileSystemArtifactStore

Concrete image processors
  -> OpenCV / NumPy
```

Important boundaries:

- Processing Engine does not depend on HTTP/FastAPI.
- Domain contracts do not depend on OpenCV, NumPy, FastAPI, PyTorch, or LaMa.
- `FilmImage` remains processing-local.
- Basic `bw_negative` conversion does not load AI runtime.
- Restoration consumes `context.working_positive`, not a public `positive`
  artifact.
- Frontend uses API response URLs only and does not know filesystem paths.

## API Contract

Implemented endpoints:

```text
GET  /health
GET  /jobs
POST /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/images/{image_id}
GET  /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview
GET  /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/download
GET  /jobs/{job_id}/download
```

Create job:

```text
POST /jobs
Content-Type: multipart/form-data

fields:
  input_processing: already_positive | bw_negative
  restoration: off | telea | lama
  files: one or more uploaded files
```

Defaults:

```text
input_processing=bw_negative
restoration=off
```

Job response shape:

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

Image response shape:

```text
id
filename
status
artifacts[]
errors[]
```

Artifact response shape:

```text
type
filename
mime_type
preview_url
download_url
```

Current public artifact types are:

```text
original
positive
restored
```

Preview endpoints return browser-friendly PNG bytes generated from the stored
artifact. Download endpoints return the stored artifact bytes and MIME type.
Batch ZIP export excludes immutable originals and includes generated artifacts
only.

## Processing Plans

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

`already_positive` preserves decoded dtype/channels as the working positive and
does not create a synthetic public `positive` artifact. `bw_negative` converts
decoded input to grayscale, inverts it, tone-normalizes it, and writes a 16-bit
TIFF `positive` artifact.

## Artifact Semantics

| Input Processing | Restoration | Public Artifacts |
| --- | --- | --- |
| `already_positive` | `off` | `original` |
| `already_positive` | `telea` | `original`, `restored` |
| `already_positive` | `lama` | `original`, `restored` |
| `bw_negative` | `off` | `original`, `positive` |
| `bw_negative` | `telea` | `original`, `positive`, `restored` |
| `bw_negative` | `lama` | `original`, `positive`, `restored` |

## Failure Model

- Original is saved before image processing starts.
- Mandatory decode/conversion failures before a base result make that image
  `failed`.
- Optional restoration failures preserve the base result and produce
  `partial_success`.
- For `bw_negative`, the recoverable base result is the public `positive`
  artifact.
- For `already_positive`, the recoverable base result is internal
  `working_positive` plus the immutable public `original`.
- Failure of one image does not stop a batch.
- User-facing API errors do not include stack traces; technical details go to
  logs.

## Frontend

The React/Vite frontend lives in `frontend/` and sends only:

```text
input_processing
restoration
files
```

Active controls:

- file selection;
- clear selection;
- process action;
- Input Processing: Already Positive / Negative -> Positive;
- Restoration: Off / TELEA / LaMa.

The UI renders only artifacts returned by the API, ordered as:

```text
original, positive, restored
```

It does not show placeholder `Positive` cards for already-positive inputs.

## How to Run

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd frontend
npm install
```

Backend:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Frontend:

```bash
cd frontend
npm run dev
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000/*`.

## How to Test

```bash
.venv/bin/python -m pytest
cd frontend
npm run build
```

Current tests cover direct engine invocation, all six runtime plans, public
artifact semantics, API validation, restoration failure semantics, batch partial
success, preview/download behavior, ZIP export, logging context, and frontend
TypeScript/Vite build.

## Known Limitations

- API job registry is in-memory; jobs disappear on server restart.
- `POST /jobs` is synchronous for the MVP.
- FilmPipe does not auto-detect negative versus already-positive inputs.
- TELEA and LaMa restoration need the Microsoft detector runtime; LaMa also
  needs prepared LaMa model files.
- Preview PNGs are display representations; downloads remain stored artifact
  format and bit depth.
- No film border detection, frame cropping, rotation, colorization, creative
  generation, accounts/auth, persistent DB, or job queue is implemented.

## Current Refactor Trail

Read these in order for the current refactor:

```text
docs/FilmPipe_REFACTOR_AGENT_PLAN.md
docs/refactor_handoff_agent1.md
docs/refactor_handoff_agent2.md
docs/refactor_final_audit.md
```
