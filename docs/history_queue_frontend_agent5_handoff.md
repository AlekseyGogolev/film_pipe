# FilmPipe History/Queue/Frontend - Agent 5 QA Handoff

Date: 2026-08-18.

## Scope

Agent 5 covered documentation and integration QA for the history, async queue,
persistent manifest, Gallery, and Lightbox work described in
`docs/history_queue_frontend_agent_plan.md`.

No production backend/frontend code was changed in this pass. Documentation was
updated in:

- `README.md`
- `frontend/README.md`
- `docs/AGENT_HANDOFF.md`

## Verified Behavior

- `POST /jobs` is documented as asynchronous: uploads are staged under
  `data/jobs/{job_id}/inputs/{index}/`, a `pending` manifest is written, and a
  local background queue processes work after HTTP `201`.
- Job polling semantics are documented for `pending`, `running`, `success`,
  `failed`, and terminal `partial_success`.
- Persistent history is documented around `data/jobs/{job_id}/job.json`,
  relative artifact paths, manifest reload, newest-first `GET /jobs`, and
  legacy job reconstruction.
- Frontend docs now describe Console/Gallery navigation, active/history
  polling, best-available thumbnails, batch ZIP actions, preview fallback, and
  fullscreen Lightbox preview/download behavior.
- Preview sizing is documented as currently frontend-compatible but not
  implemented server-side: `?max_edge=512` and `?max_edge=1920` are ignored by
  the backend today.

## Verification Commands

Backend test suite:

```bash
.venv/bin/python -m pytest
```

Result:

```text
88 passed in 0.88s
```

Frontend build command requested by the plan:

```bash
cd frontend
npm run build
```

Result:

```text
/bin/bash: line 1: npm: command not found
```

Fallback TypeScript check using installed local dependency:

```bash
cd frontend
node_modules/.bin/tsc --noEmit
```

Result: passed.

Fallback Vite build using installed local dependency:

```bash
cd frontend
node_modules/.bin/vite build
```

Result: failed because the available Node runtime is `v18.19.1`, while the
frontend requires Node 20+. The error came from `rolldown` importing
`node:util.styleText`, which is unavailable in Node 18.

## API Smoke

A safe API smoke was run against a temporary jobs root using the lightweight
standard test pipeline. It did not touch real `data/jobs` and did not run
Creative or AI restoration.

Covered:

- create `bw_negative/off/standard` job;
- verify immediate HTTP `201` response has `pending` status;
- poll until `success`;
- verify `job.json` exists;
- verify positive artifact preview with `?max_edge=512`;
- verify positive artifact download;
- create a new app instance pointed at the same temp jobs root;
- verify `GET /jobs` and `GET /jobs/{job_id}` still expose the completed job.

Result:

```text
api_smoke_ok
```

## Skipped Checks

- `npm run build` could not run because `npm` is not installed in this
  environment.
- Full Vite build and frontend browser smoke were skipped because only Node
  `v18.19.1` is available; this project documents Node.js 20+ as a requirement.
- The smoke did not launch real AI restoration or Creative inference, matching
  the non-negotiable QA constraint.

## Residual Risks

- The queue is in-process and not durable. A backend process restart during an
  active job is not resumed.
- Current manifest loading does not mark stale `pending`/`running` manifests as
  failed on startup, so an interrupted active job may remain visibly active.
- Preview `max_edge` query parameters are accepted only by being ignored; no
  server-side resizing is implemented yet.
