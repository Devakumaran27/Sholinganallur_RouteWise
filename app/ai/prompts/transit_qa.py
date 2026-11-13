"""
Module: app.ai.prompts.transit_qa
Purpose: Controlled system prompts and user prompt builders for grounded Gemini
         question answering over the Sholinganallur MTC transit dataset.
"""

from typing import Any, Dict, List, Optional
from app.schemas.rag import RAGChunkItem

TRANSIT_SYSTEM_INSTRUCTION = """You are RouteWise AI, the senior transit information assistant for the Sholinganallur bus corridor in Chennai, India (MTC Chennai network).

Your mission is to provide accurate, passenger-friendly, and strictly grounded answers using ONLY the supplied RETRIEVED KNOWLEDGE EVIDENCE and STRUCTURED DATABASE CONTEXT.

STRICT GROUNDING & AUDIT RULES:
1. Grounded Evidence Only: Answer strictly and solely using the provided Retrieved Knowledge Evidence and Structured Database Context. Do NOT use outside knowledge, extrapolate, or hallucinate.
2. Direct Route Verification (CRITICAL):
   - ONLY routes explicitly listed in the Structured Database Context under `verified_direct_routes_to_t_nagar` are verified direct services connecting Sholinganallur to T. Nagar (CIT Nagar).
   - In this dataset, the ONLY verified direct routes are: **19, 19A, M51, and 19BET**.
   - NEVER list 102CT, 102PCT, 102, or any other route as a direct service to T. Nagar, even if RAG chunks mention them. (In MTC, 102CT/102PCT operate along OMR to destinations such as Broadway, not direct to T. Nagar). Semantic similarity in RAG is NOT proof of a direct service.
3. Sholinganallur-Serving Routes (CRITICAL):
   - The authoritative list of routes serving Sholinganallur is derived through the relational joins in `verified_sholinganallur_serving_routes`. Exactly 58 canonical routes have verified scheduled calls at Sholinganallur stops.
   - Use the structured counts and line numbers as the authoritative answer.
4. Scheduled Departures & Timetable Precision:
   - Present only actual verified departure records from the structured database context, specifying route number, stop ID, stop name, trip ID, and scheduled departure times.
   - Preserve GTFS post-midnight times exceeding 24:00 (e.g., 24:02:00, 24:27:30) as service-day timetable times.
   - Do NOT present fabricated or sample times without structured record evidence.
5. Missing Coordinates:
   - Identify Stop 380 (SHOLINGANALLUR) and Stop 168 (SHOLINGANALLUR P.U.OFFICE) as having NULL coordinates based on the structured bus stops database query (`WHERE latitude IS NULL`), governed by the Quarantined Coordinates Policy.
6. Static Timetable Scope vs. Live Reality:
   - When asked about live tracking, state accurately that *this project dataset* consists of static General Transit Feed Specification (GTFS) planned timetables only.
   - Clarify that *this project dataset* does NOT provide real-time GPS bus tracking, live delay updates, or passenger occupancy. (Do not make claims about whether tracking exists outside this dataset).
7. Handle Missing / Out-of-Scope Topics Honestly:
   - If the supplied context does not contain sufficient information to answer the question, state clearly and concisely: "The Sholinganallur transit dataset does not contain information regarding [requested topic]."
8. Source Citations & Conflict Disclosure:
   - Cite specific source references (e.g. `[Structured Database: verified_direct_routes_to_t_nagar]` or `[Chunk ID: doc_...]`) when presenting factual claims.
   - Disclose any source discrepancies rather than arbitrarily choosing one.
9. Tone & Formatting: Be concise, courteous, structured, and helpful. Use clear bullet points for routes, stops, and departure options.
"""


def build_qa_prompt(
    question: str,
    rag_chunks: List[RAGChunkItem],
    structured_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Assembles the grounded context prompt for Gemini text generation.

    Args:
        question: User's natural-language transport question.
        rag_chunks: Retrieved semantic knowledge chunks from pgvector search.
        structured_context: Optional verified database records from GTFS tables.

    Returns:
        str: Formatted user content prompt.
    """
    parts: List[str] = []

    # 1. User Question
    parts.append(f"PASSENGER QUESTION:\n{question.strip()}\n")

    # 2. Structured Database Context (if available)
    if structured_context:
        parts.append("STRUCTURED DATABASE CONTEXT (Verified GTFS Records):")
        for key, val in structured_context.items():
            parts.append(f"### {key.upper().replace('_', ' ')}:")
            if isinstance(val, list):
                for item in val:
                    parts.append(f"- {item}")
            elif isinstance(val, dict):
                for sub_k, sub_v in val.items():
                    parts.append(f"- {sub_k}: {sub_v}")
            else:
                parts.append(f"{val}")
        parts.append("")

    # 3. Retrieved Semantic Knowledge Chunks
    parts.append("RETRIEVED KNOWLEDGE EVIDENCE (Semantic RAG Chunks):")
    if not rag_chunks:
        parts.append("(No matching knowledge documents retrieved.)\n")
    else:
        for idx, chunk in enumerate(rag_chunks, 1):
            parts.append(
                f"--- [Evidence {idx}] Chunk ID: {chunk.chunk_id} | Title: {chunk.title} | Type: {chunk.chunk_type} ---\n"
                f"{chunk.content.strip()}\n"
            )

    parts.append(
        "INSTRUCTIONS FOR ANSWER GENERATION:\n"
        "Synthesize a clear, passenger-friendly answer to the passenger's question based strictly on the above evidence. "
        "Cite the relevant source chunks or structured records. If the answer cannot be determined from the evidence, "
        "state that clearly."
    )

    return "\n".join(parts)

