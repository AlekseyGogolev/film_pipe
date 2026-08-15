# FilmPipe — Agent Handoff

## Current MVP State

Agent 2 B&W image processing pipeline is implemented. The backend now accepts real local image files through the direct processing engine and produces:

```text
original
positive
```

The `positive` artifact is a 16-bit TIFF generated from a decoded B&W negative scan. The project still has only a minimal FastAPI `/health` placeholder; full job HTTP API, preview/download endpoints, ZIP export, and frontend remain later-agent scope.

## Architecture

Dependency map:

```text
HTTP API placeholder
  ↓
Application / JobService
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
- Frontend is not implemented.

## Technology Decisions

- Backend: Python 3.12 package under `backend/filmpipe`.
- Processing: NumPy + OpenCV in concrete processors only.
- API: FastAPI placeholder with `/health`; full job API is Agent 3 scope.
- Frontend: React/Vite direction documented only; no implementation yet.
- Storage: filesystem under `data/jobs/{job_id}/{image_id}/{artifact_type}/`.
- Tests: pytest.
- Logging: standard Python `logging`, default file `logs/filmpipe.log`.

Image decisions:

- MVP input formats: `.tif`, `.tiff`, `.png`, `.jpg`, `.jpeg`.
- MVP input bit depth: unsigned 8-bit and unsigned 16-bit images.
- Input channels: grayscale, RGB, and RGBA. RGB/RGBA are converted to grayscale for the B&W pipeline.
- Internal representation: `FilmImage` in `backend/filmpipe/processing/image.py`, a processing-local 2D NumPy `float32` grayscale array normalized to `0.0..1.0`.
- Output format: 16-bit TIFF for `positive` artifacts.

WHY: TIFF output avoids adding lossy compression after processing, preserves 16-bit headroom for future restoration/colorization work, and keeps image-library details out of domain/API contracts.

## Important Contracts

- Domain models: `backend/filmpipe/domain/models.py`
- Processor contracts: `backend/filmpipe/domain/processor.py`
- Processing-local image representation: `backend/filmpipe/processing/image.py`
- Pipeline orchestration: `backend/filmpipe/processing/pipeline.py`
- Direct engine entrypoint: `backend/filmpipe/processing/engine.py`
- Concrete B&W processors: `backend/filmpipe/processing/processors/images.py`
- Stub/test processors: `backend/filmpipe/processing/processors/stubs.py`
- Filesystem storage: `backend/filmpipe/infrastructure/storage.py`
- Job orchestration: `backend/filmpipe/application/jobs.py`

Core concepts:

- `Processor` still has `name`, `optional`, and `process(image, context)`.
- `ProcessingPipeline` runs processors in order and resolves per-image status.
- `ArtifactType` includes `original`, `positive`, `restored`, `colorized`, `creative`.
- `ProcessingStatus` includes `pending`, `running`, `success`, `partial_success`, `failed`.
- `ProcessingError` separates `user_message` from technical diagnostics.
- `FilmImage` is not a domain contract; do not expose it through API/frontend.

## Processing Flow

Current default flow:

```text
input file
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

Do not create a second pipeline or domain model for Agent 3.

## Storage / Artifacts

Storage layout:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
```

`FileSystemArtifactStore` refuses to overwrite existing artifacts. The positive writer creates a temporary TIFF and then saves it through the existing storage contract, so storage remains the single path for artifact registration.

## Failure Model

- Original is saved before image processing starts.
- Unsupported suffix, decode failure, invalid dimensions/channels, or unsupported dtype fail before `positive`; image status becomes `failed`.
- Mandatory B&W processor failure before `positive` gives image status `failed`.
- Optional processor failure after `positive` still gives `partial_success`; the existing `positive` artifact remains available.
- Batch orchestration remains in `JobService`; single image is a special case of the same direct engine.
- User-facing errors remain short and do not include stack traces. Technical details go into `ProcessingError.technical_message` and logs.

## API Contract

Implemented only:

```text
GET /health -> {"status": "ok"}
```

Full job creation, polling, artifact preview/download, per-image errors, and ZIP export are Agent 3 scope.

## Frontend Contract

No frontend app exists yet. `frontend/README.md` records that Agent 4 should implement React/Vite against the backend API contract produced by Agent 3.

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

API placeholder:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Health endpoint:

```text
GET http://127.0.0.1:8000/health
```

## How to Test

```bash
pytest
```

Current tests cover storage immutability/overwrite behavior, pipeline success/failure/partial success, job status aggregation, logging context, direct engine invocation without HTTP, decode/validation errors, negative conversion, tone normalization, and positive artifact writing.

Last verified by Agent 2:

```text
.venv/bin/pytest
18 passed
```

## Known Limitations

- Only technical B&W negative-to-positive processing is implemented.
- No film border detection, frame cropping, rotation, dust/scratch detection, restoration, colorization, or creative processing.
- No RAW camera formats, floating-point TIFF, palette images, or CMYK handling.
- JPEG input is accepted but is already lossy; TIFF/PNG are preferred.
- Tone normalization is a basic percentile stretch, not a scanner/profile-aware correction.
- Metadata/ICC profiles are not preserved in positive artifacts.
- No HTTP job API yet.
- No frontend yet.
- No AI restoration/colorization/creative processing.

## Open Issues

- Real negative quality needs broader fixture coverage beyond synthetic test images.
- Some real scans may need frame-border masking before percentile normalization.
- Current pipeline treats RGB/RGBA inputs as B&W luminance; color negative workflow is not implemented.

## Decisions That Should Not Be Revisited Without Reason

- Keep Processing Engine independent from HTTP.
- Keep domain contracts free of OpenCV/NumPy/FastAPI.
- Keep `FilmImage` processing-local.
- Use filesystem storage for MVP artifacts; do not add a database without proven need.
- Use one pipeline model for single image and batch.
- Use 16-bit TIFF for generated positive artifacts.
- Do not start AI restoration before the B&W positive pipeline and HTTP/frontend MVP are integrated.

## Completed In This Task

- Added `FilmImage` processing-local representation.
- Added `DecodeImageProcessor`.
- Added `NegativeConverterProcessor`.
- Added `ToneNormalizerProcessor`.
- Added `PositiveArtifactWriterProcessor`.
- Replaced the default stub pipeline with the real B&W processing flow.
- Kept Agent 1 stub processors for focused contract/failure tests.
- Added synthetic image fixtures and Agent 2 tests.
- Updated README and this handoff.

## Next Agent

Agent 3 should build jobs, batch behavior, failure model exposure, and HTTP API around the existing direct processing engine. Use `default_pipeline()` as the real B&W pipeline and do not move image processing logic into HTTP handlers.
