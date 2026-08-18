from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
MODELS_ROOT = ROOT / "models"
MANIFEST_PATH = MODELS_ROOT / "MODELS.json"


@dataclass(frozen=True)
class ManualAsset:
    candidate: str
    local_path: str
    source_url: str
    approx_size: str
    license_note: str
    required_for_first_pass: bool = True
    note: str = ""


ASSETS = [
    ManualAsset(
        candidate="flux_kontext_q4",
        local_path="models/flux/flux1-kontext-dev-Q4_K_M.gguf",
        source_url="https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/blob/main/flux1-kontext-dev-Q4_K_M.gguf",
        approx_size="6.93 GB",
        license_note="FLUX.1 dev non-commercial license",
        note="Recommended first candidate for RTX 3060 12 GB.",
    ),
    ManualAsset(
        candidate="flux_kontext_q5",
        local_path="models/flux/flux1-kontext-dev-Q5_K_M.gguf",
        source_url="https://huggingface.co/QuantStack/FLUX.1-Kontext-dev-GGUF/blob/main/flux1-kontext-dev-Q5_K_M.gguf",
        approx_size="8.42 GB",
        license_note="FLUX.1 dev non-commercial license",
        required_for_first_pass=False,
        note="Try only after Q4 works and VRAM headroom is acceptable.",
    ),
    ManualAsset(
        candidate="flux_kontext_q4",
        local_path="models/flux/ae.safetensors",
        source_url="https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/blob/main/split_files/vae/ae.safetensors",
        approx_size="335 MB",
        license_note="FLUX.1 dev non-commercial license",
        note="Public fallback. Official upstream VAE path may require HF auth: https://huggingface.co/black-forest-labs/FLUX.1-dev/blob/main/ae.safetensors",
    ),
    ManualAsset(
        candidate="flux_kontext_q5",
        local_path="models/flux/ae.safetensors",
        source_url="https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/blob/main/split_files/vae/ae.safetensors",
        approx_size="335 MB",
        license_note="FLUX.1 dev non-commercial license",
        note="Same VAE as Q4. Official upstream path may require HF auth.",
    ),
    ManualAsset(
        candidate="flux_kontext_q4",
        local_path="models/flux/clip_l.safetensors",
        source_url="https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/clip_l.safetensors",
        approx_size="246 MB",
        license_note="Inherited from source encoder/model terms",
    ),
    ManualAsset(
        candidate="flux_kontext_q5",
        local_path="models/flux/clip_l.safetensors",
        source_url="https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/clip_l.safetensors",
        approx_size="246 MB",
        license_note="Inherited from source encoder/model terms",
    ),
    ManualAsset(
        candidate="flux_kontext_q4",
        local_path="models/flux/t5xxl_fp16.safetensors",
        source_url="https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/t5xxl_fp16.safetensors",
        approx_size="9.79 GB",
        license_note="Inherited from source encoder/model terms",
        note="Stable-diffusion.cpp Kontext docs use the fp16 T5XXL file.",
    ),
    ManualAsset(
        candidate="flux_kontext_q5",
        local_path="models/flux/t5xxl_fp16.safetensors",
        source_url="https://huggingface.co/comfyanonymous/flux_text_encoders/blob/main/t5xxl_fp16.safetensors",
        approx_size="9.79 GB",
        license_note="Inherited from source encoder/model terms",
        note="Same T5XXL as Q4.",
    ),
    ManualAsset(
        candidate="qwen_edit_2509_q2",
        local_path="models/qwen/Qwen-Image-Edit-2509-Q2_K.gguf",
        source_url="https://huggingface.co/QuantStack/Qwen-Image-Edit-2509-GGUF/blob/main/Qwen-Image-Edit-2509-Q2_K.gguf",
        approx_size="7.15 GB",
        license_note="Apache-2.0",
        note="Lowest practical 2509 diffusion quant for bounded hardware check.",
    ),
    ManualAsset(
        candidate="qwen_edit_2511_q2",
        local_path="models/qwen/qwen-image-edit-2511-Q2_K.gguf",
        source_url="https://huggingface.co/unsloth/Qwen-Image-Edit-2511-GGUF/blob/main/qwen-image-edit-2511-Q2_K.gguf",
        approx_size="7.47 GB",
        license_note="Apache-2.0",
        note="Lowest practical 2511 diffusion quant for bounded hardware check.",
    ),
    ManualAsset(
        candidate="qwen_edit_2509_q2",
        local_path="models/qwen/qwen_image_vae.safetensors",
        source_url="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors",
        approx_size="250 MB",
        license_note="Apache-2.0 / inherited Qwen Image terms",
    ),
    ManualAsset(
        candidate="qwen_edit_2511_q2",
        local_path="models/qwen/qwen_image_vae.safetensors",
        source_url="https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/blob/main/split_files/vae/qwen_image_vae.safetensors",
        approx_size="250 MB",
        license_note="Apache-2.0 / inherited Qwen Image terms",
    ),
    ManualAsset(
        candidate="qwen_edit_2509_q2",
        local_path="models/qwen/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        source_url="https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF",
        approx_size="4.68 GB",
        license_note="Apache-2.0",
        note="If the exact filename differs, rename it to this local path or update config/default.json.",
    ),
    ManualAsset(
        candidate="qwen_edit_2511_q2",
        local_path="models/qwen/Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
        source_url="https://huggingface.co/mradermacher/Qwen2.5-VL-7B-Instruct-GGUF",
        approx_size="4.68 GB",
        license_note="Apache-2.0",
        note="Same Qwen2.5-VL side model as 2509.",
    ),
    ManualAsset(
        candidate="qwen_edit_2509_q2",
        local_path="models/qwen/mmproj-BF16.gguf",
        source_url="https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/blob/main/mmproj-BF16.gguf",
        approx_size="1.35 GB",
        license_note="Apache-2.0",
    ),
    ManualAsset(
        candidate="qwen_edit_2511_q2",
        local_path="models/qwen/mmproj-BF16.gguf",
        source_url="https://huggingface.co/unsloth/Qwen2.5-VL-7B-Instruct-GGUF/blob/main/mmproj-BF16.gguf",
        approx_size="1.35 GB",
        license_note="Apache-2.0",
        note="Used for the GGUF VLM path; stable-diffusion.cpp docs must be rechecked if this fails.",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Manual model placement helper for the Creative image-edit experiment. "
            "This script intentionally does not download model weights."
        )
    )
    parser.add_argument(
        "--candidate",
        action="append",
        choices=sorted({asset.candidate for asset in ASSETS}),
        help="Limit output/checks to one candidate. Can be passed more than once.",
    )
    parser.add_argument("--check", action="store_true", help="Verify local files and write MODELS.json.")
    parser.add_argument("--missing-only", action="store_true", help="Show only assets that are not present locally.")
    parser.add_argument("--no-sha256", action="store_true", help="Skip sha256 while writing MODELS.json.")
    args = parser.parse_args()

    selected = list(select_assets(args.candidate))
    if args.missing_only:
        selected = [asset for asset in selected if not resolve(asset.local_path).exists()]

    print_manual_table(selected)

    if args.check:
        return write_manifest(selected, include_sha256=not args.no_sha256)
    return 0


def select_assets(candidates: list[str] | None) -> Iterable[ManualAsset]:
    if not candidates:
        seen: set[tuple[str, str]] = set()
        for asset in ASSETS:
            key = (asset.candidate, asset.local_path)
            if key not in seen:
                seen.add(key)
                yield asset
        return

    wanted = set(candidates)
    seen_paths: set[str] = set()
    for asset in ASSETS:
        if asset.candidate in wanted and asset.local_path not in seen_paths:
            seen_paths.add(asset.local_path)
            yield asset


def print_manual_table(assets: list[ManualAsset]) -> None:
    if not assets:
        print("No assets matched.")
        return

    print("Manual downloads only. Put files at these paths under experiments/creative_edit:")
    for asset in assets:
        status = "OK" if resolve(asset.local_path).exists() else "MISSING"
        print()
        print(f"[{status}] {asset.candidate}")
        print(f"  local: {asset.local_path}")
        print(f"  url:   {asset.source_url}")
        print(f"  size:  {asset.approx_size}")
        print(f"  terms: {asset.license_note}")
        if asset.note:
            print(f"  note:  {asset.note}")


def write_manifest(assets: list[ManualAsset], *, include_sha256: bool) -> int:
    MODELS_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at_unix": int(time.time()),
        "models_root": str(MODELS_ROOT.resolve()),
        "download_policy": "Manual placement only; this script did not download files.",
        "assets": [],
    }

    missing = []
    for asset in assets:
        path = resolve(asset.local_path)
        record = {
            "candidate": asset.candidate,
            "local_path": str(path),
            "source_url": asset.source_url,
            "approx_size": asset.approx_size,
            "license_note": asset.license_note,
            "exists": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else None,
        }
        if path.exists() and include_sha256:
            record["sha256"] = sha256_file(path)
        manifest["assets"].append(record)
        if not path.exists() and asset.required_for_first_pass:
            missing.append(asset.local_path)

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Wrote manifest: {MANIFEST_PATH}")

    if missing:
        print("Missing required first-pass assets:", file=sys.stderr)
        for item in missing:
            print(f"  {item}", file=sys.stderr)
        return 1
    return 0


def resolve(local_path: str) -> Path:
    return ROOT / local_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
