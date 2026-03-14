from __future__ import annotations

import os
import re

from pydantic import BaseModel, ConfigDict, Field

from sounio_stroke_lab.research_corpus import ResearchSource, search_research_corpus

try:  # pragma: no cover - networked path is optional in tests
    from agents import Agent, Runner, RunConfig, WebSearchTool
except Exception:  # pragma: no cover - fallback path
    Agent = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    RunConfig = None  # type: ignore[assignment]
    WebSearchTool = None  # type: ignore[assignment]


ABSOLUTE_PATH = re.compile(r"(/[^ \n\t]+|[A-Za-z]:\\\\[^ \n\t]+)")
IMAGE_SUFFIX = re.compile(r"\b[^ \n\t]+\.(dcm|nii|nii\.gz|npy|npz)\b", re.IGNORECASE)
PATIENT_TOKEN = re.compile(r"\b(patient|mrn|dob|accession)\b[^ \n\t]*", re.IGNORECASE)


class PublicResearchSource(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    title: str
    url: str
    summary: str
    source_type: str = "public_web"


class PublicResearchBundle(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    mode: str
    query: str
    executive_summary: str = ""
    claim_gaps: list[str] = Field(default_factory=list)
    sources: list[PublicResearchSource] = Field(default_factory=list)


def sanitize_public_research_query(question: str) -> str:
    sanitized = ABSOLUTE_PATH.sub("[REDACTED_PATH]", question)
    sanitized = IMAGE_SUFFIX.sub("[REDACTED_IMAGE_REF]", sanitized)
    sanitized = PATIENT_TOKEN.sub("[REDACTED_IDENTIFIER]", sanitized)
    return sanitized


def public_research_available() -> bool:
    return Agent is not None and Runner is not None and RunConfig is not None and WebSearchTool is not None and bool(os.getenv("OPENAI_API_KEY"))


def fallback_public_research(question: str, limit: int = 5, note: str | None = None) -> PublicResearchBundle:
    sanitized_question = sanitize_public_research_query(question)
    fallback_sources = search_research_corpus(sanitized_question, limit=limit)
    claim_gaps = ["Live public web research is unavailable; falling back to the curated local corpus."]
    if note:
        claim_gaps.append(note)
    return PublicResearchBundle(
        mode="fallback_local_corpus",
        query=sanitized_question,
        executive_summary="Used the curated local research corpus because live public web search was unavailable.",
        claim_gaps=claim_gaps,
        sources=[
            PublicResearchSource(
                title=source.title,
                url=source.url,
                summary=source.summary,
                source_type=source.source_type,
            )
            for source in fallback_sources
        ],
    )


def search_public_research(
    question: str,
    *,
    limit: int = 5,
    model: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
) -> PublicResearchBundle:
    if not public_research_available():  # pragma: no cover - networked path
        return fallback_public_research(question, limit=limit)

    sanitized_question = sanitize_public_research_query(question)
    scout = Agent(
        name="LiteratureScout",
        model=model or os.getenv("DARWIN_OPENAI_MODEL", "gpt-5"),
        instructions=(
            "Use web search to find recent, primary or official sources relevant to the question. "
            "Prefer official documentation, challenge pages, repositories and primary literature. "
            "Return concise summaries, keep source URLs explicit, and do not invent citations."
        ),
        tools=[WebSearchTool(search_context_size="high")],
        output_type=PublicResearchBundle,
    )
    prompt = (
        f"Question: {sanitized_question}\n\n"
        f"Return at most {limit} sources. "
        "Focus on information that strengthens benchmark protocol, multi-agent architecture, MCP safety, "
        "or ischemic stroke imaging evaluation. "
        "Assume any local file paths or patient-like identifiers were already redacted and must stay redacted."
    )
    try:  # pragma: no cover - networked path
        result = Runner.run_sync(
            scout,
            prompt,
            max_turns=6,
            run_config=RunConfig(
                workflow_name="Darwin Public Research",
                trace_id=trace_id,
                group_id=run_id,
                trace_metadata={"run_id": run_id or "", "question": sanitized_question[:200]},
            ),
        )
        bundle = result.final_output_as(PublicResearchBundle)
        deduped: list[PublicResearchSource] = []
        seen_urls: set[str] = set()
        for source in bundle.sources:
            if source.url in seen_urls:
                continue
            seen_urls.add(source.url)
            deduped.append(source)
            if len(deduped) >= limit:
                break
        return bundle.model_copy(update={"mode": "live_public_web", "query": sanitized_question, "sources": deduped})
    except Exception as exc:  # pragma: no cover - networked path
        return fallback_public_research(question, limit=limit, note=f"Live public web research failed: {exc}")


def bundle_to_research_sources(bundle: PublicResearchBundle) -> list[ResearchSource]:
    return [
        ResearchSource(
            title=source.title,
            url=source.url,
            summary=source.summary,
            tags=("public_web", bundle.mode),
            source_type=source.source_type,
        )
        for source in bundle.sources
    ]
