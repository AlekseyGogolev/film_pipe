from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS_ROOT = ROOT / "models"

MICROSOFT_REPO_URL = "https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life.git"
MICROSOFT_GLOBAL_CHECKPOINT_URL = (
    "https://github.com/microsoft/Bringing-Old-Photos-Back-to-Life/"
    "releases/download/v1.0/global_checkpoints.zip"
)
SYNCBN_REPO_URL = "https://github.com/vacancy/Synchronized-BatchNorm-PyTorch.git"

LAMA_REPO_URL = "https://github.com/advimman/lama.git"
BIG_LAMA_ZIP_URL = "https://huggingface.co/smartywu/big-lama/resolve/main/big-lama.zip"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download AI restoration experiment models.")
    parser.add_argument("--models-root", type=Path, default=DEFAULT_MODELS_ROOT)
    parser.add_argument(
        "--only",
        choices=("all", "detector", "lama"),
        default="all",
        help="Limit downloads to one model family.",
    )
    parser.add_argument("--force", action="store_true", help="Redownload archives and reclone repos.")
    args = parser.parse_args()

    models_root = args.models_root.resolve()
    models_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "generated_at_unix": int(time.time()),
        "models_root": str(models_root),
        "official_checksums": "No official checksums were found in the upstream READMEs/releases checked by this experiment.",
    }

    if args.only in ("all", "detector"):
        manifest["microsoft_bopbtl"] = download_microsoft(models_root, force=args.force)
    if args.only in ("all", "lama"):
        manifest["lama"] = download_lama(models_root, force=args.force)

    manifest_path = models_root / "MODELS.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote provenance manifest: {manifest_path}")


def download_microsoft(models_root: Path, *, force: bool) -> dict[str, Any]:
    model_root = models_root / "microsoft_bopbtl"
    repo_path = model_root / "repo"
    archive_path = model_root / "global_checkpoints.zip"
    model_root.mkdir(parents=True, exist_ok=True)

    ensure_git_repo(MICROSOFT_REPO_URL, repo_path, force=force)
    ensure_sync_batchnorm(repo_path, models_root, force=force)
    download_file(MICROSOFT_GLOBAL_CHECKPOINT_URL, archive_path, force=force)
    extract_zip(archive_path, repo_path / "Global", force=force)

    checkpoint = repo_path / "Global" / "checkpoints" / "detection" / "FT_Epoch_latest.pt"
    if not checkpoint.exists():
        raise FileNotFoundError(
            "Expected Microsoft scratch detector checkpoint was not found after extraction: "
            f"{checkpoint}"
        )

    return {
        "name": "Microsoft Bringing Old Photos Back to Life scratch detector",
        "repo_url": MICROSOFT_REPO_URL,
        "weights_url": MICROSOFT_GLOBAL_CHECKPOINT_URL,
        "license": "MIT",
        "local_repo": str(repo_path),
        "local_checkpoint": str(checkpoint),
        "archive_sha256": sha256_file(archive_path),
        "checkpoint_sha256": sha256_file(checkpoint),
        "sync_batchnorm_dependency": {
            "repo_url": SYNCBN_REPO_URL,
            "license": "MIT",
        },
    }


def download_lama(models_root: Path, *, force: bool) -> dict[str, Any]:
    model_root = models_root / "lama"
    repo_path = model_root / "repo"
    archive_path = model_root / "big-lama.zip"
    model_root.mkdir(parents=True, exist_ok=True)

    ensure_git_repo(LAMA_REPO_URL, repo_path, force=force)
    download_file(BIG_LAMA_ZIP_URL, archive_path, force=force)
    extract_zip(archive_path, model_root, force=force)

    big_lama_path = find_big_lama_dir(model_root)
    checkpoint = big_lama_path / "models" / "best.ckpt"
    if not checkpoint.exists():
        raise FileNotFoundError(f"Expected Big-LaMa checkpoint was not found: {checkpoint}")

    return {
        "name": "LaMa Big-LaMa",
        "repo_url": LAMA_REPO_URL,
        "weights_url": BIG_LAMA_ZIP_URL,
        "license": "Apache-2.0",
        "local_repo": str(repo_path),
        "local_model_dir": str(big_lama_path),
        "local_checkpoint": str(checkpoint),
        "archive_sha256": sha256_file(archive_path),
        "checkpoint_sha256": sha256_file(checkpoint),
        "source_note": "The official LaMa README currently points to accessible mirrors because older Yandex links are unavailable.",
    }


def ensure_git_repo(url: str, destination: Path, *, force: bool) -> None:
    if force and destination.exists():
        shutil.rmtree(destination)
    if (destination / ".git").exists():
        print(f"Repo already exists: {destination}")
        return
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Destination exists and is not an empty git repo: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", url, str(destination)])


def ensure_sync_batchnorm(repo_path: Path, models_root: Path, *, force: bool) -> None:
    target = repo_path / "Global" / "detection_models" / "sync_batchnorm"
    if target.exists() and not force:
        print(f"sync_batchnorm already exists: {target}")
        return

    cache_repo = models_root / "_source_cache" / "Synchronized-BatchNorm-PyTorch"
    ensure_git_repo(SYNCBN_REPO_URL, cache_repo, force=force)
    source = cache_repo / "sync_batchnorm"
    if not source.exists():
        raise FileNotFoundError(f"sync_batchnorm source folder not found: {source}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def download_file(url: str, destination: Path, *, force: bool) -> None:
    if destination.exists() and not force:
        print(f"Archive already exists: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    print(f"Downloading {url}")
    with urllib.request.urlopen(url) as response, temporary.open("wb") as file:
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            file.write(chunk)
            downloaded += len(chunk)
            if total:
                percent = downloaded / total * 100.0
                print(f"\r  {downloaded / 1024 / 1024:.1f} MiB / {total / 1024 / 1024:.1f} MiB ({percent:.1f}%)", end="")
        if total:
            print()
    temporary.replace(destination)


def extract_zip(archive_path: Path, destination: Path, *, force: bool) -> None:
    marker = destination / ".filmpipe_extracted"
    if marker.exists() and not force:
        print(f"Archive already extracted: {archive_path}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {archive_path} -> {destination}")
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(destination)
    marker.write_text(str(int(time.time())), encoding="utf-8")


def find_big_lama_dir(model_root: Path) -> Path:
    direct = model_root / "big-lama"
    if (direct / "config.yaml").exists() and (direct / "models" / "best.ckpt").exists():
        return direct
    candidates = [
        path.parent
        for path in model_root.rglob("config.yaml")
        if (path.parent / "models" / "best.ckpt").exists()
    ]
    if not candidates:
        raise FileNotFoundError(f"Could not find extracted Big-LaMa model under {model_root}")
    chosen = candidates[0]
    if chosen != direct and not direct.exists():
        shutil.move(str(chosen), str(direct))
        return direct
    return chosen


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"download_models.py failed: {exc}", file=sys.stderr)
        raise
