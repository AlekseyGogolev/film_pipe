# FilmPipe Refactor Handoff - Agent 1

Agent 1 completed the backend/domain/API refactor toward the new runtime model:

```text
input_processing = already_positive | bw_negative
restoration = off | telea | lama
```

## Backend Contract

Domain:

```python
class InputProcessingMode(str, Enum):
    ALREADY_POSITIVE = "already_positive"
    BW_NEGATIVE = "bw_negative"

class ProcessingOptions:
    input_processing: InputProcessingMode = InputProcessingMode.BW_NEGATIVE
    restoration: RestorationMode = RestorationMode.OFF
```

Removed from the backend domain/job model:

- `ProcessingMode`
- `InputPolarity`
- `ProcessingOptions.prompt`
- `ProcessingOptions.extra`
- `ProcessingJob.selected_modes`

`RestorationMode` remains:

```text
off | telea | lama
```

## API Contract

Primary request:

```text
POST /jobs
multipart:
  files=...
  input_processing=already_positive|bw_negative
  restoration=off|telea|lama
```

Defaults:

```text
input_processing=bw_negative
restoration=off
```

Primary response fields:

```json
{
  "id": "...",
  "status": "success",
  "input_processing": "bw_negative",
  "restoration": "off",
  "images": []
}
```

Removed from response:

- `mode`
- `polarity`
- `selected_modes`

Transitional compatibility:

- If `input_processing` is absent, legacy form fields are mapped at the API boundary only.
- `mode=off` maps to `already_positive`.
- `mode=bw&polarity=positive` maps to `already_positive`.
- `mode=bw&polarity=negative` maps to `bw_negative`.
- unsupported legacy modes such as `colorize` and `creative` return 400.
- legacy `prompt` is not represented in domain/application/engine.

Agent 2 should send only `input_processing` and `restoration`.

## Audit Map

API request parsing:

- `backend/filmpipe/api/app.py` parses `input_processing` and `restoration`.
- invalid `input_processing` returns 400 with allowed values.
- invalid `restoration` returns 400 with allowed values.

Domain model:

- `ProcessingOptions` now carries only input processing and restoration choices.
- There is no backend `mode/polarity` pair controlling unrelated stages.

Application / JobService:

- `JobService.process()` no longer accepts `selected_modes`.
- job logging now includes `input_processing`, `restoration`, and input count.
- pipeline factories can still be option-aware for tests and future processors.

Engine / pipeline factory:

- `default_pipeline(options)` builds the processor list from operations.
- `restoration=off` means no restoration processor is added.
- no default processor performs a runtime "skip because option is off" branch.

Processors:

- `DecodePositiveImageProcessor` preserves decoded dtype/channels and sets internal `working_positive`.
- `DecodeBWImageProcessor` performs the old B&W decode/preparation path.
- `NegativeConverterProcessor` and `ToneNormalizerProcessor` are only in `bw_negative` plans.
- `PositiveArtifactWriterProcessor` writes public `positive` only for B&W negative conversion and sets internal `working_positive` from the saved 16-bit output.
- `AIRestorationProcessor` reads `context.working_positive`, not `ArtifactType.POSITIVE`.

Artifacts / response:

- public artifacts are still exposed via existing preview/download endpoints.
- batch ZIP still excludes `original` and includes generated artifacts only.

## Public Artifact Semantics

| Input Processing | Restoration | Public Artifacts |
| --- | --- | --- |
| `already_positive` | `off` | `original` |
| `already_positive` | `telea` | `original`, `restored` |
| `already_positive` | `lama` | `original`, `restored` |
| `bw_negative` | `off` | `original`, `positive` |
| `bw_negative` | `telea` | `original`, `positive`, `restored` |
| `bw_negative` | `lama` | `original`, `positive`, `restored` |

Rationale: for already-positive uploads, the uploaded source is already the base result. FilmPipe does not create a fake public `positive` derivative just to satisfy the old B&W artifact model.

## Execution Plans

```text
already_positive + off
  decode_positive

already_positive + telea
  decode_positive, ai_restoration

already_positive + lama
  decode_positive, ai_restoration

bw_negative + off
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer

bw_negative + telea
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration

bw_negative + lama
  decode_bw, negative_conversion, tone_normalization, positive_artifact_writer, ai_restoration
```

Log shape:

```text
job_started input_processing=already_positive restoration=lama inputs=...
pipeline_ready input_processing=already_positive restoration=lama processors=decode_positive,ai_restoration
```

## Failure Model

- optional restoration failure after B&W conversion preserves the public `positive` artifact and produces partial success.
- optional restoration failure after already-positive decode preserves the internal working positive and produces partial success, even though there is no public `positive` artifact.
- corrupt input before working positive remains image failure.
- batch semantics are unchanged: one image failure does not stop the batch.

## Tests Updated

Covered in tests:

- all six execution plans;
- all six public artifact combinations;
- invalid `input_processing`;
- invalid `restoration`;
- absence of negative conversion and tone normalization for `already_positive`;
- color already-positive decode preserves channels instead of grayscale conversion;
- restoration uses working positive and not public positive;
- restoration failure after base result;
- batch partial success behavior;
- API response contract migration.

## Verification

Commands run:

```text
python3 -m compileall backend/filmpipe
.venv/bin/python -m pytest
git diff --check
```

Results:

```text
compileall: success
pytest: 56 passed in 0.52s
git diff --check: success
```

Note: system `python3 -m pytest` failed because pytest is not installed outside the project `.venv`; verification used `.venv/bin/python`.

## Risks / Questions For Agent 2

- Frontend must migrate form submission to `input_processing` and stop sending `mode`, `polarity`, and `prompt`.
- Frontend types must read `job.input_processing`; `job.mode`, `job.polarity`, and `job.selected_modes` are gone from the backend response.
- UI artifact rendering should use the matrix above. For `already_positive + off`, show `Original` only or treat it as the base result; do not expect a public `positive`.
- Existing README/frontend docs still contain legacy wording from earlier work. Agent 3 should do the final docs/legacy sweep after Agent 2 migrates the UI.
- The API legacy mapper is intentionally transitional. After the frontend is migrated, Agent 3 can decide whether to remove it or keep it for short-term compatibility.
