from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_restoration.composite import (  # noqa: E402
    change_metrics,
    composite_restoration,
    diff_visualization,
)
from ai_restoration.config import load_config  # noqa: E402
from ai_restoration.detectors import MicrosoftScratchDetector  # noqa: E402
from ai_restoration.io import (  # noqa: E402
    ImageData,
    copy_positive,
    iter_input_images,
    read_image,
    safe_stem,
    write_image,
)
from ai_restoration.masks import (  # noqa: E402
    load_probability_mask,
    mask_coverage_percent,
    postprocess_mask,
    save_mask_png,
    save_probability_mask,
    validate_probability_mask,
)
from ai_restoration.restorers import LaMaCliRestorer, telea_restore  # noqa: E402


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    apply_cli_overrides(config, args)

    input_paths = iter_input_images(args.input)
    if not input_paths:
        raise FileNotFoundError(f"No supported image files found under {args.input}")

    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    summaries = []
    for input_path in input_paths:
        summaries.append(process_one(input_path, output_root, args.models_root.resolve(), config, args))

    summary_path = output_root / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote summary: {summary_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FilmPipe AI restoration experiment.")
    parser.add_argument("--input", type=Path, required=True, help="Positive image file or directory.")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--models-root", type=Path, default=ROOT / "models")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "default.json")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--dilation", type=int)
    parser.add_argument("--mask-postprocess", choices=("scene_lines", "none"))
    parser.add_argument("--microsoft-input-size", choices=("full_size", "scale_256", "resize_256"))
    parser.add_argument("--tile-size", type=int)
    parser.add_argument("--tile-overlap", type=int)
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda, cuda:0, ...")
    parser.add_argument("--restorers", default=None, help="Comma-separated: telea,lama")
    parser.add_argument("--telea-radius", type=float)
    parser.add_argument("--feather-radius", type=int)
    parser.add_argument("--lama-python", default=None)
    parser.add_argument("--lama-refine", action="store_true")
    parser.add_argument("--probability-mask", type=Path, help="Use an existing .npy probability mask.")
    parser.add_argument("--force-detector", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Fail if a restorer fails.")
    return parser.parse_args()


def apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    detector = config["detector"]
    output = config["output"]
    restorers = config["restorers"]

    if args.threshold is not None:
        detector["threshold"] = args.threshold
    if args.dilation is not None:
        detector["dilation"] = args.dilation
    if args.mask_postprocess is not None:
        detector["mask_postprocess"] = args.mask_postprocess
    if args.microsoft_input_size is not None:
        detector["input_size"] = args.microsoft_input_size
    if args.tile_size is not None:
        detector["tile_size"] = args.tile_size
    if args.tile_overlap is not None:
        detector["tile_overlap"] = args.tile_overlap
    if args.telea_radius is not None:
        restorers["telea"]["radius"] = args.telea_radius
    if args.feather_radius is not None:
        output["feather_radius"] = args.feather_radius
    if args.lama_python is not None:
        restorers["lama"]["python"] = args.lama_python
    if args.lama_refine:
        restorers["lama"]["refine"] = True
    if args.restorers is not None:
        selected = {item.strip().lower() for item in args.restorers.split(",") if item.strip()}
        for name in restorers:
            restorers[name]["enabled"] = name in selected


def process_one(
    input_path: Path,
    output_root: Path,
    models_root: Path,
    config: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    image = read_image(input_path)
    result_dir = output_root / safe_stem(input_path)
    result_dir.mkdir(parents=True, exist_ok=True)

    positive_path = result_dir / f"positive{image.output_suffix}"
    copy_positive(input_path, positive_path)

    metrics: dict[str, Any] = {
        "input": {
            "path": str(input_path),
            "width": image.width,
            "height": image.height,
            "channels": image.channels,
            "dtype": str(image.array.dtype),
            "bit_depth": image.bit_depth,
        },
        "detector": {},
        "restorers": {},
        "outputs": {"positive": str(positive_path)},
    }

    probability, detector_metrics = load_or_run_detector(
        image,
        result_dir,
        models_root,
        config["detector"],
        args,
    )
    metrics["detector"] = detector_metrics

    threshold = float(config["detector"]["threshold"])
    dilation = int(config["detector"]["dilation"])
    binary_mask, restoration_mask, postprocess_metrics, debug_masks = postprocess_mask(
        probability,
        image.array,
        threshold=threshold,
        dilation=dilation,
        mode=str(config["detector"].get("mask_postprocess", "scene_lines")),
    )
    binary_path = result_dir / "binary_mask.png"
    restoration_path = result_dir / "restoration_mask.png"
    save_mask_png(binary_path, binary_mask)
    save_mask_png(restoration_path, restoration_mask)
    metrics["outputs"]["binary_mask"] = str(binary_path)
    metrics["outputs"]["restoration_mask"] = str(restoration_path)
    for debug_name, debug_mask in debug_masks.items():
        debug_path = result_dir / f"{debug_name}.png"
        save_mask_png(debug_path, debug_mask)
        metrics["outputs"][debug_name] = str(debug_path)
    metrics["detector"].update(
        {
            "threshold": threshold,
            "dilation": dilation,
            "mask_postprocess": postprocess_metrics,
            "binary_mask_coverage_percent": mask_coverage_percent(binary_mask),
            "restoration_mask_coverage_percent": mask_coverage_percent(restoration_mask),
        }
    )

    run_restorers(image, restoration_mask, result_dir, config, args, metrics)

    metrics_path = result_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Processed {input_path} -> {result_dir}")
    return {"input": str(input_path), "result_dir": str(result_dir), "metrics": str(metrics_path)}


def load_or_run_detector(
    image: ImageData,
    result_dir: Path,
    models_root: Path,
    detector_config: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[Any, dict[str, Any]]:
    probability_npy = result_dir / "probability_mask.npy"
    probability_png = result_dir / "probability_mask.png"

    if args.probability_mask is not None:
        probability = load_probability_mask(args.probability_mask, image.array.shape[:2])
        save_probability_mask(probability, probability_npy, probability_png)
        return probability, {
            "name": "external_probability_mask",
            "source": str(args.probability_mask),
            "inference_time_sec": 0.0,
            "device": "n/a",
            "probability_reused": True,
            "probability_mask": str(probability_npy),
            "probability_mask_png": str(probability_png),
        }

    if probability_npy.exists() and not args.force_detector:
        probability = load_probability_mask(probability_npy, image.array.shape[:2])
        if not probability_png.exists():
            save_probability_mask(probability, probability_npy, probability_png)
        metadata = cached_detector_metadata(result_dir, detector_config)
        metadata.update(
            {
                "inference_time_sec": 0.0,
                "device": "cached",
                "probability_reused": True,
                "probability_mask": str(probability_npy),
                "probability_mask_png": str(probability_png),
            }
        )
        return probability, metadata

    detector = MicrosoftScratchDetector(
        models_root=models_root,
        device_preference=args.device,
        input_size=str(detector_config["input_size"]),
        tile_size=int(detector_config["tile_size"]),
        tile_overlap=int(detector_config["tile_overlap"]),
    )
    start = time.perf_counter()
    result = detector.detect(image.array)
    elapsed = time.perf_counter() - start
    probability = validate_probability_mask(result.probability, image.array.shape[:2])
    save_probability_mask(probability, probability_npy, probability_png)
    metadata = dict(result.metadata)
    metadata.update(
        {
            "inference_time_sec": elapsed,
            "probability_reused": False,
            "probability_mask": str(probability_npy),
            "probability_mask_png": str(probability_png),
        }
    )
    return probability, metadata


def cached_detector_metadata(result_dir: Path, detector_config: dict[str, Any]) -> dict[str, Any]:
    metrics_path = result_dir / "metrics.json"
    if metrics_path.exists():
        try:
            previous = json.loads(metrics_path.read_text(encoding="utf-8"))
            detector = previous.get("detector", {})
            if isinstance(detector, dict):
                return dict(detector)
        except Exception:
            pass
    return {
        "name": detector_config["name"],
        "input_size": detector_config["input_size"],
        "tile_size": detector_config["tile_size"],
        "tile_overlap": detector_config["tile_overlap"],
    }


def run_restorers(
    image: ImageData,
    restoration_mask: Any,
    result_dir: Path,
    config: dict[str, Any],
    args: argparse.Namespace,
    metrics: dict[str, Any],
) -> None:
    restorers = config["restorers"]
    if restorers["telea"].get("enabled", False):
        _run_one_restorer(
            name="telea",
            candidate_factory=lambda: telea_restore(
                image.array,
                restoration_mask,
                radius=float(restorers["telea"]["radius"]),
            ),
            image=image,
            restoration_mask=restoration_mask,
            result_dir=result_dir,
            feather_radius=int(config["output"]["feather_radius"]),
            metrics=metrics,
            strict=args.strict,
        )

    if restorers["lama"].get("enabled", False):
        lama = LaMaCliRestorer(
            models_root=args.models_root.resolve(),
            python_executable=restorers["lama"].get("python"),
            device_preference=args.device,
            refine=bool(restorers["lama"].get("refine", False)),
        )
        _run_one_restorer(
            name="lama",
            candidate_factory=lambda: lama.restore(image.array, restoration_mask),
            image=image,
            restoration_mask=restoration_mask,
            result_dir=result_dir,
            feather_radius=int(config["output"]["feather_radius"]),
            metrics=metrics,
            strict=args.strict,
        )


def _run_one_restorer(
    *,
    name: str,
    candidate_factory: Any,
    image: ImageData,
    restoration_mask: Any,
    result_dir: Path,
    feather_radius: int,
    metrics: dict[str, Any],
    strict: bool,
) -> None:
    restorer_dir = result_dir / name
    restorer_dir.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    try:
        candidate = candidate_factory()
        restored = composite_restoration(
            image.array,
            candidate.image,
            restoration_mask,
            feather_radius=feather_radius,
        )
        elapsed = time.perf_counter() - start
        restored_path = restorer_dir / f"restored{image.output_suffix}"
        diff_path = restorer_dir / "diff.png"
        write_image(restored_path, restored)
        write_image(diff_path, diff_visualization(image.array, restored))
        changes = change_metrics(image.array, restored, restoration_mask)

        metrics["restorers"][name] = {
            "status": "success",
            "restoration_time_sec": elapsed,
            "changed_pixels_outside_mask": changes.changed_pixels_outside_mask,
            "changed_pixels_inside_mask": changes.changed_pixels_inside_mask,
            "max_abs_diff_outside_mask": changes.max_abs_diff_outside_mask,
            "max_abs_diff_inside_mask": changes.max_abs_diff_inside_mask,
            **candidate.metadata,
        }
        metrics["outputs"][name] = {
            "restored": str(restored_path),
            "diff": str(diff_path),
        }
    except Exception as exc:
        elapsed = time.perf_counter() - start
        metrics["restorers"][name] = {
            "status": "failed",
            "restoration_time_sec": elapsed,
            "error": str(exc),
        }
        if strict:
            raise
        print(f"{name} failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
