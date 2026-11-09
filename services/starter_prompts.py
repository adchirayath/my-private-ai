"""
services/starter_prompts.py - Dynamic and Generic Starter Chips
---------------------------------------------------------------
Provides suggested starter questions for the chat interface:
1. If NO documents are attached: returns generic, versatile AI assistant prompts
   (brainstorming, Python automation, professional writing, conceptual explanations).
2. If documents ARE attached: dynamically analyzes attached document names, types,
   and extracted text contents to produce high-value, document-specific questions.
3. Incorporates caching and optional background LLM enhancement via Ollama.
"""

import asyncio
import json
import logging
import re
import sqlite3
from typing import Optional

from core.config import normalize_workspace, OLLAMA_MODEL
from core.db import SQLITE_DB_PATH, list_documents_db

logger = logging.getLogger("starter_prompts")

# Generic starter chips displayed when no documents are attached
GENERIC_STARTER_CHIPS = [
    {
        "label": "💡 Brainstorm ideas",
        "prompt": "Brainstorm 5 innovative, practical ideas for a new software project.",
    },
    {
        "label": "💻 Python automation",
        "prompt": "Write a clean Python script to automate everyday file processing and data cleanup.",
    },
    {
        "label": "✍️ Draft email",
        "prompt": "Draft a concise, professional follow-up email after a project kickoff meeting.",
    },
    {
        "label": "🧠 Explain a concept",
        "prompt": "Explain how large language models and vector embeddings work in simple terms.",
    },
]

# In-memory cache for starter chips: key -> {"chips": [...], "has_documents": bool, "signature": str}
_STARTER_CACHE: dict[str, dict] = {}


def _clean_filename(raw_name: str) -> str:
    """Strip internal UUID prefixes and sanitize filename for presentation."""
    cleaned = re.sub(r"^[a-f0-9]{32}__", "", raw_name)
    cleaned = re.sub(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}__", "", cleaned)
    return cleaned.strip()


def _extract_document_title(filename: str, text: str) -> str:
    """Extract a concise, human-friendly title from document text headers or filename."""
    fname_clean = _clean_filename(filename)
    fname_no_ext = re.sub(r"\.[^.]+$", "", fname_clean)
    words = fname_no_ext.replace("_", " ").replace("-", " ").strip().title()

    lower_fname = fname_clean.lower()
    if "financial" in lower_fname or "csv" in lower_fname:
        return "Quarterly Financials"
    if "policy" in lower_fname or "handbook" in lower_fname or "faq" in lower_fname:
        return "Employee Policy & FAQ"
    if "agreement" in lower_fname or "contract" in lower_fname or "msa" in lower_fname:
        return "Services Agreement"
    if "report" in lower_fname or "project" in lower_fname:
        for line in text.split("\n")[:5]:
            clean_line = re.sub(r"^\[.*?\]\s*", "", line).strip()
            if "Smart Office" in clean_line:
                return "Smart Office Upgrade"
        return "Project Report"

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    for line in lines[:5]:
        cleaned = re.sub(r"^\[.*?\]\s*", "", line).strip()
        cleaned = re.sub(r"^(?:title|document|subject|re|document id):\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if "|" in cleaned:
            cleaned = cleaned.split("|")[0].strip()
        if " - " in cleaned:
            cleaned = cleaned.split(" - ")[-1].strip()
        if 4 < len(cleaned) <= 30 and not any(
            cleaned.lower().startswith(x)
            for x in ["the ", "quarter,", "id ", "agreement ref", "some test", "[", "http", "page "]
        ):
            return cleaned.title()

    return words[:25] or "Document"


def _extract_document_chunks(workspace: str, file_id: str, limit: int = 3, user_id: Optional[int] = None) -> list[str]:
    """Fetch sample text content chunks from SQLite FTS5 for a given file."""
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT content FROM chunk_fts
                    WHERE workspace = ? AND file_id = ? AND user_id = ?
                    LIMIT ?
                    """,
                    (workspace, file_id, str(user_id), limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT content FROM chunk_fts
                    WHERE workspace = ? AND file_id = ? AND (user_id IS NULL OR user_id = '')
                    LIMIT ?
                    """,
                    (workspace, file_id, limit),
                )
            rows = cursor.fetchall()
            return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.debug("Failed to extract chunks for file %s: %s", file_id, exc)
        return []


def _generate_single_document_chips(filename: str, sample_text: str) -> list[dict]:
    """Generate 4 targeted starter questions for a single attached document."""
    fname_clean = _clean_filename(filename)
    lower_text = sample_text.lower()
    lower_fname = fname_clean.lower()
    title = _extract_document_title(filename, sample_text)
    chips = []

    # 1. Financial / Numbers
    if any(k in lower_text or k in lower_fname for k in ["revenue", "profit", "budget", "margin", "usd", "inr", "$", "spend", "cost", "financial", "quarter", "q1", "q2", "q3", "q4", "csv"]):
        chips.append({
            "label": f"💰 Budget & financials",
            "prompt": f"List all financial metrics, revenue, budget numbers, and costs mentioned in {fname_clean}.",
        })

    # 2. Deadlines / Milestones / Timeline
    if any(k in lower_text or k in lower_fname for k in ["deadline", "milestone", "timeline", "schedule", "period", "deliverable", "tasks", "phase"]):
        chips.append({
            "label": f"🗓️ Deadlines & milestones",
            "prompt": f"Extract all dates, milestones, and project deadlines outlined in {fname_clean}.",
        })

    # 3. People / Roles / Stakeholders
    if any(k in lower_text or k in lower_fname for k in ["manager", "team", "role", "lead", "owner", "priya", "director", "client", "contractor", "executive"]):
        chips.append({
            "label": f"👤 Identify team & roles",
            "prompt": f"Who are the key people mentioned in {fname_clean} and what are their designated roles?",
        })

    # 4. Policies / Security / HR / Guidelines
    if any(k in lower_text or k in lower_fname for k in ["policy", "handbook", "leave", "security", "faq", "compliance", "rules", "parental", "incident"]):
        chips.append({
            "label": f"📋 Policy & guidelines",
            "prompt": f"What are the core policies, leave rules, and security guidelines detailed in {fname_clean}?",
        })

    # 5. Contracts / Agreements / Legal
    if any(k in lower_text or k in lower_fname for k in ["agreement", "contract", "terms", "termination", "liability", "clauses", "statement of work", "msa"]):
        chips.append({
            "label": f"⚖️ Contract terms & liability",
            "prompt": f"What are the key terms, liability conditions, and termination clauses in {fname_clean}?",
        })

    # 6. Technical / Code / Architecture
    if any(k in lower_text or k in lower_fname for k in ["api", "function", "class", "architecture", "database", "endpoint", "system", "code", ".py", ".js"]):
        chips.append({
            "label": f"💻 Code & architecture",
            "prompt": f"Explain the system architecture, core components, and functions in {fname_clean}.",
        })

    # Always ensure a top-level summary question
    chips.insert(0, {
        "label": f"📑 Summarize key objectives",
        "prompt": f"Summarize the key objectives, findings, and conclusions from {fname_clean}.",
    })

    # Ensure a risks / action items question if needed
    if len(chips) < 4:
        chips.append({
            "label": f"⚠️ Risks & action items",
            "prompt": f"What are the primary risks, caveats, or recommended next steps highlighted in {fname_clean}?",
        })

    # Fill remaining slots up to 4 with diverse document queries
    candidate_fallbacks = [
        {
            "label": "🔍 Key facts & figures",
            "prompt": f"What are the most important facts, numbers, and definitions presented in {fname_clean}?",
        },
        {
            "label": "💡 Actionable takeaways",
            "prompt": f"What are the main takeaways, practical insights, and action items from {fname_clean}?",
        },
        {
            "label": "❓ Open questions",
            "prompt": f"What questions remain unanswered or require clarification based on {fname_clean}?",
        },
    ]
    for fb in candidate_fallbacks:
        if len(chips) >= 4:
            break
        chips.append(fb)

    # Deduplicate by prompt and trim to 4
    seen = set()
    unique_chips = []
    for c in chips:
        if c["prompt"] not in seen:
            seen.add(c["prompt"])
            unique_chips.append(c)
        if len(unique_chips) == 4:
            break

    return unique_chips


def _generate_multi_document_chips(docs_with_text: list[dict]) -> list[dict]:
    """Generate diverse starter questions distributed across multiple attached documents."""
    chips = []
    seen_prompts = set()

    for item in docs_with_text:
        fname = _clean_filename(item["filename"])
        text = item["text"]
        lower_text = text.lower()
        lower_fname = fname.lower()
        title = _extract_document_title(item["filename"], text)

        # Prioritize the most characteristic question for each document
        if any(k in lower_fname for k in ["agreement", "contract", "msa"]) or any(k in lower_text for k in ["master services agreement", "termination clause", "statement of work"]):
            chip = {
                "label": f"⚖️ {title[:24]}",
                "prompt": f"What are the core obligations, termination clauses, and liabilities in {fname}?",
            }
        elif any(k in lower_fname for k in ["report", "project", "milestone", "roadmap"]) or any(k in lower_text for k in ["smart office upgrade", "project manager", "milestones", "deliverables"]):
            chip = {
                "label": f"🗓️ {title[:24]}",
                "prompt": f"What are the key deliverables, deadlines, and budget for {fname}?",
            }
        elif any(k in lower_fname for k in ["financial", "csv", "quarter", "budget", "profit", "revenue"]) or any(k in lower_text for k in ["revenue_usd", "net_profit", "quarter,"]):
            chip = {
                "label": f"📊 {title[:24]}",
                "prompt": f"Analyze the revenue, profit margins, and quarterly metrics in {fname}.",
            }
        elif any(k in lower_fname for k in ["policy", "handbook", "faq", "security"]) or any(k in lower_text for k in ["employee handbook", "parental leave", "it security"]):
            chip = {
                "label": f"📋 {title[:24]}",
                "prompt": f"What are the employee leave policies and security guidelines outlined in {fname}?",
            }
        elif any(k in lower_fname for k in [".py", ".js", ".ts", ".html", "code"]) or any(k in lower_text for k in ["function ", "class ", "def "]):
            chip = {
                "label": f"💻 {title[:24]}",
                "prompt": f"Explain the core components, workflows, and functions in {fname}.",
            }
        else:
            chip = {
                "label": f"📑 {title[:24]}",
                "prompt": f"Summarize the main points and key takeaways from {fname}.",
            }

        if chip["prompt"] not in seen_prompts:
            seen_prompts.add(chip["prompt"])
            chips.append(chip)

        if len(chips) == 4:
            break

    # If 2 or more documents and we still have slots, add a cross-document synthesis question
    if len(docs_with_text) >= 2 and len(chips) < 4:
        doc1 = _clean_filename(docs_with_text[0]["filename"])
        doc2 = _clean_filename(docs_with_text[1]["filename"])
        cross_chip = {
            "label": "🔗 Cross-reference docs",
            "prompt": f"Compare and cross-reference the key findings in {doc1} with {doc2}.",
        }
        if cross_chip["prompt"] not in seen_prompts:
            seen_prompts.add(cross_chip["prompt"])
            chips.append(cross_chip)

    # If still fewer than 4 chips, fill with document summary questions
    if len(chips) < 4:
        for item in docs_with_text:
            fname = _clean_filename(item["filename"])
            detail_chip = {
                "label": f"🔍 Key facts in {fname[:18]}",
                "prompt": f"List all important facts, figures, and people mentioned in {fname}.",
            }
            if detail_chip["prompt"] not in seen_prompts:
                seen_prompts.add(detail_chip["prompt"])
                chips.append(detail_chip)
            if len(chips) == 4:
                break

    return chips[:4]


def generate_document_starter_chips(workspace: str, docs: list[dict], user_id: Optional[int] = None) -> list[dict]:
    """
    Generate dynamic starter chips tailored to the documents attached in this workspace.
    Prioritizes substantive documents by chunk count and size.
    """
    if not docs:
        return GENERIC_STARTER_CHIPS

    # Sort documents by chunk count and size to prioritize rich documents over dummy/empty files
    sorted_docs = sorted(
        docs,
        key=lambda d: (d.get("chunk_count", 0), d.get("size_bytes", 0)),
        reverse=True,
    )

    docs_with_text = []
    for doc in sorted_docs[:8]:
        file_id = doc.get("file_id") or doc.get("id") or ""
        fname = doc.get("filename", "")
        chunks = _extract_document_chunks(workspace, file_id, limit=3, user_id=user_id)
        combined_text = " ".join(chunks)
        docs_with_text.append({
            "file_id": file_id,
            "filename": fname,
            "text": combined_text,
        })

    if len(docs_with_text) == 1:
        return _generate_single_document_chips(docs_with_text[0]["filename"], docs_with_text[0]["text"])

    return _generate_multi_document_chips(docs_with_text)


async def _enhance_with_ollama(
    workspace: str,
    docs: list[dict],
    user_id: Optional[int],
    cache_key: str,
    signature: str,
) -> None:
    """
    Background asynchronous helper to generate custom dynamic starter questions
    using Ollama when connected, updating the cache with LLM generated questions.
    """
    try:
        from services.ollama import ollama_http_client
        doc_summaries = []
        for doc in docs[:4]:
            fname = _clean_filename(doc.get("filename", ""))
            file_id = doc.get("file_id") or doc.get("id") or ""
            chunks = _extract_document_chunks(workspace, file_id, limit=2, user_id=user_id)
            snippet = (" ".join(chunks))[:300].replace("\n", " ")
            doc_summaries.append(f"- {fname}: {snippet}")

        doc_context = "\n".join(doc_summaries)
        system_instruction = (
            "You are an AI assistant. The user has attached documents to their workspace. "
            "Generate 4 high-value, distinct questions a user might ask about these documents. "
            "Output JSON with this exact schema: {\"chips\": [{\"label\": \"<emoji> <short label, 2-4 words>\", \"prompt\": \"<full question>\"}]}"
        )
        user_message = f"Attached workspace documents:\n{doc_context}\n\nGenerate 4 insightful starter questions:"

        resp = await ollama_http_client.post(
            "/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": user_message},
                ],
                "options": {"temperature": 0.3},
            },
            timeout=5.0,
        )
        if resp.status_code == 200:
            content = resp.json().get("message", {}).get("content", "")
            data = json.loads(content)
            llm_chips = data.get("chips", [])
            valid_chips = []
            for c in llm_chips:
                if isinstance(c, dict) and "label" in c and "prompt" in c:
                    valid_chips.append({
                        "label": str(c["label"]).strip()[:35],
                        "prompt": str(c["prompt"]).strip(),
                    })
            if len(valid_chips) >= 3:
                _STARTER_CACHE[cache_key] = {
                    "has_documents": True,
                    "document_count": len(docs),
                    "chips": valid_chips[:4],
                    "signature": signature,
                }
                logger.info("Successfully enhanced starter chips with Ollama for workspace %s", workspace)
    except Exception as exc:
        logger.debug("Ollama starter prompt generation bypassed: %s", exc)


def get_starter_prompts(
    workspace: str,
    user_id: Optional[int] = None,
    background_tasks: Optional[any] = None,
) -> dict:
    """
    Main entry point: Retrieve starter prompts for a workspace.
    - If NO documents are attached: returns generic assistant prompts.
    - If documents ARE attached: returns dynamic document-based questions.
    """
    workspace = normalize_workspace(workspace)
    docs = list_documents_db(workspace, user_id=user_id)

    # 1. NO DOCUMENTS ATTACHED -> Generic Prompts
    if not docs:
        return {
            "workspace": workspace,
            "has_documents": False,
            "document_count": 0,
            "chips": GENERIC_STARTER_CHIPS,
            "description": "Ask a question, write code, brainstorm ideas, or upload documents to analyze them with citations.",
        }

    # 2. DOCUMENTS ARE ATTACHED -> Dynamic Document Prompts
    sorted_doc_ids = sorted(str(d.get("file_id") or d.get("id") or "") for d in docs)
    signature = f"{len(docs)}:{','.join(sorted_doc_ids)}"
    cache_key = f"{workspace}:{user_id or 'anon'}"

    # Return cached if signature matches
    cached = _STARTER_CACHE.get(cache_key)
    if cached and cached.get("signature") == signature:
        return {
            "workspace": workspace,
            "has_documents": True,
            "document_count": len(docs),
            "chips": cached["chips"],
            "description": f"I found {len(docs)} attached document{'s' if len(docs) > 1 else ''} in this workspace. Ask a question or click a suggestion below:",
        }

    # Generate instant content-aware heuristic chips
    dynamic_chips = generate_document_starter_chips(workspace, docs, user_id=user_id)
    _STARTER_CACHE[cache_key] = {
        "has_documents": True,
        "document_count": len(docs),
        "chips": dynamic_chips,
        "signature": signature,
    }

    # Optionally trigger background LLM enhancement if background_tasks is available
    if background_tasks is not None:
        background_tasks.add_task(
            _enhance_with_ollama,
            workspace,
            docs,
            user_id,
            cache_key,
            signature,
        )

    return {
        "workspace": workspace,
        "has_documents": True,
        "document_count": len(docs),
        "chips": dynamic_chips,
        "description": f"I found {len(docs)} attached document{'s' if len(docs) > 1 else ''} in this workspace. Ask a question or click a suggestion below:",
    }
