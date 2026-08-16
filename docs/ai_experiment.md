# Задача: экспериментальный AI-модуль очистки плёночных сканов

## Контекст

Проект **FilmPipe** — локальный pipeline обработки сканов фотоплёнки.

Базовый B&W pipeline уже существует и способен получить технически корректный `positive` из негативного скана.

Следующий этап — экспериментально подобрать AI-подход для **технической очистки изображения от физических дефектов плёнки**:

- пыль;
- волоски;
- царапины;
- мелкие локальные повреждения.

Это **не generative restoration**.

Модель не должна самостоятельно улучшать фотографию, менять лица, текстуры, детали, композицию, резкость и т. п.

Целевая архитектура:

```text
Positive
   ↓
Defect Detector
   ↓
Probability / Binary Mask
   ↓
Restorer
   ↓
Clean Master
```

Creative/generative processing является отдельной будущей веткой и в эту задачу не входит.

---

# Цель задачи

Создать изолированный экспериментальный стенд, позволяющий:

1. автоматически скачать необходимые pretrained models/weights;
2. запустить defect detection;
3. получить и сохранить mask;
4. восстановить найденные дефекты несколькими способами;
5. сравнить результаты;
6. убедиться, что restoration не изменяет изображение за пределами области дефекта;
7. подготовить данные для выбора реализации будущих `DefectDetector` и `Restorer` FilmPipe.

Обучать собственные модели сейчас **не нужно**.

---

# Кандидаты

## Defect Detector

Первый кандидат:

**Microsoft — Bringing Old Photos Back to Life / Scratch Detection**

Исследовать официальный репозиторий:

`microsoft/bringing-old-photos-back-to-life`

Нас интересует именно pretrained scratch/defect detector, а не весь restoration pipeline проекта.

Найти официальный checkpoint detector'а и способ его запуска.

Если официальный checkpoint недоступен, несовместим с текущим окружением или возникают существенные технические проблемы — найти эквивалентный открытый pretrained вариант.

Не использовать модели без понятного происхождения и лицензии.

## Restorer A

**OpenCV TELEA**

Использовать как deterministic baseline.

## Restorer B

**LaMa**

Использовать официальный pretrained LaMa / Big-LaMa либо наиболее близкий официальный поддерживаемый вариант.

LaMa должен получать:

```text
image + defect mask
```

и использоваться только для восстановления областей дефекта.

---

# Очень важное требование: сохранение исходного изображения

Restorer не должен иметь возможность незаметно изменить весь кадр.

После получения результата inpainting выполнить явный final composite:

```text
final = original_positive outside mask
        +
        restored pixels inside mask
```

То есть пиксели за пределами итоговой restoration mask должны быть **идентичны входному positive**.

Это требование применяется даже в случае, если конкретная AI-модель возвращает полностью пересчитанное изображение.

Допускается controlled feathering/blending непосредственно около границы mask, если это необходимо для отсутствия видимого шва.

---

# Mask pipeline

Сохранять отдельно:

```text
probability mask
binary mask
restoration mask
```

Если detector выдаёт probability map:

```text
prediction
    ↓
threshold
    ↓
binary mask
    ↓
optional dilation
    ↓
restoration mask
```

Threshold и dilation должны быть конфигурируемыми.

Важно: изменение threshold не должно требовать повторного inference detector'а, если сохранён probability map.

---

# Структура

Эксперимент не должен загрязнять основной production pipeline.

Предпочтительно создать:

```text
experiments/
└── ai_restoration/
```

Внутри организовать понятную структуру самостоятельно.

Например:

```text
ai_restoration/
├── models/
├── adapters/
├── results/
├── config/
├── scripts/
└── README.md
```

Это пример, а не обязательная структура.

Не создавать сложную plugin architecture.

---

# Models / weights

Агент должен самостоятельно:

- найти официальные источники моделей;
- скачать необходимые weights;
- проверить checksum, если официальный источник его предоставляет;
- определить лицензии;
- сохранить информацию о происхождении моделей;
- настроить загрузку моделей.

Большие бинарные weights **не коммитить в Git**.

Добавить соответствующие записи в `.gitignore`.

Желательно сделать воспроизводимый bootstrap/download script, чтобы модели можно было получить повторно без ручных действий.

Например:

```bash
python download_models.py
```

Конкретный интерфейс выбрать самостоятельно.

Не скачивать модели неизвестного происхождения только ради того, чтобы эксперимент заработал.

---

# Environment

Целевое железо:

```text
Ryzen 9 7900X
RTX 3060 12 GB
64 GB RAM
NVIDIA CUDA
```

Использовать GPU для AI inference, если это разумно.

Не выполнять преждевременную оптимизацию CUDA/TensorRT/ONNX.

Сначала нужен корректный работающий эксперимент.

Не ломать существующее окружение FilmPipe.

Если зависимости экспериментальных моделей конфликтуют с основным проектом, изолировать их разумным минимальным способом.

---

# Input

Эксперимент должен принимать уже полученный **positive**, а не негатив.

Минимально:

```bash
python run_experiment.py --input path/to/image.tif
```

Поддержать один файл обязательно.

Если batch получается практически бесплатно — можно поддержать директорию, но не переусложнять задачу.

Особое внимание уделить сохранению bit depth.

Если конкретная модель требует 8-bit/RGB/resize, явно отделить:

```text
FilmPipe source representation
        ↓
model preprocessing
        ↓
model inference
        ↓
model output
        ↓
restoration applied to source-resolution image
```

Не ухудшать исходный TIFF просто потому, что модель работает на меньшем разрешении.

---

# Detector и большие изображения

Исследовать требования Microsoft detector к размеру изображения.

Не делать слепой resize большого плёночного скана до `256×256`/`1024×1024`, если из-за этого теряются мелкие дефекты.

Если необходимо, реализовать tiled inference:

```text
full resolution image
      ↓
overlapping tiles
      ↓
detector
      ↓
merge probability maps
      ↓
full-resolution mask
```

Overlap/merging должны минимизировать seams на границах tiles.

Если tiled inference пока объективно не требуется, зафиксировать причину.

---

# Outputs

Для каждого изображения сохранить примерно такой набор:

```text
results/<image>/
├── positive.*
├── probability_mask.*
├── binary_mask.png
├── restoration_mask.png
│
├── telea/
│   ├── restored.*
│   └── diff.png
│
├── lama/
│   ├── restored.*
│   └── diff.png
│
└── metrics.json
```

Форматы можно скорректировать, если для сохранения bit depth есть более подходящий вариант.

---

# Diff

Для каждого restorer создать визуализацию:

```text
abs(restored - positive)
```

Нам необходимо глазами видеть, какие области были изменены.

Дополнительно вычислить проверку:

```text
changed pixels outside restoration mask
```

В идеале:

```text
0
```

либо объяснимое минимальное значение из-за controlled feathering.

---

# Metrics

Минимально сохранить:

```text
detector inference time
restoration time
GPU / CPU device
mask coverage %
changed pixels outside mask
input dimensions
model names
model/checkpoint versions
threshold
dilation
```

Не нужно сейчас строить сложную систему benchmark'ов.

---

# Что НЕ делать

Не нужно:

- обучать свою нейросеть;
- создавать dataset;
- интегрировать эксперимент в основной FilmPipe pipeline;
- менять существующую архитектуру FilmPipe без необходимости;
- использовать generative prompt processing;
- colorization;
- face enhancement;
- super-resolution;
- denoising всего изображения;
- sharpening;
- automatic contrast enhancement;
- «улучшать» фотографию;
- подключать облачные API;
- делать UI;
- преждевременно оптимизировать inference.

Цель — **локально удалить физический дефект и максимально сохранить всё остальное изображение неизменным**.

---

# Если выбранная модель не работает

Не тратить огромное количество времени на оживление устаревшего dependency stack.

Если Microsoft detector невозможно разумно поднять в современном окружении:

1. зафиксировать конкретную техническую причину;
2. найти современный открытый pretrained detector той же задачи;
3. проверить источник и лицензию;
4. подключить его через тот же экспериментальный интерфейс.

То же относится к LaMa.

Главная цель — эксперимент FilmPipe, а не восстановление конкретного старого GitHub-проекта любой ценой.

---

# Documentation

Создать `README.md` эксперимента.

Зафиксировать:

- какие модели используются;
- ссылки на официальные источники;
- лицензии;
- конкретные checkpoints;
- как скачать models;
- как запустить эксперимент;
- зависимости;
- preprocessing;
- ограничения;
- известные проблемы.

---

# Definition of Done

Задача завершена, когда можно выполнить примерно:

```bash
python download_models.py

python run_experiment.py \
    --input ./samples/frame_01.tif
```

и получить:

```text
positive
probability mask
binary/restoration mask

TELEA restored
TELEA diff

LaMa restored
LaMa diff

metrics
```

Эксперимент должен позволять визуально сравнить:

```text
Positive
Mask
TELEA
LaMa
Diff
```

и определить, подходит ли связка для будущего FilmPipe `Clean Master`.

---

# Финальный handoff

После реализации дай короткий отчёт:

## Что реализовано

## Какие модели и checkpoints используются

## Откуда они скачаны

## Лицензии

## Как запустить

## Как устроен detector pipeline

## Как устроен restoration pipeline

## Как обеспечено отсутствие изменений вне mask

## Ограничения / проблемы

## Первые наблюдения по качеству

## Что рекомендуешь следующим шагом

Также перечисли все созданные/изменённые файлы.

Если для проверки качества нужны реальные тестовые изображения, но подходящих изображений в проекте нет — не подменяй их случайными картинками и не делай вывод о качестве модели. Подготовь рабочий pipeline и явно сообщи, какие тестовые данные нужны.
