# FilmPipe

FilmPipe is a local application foundation for processing film scans. The current backend has core domain contracts, pipeline orchestration, filesystem artifact storage, logging, tests, a minimal HTTP app placeholder, and a real MVP B&W processing pipeline.

The default processing pipeline decodes a B&W negative scan, converts it to a positive, applies automatic tone normalization, and writes a 16-bit TIFF `positive` artifact. HTTP job endpoints and the frontend are still later MVP stages.

## Requirements

- Python 3.12+
- Processing dependencies: NumPy and OpenCV
- Local API placeholder dependencies: FastAPI and Uvicorn

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
import cv2
import numpy as np
from filmpipe import process_image

source = Path("sample_negative.tiff")
positive = np.tile(np.linspace(0, 65535, 64, dtype=np.uint16), (32, 1))
negative = np.iinfo(np.uint16).max - positive
ok, encoded = cv2.imencode(".tiff", negative)
assert ok
source.write_bytes(encoded.tobytes())

result = process_image(source)
print(result.status)
for artifact in result.artifacts:
    print(artifact.type.value, artifact.path)
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

MVP input formats:

- `.tif` / `.tiff`
- `.png`
- `.jpg` / `.jpeg`

MVP input bit depths:

- 8-bit unsigned grayscale or RGB/RGBA
- 16-bit unsigned grayscale or RGB/RGBA

RGB/RGBA inputs are converted to grayscale for the B&W pipeline. JPEG is accepted for convenience, but TIFF/PNG are preferred because FilmPipe should avoid unnecessary lossy compression.

Internal representation is processing-local: a 2D NumPy `float32` grayscale image normalized to `0.0..1.0`. Domain/API models do not expose NumPy or OpenCV types.

Output `positive` artifacts are 16-bit TIFF files.

Current B&W algorithm:

```text
OpenCV decode
↓
grayscale validation
↓
negative conversion: 1.0 - pixel
↓
percentile tone normalization: 0.5% / 99.5%
↓
16-bit TIFF positive artifact
```

If the tonal range is effectively flat, normalization is skipped and the converted image is preserved.

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

These are not production implementations in the current MVP stage.
