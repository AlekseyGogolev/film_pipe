from __future__ import annotations

from io import BytesIO
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from filmpipe.application.jobs import InMemoryJobRegistry, JobService
from filmpipe.domain.models import (
    Artifact,
    ArtifactType,
    ImageProcessingResult,
    InputProcessingMode,
    ProcessingError,
    ProcessingJob,
    ProcessingOptions,
    RestorationMode,
)
from filmpipe.processing.preview import PREVIEW_MIME_TYPE, PreviewRenderError, render_preview_png

try:
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import Response
except ImportError:  # pragma: no cover - exercised only without installed deps
    FastAPI = None  # type: ignore[assignment]
    File = None  # type: ignore[assignment]
    Form = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    UploadFile = None  # type: ignore[assignment]
    Response = None  # type: ignore[assignment]


def create_app(
    *,
    job_service: JobService | None = None,
    job_registry: InMemoryJobRegistry | None = None,
):
    if FastAPI is None:
        raise RuntimeError(
            "FastAPI is not installed. Install project dependencies with "
            "`python -m pip install -e .` before starting the local API."
        )

    service = job_service or JobService()
    registry = job_registry or InMemoryJobRegistry()
    app = FastAPI(
        title="FilmPipe",
        version="0.1.0",
        description="Local FilmPipe API for B&W film scan processing jobs.",
    )
    app.state.job_service = service
    app.state.job_registry = registry

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/jobs")
    async def list_jobs() -> dict[str, list[dict[str, object]]]:
        return {"jobs": [_job_response(job) for job in registry.list()]}

    @app.post("/jobs", status_code=201)
    async def create_job(
        files: list[UploadFile] = File(...),
        input_processing: str | None = Form(None),
        restoration: str = Form(RestorationMode.OFF.value),
        mode: str | None = Form(None),
        polarity: str | None = Form(None),
    ) -> dict[str, object]:
        if not files:
            raise HTTPException(status_code=400, detail="Добавьте хотя бы один файл.")

        input_processing_mode = _resolve_input_processing(
            input_processing=input_processing,
            legacy_mode=mode,
            legacy_polarity=polarity,
        )
        restoration_mode = _parse_restoration_mode(restoration)
        options = ProcessingOptions(
            input_processing=input_processing_mode,
            restoration=restoration_mode,
        )
        with TemporaryDirectory(prefix="filmpipe-upload-") as temporary_dir:
            upload_paths: list[Path] = []
            temporary_root = Path(temporary_dir)
            for index, upload in enumerate(files):
                filename = _safe_upload_filename(upload.filename, index)
                upload_dir = temporary_root / str(index)
                upload_dir.mkdir(parents=True, exist_ok=True)
                upload_path = upload_dir / filename
                upload_path.write_bytes(await upload.read())
                await upload.close()
                upload_paths.append(upload_path)

            job = service.process(upload_paths, options=options)

        registry.save(job)
        return _job_response(job)

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, object]:
        return _job_response(_get_job(registry, job_id))

    @app.get("/jobs/{job_id}/images/{image_id}")
    async def get_image(job_id: str, image_id: str) -> dict[str, object]:
        job = _get_job(registry, job_id)
        image = _get_image(job, image_id)
        return _image_response(job.id, image)

    @app.get("/jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/preview")
    async def preview_artifact(job_id: str, image_id: str, artifact_type: ArtifactType):
        artifact = _get_artifact(registry, job_id, image_id, artifact_type)
        try:
            preview = render_preview_png(artifact.path)
        except PreviewRenderError as exc:
            raise HTTPException(
                status_code=422,
                detail="Предпросмотр этого артефакта недоступен.",
            ) from exc
        return Response(content=preview, media_type=PREVIEW_MIME_TYPE)

    @app.get("/jobs/{job_id}/images/{image_id}/artifacts/{artifact_type}/download")
    async def download_artifact(job_id: str, image_id: str, artifact_type: ArtifactType):
        artifact = _get_artifact(registry, job_id, image_id, artifact_type)
        headers = {"Content-Disposition": f'attachment; filename="{artifact.filename}"'}
        return Response(
            content=artifact.path.read_bytes(),
            media_type=artifact.mime_type,
            headers=headers,
        )

    @app.get("/jobs/{job_id}/download")
    async def download_job(job_id: str):
        job = _get_job(registry, job_id)
        archive = BytesIO()
        artifact_count = 0
        with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
            for image in job.results:
                image_stem = _safe_segment(Path(image.filename).stem or image.image_id)
                image_dir = f"{image_stem}_{image.image_id[:8]}"
                for artifact in image.artifacts:
                    if artifact.type == ArtifactType.ORIGINAL:
                        continue
                    if not artifact.path.is_file():
                        continue
                    archive_name = f"{image_dir}/{artifact.type.value}/{artifact.filename}"
                    zip_file.write(artifact.path, archive_name)
                    artifact_count += 1

        if artifact_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Для этого job пока нет готовых артефактов для скачивания.",
            )

        archive.seek(0)
        headers = {
            "Content-Disposition": f'attachment; filename="{job.id}_artifacts.zip"',
        }
        return Response(
            content=archive.getvalue(),
            media_type="application/zip",
            headers=headers,
        )

    return app


app = None


def _job_response(job: ProcessingJob) -> dict[str, object]:
    return {
        "id": job.id,
        "status": job.status.value,
        "input_processing": job.options.input_processing.value,
        "restoration": job.options.restoration.value,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "images": [_image_response(job.id, result) for result in job.results],
        "errors": [_error_response(error) for error in job.errors],
        "download_url": f"/jobs/{job.id}/download",
    }


def _image_response(job_id: str, result: ImageProcessingResult) -> dict[str, object]:
    return {
        "id": result.image_id,
        "filename": result.filename,
        "status": result.status.value,
        "artifacts": [_artifact_response(job_id, result.image_id, artifact) for artifact in result.artifacts],
        "errors": [_error_response(error) for error in result.errors],
    }


def _artifact_response(job_id: str, image_id: str, artifact: Artifact) -> dict[str, str]:
    base_url = f"/jobs/{job_id}/images/{image_id}/artifacts/{artifact.type.value}"
    return {
        "type": artifact.type.value,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "preview_url": f"{base_url}/preview",
        "download_url": f"{base_url}/download",
    }


def _error_response(error: ProcessingError) -> dict[str, object]:
    return {
        "stage": error.stage,
        "message": error.user_message,
        "recoverable": error.recoverable,
        "exception_type": error.exception_type,
    }


def _resolve_input_processing(
    *,
    input_processing: str | None,
    legacy_mode: str | None,
    legacy_polarity: str | None,
) -> InputProcessingMode:
    if input_processing is not None:
        return _parse_input_processing(input_processing)

    if legacy_mode is None and legacy_polarity is None:
        return InputProcessingMode.BW_NEGATIVE

    mode = (legacy_mode or "bw").strip().lower()
    if mode == "off":
        return InputProcessingMode.ALREADY_POSITIVE
    if mode != "bw":
        raise HTTPException(
            status_code=400,
            detail=(
                "Legacy mode is no longer part of the API. "
                "Use input_processing=already_positive or input_processing=bw_negative."
            ),
        )

    polarity = (legacy_polarity or "negative").strip().lower()
    if polarity == "negative":
        return InputProcessingMode.BW_NEGATIVE
    if polarity == "positive":
        return InputProcessingMode.ALREADY_POSITIVE

    raise HTTPException(
        status_code=400,
        detail="Legacy polarity must be one of: negative, positive.",
    )


def _parse_input_processing(value: str) -> InputProcessingMode:
    normalized = value.strip().lower()
    try:
        return InputProcessingMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in InputProcessingMode)
        raise HTTPException(
            status_code=400,
            detail=f"Input processing must be one of: {allowed}.",
        ) from exc


def _parse_restoration_mode(value: str) -> RestorationMode:
    normalized = value.strip().lower()
    try:
        return RestorationMode(normalized)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in RestorationMode)
        raise HTTPException(
            status_code=400,
            detail=f"Restoration must be one of: {allowed}.",
        ) from exc


def _get_job(registry: InMemoryJobRegistry, job_id: str) -> ProcessingJob:
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job не найден.")
    return job


def _get_image(job: ProcessingJob, image_id: str) -> ImageProcessingResult:
    image = next((result for result in job.results if result.image_id == image_id), None)
    if image is None:
        raise HTTPException(status_code=404, detail="Изображение не найдено.")
    return image


def _get_artifact(
    registry: InMemoryJobRegistry,
    job_id: str,
    image_id: str,
    artifact_type: ArtifactType,
) -> Artifact:
    job = _get_job(registry, job_id)
    image = _get_image(job, image_id)
    artifact = image.artifact(artifact_type)
    if artifact is None or not artifact.path.is_file():
        raise HTTPException(status_code=404, detail="Артефакт не найден.")
    return artifact


def _safe_upload_filename(filename: str | None, index: int) -> str:
    candidate = filename or f"upload-{index}"
    name = Path(candidate).name
    stem = _safe_segment(Path(name).stem)
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", Path(name).suffix)
    return f"{stem}{suffix}" if suffix else stem


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "item"
