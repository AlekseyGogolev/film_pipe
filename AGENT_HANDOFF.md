# FilmPipe — Agent Handoff

## Current MVP State

Agent 1 bootstrap is implemented. The project has a Python backend package with domain contracts, pipeline orchestration, filesystem artifact storage, logging, a direct processing entrypoint, tests, and a minimal FastAPI placeholder.

The current pipeline does not process image pixels. It uses `PositiveArtifactStubProcessor` to copy the immutable original into a `positive` artifact so that artifact flow, failure behavior, and storage contracts can be tested.

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
Processor contract and concrete stub processors

Application / Processing
  ↓
ArtifactStore protocol
  ↓
FileSystemArtifactStore
```

Important absences:

- Processing Engine does not depend on HTTP/FastAPI.
- Domain contracts do not depend on OpenCV, NumPy, FastAPI, or ML frameworks.
- Basic pipeline has no AI runtime.
- Frontend is not implemented.

## Technology Decisions

- Backend: Python 3.12 package under `backend/filmpipe`.
- Processing: NumPy/OpenCV are project dependencies for Agent 2, but Agent 1 core does not import them.
- API: FastAPI placeholder with `/health`; full job API is Agent 3 scope.
- Frontend: React/Vite direction documented only; no implementation yet.
- Storage: filesystem under `data/jobs/{job_id}/{image_id}/{artifact_type}/`.
- Tests: pytest.
- Logging: standard Python `logging`, default file `logs/filmpipe.log`.
- Editor setup: `.vscode/settings.json` points VS Code/Pylance at `.venv/bin/python` and `backend/`.

WHY: this keeps the processing engine callable without HTTP, avoids a database before it is needed, and leaves clear boundaries for real B&W processors and future AI processors.

## Important Contracts

- Domain models: `backend/filmpipe/domain/models.py`
- Processor contracts: `backend/filmpipe/domain/processor.py`
- Pipeline orchestration: `backend/filmpipe/processing/pipeline.py`
- Direct engine entrypoint: `backend/filmpipe/processing/engine.py`
- Filesystem storage: `backend/filmpipe/infrastructure/storage.py`
- Job orchestration: `backend/filmpipe/application/jobs.py`

Core concepts:

- `Processor` has `name`, `optional`, and `process(image, context)`.
- `ProcessingPipeline` runs processors in order and resolves per-image status.
- `ArtifactType` includes `original`, `positive`, `restored`, `colorized`, `creative`.
- `ProcessingStatus` includes `pending`, `running`, `success`, `partial_success`, `failed`.
- `ProcessingError` separates `user_message` from technical diagnostics.

## Processing Flow

Current default flow:

```text
input file
↓
save immutable original
↓
PositiveArtifactStubProcessor
↓
save positive artifact
```

Agent 2 should replace the stub flow with:

```text
Decode / Validation
↓
B&W Negative Conversion
↓
Tone / Exposure Normalization
↓
Positive Artifact
```

Do not create a second pipeline or domain model.

## Storage / Artifacts

Storage layout:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive{suffix}
```

`FileSystemArtifactStore` refuses to overwrite existing artifacts. It creates artifact directories only when saving that artifact, so missing optional artifacts do not create empty directories.

## Failure Model

- Mandatory processor failure before `positive` gives image status `failed`.
- Optional processor failure after `positive` gives image status `partial_success`; the `positive` artifact remains available.
- Job status aggregation is implemented by `ProcessingJob.recompute_status()`.
- Batch orchestration is in `JobService`; single image is a special case of the same direct engine.

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

This installs `filmpipe` in editable mode so tests and editor imports resolve without `PYTHONPATH`.

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

Current tests cover storage immutability/overwrite behavior, pipeline success/failure/partial success, job status aggregation, logging context, and direct engine invocation without HTTP.

## Known Limitations

- No real image decoding, validation, negative conversion, tone normalization, or bit-depth handling yet.
- No HTTP job API yet.
- No frontend yet.
- No AI restoration/colorization/creative processing.
- Real image processing dependencies are installed through `python -m pip install -e ".[dev]"`; Agent 1 still does not use OpenCV/NumPy in core contracts.

## Open Issues

Agent 2 must define MVP image formats, internal representation, bit depth policy, normalization algorithm, fixture set, and output format before implementing real processing.

## Decisions That Should Not Be Revisited Without Reason

- Keep Processing Engine independent from HTTP.
- Keep domain contracts free of OpenCV/NumPy/FastAPI.
- Use filesystem storage for MVP artifacts; do not add a database without proven need.
- Use one pipeline model for single image and batch.
- Do not start AI restoration before the B&W positive pipeline works end to end.

## Completed In This Task

- Created backend package structure.
- Added domain contracts for processors, jobs, artifacts, errors, options, and statuses.
- Added pipeline orchestration and direct `process_image` entrypoint.
- Added non-destructive filesystem storage.
- Added contextual logging.
- Added minimal FastAPI placeholder.
- Added pytest suite.
- Added README and this handoff.

## Next Agent

Agent 2 should implement the real B&W image processing pipeline using the existing contracts. Replace `PositiveArtifactStubProcessor` with decode/validation, negative conversion, and normalization processors without moving processing logic into HTTP handlers.
