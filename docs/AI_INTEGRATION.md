# Задача: перенести проверенный AI Restoration из experiment в FilmPipe

## Контекст

В `experiments/ai_restoration/` уже реализован и проверен экспериментальный pipeline:

```text
Positive
↓
Microsoft Defect/Scratch Detector
↓
Probability Mask
↓
Mask Postprocessing
↓
Restoration Mask
↓
TELEA или LaMa
↓
Final Composite
```

Эксперимент показал достаточное для MVP качество.

Критичный false-positive кейс с тонкими проводами был исследован. Для Microsoft detector был разработан улучшенный mask postprocessing, уменьшающий ложные срабатывания на реальных линейных структурах сцены.

TELEA и LaMa после исправления mask работают приемлемо.

Не продолжать сейчас исследование качества и не пытаться довести restoration до идеала.

Цель этой задачи — **аккуратно перенести уже доказанный подход в основной FilmPipe как optional processing stage**.

---

# Целевая production-схема

Основной B&W pipeline должен концептуально стать:

```text
Decode
↓
Negative Conversion
↓
Tone Normalization
↓
Positive
↓
Defect Detection        [optional]
↓
Mask Postprocessing     [optional]
↓
Restoration             [optional]
↓
Clean Master / Restored
```

AI restoration не является обязательным для получения успешного `positive`.

---

# Режимы restoration

Добавить настройку:

```text
restoration:
  off
  telea
  lama
```

Семантика:

### `off`

AI detector/restoration вообще не запускаются.

Результат:

```text
positive
```

### `telea`

```text
positive
→ Microsoft detector
→ production mask postprocessing
→ TELEA
→ restored
```

### `lama`

```text
positive
→ Microsoft detector
→ production mask postprocessing
→ LaMa
→ restored
```

Для текущего MVP:

```text
default = lama
```

Но архитектура не должна считать LaMa специальным случаем.

TELEA и LaMa должны быть реализациями одного концептуального `Restorer` contract.

---

# Важное ограничение

Не переносить экспериментальную директорию целиком в production.

Использовать experiment как reference и перенести только минимально необходимую доказанную реализацию.

Не переносить:

- comparison infrastructure;
- experiment CLI;
- diff generation;
- benchmark metrics;
- ROI debug tools;
- research overlays;
- временные scripts;
- исследовательские hacks.

Они остаются в `experiments/`.

---

# Defect Detector

Production detector должен использовать уже проверенный Microsoft pretrained detector.

Не менять модель в этой задаче.

Не обучать модель.

Не искать новые модели.

Использовать существующий mechanism загрузки/checkpoint, уже проверенный экспериментом.

ML-specific implementation не должна проникать в domain/API/frontend contracts.

---

# Mask Postprocessing

Перенести в production **актуальную исправленную версию mask algorithm**, полученную после эксперимента с false positives.

Pipeline концептуально:

```text
probability map
↓
threshold
↓
connected components
↓
conservative scene-line filtering
↓
mask cleanup
↓
dilation
↓
restoration mask
```

Не копировать код Spotless-Film.

Production implementation должна основываться только на нашей независимой экспериментальной реализации.

Параметры должны иметь разумные defaults.

Не выставлять сейчас сложные параметры mask processing в UI.

Threshold/dilation/etc. могут оставаться internal configuration.

---

# Restoration

## TELEA

Использовать OpenCV TELEA implementation.

## LaMa

Использовать уже проверенный LaMa integration.

Учитывать существующие ограничения VRAM.

Не отправлять без необходимости огромный full-resolution scan одним tensor, если текущая экспериментальная реализация уже использует безопасный tiled/local processing.

Не заниматься сейчас ONNX/TensorRT/оптимизацией.

---

# Final Composite — обязательный invariant

Независимо от restorer:

```text
restored pixels используются только внутри restoration mask
```

За пределами restoration mask результат должен совпадать с `positive`.

Даже если LaMa возвращает полностью пересчитанное изображение:

```text
final =
    positive outside mask
    +
    model result inside mask
```

Это обязательная защита FilmPipe от нежелательного изменения фотографии.

---

# Артефакты

При `restoration=off`:

```text
original
positive
```

При успешном restoration:

```text
original
positive
restored
```

`positive` никогда не заменяется `restored`.

`restored` является отдельным производным artifact.

Использовать существующий `ArtifactType.RESTORED`, если он уже предусмотрен domain model.

Не создавать новую сущность без необходимости.

---

# Failure semantics

Это критично.

Restoration является optional stage.

Если:

```text
Positive ✓
Detector ✗
```

или:

```text
Positive ✓
Detector ✓
Restorer ✗
```

то:

- `positive` остаётся доступен;
- image НЕ становится полностью failed;
- ошибка restoration фиксируется;
- image/job получают существующую семантику `partial_success`, если она применима;
- пользователь получает короткую понятную ошибку;
- technical details/stack trace идут только в logs.

Не менять существующую failure model FilmPipe.

---

# API

Расширить существующий `POST /jobs` минимально необходимой настройкой restoration.

Например:

```text
restoration=off|telea|lama
```

Не создавать отдельный AI API.

Не создавать отдельный endpoint для TELEA/LaMa.

Single и batch используют тот же Job/Pipeline.

API не должен знать PyTorch/OpenCV/LaMa implementation details.

---

# Frontend

Добавить простой выбор restoration.

Предпочтительный UX:

```text
Restoration

○ Off
○ TELEA
● LaMa
```

или компактный Select/Segmented control в существующем стиле UI.

LaMa — default.

Пользователь должен понимать:

- `Off` — без удаления дефектов;
- `TELEA` — быстрый вариант;
- `LaMa` — AI restoration.

Не добавлять сейчас:

- threshold;
- dilation;
- detector selection;
- model paths;
- tile size;
- GPU settings;
- advanced AI configuration.

Это internal implementation details.

Если restoration завершился ошибкой, UI должен продолжать показывать `positive` и существующий механизм stage errors.

Если `restored` существует, UI должен позволять его preview/download аналогично существующим artifacts.

Не ломать Original / Positive comparison.

Минимально расширить его так, чтобы пользователь мог увидеть `restored`.

Выбери наиболее простой UX, совместимый с текущим UI.

---

# Models / weights

Большие weights не коммитить.

Использовать уже созданный воспроизводимый механизм загрузки моделей.

Если production runtime требует предварительного:

```bash
python experiments/ai_restoration/download_models.py
```

это временно допустимо, но предпочтительно вынести/адаптировать bootstrap так, чтобы production не зависел концептуально от `experiments/`.

Не скачивать модели автоматически во время обычного image processing без явного архитектурного решения.

README должен объяснять подготовку AI models.

---

# Dependencies

Не ломать лёгкий basic FilmPipe install.

Если LaMa требует конфликтующего/устаревшего Python environment, сохранить уже проверенную возможность изолированного LaMa runtime/subprocess.

`restoration=off` и `restoration=telea` не должны требовать успешной загрузки LaMa.

По возможности basic B&W pipeline должен продолжать работать без PyTorch/LaMa runtime.

---

# Logging

Использовать существующую logging infrastructure.

Логировать минимум:

```text
job_id
image_id
detector
restorer
restoration start/end
restoration failure
timings where already easily available
```

Не создавать отдельную observability system.

---

# Tests

Добавить production tests минимум на:

1. `restoration=off`
   - detector не вызывается;
   - positive создаётся;
   - restored отсутствует.

2. successful TELEA restoration
   - positive сохраняется;
   - restored создаётся.

3. successful LaMa restoration
   - через mock/fake adapter, без обязательного тяжёлого GPU inference в unit tests.

4. detector failure after positive
   - positive остаётся;
   - status соответствует partial failure semantics.

5. restorer failure after positive
   - positive остаётся;
   - restored отсутствует;
   - ошибка доступна пользователю.

6. final composite
   - pixels outside restoration mask идентичны positive.

7. batch
   - restoration failure одного изображения не останавливает остальные.

8. API
   - `off`, `telea`, `lama` корректно сериализуются/обрабатываются;
   - invalid restoration value возвращает понятную ошибку.

Не запускать настоящую LaMa в обычном unit-test suite.

---

# Existing contracts

Сохранить существующие архитектурные границы:

- Processing Engine независим от HTTP.
- Domain не зависит от OpenCV/NumPy/PyTorch.
- Frontend не знает ML implementation.
- `FilmImage` остаётся processing-local.
- Original immutable.
- Positive сохраняется независимо от optional AI.
- Single/batch используют одну модель processing.
- Existing preview/download API contract сохраняется.

Не проводить unrelated refactoring.

---

# Definition of Done

Можно запустить FilmPipe и выбрать:

```text
Restoration: Off
Restoration: TELEA
Restoration: LaMa
```

При `Off`:

```text
negative
→ positive
```

При `TELEA`:

```text
negative
→ positive
→ detector
→ corrected mask
→ TELEA
→ restored
```

При `LaMa`:

```text
negative
→ positive
→ detector
→ corrected mask
→ LaMa
→ restored
```

Во всех случаях:

- original сохраняется;
- positive сохраняется;
- restoration optional;
- failure AI не уничтожает positive;
- restored является отдельным artifact;
- изменения вне restoration mask запрещены;
- single и batch продолжают работать;
- UI позволяет выбрать restorer;
- существующие функции FilmPipe не ломаются.

После реализации:

```bash
pytest
cd frontend && npm run build
```

должны проходить.

---

# Handoff

После завершения дай короткий отчёт:

## Что перенесено из experiment

## Какие production файлы созданы/изменены

## Как теперь устроен pipeline

## Как выбирается TELEA / LaMa / Off

## Как загружаются models

## Как обеспечено сохранение positive

## Как обеспечено отсутствие изменений вне mask

## Что происходит при AI failure

## Как запустить каждый режим

## Tests / build results

## Какие экспериментальные части намеренно НЕ были перенесены

## Известные ограничения

Не продолжай после этого оптимизацию качества detector/restorer. Это отдельная будущая задача.
