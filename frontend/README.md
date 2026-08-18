# FilmPipe Frontend

React/Vite UI for the FilmPipe MVP processing console, persistent history
gallery, and fullscreen artifact previews.

## Stack

- React 18
- TypeScript
- Vite
- lucide-react icons

## Run

Start the backend from the repository root:

```bash
uvicorn filmpipe.api.app:create_app --factory --reload
```

Install and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173/
```

The dev server proxies `/api/*` to `http://127.0.0.1:8000/*`.

## Build

```bash
npm run build
```

## API Use

The UI talks only to the FilmPipe HTTP API:

- `POST /jobs` with multipart `input_processing=already_positive|bw_negative`, `restoration=off|telea|lama`, `final_processing=standard|creative`, and one or more `files`.
- `creative_prompt` is sent only when `final_processing=creative`; the Process button is disabled until the prompt is non-empty.
- `POST /jobs` returns quickly with a `pending` job after the backend persists uploads and enqueues background processing.
- `already_positive` decodes the uploaded source as the working positive without inversion or B&W-negative preparation.
- `bw_negative` runs the B&W negative-to-positive conversion path.
- `restoration=off|telea|lama`; the UI defaults to `off` unless the user explicitly chooses restoration.
- `final_processing=standard|creative`; the UI defaults to `standard`, which does not start the Creative runtime.
- `GET /jobs/{job_id}` for polling the active job while it is `pending` or `running`.
- `GET /jobs` for the History/Gallery view and for polling history while any listed job is active.
- `preview_url` and `download_url` from artifact responses.
- `download_url` from the job response for batch ZIP export.

The active controls are file selection, clear selection, process action, Input Processing, Restoration, Final Processing, and top-level Console/Gallery navigation. Creative prompt input appears only for Creative jobs.

The UI renders only public artifacts returned by the API, ordered as `original`, `positive`, `restored`, `creative`. Already-positive jobs therefore show `Original` and, when restoration runs successfully, `Restored`; they do not show an empty or synthetic `Positive` card. Recoverable Creative failures still leave earlier artifacts visible.

## Polling

The reusable polling hook avoids overlapping requests. The active console job
polls every 1.5 seconds until it reaches a terminal status. The history list
loads on startup, refreshes when the Gallery tab is opened, and polls every 3.5
seconds while history is not loaded or while any known job is `pending` or
`running`. Poll failures are surfaced in the banner without clearing the last
known job or history state.

## History / Gallery

The Gallery tab is backed only by `GET /jobs`; it does not inspect local
filesystem paths. It displays persisted jobs after backend restart and legacy
jobs reconstructed by the API from old `data/jobs` folders. Legacy jobs are
shown with a `Legacy` chip when the API returns `legacy: true`.

Job cards show the best available thumbnail, short job id, status, image count,
created/updated timestamps, processing option chips, generated artifact chips,
and a batch ZIP action when the job has generated artifacts. Thumbnail
selection prefers `creative`, then `restored`, then `positive`, then
`original`.

Selecting a job opens the same image/artifact detail view used by the console.
Clicking any preview opens the fullscreen lightbox. The lightbox supports close
button, Escape, backdrop click, preview fallback, artifact filename, source
image filename, and a download action.

## Preview / Download

Artifact downloads keep the stored format and bit depth, such as 16-bit TIFF for generated positives. Artifact previews use the API-provided `preview_url`, which returns browser-friendly PNG bytes.

Gallery thumbnails request `preview_url?max_edge=512`, and fullscreen previews
request `preview_url?max_edge=1920`. The current backend ignores the optional
query parameter, so these URLs remain compatible but are not resized server-side
yet.
