"""Tests unitarios – no requieren Docker ni Azure."""
import os
import sys

# Asegurar que los modulos de app son importables desde la raiz del proyecto
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[2] / "app"))

# Activar fake mode antes de importar nada del proyecto
os.environ["USE_FAKE_INTEGRATIONS"] = "true"
os.environ["KEY_VAULT_URL"] = ""
os.environ["BLOB_ACCOUNT_URL"] = ""
os.environ["AZURE_OPENAI_ENDPOINT"] = ""
os.environ["QDRANT_URL"] = "http://localhost:6333"

import math
import pytest


# ── shared/fakes.py ───────────────────────────────────────────────────────────

class TestFakeEmbedding:
    def test_returns_list_of_floats(self):
        from shared.fakes import fake_embedding
        emb = fake_embedding("hello world")
        assert isinstance(emb, list)
        assert all(isinstance(v, float) for v in emb)

    def test_default_dims(self):
        from shared.fakes import fake_embedding
        emb = fake_embedding("test text")
        assert len(emb) == 1536

    def test_deterministic(self):
        from shared.fakes import fake_embedding
        assert fake_embedding("abc") == fake_embedding("abc")

    def test_different_texts_differ(self):
        from shared.fakes import fake_embedding
        assert fake_embedding("text A") != fake_embedding("text B")

    def test_normalized(self):
        from shared.fakes import fake_embedding
        emb = fake_embedding("normalization test")
        norm = math.sqrt(sum(v * v for v in emb))
        assert abs(norm - 1.0) < 1e-6, f"norm={norm} should be 1.0"

    def test_custom_dims(self):
        from shared.fakes import fake_embedding
        emb = fake_embedding("custom dims", dims=512)
        assert len(emb) == 512


# ── shared/config.py ──────────────────────────────────────────────────────────

class TestSettings:
    def test_fake_mode_enabled(self):
        from shared.config import get_settings
        settings = get_settings()
        assert settings.use_fake_integrations is True

    def test_defaults(self):
        from shared.config import get_settings
        s = get_settings()
        assert s.qdrant_collection == "talentrefine-candidates"
        assert s.azure_openai_embedding_deployment == "text-embedding-3-small"
        assert s.azure_openai_chat_deployment == "gpt-4o-mini"
        assert s.fake_embedding_dimensions == 1536

    def test_get_secret_returns_empty_in_fake_mode(self):
        from shared.config import get_secret
        # No debe conectar a Key Vault; debe devolver cadena vacia
        assert get_secret("any-secret") == ""


# ── shared/models.py ──────────────────────────────────────────────────────────

class TestModels:
    def test_candidate_payload_defaults(self):
        from shared.models import CandidatePayload
        p = CandidatePayload(
            candidate_id="abc",
            file_name="cv.pdf",
            contact_info={},
            chunk_text="some text",
        )
        assert p.exp_years == 0
        assert p.location is None

    def test_api_response(self):
        from shared.models import ApiResponse
        r = ApiResponse(data={"key": "value"})
        assert r.error is None
        assert r.data == {"key": "value"}


# ── rag_service/routers/schemas.py ────────────────────────────────────────────

class TestSchemas:
    def test_search_request_defaults(self):
        from rag_service.routers.schemas import SearchRequest
        r = SearchRequest(job_description="Python backend engineer with 5 years")
        assert r.top_k == 5
        assert r.min_exp_years is None
        assert r.required_location is None

    def test_search_request_min_length(self):
        from rag_service.routers.schemas import SearchRequest
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            SearchRequest(job_description="short")

    def test_search_request_full(self):
        from rag_service.routers.schemas import SearchRequest
        r = SearchRequest(
            job_description="Senior DevOps engineer with Kubernetes",
            min_exp_years=3.5,
            required_location="Madrid",
            top_k=10,
        )
        assert r.min_exp_years == 3.5
        assert r.required_location == "Madrid"

    def test_candidate_result_has_justification(self):
        from rag_service.routers.schemas import CandidateResult
        cr = CandidateResult(
            candidate_id="123",
            file_name="cv.pdf",
            score=0.95,
            summary="Senior Python engineer",
            justification="Encaja perfectamente por su experiencia en FastAPI",
            contact_info={"email": "a@b.com"},
            exp_years=5,
            location="Madrid",
        )
        assert cr.justification
        assert not hasattr(cr, "reason")

    def test_search_response_wraps_list(self):
        from rag_service.routers.schemas import CandidateResult, SearchResponse
        sr = SearchResponse(results=[])
        assert sr.results == []


# ── processing_service/services/indexing_service.py ──────────────────────────

class TestIndexingServiceInit:
    def test_instantiation_in_fake_mode(self):
        """En fake mode, IndexingService no debe conectar con Azure en __init__."""
        from processing_service.services.indexing_service import IndexingService
        svc = IndexingService()
        assert svc._use_fake is True

    def test_clean_text(self):
        from processing_service.services.indexing_service import IndexingService
        dirty = "Hello" + chr(0) + "World" + "\n" * 4 + "Extra   spaces"
        clean = IndexingService._clean_text(dirty)
        assert chr(0) not in clean
        assert "\n\n\n" not in clean
        assert "   " not in clean
        assert "   " not in clean


# ── rag_service/services/rag_engine.py ───────────────────────────────────────

class TestRAGEngineInit:
    def test_instantiation_in_fake_mode(self):
        """En fake mode, RAGEngine no debe conectar con Azure en __init__."""
        from rag_service.services.rag_engine import RAGEngine
        engine = RAGEngine()
        assert engine._use_fake is True

    def test_build_metadata_filters_none_when_no_filters(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(job_description="Python engineer with FastAPI experience")
        result = RAGEngine._build_metadata_filters(req)
        assert result is None

    def test_build_metadata_filters_exp_years(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(
            job_description="Python engineer with FastAPI experience",
            min_exp_years=5.0,
        )
        filters = RAGEngine._build_metadata_filters(req)
        assert filters is not None
        assert len(filters.filters) == 1
        assert filters.filters[0].key == "exp_years"

    def test_build_metadata_filters_both(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(
            job_description="Python engineer with FastAPI experience",
            min_exp_years=3.0,
            required_location="Madrid",
        )
        filters = RAGEngine._build_metadata_filters(req)
        assert filters is not None
        assert len(filters.filters) == 2

    def test_payload_matches_filters_pass(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(
            job_description="Python engineer",
            min_exp_years=3.0,
            required_location="Madrid",
        )
        payload = {"exp_years": 5, "location": "Madrid"}
        assert RAGEngine._payload_matches_filters(payload, req) is True

    def test_payload_matches_filters_fail_exp(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(job_description="Python engineer", min_exp_years=10.0)
        payload = {"exp_years": 2, "location": "Madrid"}
        assert RAGEngine._payload_matches_filters(payload, req) is False

    def test_payload_matches_filters_fail_location(self):
        from rag_service.services.rag_engine import RAGEngine
        from rag_service.routers.schemas import SearchRequest
        req = SearchRequest(job_description="Python engineer", required_location="Barcelona")
        payload = {"exp_years": 5, "location": "Madrid"}
        assert RAGEngine._payload_matches_filters(payload, req) is False

    def test_fake_summary(self):
        from rag_service.services.rag_engine import RAGEngine
        s = RAGEngine._fake_summary("word " * 35)
        assert s.startswith("[FAKE]")
        assert "..." in s

    def test_parse_llm_response_valid_json(self):
        from rag_service.services.rag_engine import RAGEngine
        from llama_index.core.schema import NodeWithScore, TextNode
        node = NodeWithScore(
            node=TextNode(
                text="chunk",
                metadata={"candidate_id": "abc-123", "file_name": "cv.pdf",
                          "contact_info": {}, "exp_years": 5, "location": "Madrid"},
            ),
            score=0.9,
        )
        response_text = (
            '[{"candidate_id": "abc-123", "summary": "Good dev", "justification": "Great fit"}]'
        )
        results = RAGEngine._parse_llm_response(response_text, [node])
        assert len(results) == 1
        assert results[0].justification == "Great fit"
        assert results[0].score == 0.9

    def test_parse_llm_response_fallback_on_invalid_json(self):
        from rag_service.services.rag_engine import RAGEngine
        from llama_index.core.schema import NodeWithScore, TextNode
        node = NodeWithScore(
            node=TextNode(
                text="chunk",
                metadata={"candidate_id": "xyz", "file_name": "cv.pdf",
                          "contact_info": {}, "exp_years": 3, "location": None},
            ),
            score=0.7,
        )
        results = RAGEngine._parse_llm_response("not json at all", [node])
        # Debe devolver resultados del fallback
        assert isinstance(results, list)
