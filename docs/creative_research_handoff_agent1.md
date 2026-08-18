# FilmPipe Creative Research Handoff - Agent 1

Дата: 2026-08-17.

Статус: research + isolated experiment scaffold prepared and measured locally.
Heavy model/runtime downloads were not performed by request; the user placed
the files manually. Production backend/frontend/API/domain code was not
changed.

## Scope Completed

- Read `docs/CREATIVE_PIPELINE_HANDOFF.md` fully.
- Verified current stable-diffusion.cpp upstream docs for:
  - image-edit model support;
  - FLUX.1 Kontext dev;
  - Qwen Image Edit, 2509, 2511;
  - server API families and native image API;
  - CUDA build and backend/parameter placement controls.
- Created isolated experiment scaffold under `experiments/creative_edit/`.
- Updated `.gitignore` only for generated experiment paths:
  - `experiments/creative_edit/models/`
  - `experiments/creative_edit/results/`
  - `experiments/creative_edit/source/`
  - `experiments/creative_edit/runtime/`
- Added manual model placement helper, runnable experiment script, and config:
  - `experiments/creative_edit/README.md`
  - `experiments/creative_edit/download_models.py`
  - `experiments/creative_edit/run_experiment.py`
  - `experiments/creative_edit/config/default.json`

## Local Runtime Diagnostics

Initial sandboxed `nvidia-smi` could not talk to the NVIDIA driver:

```text
NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.
```

Runtime update: the user first placed
`sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64.zip` under
`experiments/creative_edit/runtime/`. `sd-cli --help` and `sd-server --help`
work and report commit `de298c2`, but `sd-cli --list-devices` reports only:

```text
CPU	AMD Ryzen 9 7900X 12-Core Processor
```

No CUDA/Vulkan backend library was present in that archive. It is useful for
CLI/schema checks, but not recommended for the FLUX RTX 3060 benchmark.

The user then placed
`sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip`. It contains
`libggml-vulkan.so`, and the experiment config now points at that runtime with
`vulkan0` placement flags. However, current device probing says:

```text
ggml_vulkan: No devices found.
CPU	AMD Ryzen 9 7900X 12-Core Processor
```

System checks also show no `/dev/dri`, no `vulkaninfo` command, and `nvidia-smi`
still could not communicate with the NVIDIA driver from the sandbox. The Vulkan
runtime was staged, but real GPU inference required escalated host execution.

Later in the session the user freed the GPU memory that was held by the
FilmPipe backend. With escalated host execution, the Vulkan runtime detected:

```text
Vulkan0	NVIDIA GeForce RTX 3060
Vulkan1	AMD Ryzen 9 7900X integrated RADV device
CPU	AMD Ryzen 9 7900X 12-Core Processor
```

FLUX Q4 inference was then run successfully through `Vulkan0`. No current local
GPU blocker remains for the Vulkan CLI path as long as enough VRAM is free.

The user also explicitly requested that large model downloads be performed
manually by the user. The scaffold therefore prints model URLs and verifies
local placement; it intentionally does not auto-download weights.

## Upstream Runtime Findings

Runtime: `stable-diffusion.cpp`

Upstream HEAD checked with `git ls-remote` on 2026-08-17:

```text
de298c225bed97c3f9026b73cd7b71e7879bd41b
```

Official docs used:

- https://github.com/leejet/stable-diffusion.cpp
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/kontext.md
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/qwen_image_edit.md
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/build.md
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/backend.md
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/examples/server/api.md
- https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/examples/server/README.md

Confirmed:

- README lists Image Edit Models including `FLUX.1-Kontext-dev` and `Qwen Image Edit series`.
- Build docs support CUDA via `cmake .. -DSD_CUDA=ON`.
- Backend docs support `--backend`, `--params-backend`, `--max-vram`, `--auto-fit`, CPU/disk parameter placement, and module-specific assignment.
- Server docs expose:
  - `POST /v1/images/edits`
  - `POST /sdapi/v1/img2img`
  - `POST /sdcpp/v1/img_gen`
- Native `/sdcpp/v1/img_gen` is async and accepts `init_image` / `ref_images` as raw base64 or data URLs.

## Candidate Matrix

| Candidate | License | Files / side models | Approx file footprint | Current status |
| --- | --- | --- | --- | --- |
| FLUX.1 Kontext dev Q4_K_M | FLUX.1 dev non-commercial | Kontext GGUF + VAE + clip_l + t5xxl | ~17.3 GB with fp16 T5XXL | Recommended first manual benchmark |
| FLUX.1 Kontext dev Q5_K_M | FLUX.1 dev non-commercial | Same side models | ~18.8 GB with fp16 T5XXL | Try after Q4 if VRAM headroom exists |
| Qwen Image Edit 2509 Q2_K | Apache-2.0 | diffusion GGUF + Qwen VAE + Qwen2.5-VL GGUF + mmproj | ~13.4 GB | Bounded legal-friendly check, not recommended until measured |
| Qwen Image Edit 2511 Q2_K | Apache-2.0 | diffusion GGUF + Qwen VAE + Qwen2.5-VL GGUF + mmproj | ~13.8 GB | Bounded legal-friendly check, requires `qwen_image_zero_cond_t=true` |

Important licensing note:

- FLUX Kontext dev is acceptable for home/non-commercial experiments only unless
  a separate license decision is made.
- Qwen Image Edit and Qwen2.5-VL are Apache-2.0 and cleaner for future
  non-home/commercial use.

## Manual Asset Paths

First FLUX benchmark:

```text
experiments/creative_edit/models/flux/flux1-kontext-dev-Q4_K_M.gguf
experiments/creative_edit/models/flux/ae.safetensors
experiments/creative_edit/models/flux/clip_l.safetensors
experiments/creative_edit/models/flux/t5xxl_fp16.safetensors
```

URLs:

- https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/blob/main/flux1-kontext-dev-Q4_K_M.gguf
- https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/ae.safetensors
- https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/blob/main/split_files/vae/ae.safetensors
- https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/clip_l.safetensors
- https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/t5xxl_fp16.safetensors

Note: the official BFL `ae.safetensors` URL returned `401 GatedRepo` without
authentication during this session. The Comfy-Org/Lumina repackaged VAE URL was
publicly reachable by HEAD request and is documented as a fallback to verify.

Qwen bounded check:

```text
experiments/creative_edit/models/qwen/Qwen-Image-Edit-2509-Q2_K.gguf
experiments/creative_edit/models/qwen/qwen-image-edit-2511-Q2_K.gguf
experiments/creative_edit/models/qwen/qwen_image_vae.safetensors
experiments/creative_edit/models/qwen/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf
experiments/creative_edit/models/qwen/mmproj-BF16.gguf
```

URLs:

- https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF
- https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF
- https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files/vae
- https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF
- https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/blob/main/mmproj-BF16.gguf

Use:

```bash
python experiments/creative_edit/download_models.py --candidate flux_kontext_q4
python experiments/creative_edit/download_models.py --candidate flux_kontext_q4 --check
```

The helper does not download files.

## Runtime Placement Recommendation To Benchmark

Recommended first command shape is encoded in:

```text
experiments/creative_edit/config/default.json
```

For current local FLUX Q4 Vulkan config:

```text
--diffusion-model models/flux/flux1-kontext-dev-Q4_K_M.gguf
--vae models/flux/ae.safetensors
--clip_l models/flux/clip_l.safetensors
--t5xxl models/flux/t5xxl_fp16.safetensors
-r <input>
-p <prompt>
-o <output>
--cfg-scale 1.0
--sampling-method euler
--steps 12
--backend diffusion=vulkan0,te=cpu,vae=cpu
--params-backend diffusion=vulkan0,te=cpu,vae=cpu
--max-vram vulkan0=8
--vae-tiling
-v
```

`experiments/creative_edit/run_experiment.py` also accepts `--strength` and
passes it through to `sd-cli` for image-edit strength tuning.

Rationale:

- Keep diffusion on the GPU backend.
- Keep text encoders and VAE on CPU initially to preserve RTX 3060 VRAM.
- Cap GPU memory below 12 GB with `--max-vram vulkan0=8` for the current Vulkan runtime.
- Use FLUX Kontext's documented `--cfg-scale 1.0`.
- Prefer predictable cleanup through CLI first if server behavior is not yet
  proven.

For a future CUDA-enabled runtime, switch `vulkan0` back to `cuda0`.

Alternative to test after CLI success:

```text
--auto-fit --max-vram vulkan0=8
```

Do not recommend `--auto-fit` for production until its printed placement and
VRAM behavior are measured on this host.

## Experiment Commands

Dry run:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --dry-run
```

CLI run:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print"
```

Server mode dry run prints the server start command:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --mode server-native \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --dry-run
```

Expected output bundle:

```text
experiments/creative_edit/results/<timestamp>_<candidate>_<input>/
  input.*
  command.txt
  server_command.txt
  output.png
  stdout.log
  stderr.log
  metrics.json
```

## Prompt Set To Run

Use at least these prompts once assets/driver are ready:

1. Low-change archival edit:
   `preserve the composition and make the photo look like a clean archival print`
2. Style transformation:
   `turn this into a cinematic 1970s editorial photograph while preserving the scene layout`
3. Object/text edit:
   `change the sign text to FILMPIPE while preserving the original sign shape and lighting`
4. Identity/composition preservation:
   `keep the same person, pose, and background, but improve lighting and remove scanning artifacts`

Inputs should include:

- already-positive photo;
- FilmPipe-generated positive;
- restored-like image, or a copied positive named as restored.

## Measurement Table

Measured with:

```text
runtime: sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan
device: Vulkan0 / NVIDIA GeForce RTX 3060 12 GB
placement: diffusion=vulkan0,te=cpu,vae=cpu
params placement: diffusion=vulkan0,te=cpu,vae=cpu
max-vram: vulkan0=8
input: experiments/creative_edit/source/HR.png
prompt: preserve the composition and make the photo look like a clean archival print
```

| Candidate | Mode | Output | Wall time | Sampling time | VAE decode | Peak VRAM | Result path | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FLUX Q4 | CLI Vulkan | 512x512 PNG, 4 steps | 45.4s | 17.7s | 15.8s | 8759 MiB | `experiments/creative_edit/results/20260817-214827_flux_kontext_q4_HR/output.png` | Valid smoke output, soft |
| FLUX Q4 | CLI Vulkan | 640x640 PNG, 4 steps | 66.8s | 26.4s | 27.2s | 10335 MiB | `experiments/creative_edit/results/20260817-215044_flux_kontext_q4_HR/output.png` | Valid smoke output |
| FLUX Q4 | CLI Vulkan | 640x640 PNG, 12 steps | 116.8s | 76.0s | 27.6s | 10307 MiB | `experiments/creative_edit/results/20260817-215236_flux_kontext_q4_HR/output.png` | Current practical baseline |
| FLUX Q4 | CLI Vulkan | 640x640 PNG, 12 steps | 115.9s | 75.4s | 27.3s | 10199 MiB | `experiments/creative_edit/results/20260817-215654_flux_kontext_q4_HR/output.png` | Devil horns appeared, but large and strongly restyled |
| FLUX Q4 | CLI Vulkan | 512x768 PNG, 12 steps, `--strength 0.35` | 105.6s | 66.4s | 25.9s | 10198 MiB | `experiments/creative_edit/results/20260817-220309_flux_kontext_q4_HR/output.png` | Best horn edit so far; portrait preserved better, horns still slightly colored |
| FLUX Q4 | CLI Vulkan | 768x768, 8 steps | failed | failed after step 1 | n/a | 11280 MiB | `experiments/creative_edit/results/20260817-214649_flux_kontext_q4_HR/` | Vulkan OOM allocating diffusion compute buffer |
| FLUX Q4 | CLI Vulkan | 768x768, 4 steps | failed | failed before step 1 completed | n/a | 9063 MiB | `experiments/creative_edit/results/20260817-214950_flux_kontext_q4_HR/` | Vulkan OOM allocating diffusion compute buffer |
| Qwen 2509/2511 | CLI/server | not run | n/a | n/a | n/a | n/a | n/a | Weights not manually provided; still bounded future check |

Quality notes:

- 512/4 preserved the portrait composition and generated a valid nonblank PNG,
  but it was very soft.
- 640/12 preserved the broad composition and cleaned some scratch/noise feel,
  but introduced strong painterly/skin texture changes and identity drift.
- 512x768/12 with `--strength 0.35` is the best current prompt-edit result for
  adding small devil horns; it still needs prompt/negative-prompt tuning if the
  horns must remain monochrome.
- For production, Agent 2 should expose no quality claims yet. The provider
  needs further prompt/strength/step/resolution tuning and probably a CUDA build
  comparison.

## Recommendation For Agent 2

Do not integrate Creative into production from this handoff alone unless the
manual experiment has been run and `metrics.json` results are attached.

Provisional recommendation to benchmark first:

```text
runtime: stable-diffusion.cpp
mode: Vulkan CLI works; CUDA-enabled CLI should still be compared if available
model: FLUX.1 Kontext dev GGUF Q4_K_M
side models: ae.safetensors, clip_l.safetensors, t5xxl_fp16.safetensors
placement: diffusion=vulkan0,te=cpu,vae=cpu, max-vram vulkan0=8
output: PNG
license: non-commercial only
```

Production fallback if FLUX license is unacceptable:

```text
Qwen Image Edit 2509 or 2511 Q2/Q3 with Qwen2.5-VL side model,
but only after a measured bounded run proves it is usable on RTX 3060 12 GB.
```

Integration notes for Agent 2 once measurements exist:

- Keep Creative as an optional late processor.
- Do not start/load stable-diffusion.cpp for `final_processing=standard`.
- Prefer CLI adapter first if server edit APIs do not reliably return edited
  images for the chosen model.
- Provider must receive an explicit source image path/temp file from the
  processing context; it must not inspect FilmPipe storage folders.
- Save output as a separate `creative` artifact.
- Report model/runtime missing errors as recoverable Creative failures.

## Verification Run

Lightweight script validation performed:

```text
python -m py_compile experiments/creative_edit/download_models.py experiments/creative_edit/run_experiment.py
python experiments/creative_edit/download_models.py --candidate flux_kontext_q4
python experiments/creative_edit/run_experiment.py --candidate flux_kontext_q4 --prompt test --dry-run --skip-asset-check
python experiments/creative_edit/run_experiment.py --candidate flux_kontext_q4 --mode server-native --prompt test --dry-run --skip-asset-check
python -m py_compile experiments/creative_edit/run_experiment.py
```

Full GPU inference verification succeeded for the Vulkan CLI path on RTX 3060
12 GB. Server-mode inference and Qwen candidates remain unverified.
