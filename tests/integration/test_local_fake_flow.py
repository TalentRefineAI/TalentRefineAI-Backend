import base64
import datetime
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
DOCKER_COMPOSE_FILES = ["docker-compose.yml", "docker-compose.local-fake.yml"]
DOCKER_COMPOSE_BASE = ["docker", "compose"]
LOG_BASE_DIR = ROOT_DIR / "tests" / "integration" / "logs"
LOG_SERVICES = ["ingest-service", "processing-service", "rag-service", "qdrant", "streamlit-frontend"]


def make_log_dir() -> Path:
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = LOG_BASE_DIR / f"local_fake_flow_{timestamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def dump_compose_state(log_dir: Path) -> None:
    try:
        output = run_compose_command(
            [
                *DOCKER_COMPOSE_BASE,
                *[arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)],
                "ps",
            ]
        )
        save_text_file(log_dir / "docker_compose_ps.txt", output)
    except RuntimeError as exc:
        save_text_file(log_dir / "docker_compose_ps_error.txt", str(exc))

    try:
        output = run_compose_command(
            [
                *DOCKER_COMPOSE_BASE,
                *[arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)],
                "config",
            ]
        )
        save_text_file(log_dir / "docker_compose_config.txt", output)
    except RuntimeError as exc:
        save_text_file(log_dir / "docker_compose_config_error.txt", str(exc))


def get_service_container_ids(service: str) -> list[str]:
    try:
        output = run_compose_command(
            [
                *DOCKER_COMPOSE_BASE,
                *[arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)],
                "ps",
                "-q",
                service,
            ],
            include_stderr=False,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]
    except RuntimeError:
        return []


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
                    state = run_compose_command(
                        [
                            "docker",
                            "inspect",
                            "-f",
                            "{{.State.Running}}",
                            container_id,
                        ]
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


def dump_container_logs(log_dir: Path, service: str, container_ids: list[str]) -> None:
    for idx, container_id in enumerate(container_ids, start=1):
        try:
            output = run_compose_command(
                [
                    "docker",
                    "logs",
                    "--timestamps",
                    "--tail",
                    "1000",
                    container_id,
                ]
            )
            save_text_file(log_dir / f"{service}_{idx}_{container_id[:12]}.log", output)
        except RuntimeError as exc:
            save_text_file(log_dir / f"{service}_{idx}_docker_logs_error.txt", str(exc))


def dump_service_logs(log_dir: Path) -> None:
    for service in LOG_SERVICES:
        compose_command = [
            *DOCKER_COMPOSE_BASE,
            *[arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)],
            "logs",
            "--no-color",
            "--timestamps",
            "--tail",
            "1000",
            service,
        ]
        try:
            output = run_compose_command(compose_command)
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


def is_tool_available(name: str) -> bool:
    return shutil.which(name) is not None


def compose_args(*args: str) -> list[str]:
    return [*DOCKER_COMPOSE_BASE, *args]


def run_compose_command(args: list[str], timeout: int = 300, include_stderr: bool = True) -> str:
    process = subprocess.run(
        args,
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} failed:\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    output = process.stdout
    if include_stderr and process.stderr:
        output = f"{output}\n--- stderr ---\n{process.stderr}" if output else process.stderr
    return output


def run_python_in_service(service: str, code: str, timeout: int = 120) -> str:
    compose_files = [arg for compose_file in DOCKER_COMPOSE_FILES for arg in ("-f", compose_file)]
    process = subprocess.run(
        [*DOCKER_COMPOSE_BASE, *compose_files, "exec", "-T", service, "python", "-c", code],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"docker compose exec {service} python failed:\n"
            f"stdout:\n{process.stdout}\n"
            f"stderr:\n{process.stderr}"
        )
    return process.stdout


def wait_for_internal_http(service: str, url: str, timeout_sec: int = 120) -> None:
    deadline = time.time() + timeout_sec
    last_error = None
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


def append_evidence(log_dir: Path, title: str, payload: dict) -> None:
    evidence_path = log_dir / "flow_evidence.txt"
    with evidence_path.open("a", encoding="utf-8") as file:
        file.write(f"\n## {title}\n")
        file.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        file.write("\n")


def build_pdf_bytes(text: str = "Senior Python Engineer with FastAPI experience") -> bytes:
    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )
    stream = b"BT\n/F1 24 Tf\n100 700 Td\n(" + text.encode("latin-1") + b") Tj\nET\n"
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


@pytest.fixture(scope="session")
def local_fake_stack():
    if not is_tool_available("docker"):
        pytest.skip("Docker CLI is required for this integration test")

    compose_files = []
    for compose_file in DOCKER_COMPOSE_FILES:
        compose_files.extend(["-f", compose_file])

    log_dir = make_log_dir()
    save_text_file(log_dir / "test_start.txt", "Starting local fake integration test\n")

    try:
        run_compose_command(
            [*DOCKER_COMPOSE_BASE, *compose_files, "down", "-v", "--remove-orphans"], timeout=300
        )
    except RuntimeError as exc:
        save_text_file(log_dir / "compose_initial_down_error.txt", str(exc))

    try:
        run_compose_command(
            [
                *DOCKER_COMPOSE_BASE,
                *compose_files,
                "up",
                "--build",
                "-d",
                "qdrant",
                "ingest-service",
                "processing-service",
                "rag-service",
                "streamlit-frontend",
            ]
        )
    except RuntimeError as exc:
        save_text_file(log_dir / "compose_up_error.txt", str(exc))
        pytest.skip(f"No se pudo iniciar Docker Compose: {exc}")

    try:
        wait_for_compose_services(
            ["qdrant", "ingest-service", "processing-service", "rag-service", "streamlit-frontend"]
        )
        service_urls = {
            "qdrant": "http://qdrant:6333",
            "ingest": "http://ingest-service:8000",
            "processing": "http://processing-service:8000",
            "rag": "http://rag-service:8000",
            "frontend": "http://streamlit-frontend:8501",
        }
        save_text_file(
            log_dir / "docker_compose_runtime_urls.txt",
            "\n".join(f"{name}={url}" for name, url in service_urls.items()) + "\n",
        )
        wait_for_internal_http(
            "ingest-service", f"{service_urls['qdrant']}/collections", timeout_sec=120
        )
        wait_for_internal_http(
            "ingest-service", f"{service_urls['ingest']}/openapi.json", timeout_sec=120
        )
        wait_for_internal_http(
            "ingest-service", f"{service_urls['processing']}/openapi.json", timeout_sec=120
        )
        wait_for_internal_http(
            "ingest-service", f"{service_urls['rag']}/openapi.json", timeout_sec=120
        )
        wait_for_internal_http(
            "ingest-service", f"{service_urls['frontend']}/_stcore/health", timeout_sec=120
        )
        yield {"log_dir": log_dir, "service_urls": service_urls}
    finally:
        save_text_file(log_dir / "test_end.txt", "Stopping local fake integration test\n")
        dump_compose_state(log_dir)
        dump_service_logs(log_dir)
        try:
            run_compose_command([*DOCKER_COMPOSE_BASE, *compose_files, "down", "-v"], timeout=300)
        except RuntimeError as exc:
            save_text_file(log_dir / "compose_down_error.txt", str(exc))
        print(f"Docker Compose logs preserved in: {log_dir}")


@pytest.mark.integration
def test_local_fake_backend_flow(local_fake_stack):
    log_dir = local_fake_stack["log_dir"]
    service_urls = local_fake_stack["service_urls"]
    pdf_bytes = build_pdf_bytes()
    upload_url = f"{service_urls['ingest']}/upload"
    search_url = f"{service_urls['rag']}/search"

    append_evidence(
        log_dir,
        "test_started",
        {
            "upload_url": upload_url,
            "search_url": search_url,
            "pdf_bytes": len(pdf_bytes),
        },
    )

    upload_code = (
        "import base64, json, httpx\n"
        f"pdf_bytes = base64.b64decode({base64.b64encode(pdf_bytes).decode('ascii')!r})\n"
        "with httpx.Client(timeout=120.0) as client:\n"
        f"    response = client.post({upload_url!r}, files={{\n"
        "        'files': ('candidate.pdf', pdf_bytes, 'application/pdf')\n"
        "    })\n"
        "print(json.dumps({'status_code': response.status_code, 'text': response.text}))\n"
    )
    upload_result = json.loads(run_python_in_service("ingest-service", upload_code, timeout=180))

    upload_response_text = upload_result["text"]
    append_evidence(
        log_dir,
        "upload_response",
        {
            "url": upload_url,
            "status_code": upload_result["status_code"],
            "response": upload_response_text,
        },
    )
    assert upload_result["status_code"] == 202, f"Upload failed: {upload_response_text}"
    payload = json.loads(upload_response_text)
    assert payload.get("error") is None, f"Backend returned error: {payload.get('error')}"
    assert isinstance(payload.get("data"), list) and len(payload["data"]) == 1

    doc_id = payload["data"][0].get("id")
    assert isinstance(doc_id, str) and doc_id, "Expected a document ID in the upload response"
    append_evidence(
        log_dir,
        "document_accepted",
        {
            "doc_id": doc_id,
            "upload_payload": payload["data"][0],
        },
    )

    search_payload = {
        "job_description": "Senior Python developer with FastAPI and backend experience",
        "filters": {},
        "top_k": 5,
    }

    deadline = time.time() + 180
    search_results = None
    uploaded_candidate = None
    last_response_text = None
    last_status_code = None
    attempts = 0

    while time.time() < deadline:
        attempts += 1
        search_code = (
            "import json, httpx\n"
            f"payload = json.loads({json.dumps(search_payload)!r})\n"
            "with httpx.Client(timeout=120.0) as client:\n"
            f"    response = client.post({search_url!r}, json=payload)\n"
            "print(json.dumps({'status_code': response.status_code, 'text': response.text}))\n"
        )
        search_response = json.loads(
            run_python_in_service("ingest-service", search_code, timeout=180)
        )

        last_status_code = search_response["status_code"]
        last_response_text = search_response["text"]
        if search_response["status_code"] == 200:
            payload = json.loads(search_response["text"])
            if payload.get("error") is None and payload.get("data"):
                uploaded_candidate = next(
                    (
                        candidate
                        for candidate in payload["data"]
                        if candidate.get("candidate_id") == doc_id
                    ),
                    None,
                )
                if uploaded_candidate:
                    search_results = payload["data"]
                    break
                append_evidence(
                    log_dir,
                    f"search_attempt_{attempts}_without_uploaded_candidate",
                    {
                        "status_code": search_response["status_code"],
                        "candidate_ids": [
                            candidate.get("candidate_id") for candidate in payload["data"]
                        ],
                    },
                )
        time.sleep(3)

    append_evidence(
        log_dir,
        "search_finished",
        {
            "url": search_url,
            "payload": search_payload,
            "attempts": attempts,
            "last_status_code": last_status_code,
            "last_response": last_response_text,
            "results_found": bool(search_results),
            "uploaded_candidate_found": bool(uploaded_candidate),
        },
    )
    assert uploaded_candidate, (
        "No search result found for the uploaded document after processing. "
        f"doc_id={doc_id}. Last response: {last_response_text}"
    )
    candidate = uploaded_candidate

    append_evidence(
        log_dir,
        "candidate_result",
        {
            "doc_id": doc_id,
            "candidate_id": candidate.get("candidate_id"),
            "file_name": candidate.get("file_name"),
            "score": candidate.get("score"),
            "summary": candidate.get("summary"),
            "reason": candidate.get("reason"),
        },
    )

    assert candidate.get("candidate_id"), "Candidate result must include candidate_id"
    assert candidate.get("summary"), "Candidate result must include a summary"
    assert candidate.get("reason"), "Candidate result must include a reason"
    assert isinstance(candidate.get("score"), (int, float)), (
        "Candidate result must include a numeric score"
    )
    assert candidate["score"] >= 0, "Score must be non-negative"
