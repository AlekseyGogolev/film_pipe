# FilmPipe Refactor Agent Plan

Этот документ является рабочим заданием для последовательного рефакторинга FilmPipe.

Его цель - убрать накопившуюся путаницу между `mode`, `polarity`, старым `off`,
B&W processing, restoration и будущими `colorize` / `creative`, не ломая уже
работающие B&W conversion и optional AI restoration.

Запускать агентов нужно последовательно:

```text
Agent 1: audit + backend/domain/API refactor
↓
Agent 2: frontend + UX + API migration
↓
Agent 3: full regression, legacy sweep, docs, final architectural audit
```

Agent 2 обязан читать handoff Agent 1. Agent 3 обязан читать handoff Agent 1 и
Agent 2. Не запускать этих агентов параллельно: они будут менять одни и те же
контракты и закрепят разные модели.

---

## Current Project State

Текущий код уже частично исправлен после проблемы с positive input:

- API сейчас принимает `mode`, `polarity`, `restoration`, `prompt`.
- Frontend отправляет `mode=bw`, `polarity=positive|negative`,
  `restoration=off|telea|lama`, а также держит `prompt`.
- Domain сейчас имеет `ProcessingMode`, `InputPolarity`, `RestorationMode`.
- Engine уже строит default pipeline из options.
- Для `mode=bw`, `polarity=positive`, `restoration=lama` последний job логировал:

```text
job_started mode=bw polarity=positive restoration=lama
pipeline_ready mode=bw polarity=positive restoration=lama processors=decode,positive_artifact_writer,ai_restoration
```

То есть `NegativeConverterProcessor` и `ToneNormalizerProcessor` уже не
запускались для positive input.

Но оставшаяся проблема не решена архитектурно:

- `DecodeImageProcessor` всё ещё является B&W decoder/preparation и переводит
  цветной positive input в grayscale.
- `PositiveArtifactWriterProcessor` всегда создаёт public `positive` artifact.
- `AIRestorationProcessor` сейчас читает `ArtifactType.POSITIVE`, а не
  abstract working positive.
- Поэтому для user flow `positive input + LaMa` UI показывает:

```text
Original
Positive
Restored
```

Пользователь ожидает:

```text
Original
Restored
```

или, при restoration off, понятный базовый result без fake B&W-conversion.

Frontend также имеет layout issue: верхняя control panel пытается уместить
files, mode, input, restoration, prompt и process button в одну строку. При
узком viewport или открытом DevTools кнопка `Process` выходит за границы.

---

## Target Business Model

На текущем этапе FilmPipe должен иметь две независимые пользовательские
настройки.

### 1. Input Processing

```text
already_positive
bw_negative
```

Смысл:

```text
Already Positive
```

Пользователь загружает уже готовое позитивное изображение.

FilmPipe:

- декодирует изображение;
- не инвертирует его;
- не выполняет B&W-negative-specific preparation;
- не переводит цветной input в grayscale только из-за старого `mode=bw`;
- считает декодированное изображение current working positive.

Концептуально:

```text
input positive
↓
decode
↓
working positive
```

```text
B&W Negative -> Positive
```

Пользователь загружает чёрно-белый негатив.

FilmPipe выполняет существующий B&W path:

```text
negative
↓
decode / B&W preparation
↓
negative conversion
↓
tone normalization
↓
working positive
```

### 2. Restoration

```text
off
telea
lama
```

Restoration всегда получает `working positive`.

Она не должна знать, каким способом `working positive` был получен:

```text
already_positive input
или
bw_negative conversion
```

### Six Supported Runtime Cases

Система обязана корректно поддерживать ровно эти шесть комбинаций:

```text
already_positive + off
already_positive + telea
already_positive + lama
bw_negative + off
bw_negative + telea
bw_negative + lama
```

---

## Future Modes Boundary

На текущем этапе не реализовывать заново:

```text
colorize
creative
prompt
```

Их нельзя смешивать с `Input Processing`.

Будущая модель может стать:

```text
Input Processing
↓
Restoration
↓
Final Processing
```

Где future `Final Processing` сможет иметь:

```text
standard
colorize
creative
```

Но это не задача текущего refactor.

---

# Agent 1 - Backend / Domain / API Refactor

## Goal

Провести backend audit и привести domain/API/application/engine/pipeline к
единой модели:

```text
input_processing = already_positive | bw_negative
restoration = off | telea | lama
```

Agent 1 не занимается полноценным frontend redesign. Он должен оставить ясный
backend contract и handoff для Agent 2.

## Required Audit Before Edits

Проследить flow:

```text
API request
↓
API parsing/schema
↓
ProcessingOptions / domain model
↓
JobService
↓
Processing Engine
↓
Pipeline factory
↓
Processors
↓
Artifacts
↓
API response
```

Найти все backend usages:

```text
mode
polarity
input_type
off
bw
positive
negative
restoration
telea
lama
colorize
creative
prompt
```

Составить краткую карту:

- current parameters;
- defaults;
- compatibility mappings;
- places where parameters are ignored;
- places where one parameter controls unrelated stages;
- processor-level skip/no-op conditions.

## Backend Contract

Выбрать понятные semantic names.

Рекомендуемый API:

```text
POST /jobs
multipart:
  input_processing=already_positive|bw_negative
  restoration=off|telea|lama
  files=...
```

Рекомендуемый domain enum:

```python
class InputProcessingMode(str, Enum):
    ALREADY_POSITIVE = "already_positive"
    BW_NEGATIVE = "bw_negative"
```

`RestorationMode` можно сохранить, если он уже соответствует новой модели.

Убрать или осознанно мигрировать:

- `ProcessingMode.BW` как current user-facing mode;
- `ProcessingMode.OFF`;
- `InputPolarity`, если новая модель делает его избыточным;
- `selected_modes`, если это только legacy;
- API fields `mode`, `polarity`;
- old `prompt` field, если он не используется текущим runtime;
- backend branches for disabled `colorize` / `creative`.

Не оставлять две параллельные модели без необходимости.

## Pipeline Requirements

Pipeline должен собираться из выбранных operations.

### already_positive + off

```text
DecodePositive
WorkingPositive
BaseResult
```

Не должно быть:

```text
NegativeConverter
ToneNormalizer
AIRestoration
```

### already_positive + telea/lama

```text
DecodePositive
WorkingPositive
AIRestoration
```

Не должно быть:

```text
NegativeConverter
ToneNormalizer
```

### bw_negative + off

```text
DecodeB&W
NegativeConverter
ToneNormalizer
PositiveWriter / BaseResult
```

### bw_negative + telea/lama

```text
DecodeB&W
NegativeConverter
ToneNormalizer
PositiveWriter / BaseResult
AIRestoration
```

Processor не должен делать:

```python
if option says skip:
    return image
```

Если stage выключен, processor не должен попадать в execution plan.

## Working Image vs Public Artifacts

Разделить internal processing representation и public artifacts.

Для already-positive input:

```text
original = uploaded source
working positive = decoded source image
```

Нельзя создавать artificial grayscale public `positive` только потому, что
старый B&W mode ожидал `positive` artifact.

Restoration должна работать от `working positive`, а не обязательно от
`ArtifactType.POSITIVE`.

Agent 1 должен выбрать и зафиксировать minimal public artifact semantics.
Рекомендуемая матрица:

| Input Processing | Restoration | Public Artifacts |
| --- | --- | --- |
| already_positive | off | original, optionally base_result only if useful |
| already_positive | telea | original, restored |
| already_positive | lama | original, restored |
| bw_negative | off | original, positive |
| bw_negative | telea | original, positive, restored |
| bw_negative | lama | original, positive, restored |

Если Agent 1 выбирает другой artifact set, он обязан объяснить почему в handoff.

## Restoration Invariants

Не менять доказанный AI restoration algorithm:

- Microsoft detector;
- mask postprocessing;
- TELEA;
- LaMa integration;
- final composite safety.

Invariant:

```text
outside restoration mask == working positive pixels
```

## Failure Model

Сохранить FilmPipe invariants:

- optional restoration failure не уничтожает базовый результат;
- batch semantics не менять;
- failure одного image не останавливает batch;
- user-facing errors без stack traces.

Для already-positive input:

```text
working positive OK
restoration failed
```

не должен становиться full failure, если базовый результат доступен.

## Logging

Каждый job/image должен позволять увидеть execution plan:

```text
input_processing=already_positive restoration=lama processors=decode_positive,ai_restoration
```

и:

```text
input_processing=bw_negative restoration=lama processors=decode_bw,negative_conversion,tone_normalization,positive_writer,ai_restoration
```

Использовать существующий logging mechanism.

## Agent 1 Tests

Обязательно покрыть:

- `already_positive + off`;
- `already_positive + telea`;
- `already_positive + lama`;
- `bw_negative + off`;
- `bw_negative + telea`;
- `bw_negative + lama`;
- invalid `input_processing`;
- invalid `restoration`;
- pipeline processor lists;
- absence of `NegativeConverter` for already-positive input;
- absence of `ToneNormalizer` for already-positive input;
- color positive input does not become grayscale just because of input mode;
- restoration failure after base result;
- batch semantics;
- artifact behavior.

Heavy LaMa in unit tests can be fake/mock.

## Agent 1 Handoff

Create:

```text
docs/refactor_handoff_agent1.md
```

Include:

- final backend/domain model;
- final API contract;
- removed/migrated legacy fields;
- public artifact semantics matrix;
- execution plans for six cases;
- tests added/updated;
- verification commands and outputs;
- risks/questions for frontend.

---

# Agent 2 - Frontend / UX / API Migration

## Input

Read first:

```text
docs/refactor_handoff_agent1.md
```

Agent 2 must migrate the UI to Agent 1's contract.

## Goal

Replace current UI model:

```text
B&W
Colorize
Creative
Prompt
Input Negative/Positive bolted onto mode=bw
```

with current runtime model:

```text
Input Processing:
[Already Positive] [Negative -> Positive]

Restoration:
[Off] [TELEA] [LaMa]
```

## UI Requirements

Working UI should show only current meaningful controls:

- file selection;
- primary process action;
- Input Processing;
- Restoration.

Remove from active UI:

- B&W top-level mode button;
- disabled Colorize;
- disabled Creative;
- Creative prompt.

B&W is now the action `Negative -> Positive`, not a final/user-facing mode.

## Layout Requirements

Fix the control panel overflow.

Do not force all controls into one rigid row.

Preferred layout:

```text
Row 1:
files / primary action

Row 2:
Input Processing / Restoration
```

or equivalent responsive layout.

Verify:

- normal desktop viewport;
- narrow viewport;
- browser with DevTools open;
- long localized labels;
- long file names.

`Process` button must not overflow the control container.

## API Payload

Frontend must send the new API fields only, for example:

```text
input_processing=already_positive
restoration=lama
files=(binary)
```

Do not send legacy fields if Agent 1 removed them:

```text
mode
polarity
prompt
```

## Rendering Requirements

Render artifacts according to Agent 1's artifact semantics.

Expected behavior if Agent 1 uses the recommended matrix:

### already_positive + off

Show uploaded/base result without implying negative conversion.

### already_positive + telea/lama

Show:

```text
Original
Restored
```

Do not show a fake/intermediate grayscale `Positive` card.

### bw_negative + off

Show:

```text
Original
Positive
```

### bw_negative + telea/lama

Show:

```text
Original
Positive
Restored
```

## Agent 2 Tests / Verification

Update frontend types and API client.

Verify:

- payload for `already_positive + off`;
- payload for `already_positive + lama`;
- payload for `bw_negative + off`;
- payload for `bw_negative + lama`;
- no legacy `mode/polarity/prompt` in new request;
- rendering artifact cards from API response;
- layout does not overflow.

Minimum command:

```bash
npm run build
```

Manual check through DevTools Network is strongly recommended.

## Agent 2 Handoff

Create:

```text
docs/refactor_handoff_agent2.md
```

Include:

- controls left in UI;
- exact payload sent by frontend;
- legacy UI removed;
- artifact rendering rules;
- viewports checked;
- commands run and results;
- notes for Agent 3.

---

# Agent 3 - Full Regression / Legacy Sweep / Docs / Final Audit

## Input

Read first:

```text
docs/refactor_handoff_agent1.md
docs/refactor_handoff_agent2.md
```

## Hard Rule

Agent 3 must not add new functionality.

Agent 3 is a cleanup/audit/regression agent only.

Do not add:

- new modes;
- new processors;
- auto-detect polarity;
- colorize API;
- creative API;
- prompt behavior;
- plugin system;
- new restoration algorithm.

Allowed changes:

- fix bugs introduced by refactor;
- remove legacy remnants;
- update tests;
- update docs;
- fix naming inconsistencies;
- fix logging inconsistencies;
- small cleanup required to complete the new model.

## Legacy Sweep

Search for:

```text
mode
polarity
input_type
off
bw
positive
negative
restoration
telea
lama
colorize
creative
prompt
```

For every occurrence classify it:

- current model;
- legacy to delete;
- documented compatibility;
- future docs only;
- unrelated domain language.

Especially inspect:

- `README.md`;
- `frontend/README.md`;
- `AGENT_HANDOFF.md`;
- `docs/*`;
- `backend/filmpipe/domain/models.py`;
- `backend/filmpipe/api/app.py`;
- `backend/filmpipe/application/jobs.py`;
- `backend/filmpipe/processing/engine.py`;
- `backend/filmpipe/processing/processors/*`;
- `frontend/src/types.ts`;
- `frontend/src/api.ts`;
- `frontend/src/App.tsx`;
- all tests.

## Regression Matrix

Prove these six cases:

| Case | Must Prove |
| --- | --- |
| already_positive + off | no negative conversion, no tone normalization, no restoration |
| already_positive + telea | no negative conversion, TELEA gets working positive |
| already_positive + lama | no negative conversion, LaMa gets working positive |
| bw_negative + off | existing B&W conversion works |
| bw_negative + telea | conversion then TELEA works |
| bw_negative + lama | conversion then LaMa works |

For every case verify:

- request payload;
- parsed backend options;
- execution plan;
- absent processors are truly absent;
- public artifacts;
- frontend rendering;
- status/failure semantics.

## Required Invariants

Agent 3 must verify:

- positive input is not inverted;
- color positive input is not forced into grayscale by old B&W path;
- negative input still produces technically correct positive;
- restoration gets working positive;
- optional restoration failure preserves base result;
- batch semantics unchanged;
- public artifacts are useful user results, not accidental internal stages;
- logs expose execution plan;
- docs describe exactly one current model.

## Docs

Update docs to the final model:

```text
Input Processing
Restoration
```

Remove stale docs describing the old current runtime as:

```text
mode=bw
mode=off
polarity
```

unless it is explicitly documented as temporary compatibility and is actually
still supported.

## Agent 3 Final Deliverable

Create:

```text
docs/refactor_final_audit.md
```

Include:

- final business model;
- final API contract;
- final artifact semantics matrix;
- execution plans for six cases;
- legacy sweep results;
- tests run and outputs;
- known limitations;
- what was intentionally not implemented.

## Final Verification Commands

Minimum:

```bash
.venv/bin/python -m pytest
cd frontend
npm run build
```

Manual smoke check:

```text
backend running
frontend running
already_positive + lama job
bw_negative + off job
DevTools payload verified
artifact cards verified
logs verified
```

---

## Non-Goals For This Refactor

Do not implement:

- automatic negative/positive detection;
- new colorization;
- new creative generation;
- prompt workflow;
- accounts/auth;
- persistent DB/job queue;
- plugin architecture;
- new AI restoration quality work;
- replacement of Microsoft detector, TELEA, or LaMa.

This refactor is about making current FilmPipe semantics explicit and stable.
