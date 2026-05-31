"""Harness de integracion para el flujo backend local_fake."""

from __future__ import annotations

import base64
import datetime
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[3]
DOCKER_COMPOSE_FILES = ["docker-compose.yml", "docker-compose.local-fake.yml"]
DOCKER_COMPOSE_BASE = ["docker", "compose"]
BACKEND_SERVICES = ["qdrant", "ingest-service", "processing-service", "rag-service"]
LOG_BASE_DIR = ROOT_DIR / "tests" / "integration" / "logs"
CRITICAL_LOG_PATTERNS = (
    "ERROR",
    "Traceback",
    "VectorStoreError",
    "Unexpected Response: 409",
)


def make_log_dir() -> Path:
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = LOG_BASE_DIR / f"local_fake_flow_{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def compose_file_args() -> list[str]:
    return [arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)]


def run_command(args: list[str], timeout: int = 300, include_stderr: bool = True) -> str:
    process = subprocess.run(
        args,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"{' '.join(args)} failed:\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    output = process.stdout
    if include_stderr and process.stderr:
        output = f"{output}\n--- stderr ---\n{process.stderr}" if output else process.stderr
    return output


def run_compose(args: list[str], timeout: int = 300, include_stderr: bool = True) -> str:
    return run_command(
        [*DOCKER_COMPOSE_BASE, *compose_file_args(), *args],
        timeout=timeout,
        include_stderr=include_stderr,
    )


def run_python_in_service(service: str, code: str, timeout: int = 120) -> str:
    return run_compose(["exec", "-T", service, "python", "-c", code], timeout=timeout)


def get_service_container_ids(service: str) -> list[str]:
    try:
        output = run_compose(["ps", "-q", service], include_stderr=False)
    except RuntimeError:
        return []
    return [line.strip() for line in output.splitlines() if line.strip()]


def wait_for_compose_services(services: list[str], timeout_sec: int = 120) -> None:
    deadline = time.time() + timeout_sec
    last_state: dict[str, list[str]] = {}

    while time.time() < deadline:
        all_running = True
        for service in services:
            container_ids = get_service_container_ids(service)
            last_state[service] = container_ids
            if not container_ids:
                all_running = False
                continue

            for container_id in container_ids:
                try:
                    state = run_command(
                        ["docker", "inspect", "-f", "{{.State.Running}}", container_id]
                    ).strip()
                except RuntimeError:
                    all_running = False
                    break
                if state != "true":
                    all_running = False
                    break

        if all_running:
            return

        time.sleep(1)

    raise RuntimeError(f"Timeout waiting for compose services to run: {last_state}")


def wait_for_internal_http(service: str, url: str, timeout_sec: int = 120) -> None:
    deadline = time.time() + timeout_sec
    last_error = ""
    code = (
        "import httpx, sys\n"
        f"response = httpx.get({url!r}, timeout=10.0)\n"
        "print(response.status_code)\n"
        "sys.exit(0 if response.status_code == 200 else 1)\n"
    )

    while time.time() < deadline:
        try:
            run_python_in_service(service, code, timeout=20)
            return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(2)

    raise RuntimeError(f"Timeout waiting for internal URL {url}: {last_error}")


def dump_compose_state(log_dir: Path) -> None:
    for command_name, args, filename in (
        ("ps", ["ps"], "docker_compose_ps.txt"),
        ("config", ["config"], "docker_compose_config.txt"),
    ):
        try:
            save_text_file(log_dir / filename, run_compose(args))
        except RuntimeError as exc:
            save_text_file(log_dir / f"docker_compose_{command_name}_error.txt", str(exc))


def dump_container_logs(log_dir: Path, service: str, container_ids: list[str]) -> None:
    for idx, container_id in enumerate(container_ids, start=1):
        try:
            output = run_command(["docker", "logs", "--timestamps", "--tail", "1000", container_id])
            save_text_file(log_dir / f"{service}_{idx}_{container_id[:12]}.log", output)
        except RuntimeError as exc:
            save_text_file(log_dir / f"{service}_{idx}_docker_logs_error.txt", str(exc))


def dump_service_logs(log_dir: Path) -> None:
    for service in BACKEND_SERVICES:
        try:
            output = run_compose(["logs", "--no-color", "--timestamps", "--tail", "1000", service])
            if output.strip():
                save_text_file(log_dir / f"{service}.log", output)
                continue
            save_text_file(
                log_dir / f"{service}_logs_empty.txt", "docker compose logs returned empty output"
            )
        except RuntimeError as exc:
            save_text_file(log_dir / f"{service}_logs_error.txt", str(exc))

        container_ids = get_service_container_ids(service)
        save_text_file(log_dir / f"{service}_container_ids.txt", "\n".join(container_ids) or "none")
        if container_ids:
            dump_container_logs(log_dir, service, container_ids)


def append_evidence(log_dir: Path, title: str, payload: dict[str, Any]) -> None:
    evidence_path = log_dir / "flow_evidence.txt"
    with evidence_path.open("a", encoding="utf-8") as file:
        file.write(f"\n## {title}\n")
        file.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        file.write("\n")


def build_pdf_bytes(text: str = "Senior Python Engineer with FastAPI experience") -> bytes:
    """Construye un PDF minimo con texto seleccionable, sin depender de reportlab."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )
    stream = b"BT\n/F1 24 Tf\n100 700 Td\n(" + safe_text.encode("latin-1") + b") Tj\nET\n"
    obj4 = (
        b"4 0 obj\n<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"endstream\nendobj\n"
    )
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    body = obj1 + obj2 + obj3 + obj4 + obj5
    xref_offset = len(header) + len(body)

    offsets = []
    position = len(header)
    for obj in (obj1, obj2, obj3, obj4, obj5):
        offsets.append(position)
        position += len(obj)

    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        xref += f"{offset:010d} 00000 n \n".encode()

    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return header + body + xref + trailer


def assert_no_processing_critical_errors(log_dir: Path) -> None:
    dump_service_logs(log_dir)
    processing_log = log_dir / "processing-service.log"
    assert processing_log.exists(), "processing-service.log was not captured"
    content = processing_log.read_text(encoding="utf-8", errors="replace")
    found = [pattern for pattern in CRITICAL_LOG_PATTERNS if pattern in content]
    assert not found, f"Critical processing log patterns found: {found}"


@pytest.fixture(scope="session")
def local_fake_stack() -> dict[str, Any]:
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is required for local_fake integration tests")

    log_dir = make_log_dir()
    save_text_file(log_dir / "test_start.txt", "Starting backend local_fake integration test\n")

    try:
        run_compose(["down", "-v", "--remove-orphans"], timeout=300)
    except RuntimeError as exc:
        save_text_file(log_dir / "compose_initial_down_error.txt", str(exc))

    try:
        run_compose(["up", "--build", "-d", *BACKEND_SERVICES], timeout=600)
    except RuntimeError as exc:
        save_text_file(log_dir / "compose_up_error.txt", str(exc))
        pytest.fail(f"No se pudo iniciar Docker Compose: {exc}")

    service_urls = {
        "qdrant": "http://qdrant:6333",
        "ingest": "http://ingest-service:8000",
        "processing": "http://processing-service:8000",
        "rag": "http://rag-service:8000",
    }

    try:
        wait_for_compose_services(BACKEND_SERVICES)
        save_text_file(
            log_dir / "docker_compose_runtime_urls.txt",
            "\n".join(f"{name}={url}" for name, url in service_urls.items()) + "\n",
        )
        wait_for_internal_http("ingest-service", f"{service_urls['qdrant']}/collections")
        wait_for_internal_http("ingest-service", f"{service_urls['ingest']}/openapi.json")
        wait_for_internal_http("ingest-service", f"{service_urls['processing']}/openapi.json")
        wait_for_internal_http("ingest-service", f"{service_urls['rag']}/openapi.json")
        yield {"log_dir": log_dir, "service_urls": service_urls}
    finally:
        save_text_file(log_dir / "test_end.txt", "Stopping backend local_fake integration test\n")
        dump_compose_state(log_dir)
        dump_service_logs(log_dir)
        try:
            run_compose(["down", "-v"], timeout=300)
        except RuntimeError as exc:
            save_text_file(log_dir / "compose_down_error.txt", str(exc))
        print(f"Docker Compose logs preserved in: {log_dir}")


@pytest.fixture()
def upload_pdf(local_fake_stack: dict[str, Any]):
    def _upload(filename: str = "candidate.pdf", text: str | None = None) -> dict[str, Any]:
        log_dir = local_fake_stack["log_dir"]
        service_urls = local_fake_stack["service_urls"]
        pdf_bytes = build_pdf_bytes(text or "Senior Python Engineer with FastAPI experience")
        upload_url = f"{service_urls['ingest']}/upload"
        upload_code = (
            "import base64, json, httpx\n"
            f"pdf_bytes = base64.b64decode({base64.b64encode(pdf_bytes).decode('ascii')!r})\n"
            "with httpx.Client(timeout=120.0) as client:\n"
            f"    response = client.post({upload_url!r}, files={{\n"
            f"        'files': ({filename!r}, pdf_bytes, 'application/pdf')\n"
            "    })\n"
            "print(json.dumps({'status_code': response.status_code, 'text': response.text}))\n"
        )
        result = json.loads(run_python_in_service("ingest-service", upload_code, timeout=180))
        append_evidence(
            log_dir,
            "upload_response",
            {"url": upload_url, "status_code": result["status_code"], "response": result["text"]},
        )
        return result

    return _upload


def search_candidates(
    service_urls: dict[str, str],
    job_description: str,
    top_k: int = 5,
    timeout: int = 180,
) -> dict[str, Any]:
    search_url = f"{service_urls['rag']}/search"
    payload = {"job_description": job_description, "top_k": top_k}
    search_code = (
        "import json, httpx\n"
        f"payload = json.loads({json.dumps(payload)!r})\n"
        "with httpx.Client(timeout=120.0) as client:\n"
        f"    response = client.post({search_url!r}, json=payload)\n"
        "print(json.dumps({'status_code': response.status_code, 'text': response.text}))\n"
    )
    return json.loads(run_python_in_service("ingest-service", search_code, timeout=timeout))


def list_candidates(
    service_urls: dict[str, str],
    timeout: int = 60,
) -> dict[str, Any]:
    """Llama a GET /search/candidates en el rag-service y devuelve {status_code, text}."""
    list_url = f"{service_urls['rag']}/search/candidates"
    code = (
        "import json, httpx\n"
        "with httpx.Client(timeout=30.0) as client:\n"
        f"    response = client.get({list_url!r})\n"
        "print(json.dumps({'status_code': response.status_code, 'text': response.text}))\n"
    )
    return json.loads(run_python_in_service("ingest-service", code, timeout=timeout))
