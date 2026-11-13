"""
Module: app.services.rag_service
Purpose: Domain service for semantic RAG retrieval using Gemini query embeddings
         and PostgreSQL pgvector cosine similarity search over public.rag_chunks.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from pgvector import Vector

from app.ai.rag.embeddings.embedding_service import EmbeddingService
from app.schemas.rag import RAGChunkItem, RAGSearchResponse

logger = logging.getLogger(__name__)


class RAGService:
    """
    Encapsulates semantic similarity retrieval over the Sholinganallur transit knowledge base.
    1. Generates 768-dimensional query embeddings using Gemini (RETRIEVAL_QUERY).
    2. Executes parameterized pgvector cosine distance queries against public.rag_chunks.
    3. Formats and ranks the top-K relevant chunks with cosine similarity scores.
    """

    def __init__(self, embedding_service: Optional[EmbeddingService] = None) -> None:
        """
        Initialize RAGService with an optional injected EmbeddingService.
        """
        self.embedding_service = embedding_service or EmbeddingService()

    async def search(
        self,
        conn: psycopg.AsyncConnection,
        query: str,
        top_k: int = 5,
        chunk_type: Optional[str] = None,
    ) -> RAGSearchResponse:
        """
        Execute semantic similarity retrieval for a user question.

        Args:
            conn: Active asynchronous PostgreSQL connection from the connection pool.
            query: Natural language transport query.
            top_k: Maximum number of top chunks to retrieve (default: 5).
            chunk_type: Optional category filter (e.g., 'corridor_guide', 'route_profile', 'stop_profile').

        Returns:
            RAGSearchResponse: Ranked semantic chunks with cosine similarity scores.

        Raises:
            AuthenticationError: On Gemini API key failures.
            RateLimitError: On Gemini rate/quota exhaustion.
            TransientServerError: On Gemini transport/network issues.
            InvalidRequestError: On invalid query or dimension errors.
            psycopg.Error: On database query errors.
        """
        cleaned_query = query.strip()

        # 1. Generate query embedding via Gemini SDK (task_type=RETRIEVAL_QUERY, 768 dims)
        query_vector = self.embedding_service.embed_query(cleaned_query)

        # 2. Parameterized SQL query utilizing HNSW vector index (idx_rag_chunks_hnsw)
        # Cosine distance (<=>) ranges from 0.0 (identical) to 2.0 (diametrically opposite).
        # Similarity score is defined as: 1.0 - cosine_distance (ranging from -1.0 to +1.0).
        sql = """
            SELECT
                c.chunk_id,
                c.document_id,
                c.chunk_type,
                c.title,
                c.content,
                c.metadata,
                1.0 - (c.embedding <=> %(vector)s) AS similarity
            FROM public.rag_chunks c
            WHERE (%(chunk_type)s::text IS NULL OR LOWER(c.chunk_type) = LOWER(%(chunk_type)s::text))
            ORDER BY c.embedding <=> %(vector)s ASC
            LIMIT %(top_k)s;
        """

        params = {
            "vector": Vector(query_vector),
            "chunk_type": chunk_type.strip() if chunk_type else None,
            "top_k": top_k,
        }

        # 3. Asynchronously execute query with dict row factory
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

        # 4. Transform rows into validated Pydantic models
        chunks: List[RAGChunkItem] = []
        for r in rows:
            raw_sim = float(r["similarity"])
            # Clamp similarity strictly to valid mathematical cosine range [-1.0, 1.0] for precision protection
            normalized_sim = round(max(-1.0, min(1.0, raw_sim)), 4)
            metadata = r["metadata"] if isinstance(r["metadata"], dict) else {}

            chunks.append(
                RAGChunkItem(
                    chunk_id=r["chunk_id"],
                    document_id=r["document_id"],
                    chunk_type=r["chunk_type"],
                    title=r["title"],
                    content=r["content"],
                    similarity=normalized_sim,
                    metadata=metadata,
                )
            )

        logger.info(
            "RAG search completed: query='%s', top_k=%d, chunk_type=%s, retrieved=%d",
            cleaned_query[:60],
            top_k,
            chunk_type,
            len(chunks),
        )

        return RAGSearchResponse(
            query=cleaned_query,
            retrieved_count=len(chunks),
            chunks=chunks,
        )

