# FilmPipe Creative Backend Handoff - Agent 2

Дата: 2026-08-18.

Статус: backend Creative contract and optional late-stage processor integrated.
Frontend Creative UX is intentionally left for Agent 3.

## Scope Completed

- Read `docs/CREATIVE_PIPELINE_HANDOFF.md` and
  `docs/creative_research_handoff_agent1.md`.
- Added backend domain contract:
  - `FinalProcessingMode.STANDARD = "standard"`
  - `FinalProcessingMode.CREATIVE = "creative"`
  - `ProcessingOptions.final_processing`
  - `ProcessingOptions.creative_prompt`
  - `ArtifactType.CREATIVE = "creative"`
- Added processing/provider boundary in `backend/filmpipe/processing/generative.py`:
  - `CreativeSource`
  - `CreativeRequest`
  - `CreativeResult`
  - `CreativeProvider`
  - `StableDiffusionCppProvider`
  - `GenerativeProcessor`
  - `select_creative_source`
- Wired `GenerativeProcessor` into `default_processors()` only when
  `final_processing=creative`.
- Extended `POST /jobs` to accept:
  - `final_processing`, default `standard`
  - `creative_prompt`, required only for `creative`
- Added `final_processing` to job responses.
- Updated focused tests with fake Creative provider. No real GPU inference runs
  in the normal test suite.
- Updated `README.md` and `docs/AGENT_HANDOFF.md` with backend/runtime contract
  and limitations.

## Changed Files

- `backend/filmpipe/domain/models.py`
- `backend/filmpipe/domain/__init__.py`
- `backend/filmpipe/__init__.py`
- `backend/filmpipe/processing/generative.py`
- `backend/filmpipe/processing/engine.py`
- `backend/filmpipe/processing/processors/__init__.py`
- `backend/filmpipe/application/jobs.py`
- `backend/filmpipe/api/app.py`
- `tests/test_generative.py`
- `tests/test_api.py`
- `README.md`
- `docs/AGENT_HANDOFF.md`

## API Contract

`POST /jobs` multipart fields:

```text
files
input_processing=already_positive|bw_negative
restoration=off|telea|lama
final_processing=standard|creative
creative_prompt=<required non-blank for creative>
```

Defaults:

```text
input_processing=bw_negative
restoration=off
final_processing=standard
```

Job response now includes:

```text
final_processing
```

`creative_prompt` is not echoed in API responses. Runtime/model paths and
provider metadata are not exposed through the API.

Validation behavior:

- invalid `final_processing` returns 400 with allowed values;
- `final_processing=creative` with missing/blank `creative_prompt` returns 400;
- `final_processing=standard` ignores `creative_prompt` and does not start
  Creative;
- unknown form fields are still rejected.

## Pipeline Semantics

Standard plans remain unchanged.

Creative plans append `generative_processing` after the last technical stage:

```text
already_positive + off + creative:
  decode_positive, generative_processing

already_positive + telea/lama + creative:
  decode_positive, ai_restoration, generative_processing

bw_negative + off + creative:
  decode_bw, negative_conversion, tone_normalization,
  positive_artifact_writer, generative_processing

bw_negative + telea/lama + creative:
  decode_bw, negative_conversion, tone_normalization,
  positive_artifact_writer, ai_restoration, generative_processing
```

Source selection uses only `ProcessingContext`:

```text
restored artifact
positive artifact
context.working_positive
original artifact
```

The selected source is converted to a temporary 8-bit PNG for the provider.
Existing `original`, `positive`, and `restored` artifacts are not modified.

Creative success saves only:

```text
ArtifactType.CREATIVE
data/jobs/{job_id}/{image_id}/creative/{safe_stem}_creative.png
```

Creative failure is recoverable. Prior technical artifacts remain visible, the
`creative` artifact is absent, and image/job status can become
`partial_success`.

## Runtime Config

The default provider is CLI-first stable-diffusion.cpp using Agent 1's measured
FLUX Q4 Vulkan path. Defaults are repo-relative and can be overridden:

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

Default command shape follows Agent 1:

```text
sd-cli
--diffusion-model experiments/creative_edit/models/flux/flux1-kontext-dev-Q4_K_M.gguf
--vae experiments/creative_edit/models/flux/ae.safetensors
--clip_l experiments/creative_edit/models/flux/clip_l.safetensors
--t5xxl experiments/creative_edit/models/flux/t5xxl_fp16.safetensors
-r <temporary source.png>
-p <creative_prompt>
-o <temporary creative.png>
--cfg-scale 1.0
--sampling-method euler
--steps 12
--backend diffusion=vulkan0,te=cpu,vae=cpu
--params-backend diffusion=vulkan0,te=cpu,vae=cpu
--max-vram vulkan0=8
--vae-tiling
-v
-W 640
-H 640
-s 1118877715456453
```

Missing runtime or model files raise a recoverable Creative error with an
actionable message. No model download happens during normal processing.

## Tests Run

```text
.venv/bin/python -m pytest tests/test_generative.py tests/test_api.py
32 passed

.venv/bin/python -m pytest
72 passed
```

## Known Limitations

- Real stable-diffusion.cpp provider smoke was not run by Agent 2; Agent 1
  already measured the Vulkan CLI path.
- Server mode remains unintegrated and unverified for production. The provider
  is CLI per image for predictable cleanup.
- `POST /jobs` is still synchronous, so Creative jobs can block for minutes.
- The default FLUX.1 Kontext dev path is non-commercial per Agent 1. Qwen
  Apache-2.0 candidates remain future bounded checks.
- No frontend controls or TypeScript types were updated in this Agent 2 pass.
- No scheduler, warm model pool, or managed `sd-server` lifecycle was added.

## Agent 3 Notes

- Update frontend types: `FinalProcessingMode`, `ArtifactType` includes
  `creative`, `Job.final_processing`.
- Send `final_processing` for all jobs.
- Send `creative_prompt` only when final processing is `creative`.
- Add Final Processing segmented control and prompt UX.
- Render artifact order: `original`, `positive`, `restored`, `creative`.
- Keep frontend talking only to FilmPipe API, never to stable-diffusion.cpp.
- Preserve standard scenarios and recoverable Creative failure display.
