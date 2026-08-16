# FilmPipe Frontend

Minimal desktop-oriented React/Vite UI for the FilmPipe MVP.

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

- `POST /jobs` with multipart `mode=bw`, `polarity=negative|positive`, and one or more `files`.
- `polarity=positive` skips negative conversion and tone normalization for already-positive inputs.
- `restoration=off|telea|lama`; the UI defaults to `off` unless the user explicitly chooses restoration.
- `GET /jobs/{job_id}` for polling if a job is pending/running.
- `preview_url` and `download_url` from artifact responses.
- `download_url` from the job response for batch ZIP export.

`Colorize` and `Creative` are visible as disabled future modes. The creative prompt field exists but is disabled while the backend supports only `bw`.

When `restored` exists, the UI previews and downloads it the same way as `original` and `positive`. If AI restoration fails, the UI keeps showing `positive` with the existing recoverable error state.

## Known UI Limitation

Browsers generally do not render TIFF in `<img>`. The UI attempts `preview_url` and shows a fallback with download actions when the browser cannot display `image/tiff`.
