from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import cv2
import numpy as np

from filmpipe.domain.models import ArtifactType, ProcessingError
from filmpipe.domain.processor import ProcessingContext, ProcessorResult
from filmpipe.processing.image import FilmImage, normalized_grayscale_array

SUPPORTED_IMAGE_SUFFIXES = frozenset({".tif", ".tiff", ".png", ".jpg", ".jpeg"})
OUTPUT_SUFFIX = ".tiff"
OUTPUT_MIME_TYPE = "image/tiff"


@dataclass
class DecodePositiveImageProcessor:
    name: str = "decode_positive"
    optional: bool = False
    supported_suffixes: frozenset[str] = SUPPORTED_IMAGE_SUFFIXES

    def process(self, image: Path | str, context: ProcessingContext) -> ProcessorResult:
        source_path = Path(image)
        decoded_result = _decode_source_image(
            source_path,
            context,
            stage=self.name,
            supported_suffixes=self.supported_suffixes,
        )
        if isinstance(decoded_result, ProcessorResult):
            return decoded_result
        decoded = decoded_result

        try:
            bit_depth, channels = _decoded_image_shape(decoded)
        except ValueError as exc:
            return _failure(
                self.name,
                f"Не удалось подготовить позитивное изображение {context.filename}.",
                str(exc),
            )

        image_data = FilmImage(
            data=decoded.copy(),
            bit_depth=bit_depth,
            source_path=source_path,
            filename=context.filename,
            metadata=_decoded_metadata(decoded, bit_depth, channels),
        )
        context.metadata.update(image_data.metadata)
        context.working_positive = image_data.data
        return ProcessorResult.success(image=image_data)


@dataclass
class DecodeBWImageProcessor:
    name: str = "decode_bw"
    optional: bool = False
    supported_suffixes: frozenset[str] = SUPPORTED_IMAGE_SUFFIXES

    def process(self, image: Path | str, context: ProcessingContext) -> ProcessorResult:
        source_path = Path(image)
        decoded_result = _decode_source_image(
            source_path,
            context,
            stage=self.name,
            supported_suffixes=self.supported_suffixes,
        )
        if isinstance(decoded_result, ProcessorResult):
            return decoded_result
        decoded = decoded_result

        try:
            grayscale, bit_depth, channels = _decoded_to_grayscale(decoded)
        except ValueError as exc:
            return _failure(
                self.name,
                f"Не удалось подготовить B&W изображение {context.filename}.",
                str(exc),
            )

        image_data = FilmImage(
            data=grayscale,
            bit_depth=bit_depth,
            source_path=source_path,
            filename=context.filename,
            metadata=_decoded_metadata(decoded, bit_depth, channels),
        )
        context.metadata.update(image_data.metadata)
        return ProcessorResult.success(image=image_data)


@dataclass
class NegativeConverterProcessor:
    name: str = "negative_conversion"
    optional: bool = False

    def process(self, image: FilmImage, context: ProcessingContext) -> ProcessorResult:
        if not isinstance(image, FilmImage):
            return _failure(
                self.name,
                f"Не удалось преобразовать негатив {context.filename}.",
                f"Expected FilmImage, got {type(image).__name__}",
            )

        try:
            converted = 1.0 - normalized_grayscale_array(image.data)
        except ValueError as exc:
            return _failure(
                self.name,
                f"Данные изображения {context.filename} некорректны.",
                str(exc),
            )

        return ProcessorResult.success(
            image=image.with_data(
                converted,
                metadata={"negative_converted": True},
            )
        )


@dataclass
class ToneNormalizerProcessor:
    name: str = "tone_normalization"
    optional: bool = False
    low_percentile: float = 0.5
    high_percentile: float = 99.5
    min_tonal_range: float = 1e-4

    def __post_init__(self) -> None:
        if not 0.0 <= self.low_percentile < self.high_percentile <= 100.0:
            raise ValueError("Percentiles must satisfy 0 <= low < high <= 100")
        if self.min_tonal_range <= 0:
            raise ValueError("min_tonal_range must be positive")

    def process(self, image: FilmImage, context: ProcessingContext) -> ProcessorResult:
        if not isinstance(image, FilmImage):
            return _failure(
                self.name,
                f"Не удалось нормализовать изображение {context.filename}.",
                f"Expected FilmImage, got {type(image).__name__}",
            )

        try:
            data = normalized_grayscale_array(image.data)
        except ValueError as exc:
            return _failure(
                self.name,
                f"Данные изображения {context.filename} некорректны.",
                str(exc),
            )

        low, high = np.percentile(data, [self.low_percentile, self.high_percentile])
        if not np.isfinite(low) or not np.isfinite(high):
            return _failure(
                self.name,
                f"Не удалось вычислить тональный диапазон {context.filename}.",
                f"Invalid percentile values: low={low}, high={high}",
            )

        tonal_range = float(high - low)
        if tonal_range < self.min_tonal_range:
            normalized = data
            metadata: dict[str, Any] = {
                "tone_normalized": False,
                "normalization_reason": "tonal_range_too_small",
                "black_point": float(low),
                "white_point": float(high),
            }
        else:
            normalized = np.clip((data - low) / tonal_range, 0.0, 1.0).astype(
                np.float32,
                copy=False,
            )
            metadata = {
                "tone_normalized": True,
                "black_point": float(low),
                "white_point": float(high),
            }

        context.metadata.update(metadata)
        return ProcessorResult.success(image=image.with_data(normalized, metadata=metadata))


@dataclass
class PositiveArtifactWriterProcessor:
    name: str = "positive_artifact_writer"
    optional: bool = False
    output_suffix: str = OUTPUT_SUFFIX

    def process(self, image: FilmImage, context: ProcessingContext) -> ProcessorResult:
        if not isinstance(image, FilmImage):
            return _failure(
                self.name,
                f"Не удалось сохранить позитив {context.filename}.",
                f"Expected FilmImage, got {type(image).__name__}",
            )

        try:
            output_image = _to_uint16_output(image.data)
            context.working_positive = output_image
            ok, encoded = cv2.imencode(self.output_suffix, output_image)
            if not ok:
                return _failure(
                    self.name,
                    f"Не удалось записать позитив {context.filename}.",
                    f"OpenCV failed to encode {self.output_suffix}",
                )

            with TemporaryDirectory(prefix="filmpipe-positive-") as temporary_dir:
                source_stem = Path(context.filename).stem or "image"
                temporary_path = Path(temporary_dir) / f"{source_stem}{self.output_suffix}"
                temporary_path.write_bytes(encoded.tobytes())
                artifact = context.artifact_store.save_artifact(
                    context.job_id,
                    context.image_id,
                    ArtifactType.POSITIVE,
                    temporary_path,
                )
        except Exception as exc:
            return ProcessorResult.failure(
                ProcessingError.from_exception(
                    stage=self.name,
                    user_message=f"Не удалось сохранить позитив {context.filename}.",
                    exc=exc,
                    recoverable=False,
                )
            )

        return ProcessorResult.success(image=image, artifacts=[artifact])


def _decode_source_image(
    source_path: Path,
    context: ProcessingContext,
    *,
    stage: str,
    supported_suffixes: frozenset[str],
) -> np.ndarray | ProcessorResult:
    suffix = source_path.suffix.lower()
    if suffix not in supported_suffixes:
        return _failure(
            stage,
            f"Формат файла {context.filename} не поддерживается.",
            f"Unsupported suffix: {suffix or '<none>'}",
        )

    try:
        encoded = np.frombuffer(source_path.read_bytes(), dtype=np.uint8)
    except OSError as exc:
        return ProcessorResult.failure(
            ProcessingError.from_exception(
                stage=stage,
                user_message=f"Не удалось прочитать файл {context.filename}.",
                exc=exc,
                recoverable=False,
            )
        )

    if encoded.size == 0:
        return _failure(
            stage,
            f"Файл {context.filename} пустой или повреждён.",
            "Source file has zero bytes",
        )

    decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
    if decoded is None:
        return _failure(
            stage,
            f"Не удалось декодировать изображение {context.filename}.",
            "OpenCV returned None while decoding image bytes",
        )
    return decoded


def _decoded_image_shape(decoded: np.ndarray) -> tuple[int, int]:
    if decoded.ndim < 2:
        raise ValueError(f"Unsupported decoded image dimensions: {decoded.ndim}")
    if decoded.size == 0 or decoded.shape[0] <= 0 or decoded.shape[1] <= 0:
        raise ValueError("Decoded image has invalid dimensions")

    if decoded.dtype == np.uint8:
        bit_depth = 8
    elif decoded.dtype == np.uint16:
        bit_depth = 16
    else:
        raise ValueError(f"Unsupported image dtype: {decoded.dtype}")

    channels = 1
    if decoded.ndim == 3:
        channels = int(decoded.shape[2])
        if channels not in (1, 3, 4):
            raise ValueError(f"Unsupported channel count: {channels}")
    elif decoded.ndim != 2:
        raise ValueError(f"Unsupported decoded image dimensions: {decoded.ndim}")
    return bit_depth, channels


def _decoded_metadata(
    decoded: np.ndarray,
    bit_depth: int,
    channels: int,
) -> dict[str, int | str]:
    return {
        "input_dtype": str(decoded.dtype),
        "input_bit_depth": bit_depth,
        "input_channels": channels,
        "input_width": int(decoded.shape[1]),
        "input_height": int(decoded.shape[0]),
    }


def _decoded_to_grayscale(decoded: np.ndarray) -> tuple[np.ndarray, int, int]:
    bit_depth, channels = _decoded_image_shape(decoded)
    max_value = np.iinfo(np.uint8).max if bit_depth == 8 else np.iinfo(np.uint16).max

    if decoded.ndim == 2:
        grayscale = decoded
    elif decoded.ndim == 3:
        if channels == 1:
            grayscale = decoded[:, :, 0]
        elif channels == 3:
            grayscale = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
        elif channels == 4:
            grayscale = cv2.cvtColor(decoded, cv2.COLOR_BGRA2GRAY)
        else:
            raise ValueError(f"Unsupported channel count: {channels}")
    else:
        raise ValueError(f"Unsupported decoded image dimensions: {decoded.ndim}")

    normalized = grayscale.astype(np.float32) / float(max_value)
    return normalized_grayscale_array(normalized), bit_depth, channels


def _to_uint16_output(data: Any) -> np.ndarray:
    normalized = normalized_grayscale_array(data)
    return np.rint(normalized * np.iinfo(np.uint16).max).astype(np.uint16)


def _failure(
    stage: str,
    user_message: str,
    technical_message: str,
) -> ProcessorResult:
    return ProcessorResult.failure(
        ProcessingError(
            stage=stage,
            user_message=user_message,
            technical_message=technical_message,
            recoverable=False,
        )
    )
