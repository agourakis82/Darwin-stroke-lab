from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchSource:
    title: str
    url: str
    summary: str
    tags: tuple[str, ...]
    source_type: str


DEFAULT_RESEARCH_CORPUS: tuple[ResearchSource, ...] = (
    ResearchSource(
        title="Model Context Protocol Architecture",
        url="https://modelcontextprotocol.io/docs/learn/architecture",
        summary="Explains MCP hosts, clients, servers, transports and the boundary between tools, resources and prompts.",
        tags=("mcp", "architecture", "tooling", "protocol"),
        source_type="specification",
    ),
    ResearchSource(
        title="Model Context Protocol Specification 2025-06-18",
        url="https://modelcontextprotocol.io/specification/2025-06-18",
        summary="Defines the official protocol surface including tools, resources, prompts, roots, sampling and progress notifications.",
        tags=("mcp", "spec", "tools", "resources", "prompts", "progress"),
        source_type="specification",
    ),
    ResearchSource(
        title="OpenAI Agents SDK MCP Guide",
        url="https://openai.github.io/openai-agents-python/mcp/",
        summary="Shows how to connect MCP servers to Agents SDK agents, including stdio and HTTP transports, caching and approvals.",
        tags=("openai", "agents", "mcp", "python"),
        source_type="sdk_doc",
    ),
    ResearchSource(
        title="OpenAI Agents SDK Handoffs Guide",
        url="https://openai.github.io/openai-agents-python/handoffs/",
        summary="Describes manager-first delegation patterns, specialized agents and input filters for handoffs.",
        tags=("openai", "agents", "handoffs", "orchestration"),
        source_type="sdk_doc",
    ),
    ResearchSource(
        title="OpenAI Agents SDK Guardrails Guide",
        url="https://openai.github.io/openai-agents-python/guardrails/",
        summary="Defines input and output guardrails for validating requests and final outputs around agent runs.",
        tags=("openai", "agents", "guardrails", "safety"),
        source_type="sdk_doc",
    ),
    ResearchSource(
        title="OpenAI Agents SDK Tracing Guide",
        url="https://openai.github.io/openai-agents-python/tracing/",
        summary="Explains built-in tracing, spans and observability hooks for agent runs and tool calls.",
        tags=("openai", "agents", "tracing", "observability"),
        source_type="sdk_doc",
    ),
    ResearchSource(
        title="OpenAI Connectors and MCP Guide",
        url="https://developers.openai.com/api/docs/guides/tools-connectors-mcp",
        summary="Covers connectors, remote MCP tools and approval controls for tools exposed to OpenAI models.",
        tags=("openai", "connectors", "mcp", "approvals"),
        source_type="api_doc",
    ),
    ResearchSource(
        title="Practical Guide to Building AI Agents",
        url="https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/",
        summary="Provides production patterns for agent workflows, specialization, evaluation and operational controls.",
        tags=("openai", "agents", "production", "evaluation"),
        source_type="guide",
    ),
    ResearchSource(
        title="ISLES Challenge",
        url="https://www.isles-challenge.org/",
        summary="Official information for ischemic stroke lesion segmentation benchmarks and challenge framing.",
        tags=("stroke", "isles", "benchmark", "segmentation"),
        source_type="dataset",
    ),
    ResearchSource(
        title="AISD Dataset Repository",
        url="https://github.com/GriffinLiang/AISD",
        summary="Public acute ischemic stroke dataset repository used for external validation and cohort building.",
        tags=("stroke", "dataset", "aisd", "validation"),
        source_type="dataset",
    ),
    ResearchSource(
        title="Review of AI for Ischemic Stroke Imaging",
        url="https://pubmed.ncbi.nlm.nih.gov/39747958/",
        summary="Recent review highlighting reporting gaps, external validation deficits and methodological caveats in ischemic stroke imaging AI.",
        tags=("stroke", "review", "validation", "reporting"),
        source_type="literature",
    ),
)


def search_research_corpus(query: str, limit: int = 6) -> list[ResearchSource]:
    tokens = {token.strip().lower() for token in query.split() if token.strip()}
    scored: list[tuple[int, ResearchSource]] = []
    for source in DEFAULT_RESEARCH_CORPUS:
        haystack = " ".join((source.title, source.summary, " ".join(source.tags))).lower()
        score = sum(1 for token in tokens if token in haystack)
        if score > 0:
            scored.append((score, source))
    if not scored:
        return list(DEFAULT_RESEARCH_CORPUS[:limit])
    scored.sort(key=lambda item: (-item[0], item[1].title))
    return [source for _, source in scored[:limit]]


def render_evidence_table(question: str, sources: list[ResearchSource]) -> str:
    lines = [
        f"# Evidence Table\n",
        f"Question: {question}\n",
        "",
        "| Source | Type | Why it matters |",
        "| --- | --- | --- |",
    ]
    for source in sources:
        lines.append(f"| [{source.title}]({source.url}) | {source.source_type} | {source.summary} |")
    return "\n".join(lines) + "\n"


def merge_research_sources(*source_groups: list[ResearchSource], limit: int | None = None) -> list[ResearchSource]:
    merged: list[ResearchSource] = []
    seen_urls: set[str] = set()
    for group in source_groups:
        for source in group:
            if source.url in seen_urls:
                continue
            seen_urls.add(source.url)
            merged.append(source)
            if limit is not None and len(merged) >= limit:
                return merged
    return merged


def recommend_protocol_actions(question: str, sources: list[ResearchSource]) -> list[str]:
    tags = {tag for source in sources for tag in source.tags}
    actions = [
        "Keep PHI and raw study paths on trusted local tooling only.",
        "Persist every benchmark manifest, trace id and artifact path for auditability.",
    ]
    if "benchmark" in tags or "isles" in tags:
        actions.append("Use a fixed train/test or external-validation manifest and preserve the exact case list in artifacts.")
    if "validation" in tags or "review" in tags:
        actions.append("Report external validation, subgroup behavior and failure analysis before claiming superiority.")
    if "mcp" in tags:
        actions.append("Expose manifests, artifacts and safety policy as MCP resources instead of scraping unstructured files.")
    if "agents" in tags:
        actions.append("Prefer manager-first handoffs and add guardrails only at run entry and final output boundaries.")
    if "stroke" in question.lower():
        actions.append("Keep clinical output framed as research support, not diagnostic or regulatory advice.")
    return actions
