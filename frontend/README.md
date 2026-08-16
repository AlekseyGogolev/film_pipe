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

- `POST /jobs` with multipart `input_processing=already_positive|bw_negative`, `restoration=off|telea|lama`, and one or more `files`.
- `already_positive` decodes the uploaded source as the working positive without inversion or B&W-negative preparation.
- `bw_negative` runs the B&W negative-to-positive conversion path.
- `restoration=off|telea|lama`; the UI defaults to `off` unless the user explicitly chooses restoration.
- `GET /jobs/{job_id}` for polling if a job is pending/running.
- `preview_url` and `download_url` from artifact responses.
- `download_url` from the job response for batch ZIP export.

The active controls are file selection, process action, Input Processing, and Restoration. Future colorization/creative controls are not shown in the current runtime UI.

The UI renders only public artifacts returned by the API, ordered as `original`, `positive`, `restored`. Already-positive jobs therefore show `Original` and, when restoration runs successfully, `Restored`; they do not show an empty or synthetic `Positive` card.

## Preview / Download

Artifact downloads keep the stored format and bit depth, such as 16-bit TIFF for generated positives. Artifact previews use the API-provided `preview_url`, which returns browser-friendly PNG bytes.
