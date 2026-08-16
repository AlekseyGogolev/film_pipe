# FilmPipe AI Restoration Experiment

Изолированный стенд для проверки технической очистки готового `positive` от физических дефектов пленки: пыль, волоски, царапины, мелкие локальные повреждения. Это не production pipeline FilmPipe и не creative/generative ветка.

## Что используется

Detector:

- Microsoft `Bringing Old Photos Back to Life`, scratch detector из `Global/detection.py`.
- Checkpoint: `Global/checkpoints/detection/FT_Epoch_latest.pt` из официального `global_checkpoints.zip`.
- Source: https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life
- Weights: https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life/releases/download/v1.0/global_checkpoints.zip
- License: MIT для кода и pretrained model по README/LICENSE Microsoft.

Restorer A:

- OpenCV TELEA, deterministic baseline.
- License зависит от установленного OpenCV package.

Restorer B:

- Official LaMa repo `advimman/lama`.
- Checkpoint: Big-LaMa, папка `big-lama` с `models/best.ckpt`.
- Source: https://github.com/advimman/lama
- Current downloadable Big-LaMa mirror: https://huggingface.co/smartywu/big-lama/resolve/main/big-lama.zip
- License: Apache-2.0 для LaMa repo; Hugging Face model card также указывает `apache-2.0`.

У LaMa старые Yandex-ссылки недоступны; официальный README сейчас сам указывает Google Drive/Hugging Face mirror. Этот стенд фиксирует это в `models/MODELS.json` после загрузки.

## Установка

Из корня репозитория:

```bash
cd experiments/ai_restoration
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Для GPU поставьте CUDA-сборку PyTorch, подходящую вашей системе, до или вместо обычного `torch` из `requirements.txt`.

LaMa в этом стенде запускается через lightweight native adapter: он загружает официальный `advimman/lama` generator и официальный Big-LaMa checkpoint напрямую, без устаревшего `bin/predict.py`. Для этого нужен дополнительный runtime-набор:

```bash
python -m pip install -r requirements-lama.txt
```

Параметр `--lama-python` оставлен для будущего отдельного окружения/CLI, но текущая реализация использует активный Python процесса.

## Скачать модели

```bash
python download_models.py
```

Скрипт скачивает/клонирует только в `experiments/ai_restoration/models/`, который добавлен в `.gitignore`. Большие веса не коммитятся. Если официальный источник не предоставляет checksum, скрипт считает локальный `sha256` и сохраняет его в `models/MODELS.json` как provenance, но не заявляет внешнюю проверку.

## Запуск

Минимально:

```bash
python run_experiment.py --input path/to/positive.tif
```

Эквивалент для систем, где нет команды `python`:

```bash
python3 run_experiment.py --input path/to/positive.tif
```

Полезные параметры:

```bash
python run_experiment.py \
  --input path/to/positive.tif \
  --threshold 0.4 \
  --dilation 2 \
  --mask-postprocess scene_lines \
  --tile-size 1024 \
  --tile-overlap 128 \
  --restorers telea,lama \
  --device auto
```

Если нужно менять threshold/dilation без повторного detector inference, запускайте повторно в тот же output directory. `probability_mask.npy` будет переиспользован, если не указан `--force-detector`.

Для smoke-теста restoration без AI detector можно передать готовую probability mask:

```bash
python run_experiment.py \
  --input path/to/positive.tif \
  --probability-mask path/to/probability_mask.npy \
  --restorers telea
```

## Outputs

Для каждого входного файла создается:

```text
results/<image>/
├── positive.*
├── probability_mask.npy
├── probability_mask.png
├── binary_mask.png
├── restoration_mask.png
├── telea/
│   ├── restored.*
│   └── diff.png
├── lama/
│   ├── restored.*
│   └── diff.png
└── metrics.json
```

`positive.*` копируется из входа без конверсии. `restored.*` пишется в lossless TIFF/PNG-совместимом формате и сохраняет dtype исходника, кроме внутреннего ограничения LaMa: сама модель работает на 8-bit RGB копии, а затем результат приводится обратно к форме/dtype исходного изображения только внутри mask.

## Detector Pipeline

```text
positive source pixels
  -> 8-bit RGB model copy
  -> grayscale normalized tensor
  -> Microsoft scratch detector
  -> full-resolution probability mask
  -> threshold
  -> raw binary mask
  -> optional scene-line component filter
  -> binary mask
  -> optional dilation
  -> restoration mask
```

`mask_postprocess=scene_lines` is enabled by default. It filters long/dark/Hough-supported scene-line components from the Microsoft scratch mask before dilation, which helps avoid erasing wires, branches, building edges, and similar thin scene geometry. Use `--mask-postprocess none` to reproduce the raw Microsoft threshold+dilation behavior.

Для больших сканов tiled inference включен по умолчанию с `tile_size=1024`.
Если VRAM всё равно не хватает, уменьшайте tile:

```bash
python run_experiment.py --input frame.tif --tile-size 768 --tile-overlap 96
```

Tiles объединяются weighted average с плавным весом на overlap, чтобы снизить границы стыков. Если нужно проверить поведение detector без tiling на небольшом изображении, можно указать `--tile-size 0`. Для больших TIFF-сканов это часто приводит к CUDA OOM.

## Restoration Pipeline

TELEA:

```text
source-resolution positive + restoration_mask
  -> cv2.inpaint(..., INPAINT_TELEA)
  -> final composite
```

LaMa:

```text
source-resolution positive
  -> 8-bit RGB PNG working copy
restoration_mask
  -> 8-bit mask
  -> native adapter loads official LaMa generator/checkpoint
  -> source-shape candidate
  -> final composite
```

Native adapter honors `--device auto|cpu|cuda|cuda:0` when CUDA is available. If LaMa hits GPU memory limits on large scans, rerun that stage with `--device cpu`; cached `probability_mask.npy` means detector inference will not repeat.

Final composite применяется всегда:

```text
final = original_positive outside restoration_mask
        + restored pixels inside restoration_mask
```

Поэтому пиксели за пределами `restoration_mask` должны быть идентичны исходному `positive`. Метрика `changed_pixels_outside_mask` записывается для каждого restorer; нормальное значение `0`.

## Ограничения

- Microsoft detector обучен как scratch detector; пыль и волоски могут ловиться хуже или давать false positives.
- LaMa является inpainting-моделью общего назначения. Ее вывод обязательно ограничивается маской final composite, чтобы она не меняла весь кадр.
- Official LaMa dependency stack старый. Если он конфликтует с Python 3.12 окружением FilmPipe, держите его в `.venv` этого эксперимента или запускайте официальный Docker/venv отдельно.
- Без реальных positive scans с дефектами нельзя честно оценить качество. Synthetic images годятся только для проверки пайплайна и инварианта "outside mask unchanged".
