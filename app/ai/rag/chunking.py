"""
Module: app.ai.rag.chunking
Purpose: Structure-aware, semantic document chunking and reconstruction engine
         for Sholinganallur_RouteWise. Implements deterministic character offset
         source-tracking, heading preservation, atomic preservation of short documents,
         and structured variant-level splitting with context preservation and overlap.
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.ai.rag.token_counter import count_tokens, get_token_counter_metadata

logger = logging.getLogger("chunking")

PIPELINE_VERSION = "v1.0.0-chunking"
MAX_TOKEN_CEILING = 600
TARGET_MIN_TOKENS = 300
TARGET_MAX_TOKENS = 500
MIN_TOKEN_FLOOR = 100


def compute_sha256(text: str) -> str:
    """Computes deterministic SHA-256 hex digest of string content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reconstruct_document(chunks: List[Dict[str, Any]]) -> str:
    """
    Reconstructs the original parent document content from its ordered chunks.
    
    Guarantees:
    - Reads chunks in deterministic order (by chunk_index).
    - Removes only explicitly recorded injected context headers and overlap text.
    - Preserves 100% of original whitespace, newlines, punctuation, Markdown formatting,
      Unicode characters, and line endings.
    - Performs zero arbitrary regex stripping or lossy normalization.
    - Verifies continuous source coverage without gaps or duplicate source ranges.
    """
    sorted_chunks = sorted(chunks, key=lambda c: c["chunk_index"])
    reconstructed_parts: List[str] = []
    expected_start = 0

    for c in sorted_chunks:
        start = c["source_start_char"]
        end = c["source_end_char"]

        if start != expected_start:
            raise ValueError(
                f"Source range discontinuity in chunk {c['chunk_id']}: "
                f"expected start {expected_start}, but got {start}"
            )
        expected_start = end

        chunk_text = c["chunk_text"]
        injected_hdr = c.get("injected_context_header", "")
        overlap = c.get("overlap_text", "")
        prefix = injected_hdr + overlap

        if prefix:
            if not chunk_text.startswith(prefix):
                raise ValueError(
                    f"Chunk {c['chunk_id']} text does not start with explicitly "
                    f"recorded injected prefix (len={len(prefix)})."
                )
            source_slice = chunk_text[len(prefix):]
        else:
            source_slice = chunk_text

        reconstructed_parts.append(source_slice)

    return "".join(reconstructed_parts)


class DocumentChunker:
    """
    Structure-aware semantic chunker with deterministic character offset tracking
    and 100% byte-for-byte parent document reconstruction capability.
    """

    def __init__(self, pipeline_version: str = PIPELINE_VERSION):
        self.pipeline_version = pipeline_version
        self.token_counter_meta = get_token_counter_metadata()

    def chunk_document(self, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Routes a document to the appropriate chunking strategy based on
        document type and token size.
        """
        doc_type = doc.get("document_type", "unknown")
        content = doc.get("content", "")
        token_count = count_tokens(content)

        if doc_type == "stop_profile":
            return self._chunk_stop_profile(doc, token_count)
        elif doc_type == "route_profile":
            return self._chunk_route_profile(doc, token_count)
        elif doc_type == "corridor_guide":
            return self._chunk_corridor_guide(doc, token_count)
        elif doc_type == "system_governance":
            return self._chunk_system_governance(doc, token_count)
        else:
            return self._chunk_generic(doc, token_count)

    def _create_chunk_record(
        self,
        doc: Dict[str, Any],
        chunk_index: int,
        title: str,
        section_heading: str,
        chunk_text: str,
        source_start_char: int,
        source_end_char: int,
        injected_context_header: str = "",
        overlap_text: str = "",
        is_atomic: bool = False,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Creates a standardized, validated chunk dictionary with all required schema fields."""
        doc_id = doc["document_id"]
        chunk_id = f"{doc_id}_c{chunk_index:03d}"
        tokens = count_tokens(chunk_text)
        chunk_hash = compute_sha256(chunk_text)
        has_overlap = len(overlap_text) > 0
        overlap_tokens = count_tokens(overlap_text) if has_overlap else 0

        meta: Dict[str, Any] = {
            "is_atomic": is_atomic,
            "has_overlap": has_overlap,
            "overlap_tokens": overlap_tokens,
            "source_start_char": source_start_char,
            "source_end_char": source_end_char,
            "injected_context_header": injected_context_header,
            "overlap_text": overlap_text,
            "source_tokens": count_tokens(doc.get("content", "")),
        }
        if doc.get("metadata"):
            meta.update(doc["metadata"])
        if extra_metadata:
            meta.update(extra_metadata)

        return {
            "chunk_id": chunk_id,
            "document_id": doc_id,
            "chunk_index": chunk_index,
            "document_type": doc.get("document_type", "unknown"),
            "title": title,
            "source_dataset": doc.get("source_file", doc.get("source_dataset", "unknown")),
            "dataset_version": doc.get("dataset_version", "v1.0.0-sholinganallur"),
            "section_heading": section_heading,
            "chunk_text": chunk_text,
            "chunk_hash": chunk_hash,
            "token_count": tokens,
            "pipeline_version": self.pipeline_version,
            "source_start_char": source_start_char,
            "source_end_char": source_end_char,
            "injected_context_header": injected_context_header,
            "overlap_text": overlap_text,
            "has_overlap": has_overlap,
            "overlap_tokens": overlap_tokens,
            "metadata": meta,
        }

    def _chunk_stop_profile(self, doc: Dict[str, Any], total_tokens: int) -> List[Dict[str, Any]]:
        """
        Stop profiles (~275 to 450 tokens) are atomic discrete entities.
        Preserves 1 Document = 1 Atomic Chunk with 0% overlap and complete source range.
        """
        content = doc["content"]
        chunk = self._create_chunk_record(
            doc=doc,
            chunk_index=0,
            title=doc["title"],
            section_heading="Bus Stop Directory & Transit Profile",
            chunk_text=content,
            source_start_char=0,
            source_end_char=len(content),
            injected_context_header="",
            overlap_text="",
            is_atomic=True,
        )
        return [chunk]

    def _chunk_route_profile(self, doc: Dict[str, Any], total_tokens: int) -> List[Dict[str, Any]]:
        """
        Route profiles:
        - Small routes (<= 560 tokens) remain 1 atomic chunk without splitting or overlap.
        - Large multi-variant routes (> 560 tokens) are split along ### Variant Route ID boundaries
          with deterministic source offset tracking and 1-variant overlap.
        """
        content = doc["content"]
        if total_tokens <= 560:
            return [
                self._create_chunk_record(
                    doc=doc,
                    chunk_index=0,
                    title=doc["title"],
                    section_heading="Complete Route Profile & Service Variants",
                    chunk_text=content,
                    source_start_char=0,
                    source_end_char=len(content),
                    injected_context_header="",
                    overlap_text="",
                    is_atomic=True,
                )
            ]

        title_line = content.splitlines()[0]
        variant_starts = [m.start() for m in re.finditer(r"### Variant Route ID:", content)]
        n_variants = len(variant_starts)

        chunks = []

        # Chunk 0: from 0 up to variant cut_1
        cut_prev = 0
        cut_cur = 1
        while cut_cur < n_variants:
            cand = content[:variant_starts[cut_cur]]
            if count_tokens(cand) > TARGET_MAX_TOKENS:
                break
            cut_prev = cut_cur
            cut_cur += 1

        end_0 = variant_starts[cut_prev]
        c0_text = content[:end_0]
        chunks.append(
            self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=f"{doc['title']} - Overview & Variants Part 1",
                section_heading="Overview & Initial Operational Variants",
                chunk_text=c0_text,
                source_start_char=0,
                source_end_char=end_0,
                injected_context_header="",
                overlap_text="",
                is_atomic=False,
                extra_metadata={"variant_count_in_chunk": cut_prev},
            )
        )

        # Subsequent chunks
        part_num = 2
        cur_start_idx = cut_prev

        while cur_start_idx < n_variants:
            overlap_var = content[variant_starts[cur_start_idx - 1] : variant_starts[cur_start_idx]]
            injected_hdr = (
                f"{title_line} (Continued)\n\n"
                f"## Operational Service Variants (Continued - Part {part_num})\n\n"
            )
            prefix = injected_hdr + overlap_var

            cand_idx = cur_start_idx + 1
            best_cand_idx = cur_start_idx + 1
            while cand_idx <= n_variants:
                slice_end = variant_starts[cand_idx] if cand_idx < n_variants else len(content)
                cand_text = prefix + content[variant_starts[cur_start_idx] : slice_end]
                if count_tokens(cand_text) > TARGET_MAX_TOKENS and (cand_idx - cur_start_idx) > 1:
                    break
                best_cand_idx = cand_idx
                cand_idx += 1

            end_pos = variant_starts[best_cand_idx] if best_cand_idx < n_variants else len(content)
            chunk_slice = content[variant_starts[cur_start_idx] : end_pos]
            chunk_text = prefix + chunk_slice

            chunks.append(
                self._create_chunk_record(
                    doc=doc,
                    chunk_index=part_num - 1,
                    title=f"{doc['title']} - Variants Part {part_num}",
                    section_heading=f"Operational Variants (Part {part_num})",
                    chunk_text=chunk_text,
                    source_start_char=variant_starts[cur_start_idx],
                    source_end_char=end_pos,
                    injected_context_header=injected_hdr,
                    overlap_text=overlap_var,
                    is_atomic=False,
                    extra_metadata={"variant_count_in_chunk": best_cand_idx - cur_start_idx},
                )
            )

            cur_start_idx = best_cand_idx
            part_num += 1

        return chunks

    def _chunk_corridor_guide(self, doc: Dict[str, Any], total_tokens: int) -> List[Dict[str, Any]]:
        """
        Splits Corridor Guide (~883 tokens) into 2 logical, semantically coherent chunks
        with deterministic source character range tracking:
        - Chunk 0: Overview, Operating Routes, Journey Duration, and Boarding/Destination Hubs (0 to split_pos).
        - Chunk 1: Injected Context Header + 30 Intermediate Stops/Landmarks & Commuter Advisory (split_pos to end).
        """
        content = doc["content"]
        title_line = content.splitlines()[0]

        marker = "## Intermediate Corridor Path & Landmarks"
        split_pos = content.find(marker)

        if split_pos != -1:
            slice_0 = content[:split_pos]
            slice_1 = content[split_pos:]

            injected_hdr_1 = f"{title_line} (Continued)\n\n"
            c1_text = injected_hdr_1 + slice_1

            c0 = self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=f"{doc['title']} - Routes, Schedules & Boarding Hubs",
                section_heading="Corridor Overview, Operating Routes & Terminals",
                chunk_text=slice_0,
                source_start_char=0,
                source_end_char=split_pos,
                injected_context_header="",
                overlap_text="",
                is_atomic=False,
            )
            c1 = self._create_chunk_record(
                doc=doc,
                chunk_index=1,
                title=f"{doc['title']} - Waypoints, Landmarks & Commuter Advisory",
                section_heading="Intermediate Landmarks & Commuter Advisory",
                chunk_text=c1_text,
                source_start_char=split_pos,
                source_end_char=len(content),
                injected_context_header=injected_hdr_1,
                overlap_text="",
                is_atomic=False,
            )
            return [c0, c1]

        # Fallback if marker not found
        return [
            self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=doc["title"],
                section_heading="Corridor Guide",
                chunk_text=content,
                source_start_char=0,
                source_end_char=len(content),
                injected_context_header="",
                overlap_text="",
                is_atomic=True,
            )
        ]

    def _chunk_system_governance(self, doc: Dict[str, Any], total_tokens: int) -> List[Dict[str, Any]]:
        """
        Splits System Governance document into 3 sequential, semantically distinct chunks
        with deterministic source character range tracking:
        - Chunk 0: Authority, Scope, Provenance Hierarchy (0 to cut1).
        - Chunk 1: Injected Context Header + Core Operational Boundaries & Relational Stop Matching (cut1 to cut2).
        - Chunk 2: Injected Context Header + Quarantined Coordinates, GTFS Times & Grounding Rules (cut2 to end).
        """
        content = doc["content"]
        title_line = content.splitlines()[0]

        marker1 = "## Core Operational Boundaries & Disclaimers"
        marker2 = "## Quarantined Coordinates Policy"

        cut1 = content.find(marker1)
        cut2 = content.find(marker2)

        if cut1 != -1 and cut2 != -1:
            slice_0 = content[:cut1]
            slice_1 = content[cut1:cut2]
            slice_2 = content[cut2:]

            injected_hdr = f"{title_line} (Continued)\n\n"

            c0 = self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=f"{doc['title']} - Authority & Dataset Provenance",
                section_heading="System Authority & Dataset Scope",
                chunk_text=slice_0,
                source_start_char=0,
                source_end_char=cut1,
                injected_context_header="",
                overlap_text="",
                is_atomic=False,
            )
            c1 = self._create_chunk_record(
                doc=doc,
                chunk_index=1,
                title=f"{doc['title']} - Operational Boundaries & Stop Matching Methodology",
                section_heading="Core Operational Boundaries & Disclaimers",
                chunk_text=injected_hdr + slice_1,
                source_start_char=cut1,
                source_end_char=cut2,
                injected_context_header=injected_hdr,
                overlap_text="",
                is_atomic=False,
            )
            c2 = self._create_chunk_record(
                doc=doc,
                chunk_index=2,
                title=f"{doc['title']} - Quarantined Coordinates & Timetable Standards",
                section_heading="Coordinate Policies, Time Notation & Grounding Rules",
                chunk_text=injected_hdr + slice_2,
                source_start_char=cut2,
                source_end_char=len(content),
                injected_context_header=injected_hdr,
                overlap_text="",
                is_atomic=False,
            )
            return [c0, c1, c2]

        return [
            self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=doc["title"],
                section_heading="Governance Document",
                chunk_text=content,
                source_start_char=0,
                source_end_char=len(content),
                injected_context_header="",
                overlap_text="",
                is_atomic=True,
            )
        ]

    def _chunk_generic(self, doc: Dict[str, Any], total_tokens: int) -> List[Dict[str, Any]]:
        """Fallback chunker for unclassified documents."""
        content = doc.get("content", "")
        return [
            self._create_chunk_record(
                doc=doc,
                chunk_index=0,
                title=doc.get("title", "Document"),
                section_heading="General Content",
                chunk_text=content,
                source_start_char=0,
                source_end_char=len(content),
                injected_context_header="",
                overlap_text="",
                is_atomic=True,
            )
        ]

    def chunk_all_documents(self, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Processes all documents deterministically, sorted by document_id."""
        sorted_docs = sorted(docs, key=lambda d: d["document_id"])
        all_chunks: List[Dict[str, Any]] = []

        for doc in sorted_docs:
            chunks = self.chunk_document(doc)
            all_chunks.extend(chunks)

        logger.info(
            "Chunking complete: %d source documents transformed into %d chunks.",
            len(docs),
            len(all_chunks),
        )
        return all_chunks

    def save_chunks(
        self,
        chunks: List[Dict[str, Any]],
        output_dir: Path | str = Path("data/chunks"),
    ) -> Dict[str, Any]:
        """
        Saves each chunk as an individual JSON file in data/chunks/
        and produces a master chunks_manifest.json catalog.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        manifest = []
        for chunk in chunks:
            chunk_file = out_path / f"{chunk['chunk_id']}.json"
            with open(chunk_file, "w", encoding="utf-8") as f:
                json.dump(chunk, f, indent=2, ensure_ascii=False)

            manifest.append({
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "chunk_index": chunk["chunk_index"],
                "document_type": chunk["document_type"],
                "title": chunk["title"],
                "source_dataset": chunk["source_dataset"],
                "dataset_version": chunk["dataset_version"],
                "section_heading": chunk["section_heading"],
                "chunk_hash": chunk["chunk_hash"],
                "token_count": chunk["token_count"],
                "content_length_chars": len(chunk["chunk_text"]),
                "content_lines": len(chunk["chunk_text"].splitlines()),
                "source_start_char": chunk["source_start_char"],
                "source_end_char": chunk["source_end_char"],
                "injected_context_header": chunk["injected_context_header"],
                "overlap_text": chunk["overlap_text"],
                "has_overlap": chunk["has_overlap"],
                "overlap_tokens": chunk["overlap_tokens"],
                "pipeline_version": chunk["pipeline_version"],
                "metadata": chunk["metadata"],
            })

        manifest_file = out_path / "chunks_manifest.json"
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        logger.info("Saved %d chunks and manifest to %s", len(chunks), out_path)

        return {
            "total_chunks": len(chunks),
            "manifest_file": str(manifest_file),
            "output_directory": str(out_path),
        }
