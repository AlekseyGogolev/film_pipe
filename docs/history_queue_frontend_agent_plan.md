# FilmPipe History, Queue, And Gallery - Agent Plan

Дата: 2026-08-18.

Статус: ТЗ для следующего этапа после MVP. Этот документ предназначен для нескольких агентов, которые будут параллельно доводить backend/frontend до асинхронных jobs, persistent history и gallery/lightbox UX.

## Product Goal

Сделать FilmPipe удобным для долгих локальных обработок:

- пользователь запускает job и сразу получает ответ от API;
- backend продолжает обработку в фоне и не держит HTTP request открытым минутами;
- frontend polling показывает реальный прогресс job;
- история предыдущих jobs доступна после перезапуска backend;
- отдельный экран Gallery/History показывает прошлые результаты из `data/jobs`;
- клик по preview открывает изображение на весь экран без скачивания master-файла.

## Current State

Backend уже имеет:

- `GET /jobs`;
- `POST /jobs`;
- `GET /jobs/{job_id}`;
- `GET /jobs/{job_id}/images/{image_id}`;
- artifact preview/download endpoints;
- batch ZIP endpoint;
- `FileSystemArtifactStore(root="data/jobs")`;
- public artifacts: `original`, `positive`, `restored`, `creative`.

Но сейчас:

- `POST /jobs` синхронный и вызывает `JobService.process(...)` прямо внутри request handler;
- real queue отсутствует;
- registry хранится в памяти через `InMemoryJobRegistry`;
- после перезапуска backend jobs исчезают из API, хотя файлы остаются в `data/jobs`;
- старые job folders не имеют manifest-файла;
- frontend хранит только один active job;
- polling на frontend уже есть, но почти бесполезен для долгих jobs, потому что `POST /jobs` возвращается только после завершения.

Ключевые файлы:

- `backend/filmpipe/api/app.py`
- `backend/filmpipe/application/jobs.py`
- `backend/filmpipe/infrastructure/storage.py`
- `backend/filmpipe/domain/models.py`
- `backend/filmpipe/processing/engine.py`
- `backend/filmpipe/processing/pipeline.py`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `tests/test_api.py`
- `tests/test_jobs.py`
- `tests/test_storage.py`

## Non-Negotiable Constraints

- Frontend must talk only to FilmPipe API. It must not inspect filesystem paths and must not call AI/Creative runtime directly.
- API must not expose raw local filesystem paths.
- Existing artifact preview/download URLs must remain valid.
- Existing public artifact semantics must remain unchanged.
- Original uploaded files must remain immutable.
- `final_processing=standard` must not instantiate or call Creative provider.
- Restoration and Creative failures remain recoverable where they are recoverable today.
- Normal test suite must not launch real heavy AI/Creative inference.
- Keep implementation local-first and MVP-friendly. Do not introduce Redis, Celery, Postgres, auth, WebSockets, or distributed workers unless a later task explicitly asks for that.
- Use `pytest` for backend verification and `npm run build` for frontend verification.

## Target API Behavior

### Create Job

`POST /jobs` should:

1. Validate multipart fields exactly as today.
2. Persist uploads into a stable job-owned input/staging directory, not a temporary directory that disappears before background work starts.
3. Create a `ProcessingJob` with `pending` or `running` status.
4. Save it to persistent registry/manifest.
5. Enqueue background processing.
6. Return quickly with HTTP `201` and the current job representation.

The response shape should stay compatible:

```text
id
status
input_processing
restoration
final_processing
created_at
updated_at
images[]
errors[]
download_url
```

Adding extra fields is allowed only if they are useful and documented, for example:

```text
position
progress
legacy
```

But do not require frontend to depend on raw filesystem state.

### Poll Job

`GET /jobs/{job_id}` should return the latest persisted/in-memory state:

- `pending` before work starts;
- `running` while at least one image is pending/running;
- `success` when all images succeeded;
- `failed` when all images failed;
- `partial_success` only after processing is done and results are mixed or recoverable errors exist.

Important: fix `ProcessingJob.recompute_status()`. Current logic can report `partial_success` too early if one image is done and another is still pending/running.

### List Jobs

`GET /jobs` should return jobs from persistent history, not only current process memory.

Recommended ordering:

- newest first by `created_at` or best available inferred timestamp;
- running/pending jobs still included;
- legacy reconstructed jobs included.

For gallery performance, either keep current full job shape for MVP or add a documented summary endpoint later. If adding summary fields, keep old behavior backward-compatible.

### Artifact Preview

Existing endpoint stays:

```text
GET /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview
```

Recommended optional enhancement:

```text
GET /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview?max_edge=512
GET /jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview?max_edge=1920
```

Use smaller previews for gallery thumbnails and larger previews for fullscreen modal. Downloads must still use `download_url` and preserve original artifact format/bit depth.

## Persistent Storage Design

Recommended manifest path:

```text
data/jobs/{job_id}/job.json
```

Recommended stable upload/input path:

```text
data/jobs/{job_id}/inputs/{index}/{safe_filename}
```

Existing artifact layout should remain:

```text
data/jobs/{job_id}/{image_id}/original/{source_filename}
data/jobs/{job_id}/{image_id}/positive/{safe_stem}_positive.tiff
data/jobs/{job_id}/{image_id}/restored/{safe_stem}_restored.tiff
data/jobs/{job_id}/{image_id}/creative/{safe_stem}_creative.png
```

Manifest should include only API-safe data and relative paths needed to rebuild `Artifact` objects internally:

```json
{
  "schema_version": 1,
  "id": "job-id",
  "status": "success",
  "input_processing": "bw_negative",
  "restoration": "off",
  "final_processing": "standard",
  "creative_prompt": null,
  "created_at": "2026-08-18T08:43:45.981000+00:00",
  "updated_at": "2026-08-18T08:43:45.988000+00:00",
  "inputs": [
    "inputs/0/scan.tiff"
  ],
  "images": [
    {
      "id": "image-id",
      "filename": "scan.tiff",
      "status": "success",
      "artifacts": [
        {
          "type": "original",
          "filename": "scan.tiff",
          "mime_type": "image/tiff",
          "relative_path": "image-id/original/scan.tiff"
        }
      ],
      "errors": []
    }
  ],
  "errors": [],
  "legacy": false
}
```

Implementation detail: API responses should still be produced through existing response helpers or equivalent centralized mappers.

## Legacy History Reconstruction

There are existing folders under `data/jobs` without `job.json`. They should be visible in Gallery.

For legacy jobs:

- scan `data/jobs/{job_id}/{image_id}/{artifact_type}/*`;
- infer image filename from first `original` artifact if present;
- infer image status from artifacts:
  - generated artifacts exist: likely `success`;
  - only original exists: likely `success` for already-positive/off or failed decode; cannot know exactly;
  - no readable artifacts: likely `failed` or skip broken image;
- set options to reasonable default or add explicit unknown/legacy handling.

Preferred API-safe approach:

- keep response contract values valid by defaulting legacy options to:
  - `input_processing="bw_negative"`;
  - `restoration="off"`;
  - `final_processing="standard"`;
- add optional `legacy: true` / `inferred: true` metadata if extending response shape.

Do not pretend inferred values are exact in docs or UI.

## Agent Breakdown

### Agent 1 - Backend Persistent Job Store

Goal: make job history survive backend restarts and expose old `data/jobs` folders through the API.

Primary files:

- `backend/filmpipe/application/jobs.py`
- `backend/filmpipe/infrastructure/storage.py`
- new file allowed: `backend/filmpipe/infrastructure/job_store.py`
- `backend/filmpipe/domain/models.py`
- `tests/test_storage.py`
- `tests/test_api.py`
- new tests allowed: `tests/test_job_store.py`

Tasks:

- Implement JSON serialization/deserialization for `ProcessingJob`, `ImageProcessingResult`, `Artifact`, and `ProcessingError`.
- Store relative artifact paths in manifest, but rebuild internal `Artifact.path` as server-local `Path`.
- Add persistent repository/registry abstraction.
- Load manifests from `data/jobs` on app startup or registry initialization.
- Add legacy scanner for job directories without manifest.
- Preserve `InMemoryJobRegistry` only for tests or replace it with a compatible interface.
- Ensure `_job_response()` does not expose local paths.

Acceptance criteria:

- Creating a job writes `data/jobs/{job_id}/job.json`.
- A new app instance pointed at the same jobs root can `GET /jobs` and `GET /jobs/{job_id}`.
- Existing legacy directories without `job.json` appear in `GET /jobs`.
- Artifact preview/download works for manifest-loaded and legacy-loaded jobs.
- Backend tests pass without real AI runtime.

Notes:

- Coordinate interface names with Agent 2 before large edits.
- Avoid putting HTTP/FastAPI concerns into persistence code.

### Agent 2 - Backend Async Queue And Non-Blocking Job Creation

Goal: make `POST /jobs` enqueue work and return quickly while background processing updates job state.

Primary files:

- `backend/filmpipe/api/app.py`
- `backend/filmpipe/application/jobs.py`
- possible new file: `backend/filmpipe/application/queue.py`
- `backend/filmpipe/domain/models.py`
- `tests/test_api.py`
- `tests/test_jobs.py`

Tasks:

- Add in-process background queue using standard library only, preferably `ThreadPoolExecutor(max_workers=1)` or a simple worker thread.
- Persist uploaded files before enqueueing.
- Create initial job with stable ids and image placeholders if useful for progress UI.
- Save job before enqueueing.
- Update job status before/after each image.
- Persist manifest after every meaningful state transition.
- Return `201` immediately from `POST /jobs`.
- Keep current sync `JobService.process(...)` usable for tests/direct callers or add a clear lower-level worker method.
- Fix `ProcessingJob.recompute_status()` for mixed running/completed states.

Acceptance criteria:

- A slow fake processor test proves `POST /jobs` returns before processing finishes.
- Polling `GET /jobs/{id}` observes `pending/running` and later terminal status.
- `GET /jobs` remains responsive during a running job.
- Image-level progress appears as images complete.
- Exceptions in worker become job/image errors, not unhandled thread crashes.
- Existing API validation tests still pass.

Notes:

- Single worker is preferred for MVP because restoration/creative can be GPU/VRAM heavy.
- Do not introduce cancellation unless explicitly scoped later.
- If using FastAPI `BackgroundTasks`, ensure it truly does not block response and still survives enough for local MVP. A dedicated executor is easier to test.

### Agent 3 - Frontend API Client, Types, And Polling State

Goal: prepare frontend data layer for active jobs plus history.

Primary files:

- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`
- optional new files under `frontend/src/`

Tasks:

- Add `listJobs(): Promise<{ jobs: Job[] }>` or normalized equivalent.
- Add any optional API fields introduced by Agents 1/2, keeping compatibility with current shape.
- Build reusable polling helper/hook:
  - poll active job while `pending/running`;
  - poll history while any listed job is `pending/running`;
  - avoid overlapping requests if prior poll is still in flight;
  - stop polling terminal jobs.
- Keep `apiUrl()` as central URL mapper.
- Keep frontend unaware of local filesystem paths.

Acceptance criteria:

- TypeScript build passes.
- Current processing screen still works.
- After async backend change, submit returns quickly and UI continues polling.
- Poll errors are surfaced without destroying the last known job state.

Notes:

- No need for React Router unless the agent chooses it and keeps the UI simple. A local `view` state is enough for MVP.
- Reuse existing status/artifact types.

### Agent 4 - Frontend Gallery/History UX And Lightbox

Goal: add the user-facing history gallery and fullscreen preview.

Primary files:

- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- optional new components under `frontend/src/`

Tasks:

- Add top-level navigation between:
  - Processing Console;
  - History/Gallery.
- Gallery should show previous jobs from `GET /jobs`.
- Job cards should include:
  - thumbnail from best available artifact preview;
  - job short id;
  - status;
  - created/updated time;
  - image count;
  - options: input processing, restoration, final processing;
  - generated artifact chips;
  - batch ZIP action when available.
- Selecting a job opens job details using the same artifact/detail rendering concepts as current result pane.
- Clicking any preview opens fullscreen modal/lightbox using `preview_url`.
- Lightbox should include:
  - large image;
  - close button;
  - Escape close;
  - backdrop click close;
  - artifact label/filename;
  - download action using `download_url`.
- Use existing visual language: quiet tool UI, restrained colors, 8px radii, lucide icons.
- Make responsive layouts work on desktop and mobile.

Acceptance criteria:

- User can open Gallery and inspect previous jobs.
- User can open any available preview fullscreen without downloading master artifact.
- User can still download individual artifacts and batch ZIP.
- UI handles preview failure with existing fallback pattern.
- No text overlap on narrow screens.
- `npm run build` passes.

Notes:

- For thumbnail selection, prefer `creative`, then `restored`, then `positive`, then `original`.
- Use `preview_url` for viewing and `download_url` for downloading.
- Avoid adding marketing/landing-page UI. First screen remains a working tool.

### Agent 5 - Integration QA, Docs, And Handoff

Goal: verify the whole feature across backend/frontend and document the new contract.

Primary files:

- `README.md`
- `frontend/README.md`
- `docs/AGENT_HANDOFF.md`
- optional new final handoff doc in `docs/`

Tasks:

- Update API docs:
  - async `POST /jobs`;
  - polling semantics;
  - persistent history;
  - manifest location;
  - legacy reconstruction caveats;
  - optional preview sizing if implemented.
- Update frontend docs:
  - History/Gallery;
  - lightbox preview;
  - polling behavior.
- Run backend tests:

```bash
.venv/bin/python -m pytest
```

- Run frontend build:

```bash
cd frontend
npm run build
```

- Do a local smoke if dependencies are available:
  - start backend;
  - start frontend;
  - submit a lightweight job;
  - confirm `POST /jobs` returns before terminal completion for a slow fake/dev path if available;
  - confirm `GET /jobs` shows it;
  - restart backend;
  - confirm history remains visible;
  - open gallery preview/lightbox.

Acceptance criteria:

- Docs reflect actual implemented behavior.
- Verification commands and results are recorded in final handoff.
- Any skipped checks are explicitly explained.
- No real heavy Creative inference is launched during routine QA unless explicitly approved.

## Suggested Work Order

1. Agent 1 and Agent 2 coordinate the registry/repository interface first.
2. Agent 1 implements persistence and legacy loading.
3. Agent 2 implements queue on top of that interface.
4. Agent 3 updates frontend client/types/polling against the new backend contract.
5. Agent 4 builds Gallery and Lightbox UI.
6. Agent 5 runs integration QA and docs pass.

Parallelization notes:

- Agent 1 and Agent 2 can work in parallel only after agreeing on a small registry interface.
- Agent 3 can start with `listJobs()` and polling helpers using the existing full job shape.
- Agent 4 can start component/layout work with mocked `Job[]`, then wire to Agent 3 API.
- Agent 5 should start after implementation stabilizes.

## Recommended Backend Interfaces

Keep the interface intentionally small:

```python
class JobRegistry:
    def save(self, job: ProcessingJob) -> ProcessingJob: ...
    def get(self, job_id: str) -> ProcessingJob | None: ...
    def list(self) -> list[ProcessingJob]: ...
```

If queue needs atomic mutations, add one focused helper:

```python
def update(self, job_id: str, mutate: Callable[[ProcessingJob], None]) -> ProcessingJob | None:
    ...
```

This lets worker code update and persist under one lock.

## Recommended Frontend Structure

Small refactor is acceptable:

```text
frontend/src/App.tsx
frontend/src/api.ts
frontend/src/types.ts
frontend/src/styles.css
frontend/src/components/Lightbox.tsx
frontend/src/components/GalleryView.tsx
frontend/src/components/JobDetails.tsx
```

Do not over-abstract. Extract components only where it reduces duplication between current results panel and history details.

## Edge Cases To Cover

- Job has no generated artifacts, only original.
- Original preview fails because file is corrupt or unsupported.
- Legacy job has no manifest.
- Legacy job has missing artifact file.
- Job is running while user switches to Gallery.
- Backend restarts after completed job.
- Backend restarts while a job was running. For MVP, acceptable behavior:
  - mark stale running jobs as `failed` or `partial_success` with clear recoverable/system error;
  - do not silently show them as forever running.
- Batch ZIP endpoint returns `404` if no generated artifacts exist.
- Creative failure leaves previous artifacts visible.
- Many jobs in `data/jobs` should not make the frontend unusable.

## Definition Of Done

The feature is done when:

- `POST /jobs` no longer blocks on full processing;
- frontend polling shows live job state;
- `GET /jobs` returns current and previous jobs from persistent storage;
- existing `data/jobs` folders are visible in Gallery;
- new jobs survive backend restart;
- users can open artifact previews fullscreen from both active job results and Gallery;
- downloads still return original artifact bytes/formats;
- backend tests pass;
- frontend TypeScript build passes;
- README and handoff docs match the implemented behavior.

