# FilmPipe

FilmPipe is a local application foundation for processing film scans. The current MVP has core domain contracts, pipeline orchestration, filesystem artifact storage, logging, tests, a local HTTP API, a real B&W processing pipeline, optional AI restoration, and a minimal React/Vite frontend.

The default processing pipeline is built from job options. Negative inputs are decoded, converted to positive, tone-normalized, written as a 16-bit TIFF `positive` artifact, then optionally restored. Already-positive scans use `polarity=positive`, which omits negative conversion and tone normalization from the execution plan.

## Requirements

- Python 3.12+
- Node.js 20+ and npm for the frontend
- Processing dependencies: NumPy and OpenCV
- Local API dependencies: FastAPI, python-multipart, and Uvicorn
- Optional AI restoration dependencies: PyTorch for the Microsoft detector, and the LaMa runtime dependencies only when using `restoration=lama`

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cd frontend
npm install
```

This installs `filmpipe` in editable mode, so imports such as `from filmpipe import process_image` work without setting `PYTHONPATH`.

VS Code workspace settings point Python/Pylance at `.venv/bin/python` and add `backend/` as an analysis path. If the editor still shows stale unresolved imports, reload the VS Code window and select the `.venv` interpreter.

## Run Tests

```bash
pytest
cd frontend
npm run build
```

## Direct Processing Smoke Test

```bash
python - <<'PY'
from pathlib import Path
import cv2
import numpy as np
from filmpipe import ProcessingMode, ProcessingOptions, RestorationMode, process_image

source = Path("sample_negative.tiff")
positive = np.tile(np.linspace(0, 65535, 64, dtype=np.uint16), (32, 1))
negative = np.iinfo(np.uint16).max - positive
ok, encoded = cv2.imencode(".tiff", negative)
assert ok
source.write_bytes(encoded.tobytes())

result = process_image(
    source,
    options=ProcessingOptions(
        mode=ProcessingMode.BW,
        restoration=RestorationMode.OFF,
    ),
)
print(result.status)
for artifact in result.artifacts:
    print(artifact.type.value, artifact.path)
PY
```

Artifacts are written under `data/jobs/{job_id}/{image_id}/{artifact_type}/`.

## AI Restoration Models

Restoration modes:

```text
off    positive only, no detector/restorer
telea  Microsoft scratch detector + production mask postprocessing + OpenCV TELEA
lama   Microsoft scratch detector + production mask postprocessing + LaMa
```

The API/frontend default is `restoration=off`. Use `telea` or `lama` only when the Microsoft detector runtime is prepared; both restoration modes need PyTorch for defect detection. Use `restoration=off` for the lightweight pipeline without PyTorch or model files.

Large model weights are not committed. For the current MVP, prepare the already verified experiment models with:

```bash
cd experiments/ai_restoration
python download_models.py
```

Install the AI runtime into the main backend environment with:

```bash
source .venv/bin/activate
python -m pip install -e ".[ai]"
```

During local development, FilmPipe can also reuse the already prepared experiment runtime. The restoration processor checks `FILMPIPE_AI_RUNTIME_SITE_PACKAGES`, then `FILMPIPE_AI_RUNTIME_VENV`, then `experiments/ai_restoration/.venv` if it exists. To make that explicit:

```bash
export FILMPIPE_AI_RUNTIME_VENV=experiments/ai_restoration/.venv
```

The experiment runtime can be created or refreshed with:

```bash
cd experiments/ai_restoration
python -m pip install -r requirements.txt
python -m pip install -r requirements-lama.txt
```

Production lookup uses `FILMPIPE_AI_MODELS_ROOT` when set. Otherwise it checks `models/ai_restoration` and then the existing `experiments/ai_restoration/models` directory. `restoration=off` never loads the detector or LaMa, and `restoration=telea` does not require LaMa files to load.

## Local API

Start the local API:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Then open:

```text
http://127.0.0.1:8000/health
```

### API Contract

Create a B&W processing job with multipart form data:

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -F "mode=bw" \
  -F "polarity=negative" \
  -F "restoration=off" \
  -F "files=@scan_001.tiff" \
  -F "files=@scan_002.tiff"
```

`POST /jobs` processes synchronously for the MVP and returns the final job state. Frontend polling should use:

```text
GET /jobs/{job_id}
GET /jobs/{job_id}/images/{image_id}
```

Job responses use API concepts only:

```text
id
status
mode
polarity
restoration
selected_modes
images[]
errors[]
download_url
```

Each image includes:

```text
id
filename
status
artifacts[]
errors[]
```

Each artifact includes `type`, `filename`, `mime_type`, `preview_url`, and `download_url`; filesystem paths are not exposed.

Artifact endpoints:

```text
GET /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview
GET /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/download
```

`/preview` returns a browser-friendly PNG representation generated from the
stored artifact. `/download` returns the stored artifact bytes and MIME type, so
MVP positive downloads remain 16-bit TIFF masters.

Batch ZIP export:

```text
GET /jobs/{job_id}/download
```

The ZIP contains existing generated result artifacts such as `positive`; immutable `original` files are available per image but are not included in batch result ZIPs.

Only `mode=bw` is implemented for normal processing. Use `polarity=negative` for negative-to-positive conversion or `polarity=positive` for already-positive input. Legacy `mode=off` is still accepted as an alias for already-positive input. `colorize` and `creative` currently return a clear `400` response.

## Local Frontend

Start the API first:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Then start the frontend:

```bash
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:5173/
```

The Vite dev server proxies `/api/*` to `http://127.0.0.1:8000/*`. For a different backend URL, set:

```bash
VITE_FILMPIPE_API_BASE=http://127.0.0.1:8000 npm run dev
```

The frontend uses only API response URLs for artifact preview/download and does not depend on filesystem paths.

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

## Output Artifacts

The filesystem storage layout is:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
data/jobs/{job_id}/{image_id}/restored/{safe_stem}_restored.tiff
```

`original` artifacts are immutable copies of uploaded files. `positive`
artifacts are generated separately and do not replace originals. `restored`
artifacts are optional derivatives and never replace `positive`. Batch ZIP
downloads include generated result artifacts such as `positive` and `restored`
and exclude immutable originals.

Current negative-input B&W algorithm:

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
↓
optional restoration: off | TELEA | LaMa
```

If the tonal range is effectively flat, normalization is skipped and the converted image is preserved.

With `polarity=positive`, the default execution plan is decode → positive artifact writer → optional restoration. `NegativeConverterProcessor` and tone normalization are not added to the plan.

Restoration failures are recoverable: the `positive` artifact remains available, `restored` is omitted, and the image/job can report `partial_success`.

## Logging

By default logs are written to:

```text
logs/filmpipe.log
```

Log records include `job_id`, `image_id`, and `processor` context. User-facing errors intentionally do not include stack traces.

## Known MVP Limitations

- The API job registry is in memory; jobs disappear on server restart.
- `POST /jobs` runs synchronously. Agent 5 measured about 0.01s for one small
  real negative, 0.26s for a three-image real batch, and 2.86s for a 36-image
  repeated real batch on this local environment, so no background queue was
  added for the MVP.
- FilmPipe does not auto-detect negative versus positive scans; choose
  `Input: Negative` for negatives or `Input: Positive` for already-positive
  inputs.
- Preview PNGs are display representations. Downloads remain the stored
  artifact format and bit depth.
- No film border detection, frame cropping, rotation, colorization, or creative processing is implemented.
- Metadata and ICC profiles are not preserved in generated positive artifacts.

## MVP Extension Points

Processing concepts:

- `DefectDetector`
- `Restorer`
- `InferenceProvider`
- `Colorizer`
- `GenerativeProcessor`

`DefectDetector` and `Restorer` now have production-local contracts for optional AI restoration. The remaining concepts are future extension points.
