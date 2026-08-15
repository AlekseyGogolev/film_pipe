# FilmPipe — Architecture Guardrails

> Дополнение к `FilmPipe_SPEC.md` и `FilmPipe_MVP_AGENT_PLAN.md`.
>
> Этот документ не задаёт конкретный framework, паттерн или структуру каталогов. Его задача — удерживать реализацию FilmPipe в простой, понятной и расширяемой архитектуре на протяжении MVP.

# 1. Главный принцип

FilmPipe должен быть спроектирован так, чтобы MVP оставался простым, но следующий processing stage можно было добавить без переписывания существующей системы.

Предпочтение:

```text
clear boundaries
+ small explicit contracts
+ composition
+ replaceable implementations
```

вместо:

```text
large abstractions
+ inheritance hierarchies
+ framework-specific domain logic
+ infrastructure "for the future"
```

Не создавать абстракцию только потому, что она теоретически может понадобиться.

Создавать границу там, где уже существует реальная ответственность, которую необходимо изолировать.

---

# 2. Архитектурная цель

Концептуально зависимости должны двигаться примерно так:

```text
Frontend
   ↓
HTTP API
   ↓
Application / Job orchestration
   ↓
Processing Engine
   ↓
Processing Pipeline
   ↓
Processor contracts
   ↓
Concrete processors
```

Дополнительные infrastructure dependencies:

```text
Processing / Application
        ↓
Artifact Storage

Concrete image processors
        ↓
Image libraries
(OpenCV / NumPy / etc.)
```

Это концептуальная dependency map, а не обязательная структура директорий.

Агент может выбрать другую структуру, если сохраняются те же границы ответственности.

---

# 3. Запрещённое направление зависимостей

Следующие зависимости считаются архитектурным smell и не должны появляться без сильной причины:

```text
Processing Engine → HTTP
Domain contracts → FastAPI / HTTP framework
Domain contracts → OpenCV
Domain contracts → конкретный ML framework
Processor → HTTP API
Frontend → OpenCV / NumPy / ML implementation
Frontend → filesystem implementation details
Pipeline → конкретный AI provider
Basic B&W processing → AI runtime
```

Особенно важно:

> Processing Engine должен оставаться вызываемым напрямую без HTTP.

И:

> Pipeline должен управлять последовательностью обработки, а не знать детали реализации каждого алгоритма.

---

# 4. Processor

`Processor` — минимальная единица processing pipeline.

Processor должен:

- выполнять одну понятную processing responsibility;
- иметь явный input/output contract;
- не зависеть от HTTP/UI;
- не управлять всем Job;
- не решать самостоятельно storage/API concerns, если это не является его прямой ответственностью;
- явно сообщать success/failure;
- быть заменяемым другой реализацией того же назначения.

Примеры responsibilities:

```text
Decode
Negative Conversion
Tone Normalization
Exposure Normalization
Defect Detection
Restoration
Colorization
Generative Processing
```

Не требуется создавать отдельный Processor на каждую строку кода.

Разделять этапы нужно по ответственности, а не ради количества классов.

---

# 5. Pipeline

Pipeline отвечает за orchestration processing stages.

Он должен знать:

```text
какие processors выполнить
в каком порядке
какой результат передать дальше
какой artifact считать полезным
как обработать failure конкретного stage
```

Он не должен знать:

```text
HTTP request/response
React/frontend state
детали конкретной AI model
детали конкретного inference provider
детали UI
```

Pipeline не должен превращаться в огромную функцию с hardcoded обработкой всех возможных режимов.

При этом не требуется строить generic workflow engine.

Нужен минимальный pipeline, достаточный FilmPipe.

---

# 6. Stable contracts

Особое внимание уделить небольшим стабильным контрактам между частями системы.

Минимально должны быть понятны границы вокруг:

```text
Processor
Pipeline
Artifact
Processing Job
Image Result
Processing Error
Storage
```

Контракты должны описывать FilmPipe concepts, а не детали библиотек.

Плохо:

```text
domain result = cv2.Mat-like implementation detail
```

Лучше:

```text
FilmPipe Image / Artifact contract
        ↓
конкретная image representation внутри processing implementation
```

Не создавать лишний wrapper, если библиотечный тип безопасно остаётся внутри processing layer.

Цель — не дать implementation details протечь через всю систему.

---

# 7. Composition over inheritance

Предпочитать composition.

Хорошее направление:

```text
Pipeline(
    processors=[
        DecodeProcessor(...),
        NegativeConverter(...),
        ToneNormalizer(...),
    ]
)
```

или эквивалентная простая композиция.

Избегать без необходимости:

```text
AbstractBaseProcessor
  ↓
AbstractImageProcessor
  ↓
AbstractNormalizationProcessor
  ↓
BaseToneNormalizationProcessor
  ↓
ConcreteToneNormalizationProcessor
```

Если inheritance не решает реальную проблему MVP — не использовать его.

---

# 8. Replaceability test

При архитектурных решениях использовать мысленный тест:

### Новый processor

Можно ли добавить:

```text
DefectDetector
```

без переписывания существующих `NegativeConverter` и `ToneNormalizer`?

### Замена реализации

Можно ли заменить один `ToneNormalizer` другим, не меняя HTTP API и frontend?

### Новый AI backend

Можно ли в будущем заменить:

```text
ONNX Runtime → PyTorch / TensorRT
```

без изменения Job model и frontend?

### Запуск без HTTP

Можно ли вызвать Processing Engine непосредственно из Python/CLI?

Если ответ «нет», проверить, не сцеплены ли ответственности слишком сильно.

Не требуется создавать реализации этих будущих сценариев сейчас.

---

# 9. Artifact boundary

Artifacts являются результатами processing, а не случайными temporary files.

Минимально:

```text
Original
Positive
```

В будущем:

```text
Clean Master
Restored
Colorized
Creative
Export
```

Artifact должен иметь понятную identity/type и связь с конкретным image/job.

Storage location не должен становиться единственной domain identity artifact.

Original immutable.

Новый artifact не заменяет предыдущий.

---

# 10. Failure boundary

Failure является частью архитектуры, а не исключительным edge case.

Pipeline должен сохранять уже полученный полезный результат.

Пример:

```text
Original
↓
Conversion ✓
↓
Normalization ✓
↓
Positive ✓
↓
Optional Restoration ✗
```

Система должна сохранить:

```text
Positive
```

и отдельно зафиксировать:

```text
Restoration failure
```

Не проектировать processing API вокруг единственного результата вида:

```text
everything succeeded
OR
throw and lose everything
```

Batch использует ту же модель:

```text
Image A ✓
Image B ✗
Image C ✓
```

Failure B не уничтожает A/C.

---

# 11. Infrastructure boundary

Filesystem, HTTP framework, logging backend и будущий inference runtime являются infrastructure details.

Их допустимо использовать напрямую внутри соответствующего implementation layer.

Не требуется создавать interface для каждой библиотеки.

Абстракция оправдана, если:

1. существует реальная граница ответственности;
2. реализация вероятно заменяема согласно FilmPipe SPEC;
3. abstraction упрощает тестирование или orchestration;
4. она уже нужна текущему коду.

Не создавать:

```text
ILoggerFactoryProviderAdapter
IFileSystemManagerFactory
IImageLibraryFacadeFactory
```

только ради «чистой архитектуры».

---

# 12. Application / Job orchestration

Processing Job и Processing Pipeline — связанные, но разные concepts.

Концептуально:

```text
Job
= пользовательская processing operation
= inputs + options + status + results + errors

Pipeline
= способ обработки одного image
```

Batch orchestration вызывает один и тот же image pipeline для N images.

Не создавать отдельные processing architectures:

```text
SingleImagePipeline
BatchImagePipeline
```

если batch можно представить как orchestration одного image pipeline.

---

# 13. Frontend boundary

Frontend работает с FilmPipe API concepts:

```text
Job
Image
Status
Artifact
Error
Mode
```

Frontend не должен знать:

```text
cv2
numpy dtype
CUDA
ONNX
PyTorch
TensorRT
конкретный processor class
filesystem path internals
```

Backend API должен предоставлять достаточно информации для UI, не раскрывая implementation details.

---

# 14. AI boundary

AI пока не является частью MVP processing foundation.

Архитектура должна позволять позднее построить:

```text
Positive
↓
DefectDetector
↓
Mask
↓
Restorer
↓
Clean Master
```

но сейчас не нужно создавать fake production infrastructure для этого flow.

Достаточно, чтобы существующий Processor/Pipeline design не блокировал его добавление.

AI provider details не должны проникать в:

```text
Job core model
Frontend
Basic B&W pipeline contracts
```

---

# 15. Простота структуры проекта

Агент должен стремиться к структуре, из которой новый разработчик или следующий агент быстро понимает:

```text
где domain concepts
где processing
где concrete processors
где application/job orchestration
где infrastructure
где API
где frontend
где tests
```

Пример концептуальной структуры:

```text
backend/
  domain/
  application/
  processing/
    pipeline
    processors/
  infrastructure/
  api/

frontend/

tests/
```

Это НЕ обязательный шаблон.

Если более простая структура лучше подходит реальному размеру проекта — использовать её.

Не создавать пустые layers/directories ради соответствия этому примеру.

---

# 16. Architecture check перед завершением Agent 1

Перед завершением первой задачи агент должен проверить:

- [ ] Processing Engine запускается без HTTP.
- [ ] Domain concepts не зависят от HTTP framework.
- [ ] Basic pipeline не зависит от AI.
- [ ] Concrete image library не протекла в API/frontend.
- [ ] Single/batch не требуют разных processing engines.
- [ ] Processor responsibilities ограничены.
- [ ] Pipeline занимается orchestration.
- [ ] Original immutable.
- [ ] Artifacts представлены явно.
- [ ] Failure model допускает partial result.
- [ ] Новый Processor можно добавить без переписывания существующих processors.
- [ ] Нет абстракций, не решающих текущую или явно заданную SPEC проблему.
- [ ] Нет второго способа делать то, для чего уже существует один понятный contract.
- [ ] Архитектура отражена в `AGENT_HANDOFF.md`.

---

# 17. Обязательная Architecture Map в handoff

После Agent 1 в `AGENT_HANDOFF.md` должна появиться короткая фактическая карта зависимостей проекта.

Не копировать пример автоматически. Описать то, что реально реализовано.

Формат примерно:

```text
HTTP API
  ↓
Application / Job Service
  ↓
Processing Engine
  ↓
Pipeline
  ↓
Processors

Application / Processing
  ↓
Artifact Storage

Concrete processors
  ↓
OpenCV / NumPy
```

Также явно перечислить запрещённые/отсутствующие зависимости, если это важно:

```text
Processing Engine DOES NOT depend on HTTP.
Frontend DOES NOT know processor implementations.
Domain DOES NOT depend on OpenCV.
```

Следующий агент должен сначала свериться с этой картой, прежде чем добавлять новые зависимости.

---

# 18. Правило изменения архитектуры следующими агентами

Architecture Agent 1 не является неприкосновенной.

Но менять принятое решение можно только если найдено одно из следующего:

1. оно противоречит `FilmPipe_SPEC.md`;
2. оно блокирует текущую обязательную функцию MVP;
3. оно создаёт доказанную техническую проблему;
4. фактическая реализация уже разошлась с первоначальным решением и её необходимо привести в согласованное состояние.

Формулировка:

```text
"Я бы сделал иначе"
```

не является достаточной причиной.

При изменении архитектуры обязательно обновить:

```text
AGENT_HANDOFF.md
```

и кратко зафиксировать:

```text
OLD
NEW
WHY
IMPACT
```

---

# 19. Анти-оверинжиниринг

FilmPipe MVP не является демонстрацией паттернов проектирования.

Не использовать архитектурные конструкции ради самих конструкций.

Перед созданием abstraction спросить:

```text
Какую существующую проблему FilmPipe она решает прямо сейчас?
```

Если ответа нет, скорее всего abstraction пока не нужна.

Особенно избегать:

- factory ради создания одного класса;
- repository abstraction поверх единственного простого filesystem storage без причины;
- event bus для последовательного локального pipeline;
- dependency injection framework для нескольких объектов;
- generic workflow engine;
- service locator;
- глубокие inheritance trees;
- microservice boundaries внутри локального MVP;
- универсальную plugin system;
- premature async/distributed architecture.

Простая архитектура с хорошими границами предпочтительнее сложной «идеальной» архитектуры.

---

# 20. Итоговый критерий

Хорошая архитектура FilmPipe MVP — это архитектура, в которой:

> текущий B&W pipeline легко понять и запустить, а следующий реальный processing stage можно добавить локально, не переписывая приложение целиком.

Если архитектура делает MVP заметно сложнее, но не помогает текущей реализации и не защищает явно заданный extension point из `FilmPipe_SPEC.md`, её следует упростить.
