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

- `POST /jobs` with multipart `mode=bw` and one or more `files`.
- `GET /jobs/{job_id}` for polling if a job is pending/running.
- `preview_url` and `download_url` from artifact responses.
- `download_url` from the job response for batch ZIP export.

`Colorize` and `Creative` are visible as disabled future modes. The creative prompt field exists but is disabled while the backend supports only `bw`.

## Known UI Limitation

Browsers generally do not render TIFF in `<img>`. The UI attempts `preview_url` and shows a fallback with download actions when the browser cannot display `image/tiff`.
