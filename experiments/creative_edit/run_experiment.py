from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "default.json"


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    candidate = config["candidates"][args.candidate]
    result_dir = make_result_dir(args.output_root, args.candidate, args.input)
    result_dir.mkdir(parents=True, exist_ok=True)

    input_copy = None
    if args.input is not None:
        input_path = args.input.resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Input image not found: {input_path}")
        input_copy = result_dir / f"input{input_path.suffix.lower()}"
        shutil.copy2(input_path, input_copy)

    prompt = args.prompt.strip()
    if not prompt:
        raise ValueError("Prompt must be non-empty.")

    output_path = result_dir / f"output.{config['defaults'].get('output_format', 'png')}"
    command = build_cli_command(config, candidate, args, input_copy or Path("SOURCE_REQUIRED"), output_path)
    metrics: dict[str, Any] = {
        "candidate": args.candidate,
        "label": candidate.get("label"),
        "mode": args.mode,
        "prompt": prompt,
        "input": str(input_copy) if input_copy else None,
        "output": str(output_path),
        "created_at_unix": int(time.time()),
        "command": command,
        "environment": probe_environment(),
        "assets": check_assets(candidate),
    }

    missing_assets = [asset for asset in metrics["assets"] if not asset["exists"]]
    if missing_assets and not args.skip_asset_check:
        metrics["status"] = "blocked_missing_assets"
        write_json(result_dir / "metrics.json", metrics)
        print_missing_assets(missing_assets)
        print(f"Wrote metrics: {result_dir / 'metrics.json'}")
        return 2

    if args.dry_run:
        metrics["status"] = "dry_run"
        write_text(result_dir / "command.txt", shell_join(command))
        if args.mode.startswith("server"):
            server_command = build_server_command(config, candidate, args)
            metrics["server_start_hint"] = server_command
            write_text(result_dir / "server_command.txt", shell_join(server_command))
        write_json(result_dir / "metrics.json", metrics)
        if args.mode.startswith("server"):
            print("Server command:")
            print(shell_join(metrics["server_start_hint"]))
            print()
            print("Client mode uses the configured server endpoint.")
        else:
            print(shell_join(command))
        print(f"Wrote dry-run bundle: {result_dir}")
        return 0

    if args.mode == "cli":
        if input_copy is None:
            raise ValueError("--input is required for cli mode.")
        return run_cli(
            command,
            result_dir,
            output_path,
            metrics,
            timeout_sec=args.timeout_sec or config["defaults"]["timeout_sec"],
        )

    if args.mode == "server-openai":
        if input_copy is None:
            raise ValueError("--input is required for server-openai mode.")
        return run_server_openai(config, candidate, args, input_copy, result_dir, output_path, metrics)

    if args.mode == "server-native":
        if input_copy is None:
            raise ValueError("--input is required for server-native mode.")
        return run_server_native(config, candidate, args, input_copy, result_dir, output_path, metrics)

    raise ValueError(f"Unsupported mode: {args.mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a manual FilmPipe Creative image-edit experiment.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--candidate",
        default="flux_kontext_q4",
        choices=("flux_kontext_q4", "flux_kontext_q5", "qwen_edit_2509_q2", "qwen_edit_2511_q2"),
    )
    parser.add_argument("--input", type=Path, help="Source image to edit.")
    parser.add_argument("--prompt", required=True, help="Image edit prompt.")
    parser.add_argument("--output-root", type=Path, default=ROOT / "results")
    parser.add_argument("--mode", choices=("cli", "server-openai", "server-native"), default="cli")
    parser.add_argument("--sd-cli", type=Path, help="Override path to sd-cli.")
    parser.add_argument("--server-url", help="Override server URL for server modes.")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--strength", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--timeout-sec", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Write command/metrics without running inference.")
    parser.add_argument("--skip-asset-check", action="store_true", help="Allow a run even if configured assets are missing.")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_result_dir(output_root: Path, candidate: str, input_path: Path | None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = input_path.stem if input_path else "dry_run"
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in stem)
    return output_root.resolve() / f"{stamp}_{candidate}_{safe}"


def build_cli_command(
    config: dict[str, Any],
    candidate: dict[str, Any],
    args: argparse.Namespace,
    input_path: Path,
    output_path: Path,
) -> list[str]:
    sd_cli = args.sd_cli or resolve_config_path(config["runtime"]["sd_cli"])
    values = {
        "input": str(input_path),
        "prompt": args.prompt.strip(),
        "output": str(output_path),
    }

    command = [str(sd_cli)]
    for token in candidate["cli_args"]:
        command.append(expand_token(str(token), values))

    defaults = config["defaults"]
    add_or_replace(command, "-W", "--width", str(args.width or defaults["width"]))
    add_or_replace(command, "-H", "--height", str(args.height or defaults["height"]))
    add_or_replace(command, "-s", "--seed", str(args.seed if args.seed is not None else defaults["seed"]))
    if args.steps is not None:
        add_or_replace(command, None, "--steps", str(args.steps))
    if args.strength is not None:
        add_or_replace(command, None, "--strength", str(args.strength))
    return command


def expand_token(token: str, values: dict[str, str]) -> str:
    if token == "{input}":
        return values["input"]
    if token == "{prompt}":
        return values["prompt"]
    if token == "{output}":
        return values["output"]
    if token.startswith("{") and token.endswith("}"):
        return str(resolve_config_path(token[1:-1]))
    return token


def add_or_replace(command: list[str], short_flag: str | None, long_flag: str, value: str) -> None:
    for flag in (long_flag, short_flag):
        if flag is None:
            continue
        if flag in command:
            index = command.index(flag)
            if index + 1 >= len(command):
                command.append(value)
            else:
                command[index + 1] = value
            return
    command.extend([long_flag, value])


def check_assets(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for local_path in candidate.get("expected_assets", []):
        path = resolve_config_path(local_path)
        records.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else None,
            }
        )
    return records


def print_missing_assets(missing_assets: list[dict[str, Any]]) -> None:
    print("Missing assets. Put model files under experiments/creative_edit/models first:", file=sys.stderr)
    for asset in missing_assets:
        print(f"  {asset['path']}", file=sys.stderr)
    print("Run: python experiments/creative_edit/download_models.py --candidate <name>", file=sys.stderr)


def run_cli(
    command: list[str],
    result_dir: Path,
    output_path: Path,
    metrics: dict[str, Any],
    *,
    timeout_sec: int | None,
) -> int:
    executable = Path(command[0])
    if not executable.exists():
        metrics["status"] = "blocked_missing_runtime"
        metrics["error"] = f"sd-cli not found: {executable}"
        write_json(result_dir / "metrics.json", metrics)
        print(metrics["error"], file=sys.stderr)
        return 2

    write_text(result_dir / "command.txt", shell_join(command))
    monitor = NvidiaSmiMonitor()
    start = time.perf_counter()
    monitor.start()
    completed = subprocess.run(
        command,
        cwd=result_dir,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_sec,
    )
    elapsed = time.perf_counter() - start
    monitor.stop()

    write_text(result_dir / "stdout.log", completed.stdout)
    write_text(result_dir / "stderr.log", completed.stderr)
    metrics.update(
        {
            "status": "success" if completed.returncode == 0 and output_path.exists() else "failed",
            "returncode": completed.returncode,
            "elapsed_sec": elapsed,
            "output_exists": output_path.exists(),
            "output_size_bytes": output_path.stat().st_size if output_path.exists() else None,
            "nvidia_smi_samples": monitor.samples,
            "peak_vram_mib": monitor.peak_vram_mib,
        }
    )
    write_json(result_dir / "metrics.json", metrics)
    print(f"Wrote result bundle: {result_dir}")
    return 0 if metrics["status"] == "success" else 1


def run_server_openai(
    config: dict[str, Any],
    candidate: dict[str, Any],
    args: argparse.Namespace,
    input_path: Path,
    result_dir: Path,
    output_path: Path,
    metrics: dict[str, Any],
) -> int:
    server_url = (args.server_url or config["runtime"]["server_url"]).rstrip("/")
    url = f"{server_url}/v1/images/edits"
    fields = {
        "prompt": args.prompt.strip(),
        "response_format": "b64_json",
    }
    start = time.perf_counter()
    response = multipart_post(url, fields, "image", input_path)
    elapsed = time.perf_counter() - start
    write_json(result_dir / "server_response.json", response)
    save_b64_image_response(response, output_path)
    metrics.update(
        {
            "status": "success" if output_path.exists() else "server_response_without_image",
            "server_url": server_url,
            "elapsed_sec": elapsed,
            "output_exists": output_path.exists(),
            "server_start_hint": build_server_command(config, candidate, args),
        }
    )
    write_json(result_dir / "metrics.json", metrics)
    print(f"Wrote server-openai bundle: {result_dir}")
    return 0 if output_path.exists() else 1


def run_server_native(
    config: dict[str, Any],
    candidate: dict[str, Any],
    args: argparse.Namespace,
    input_path: Path,
    result_dir: Path,
    output_path: Path,
    metrics: dict[str, Any],
) -> int:
    server_url = (args.server_url or config["runtime"]["server_url"]).rstrip("/")
    request_body = build_native_request(config, candidate, args, input_path)
    write_json(result_dir / "native_request.json", request_body)

    start = time.perf_counter()
    submission = json_post(f"{server_url}/sdcpp/v1/img_gen", request_body, expected_status=(200, 202))
    write_json(result_dir / "native_submission.json", submission)
    poll_url = submission.get("poll_url")
    if not poll_url:
        metrics.update(
            {
                "status": "server_submission_without_poll_url",
                "server_url": server_url,
                "elapsed_sec": time.perf_counter() - start,
                "server_start_hint": build_server_command(config, candidate, args),
            }
        )
        write_json(result_dir / "metrics.json", metrics)
        return 1

    job = poll_native_job(server_url, str(poll_url), timeout_sec=args.timeout_sec or config["defaults"]["timeout_sec"])
    write_json(result_dir / "native_job.json", job)
    save_native_job_image(job, output_path)
    elapsed = time.perf_counter() - start
    metrics.update(
        {
            "status": "success" if output_path.exists() else f"server_job_{job.get('status', 'unknown')}",
            "server_url": server_url,
            "elapsed_sec": elapsed,
            "output_exists": output_path.exists(),
            "server_start_hint": build_server_command(config, candidate, args),
        }
    )
    write_json(result_dir / "metrics.json", metrics)
    print(f"Wrote server-native bundle: {result_dir}")
    return 0 if output_path.exists() else 1


def build_server_command(config: dict[str, Any], candidate: dict[str, Any], args: argparse.Namespace) -> list[str]:
    sd_server = resolve_config_path(config["runtime"]["sd_server"])
    command = [str(sd_server)]
    values = {"prompt": args.prompt.strip(), "input": "", "output": ""}
    for token in candidate.get("server_start_args", []):
        command.append(expand_token(str(token), values))
    parsed = urllib.parse.urlparse(args.server_url or config["runtime"]["server_url"])
    if parsed.hostname:
        command.extend(["--listen-ip", parsed.hostname])
    if parsed.port:
        command.extend(["--listen-port", str(parsed.port)])
    return command


def build_native_request(
    config: dict[str, Any],
    candidate: dict[str, Any],
    args: argparse.Namespace,
    input_path: Path,
) -> dict[str, Any]:
    defaults = config["defaults"]
    native = candidate.get("native_request", {})
    width = args.width or defaults["width"]
    height = args.height or defaults["height"]
    steps = args.steps or native.get("sample_steps", 24)
    return {
        "prompt": args.prompt.strip(),
        "negative_prompt": "",
        "clip_skip": -1,
        "width": width,
        "height": height,
        "strength": native.get("strength", 0.75),
        "seed": args.seed if args.seed is not None else defaults["seed"],
        "batch_count": 1,
        "auto_resize_ref_image": True,
        "increase_ref_index": False,
        "embed_image_metadata": True,
        "init_image": image_to_data_url(input_path),
        "ref_images": [],
        "mask_image": None,
        "control_image": None,
        "ip_adapter_image": None,
        "sample_params": {
            "scheduler": "discrete",
            "sample_method": native.get("sample_method", "euler"),
            "sample_steps": steps,
            "eta": 1.0,
            "shifted_timestep": 0,
            "custom_sigmas": [],
            "flow_shift": native.get("flow_shift", 0.0),
            "guidance": {
                "txt_cfg": native.get("cfg_scale", 1.0),
                "img_cfg": native.get("cfg_scale", 1.0),
                "distilled_guidance": 3.5,
                "slg": {"layers": [7, 8, 9], "layer_start": 0.01, "layer_end": 0.2, "scale": 0.0},
            },
        },
        "lora": [],
        "hires": {
            "enabled": False,
            "upscaler": "Latent",
            "scale": 2.0,
            "target_width": 0,
            "target_height": 0,
            "steps": 0,
            "denoising_strength": 0.7,
            "custom_sigmas": [],
            "upscale_tile_size": 128,
        },
        "vae_tiling_params": {
            "enabled": True,
            "temporal_tiling": False,
            "tile_size_x": 0,
            "tile_size_y": 0,
            "target_overlap": 0.5,
            "rel_size_x": 0.0,
            "rel_size_y": 0.0,
            "extra_tiling_args": "",
        },
        "cache_mode": "disabled",
        "cache_option": "",
        "scm_mask": "",
        "scm_policy_dynamic": True,
        "output_format": config["defaults"].get("output_format", "png"),
        "output_compression": 100,
    }


def poll_native_job(server_url: str, poll_url: str, *, timeout_sec: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    url = server_url + poll_url if poll_url.startswith("/") else poll_url
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = json_get(url)
        status = str(last.get("status", "")).lower()
        if status in {"complete", "completed", "success", "succeeded", "failed", "error", "canceled", "cancelled"}:
            return last
        time.sleep(2.0)
    last["poll_timeout_sec"] = timeout_sec
    return last


def multipart_post(url: str, fields: dict[str, str], file_field: str, path: Path) -> dict[str, Any]:
    boundary = f"----filmpipe-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return decode_json_response(request)


def json_post(url: str, payload: dict[str, Any], *, expected_status: tuple[int, ...] = (200,)) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return decode_json_response(request, expected_status=expected_status)


def json_get(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    return decode_json_response(request)


def decode_json_response(
    request: urllib.request.Request,
    *,
    expected_status: tuple[int, ...] = (200,),
) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        text = exc.read().decode("utf-8", errors="replace")
    if status not in expected_status:
        raise RuntimeError(f"HTTP {status} from {request.full_url}: {text[:1000]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_response": text, "status_code": status}


def save_b64_image_response(response: dict[str, Any], output_path: Path) -> None:
    data = response.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("b64_json"), str):
            output_path.write_bytes(base64.b64decode(first["b64_json"]))


def save_native_job_image(job: dict[str, Any], output_path: Path) -> None:
    for key in ("image", "output", "result"):
        value = job.get(key)
        if isinstance(value, str) and looks_like_base64_image(value):
            output_path.write_bytes(decode_data_url_or_base64(value))
            return
    images = job.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str) and looks_like_base64_image(first):
            output_path.write_bytes(decode_data_url_or_base64(first))
        if isinstance(first, dict):
            for key in ("image", "b64_json", "data"):
                value = first.get(key)
                if isinstance(value, str) and looks_like_base64_image(value):
                    output_path.write_bytes(decode_data_url_or_base64(value))
                    return


def image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def looks_like_base64_image(value: str) -> bool:
    return value.startswith("data:image/") or len(value) > 100


def decode_data_url_or_base64(value: str) -> bytes:
    if value.startswith("data:"):
        _, encoded = value.split(",", 1)
        return base64.b64decode(encoded)
    return base64.b64decode(value)


def probe_environment() -> dict[str, Any]:
    return {
        "platform": sys.platform,
        "python": sys.version,
        "cwd": os.getcwd(),
        "nvidia_smi": run_probe(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.free,memory.total,driver_version",
                "--format=csv,noheader",
            ]
        ),
    }


def run_probe(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    except FileNotFoundError:
        return {"available": False, "error": "not found"}
    return {
        "available": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


class NvidiaSmiMonitor:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []
        self.peak_vram_mib: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if shutil.which("nvidia-smi") is None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            sample = run_probe(
                [
                    "nvidia-smi",
                    "--query-gpu=timestamp,memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ]
            )
            if sample["available"]:
                parsed = parse_nvidia_smi_sample(sample["stdout"])
                if parsed:
                    self.samples.append(parsed)
                    used = parsed.get("memory_used_mib")
                    if isinstance(used, int):
                        self.peak_vram_mib = max(self.peak_vram_mib or 0, used)
            else:
                self.samples.append(sample)
                return
            time.sleep(1.0)


def parse_nvidia_smi_sample(stdout: str) -> dict[str, Any] | None:
    first_line = stdout.splitlines()[0] if stdout else ""
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 4:
        return None
    try:
        return {
            "timestamp": parts[0],
            "memory_used_mib": int(parts[1]),
            "memory_total_mib": int(parts[2]),
            "gpu_util_percent": int(parts[3]),
        }
    except ValueError:
        return {"raw": first_line}


def resolve_config_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def shell_join(command: list[str]) -> str:
    return shlex.join(command)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value + ("\n" if value and not value.endswith("\n") else ""), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
