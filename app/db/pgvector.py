import logging
from typing import Optional
from psycopg.rows import dict_row
from app.db.connections import get_connection
from app.utils.ids import generate_uuid
from app.core.config import settings

logger = logging.getLogger("uvicorn.error")

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_hash TEXT UNIQUE NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    ingestion_status TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    chunking_version TEXT NOT NULL,
    embedding_model TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR({settings.EMBEDDING_DIMENSIONS}) NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx 
ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_document_id_idx 
ON chunks (document_id);

CREATE INDEX IF NOT EXISTS documents_file_hash_idx 
ON documents (file_hash);
"""


class PgVectorStore:
    """
    Supabase PostgreSQL + pgvector storage implementation for Sholinganallur_RouteWise.
    Manages vector embeddings (768-dim) and document metadata.
    """

    @staticmethod
    async def init_db() -> None:
        """
        Initializes the vector extension, documents table, chunks table, and HNSW index.
        """
        async with await get_connection() as conn:
            await conn.execute(SCHEMA_SQL)
            logger.info("[pgvector] Database tables and HNSW index initialized successfully.")

    @staticmethod
    async def _fetch_document(column: str, value: str) -> Optional[dict]:
        """
        Helper method to fetch a single document record by column and value.
        """
        async with await get_connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM documents WHERE {column} = %s LIMIT 1;",
                (value,)
            )
            row = await cur.fetchone()
            if row:
                row["uploaded_at"] = (
                    row["uploaded_at"].isoformat()
                    if hasattr(row["uploaded_at"], "isoformat")
                    else str(row["uploaded_at"])
                )
                row["ingested_at"] = (
                    row["ingested_at"].isoformat()
                    if hasattr(row["ingested_at"], "isoformat")
                    else str(row["ingested_at"])
                )
            return row

    @staticmethod
    async def get_document_by_hash(file_hash: str) -> Optional[dict]:
        """
        Check if a file with the given hash has already been ingested.
        """
        return await PgVectorStore._fetch_document("file_hash", file_hash)

    @staticmethod
    async def get_document_by_id(document_id: str) -> Optional[dict]:
        """
        Fetch a document record by its primary key ID.
        """
        return await PgVectorStore._fetch_document("document_id", document_id)

    @staticmethod
    async def insert(
        document_record: dict,
        chunks: list[dict],
        embeddings: list[list[float]],
    ) -> None:
        """
        Atomically inserts a document record and all its chunk embeddings in a single transaction.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"Chunks count ({len(chunks)}) does not match embeddings count ({len(embeddings)})"
            )

        async with await get_connection() as conn:
            async with conn.transaction():
                # 1. Insert or update document record
                await conn.execute(
                    """
                    INSERT INTO documents (
                        document_id, filename, file_hash, file_size_bytes, uploaded_at,
                        ingested_at, ingestion_status, chunk_count, chunking_version, embedding_model
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        ingestion_status = EXCLUDED.ingestion_status,
                        ingested_at = EXCLUDED.ingested_at;
                    """,
                    (
                        document_record["document_id"],
                        document_record["filename"],
                        document_record["file_hash"],
                        int(document_record["file_size_bytes"]),
                        str(document_record["uploaded_at"]),
                        str(document_record["ingested_at"]),
                        str(document_record["ingestion_status"]),
                        int(document_record["chunk_count"]),
                        str(document_record["chunking_version"]),
                        str(document_record["embedding_model"]),
                    ),
                )

                # 2. Bulk insert chunks and 768-dim vector embeddings
                if chunks and embeddings:
                    chunk_rows = [
                        (
                            chunk.get("id") or chunk.get("chunk_id") or generate_uuid(),
                            document_record["document_id"],
                            document_record["filename"],
                            int(chunk["chunk_index"]),
                            int(chunk.get("page_number", 1)),
                            chunk.get("content") or chunk.get("text", ""),
                            emb,
                        )
                        for chunk, emb in zip(chunks, embeddings)
                    ]
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            """
                            INSERT INTO chunks (
                                chunk_id, document_id, filename, chunk_index, page_number, content, embedding
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (chunk_id) DO NOTHING;
                            """,
                            chunk_rows,
                        )

            logger.info(
                f"[pgvector] Ingested '{document_record['filename']}' ({len(chunks)} chunks, 768-dim)."
            )

    @staticmethod
    async def retrieve(
        query_embedding: list[float],
        n_results: int = 5,
        document_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Cosine similarity search returning top-K chunks formatted for RAG queries.
        Uses pgvector cosine distance operator (<=>).
        """
        where_filter = "WHERE document_id = %s" if document_id else ""
        query = f"""
            SELECT chunk_id, document_id, filename, chunk_index, page_number, content,
                   embedding <=> %s::vector AS distance
            FROM chunks
            {where_filter}
            ORDER BY distance ASC
            LIMIT %s;
        """
        params = (query_embedding, document_id, n_results) if document_id else (query_embedding, n_results)

        async with await get_connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(query, params)
            rows = await cur.fetchall()

            return [
                {
                    "id": r["chunk_id"],
                    "content": r["content"],
                    "metadata": {
                        "document_id": r["document_id"],
                        "filename": r["filename"],
                        "chunk_index": r["chunk_index"],
                        "page_number": r["page_number"],
                    },
                    "distance": float(r["distance"]),
                    "similarity": round(1.0 - float(r["distance"]), 4),
                }
                for r in rows
            ]

    @staticmethod
    async def delete_document(document_id: str) -> bool:
        """
        Deletes a document and cascades deletion to all associated chunks.
        """
        async with await get_connection() as conn:
            cur = await conn.execute(
                "DELETE FROM documents WHERE document_id = %s RETURNING document_id;",
                (document_id,)
            )
            deleted = await cur.fetchone()
            return deleted is not None


pgvector_store = PgVectorStore()

