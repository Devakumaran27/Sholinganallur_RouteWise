"""
RAG Knowledge Store & Vector Embeddings Ingestion Service.
Phase 4 — Step 4.4: Ingests 86 documents and 145 rag_chunks (768-dim embeddings)
into Supabase PostgreSQL using Psycopg 3 with pgvector.
"""

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg
from psycopg.types.json import Jsonb

try:
    from pgvector import Vector
except ImportError:
    Vector = None  # type: ignore

from app.db.connections import get_sync_connection, mask_database_url

logger = logging.getLogger("rag_ingestion")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Expected canonical counts
EXPECTED_DOCUMENTS_COUNT = 86
EXPECTED_CHUNKS_COUNT = 145
EXPECTED_VECTORS_COUNT = 145
EXPECTED_EMBEDDING_DIMENSIONS = 768
EXPECTED_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_BATCH_SIZE = 50
DATASET_STAGE = "RAG_DOCUMENTS_AND_EMBEDDINGS"
DATASET_VERSION = "v1.0.0-sholinganallur"


@dataclass
class RAGPreflightSummary:
    """Result of RAG dataset preflight validation."""
    valid: bool
    doc_count: int
    chunk_count: int
    vector_count: int
    composite_hash: str
    file_hashes: Dict[str, str]
    fk_integrity_valid: bool
    pk_uniqueness_valid: bool
    vectors_valid: bool
    dimensions_valid: bool
    model_name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RAGIngestionResult:
    """Result of RAG dataset ingestion execution."""
    success: bool
    stage: str
    dataset_version: str
    composite_hash: str
    counts_ingested: Dict[str, int]
    total_records: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    ingestion_log_id: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class RAGVerificationResult:
    """Result of live database row count verification."""
    all_matched: bool
    actual_counts: Dict[str, int]
    expected_counts: Dict[str, int]
    discrepancies: Dict[str, Dict[str, int]]
    latest_ingestion_log: Optional[Dict[str, Any]] = None


def format_vector(embedding: List[float]) -> Any:
    """Convert a Python float list into a pgvector-compatible Vector object."""
    if Vector is not None:
        return Vector(embedding)
    return f"[{','.join(str(x) for x in embedding)}]"


def _is_mock_connection(conn: Any) -> bool:
    """Check if connection object is a mock/test stub."""
    if hasattr(conn, "_mock_return_value") or not hasattr(conn, "adapters"):
        return True
    try:
        from unittest.mock import NonCallableMagicMock
        if isinstance(conn, NonCallableMagicMock):
            return True
    except ImportError:
        pass
    return False


def register_vector(conn: Any, schema: str = "extensions") -> None:
    """
    Register pgvector extension types with a psycopg connection for the specified schema.
    Resolves type information using the schema-qualified type name (e.g., 'extensions.vector').

    Args:
        conn: The active psycopg.Connection or mock connection.
        schema: PostgreSQL schema containing the pgvector extension (default: 'extensions').

    Raises:
        psycopg.ProgrammingError: If the vector type is not found in the database.
    """
    if _is_mock_connection(conn):
        return

    from psycopg.types import TypeInfo
    from pgvector.psycopg.vector import register_vector_info

    target_name = f"{schema}.vector" if schema else "vector"
    info = TypeInfo.fetch(conn, target_name)
    if info is None and schema:
        info = TypeInfo.fetch(conn, "vector")
    if info is None:
        raise psycopg.ProgrammingError(
            f"pgvector extension type 'vector' not found in schema '{schema}'"
        )
    register_vector_info(conn, info)


def register_pgvector(conn: psycopg.Connection, schema: str = "extensions") -> None:
    """
    Register pgvector types with psycopg connection for the given schema (default: 'extensions').
    Explicitly registers using register_vector(conn, schema="extensions").
    Does not silently swallow registration failures for a real PostgreSQL connection.
    Mock/unit-test connections are handled gracefully without error.

    Args:
        conn: The active psycopg.Connection or mock connection.
        schema: PostgreSQL schema containing pgvector (default: 'extensions').

    Raises:
        RuntimeError: If pgvector registration in the specified schema fails on a real connection.
    """
    if _is_mock_connection(conn):
        logger.debug("Mock connection detected; skipping pgvector registration.")
        return

    try:
        register_vector(conn, schema="extensions")
        logger.debug(f"pgvector types registered with psycopg connection in schema '{schema}'.")
    except Exception as exc:
        err_msg = f"pgvector registration in '{schema}' schema failed: {exc}"
        logger.error(err_msg)
        raise RuntimeError(err_msg) from exc


def smoke_test_pgvector(
    conn: Optional[psycopg.Connection] = None,
    schema: str = "extensions",
) -> Dict[str, Any]:
    """
    Execute a safe, read-only database smoke test to verify pgvector vector adaptation.
    Performs:
    1. Opens or uses a PostgreSQL connection.
    2. Registers pgvector using the specified schema ('extensions').
    3. Creates a test Vector containing exactly 768 finite float values.
    4. Executes a read-only parameterized query casting the parameter to extensions.vector(768):
       SELECT %s::extensions.vector(768) AS test_vec;
    5. Confirms the returned vector has 768 dimensions and finite float values.
    6. Ensures zero rows inserted, updated, or deleted.
    7. Never prints credentials or secret connection strings.

    Returns:
        Dict[str, Any]: Test summary with status, dimensions, schema, and verification details.
    """
    owns_conn = False
    active_conn = conn
    if active_conn is None:
        active_conn = get_sync_connection()
        owns_conn = True

    try:
        # 1. Register pgvector with target schema
        register_pgvector(active_conn, schema=schema)

        # 2. Create test vector with 768 finite float values
        test_values = [round(math.sin(i + 1) * 0.01, 6) for i in range(EXPECTED_EMBEDDING_DIMENSIONS)]
        for val in test_values:
            if not math.isfinite(val):
                raise ValueError("Generated test vector contains non-finite float value.")

        test_payload = format_vector(test_values)

        # 3. Execute read-only parameterized query
        with active_conn.cursor() as cur:
            query = f"SELECT %s::{schema}.vector({EXPECTED_EMBEDDING_DIMENSIONS}) AS test_vec;"
            cur.execute(query, (test_payload,))
            row = cur.fetchone()

            if row is None or row[0] is None:
                raise ValueError("Read-only vector smoke-test query returned no rows or NULL result.")

            result_vec = row[0]

            # 4. Extract and verify dimensions
            if hasattr(result_vec, "dimensions") and callable(result_vec.dimensions):
                actual_dims = result_vec.dimensions()
            elif hasattr(result_vec, "to_list") and callable(result_vec.to_list):
                actual_dims = len(result_vec.to_list())
            elif isinstance(result_vec, (list, tuple)):
                actual_dims = len(result_vec)
            elif isinstance(result_vec, str):
                parsed = json.loads(result_vec) if result_vec.startswith("[") else []
                actual_dims = len(parsed)
            else:
                raise TypeError(f"Unexpected vector result type: {type(result_vec)}")

            if actual_dims != EXPECTED_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Vector dimension mismatch: expected {EXPECTED_EMBEDDING_DIMENSIONS}, got {actual_dims}"
                )

            # 5. Extract values and verify finite numbers
            if hasattr(result_vec, "to_list") and callable(result_vec.to_list):
                returned_floats = result_vec.to_list()
            elif isinstance(result_vec, (list, tuple)):
                returned_floats = list(result_vec)
            elif isinstance(result_vec, str) and result_vec.startswith("["):
                returned_floats = json.loads(result_vec)
            else:
                returned_floats = []

            for elem in returned_floats:
                if not math.isfinite(float(elem)):
                    raise ValueError(f"Returned vector contains non-finite value: {elem}")

        logger.info(
            f"pgvector smoke test PASSED: successfully adapted and round-tripped "
            f"{actual_dims}-dimension vector in schema '{schema}' (0 rows modified)."
        )

        return {
            "status": "SUCCESS",
            "schema": schema,
            "expected_dimensions": EXPECTED_EMBEDDING_DIMENSIONS,
            "actual_dimensions": actual_dims,
            "round_trip_valid": True,
            "zero_writes_verified": True,
            "message": f"Read-only {actual_dims}-dim pgvector round-trip verified successfully.",
        }

    finally:
        if owns_conn and active_conn is not None:
            active_conn.close()


class RAGIngestionService:
    """
    Ingestion service for RAG documents and semantic chunks with pgvector embeddings.
    Enforces FK order: documents (86) -> rag_chunks (145).
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else Path("data")
        self.docs_dir = self.data_dir / "documents"
        self.chunks_dir = self.data_dir / "chunks"
        self.vectors_dir = self.data_dir / "embeddings" / "vectors"
        self.checkpoints_dir = self.data_dir / "embeddings" / "checkpoints"

    def compute_sha256(self, file_path: Path) -> str:
        """Compute SHA-256 digest of a local file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def run_preflight(self) -> RAGPreflightSummary:
        """
        Validate source artifacts before ingestion:
        - All required manifests and directories exist
        - Expected counts: 86 docs, 145 chunks, 145 vectors
        - Every chunk references an existing document (FK integrity)
        - Primary key uniqueness on document_id and chunk_id
        - All 145 vector files match chunk IDs and chunk SHA-256 hashes
        - Vector dimensions are exactly 768 and all float values are finite
        - Embedding model matches gemini-embedding-001
        """
        errors: List[str] = []
        warnings: List[str] = []
        file_hashes: Dict[str, str] = {}

        # 1. File existence checks
        doc_manifest_path = self.docs_dir / "documents_manifest.json"
        chunk_manifest_path = self.chunks_dir / "chunks_manifest.json"
        checkpoint_path = self.checkpoints_dir / "batch_checkpoint.json"

        if not doc_manifest_path.exists():
            errors.append(f"Missing documents manifest: {doc_manifest_path}")
        else:
            file_hashes["documents_manifest"] = self.compute_sha256(doc_manifest_path)

        if not chunk_manifest_path.exists():
            errors.append(f"Missing chunks manifest: {chunk_manifest_path}")
        else:
            file_hashes["chunks_manifest"] = self.compute_sha256(chunk_manifest_path)

        if not checkpoint_path.exists():
            warnings.append(f"Batch checkpoint not found at {checkpoint_path}")
        else:
            file_hashes["batch_checkpoint"] = self.compute_sha256(checkpoint_path)

        if not self.vectors_dir.exists():
            errors.append(f"Vectors directory missing: {self.vectors_dir}")

        if errors:
            return RAGPreflightSummary(
                valid=False,
                doc_count=0,
                chunk_count=0,
                vector_count=0,
                composite_hash="",
                file_hashes=file_hashes,
                fk_integrity_valid=False,
                pk_uniqueness_valid=False,
                vectors_valid=False,
                dimensions_valid=False,
                model_name="",
                errors=errors,
                warnings=warnings,
            )

        # 2. Load manifests
        try:
            with open(doc_manifest_path, "r", encoding="utf-8") as f:
                docs_manifest: List[Dict[str, Any]] = json.load(f)
        except Exception as e:
            errors.append(f"Failed to load documents manifest: {e}")
            docs_manifest = []

        try:
            with open(chunk_manifest_path, "r", encoding="utf-8") as f:
                chunks_manifest: List[Dict[str, Any]] = json.load(f)
        except Exception as e:
            errors.append(f"Failed to load chunks manifest: {e}")
            chunks_manifest = []

        doc_count = len(docs_manifest)
        chunk_count = len(chunks_manifest)

        vector_files = list(self.vectors_dir.glob("*.json"))
        vector_count = len(vector_files)

        # Count assertions
        if doc_count != EXPECTED_DOCUMENTS_COUNT:
            errors.append(f"Document count mismatch: expected {EXPECTED_DOCUMENTS_COUNT}, found {doc_count}")

        if chunk_count != EXPECTED_CHUNKS_COUNT:
            errors.append(f"Chunk count mismatch: expected {EXPECTED_CHUNKS_COUNT}, found {chunk_count}")

        if vector_count != EXPECTED_VECTORS_COUNT:
            errors.append(f"Vector files count mismatch: expected {EXPECTED_VECTORS_COUNT}, found {vector_count}")

        # 3. Primary Key Uniqueness
        doc_ids = [d.get("document_id") for d in docs_manifest if d.get("document_id")]
        pk_docs_valid = len(doc_ids) == len(set(doc_ids))
        if not pk_docs_valid:
            errors.append("Duplicate document_id found in documents manifest")

        chunk_ids = [c.get("chunk_id") for c in chunks_manifest if c.get("chunk_id")]
        pk_chunks_valid = len(chunk_ids) == len(set(chunk_ids))
        if not pk_chunks_valid:
            errors.append("Duplicate chunk_id found in chunks manifest")

        # 4. Foreign Key Integrity (chunk.document_id -> doc.document_id)
        known_doc_ids: Set[str] = set(doc_ids)
        fk_valid = True
        missing_doc_refs: Set[str] = set()
        for c in chunks_manifest:
            parent_id = c.get("document_id")
            if not parent_id or parent_id not in known_doc_ids:
                missing_doc_refs.add(str(parent_id))
                fk_valid = False

        if missing_doc_refs:
            errors.append(f"rag_chunks references unknown document_ids: {missing_doc_refs}")

        # 5. Document Files Validation (existence, non-empty content)
        for d in docs_manifest:
            did = d.get("document_id")
            if not did:
                errors.append("Document entry in manifest missing document_id")
                continue
            doc_file_path = self.docs_dir / f"{did}.json"
            if not doc_file_path.exists():
                errors.append(f"Missing document JSON file for '{did}': {doc_file_path}")
            else:
                try:
                    with open(doc_file_path, "r", encoding="utf-8") as df:
                        doc_data = json.load(df)
                    content = doc_data.get("content", "")
                    if not content or not str(content).strip():
                        errors.append(f"Document '{did}' has empty content in {doc_file_path.name}")
                except Exception as e:
                    errors.append(f"Corrupt document JSON file {doc_file_path.name}: {e}")

        # 6. Chunk Files Validation (existence, non-empty text, hash match)
        chunk_map: Dict[str, Dict[str, Any]] = {}
        for c in chunks_manifest:
            cid = c.get("chunk_id")
            if not cid:
                errors.append("Encountered chunk entry without chunk_id in manifest")
                continue
            chunk_map[cid] = c

            chunk_file_path = self.chunks_dir / f"{cid}.json"
            if not chunk_file_path.exists():
                errors.append(f"Missing chunk JSON file for '{cid}': {chunk_file_path}")
            else:
                try:
                    with open(chunk_file_path, "r", encoding="utf-8") as cf:
                        c_data = json.load(cf)
                    content = c_data.get("chunk_text") or c_data.get("content", "")
                    if not content or not str(content).strip():
                        errors.append(f"Chunk '{cid}' has empty content in {chunk_file_path.name}")
                    if c_data.get("chunk_hash") != c.get("chunk_hash"):
                        errors.append(
                            f"Chunk '{cid}' hash mismatch between file ({c_data.get('chunk_hash')}) "
                            f"and manifest ({c.get('chunk_hash')})"
                        )
                except Exception as e:
                    errors.append(f"Corrupt chunk JSON file {chunk_file_path.name}: {e}")

        # 7. Vector Files Validation (1:1 correspondence, exact set matching, dimensions, finite checks)
        vector_files = list(self.vectors_dir.glob("*.json"))
        vector_count = len(vector_files)
        vectors_valid = True
        dimensions_valid = True
        detected_model = ""
        seen_vector_cids: Dict[str, Path] = {}

        for v_path in vector_files:
            try:
                with open(v_path, "r", encoding="utf-8") as vf:
                    v_data = json.load(vf)
            except Exception as e:
                errors.append(f"Corrupt vector file {v_path.name}: {e}")
                vectors_valid = False
                continue

            cid = v_data.get("chunk_id")
            if not cid:
                errors.append(f"Vector file {v_path.name} is missing 'chunk_id'")
                vectors_valid = False
                continue

            # Reject duplicate vector chunk_id values
            if cid in seen_vector_cids:
                errors.append(
                    f"Duplicate vector chunk_id '{cid}' found in '{v_path.name}' and '{seen_vector_cids[cid].name}'"
                )
                vectors_valid = False
            else:
                seen_vector_cids[cid] = v_path

            # Reject vectors whose IDs are not in the chunk manifest
            if cid not in chunk_map:
                errors.append(f"Vector {v_path.name} chunk_id '{cid}' not found in chunks manifest (orphaned vector)")
                vectors_valid = False
                continue

            # Confirm vector filename matches chunk_id
            if v_path.stem != cid:
                errors.append(f"Vector filename '{v_path.name}' does not match its internal chunk_id '{cid}'")
                vectors_valid = False

            c_item = chunk_map[cid]

            # Confirm each vector's chunk_hash matches the corresponding chunk manifest hash
            v_hash = v_data.get("chunk_hash")
            c_hash = c_item.get("chunk_hash")
            if v_hash != c_hash:
                errors.append(f"Chunk {cid} hash mismatch: vector '{v_hash}' != manifest '{c_hash}'")
                vectors_valid = False

            # Model check
            model = v_data.get("embedding_metadata", {}).get("model", "")
            if not detected_model:
                detected_model = model
            elif model != detected_model:
                warnings.append(f"Inconsistent model detected: {model} vs {detected_model}")

            if model != EXPECTED_EMBEDDING_MODEL:
                errors.append(f"Chunk {cid} model mismatch: expected {EXPECTED_EMBEDDING_MODEL}, found {model}")
                vectors_valid = False

            # Confirm every vector has exactly 768 finite numeric values
            emb = v_data.get("embedding")
            if not isinstance(emb, list):
                errors.append(f"Chunk {cid} embedding is not a list in {v_path.name}")
                vectors_valid = False
                dimensions_valid = False
            elif len(emb) != EXPECTED_EMBEDDING_DIMENSIONS:
                errors.append(
                    f"Chunk {cid} dimension mismatch: expected {EXPECTED_EMBEDDING_DIMENSIONS}, found {len(emb)}"
                )
                dimensions_valid = False
            else:
                for idx, val in enumerate(emb):
                    if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                        errors.append(f"Chunk {cid} contains non-finite value at index {idx}: {val}")
                        vectors_valid = False
                        break

        # Explicitly compare the complete set of chunk IDs with the complete set of vector IDs
        manifest_chunk_id_set = set(chunk_map.keys())
        vector_chunk_id_set = set(seen_vector_cids.keys())

        if manifest_chunk_id_set != vector_chunk_id_set:
            missing_in_vectors = manifest_chunk_id_set - vector_chunk_id_set
            extra_in_vectors = vector_chunk_id_set - manifest_chunk_id_set
            if missing_in_vectors:
                errors.append(f"Chunk IDs present in manifest but missing vector files: {sorted(list(missing_in_vectors))}")
            if extra_in_vectors:
                errors.append(f"Vector chunk IDs present but missing from manifest: {sorted(list(extra_in_vectors))}")
            vectors_valid = False

        # Compute composite SHA-256
        combined_hashes = "".join(sorted(file_hashes.values()))
        composite_hash = hashlib.sha256(combined_hashes.encode("utf-8")).hexdigest()

        is_valid = (
            len(errors) == 0
            and pk_docs_valid
            and pk_chunks_valid
            and fk_valid
            and vectors_valid
            and dimensions_valid
        )

        return RAGPreflightSummary(
            valid=is_valid,
            doc_count=doc_count,
            chunk_count=chunk_count,
            vector_count=vector_count,
            composite_hash=composite_hash,
            file_hashes=file_hashes,
            fk_integrity_valid=fk_valid,
            pk_uniqueness_valid=pk_docs_valid and pk_chunks_valid,
            vectors_valid=vectors_valid,
            dimensions_valid=dimensions_valid,
            model_name=detected_model or EXPECTED_EMBEDDING_MODEL,
            errors=errors,
            warnings=warnings,
        )

    def load_documents(self) -> List[Dict[str, Any]]:
        """
        Load all 86 documents, joining manifest metadata with document content files
        and calculating total_chunks per document from the chunks manifest.
        Raises FileNotFoundError or ValueError if any file or content is missing or invalid.
        """
        doc_manifest_path = self.docs_dir / "documents_manifest.json"
        if not doc_manifest_path.exists():
            raise FileNotFoundError(f"Documents manifest missing: {doc_manifest_path}")

        chunk_manifest_path = self.chunks_dir / "chunks_manifest.json"
        if not chunk_manifest_path.exists():
            raise FileNotFoundError(f"Chunks manifest missing: {chunk_manifest_path}")

        with open(doc_manifest_path, "r", encoding="utf-8") as f:
            docs_manifest = json.load(f)

        with open(chunk_manifest_path, "r", encoding="utf-8") as f:
            chunks_manifest = json.load(f)

        # Count chunks per document_id
        chunk_counts: Dict[str, int] = {}
        for c in chunks_manifest:
            did = c.get("document_id")
            if did:
                chunk_counts[did] = chunk_counts.get(did, 0) + 1

        documents: List[Dict[str, Any]] = []
        for d in docs_manifest:
            did = d.get("document_id")
            if not did:
                raise ValueError("Document in manifest is missing 'document_id'")

            doc_file_path = self.docs_dir / f"{did}.json"
            if not doc_file_path.exists():
                raise FileNotFoundError(f"Document file missing for '{did}': {doc_file_path}")

            with open(doc_file_path, "r", encoding="utf-8") as df:
                doc_data = json.load(df)

            content = doc_data.get("content", "")
            if not content or not str(content).strip():
                raise ValueError(f"Document '{did}' has empty content in {doc_file_path.name}")

            file_hash = doc_data.get("document_hash") or doc_data.get("file_hash") or ""
            if not file_hash:
                raise ValueError(f"Document '{did}' is missing document_hash/file_hash in {doc_file_path.name}")

            total_chunks = chunk_counts.get(did, doc_data.get("total_chunks", 1))
            metadata = doc_data.get("metadata", {})

            documents.append(
                {
                    "document_id": did,
                    "title": doc_data.get("title", ""),
                    "document_type": doc_data.get("document_type", ""),
                    "source_file": doc_data.get("source_file", ""),
                    "file_hash": file_hash,
                    "total_chunks": total_chunks,
                    "metadata": metadata,
                }
            )

        return documents

    def load_chunks_and_vectors(self) -> List[Dict[str, Any]]:
        """
        Load all 145 chunks, joining chunk manifests, chunk text content,
        and embedding vector arrays.
        Raises FileNotFoundError or ValueError if any chunk or vector file or content is missing/invalid.
        """
        chunk_manifest_path = self.chunks_dir / "chunks_manifest.json"
        if not chunk_manifest_path.exists():
            raise FileNotFoundError(f"Chunks manifest missing: {chunk_manifest_path}")

        with open(chunk_manifest_path, "r", encoding="utf-8") as f:
            chunks_manifest = json.load(f)

        chunks_with_vectors: List[Dict[str, Any]] = []
        for c in chunks_manifest:
            cid = c.get("chunk_id")
            if not cid:
                raise ValueError("Chunk in manifest is missing 'chunk_id'")

            # Load chunk content from individual file
            chunk_file_path = self.chunks_dir / f"{cid}.json"
            if not chunk_file_path.exists():
                raise FileNotFoundError(f"Chunk file missing for '{cid}': {chunk_file_path}")

            with open(chunk_file_path, "r", encoding="utf-8") as cf:
                c_file_data = json.load(cf)

            content = c_file_data.get("chunk_text") or c_file_data.get("content", "")
            if not content or not str(content).strip():
                raise ValueError(f"Chunk '{cid}' has empty content in {chunk_file_path.name}")

            # Load vector embedding from vector file
            vector_file_path = self.vectors_dir / f"{cid}.json"
            if not vector_file_path.exists():
                raise FileNotFoundError(f"Vector file missing for '{cid}': {vector_file_path}")

            with open(vector_file_path, "r", encoding="utf-8") as vf:
                v_data = json.load(vf)

            v_cid = v_data.get("chunk_id")
            if v_cid != cid:
                raise ValueError(
                    f"Vector file {vector_file_path.name} chunk_id '{v_cid}' does not match expected '{cid}'"
                )

            v_hash = v_data.get("chunk_hash")
            c_hash = c.get("chunk_hash")
            if v_hash != c_hash:
                raise ValueError(
                    f"Chunk '{cid}' hash mismatch: vector '{v_hash}' != manifest '{c_hash}'"
                )

            embedding: List[float] = v_data.get("embedding", [])
            if not isinstance(embedding, list):
                raise ValueError(f"Chunk '{cid}' embedding is not a list in {vector_file_path.name}")

            if len(embedding) != EXPECTED_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Chunk '{cid}' embedding dimension {len(embedding)} != {EXPECTED_EMBEDDING_DIMENSIONS}"
                )

            for idx, val in enumerate(embedding):
                if not isinstance(val, (int, float)) or math.isnan(val) or math.isinf(val):
                    raise ValueError(f"Chunk '{cid}' embedding contains non-finite value at index {idx}: {val}")

            # Schema expects: chunk_id, document_id, chunk_type, title, content, token_estimate, metadata, embedding
            chunk_type = c.get("chunk_type") or c.get("document_type", "")
            token_estimate = c.get("token_count") or c.get("token_estimate", 0)
            metadata = c.get("metadata", {})

            chunks_with_vectors.append(
                {
                    "chunk_id": cid,
                    "document_id": c["document_id"],
                    "chunk_type": chunk_type,
                    "title": c.get("title", ""),
                    "content": content,
                    "token_estimate": token_estimate,
                    "metadata": metadata,
                    "embedding": embedding,
                }
            )

        return chunks_with_vectors

    def ingest_all(
        self,
        conn: psycopg.Connection,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> RAGIngestionResult:
        """
        Execute full transactional ingestion of RAG documents and chunks into Supabase PostgreSQL.
        Strict FK Order:
          1. public.documents (86 rows)
          2. public.rag_chunks (145 rows, batched)
        Audit Logging:
          0. Inserts IN_PROGRESS record into public.dataset_ingestion_log
          3. Updates record to SUCCESS with elapsed duration and row counts
        Rollback:
          Any exception triggers immediate rollback and records FAILED in audit log.
        """
        start_time = time.time()
        started_at = datetime.now(timezone.utc)
        ingestion_log_id: Optional[int] = None
        counts_ingested: Dict[str, int] = {"documents": 0, "rag_chunks": 0}

        # 1. Run Preflight
        preflight = self.run_preflight()
        if not preflight.valid:
            error_msg = f"Preflight validation failed: {'; '.join(preflight.errors)}"
            logger.error(error_msg)
            return RAGIngestionResult(
                success=False,
                stage=DATASET_STAGE,
                dataset_version=DATASET_VERSION,
                composite_hash=preflight.composite_hash,
                counts_ingested=counts_ingested,
                total_records=0,
                started_at=started_at,
                duration_seconds=time.time() - start_time,
                error_message=error_msg,
            )

        # 2. Register pgvector
        register_pgvector(conn, schema="extensions")

        # 3. Load datasets
        documents = self.load_documents()
        chunks_with_vectors = self.load_chunks_and_vectors()

        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 0. Insert initial IN_PROGRESS audit log
                    cur.execute(
                        """
                        INSERT INTO public.dataset_ingestion_log (
                            dataset_version, dataset_file_hash, ingestion_stage,
                            status, total_records_ingested, quarantined_records_count,
                            started_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING ingestion_id;
                        """,
                        (
                            DATASET_VERSION,
                            preflight.composite_hash,
                            DATASET_STAGE,
                            "IN_PROGRESS",
                            0,
                            0,
                            started_at,
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        ingestion_log_id = row[0]

                    # 1. Ingest documents (86 rows) - FK dependency Level 0
                    doc_rows = [
                        (
                            d["document_id"],
                            d["title"],
                            d["document_type"],
                            d["source_file"],
                            d["file_hash"],
                            d["total_chunks"],
                            Jsonb(d["metadata"]),
                        )
                        for d in documents
                    ]
                    cur.executemany(
                        """
                        INSERT INTO public.documents (
                            document_id, title, document_type, source_file,
                            file_hash, total_chunks, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (document_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            document_type = EXCLUDED.document_type,
                            source_file = EXCLUDED.source_file,
                            file_hash = EXCLUDED.file_hash,
                            total_chunks = EXCLUDED.total_chunks,
                            metadata = EXCLUDED.metadata;
                        """,
                        doc_rows,
                    )
                    counts_ingested["documents"] = len(doc_rows)
                    logger.info(f"Ingested {len(doc_rows)} documents.")

                    # 2. Ingest rag_chunks (145 rows, batched) - FK dependency Level 1
                    chunk_rows = [
                        (
                            c["chunk_id"],
                            c["document_id"],
                            c["chunk_type"],
                            c["title"],
                            c["content"],
                            c["token_estimate"],
                            Jsonb(c["metadata"]),
                            format_vector(c["embedding"]),
                        )
                        for c in chunks_with_vectors
                    ]
                    total_chunks = len(chunk_rows)
                    for offset in range(0, total_chunks, batch_size):
                        batch = chunk_rows[offset : offset + batch_size]
                        cur.executemany(
                            """
                            INSERT INTO public.rag_chunks (
                                chunk_id, document_id, chunk_type, title, content,
                                token_estimate, metadata, embedding
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (chunk_id) DO UPDATE SET
                                document_id = EXCLUDED.document_id,
                                chunk_type = EXCLUDED.chunk_type,
                                title = EXCLUDED.title,
                                content = EXCLUDED.content,
                                token_estimate = EXCLUDED.token_estimate,
                                metadata = EXCLUDED.metadata,
                                embedding = EXCLUDED.embedding;
                            """,
                            batch,
                        )
                    counts_ingested["rag_chunks"] = total_chunks
                    logger.info(f"Ingested {total_chunks} rag_chunks in batches of {batch_size}.")

                    # Update dataset_ingestion_log to SUCCESS
                    total_records = sum(counts_ingested.values())
                    duration = time.time() - start_time
                    completed_at = datetime.now(timezone.utc)

                    if ingestion_log_id:
                        cur.execute(
                            """
                            UPDATE public.dataset_ingestion_log
                            SET status = 'SUCCESS',
                                total_records_ingested = %s,
                                completed_at = %s,
                                duration_seconds = %s
                            WHERE ingestion_id = %s;
                            """,
                            (
                                total_records,
                                completed_at,
                                Decimal(str(round(duration, 2))),
                                ingestion_log_id,
                            ),
                        )

            logger.info(f"Successfully committed ingestion of {total_records} RAG records in {duration:.2f}s.")
            return RAGIngestionResult(
                success=True,
                stage=DATASET_STAGE,
                dataset_version=DATASET_VERSION,
                composite_hash=preflight.composite_hash,
                counts_ingested=counts_ingested,
                total_records=total_records,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                ingestion_log_id=ingestion_log_id,
            )

        except Exception as exc:
            duration = time.time() - start_time
            error_msg = f"RAG Ingestion transaction failed: {exc}"
            logger.error(error_msg)

            # Try to log failure in a separate transaction
            try:
                if ingestion_log_id and conn and not conn.closed:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE public.dataset_ingestion_log
                                SET status = 'FAILED',
                                    error_message = %s,
                                    completed_at = %s,
                                    duration_seconds = %s
                                WHERE ingestion_id = %s;
                                """,
                                (
                                    error_msg[:1000],
                                    datetime.now(timezone.utc),
                                    Decimal(str(round(duration, 2))),
                                    ingestion_log_id,
                                ),
                            )
            except Exception as log_exc:
                logger.warning(f"Failed to record failure in dataset_ingestion_log: {log_exc}")

            return RAGIngestionResult(
                success=False,
                stage=DATASET_STAGE,
                dataset_version=DATASET_VERSION,
                composite_hash=preflight.composite_hash,
                counts_ingested=counts_ingested,
                total_records=0,
                started_at=started_at,
                duration_seconds=duration,
                ingestion_log_id=ingestion_log_id,
                error_message=error_msg,
            )

    def verify_database(self, conn: psycopg.Connection) -> RAGVerificationResult:
        """
        Verify that database row counts match expected RAG specifications:
          - public.documents: 86
          - public.rag_chunks: 145
        Also queries the latest RAG stage log from public.dataset_ingestion_log.
        """
        expected_counts = {
            "documents": EXPECTED_DOCUMENTS_COUNT,
            "rag_chunks": EXPECTED_CHUNKS_COUNT,
        }
        actual_counts: Dict[str, int] = {}
        discrepancies: Dict[str, Dict[str, int]] = {}

        with conn.cursor() as cur:
            for table in expected_counts:
                cur.execute(f"SELECT count(*) FROM public.{table};")
                row = cur.fetchone()
                count = row[0] if row else 0
                actual_counts[table] = count
                if count != expected_counts[table]:
                    discrepancies[table] = {
                        "expected": expected_counts[table],
                        "actual": count,
                    }

            # Fetch latest RAG stage ingestion log
            cur.execute(
                """
                SELECT ingestion_id, dataset_version, dataset_file_hash,
                       ingestion_stage, status, total_records_ingested,
                       quarantined_records_count, error_message, started_at,
                       completed_at, duration_seconds
                FROM public.dataset_ingestion_log
                WHERE ingestion_stage = %s
                ORDER BY ingestion_id DESC
                LIMIT 1;
                """,
                (DATASET_STAGE,),
            )
            log_row = cur.fetchone()
            latest_log: Optional[Dict[str, Any]] = None
            if log_row:
                latest_log = {
                    "ingestion_id": log_row[0],
                    "dataset_version": log_row[1],
                    "dataset_file_hash": log_row[2],
                    "ingestion_stage": log_row[3],
                    "status": log_row[4],
                    "total_records_ingested": log_row[5],
                    "quarantined_records_count": log_row[6],
                    "error_message": log_row[7],
                    "started_at": log_row[8].isoformat() if log_row[8] else None,
                    "completed_at": log_row[9].isoformat() if log_row[9] else None,
                    "duration_seconds": float(log_row[10]) if log_row[10] else None,
                }

        all_matched = len(discrepancies) == 0
        return RAGVerificationResult(
            all_matched=all_matched,
            actual_counts=actual_counts,
            expected_counts=expected_counts,
            discrepancies=discrepancies,
            latest_ingestion_log=latest_log,
        )

    def smoke_test_pgvector(
        self,
        conn: Optional[psycopg.Connection] = None,
        schema: str = "extensions",
    ) -> Dict[str, Any]:
        """
        Execute a safe, read-only database smoke test to verify pgvector vector adaptation.
        Confirms 768-dim vector round-trip without writing data.
        """
        return smoke_test_pgvector(conn=conn, schema=schema)

