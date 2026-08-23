"""Document (PDF/DOCX) source connector (ADR-010).

Unlike the other three connectors in this package (`csv_source.py`,
`postgres_source.py`, `rest_source.py`), a document's text isn't already
tabular — this is the one `sources/` connector that calls an LLM (via
`core.llm.get_llm()`) to structure extracted text into rows, following the
same retry-loop/JSON-parse shape `agents/pipeline/orchestrator.py` already uses for
spec -> pipeline_plan. Same convention as the other connectors otherwise: no
source-level try/except, errors propagate to `agents/pipeline/extractor.py`'s single
catch point.
"""

import json

import pandas as pd

from ai_etl.core.llm import get_llm

MAX_ATTEMPTS = 3

# Cap on how much extracted text goes into the prompt — a long PDF/DOCX
# could otherwise blow past the model's context window or run up token cost
# for little gain (row-structuring past a few thousand words rarely needs
# more context, and truncation is enough to keep this connector usable
# without adding a chunking strategy this ADR doesn't scope).
MAX_TEXT_CHARS = 20_000

DOCUMENT_EXTRACTION_PROMPT = """You are extracting tabular data from a document's text.

Document text:
\"\"\"
{text}
\"\"\"

Identify the tabular data in this text (a table, a list of records, or \
structured line items) and extract it as a JSON array of row objects. Each \
object's keys are the column names; use the same keys for every row.

Respond ONLY with a valid JSON array. No explanation, no markdown code fences.
"""


def load_document(
    path: str,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> pd.DataFrame:
    """Extract tabular data from a PDF or DOCX file into a DataFrame.

    Raises ValueError for unsupported extensions or if the LLM fails to
    produce a valid JSON array of row objects after MAX_ATTEMPTS retries —
    matching rest_source.py's plain-exception style for unexpected shapes.

    Args:
        llm_provider_override / llm_model_override: Sprint 30/gap-closing
            (ADR-031 §5) — this connector's `get_llm()` call is reached from
            `agents/pipeline/extractor.py::extractor_node`, inside the same graph
            run as every other `get_llm()` call site, so it respects the same
            per-`saved_pipeline` override rather than being a silent exception to
            it. `None` (the default) for every caller that doesn't pass it —
            unchanged behavior.
    """
    text = _extract_text(path)
    return _structure_text(text, llm_provider_override, llm_model_override)


def _extract_text(path: str) -> str:
    if path.endswith(".pdf"):
        return _extract_pdf_text(path)
    if path.endswith(".docx"):
        return _extract_docx_text(path)
    raise ValueError(f"Unsupported document type: {path}")


def _extract_pdf_text(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx_text(path: str) -> str:
    import docx

    document = docx.Document(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _structure_text(
    text: str,
    llm_provider_override: str | None = None,
    llm_model_override: str | None = None,
) -> pd.DataFrame:
    llm = get_llm(provider=llm_provider_override, model=llm_model_override)
    prompt = DOCUMENT_EXTRACTION_PROMPT.format(text=text[:MAX_TEXT_CHARS])
    last_error: str | None = None

    for _attempt in range(1, MAX_ATTEMPTS + 1):
        response = llm.invoke(prompt)
        content = str(response.content).strip()

        try:
            records = json.loads(content)
            if not isinstance(records, list):
                raise ValueError("Expected a JSON array of row objects")
            return pd.DataFrame(records)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            prompt += (
                f"\n\nPrevious response was invalid: {e}\nResponse was:\n{content}"
                "\n\nReturn ONLY a valid JSON array of row objects."
            )

    raise ValueError(
        f"Failed to extract structured data from document after {MAX_ATTEMPTS} attempts: {last_error}"
    )
