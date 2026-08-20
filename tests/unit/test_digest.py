"""Unit tests for services/digest.py — briefing formatting (Sprint 14, ADR-018)."""

from ai_etl.core.drift import DriftMetric
from ai_etl.services.digest import build_digest

_ZERO_TOKENS = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _finding(
    name: str = "rows_loaded", previous: float = 1000.0, current: float = 1500.0
) -> DriftMetric:
    return {
        "name": name,
        "previous": previous,
        "current": current,
        "pct_change": 50.0,
        "threshold_pct": 20.0,
        "triggered": True,
    }


def _advisor_result() -> dict:
    return {
        "recommendations": [
            {
                "action": "Investigar aumento de volume",
                "rationale": "rows_loaded subiu 50%",
                "priority": "high",
                "expected_impact": "Evitar sobrecarga do destino",
            }
        ],
        "summary": "O volume de dados cresceu significativamente nesta execução.",
        "error": None,
        "tokens": dict(_ZERO_TOKENS),
    }


def test_build_digest_includes_pipeline_name_in_subject() -> None:
    digest = build_digest(
        pipeline_name="Nightly Postgres sync",
        business_question="Quais produtos mais vendem?",
        run_id="run-123",
        findings=[_finding()],
        advisor_result=_advisor_result(),
    )
    assert "Nightly Postgres sync" in digest["subject"]


def test_build_digest_text_includes_metric_and_summary() -> None:
    digest = build_digest(
        pipeline_name="Nightly Postgres sync",
        business_question="Quais produtos mais vendem?",
        run_id="run-123",
        findings=[_finding()],
        advisor_result=_advisor_result(),
    )
    assert "rows_loaded" in digest["text"]
    assert "1000" in digest["text"]
    assert "1500" in digest["text"]
    assert "cresceu significativamente" in digest["text"]
    assert "run-123" in digest["text"]


def test_build_digest_html_includes_recommendation() -> None:
    digest = build_digest(
        pipeline_name="Pipeline X",
        business_question="",
        run_id="run-456",
        findings=[_finding()],
        advisor_result=_advisor_result(),
    )
    assert "Investigar aumento de volume" in digest["html"]
    assert "HIGH" in digest["html"]


def test_build_digest_slack_blocks_have_expected_shape() -> None:
    digest = build_digest(
        pipeline_name="Pipeline X",
        business_question="",
        run_id="run-456",
        findings=[_finding()],
        advisor_result=_advisor_result(),
    )
    blocks = digest["slack_blocks"]
    assert blocks[0]["type"] == "header"
    assert "Pipeline X" in blocks[0]["text"]["text"]
    assert any("rows_loaded" in b.get("text", {}).get("text", "") for b in blocks)


def test_build_digest_handles_empty_recommendations() -> None:
    advisor_result = {
        "recommendations": [],
        "summary": "Sem recomendações desta vez.",
        "error": None,
        "tokens": dict(_ZERO_TOKENS),
    }
    digest = build_digest(
        pipeline_name="Pipeline X",
        business_question="",
        run_id="run-789",
        findings=[_finding()],
        advisor_result=advisor_result,
    )
    assert "Nenhuma recomendação disponível" in digest["text"]


def test_build_digest_formats_zero_previous_finding_without_crashing() -> None:
    finding: DriftMetric = {
        "name": "rows_loaded",
        "previous": 0.0,
        "current": 500.0,
        "pct_change": None,
        "threshold_pct": 20.0,
        "triggered": True,
    }
    digest = build_digest(
        pipeline_name="Pipeline X",
        business_question="",
        run_id="run-000",
        findings=[finding],
        advisor_result=_advisor_result(),
    )
    assert "novo valor" in digest["text"]
