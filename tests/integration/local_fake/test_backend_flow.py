"""E2E backend local_fake: upload -> processing automatico -> search."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from .conftest import append_evidence, assert_no_processing_critical_errors, list_candidates, search_candidates


@pytest.mark.integration
def test_backend_services_are_reachable(local_fake_stack: dict[str, Any]) -> None:
    log_dir = local_fake_stack["log_dir"]
    service_urls = local_fake_stack["service_urls"]
    append_evidence(log_dir, "services_reachable", service_urls)
    assert set(service_urls) == {"qdrant", "ingest", "processing", "rag"}


@pytest.mark.integration
def test_upload_accepts_pdf(local_fake_stack: dict[str, Any], upload_pdf) -> None:
    result = upload_pdf(filename="cv_upload_check.pdf")
    assert result["status_code"] == 202, result["text"]
    payload = json.loads(result["text"])
    assert payload.get("error") is None
    assert isinstance(payload.get("data"), list)
    assert payload["data"]
    assert payload["data"][0].get("id")
    assert payload["data"][0].get("status") == "pending"


@pytest.mark.integration
def test_search_schema_validation(local_fake_stack: dict[str, Any]) -> None:
    service_urls = local_fake_stack["service_urls"]
    result = search_candidates(service_urls, job_description="short")
    assert result["status_code"] == 422


@pytest.mark.integration
def test_local_fake_backend_flow(local_fake_stack: dict[str, Any], upload_pdf) -> None:
    log_dir = local_fake_stack["log_dir"]
    service_urls = local_fake_stack["service_urls"]

    upload_result = upload_pdf(
        filename="candidate.pdf",
        text="Senior Python Engineer with FastAPI Docker Qdrant and backend experience",
    )
    assert upload_result["status_code"] == 202, f"Upload failed: {upload_result['text']}"

    upload_payload = json.loads(upload_result["text"])
    assert upload_payload.get("error") is None
    doc_id = upload_payload["data"][0].get("id")
    assert isinstance(doc_id, str) and doc_id

    append_evidence(
        log_dir,
        "document_accepted",
        {"doc_id": doc_id, "upload_payload": upload_payload["data"][0]},
    )

    deadline = time.time() + 180
    attempts = 0
    uploaded_candidate = None
    last_response_text = ""
    last_status_code = None

    while time.time() < deadline:
        attempts += 1
        search_response = search_candidates(
            service_urls,
            job_description="Senior Python developer with FastAPI and backend experience",
            top_k=5,
        )
        last_status_code = search_response["status_code"]
        last_response_text = search_response["text"]
        if last_status_code == 200:
            payload = json.loads(last_response_text)
            results = payload.get("results", [])
            uploaded_candidate = next(
                (candidate for candidate in results if candidate.get("candidate_id") == doc_id),
                None,
            )
            if uploaded_candidate:
                break
            append_evidence(
                log_dir,
                f"search_attempt_{attempts}_without_uploaded_candidate",
                {
                    "status_code": last_status_code,
                    "candidate_ids": [candidate.get("candidate_id") for candidate in results],
                },
            )
        time.sleep(3)

    append_evidence(
        log_dir,
        "search_finished",
        {
            "attempts": attempts,
            "last_status_code": last_status_code,
            "last_response": last_response_text,
            "uploaded_candidate_found": bool(uploaded_candidate),
        },
    )

    assert uploaded_candidate, (
        "No search result found for uploaded document. "
        f"doc_id={doc_id}. Last response: {last_response_text}"
    )
    assert uploaded_candidate.get("candidate_id") == doc_id
    assert uploaded_candidate.get("file_name", "").endswith("/candidate.pdf")
    assert isinstance(uploaded_candidate.get("score"), (int, float))
    assert uploaded_candidate["score"] >= 0
    assert uploaded_candidate.get("summary")
    assert uploaded_candidate.get("justification")

    append_evidence(
        log_dir,
        "candidate_result",
        {
            "doc_id": doc_id,
            "candidate_id": uploaded_candidate.get("candidate_id"),
            "file_name": uploaded_candidate.get("file_name"),
            "score": uploaded_candidate.get("score"),
            "summary": uploaded_candidate.get("summary"),
            "justification": uploaded_candidate.get("justification"),
        },
    )
    assert_no_processing_critical_errors(log_dir)


@pytest.mark.integration
def test_list_candidates_endpoint(local_fake_stack: dict[str, Any], upload_pdf) -> None:
    """GET /search/candidates devuelve candidatos tras la indexación del CV subido."""
    service_urls = local_fake_stack["service_urls"]
    log_dir = local_fake_stack["log_dir"]

    # Ensure at least one candidate is uploaded and indexed
    upload_result = upload_pdf(filename="listtest.pdf", text="Python Backend Engineer FastAPI")
    assert upload_result["status_code"] == 202, f"Upload failed: {upload_result}"

    # Poll the list endpoint until a candidate appears (max 120s)
    deadline = time.time() + 120
    body: dict[str, Any] = {}
    while time.time() < deadline:
        raw = list_candidates(service_urls, timeout=30)
        if raw["status_code"] == 200:
            body = json.loads(raw["text"])
            if body.get("total", 0) > 0:
                break
        time.sleep(5)

    append_evidence(log_dir, "list_candidates_response", body)

    assert body.get("total", 0) > 0, "Expected at least 1 candidate in Qdrant after upload"
    candidates = body.get("candidates", [])
    assert isinstance(candidates, list)
    first = candidates[0]
    assert "candidate_id" in first, "Missing candidate_id in list response"
    assert "name" in first, "Missing name in list response"
    assert "email" in first, "Missing email in list response"
    assert "file_name" in first, "Missing file_name in list response"
