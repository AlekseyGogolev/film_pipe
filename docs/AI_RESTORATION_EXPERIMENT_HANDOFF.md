# AI Restoration Experiment Handoff

Документ для следующих агентов: что было сделано в эксперименте очистки пленочных позитивов, какие проблемы встретились, почему решения именно такие, и как заново прогнать обе restoration-ветки.

## Где находится эксперимент

Основной код:

```text
experiments/ai_restoration/
```

Важные файлы:

```text
run_experiment.py
download_models.py
requirements.txt
requirements-lama.txt
config/default.json
ai_restoration/detectors.py
ai_restoration/masks.py
ai_restoration/restorers.py
ai_restoration/composite.py
README.md
```

Локальные данные не коммитятся:

```text
experiments/ai_restoration/source/
experiments/ai_restoration/results/
experiments/ai_restoration/models/
experiments/ai_restoration/.venv/
```

## Цель

Стенд проверяет техническую очистку готового FilmPipe `positive` от физических дефектов пленки:

- пыль;
- волоски;
- царапины;
- мелкие локальные повреждения.

Это не generative restoration всего кадра. Любой restorer обязан менять только pixels внутри итоговой `restoration_mask`.

## Текущий pipeline

```text
positive
  -> Microsoft scratch detector
  -> probability_mask.npy / probability_mask.png
  -> threshold
  -> raw_binary_mask.png
  -> scene-line component postprocess
  -> binary_mask.png
  -> dilation
  -> restoration_mask.png
  -> TELEA restored + diff
  -> LaMa restored + diff
  -> metrics.json
```

`probability_mask.npy` кэшируется. Если меняются только threshold, dilation или mask postprocess, detector заново не запускается.

## Почему появился mask postprocess

Microsoft scratch detector ловит длинные контрастные grayscale-структуры как scratches. На реальном кадре он маскировал провода/тонкие линии сцены. TELEA и LaMa после этого честно стирали то, что дала маска, поэтому проблема была не в restorer, а до него.

Добавлен conservative postprocess в `ai_restoration/masks.py`:

```text
raw binary mask
  -> connected components
  -> PCA geometry per component
  -> local contrast estimate
  -> Canny/Hough scene-line support
  -> remove long/dark/Hough-supported scene-line components
```

Он включен по умолчанию:

```json
"mask_postprocess": "scene_lines"
```

Для сравнения со старым поведением:

```bash
python run_experiment.py --input source --output results --mask-postprocess none
```

## Важные инварианты

Final composite выполняется всегда:

```text
final = original outside restoration_mask
        + restorer candidate inside restoration_mask
```

Даже если LaMa возвращает полностью пересчитанное изображение, оно обрезается маской в `composite_restoration`. Метрика `changed_pixels_outside_mask` должна быть `0`.

При последнем прогоне по `source/`:

```text
TELEA: changed_pixels_outside_mask = 0
LaMa : changed_pixels_outside_mask = 0
```

## История проблем и решений

### Placeholder input path

Пользователь сначала запустил пример из README:

```bash
python run_experiment.py --input path/to/positive.tif
```

Это был placeholder, не реальный файл. Ошибка была корректной:

```text
FileNotFoundError: Input path not found: path/to/positive.tif
```

Решение: договорились складывать позитивы в:

```text
experiments/ai_restoration/source/
```

и запускать:

```bash
python run_experiment.py --input source --output results
```

### PyTorch отсутствовал в experiment venv

Detector упал с:

```text
ModuleNotFoundError: No module named 'torch'
```

Решение: поставить зависимости в `experiments/ai_restoration/.venv`:

```bash
python -m pip install -r requirements.txt
```

Для NVIDIA GPU использовалась CUDA-сборка PyTorch.

### CUDA OOM на full-size detector inference

Большой TIFF пытался пройти через Microsoft detector целиком. Несмотря на то, что checkpoint небольшой, VRAM съедают не только weights, но и activation maps/intermediate tensors. На 12 GB RTX 3060 это привело к:

```text
torch.OutOfMemoryError: CUDA out of memory
```

Решение:

- включить tiled inference по умолчанию;
- дефолт `tile_size=1024`, `tile_overlap=128`;
- при OOM рекомендовать `--tile-size 768 --tile-overlap 96` или `--device cpu`.

### LaMa official CLI оказался хрупким

Изначально LaMa запускалась через официальный:

```text
advimman/lama/bin/predict.py
```

Проблемы:

- не хватало `tqdm`;
- затем всплывал широкий stack старых dependency;
- новый `albumentations` больше не содержит `DualIAATransform`;
- upstream requirements старые и плохо ложатся на Python 3.12;
- `torch.load` в новых PyTorch требует учитывать `weights_only`.

Решение: заменить вызов official CLI на lightweight native adapter в `ai_restoration/restorers.py`.

Native adapter:

- использует официальный `advimman/lama` repo;
- загружает официальный Big-LaMa `models/best.ckpt`;
- создает `FFCResNetGenerator` напрямую;
- берет только `generator.*` веса из checkpoint;
- делает 8-bit RGB inference;
- затем приводит candidate output обратно к shape/dtype source image;
- final composite все равно ограничивает изменения маской.

Для native LaMa нужны зависимости:

```bash
python -m pip install -r requirements-lama.txt
```

### LaMa могла идти на CPU

В одном прогоне PyTorch писал:

```text
UserWarning: Can't initialize NVML
```

LaMa выбрала CPU. Это не ломает результат, но делает прогон медленнее. В `metrics.json` смотреть:

```text
restorers.lama.device
```

Если GPU недоступен или LaMa ловит OOM, можно явно запускать:

```bash
python run_experiment.py --input source --output results --device cpu --restorers lama
```

Detector при наличии cached `probability_mask.npy` заново не пойдет.

## Как заново прогнать обе модели

Из корня эксперимента:

```bash
cd /media/horizen/baza/code/film_pipe/experiments/ai_restoration
source .venv/bin/activate

python run_experiment.py \
  --input source \
  --output results \
  --device auto \
  --tile-size 1024 \
  --tile-overlap 128 \
  --restorers telea,lama
```

Если нужно принудительно пересчитать detector probability:

```bash
python run_experiment.py \
  --input source \
  --output results \
  --device auto \
  --tile-size 1024 \
  --tile-overlap 128 \
  --restorers telea,lama \
  --force-detector
```

Если нужно быстро проверить только маску/restoration без LaMa:

```bash
python run_experiment.py \
  --input source \
  --output results \
  --restorers telea
```

## Что смотреть в results

Для каждого изображения:

```text
positive.*
probability_mask.npy
probability_mask.png
raw_binary_mask.png
scene_line_support.png
filtered_binary_mask.png
binary_mask.png
restoration_mask.png
telea/restored.*
telea/diff.png
lama/restored.*
lama/diff.png
metrics.json
```

Самые полезные сравнения:

- `raw_binary_mask.png` против `binary_mask.png`;
- `scene_line_support.png` для понимания, что фильтр посчитал scene lines;
- `restoration_mask.png` против исходного `positive`;
- `telea/diff.png` и `lama/diff.png`;
- `changed_pixels_outside_mask` в `metrics.json`.

## Последний sanity result

На проблемном wire-кадре:

```text
raw binary coverage      1.0023%
filtered binary coverage 0.3480%
restoration coverage     0.9971%
binary reduction         65.3%
```

Это говорит, что фильтр реально уменьшил ложное маскирование проводов. Это не доказывает recall на настоящих scratches: нужен набор вручную размеченных ROI.

## Риски

- Scene-line filter может удалить длинную настоящую царапину.
- Microsoft detector все еще scratch-oriented; пыль/волоски могут ловиться неполно.
- LaMa работает на 8-bit RGB working copy, поэтому для 16-bit TIFF точные значения внутри маски являются реконструкцией из 8-bit результата. Вне маски dtype/source pixels сохраняются.
- Без ground-truth defect labels нельзя честно оценить качество, только inspect визуально.

## Рекомендованные следующие шаги

1. Собрать 10-30 ROI: wires/branches/building edges и настоящие defects.
2. Сравнить `mask-postprocess none` против `scene_lines` по false positives/false negatives.
3. Добавить ROI metrics в experiment, не в production FilmPipe.
4. Если recall по настоящим scratches просел, ослабить фильтры или добавить режим `conservative/medium/aggressive`.
5. Новый neural detector рассматривать только после ROI benchmark.
