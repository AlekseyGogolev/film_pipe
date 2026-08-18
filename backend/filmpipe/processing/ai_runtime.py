from __future__ import annotations

import gc
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def ai_runtime_lifecycle(
    runtime: Any,
    *,
    logger: logging.Logger | logging.LoggerAdapter[Any],
    processor: str,
    stage: str,
    provider: str | None = None,
    model: str | None = None,
) -> Iterator[Any]:
    provider_label = provider or runtime_label(runtime, stage)
    model_label = model or runtime_model_id(runtime) or "-"

    logger.info(
        "ai_runtime_started provider=%s stage=%s model=%s",
        provider_label,
        stage,
        model_label,
        extra={"processor": processor},
    )
    try:
        yield runtime
    except BaseException:
        raise
    else:
        logger.info(
            "ai_runtime_finished provider=%s stage=%s model=%s",
            provider_label,
            stage,
            model_label,
            extra={"processor": processor},
        )
    finally:
        logger.info(
            "ai_runtime_cleanup_started provider=%s stage=%s model=%s",
            provider_label,
            stage,
            model_label,
            extra={"processor": processor},
        )
        try:
            close_runtime(runtime)
        except Exception:
            logger.exception(
                "ai_runtime_cleanup_failed provider=%s stage=%s model=%s",
                provider_label,
                stage,
                model_label,
                extra={"processor": processor},
            )
        else:
            logger.info(
                "ai_runtime_cleanup_finished provider=%s stage=%s model=%s",
                provider_label,
                stage,
                model_label,
                extra={"processor": processor},
            )


def close_runtime(runtime: Any) -> bool:
    close = getattr(runtime, "close", None)
    if callable(close):
        close()
        return True

    release = getattr(runtime, "release", None)
    if callable(release):
        release()
        return True

    return False


def runtime_label(runtime: Any, default: str) -> str:
    name = getattr(runtime, "name", None)
    if name is None:
        return default
    return str(name)


def runtime_model_id(runtime: Any) -> str | None:
    model_id = getattr(runtime, "model_id", None)
    if model_id is None:
        return None
    return str(model_id)


def release_torch_cuda_cache(torch: Any | None) -> None:
    gc.collect()
    if torch is None:
        return

    cuda = getattr(torch, "cuda", None)
    if cuda is None:
        return

    is_available = getattr(cuda, "is_available", None)
    if not callable(is_available) or not is_available():
        return

    empty_cache = getattr(cuda, "empty_cache", None)
    if callable(empty_cache):
        empty_cache()


def torch_inference_context(torch: Any) -> Any:
    inference_mode = getattr(torch, "inference_mode", None)
    if callable(inference_mode):
        return inference_mode()
    return torch.no_grad()
