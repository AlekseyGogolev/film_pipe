# FilmPipe Creative Pipeline Handoff

Дата подготовки: 2026-08-17.

Этот документ является handoff для последовательной работы трех агентов над будущей capability:

```text
последний успешный технический результат
+ text prompt
-> локальная image-edit модель
-> creative artifact
```

В рамках подготовки этого handoff production-код FilmPipe не менялся. Субагенты не запускались.

## 1. Current Architecture Findings

### Runtime model

Текущий FilmPipe MVP имеет две независимые runtime-опции:

```text
input_processing = already_positive | bw_negative
restoration      = off | telea | lama
```

Они представлены в `backend/filmpipe/domain/models.py`:

- `InputProcessingMode`
- `RestorationMode`
- `ProcessingOptions`
- `ArtifactType`
- `ProcessingStatus`
- `ProcessingJob`
- `ImageProcessingResult`
- `ProcessingError`

Активные `ArtifactType` сейчас только:

```text
original
positive
restored
```

`colorized` и `creative` были намеренно убраны из активного domain enum в предыдущем refactor cleanup, потому что они не были реальными runtime-состояниями. Возвращать нужно только `creative` как реализуемую capability, не resurrect старые заглушки и не добавлять `colorize`.

### Processing contracts

Ключевые контракты находятся в `backend/filmpipe/domain/processor.py`:

- `ArtifactStore` protocol: `save_original`, `save_artifact`;
- `ProcessingContext`: `job_id`, `image_id`, `filename`, `options`, `artifact_store`, `logger`, `artifacts`, `metadata`, `working_positive`;
- `ProcessorResult`: `image`, `artifacts`, `errors`, `stop_pipeline`;
- `Processor` protocol: `name`, `optional`, `process(image, context)`.

`ProcessingContext.artifacts` уже является правильным orchestration/context boundary для выбора предыдущих публичных результатов. `context.working_positive` уже является internal base-result для `already_positive`, где публичный `positive` artifact не создается.

### Pipeline and source of truth

`backend/filmpipe/processing/engine.py` строит pipeline через `default_processors(options)`:

```text
already_positive + off:
  decode_positive

already_positive + telea/lama:
  decode_positive, ai_restoration

bw_negative + off:
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer

bw_negative + telea/lama:
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration
```

Важный текущий стиль: если stage выключен, processor не добавляется в список. Нет runtime no-op processor для `restoration=off`. Creative должен сохранить этот стиль.

`ProcessingPipeline.run()`:

- последовательно вызывает processors;
- добавляет artifacts в `ImageProcessingResult` и `context.artifacts`;
- меняет `current_image`, если processor вернул `image`;
- ловит исключения;
- optional failure не останавливает pipeline;
- mandatory failure останавливает;
- статус вычисляется через `_resolve_status()`.

Partial-success уже подходит для Creative:

```text
has_recoverable_result = result.has_positive or context.working_positive is not None
```

Для `bw_negative` recoverable result - публичный `positive`. Для `already_positive` recoverable result - internal `working_positive` плюс публичный immutable `original`.

### Image processors

`backend/filmpipe/processing/processors/images.py`:

- `DecodePositiveImageProcessor` декодирует already-positive input, сохраняет dtype/channels, ставит `context.working_positive = image_data.data`, не создает public `positive`;
- `DecodeBWImageProcessor` декодирует и приводит B&W negative к grayscale normalized float32;
- `NegativeConverterProcessor` инвертирует negative;
- `ToneNormalizerProcessor` нормализует тон;
- `PositiveArtifactWriterProcessor` пишет 16-bit TIFF `positive`, затем ставит `context.working_positive` в этот 16-bit output.

Для Creative это значит: source selection не должен искать файлы по layout. Он должен читать уже известные FilmPipe concepts:

```text
context.artifacts[RESTORED]
context.artifacts[POSITIVE]
context.artifacts[ORIGINAL]
context.working_positive
```

### Restoration

`backend/filmpipe/processing/restoration.py` реализует:

- `AIRestorationProcessor(optional=True)`;
- `DefectDetector` protocol;
- `Restorer` protocol;
- `MicrosoftScratchDetector`;
- `TeleaRestorer`;
- `LaMaRestorer`;
- mask postprocessing;
- final composite invariant для restoration.

`AIRestorationProcessor` читает `context.working_positive`, а не `ArtifactType.POSITIVE`. Это важный precedent: поздние processors должны зависеть от context, а не от filesystem layout.

Restoration failure уже recoverable:

- detector failure -> `ProcessingError(recoverable=True)`, `stop_pipeline=False`;
- restorer failure -> то же;
- base result сохраняется.

Creative должен повторить эту семантику, но без mask/final composite invariant: creative сознательно может менять фотографию. Единственный invariant: он не заменяет `original`, `positive`, `restored`, а пишет отдельный `creative`.

### Application / JobService

`backend/filmpipe/application/jobs.py`:

- `JobService.process(inputs, options, job_id=None)` создает `ProcessingJob`;
- для каждого input вызывает `process_image(...)`;
- каждый image получает отдельный `image_id`;
- failure одного image не останавливает batch;
- `ProcessingJob.recompute_status()` сводит статусы images.

Pipeline factory already option-aware. Tests use injectable pipeline factories.

Potential issue for Creative lifecycle: `JobService` сейчас строит отдельный pipeline per image. Если Creative runtime будет managed server, Agent 2 должен решить, запускать его per job, per image, или использовать внешний configured endpoint. Не проектировать сложный GPU scheduler; нужен простой lifecycle.

### ArtifactStore

`backend/filmpipe/infrastructure/storage.py`:

- `FileSystemArtifactStore(root="data/jobs")`;
- `save_original()` копирует immutable input;
- `save_artifact()` кладет derivative в `data/jobs/{job_id}/{image_id}/{artifact_type}/`;
- имя строится как `{safe_stem}_{artifact_type.value}{suffix}`;
- overwrite запрещен.

После добавления `ArtifactType.CREATIVE` storage автоматически сможет писать:

```text
data/jobs/{job_id}/{image_id}/creative/{safe_stem}_creative.png
```

или другой lossless/browser-friendly формат, выбранный Agent 2.

### REST API

`backend/filmpipe/api/app.py`:

- `POST /jobs` принимает multipart fields только `files`, `input_processing`, `restoration`;
- unknown form fields дают 400;
- job creation синхронный;
- response exposes API concepts, не filesystem paths;
- artifact routes используют `artifact_type: ArtifactType`, поэтому новый enum автоматически расширит path parser;
- preview endpoint рендерит stored artifact в PNG через `render_preview_png`;
- download endpoint возвращает stored bytes;
- batch ZIP исключает `original`, но включает все generated artifacts.

Creative integration backend-side должен расширить strict form validation. Минимальный будущий request:

```text
files
input_processing=already_positive|bw_negative
restoration=off|telea|lama
final_processing=standard|creative
creative_prompt=...
```

`creative_prompt` должен быть обязателен только для `final_processing=creative`.

### Frontend

Frontend живет в:

```text
frontend/src/App.tsx
frontend/src/api.ts
frontend/src/types.ts
frontend/src/styles.css
```

Current UI:

```text
Input Processing
[ Already Positive ] [ Negative -> Positive ]

Restoration
[ Off ] [ TELEA ] [ LaMa ]
```

Current `ArtifactType` union:

```ts
"original" | "positive" | "restored"
```

Artifacts are rendered only if returned by API, ordered:

```text
original, positive, restored
```

Agent 3 should extend this same control pattern:

```text
Final Processing
[ Standard ] [ Creative ]
```

When `Creative` is selected, show prompt. When `Standard`, do not send prompt unless Agent 2 intentionally supports it.

### Tests

Relevant existing tests:

- `tests/test_pipeline.py`: success, mandatory failure, optional failure after positive, optional failure after working positive;
- `tests/test_image_processing.py`: all six pipeline plans, already-positive semantics, positive writer;
- `tests/test_restoration.py`: restoration off skip, fake TELEA/LaMa success, detector/restorer failure partial success, batch failure isolation;
- `tests/test_api.py`: artifact matrix, strict field validation, preview/download, ZIP, invalid values;
- `tests/test_engine.py`: direct processing without HTTP dependency;
- `tests/test_logging.py`: user error excludes stack trace, logs keep context;
- `tests/test_storage.py`: immutable original, no overwrite, no empty dirs.

Creative tests should follow the existing fake-adapter style and must not run heavy GPU inference in the normal unit suite.

## 2. Recommended Target Architecture

### Target runtime model

Add a third independent option:

```text
input_processing = already_positive | bw_negative
restoration      = off | telea | lama
final_processing = standard | creative
```

Recommended domain additions:

```text
FinalProcessingMode:
  STANDARD = "standard"
  CREATIVE = "creative"

ProcessingOptions:
  input_processing: InputProcessingMode
  restoration: RestorationMode
  final_processing: FinalProcessingMode = STANDARD
  creative_prompt: str | None = None

ArtifactType:
  CREATIVE = "creative"
```

Do not add `colorize` now.

Do not add a generic `prompt` field if the capability is specifically Creative. A specific `creative_prompt` keeps the contract clearer and avoids reintroducing removed generic fields.

### Pipeline composition

Pipeline construction should remain explicit:

```text
base input processors
-> optional ai_restoration if restoration != off
-> optional generative_processor if final_processing == creative
```

Expected execution examples:

```text
already_positive + off + standard:
  decode_positive

already_positive + off + creative:
  decode_positive, generative_processing

already_positive + lama + creative:
  decode_positive, ai_restoration, generative_processing

bw_negative + off + creative:
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, generative_processing

bw_negative + lama + creative:
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration, generative_processing
```

`standard` must not start or contact the creative runtime.

### Source selection

Create a small processing-layer source selector used by `GenerativeProcessor`.
It should select only from `ProcessingContext`, not from filesystem layout:

```text
1. ArtifactType.RESTORED if present in context.artifacts
2. else ArtifactType.POSITIVE if present in context.artifacts
3. else already-positive base:
   - use ArtifactType.ORIGINAL if the provider accepts a file path
   - or use context.working_positive if the provider needs pixels/temp normalized image
```

Recommended internal concept:

```text
CreativeSource:
  kind: "restored" | "positive" | "working_positive" | "original"
  path: Path | None
  image: Any | None
  artifact: Artifact | None
```

The provider/adapter receives an explicit `CreativeRequest`:

```text
source image path or prepared temp image
prompt
job_id
image_id
output format
runtime/model config
```

The provider must not inspect FilmPipe storage folders to decide what to edit.

### Processor/provider boundary

Recommended structure:

```text
backend/filmpipe/processing/generative.py
  GenerativeProcessor(optional=True)
  CreativeProvider protocol
  CreativeRequest / CreativeResult dataclasses
  select_creative_source(context, image)
  StableDiffusionCppProvider or adapter
```

Keep ML/runtime specifics out of domain/API/frontend. Domain knows `final_processing`, `creative_prompt`, and `ArtifactType.CREATIVE`; it does not know GGUF, CUDA, stable-diffusion.cpp, model paths, or sampling knobs.

Provider responsibilities:

- call local inference runtime;
- handle process/server errors;
- write or return generated image bytes/path;
- expose metadata useful for logs/context;
- never decide artifact type or FilmPipe source semantics.

Processor responsibilities:

- validate `creative_prompt` in processing context as a safety net;
- select source via context;
- call provider;
- save output through `context.artifact_store.save_artifact(..., ArtifactType.CREATIVE, ...)`;
- return `ProcessorResult.success(..., artifacts=[creative])`;
- on runtime failure return recoverable `ProcessingError` with `stop_pipeline=False`.

### Runtime lifecycle

Target hardware:

```text
CPU: Ryzen 9 7900X, 12C / 24T
RAM: 64 GB DDR5-6000
GPU: RTX 3060 12 GB, CUDA
```

Creative must not assume VRAM is permanently free. FilmPipe can already run:

```text
Microsoft detector
TELEA
LaMa
```

Simple lifecycle strategy, not a scheduler:

1. If `final_processing=standard`, do not load/start creative runtime.
2. If `final_processing=creative`, run restoration first.
3. Before creative inference, release obvious in-process AI memory where practical:
   - let restoration processor instances go out of scope naturally;
   - consider best-effort `torch.cuda.empty_cache()` only in a narrow processing utility if PyTorch is already imported;
   - prefer a separate stable-diffusion.cpp process/server so its VRAM lifetime is visible and terminable.
4. Creative provider should support one of:
   - configured already-running local `sd-server` endpoint;
   - FilmPipe-managed `sd-server` subprocess started on demand and terminated after job;
   - CLI invocation per image for the first safe integration if server mode proves unsuitable.
5. Use stable-diffusion.cpp placement knobs from Agent 1 handoff:
   - GGUF quantization;
   - CUDA backend;
   - CPU/RAM/disk parameter placement;
   - `--max-vram`;
   - `--auto-fit`;
   - text encoder / VAE CPU placement;
   - VAE tiling if needed.

The first production version should prefer predictable cleanup over maximum throughput.

### API semantics

`POST /jobs` should remain a single FilmPipe API endpoint. Do not create a separate creative endpoint and do not let frontend talk directly to stable-diffusion.cpp.

Recommended request fields:

```text
files
input_processing
restoration
final_processing
creative_prompt
```

Recommended response additions:

```text
final_processing
```

Returning `creative_prompt` is optional. If echoed, treat it as user-visible job metadata, not as runtime internals. It is not required for artifact display.

Validation:

- `final_processing` defaults to `standard`;
- invalid final mode returns clear 400 with allowed values;
- `creative_prompt` is required and trimmed non-empty for `creative`;
- `standard` never starts creative runtime;
- unknown fields continue to be rejected clearly.

### Artifact semantics

Existing standard matrix remains unchanged:

| Input Processing | Restoration | Final | Public Artifacts |
| --- | --- | --- | --- |
| `already_positive` | `off` | `standard` | `original` |
| `already_positive` | `telea/lama` | `standard` | `original`, `restored` |
| `bw_negative` | `off` | `standard` | `original`, `positive` |
| `bw_negative` | `telea/lama` | `standard` | `original`, `positive`, `restored` |

Creative success appends `creative` without replacing anything:

| Input Processing | Restoration | Final | Public Artifacts |
| --- | --- | --- | --- |
| `already_positive` | `off` | `creative` | `original`, `creative` |
| `already_positive` | `telea/lama` | `creative` | `original`, `restored`, `creative` |
| `bw_negative` | `off` | `creative` | `original`, `positive`, `creative` |
| `bw_negative` | `telea/lama` | `creative` | `original`, `positive`, `restored`, `creative` |

Creative failure:

```text
Positive ✓
Restored ✓
Creative ✗
```

Result:

```text
positive/restored artifacts remain
creative is absent
image status partial_success
job status partial_success if applicable
recoverable error stage "creative" or "generative_processing"
```

### Frontend target

Extend existing UI rather than creating a new page:

```text
Input Processing
[ Already Positive ] [ Negative -> Positive ]

Restoration
[ Off ] [ TELEA ] [ LaMa ]

Final Processing
[ Standard ] [ Creative ]

Prompt appears only when Creative is active.
```

Update:

- `frontend/src/types.ts`: `FinalProcessingMode`, `ArtifactType` includes `creative`, `Job.final_processing`;
- `frontend/src/api.ts`: send `final_processing`; send `creative_prompt` only for Creative unless backend contract says otherwise;
- `frontend/src/App.tsx`: state, controls, job meta label, prompt UX, artifact order;
- `frontend/src/styles.css`: responsive layout, no overflow.

Recommended artifact order:

```text
original, positive, restored, creative
```

## 3. Model / Runtime Research Plan

Agent 1 must verify current upstream instead of trusting stale names.

Sources checked for this handoff:

- stable-diffusion.cpp README: https://github.com/leejet/stable-diffusion.cpp
- stable-diffusion.cpp Qwen Image Edit docs: https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/qwen_image_edit.md
- stable-diffusion.cpp FLUX Kontext docs: https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/kontext.md
- stable-diffusion.cpp server API docs: https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/examples/server/api.md
- stable-diffusion.cpp backend placement docs: https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/backend.md
- stable-diffusion.cpp build docs: https://raw.githubusercontent.com/leejet/stable-diffusion.cpp/master/docs/build.md
- FLUX.1 Kontext dev model card: https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev
- FLUX.1 Kontext dev GGUF: https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF
- Qwen Image Edit 2511 model card: https://huggingface.co/Qwen/Qwen-Image-Edit-2511
- Qwen Image Edit 2509 model card: https://huggingface.co/Qwen/Qwen-Image-Edit-2509
- Qwen Image Edit base model card: https://huggingface.co/Qwen/Qwen-Image-Edit
- Qwen Image Edit 2511 GGUF: https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF
- Qwen Image Edit 2509 GGUF: https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF
- Qwen2.5-VL 7B model card: https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct

Current high-level findings as of 2026-08-17:

- stable-diffusion.cpp upstream lists image-edit support for `FLUX.1-Kontext-dev` and `Qwen Image Edit series`.
- stable-diffusion.cpp has an example server with OpenAI-compatible `/v1/images/edits`, WebUI-compatible `/sdapi/v1/img2img`, and native `/sdcpp/v1/img_gen` APIs.
- stable-diffusion.cpp supports CUDA, GGUF, safetensors, and CPU/RAM/disk parameter placement through `--backend`, `--params-backend`, `--max-vram`, and `--auto-fit`.
- FLUX.1 Kontext dev is 12B, image-to-image, strong edit consistency, but licensed under FLUX.1 dev non-commercial license.
- QuantStack FLUX.1 Kontext GGUF has practical quant sizes for RTX 3060: Q4 around 6.8-6.93 GB, Q5 around 8.28-8.42 GB, Q8 around 12.7 GB, before text encoders/VAE/runtime buffers.
- Qwen Image Edit family is Apache-2.0 and production-license friendly. Current stable-diffusion.cpp Qwen docs include Qwen Image Edit, 2509, and 2511.
- Qwen Image Edit 2511 is a 20B model; Unsloth GGUF shows Q2 around 7.47 GB, Q3 around 9.22-10.6 GB, Q4 around 11.9-13.2 GB, Q8 around 21.8 GB, BF16/F16 around 40.9 GB, before text encoder/VAE/runtime buffers.
- Qwen Image Edit 2511 requires `qwen_image_zero_cond_t=true` in stable-diffusion.cpp docs for good edit quality.
- Qwen 2509/2511 use Qwen2.5-VL 7B as text/vision encoder; that encoder is Apache-2.0.

Tentative ranking before real benchmark:

1. FLUX.1 Kontext dev GGUF Q4/Q5 is likely the most practical first desktop runtime on RTX 3060 12 GB, especially if text encoder/VAE are on CPU, but the license is non-commercial. Do not choose it for production/commercial use without a license decision.
2. Qwen Image Edit 2511 or 2509 is legally cleaner under Apache-2.0 and likely higher capability for text/identity/product edits, but heavier. Agent 1 must evaluate it with a bounded attempt or explicitly rule it out with evidence; do not spend half a day forcing Qwen to run if the measured/observed memory movement makes it impractical on 12 GB VRAM.
3. Other stable-diffusion.cpp image-edit models listed upstream, such as LongCat/Boogu/Mage-Flow edit families, can be secondary fallback candidates only if the two primary families fail. They should not distract from the main experiment.

Research priority:

- Primary practical path: get FLUX.1 Kontext dev GGUF Q4 or Q5 running end-to-end as `image + prompt -> image` through stable-diffusion.cpp.
- Bounded legal-friendly check: evaluate Qwen Image Edit 2511/2509 enough to decide whether it is viable for this desktop. If it requires excessive RAM<->VRAM streaming, very slow per-step time, unstable server behavior, or side-model memory beyond the target hardware, rule it out for now with metrics/logs instead of continuing to force it.
- Preserve the license finding in all handoffs: FLUX Kontext dev is acceptable for a home/non-commercial experiment, while Qwen is Apache-2.0 and therefore cleaner if FilmPipe later becomes non-home/commercial.

Agent 1 should produce measured answers, not only docs research:

- image + prompt editing correctness;
- source composition preservation;
- creative strength/drift control;
- GGUF quant choice;
- actual VRAM/RAM/time on RTX 3060 12 GB;
- server API viability versus CLI;
- model startup latency;
- repeated request behavior;
- whether runtime releases VRAM after process/server shutdown;
- output dimensions and up/downscale behavior;
- license and bootstrap constraints.

## 4. Risks

### VRAM and RAM

Qwen Image Edit 20B plus Qwen2.5-VL 7B text/vision encoder can exceed 12 GB VRAM unless quantized/offloaded. FLUX Q4 is much smaller, but text encoders, VAE, activations, and existing restoration runtime can still push the machine over budget. Agent 1 must benchmark whole-command memory, not only model file size.

### Runtime lifecycle

Current restoration is in-process Python/PyTorch for detector/LaMa. Creative via stable-diffusion.cpp should preferably be a separate process or server with explicit lifecycle. Otherwise VRAM can remain occupied between restoration and creative.

### Startup latency

`POST /jobs` is synchronous today. Starting a large creative model per image may make the API appear blocked for minutes. Agent 2 should not invent a job queue in this task, but must surface recoverable failures and log timing. Agent 1 should quantify startup and warm-run latency.

### Server API compatibility

stable-diffusion.cpp has server APIs, but Agent 1 must verify that the chosen image-edit model works through a stable server endpoint with reference image input and all required native args. If not, backend integration may need a CLI adapter first.

### Image resolution and bit depth

FilmPipe positives/restored artifacts are often 16-bit TIFF. Most image-edit diffusion runtimes operate on 8-bit RGB and fixed-ish dimensions. Agent 2 must convert the selected source to a provider-friendly temp image without changing existing artifacts. Creative output can be PNG/JPEG/TIFF, but should be previewable and downloadable through existing API.

### Source selection bugs

The most dangerous integration bug is accidentally editing `original` when `restored` exists, or searching `data/jobs/...` manually. Source selection must be tested through `ProcessingContext`.

### Licensing

FLUX.1 Kontext dev is non-commercial. Qwen Image Edit is Apache-2.0. Quantized GGUF repos generally inherit base model terms. The production default cannot be chosen on quality alone.

### Bootstrap size

Model sets are multi-GB to tens of GB. Bootstrap must be explicit and reproducible, like `experiments/ai_restoration/download_models.py`, and must not auto-download huge weights during normal image processing.

### API contract expansion

API currently rejects unknown fields. Agent 2 must update backend strict validation before Agent 3 sends `final_processing` and `creative_prompt`.

### Legacy field regression

Previous refactor removed generic `prompt`, `mode`, `polarity`, `selected_modes`, and inactive creative/colorize UI. Do not reintroduce stale concepts.

## Prompt / Agent 1 - Research & Experiment

Copy this prompt for Agent 1:

```text
Ты Agent 1 для FilmPipe Creative. Твоя задача - research + isolated experiment, без production integration.

Контекст:
- Репозиторий: /media/horizen/baza/code/film_pipe
- Прочитай docs/CREATIVE_PIPELINE_HANDOFF.md полностью перед действиями.
- FilmPipe production сейчас имеет input_processing=already_positive|bw_negative и restoration=off|telea|lama.
- Creative должен в будущем работать поверх последнего успешного технического результата, но ты НЕ интегрируешь это в production.

Жесткие ограничения:
- Не меняй production backend/frontend/API/domain код.
- Не добавляй production enums/options/API fields.
- Не запускай subagents/background agents.
- Не скачивай модели в production directories.
- Все тяжелые модели, runtime build artifacts, sample inputs/results должны быть под experiments/creative_edit/ или другой явно experiment-only ignored path.
- Не ломай existing experiments/ai_restoration.

Цель:
Выбрать практически пригодную локальную image-edit связку для целевого железа:

CPU: Ryzen 9 7900X, 12C/24T
RAM: 64 GB DDR5-6000
GPU: RTX 3060 12 GB CUDA

Предпочтительный runtime:
- stable-diffusion.cpp, желательно server/API mode.
- Цель - избежать production dependency на PyTorch/Diffusers, если stable-diffusion.cpp реально подходит.

Приоритет эксперимента:
- Основной обязательный практический кандидат: FLUX.1 Kontext dev GGUF Q4/Q5 через stable-diffusion.cpp. Доведи его до рабочего `image + prompt -> image`, если нет конкретного upstream/hardware blocker.
- Qwen Image Edit 2511/2509 нужно проверить честно, но bounded: если по размерам моделей, side encoders, логам, VRAM/RAM или скорости видно, что он превращается в непрактичное перекладывание гигабайтов RAM<->VRAM на RTX 3060 12 GB, останови попытку и явно rule out with evidence.
- Не трать полдня на оживление Qwen любой ценой. Для Agent 2 важнее рабочий FLUX path и честная таблица причин, почему Qwen пока не выбран.
- Обязательно сохрани license note: FLUX Kontext dev = non-commercial, приемлемо для домашнего эксперимента; Qwen = Apache-2.0, более чистый вариант для будущего non-home/commercial сценария.

Обязательное upstream research:
1. Проверь текущий stable-diffusion.cpp upstream:
   - поддержка image edit models;
   - поддержка FLUX.1-Kontext-dev;
   - поддержка Qwen Image Edit series, включая актуальные 2509/2511, если они актуальны;
   - server/API support для image edit;
   - CUDA build/release options;
   - GGUF/quant/offload/auto-fit/max-vram/params-backend knobs.
2. Проверь модели:
   - FLUX.1 Kontext Dev;
   - Qwen Image Edit;
   - Qwen Image Edit 2509;
   - Qwen Image Edit 2511;
   - secondary candidates только если primary candidates не подходят.
3. Для каждого разумного кандидата зафиксируй:
   - image + prompt editing capability;
   - GGUF/quant availability;
   - required side models: VAE, text encoders, mmproj/vision encoder;
   - file sizes;
   - license;
   - expected and measured VRAM/RAM/time;
   - CPU/RAM offload strategy;
   - quality and source-composition preservation;
   - bootstrap complexity;
   - server API viability;
   - production integration suitability.

Experiment requirements:
1. Создай isolated experiment structure, preferably:

   experiments/creative_edit/
     README.md
     download_models.py
     run_experiment.py
     config/default.json
     results/        (ignored)
     models/         (ignored)
     source/         (ignored)

   Если нужна другая структура - обоснуй в README.

2. Добавь/обнови .gitignore только для experiment-only generated paths, если нужно.
3. Самостоятельно скачай нужные модели в experiment path.
4. Собери или скачай stable-diffusion.cpp runtime with CUDA, фиксируя exact source/release/commit.
5. Проверь минимум:
   - FLUX.1-Kontext-dev GGUF Q4 or Q5 on RTX 3060 and produce a real edited output;
   - Qwen Image Edit 2511 or 2509 with the lowest practical quant on RTX 3060, or explicitly rule it out with concrete evidence before deep debugging;
   - server/API mode first. If server edit endpoint is unsuitable, document why and use CLI fallback.
6. Используй несколько prompts:
   - low-change edit preserving composition;
   - style/creative transformation;
   - object/text edit if model supports it;
   - prompt that should preserve face/identity/composition reasonably.
7. Используй input images representative for FilmPipe:
   - already-positive photo;
   - FilmPipe-generated positive if available;
   - restored-like image if available or a copied positive named as restored.
   Do not require production pipeline changes.
8. Measure for each candidate/config:
   - cold start time;
   - warm request time if server mode works;
   - peak VRAM via nvidia-smi or equivalent;
   - peak RAM where practical;
   - output size/resolution;
   - whether VRAM is released after process/server shutdown.
9. Produce outputs under results with:
   - input copy;
   - output image;
   - command/API request used;
   - metrics.json;
   - short qualitative notes.

Important stable-diffusion.cpp checks:
- For FLUX Kontext, verify preconverted GGUF from QuantStack and required VAE/clip_l/t5xxl.
- For Qwen Image Edit 2511, verify `qwen_image_zero_cond_t=true` or current equivalent, plus Qwen2.5-VL 7B and mmproj requirements. Keep this bounded: if the lowest practical quant plus side models clearly does not fit a useful 12 GB VRAM desktop workflow, record the commands/logs/measurements and stop.
- Test `--backend`, `--params-backend`, `--max-vram`, `--auto-fit`, `--offload-to-cpu`, text encoder CPU placement, VAE tiling/CPU placement where relevant.

Deliverables:
1. Working isolated experiment runnable from README.
2. A final handoff document, for example:

   docs/creative_research_handoff_agent1.md

   It must include:
   - exact runtime version/commit/build flags;
   - exact model repos/files/quants/checksums if feasible;
   - commands/API requests;
   - measured VRAM/RAM/time table;
   - output examples paths;
   - qualitative comparison;
   - licensing table;
   - recommended production pair:
     runtime + model + quant + side models + backend/offload flags + API mode;
   - fallback if recommended pair fails;
   - clear integration notes for Agent 2.

Definition of Done:
- FLUX.1 Kontext dev GGUF Q4/Q5 runs locally on the target machine and produces a valid edited output from input image + prompt, unless blocked by a concrete documented upstream/runtime/hardware issue.
- stable-diffusion.cpp support for FLUX Kontext and Qwen Image Edit series is verified against current upstream.
- At least one Qwen Image Edit variant is evaluated with a bounded attempt or explicitly ruled out with evidence, without spending excessive time forcing an impractical configuration.
- VRAM/RAM/time are measured on RTX 3060 12 GB, not guessed.
- Recommended model/runtime choice is justified by quality, hardware practicality, license, and integration simplicity.
- No production FilmPipe code is changed.
- All generated heavy assets remain in experiment-only ignored paths.
- Handoff for Agent 2 is complete enough to integrate without repeating basic research.
```

## Prompt / Agent 2 - Backend Integration

Copy this prompt for Agent 2:

```text
Ты Agent 2 для FilmPipe Creative backend integration.

Контекст:
- Репозиторий: /media/horizen/baza/code/film_pipe
- Сначала прочитай docs/CREATIVE_PIPELINE_HANDOFF.md полностью.
- Затем прочитай handoff Agent 1, ожидаемый файл: docs/creative_research_handoff_agent1.md или путь, который даст пользователь.
- Используй выбранную Agent 1 runtime/model/quant связку. Не повторяй ML research без необходимости.

Цель:
Интегрировать Creative как optional late stage в существующую backend архитектуру FilmPipe:

последний успешный технический результат
+ creative_prompt
-> CreativeProvider / GenerativeProcessor
-> local inference runtime
-> ArtifactType.CREATIVE

Жесткие ограничения:
- Не трогай frontend, кроме случая, если backend contract невозможно стабилизировать без минимального type/client change. В норме frontend оставь Agent 3.
- Не запускай subagents/background agents.
- Не добавляй colorize.
- Не создавай отдельный creative HTTP API.
- Frontend не должен общаться с inference runtime напрямую.
- Creative processor/provider не должен искать source image по filesystem layout.
- Не скачивай модели заново, если Agent 1 уже подготовил documented paths. Если нужен bootstrap script, делай explicit/manual, не auto-download during normal processing.
- Сохрани direct processing without HTTP.

Обязательная architecture fit:
1. Domain:
   - Добавь `FinalProcessingMode` со значениями `standard`, `creative`.
   - Расширь `ProcessingOptions`:
     `final_processing=FinalProcessingMode.STANDARD`,
     `creative_prompt: str | None = None`.
   - Добавь `ArtifactType.CREATIVE = "creative"`.
   - Экспортируй новые domain symbols там, где проект уже экспортирует похожие symbols.

2. Pipeline construction:
   - В `backend/filmpipe/processing/engine.py` добавляй `GenerativeProcessor` только если `options.final_processing == CREATIVE`.
   - При `standard` creative runtime не должен создаваться, импортироваться тяжелым образом или запускаться.
   - Сохрани существующие execution plans для всех standard scenarios.

3. Source selection:
   - Реализуй processing-layer selector, который выбирает:
     a. `ArtifactType.RESTORED`, если он есть в `context.artifacts`;
     b. иначе `ArtifactType.POSITIVE`, если он есть;
     c. иначе already-positive base через `context.working_positive` and/or `ArtifactType.ORIGINAL`.
   - Selector использует только `ProcessingContext`/current image, не `data/jobs` layout.
   - Покрой selector unit tests.

4. GenerativeProcessor:
   - optional=True.
   - Validate prompt non-empty as safety net.
   - Convert selected source to provider-friendly temp file if needed, without modifying existing artifacts.
   - Call `CreativeProvider`.
   - Save provider output through `context.artifact_store.save_artifact(..., ArtifactType.CREATIVE, ...)`.
   - On provider/runtime failure return recoverable `ProcessingError`, `stop_pipeline=False`.
   - Add useful logs: source kind, provider name, model id/config, start/end/failure, duration.

5. Provider/adapter:
   - Implement a small provider boundary, for example in `backend/filmpipe/processing/generative.py`:
     `CreativeProvider`, `CreativeRequest`, `CreativeResult`, `StableDiffusionCppProvider`.
   - Keep stable-diffusion.cpp details inside provider/processing layer.
   - Provider should use Agent 1 recommendation:
     server endpoint if proven reliable, otherwise CLI adapter.
   - Runtime config should come from env/config constants, not frontend:
     model path(s), server URL or executable path, backend/offload flags, output format.
   - Missing runtime/model should produce a clear recoverable Creative error.

6. Resource lifecycle:
   - Do not design a complex GPU scheduler.
   - Ensure `standard` path never loads Creative.
   - For `creative`, follow Agent 1 recommended lifecycle.
   - If using managed subprocess/server, make startup/shutdown explicit and safe.
   - If using CLI per request, document startup latency.
   - Add best-effort cleanup only where simple and localized.

7. API:
   - Extend `POST /jobs` multipart parsing:
     `final_processing` default `standard`;
     `creative_prompt` optional but required for creative.
   - Extend strict unknown-field validation to include new fields.
   - Return clear 400 for invalid final_processing and missing/blank creative prompt.
   - Add `final_processing` to job response.
   - Do not expose filesystem paths or model/runtime details.

8. Artifacts:
   - Creative success creates separate public `creative` artifact.
   - Creative never replaces `original`, `positive`, or `restored`.
   - Existing preview/download endpoints should work through `ArtifactType.CREATIVE`.
   - Batch ZIP should include `creative` automatically because it already includes generated non-original artifacts.

9. Tests:
   Add focused tests with fake CreativeProvider, no real GPU inference:
   - `standard` plans are unchanged and do not instantiate/call creative provider.
   - creative plans append `generative_processing` after restoration.
   - source selection prefers restored over positive over already-positive base.
   - creative success creates `creative` and preserves existing artifacts.
   - creative failure after positive/restored gives partial_success and preserves previous artifacts.
   - creative failure after already-positive base gives partial_success with original still available.
   - batch creative failure for one image does not stop others.
   - API accepts/serializes `final_processing=standard|creative`.
   - API rejects invalid final_processing.
   - API rejects creative with missing/blank prompt.
   - unknown fields still return clear 400.
   - direct `process_image` still works without FastAPI.

10. Documentation:
   - Add/update backend/runtime docs minimally so Agent 3 can finish full docs sweep.
   - Document env vars/config needed for Creative runtime.
   - Do not claim quality/performance beyond Agent 1 measured handoff.

Definition of Done:
- `ProcessingOptions` supports `final_processing` and `creative_prompt`.
- `default_pipeline` produces correct standard and creative processor lists.
- `GenerativeProcessor` integrates through `ProcessingContext`, `ArtifactStore`, and `CreativeProvider`.
- Creative source selection uses existing orchestration/context, not filesystem layout.
- Creative success writes only `ArtifactType.CREATIVE`.
- Creative failure is recoverable and preserves original/positive/restored.
- API contract supports `final_processing` and prompt validation.
- Existing standard scenarios still pass.
- Unit/API tests use fake provider and do not require heavy model inference.
- Run and report:
  `.venv/bin/python -m pytest`
  and any focused command needed for provider smoke if safe.
- Provide handoff for Agent 3, for example:
  docs/creative_backend_handoff_agent2.md
  with changed files, API contract, env vars, tests, and known limitations.
```

## Prompt / Agent 3 - Frontend / E2E / Docs / Cleanup

Copy this prompt for Agent 3:

```text
Ты Agent 3 для FilmPipe Creative frontend/e2e/docs cleanup.

Контекст:
- Репозиторий: /media/horizen/baza/code/film_pipe
- Сначала прочитай docs/CREATIVE_PIPELINE_HANDOFF.md полностью.
- Затем прочитай handoff Agent 1 и Agent 2:
  - docs/creative_research_handoff_agent1.md
  - docs/creative_backend_handoff_agent2.md
  или пути, которые даст пользователь.

Цель:
Завершить пользовательскую интеграцию Creative:

Input Processing
[ Already Positive ] [ Negative -> Positive ]

Restoration
[ Off ] [ TELEA ] [ LaMa ]

Final Processing
[ Standard ] [ Creative ]

При Creative показать prompt UX, отправить backend contract, отобразить `creative` artifact, выполнить e2e smoke/regression/docs cleanup.

Жесткие ограничения:
- Не проектируй новую ML architecture.
- Не меняй backend provider/runtime architecture, кроме мелких fixes, найденных e2e.
- Не добавляй colorize.
- Не запускай subagents/background agents.
- Frontend не должен общаться со stable-diffusion.cpp или любым inference runtime напрямую.
- Не скачивай модели без необходимости; используй Agent 1/2 setup.

Frontend implementation:
1. Types:
   - Update `frontend/src/types.ts`:
     `FinalProcessingMode = "standard" | "creative"`;
     `ArtifactType` includes `"creative"`;
     `Job` includes `final_processing`.

2. API client:
   - Update `frontend/src/api.ts` createJob signature to accept final processing and optional creative prompt.
   - Send `final_processing`.
   - Send `creative_prompt` only when final processing is creative, unless Agent 2 contract explicitly says otherwise.
   - Preserve existing `apiUrl`, preview/download URL behavior.

3. App state/UI:
   - Extend `frontend/src/App.tsx` with `finalProcessing` state default `standard`.
   - Add third segmented control block `Final Processing` using existing visual pattern.
   - Add prompt input/textarea only when Creative is selected.
   - Disable submit for Creative until prompt is non-empty.
   - Preserve existing file selection, input processing, restoration controls.
   - Add job meta label for final processing.
   - Add `Creative` artifact label and append to artifact order:
     `original`, `positive`, `restored`, `creative`.

4. UX constraints:
   - Do not make a landing page.
   - Keep current desktop console style.
   - Ensure text does not overflow on mobile/desktop.
   - Use existing lucide icons where useful.
   - No visible in-app instructional paragraphs about implementation details.
   - `Standard` must feel like normal processing, not an error or disabled creative.

5. Error display:
   - Existing `ErrorList` should show recoverable Creative errors.
   - If Creative fails but positive/restored exists, UI must still show those artifacts.

6. E2E/smoke:
   - Run backend tests after frontend changes if Agent 2 changed any backend fixes.
   - Run `cd frontend && npm run build`.
   - Start backend/frontend if practical.
   - Smoke through API or browser:
     a. already_positive + off + standard;
     b. bw_negative + off + standard;
     c. bw_negative + restoration + standard if lightweight/fake/safe;
     d. creative path with configured/fake provider if Agent 2 provided a smoke mode;
     e. creative runtime failure path should preserve previous artifacts.
   - Do not require real heavy ML inference for routine frontend validation unless Agent 1/2 setup makes it practical and user-approved.

7. Regression matrix:
   Verify old standard scenarios:
   - `already_positive + off` still shows only Original.
   - `already_positive + telea/lama` shows Original + Restored when restored exists.
   - `bw_negative + off` shows Original + Positive.
   - `bw_negative + telea/lama` shows Original + Positive + Restored.
   Verify creative success matrix using fake/provider fixture if available:
   - Creative appends Creative without hiding previous artifacts.

8. Docs cleanup:
   Update current docs, at minimum:
   - README.md
   - frontend/README.md
   - docs/AGENT_HANDOFF.md
   - any Creative-specific Agent 2 docs if stale after UI work.
   Keep historical docs historical; do not rewrite old plans unless they claim current behavior.

   Docs must explain:
   - new Final Processing option;
   - request fields;
   - artifact semantics;
   - setup/bootstrap for creative runtime from Agent 1/2;
   - standard mode does not start Creative;
   - failure semantics;
   - known limitations: synchronous API, startup latency, license, heavy model setup.

9. Legacy sweep:
   - Search for stale active-contract text:
     `creative not implemented`, `future creative controls are not shown`, artifact union missing creative, API accepts only old fields.
   - Do not reintroduce removed `mode`, `polarity`, `selected_modes`, generic `prompt`, or `colorize`.

Definition of Done:
- Frontend has Final Processing [Standard] [Creative].
- Creative prompt appears only for Creative and is required before submit.
- Frontend sends backend contract correctly.
- UI renders `creative` artifact preview/download using API URLs.
- Existing standard scenarios still render correctly.
- Recoverable Creative failure displays error and keeps prior artifacts visible.
- Docs reflect current behavior and setup.
- Run and report:
  `cd frontend && npm run build`
  `.venv/bin/python -m pytest` if backend changed or for final regression.
- Provide final concise handoff:
  changed files, UX behavior, API payloads, e2e/regression results, docs updated, known limitations.
```
