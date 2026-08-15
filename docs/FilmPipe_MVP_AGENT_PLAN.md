# FilmPipe MVP — план последовательной реализации агентами

> Основной источник требований: `FilmPipe_SPEC.md`.
> Этот документ не заменяет основное ТЗ. Он определяет порядок реализации MVP, границы задач агентов и обязательный механизм передачи контекста между ними.

## 0. Порядок работы

MVP реализуется последовательно:

```text
Agent 1 — Architecture & Bootstrap
        ↓
Agent 2 — B&W Image Processing Pipeline
        ↓
Agent 3 — Jobs, Batch, Failure Model & HTTP API
        ↓
Agent 4 — Minimal Frontend
        ↓
Agent 5 — MVP Integration & Hardening
```

Не запускать эти задачи как пять независимых параллельных реализаций. Каждый следующий агент продолжает фактическую реализацию предыдущего.

Главный принцип:

> Не перепроектировать уже принятое решение без доказанной необходимости.

Если предыдущий агент уже выбрал структуру проекта, контракты, модели, формат артефактов, API или другой технический подход и он удовлетворяет `FilmPipe_SPEC.md`, следующий агент использует это решение, а не создаёт альтернативное из личных предпочтений.

---

# 1. Обязательный Agent Handoff

В корне проекта должен существовать живой файл:

`AGENT_HANDOFF.md`

Он является короткой актуальной технической выжимкой состояния проекта для следующего агента.

## Перед началом каждой задачи агент обязан

1. Полностью прочитать `FilmPipe_SPEC.md`.
2. Полностью прочитать этот implementation plan.
3. Полностью прочитать `AGENT_HANDOFF.md`, если он существует.
4. Изучить фактическое состояние репозитория.
5. Проверить реально существующую реализацию перед выводами о системе.
6. Продолжать существующую архитектуру, если она не противоречит ТЗ и не блокирует текущую задачу.

При конфликте источников использовать приоритет:

```text
FilmPipe_SPEC.md
↓
фактическая реализация в репозитории
↓
AGENT_HANDOFF.md
↓
этот implementation plan
↓
собственные предположения агента
```

Если handoff расходится с кодом, проверить причину и привести handoff в соответствие с фактическим состоянием.

## Агент НЕ должен

- заново выбирать стек без необходимости;
- создавать параллельную архитектуру;
- вводить второй способ решения уже решённой задачи;
- менять публичные контракты только из-за личных предпочтений;
- переписывать рабочий код без причины, относящейся к текущей задаче;
- расширять scope MVP;
- додумывать отсутствующие требования как обязательные;
- начинать AI-restoration, colorization или generative processing;
- создавать инфраструктуру «на будущее»;
- молча менять решение предыдущего агента.

Если существующее решение действительно необходимо изменить, агент обязан:

1. определить конкретную проблему;
2. убедиться, что изменение необходимо для выполнения ТЗ;
3. минимизировать область изменения;
4. зафиксировать новое решение и причину в `AGENT_HANDOFF.md`.

## После выполнения каждой задачи агент обязан

1. Убедиться, что проект находится в рабочем состоянии.
2. Запустить относящиеся к задаче тесты/проверки.
3. Обновить `AGENT_HANDOFF.md`.
4. Оставить следующему агенту не историю рассуждений, а выжимку текущего состояния системы.

Следующий агент после чтения handoff должен сразу понимать:

- что уже существует;
- как это устроено;
- какие решения считаются принятыми;
- какие контракты нельзя случайно сломать;
- как запустить и проверить систему;
- что осталось сделать;
- какие реальные проблемы известны;
- по какому направлению продолжать работу.

---

# 2. Формат `AGENT_HANDOFF.md`

Файл поддерживается как актуальный документ, а не как бесконечный append-only журнал.

Рекомендуемая структура:

```md
# FilmPipe — Agent Handoff

## Current MVP State
Что реально работает сейчас.

## Architecture
Текущая структура системы и границы модулей.

## Technology Decisions
- Backend:
- Processing:
- API:
- Frontend:
- Storage:
- Tests:
- Logging:

Для нетривиальных решений — коротко WHY.

## Important Contracts
Processor, Pipeline, Job, Artifact, errors и другие важные контракты.
Указать назначение и расположение в коде, не копировать весь код.

## Processing Flow
Фактически реализованный pipeline.

## Storage / Artifacts
Как и где хранятся original/positive и другие результаты.

## Failure Model
Image/job errors, partial success и сохранение полезных артефактов.

## API Contract
Endpoint'ы и ключевые request/response contracts, если API уже существует.

## Frontend Contract
Какие backend contracts использует frontend, если он уже существует.

## How to Run
Команды запуска.

## How to Test
Команды тестов и важные fixtures/scenarios.

## Known Limitations
Только реальные известные ограничения.

## Open Issues
Для каждой проблемы:
- проблема;
- влияние;
- что уже проверено;
- рекомендуемое направление, если оно известно.

## Decisions That Should Not Be Revisited Without Reason
Ключевые решения, на которые следующий агент должен опираться.

## Completed In This Task
Что реально выполнено текущим агентом.

## Next Agent
Что делать дальше и на что обратить внимание.
```

Handoff должен быть коротким относительно основного ТЗ, конкретным, основанным на коде и пригодным для машинного чтения.

Плохо:

```text
Storage можно сделать через filesystem, SQLite или что-нибудь ещё.
```

Хорошо:

```text
Storage: filesystem.
Job artifacts: data/jobs/{job_id}/{image_id}/
Причина: для MVP persistence БД не требуется.
Не вводить БД без новой доказанной необходимости.
```

Если вопрос не решён, написать это явно:

```text
OPEN ISSUE:
16-bit TIFF normalization нестабилен на fixture X.
Не заменять pipeline целиком.
Сначала проверить percentile calculation в ToneNormalizer.
```

---

# 3. Общий контекст MVP

Главная цель:

```text
B&W negative scan
→ upload
→ decode / validation
→ negative conversion
→ normalization
→ technically correct positive
→ preview
→ download
```

Критические инварианты:

- Original immutable.
- Processing Engine не зависит от HTTP.
- Single и batch используют один pipeline.
- Ошибка одного изображения не останавливает остальные.
- Уже созданный корректный artifact не уничтожается ошибкой последующего этапа.
- Frontend не знает деталей OpenCV / NumPy / ML backend.
- Basic pipeline полностью работает без AI.
- User-facing errors отделены от technical logs.
- Creative processing отделён от film processing.
- Производные изображения сохраняются отдельно.
- AI — будущий optional layer, а не фундамент MVP.

В MVP не реализовывать production-версии `DefectDetector`, `Restorer`, `Colorizer`, `GenerativeProcessor`; собственное обучение ML; dataset; distributed processing; microservices; Kubernetes; cloud; auth/accounts; БД без доказанной необходимости; сложную plugin system; observability platform; production deployment; преждевременную CUDA/inference optimization.

---

# 4. Agent Task 1 — Architecture & Bootstrap

## Цель

Создать минимальный технический фундамент FilmPipe без лишней инфраструктуры.

## Задача

Изучить основное ТЗ и репозиторий. Определить минимально достаточный стек.

Предпочтительное направление ТЗ:

```text
Backend / Processing: Python + NumPy + OpenCV
API: local HTTP API
Frontend: React
```

Это кандидаты, а не жёсткая фиксация. Существенное отклонение обосновать.

Создать минимальную структуру проекта и необходимые domain contracts, концептуально соответствующие:

```text
Processor
ProcessingContext
ProcessingResult / Artifact
ProcessingPipeline
ProcessingJob
ImageProcessingResult
ProcessingError
```

Названия могут отличаться, если модель проще и понятнее. Не строить полноценную plugin architecture.

### Processing Engine

Он независим от HTTP. Должна существовать возможность концептуально выполнить:

```python
result = process(image, options)
```

без HTTP server.

### Artifacts / Storage

Обязательно предусмотреть `original` и `positive`, а также возможность позднее добавить `restored`, `colorized`, `creative`.

Определить простой non-destructive filesystem storage. Не создавать пустые директории/артефакты без необходимости.

### Logging

Настроить единый logging mechanism минимум с:

```text
job_id
image/image_id
processor/stage
status
error
```

Stack trace — в technical log, не в user-facing error.

### Результат

Должны существовать:

- структура backend/processing engine;
- domain contracts;
- pipeline orchestration;
- job/artifact/error model;
- минимальный storage;
- logging;
- конфигурация;
- минимальные тесты ядра;
- README с development setup;
- актуальный `AGENT_HANDOFF.md`.

Качественная обработка негатива и UI пока не требуются.

### Handoff Agent 1 → Agent 2

Особенно зафиксировать стек, структуру каталогов, Processor/Pipeline/Artifact/Job contracts, storage layout, error model, logging, команды запуска/тестов, реальные extension points и то, что намеренно не создавалось.

---

# 5. Agent Task 2 — B&W Image Processing Pipeline

## Цель

Получить главное техническое доказательство:

```text
real B&W negative → technically correct positive
```

## Перед началом

Прочитать handoff и использовать существующие contracts Agent 1. Не создавать второй pipeline/domain model.

## Задача

Реализовать:

```text
Decode / Validation
↓
Preprocessing (только если действительно требуется)
↓
Negative Conversion
↓
Tone / Exposure Normalization
↓
Positive Artifact
```

Перед реализацией определить и зафиксировать:

- MVP input formats;
- internal image representation;
- bit depth;
- output format;
- необходимые преобразования bit depth.

Приоритет — сохранение исходной информации и отсутствие лишней lossy compression.

Не ограничиваться `255 - image`, если этого недостаточно для технически пригодного positive. Normalization должна быть автоматической. Художественная коррекция не требуется.

Цель — нейтральный технический positive для просмотра и дальнейшей обработки.

### Errors

Проверить unsupported format, corrupted image, decode failure, invalid dimensions/data, processor failure. Ошибка до получения positive означает failure конкретного изображения.

### Tests

Минимум:

- validation;
- negative conversion;
- normalization;
- pipeline execution;
- failed processing;
- сохранение positive artifact;
- небольшой набор fixtures.

### Результат

Processing Engine без HTTP принимает реальный B&W negative и создаёт `original` + `positive`.

### Handoff Agent 2 → Agent 3

Зафиксировать formats, internal representation, bit depth, output, фактический processing flow, processors, normalization algorithm, fixtures, способ запуска без HTTP, типичные ошибки, ограничения качества, критерий successful result и unresolved image-processing issues.

---

# 6. Agent Task 3 — Jobs, Batch, Failure Model & HTTP API

## Цель

Превратить готовый Processing Engine в backend MVP для frontend.

## Перед началом

Использовать решения handoff. Не менять processing algorithms Agent 2 без необходимости текущей задачи. Processing logic не переносить в HTTP handlers.

## Processing Job

Минимально:

```text
id
inputs
options
selected_modes
status
results
errors
```

Без БД, если она объективно не нужна.

Нужно различать `success`, `partial_success`, `failed` или эквивалентную семантику.

Single image — частный случай batch. Каждый image имеет собственные status, artifacts, errors. Failure одного image не останавливает остальные.

### Partial results

Если positive создан, а последующий optional processor упал, positive остаётся AVAILABLE. Для тестирования допустим намеренно падающий stub processor после positive.

### HTTP API

Frontend должен уметь:

- создать job и передать files/options/mode;
- получить job state;
- получить per-image state;
- получить artifacts и user-facing errors;
- получить artifact для preview/download;
- скачать batch.

REST contract определить и документировать. Для MVP polling предпочтительнее WebSocket без доказанной realtime-необходимости.

### Export

Отдельный download + batch ZIP. ZIP содержит только существующие корректные artifacts.

### Tests

Проверить single success, batch success, failure одного image, partial success, сохранение artifact после optional failure, download и ZIP.

### Handoff Agent 3 → Agent 4

Особенно подробно зафиксировать backend start, base API/config, endpoints, request/response schemas, job/image statuses, error/artifact representation, preview/download, polling semantics, ZIP endpoint и поведение test stubs. Frontend-agent не должен угадывать API по коду.

---

# 7. Agent Task 4 — Minimal Frontend

## Цель

Создать минимальный desktop-oriented UI полного MVP flow.

## Перед началом

Прочитать API Contract в handoff и использовать существующий backend contract. Не менять API только ради удобства frontend.

## UI

Пользователь может:

- выбрать один или несколько файлов;
- увидеть выбранные файлы;
- выбрать mode;
- запустить processing;
- увидеть job и per-image state;
- увидеть ошибки;
- открыть Original;
- открыть Positive;
- сравнить Original / Processed;
- скачать отдельный result;
- скачать batch results.

UI архитектурно знает `B&W`, `Colorize`, `Creative`, но реально работает только B&W. Остальные могут быть disabled / Not implemented. Для Creative предусмотреть prompt только на уровне, требуемом основным ТЗ; generative processing не реализовывать.

Ошибки привязывать к конкретному image/stage. Raw stack trace не показывать. При partial success успешные artifacts остаются доступны.

Не тратить этап на сложный дизайн. Приоритет: working flow, clear state/errors, before/after, download.

### Handoff Agent 4 → Agent 5

Зафиксировать frontend stack/structure, команды запуска, API client, polling/state mechanism, UI states, Original/Processed implementation, download flow, UX limitations и integration problems.

---

# 8. Agent Task 5 — MVP Integration & Hardening

## Цель

Получить реально работающий FilmPipe MVP и проверить Definition of Done.

Не добавлять новые крупные features. Основная работа:

```text
integration
testing
bug fixing
UX fixes
pipeline hardening
documentation
```

## End-to-end

На реальном B&W negative:

```text
open application
→ upload
→ select B&W
→ process
→ positive generated
→ preview
→ Original / Processed
→ download
```

Проверить batch.

Смешанный batch:

```text
valid
valid
corrupted
valid
```

должен дать:

```text
success
success
failed
success
```

а job — `partial_success`.

## Failure scenarios

### Decode failure

```text
Original
↓
Decode ✗
Result: failed
```

Пользователь получает понятную ошибку.

### Optional processor failure

Через test/stub:

```text
Conversion ✓
Normalization ✓
Positive ✓
Optional Processor ✗
```

Positive обязан сохраниться.

### Batch isolation

Failure одного image не влияет на остальные.

### Cleanup

Проверить разумное поведение temporary files и job artifacts. Сложная persistent job infrastructure не требуется.

## Финальная документация

README должен содержать:

- Requirements;
- Installation;
- Backend start;
- Frontend start;
- Development mode;
- Processing test;
- Supported image formats;
- Output artifacts;
- Logs location;
- Known MVP limitations.

Отдельно перечислить будущие extension points:

```text
DefectDetector
Restorer
InferenceProvider
Colorizer
GenerativeProcessor
```

Не реализовывать их.

## Финальный handoff

Agent 5 обновляет `AGENT_HANDOFF.md` до состояния итоговой технической карты MVP: что работает, как запустить, как тестировать, известные ограничения и где находятся extension points следующего этапа AI restoration.

---

# 9. Definition of Done всего MVP

Работа закончена, когда реальный открытый скан чёрно-белого негатива без внешнего графического редактора проходит:

```text
upload
→ FilmPipe
→ automatic processing
→ technically correct positive
→ preview
→ download
```

Также работают:

- single;
- batch;
- immutable original;
- separate derived artifacts;
- partial failure;
- per-image errors;
- batch isolation;
- logging;
- Original / Processed;
- batch download.

После этого MVP считается подтверждённым.

Следующий отдельный этап проекта:

```text
Defect Detection
→ Mask
→ Restoration
→ Clean Master
```

AI restoration не должен начинаться раньше завершения и проверки этого MVP.

---

# 10. Правило для всех агентов в одной фразе

> Сначала прочитай ТЗ, затем handoff, затем код; продолжай уже принятое решение, не выдумывай параллельную архитектуру, выполни только свою задачу, проверь результат и оставь следующему агенту точную актуальную выжимку состояния системы.
