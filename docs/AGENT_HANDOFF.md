# FilmPipe - Agent Handoff

## Current MVP State

FilmPipe supports a local asynchronous processing flow:

```text
multipart upload
-> stable job input staging
-> persistent job manifest
-> in-process background queue
-> input processing / optional restoration / optional creative final processing
-> persisted per-image status/errors/artifacts
-> browser-friendly artifact preview/download
-> persistent history/gallery
-> batch ZIP download for generated artifacts
```

The current backend runtime model has three independent job choices:

```text
input_processing = already_positive | bw_negative
restoration      = off | telea | lama
final_processing = standard | creative
```

Job creation accepts only the current contract fields: `files`,
`input_processing`, `restoration`, `final_processing`, and `creative_prompt`.
Unknown form fields are rejected with a clear HTTP 400 response.

## Architecture

Dependency map:

```text
Frontend / React UI
  -> HTTP API
  -> Application / JobService + JobQueue
  -> Infrastructure / FileSystemJobRegistry
  -> Processing Engine
  -> ProcessingPipeline
  -> Processor contract and concrete processors

Application / Processing
  -> ArtifactStore protocol
  -> FileSystemArtifactStore

Concrete image processors
  -> OpenCV / NumPy

Creative provider
  -> stable-diffusion.cpp CLI subprocess when final_processing=creative
```

Important boundaries:

- Processing Engine does not depend on HTTP/FastAPI.
- Domain contracts do not depend on OpenCV, NumPy, FastAPI, PyTorch, or LaMa.
- `FilmImage` remains processing-local.
- Frontend talks only to the FilmPipe HTTP API and never inspects local
  filesystem paths.
- API responses do not expose local artifact paths.
- Job manifests store API-safe data plus relative paths used internally to
  rebuild `Artifact.path`.
- Basic `bw_negative` conversion does not load AI runtime.
- Restoration consumes `context.working_positive`, not a public `positive`
  artifact.
- `final_processing=standard` does not instantiate or call the Creative
  provider.
- Creative source selection uses `ProcessingContext.artifacts` and
  `context.working_positive`, not filesystem layout.
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
  final_processing: standard | creative
  creative_prompt: required only for final_processing=creative
  files: one or more uploaded files
```

Defaults:

```text
input_processing=bw_negative
restoration=off
final_processing=standard
```

`POST /jobs` is now asynchronous. The request handler validates the multipart
form, writes uploaded files to a stable job-owned directory, creates a
`pending` job, persists `data/jobs/{job_id}/job.json`, enqueues background work,
and returns HTTP `201` with the current job representation. Processing then
runs in a single-process `ThreadPoolExecutor(max_workers=1)` queue.

Job response shape:

```text
id
status
input_processing
restoration
final_processing
creative_prompt
created_at
updated_at
images[]
errors[]
download_url
legacy
```

Job status semantics:

```text
pending          job has been accepted before the worker starts
running          at least one image is pending/running after processing starts
success          all images succeeded
failed           all images failed or a worker-level unrecoverable error failed the job
partial_success  terminal mixed/recoverable state after active processing is done
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
creative
```

Preview endpoints return browser-friendly PNG bytes generated from the stored
artifact. Download endpoints return the stored artifact bytes and MIME type.
Batch ZIP export excludes immutable originals and includes generated artifacts
only, including `creative` when present.

The frontend may request preview URLs with optional query strings such as
`?max_edge=512` or `?max_edge=1920`. The current backend does not implement
server-side preview resizing; FastAPI ignores the unknown query parameter and
returns the normal PNG preview.

## Persistent History

Default storage root:

```text
data/jobs
```

Non-legacy job layout:

```text
data/jobs/{job_id}/job.json
data/jobs/{job_id}/inputs/{index}/{safe_filename}
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
data/jobs/{job_id}/{image_id}/restored/{safe_stem}_restored.tiff
data/jobs/{job_id}/{image_id}/creative/{safe_stem}_creative.png
```

`FileSystemJobRegistry` loads existing manifests at app startup and returns
jobs newest-first from `GET /jobs`. Manifest artifact entries use
`relative_path`, not absolute server paths. When a job directory has no
`job.json`, the registry scans image artifact folders and reconstructs a legacy
job where possible. Legacy jobs use valid default option values
`bw_negative/off/standard` and include `legacy: true`; those options are
inferred compatibility values, not guaranteed historical settings.

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

already_positive + off + creative
  decode_positive, generative_processing

already_positive + telea/lama + creative
  decode_positive, ai_restoration, generative_processing

bw_negative + off + creative
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, generative_processing

bw_negative + telea/lama + creative
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration, generative_processing
```

`already_positive` preserves decoded dtype/channels as the working positive and
does not create a synthetic public `positive` artifact. `bw_negative` converts
decoded input to grayscale, inverts it, tone-normalizes it, and writes a 16-bit
TIFF `positive` artifact. `generative_processing` runs only for
`final_processing=creative` and writes a separate PNG `creative` artifact.

## Artifact Semantics

| Input Processing | Restoration | Final Processing | Public Artifacts |
| --- | --- | --- | --- |
| `already_positive` | `off` | `standard` | `original` |
| `already_positive` | `telea/lama` | `standard` | `original`, `restored` |
| `bw_negative` | `off` | `standard` | `original`, `positive` |
| `bw_negative` | `telea/lama` | `standard` | `original`, `positive`, `restored` |
| `already_positive` | `off` | `creative` | `original`, `creative` |
| `already_positive` | `telea/lama` | `creative` | `original`, `restored`, `creative` |
| `bw_negative` | `off` | `creative` | `original`, `positive`, `creative` |
| `bw_negative` | `telea/lama` | `creative` | `original`, `positive`, `restored`, `creative` |

## Failure Model

- Original is saved before image processing starts.
- Mandatory decode/conversion failures before a base result make that image
  `failed`.
- Optional restoration failures preserve the base result and produce
  `partial_success`.
- Optional Creative failures preserve the latest technical base result and
  produce `partial_success`.
- For `bw_negative`, the recoverable base result is the public `positive`
  artifact.
- For `already_positive`, the recoverable base result is internal
  `working_positive` plus the immutable public `original`.
- Failure of one image does not stop a batch.
- User-facing API errors do not include stack traces; technical details go to
  logs.

## Frontend

The React/Vite frontend lives in `frontend/`. It has two top-level views:

```text
Processing Console
History Gallery
```

It sends the current backend contract:

```text
input_processing
restoration
final_processing
creative_prompt only when final_processing=creative
files
```

Active controls:

- file selection;
- clear selection;
- process action;
- Console/Gallery navigation;
- Input Processing: Already Positive / Negative -> Positive;
- Restoration: Off / TELEA / LaMa;
- Final Processing: Standard / Creative.

The Creative prompt field appears only when Creative is selected, and submit is
disabled until it is non-empty. After submit, the API returns quickly with a
`pending` job and the console keeps polling until the job is terminal.

Polling behavior:

- active console job polls `GET /jobs/{job_id}` every 1.5 seconds while
  `pending` or `running`;
- history loads from `GET /jobs`, refreshes when Gallery opens, and polls every
  3.5 seconds while history is not loaded or any listed job is active;
- the polling hook prevents overlapping requests;
- poll errors are surfaced without clearing the last known state.

The UI renders artifacts returned by the API, ordered as:

```text
original, positive, restored, creative
```

It does not show placeholder `Positive` cards for already-positive inputs.
Recoverable Creative failures still display earlier `original`, `positive`, or
`restored` artifacts returned by the API.

Gallery job cards include best available thumbnail, short id, status,
created/updated timestamps, image count, option chips, generated artifact chips,
and batch ZIP action when generated artifacts exist. Thumbnail selection
prefers `creative`, then `restored`, then `positive`, then `original`.

Selecting a history job opens the same image/artifact detail renderer used by
the console. Clicking a preview opens a fullscreen lightbox using `preview_url`
for display and `download_url` for master download. The lightbox supports close
button, Escape, and backdrop click.

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

Current tests cover direct engine invocation, standard and Creative backend
runtime plans, public artifact semantics, API validation, async job creation,
manifest persistence, reload from persistent history, legacy history
reconstruction, restoration and Creative failure semantics, batch partial
success, preview/download behavior, ZIP export, logging context, and frontend
TypeScript/Vite build.

## Known Limitations

- The queue is local and in-process. It is not durable across backend process
  death and does not support cancellation.
- Jobs completed before restart are restored from manifests, but a backend
  restart during a running job is not resumed. Current startup loading does not
  yet mark stale active manifests as failed.
- FilmPipe does not auto-detect negative versus already-positive inputs.
- TELEA and LaMa restoration need the Microsoft detector runtime; LaMa also
  needs prepared LaMa model files.
- Creative processing needs a prepared stable-diffusion.cpp runtime and model
  files. The default backend provider uses the Agent 1 FLUX.1 Kontext Q4 Vulkan
  experiment paths and is synchronous CLI-first.
- Preview PNGs are display representations; downloads remain stored artifact
  format and bit depth.
- Preview `max_edge` query parameters are frontend-compatible but not
  implemented server-side yet.
- No film border detection, frame cropping, rotation, colorization, Creative
  server warm pool, accounts/auth, persistent DB, external queue, or distributed
  workers are implemented.

## Creative Runtime Env Vars

Defaults follow `docs/creative_research_handoff_agent1.md`:

```text
FILMPIPE_CREATIVE_SD_CLI
FILMPIPE_CREATIVE_DIFFUSION_MODEL
FILMPIPE_CREATIVE_VAE
FILMPIPE_CREATIVE_CLIP_L
FILMPIPE_CREATIVE_T5XXL
FILMPIPE_CREATIVE_BACKEND
FILMPIPE_CREATIVE_PARAMS_BACKEND
FILMPIPE_CREATIVE_MAX_VRAM
FILMPIPE_CREATIVE_WIDTH
FILMPIPE_CREATIVE_HEIGHT
FILMPIPE_CREATIVE_STEPS
FILMPIPE_CREATIVE_SEED
FILMPIPE_CREATIVE_STRENGTH
FILMPIPE_CREATIVE_TIMEOUT_SEC
```

## Current Refactor Trail

Read these in order for the current refactor:

```text
docs/FilmPipe_REFACTOR_AGENT_PLAN.md
docs/refactor_handoff_agent1.md
docs/refactor_handoff_agent2.md
docs/refactor_final_audit.md
docs/history_queue_frontend_agent_plan.md
docs/history_queue_frontend_agent5_handoff.md
```
