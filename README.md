# FilmPipe

FilmPipe is a local application foundation for processing film scans. The current MVP has core domain contracts, pipeline orchestration, filesystem artifact storage, logging, tests, a local HTTP API, a B&W negative-to-positive path, already-positive input handling, optional AI restoration, optional backend Creative processing, and a minimal React/Vite frontend.

The processing pipeline is built from three independent job options:

```text
input_processing = already_positive | bw_negative
restoration      = off | telea | lama
final_processing = standard | creative
```

Already-positive inputs are decoded as the working positive without inversion, B&W preparation, or a synthetic public `positive` artifact. B&W negative inputs are decoded, converted to positive, tone-normalized, written as a 16-bit TIFF `positive` artifact, then optionally restored. Creative processing runs last when requested and writes a separate `creative` artifact without replacing `original`, `positive`, or `restored`.

## Requirements

- Python 3.12+
- Node.js 20+ and npm for the frontend
- Processing dependencies: NumPy and OpenCV
- Local API dependencies: FastAPI, python-multipart, and Uvicorn
- Optional AI restoration dependencies: PyTorch for the Microsoft detector, and the LaMa runtime dependencies only when using `restoration=lama`
- Optional Creative runtime: prepared stable-diffusion.cpp CLI and model files only when using `final_processing=creative`

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
from filmpipe import InputProcessingMode, ProcessingOptions, RestorationMode, process_image

source = Path("sample_negative.tiff")
positive = np.tile(np.linspace(0, 65535, 64, dtype=np.uint16), (32, 1))
negative = np.iinfo(np.uint16).max - positive
ok, encoded = cv2.imencode(".tiff", negative)
assert ok
source.write_bytes(encoded.tobytes())

result = process_image(
    source,
    options=ProcessingOptions(
        input_processing=InputProcessingMode.BW_NEGATIVE,
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

## Creative Runtime

Creative modes:

```text
standard  technical pipeline only, no Creative runtime
creative  selected technical/restored source + creative_prompt -> creative PNG
```

Creative is a backend late-stage processor. It selects the source from the
processing context in this order: `restored`, `positive`, already-positive
`working_positive`, then immutable `original`. It converts that source to a
temporary 8-bit PNG for the provider and saves only a separate `creative`
artifact.

The current provider is a stable-diffusion.cpp CLI adapter using the Agent 1
FLUX.1 Kontext Q4 Vulkan experiment paths by default. Prepare the assets under
`experiments/creative_edit/` as documented in
`docs/creative_research_handoff_agent1.md`, or override paths with env vars:

```bash
export FILMPIPE_CREATIVE_SD_CLI=experiments/creative_edit/runtime/sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan/sd-cli
export FILMPIPE_CREATIVE_DIFFUSION_MODEL=experiments/creative_edit/models/flux/flux1-kontext-dev-Q4_K_M.gguf
export FILMPIPE_CREATIVE_VAE=experiments/creative_edit/models/flux/ae.safetensors
export FILMPIPE_CREATIVE_CLIP_L=experiments/creative_edit/models/flux/clip_l.safetensors
export FILMPIPE_CREATIVE_T5XXL=experiments/creative_edit/models/flux/t5xxl_fp16.safetensors
```

Optional tuning env vars include `FILMPIPE_CREATIVE_BACKEND`,
`FILMPIPE_CREATIVE_PARAMS_BACKEND`, `FILMPIPE_CREATIVE_MAX_VRAM`,
`FILMPIPE_CREATIVE_WIDTH`, `FILMPIPE_CREATIVE_HEIGHT`,
`FILMPIPE_CREATIVE_STEPS`, `FILMPIPE_CREATIVE_SEED`,
`FILMPIPE_CREATIVE_STRENGTH`, and `FILMPIPE_CREATIVE_TIMEOUT_SEC`.

Missing runtime or model files are reported as recoverable Creative failures:
previous `original`, `positive`, or `restored` artifacts remain available and
the image can report `partial_success`.

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

Create a processing job with multipart form data:

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -F "input_processing=bw_negative" \
  -F "restoration=off" \
  -F "final_processing=standard" \
  -F "files=@scan_001.tiff" \
  -F "files=@scan_002.tiff"
```

Use `input_processing=already_positive` for scans that are already positive.
The default request options are `input_processing=bw_negative` and
`restoration=off`, `final_processing=standard`.

Creative backend processing uses the same endpoint:

```bash
curl -X POST http://127.0.0.1:8000/jobs \
  -F "input_processing=bw_negative" \
  -F "restoration=off" \
  -F "final_processing=creative" \
  -F "creative_prompt=preserve the composition and make the photo look like a clean archival print" \
  -F "files=@scan_001.tiff"
```

`creative_prompt` is required and trimmed non-empty only when
`final_processing=creative`. `final_processing=standard` does not start or
contact the Creative runtime.

`POST /jobs` processes synchronously for the MVP and returns the final job state. Frontend polling should use:

```text
GET /jobs/{job_id}
GET /jobs/{job_id}/images/{image_id}
```

Job responses use API concepts only:

```text
id
status
input_processing
restoration
final_processing
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

The ZIP contains existing generated result artifacts such as `positive`,
`restored`, and `creative`; immutable `original` files are available per image
but are not included in batch result ZIPs. For `already_positive + off +
standard`, no generated artifact exists, so the batch ZIP endpoint returns
`404`.

Requests with unknown form fields receive a clear `400` response. The API
accepts only `files`, `input_processing`, `restoration`, `final_processing`,
and `creative_prompt` for job creation.

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

RGB/RGBA inputs are converted to grayscale only for the `bw_negative` pipeline. `already_positive` preserves decoded channels for the internal working positive. JPEG is accepted for convenience, but TIFF/PNG are preferred because FilmPipe should avoid unnecessary lossy compression.

Internal image representations are processing-local. The `bw_negative` path uses a 2D NumPy `float32` grayscale image normalized to `0.0..1.0`; `already_positive` keeps the decoded dtype/channels as the working positive. Domain/API models do not expose NumPy or OpenCV types.

Output `positive` artifacts are 16-bit TIFF files.

## Output Artifacts

The filesystem storage layout is:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
data/jobs/{job_id}/{image_id}/restored/{safe_stem}_restored.tiff
data/jobs/{job_id}/{image_id}/creative/{safe_stem}_creative.png
```

`original` artifacts are immutable copies of uploaded files. `positive`
artifacts are generated for B&W negative conversion and do not replace
originals. `restored` artifacts are optional derivatives. `creative` artifacts
are optional final derivatives. Batch ZIP downloads include generated result
artifacts such as `positive`, `restored`, and `creative` and exclude
immutable originals.

Public artifact semantics:

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

`bw_negative` execution plan:

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
↓
optional final creative processing
```

If the tonal range is effectively flat, normalization is skipped and the converted image is preserved.

`already_positive` execution plan:

```text
OpenCV decode
↓
working positive
↓
optional restoration: off | TELEA | LaMa
↓
optional final creative processing
```

For `already_positive`, `NegativeConverterProcessor`, `ToneNormalizerProcessor`, and `PositiveArtifactWriterProcessor` are not added to the plan.

Restoration and Creative failures are recoverable: the base result remains
available, the failed derivative is omitted, and the image/job can report
`partial_success`.

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
  `Input Processing: Negative -> Positive` for negatives or `Already Positive`
  for already-positive inputs.
- Preview PNGs are display representations. Downloads remain the stored
  artifact format and bit depth.
- No film border detection, frame cropping, rotation, colorization, Creative
  frontend controls, Creative server warm pool, or job queue is implemented.
  The current Creative backend provider is synchronous CLI-first and inherits
  Agent 1's FLUX non-commercial license limitation unless configured otherwise.
- Metadata and ICC profiles are not preserved in generated positive artifacts.

## MVP Extension Points

Processing concepts:

- `DefectDetector`
- `Restorer`
- `InferenceProvider`
- `Colorizer`
- `GenerativeProcessor`

`DefectDetector` and `Restorer` have production-local contracts for optional AI
restoration. `GenerativeProcessor` has a backend provider boundary for optional
Creative processing. `Colorizer` remains a future extension point.
