# FilmPipe Creative Edit Experiment

Isolated research scaffold for the future FilmPipe Creative capability:

```text
latest successful technical result + text prompt -> local image-edit model -> creative artifact
```

This is not production integration. It does not change FilmPipe backend, API,
domain, or frontend code.

## Manual Download Policy

This experiment intentionally does not auto-download model weights or runtime
artifacts. Put large files under this ignored experiment tree yourself:

```text
experiments/creative_edit/
  runtime/   stable-diffusion.cpp checkout/build or copied binaries
  models/    GGUF/safetensors model files
  source/    input photos for testing
  results/   generated outputs and metrics
```

List the required files and URLs:

```bash
python experiments/creative_edit/download_models.py --candidate flux_kontext_q4
python experiments/creative_edit/download_models.py --candidate qwen_edit_2509_q2
python experiments/creative_edit/download_models.py --candidate qwen_edit_2511_q2
```

After placing files, write a local manifest with sizes and hashes:

```bash
python experiments/creative_edit/download_models.py --candidate flux_kontext_q4 --check
```

## Runtime

Preferred runtime is `stable-diffusion.cpp`.

Source:

- https://github.com/leejet/stable-diffusion.cpp
- Build docs: https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/build.md
- Server docs: https://github.com/leejet/stable-diffusion.cpp/blob/master/examples/server/api.md

Manual CUDA build location expected by `config/default.json`:

```text
experiments/creative_edit/runtime/sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan/sd-cli
experiments/creative_edit/runtime/sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan/sd-server
```

The local `sd-master-de298c2-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip` archive
contains `libggml-vulkan.so`. If `sd-cli --list-devices` does not show a
`vulkan0` device, fix the system GPU driver/Vulkan stack before running FLUX.
For best RTX 3060 performance, a CUDA-enabled archive or a source build with
`-DSD_CUDA=ON` is still preferred.

Suggested manual build commands:

```bash
mkdir -p experiments/creative_edit/runtime
cd experiments/creative_edit/runtime
git clone --recursive https://github.com/leejet/stable-diffusion.cpp
cd stable-diffusion.cpp
cmake -B build -DSD_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j
```

If you use a different prebuilt binary, either update `config/default.json` or
pass `--sd-cli /path/to/sd-cli`.

## Model Placement

First practical candidate:

```text
models/flux/flux1-kontext-dev-Q4_K_M.gguf
models/flux/ae.safetensors
models/flux/clip_l.safetensors
models/flux/t5xxl_fp16.safetensors
```

URLs:

- FLUX Kontext Q4 GGUF: https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/blob/main/flux1-kontext-dev-Q4_K_M.gguf
- FLUX VAE official path: https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/ae.safetensors
- FLUX VAE public fallback checked during research: https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/blob/main/split_files/vae/ae.safetensors
- `clip_l`: https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/clip_l.safetensors
- `t5xxl_fp16`: https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/t5xxl_fp16.safetensors

Bounded Qwen check:

```text
models/qwen/Qwen-Image-Edit-2509-Q2_K.gguf
models/qwen/qwen-image-edit-2511-Q2_K.gguf
models/qwen/qwen_image_vae.safetensors
models/qwen/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf
models/qwen/mmproj-BF16.gguf
```

URLs:

- Qwen 2509 GGUF: https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF
- Qwen 2511 GGUF: https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF
- Qwen VAE: https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/tree/main/split_files/vae
- Qwen2.5-VL GGUF: https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF
- Qwen2.5-VL mmproj: https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/blob/main/mmproj-BF16.gguf

If a downloaded filename differs, rename it to the local path above or edit
`config/default.json`.

## Run

Put representative inputs under `source/`, for example:

```text
source/already_positive.png
source/filmpipe_positive.png
source/restored_like.png
```

Create the ignored source directory first if needed:

```bash
mkdir -p experiments/creative_edit/source
```

Dry run first:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --dry-run
```

CLI experiment:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --width 640 \
  --height 640 \
  --steps 12
```

Server API viability smoke, after manually starting `sd-server` with the
`server_command.txt` generated by a dry run:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --mode server-native \
  --input experiments/creative_edit/source/already_positive.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --dry-run
```

Then start the printed server command and rerun without `--dry-run`.

Each run writes:

```text
results/<timestamp>_<candidate>_<input>/
  input.*
  command.txt
  server_command.txt       # server modes only
  output.png               # if inference succeeds
  stdout.log / stderr.log  # CLI mode
  *_request.json           # server modes
  metrics.json
```

`metrics.json` records elapsed time, command/API shape, file sizes, and
`nvidia-smi` samples when the NVIDIA driver is available.

## Research Notes

As of 2026-08-17, upstream stable-diffusion.cpp lists image-edit support for
`FLUX.1-Kontext-dev` and the Qwen Image Edit series, and exposes OpenAI,
WebUI-compatible, and native server APIs. The native `/sdcpp/v1/img_gen` API
accepts image fields as base64/data URLs and returns an async job.

License posture:

- FLUX.1 Kontext dev GGUF inherits the FLUX.1 dev non-commercial license.
- Qwen Image Edit 2509/2511 and Qwen2.5-VL are Apache-2.0.

Initial sandboxed blocker observed in this session:

```text
nvidia-smi failed because it could not communicate with the NVIDIA driver.
```

Real VRAM/time/quality measurements require the manual assets above and a
working NVIDIA driver. With escalated host execution and the Vulkan runtime,
FLUX Q4 was successfully measured on the local RTX 3060 12 GB.

## Measured Local Results

Measured on RTX 3060 12 GB through the Vulkan release runtime after freeing
the existing FilmPipe backend VRAM:

| Run | Result | Time | Peak VRAM | Notes |
| --- | --- | --- | --- | --- |
| 512x512, 4 steps | success | 45.4s | 8759 MiB | Valid PNG, soft smoke output |
| 640x640, 4 steps | success | 66.8s | 10335 MiB | Better size, still smoke quality |
| 640x640, 12 steps | success | 116.8s | 10307 MiB | Current practical baseline |
| 512x768, 12 steps, `--strength 0.35` | success | 105.6s | 10198 MiB | Best current small-horns edit; slight color drift |
| 768x768, 4/8 steps | failed | 24-35s | 9063-11280 MiB | Vulkan OOM while allocating diffusion compute buffer |

Current recommended first local run:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/HR.png \
  --prompt "preserve the composition and make the photo look like a clean archival print" \
  --width 640 \
  --height 640 \
  --steps 12
```

For a more local image edit, lower the denoising strength:

```bash
python experiments/creative_edit/run_experiment.py \
  --candidate flux_kontext_q4 \
  --input experiments/creative_edit/source/HR.png \
  --prompt "same vintage black-and-white child portrait, preserve face, eyes, mouth, pose, hair, lighting and background; add only two tiny cute devil horn clips on a thin hairband above the hair" \
  --width 512 \
  --height 768 \
  --steps 12 \
  --strength 0.35
```

Output examples:

```text
results/20260817-214827_flux_kontext_q4_HR/output.png  # 512x512, 4 steps
results/20260817-215044_flux_kontext_q4_HR/output.png  # 640x640, 4 steps
results/20260817-215236_flux_kontext_q4_HR/output.png  # 640x640, 12 steps
results/20260817-220309_flux_kontext_q4_HR/output.png  # 512x768, horns, strength 0.35
```
