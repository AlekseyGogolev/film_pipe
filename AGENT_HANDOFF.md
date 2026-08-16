# FilmPipe — Agent Handoff

## Current MVP State

Agent 4 frontend work is implemented. The MVP now supports:

```text
multipart upload
→ B&W processing job
→ per-image status/errors/artifacts
→ browser-friendly artifact preview/download
→ Original / Positive comparison UI
→ individual artifact download
→ batch ZIP download
```

The real B&W processing pipeline from Agent 2 remains unchanged and still produces:

```text
original
positive
```

`positive` is a 16-bit TIFF generated from a decoded B&W negative scan. The frontend is a minimal React/Vite app in `frontend/` and talks only to the HTTP API contract.

Agent 5 verified the MVP end-to-end on real/open negative material and fixed the browser preview blocker. `/preview` now returns transient 8-bit PNG bytes for browser display; `/download` still returns the stored master artifact such as the 16-bit TIFF positive.

## Architecture

Dependency map:

```text
Frontend / React UI
  ↓
HTTP API
  ↓
Application / JobService + InMemoryJobRegistry
  ↓
Processing Engine
  ↓
ProcessingPipeline
  ↓
Processor contract and concrete processors

Application / Processing
  ↓
ArtifactStore protocol
  ↓
FileSystemArtifactStore

Concrete image processors
  ↓
OpenCV / NumPy
```

Important boundaries:

- Processing Engine does not depend on HTTP/FastAPI.
- Domain contracts do not depend on OpenCV, NumPy, FastAPI, or ML frameworks.
- OpenCV/NumPy are confined to `backend/filmpipe/processing`.
- Basic B&W pipeline has no AI runtime.
- HTTP handlers do not contain image processing logic.
- Frontend uses FilmPipe API concepts only: Job, Image, Status, Artifact, Error, Mode.
- Frontend does not know filesystem paths, OpenCV/NumPy types, processor implementations, or storage layout.

## Technology Decisions

- Backend: Python 3.12 package under `backend/filmpipe`.
- Processing: NumPy + OpenCV in concrete processors only.
- API: FastAPI factory at `filmpipe.api.app:create_app`.
- Uploads: multipart form parsing via `python-multipart`.
- Preview: backend-generated PNG representation from stored artifacts.
- Job persistence: in-memory `InMemoryJobRegistry`; jobs reset on server restart.
- Job execution: `POST /jobs` processes synchronously for MVP and returns the final job state.
- Frontend: React 18 + TypeScript + Vite 8 under `frontend/`.
- Frontend API base: defaults to `/api`; Vite dev server proxies `/api/*` to `http://127.0.0.1:8000/*`.
- Frontend icons: `lucide-react`.
- Storage: filesystem under `data/jobs/{job_id}/{image_id}/{artifact_type}/`.
- Tests: pytest; API tests use a small in-process ASGI harness because the installed FastAPI/Starlette TestClient/httpx transports hang in this environment.
- Logging: standard Python `logging`, default file `logs/filmpipe.log`.

WHY:

- No DB is needed for local MVP job state.
- Synchronous job creation keeps Agent 3 simple; frontend can still poll `GET /jobs/{job_id}` and will receive final state for now.
- ZIP export reads existing generated artifacts from filesystem storage and excludes immutable originals.
- PNG previews keep browser display independent of TIFF support while preserving the master TIFF download contract.
- Vite proxy avoids adding CORS to the FastAPI MVP and keeps backend API unchanged.

Image decisions from Agent 2 remain:

- MVP input formats: `.tif`, `.tiff`, `.png`, `.jpg`, `.jpeg`.
- MVP input bit depth: unsigned 8-bit and unsigned 16-bit images.
- Input channels: grayscale, RGB, and RGBA. RGB/RGBA are converted to grayscale for the B&W pipeline.
- Internal representation: `FilmImage` in `backend/filmpipe/processing/image.py`, a processing-local 2D NumPy `float32` grayscale array normalized to `0.0..1.0`.
- Output format: 16-bit TIFF for `positive` artifacts.

## Important Contracts

- Domain models: `backend/filmpipe/domain/models.py`
- Processor contracts: `backend/filmpipe/domain/processor.py`
- Processing-local image representation: `backend/filmpipe/processing/image.py`
- Pipeline orchestration: `backend/filmpipe/processing/pipeline.py`
- Direct engine entrypoint: `backend/filmpipe/processing/engine.py`
- Concrete B&W processors: `backend/filmpipe/processing/processors/images.py`
- Stub/test processors: `backend/filmpipe/processing/processors/stubs.py`
- Filesystem storage: `backend/filmpipe/infrastructure/storage.py`
- Job orchestration and in-memory registry: `backend/filmpipe/application/jobs.py`
- HTTP API and response serialization helpers: `backend/filmpipe/api/app.py`
- Browser preview rendering helper: `backend/filmpipe/processing/preview.py`
- Frontend entrypoint: `frontend/src/main.tsx`
- Frontend app/state/UI: `frontend/src/App.tsx`
- Frontend typed API client: `frontend/src/api.ts`
- Frontend API response types: `frontend/src/types.ts`
- Frontend styling: `frontend/src/styles.css`
- Vite proxy/config: `frontend/vite.config.ts`

Core concepts:

- `Processor` has `name`, `optional`, and `process(image, context)`.
- `ProcessingPipeline` runs processors in order and resolves per-image status.
- `JobService.process()` handles single and batch with the same image pipeline.
- `InMemoryJobRegistry` stores completed jobs for API lookup in the current process.
- `ArtifactType` includes `original`, `positive`, `restored`, `colorized`, `creative`.
- `ProcessingStatus` includes `pending`, `running`, `success`, `partial_success`, `failed`.
- `ProcessingError` separates `user_message` from technical diagnostics.
- `FilmImage` is not a domain/API contract; do not expose it through API/frontend.

## Processing Flow

Current default flow:

```text
uploaded file
↓
temporary upload file
↓
save immutable original
↓
DecodeImageProcessor
↓
NegativeConverterProcessor
↓
ToneNormalizerProcessor
↓
PositiveArtifactWriterProcessor
↓
save positive artifact
↓
API job/image/artifact response
```

Normalization algorithm:

- Decode with OpenCV using unchanged bit depth.
- Validate shape, channels, dtype, and non-empty dimensions.
- Convert RGB/RGBA to grayscale when needed.
- Normalize decoded pixels to `float32` in `0.0..1.0`.
- Convert negative to positive with `1.0 - pixel`.
- Stretch tone using 0.5 and 99.5 percentiles.
- If tonal range is effectively flat, skip stretching and preserve the converted image.
- Encode output as 16-bit TIFF.

Do not create a second pipeline or domain model for the frontend.

## Storage / Artifacts

Storage layout:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
```

`FileSystemArtifactStore` refuses to overwrite existing artifacts. API responses expose artifact URLs, not filesystem paths.

Artifact response shape:

```text
type
filename
mime_type
preview_url
download_url
```

`mime_type` describes the stored artifact/download. `preview_url` returns a PNG display representation and is intentionally not the master artifact bytes.

Batch ZIP export includes existing generated artifacts such as `positive`. It intentionally excludes `original` files.

## Failure Model

- Original is saved before image processing starts.
- Unsupported suffix, decode failure, invalid dimensions/channels, or unsupported dtype fail before `positive`; image status becomes `failed`.
- Mandatory B&W processor failure before `positive` gives image status `failed`.
- Optional processor failure after `positive` still gives image status `partial_success`; the existing `positive` artifact remains available.
- Batch orchestration remains in `JobService`; single image is a special case of the same flow.
- Failure of one image does not stop the rest of the batch.
- `JobService` now catches unexpected per-image exceptions so one image cannot abort the whole job.
- User-facing errors remain short and do not include stack traces. Technical details go into `ProcessingError.technical_message` and logs.

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
  mode: bw
  prompt: optional string, accepted but not used by bw
  files: one or more uploaded files
```

Only `mode=bw` is implemented. `colorize` and `creative` return HTTP 400 with a clear message.

`POST /jobs` currently runs synchronously and returns the final job. Polling contract for frontend:

```text
POST /jobs -> job response
GET /jobs/{job_id} -> latest stored job response
```

Job response shape:

```text
id
status
mode
selected_modes
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

Error response shape:

```text
stage
message
recoverable
exception_type
```

`message` is user-facing. No stack traces are returned through API.

Artifact types in routes use enum values such as:

```text
original
positive
restored
colorized
creative
```

Preview endpoints return browser-friendly `image/png` bytes generated from the stored artifact. Download endpoints return the artifact bytes with the stored artifact MIME type. Batch ZIP returns `application/zip` and contains only existing generated result artifacts.

## Frontend Contract

Implemented frontend flow:

```text
select files
select mode bw
POST /jobs
render job.status and images[].status
show images[].errors[].message
use original/positive preview_url for comparison
use artifact download_url for individual downloads
use job.download_url for batch ZIP
```

Current UI behavior:

- Single and batch file selection via local file picker.
- B&W is enabled; Colorize and Creative are visible but disabled.
- Creative prompt field exists but is disabled while only B&W is supported.
- `POST /jobs` is called with multipart `mode` and `files`.
- Polling exists for `pending`/`running` jobs, although current backend returns final state synchronously.
- Job status, per-image status, per-image stage errors, artifact links, and batch ZIP are rendered from API response fields.
- Original/Positive panels attempt to render `preview_url` in `<img>`.
- `preview_url` now serves PNG for valid image artifacts, including 16-bit TIFF positives.
- If preview generation or browser image loading fails, the UI shows a fallback and keeps download actions available.

Do not expose or depend on filesystem paths, OpenCV/NumPy details, processor class names beyond user-facing `stage`, or storage layout.

## How to Run

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Direct processing:

```python
from pathlib import Path
from filmpipe import process_image

result = process_image(Path("sample_negative.tiff"))
print(result.status)
print([artifact.path for artifact in result.artifacts])
```

API:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Health endpoint:

```text
GET http://127.0.0.1:8000/health
```

Create a job:

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -F "mode=bw" \
  -F "files=@scan_001.tiff" \
  -F "files=@scan_002.tiff"
```

Use the factory target. `filmpipe.api.app:app` is not the supported start path.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Frontend URL:

```text
http://127.0.0.1:5173/
```

`npm run dev` binds to `127.0.0.1` and uses the Vite `/api` proxy. If the backend runs elsewhere, set `VITE_FILMPIPE_API_BASE`.

## How to Test

```bash
pytest
cd frontend
npm run build
```

Current tests cover storage immutability/overwrite behavior, pipeline success/failure/partial success, job status aggregation, logging context, direct engine invocation without HTTP, decode/validation errors, negative conversion, tone normalization, positive artifact writing, HTTP job creation, batch partial success, optional failure exposure, artifact preview/download, and ZIP export.

Last verified by Agent 5:

```text
.venv/bin/pytest
22 passed

npm run build (from frontend/)
passed
```

Manual smoke verified by Agent 5:

```text
Real/open actual negative samples used from Wikimedia Commons:
- Abraham_Lincoln_O-77_negative_by_Gardner,_1863.png — https://commons.wikimedia.org/wiki/File:Abraham_Lincoln_O-77_negative_by_Gardner,_1863.png
- Glass_plate_negative.jpg — https://commons.wikimedia.org/wiki/File:Glass_plate_negative.jpg
- 35mm_movie_negative.jpg — https://commons.wikimedia.org/wiki/File:35mm_movie_negative.jpg

Direct real-negative processing:
- 3 selected actual-negative/film-border samples -> success.
- Positive output visually usable for Abraham Lincoln O-77 and Glass plate negative.
- 35mm movie negative has film/perforation borders; frame content remained inspectable after conversion.
- Other archive files labeled as "negative" were found to contain already-positive pixels; pipeline intentionally does not auto-detect polarity.

Headless Chrome UI smoke:
- valid, valid, corrupted, valid upload through real <input type=file>.
- Per-image statuses: success, success, failed, success.
- Job status: partial_success / "Частично".
- Valid Original and Positive previews rendered in browser with non-zero naturalWidth/naturalHeight.
- Failed corrupted image showed only "Не удалось декодировать изображение ..." with no stack trace/internal details.
- Batch ZIP endpoint returned application/zip with 3 positive TIFF artifacts and excluded the failed image/originals.

Sync POST timings in this local environment:
- single small real negative: ~0.009s.
- 3-image real batch: ~0.257s.
- 36-image repeated real batch: ~2.865s.
```

## Known Limitations

- API job registry is in-memory; jobs disappear on server restart.
- `POST /jobs` is synchronous for MVP; long batches block that request until complete.
- Only technical B&W negative-to-positive processing is implemented.
- Preview PNGs are display representations generated on request; downloads remain the stored artifact format/bit depth.
- FilmPipe assumes input pixels are an actual negative. It does not detect already-positive archive scans.
- No film border detection, frame cropping, rotation, dust/scratch detection, restoration, colorization, or creative processing.
- No RAW camera formats, floating-point TIFF, palette images, or CMYK handling.
- JPEG input is accepted but is already lossy; TIFF/PNG are preferred.
- Tone normalization is a basic percentile stretch, not a scanner/profile-aware correction.
- Metadata/ICC profiles are not preserved in positive artifacts.
- No AI restoration/colorization/creative processing.

## Open Issues

- Real negative quality now has limited real/open smoke coverage, but broader fixture coverage is still needed.
- Some real scans may need frame-border masking before percentile normalization. Agent 5 did not add border detection because selected actual-negative/film-border samples did not prove a blocking failure.
- Current pipeline treats RGB/RGBA inputs as B&W luminance; color negative workflow is not implemented.
- The installed FastAPI/Starlette TestClient/httpx transports hang in this environment; API tests currently use a tiny local ASGI harness instead.
- No committed automated frontend component/e2e test suite yet; Agent 5 used a one-off headless Chrome smoke for integration verification.

## Decisions That Should Not Be Revisited Without Reason

- Keep Processing Engine independent from HTTP.
- Keep domain contracts free of OpenCV/NumPy/FastAPI.
- Keep `FilmImage` processing-local.
- Use filesystem storage for MVP artifacts; do not add a database without proven need.
- Use in-memory job registry for Agent 3/4 MVP flow unless persistence becomes necessary.
- Use one pipeline model for single image and batch.
- Use 16-bit TIFF for generated positive artifacts.
- Keep `/preview` browser-friendly and `/download` master-format preserving.
- Do not include immutable originals in batch result ZIP by default.
- Keep AI restoration as a future optional stage after `positive`; do not fold it into the mandatory B&W MVP path.
- Keep frontend on API response URLs and typed API concepts; do not add filesystem coupling.
- Do not add CORS just for Vite dev while the `/api` proxy satisfies local frontend development.

## Completed In This Task

- Added backend PNG preview rendering for artifact preview endpoints.
- Preserved download/master artifact behavior: positive downloads are still 16-bit TIFF.
- Updated API tests for PNG preview plus TIFF download preservation.
- Verified real/open actual-negative processing and visually inspected outputs.
- Verified mixed batch through the UI: valid, valid, corrupted, valid -> success, success, failed, success; job -> partial_success.
- Verified user-facing errors in UI do not expose stack traces/internal details.
- Measured synchronous POST timings and kept sync execution because local MVP timings were acceptable.
- Updated root README and this handoff.

## Next Agent

MVP integration is verified. The next separate phase can start AI-restoration exploration:

```text
Positive
↓
DefectDetector
↓
Mask
↓
Restorer
↓
Clean Master
```

Keep the existing B&W pipeline and preview/download contracts stable while adding that optional stage.
