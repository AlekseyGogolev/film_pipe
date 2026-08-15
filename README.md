# FilmPipe

FilmPipe is a local application foundation for processing film scans. Agent 1 implements the MVP architecture bootstrap only: core domain contracts, pipeline orchestration, filesystem artifact storage, logging, tests, and a minimal HTTP app placeholder.

The current processing pipeline creates a `positive` artifact with a stub processor. Real B&W negative conversion and tone normalization are Agent 2 scope.

## Requirements

- Python 3.12+
- Planned processing dependencies: NumPy and OpenCV
- Planned local API dependencies: FastAPI and Uvicorn

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

This installs `filmpipe` in editable mode, so imports such as `from filmpipe import process_image` work without setting `PYTHONPATH`.

VS Code workspace settings point Python/Pylance at `.venv/bin/python` and add `backend/` as an analysis path. If the editor still shows stale unresolved imports, reload the VS Code window and select the `.venv` interpreter.

## Run Tests

```bash
pytest
```

## Direct Processing Smoke Test

```bash
python - <<'PY'
from pathlib import Path
from filmpipe import process_image

source = Path("sample_scan.txt")
source.write_bytes(b"placeholder negative bytes")
result = process_image(source)
print(result.status)
print([artifact.type.value for artifact in result.artifacts])
PY
```

Artifacts are written under `data/jobs/{job_id}/{image_id}/{artifact_type}/`.

## Local API Placeholder

The API layer is intentionally minimal in Agent 1. It exposes only health once dependencies are installed:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Then open:

```text
http://127.0.0.1:8000/health
```

Job creation, polling, artifact preview/download, and ZIP export are Agent 3 scope.

## Supported Image Formats

Agent 1 does not decode images yet. Agent 2 must define and test MVP image formats, internal representation, bit depth handling, and output format before implementing real B&W processing.

## Logging

By default logs are written to:

```text
logs/filmpipe.log
```

Log records include `job_id`, `image_id`, and `processor` context. User-facing errors intentionally do not include stack traces.

## MVP Extension Points

Reserved processing concepts:

- `DefectDetector`
- `Restorer`
- `InferenceProvider`
- `Colorizer`
- `GenerativeProcessor`

These are not production implementations in Agent 1.
