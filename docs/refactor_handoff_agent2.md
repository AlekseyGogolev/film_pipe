# FilmPipe Refactor Handoff - Agent 2

Agent 3 note: this file records the intermediate state after Agent 2. The final
contract is documented in `docs/refactor_final_audit.md`.

Agent 2 completed the frontend/API migration to Agent 1's runtime contract:

```text
input_processing = already_positive | bw_negative
restoration = off | telea | lama
```

## UI Controls

Active controls left in the frontend:

- file selection;
- clear selected files;
- primary `Process` action;
- `Input Processing`: `Already Positive`, `Negative -> Positive`;
- `Restoration`: `Off`, `TELEA`, `LaMa`.

Removed from the active UI:

- top-level `B&W` mode;
- disabled `Colorize` and `Creative` buttons;
- creative prompt field;
- input polarity buttons.

`B&W` is now represented only as the `Negative -> Positive` input-processing
choice.

## Frontend API Payload

`frontend/src/api.ts` now sends only:

```text
input_processing=already_positive|bw_negative
restoration=off|telea|lama
files=(binary)
```

Payload audit through headless Chrome/CDP intercepted real frontend submits:

| Case | input_processing | restoration | has file | extra fields |
| --- | --- | --- | --- | --- |
| `already_positive + off` | `already_positive` | `off` | yes | no |
| `already_positive + lama` | `already_positive` | `lama` | yes | no |
| `bw_negative + off` | `bw_negative` | `off` | yes | no |
| `bw_negative + lama` | `bw_negative` | `lama` | yes | no |

## Types / Response Contract

`frontend/src/types.ts` now expects:

```ts
job.input_processing
job.restoration
```

Current public frontend artifact types are:

```text
original | positive | restored
```

## Artifact Rendering

The UI renders only public artifacts returned by the API, ordered as:

```text
original, positive, restored
```

It no longer creates placeholder/missing `Positive` cards.

Mock response rendering audit through headless Chrome/CDP:

| Case | Rendered cards |
| --- | --- |
| `already_positive + off` | `Original` |
| `already_positive + lama` | `Original`, `Restored` |
| `bw_negative + off` | `Original`, `Positive` |
| `bw_negative + lama` | `Original`, `Positive`, `Restored` |

This matches Agent 1's public artifact semantics.

## Layout

The control panel was changed from one rigid row to two responsive rows:

```text
Row 1: files / Process
Row 2: Input Processing / Restoration
```

Headless Chrome screenshots and DOM metrics were checked at:

- `1440x900`;
- `900x900`;
- `390x900`;
- the same three viewports with a very long selected filename.

DOM audit results for all checked viewports:

- `Process` stayed inside `.controlBand`;
- `documentScrollWidth` did not exceed viewport width;
- `overflowingElements` was empty;
- the long filename produced one `.fileRow` and did not create horizontal overflow.

Temporary screenshot files created during verification:

```text
/tmp/filmpipe-layout-1440.png
/tmp/filmpipe-layout-900.png
/tmp/filmpipe-layout-390.png
```

Note: the Browser plugin was unavailable in this environment (`No browser is
available`), so verification used installed headless `google-chrome` via CDP.

## Docs Updated

Updated:

- `frontend/README.md`

The frontend README now documents `input_processing/restoration` and the current
active controls. Full repository-wide docs cleanup is still left for Agent 3.

## Verification

Commands run:

```bash
cd frontend
npm run build
```

Result:

```text
tsc --noEmit: success
vite build: success
```

Additional checks:

```bash
git diff --check
curl -I http://127.0.0.1:5174/
```

Results:

```text
git diff --check: success
local Vite HTTP smoke: HTTP/1.1 200 OK
```

Vite dev server was started for manual/headless verification and used:

```text
http://127.0.0.1:5174/
```

## Notes For Agent 3

- Agent 3 should perform the full contract sweep across root README, older docs,
  handoff files, and tests.
- Do not reintroduce colorize/creative behavior during the cleanup pass.
