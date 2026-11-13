"""
Unit tests for Phase 4 Step 4.4 — RAG Documents and Embeddings Ingestion.
Tests preflight validation, foreign key ordering, vector format conversion,
SHA-256 hash matching, dimensions and finite checks, idempotency, rollback,
and database verification.
"""

import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import psycopg
from psycopg.types.json import Jsonb

from app.db.rag_ingestion import (
    DEFAULT_BATCH_SIZE,
    EXPECTED_CHUNKS_COUNT,
    EXPECTED_DOCUMENTS_COUNT,
    EXPECTED_EMBEDDING_DIMENSIONS,
    EXPECTED_EMBEDDING_MODEL,
    EXPECTED_VECTORS_COUNT,
    RAGIngestionService,
    format_vector,
    register_pgvector,
    register_vector,
    smoke_test_pgvector,
)


def create_mock_rag_dataset(tmp_path: Path) -> None:
    """Helper to generate a complete, valid minimal mock RAG dataset."""
    docs_dir = tmp_path / "documents"
    chunks_dir = tmp_path / "chunks"
    vectors_dir = tmp_path / "embeddings" / "vectors"
    checkpoints_dir = tmp_path / "embeddings" / "checkpoints"
    for d in [docs_dir, chunks_dir, vectors_dir, checkpoints_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # 86 docs
    docs = []
    for i in range(86):
        did = f"doc_{i}"
        doc_hash = f"doc_hash_{i:04d}"
        docs.append({"document_id": did, "document_hash": doc_hash})
        with open(docs_dir / f"{did}.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "document_id": did,
                    "title": f"Doc Title {i}",
                    "document_type": "stop_profile",
                    "source_file": "bus_stops.json",
                    "content": f"Non-empty content for doc {i}",
                    "document_hash": doc_hash,
                    "metadata": {"doc_index": i},
                },
                f,
            )

    with open(docs_dir / "documents_manifest.json", "w", encoding="utf-8") as f:
        json.dump(docs, f)

    # 145 chunks & vectors
    chunks = []
    for i in range(145):
        cid = f"c_{i}"
        did = f"doc_{i % 86}"
        c_hash = f"hash_{i:04d}"
        chunks.append(
            {
                "chunk_id": cid,
                "document_id": did,
                "chunk_hash": c_hash,
                "token_count": 100,
                "document_type": "stop_profile",
                "title": f"Chunk Title {i}",
            }
        )
        with open(chunks_dir / f"{cid}.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "chunk_id": cid,
                    "chunk_text": f"Non-empty text for chunk {i}",
                    "chunk_hash": c_hash,
                    "token_count": 100,
                    "document_type": "stop_profile",
                },
                f,
            )

        with open(vectors_dir / f"{cid}.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "chunk_id": cid,
                    "chunk_hash": c_hash,
                    "embedding": [0.01] * 768,
                    "embedding_metadata": {"model": "gemini-embedding-001"},
                },
                f,
            )

    with open(chunks_dir / "chunks_manifest.json", "w", encoding="utf-8") as f:
        json.dump(chunks, f)

    with open(checkpoints_dir / "batch_checkpoint.json", "w", encoding="utf-8") as f:
        json.dump({"checkpoint_version": "1.0.0"}, f)


class TestRAGIngestionPreflight(unittest.TestCase):
    """Tests for dry-run preflight validation of real and mocked artifacts."""

    def setUp(self):
        self.service = RAGIngestionService()

    def test_01_preflight_passes_on_actual_source_artifacts(self):
        """Preflight on real repository artifacts must pass with 100% validity."""
        summary = self.service.run_preflight()
        self.assertTrue(summary.valid, f"Preflight failed: {summary.errors}")
        self.assertEqual(summary.doc_count, EXPECTED_DOCUMENTS_COUNT)
        self.assertEqual(summary.chunk_count, EXPECTED_CHUNKS_COUNT)
        self.assertEqual(summary.vector_count, EXPECTED_VECTORS_COUNT)
        self.assertEqual(summary.model_name, EXPECTED_EMBEDDING_MODEL)
        self.assertTrue(summary.fk_integrity_valid)
        self.assertTrue(summary.pk_uniqueness_valid)
        self.assertTrue(summary.vectors_valid)
        self.assertTrue(summary.dimensions_valid)
        self.assertEqual(len(summary.errors), 0)

    def test_02_preflight_detects_missing_document_manifest(self):
        """Missing documents manifest must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            service = RAGIngestionService(data_dir=Path(tmpdir))
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Missing documents manifest" in e for e in summary.errors))

    def test_03_preflight_detects_document_count_mismatch(self):
        """Divergence from 86 documents must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Overwrite documents manifest with only 1 document instead of 86
            with open(tmp_path / "documents" / "documents_manifest.json", "w") as f:
                json.dump([{"document_id": "doc_0"}], f)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Document count mismatch" in e for e in summary.errors))

    def test_04_preflight_detects_chunk_count_mismatch(self):
        """Divergence from 145 chunks must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Overwrite chunks manifest with only 10 chunks instead of 145
            with open(tmp_path / "chunks" / "chunks_manifest.json", "w") as f:
                json.dump([{"chunk_id": f"c_{i}", "document_id": "doc_0"} for i in range(10)], f)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Chunk count mismatch" in e for e in summary.errors))

    def test_05_preflight_detects_dangling_chunk_foreign_key(self):
        """Chunk referencing unknown document_id must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Alter chunk manifest so c_144 references an unknown document
            chunk_manifest_path = tmp_path / "chunks" / "chunks_manifest.json"
            with open(chunk_manifest_path, "r") as f:
                chunks = json.load(f)
            chunks[144]["document_id"] = "unknown_doc_xyz"
            with open(chunk_manifest_path, "w") as f:
                json.dump(chunks, f)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertFalse(summary.fk_integrity_valid)
            self.assertTrue(any("unknown document_ids" in e for e in summary.errors))

    def test_06_preflight_detects_vector_hash_mismatch(self):
        """Vector file chunk_hash mismatching manifest chunk_hash must fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Corrupt the hash for vector c_0
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as vf:
                v_data = json.load(vf)
            v_data["chunk_hash"] = "corrupted_hash"
            with open(v_path, "w") as vf:
                json.dump(v_data, vf)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertFalse(summary.vectors_valid)
            self.assertTrue(any("hash mismatch" in e for e in summary.errors))

    def test_07_preflight_detects_invalid_vector_dimensions(self):
        """Vector embedding not exactly 768 dimensions must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # c_0 has 512 dimensions instead of 768
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as vf:
                v_data = json.load(vf)
            v_data["embedding"] = [0.01] * 512
            with open(v_path, "w") as vf:
                json.dump(v_data, vf)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertFalse(summary.dimensions_valid)
            self.assertTrue(any("dimension mismatch" in e for e in summary.errors))

    def test_08_preflight_detects_non_finite_vector_values(self):
        """Vector embedding containing NaN or Inf must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # c_0 has float('nan')
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as vf:
                v_data = json.load(vf)
            v_data["embedding"][10] = float("nan")
            with open(v_path, "w") as vf:
                json.dump(v_data, vf)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertFalse(summary.vectors_valid)
            self.assertTrue(any("non-finite value" in e for e in summary.errors))

    def test_09_preflight_detects_unexpected_model_name(self):
        """Vector embedding generated by unauthorized model must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as vf:
                v_data = json.load(vf)
            v_data["embedding_metadata"]["model"] = "text-embedding-ada-002"
            with open(v_path, "w") as vf:
                json.dump(v_data, vf)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("model mismatch" in e for e in summary.errors))

    def test_18_preflight_detects_missing_chunk_json_file(self):
        """Missing data/chunks/{chunk_id}.json must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Delete chunk c_5
            chunk_file = tmp_path / "chunks" / "c_5.json"
            chunk_file.unlink()

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Missing chunk JSON file for 'c_5'" in e for e in summary.errors))

    def test_19_preflight_detects_missing_vector_json_file(self):
        """Missing data/embeddings/vectors/{chunk_id}.json must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Delete vector c_10
            vector_file = tmp_path / "embeddings" / "vectors" / "c_10.json"
            vector_file.unlink()

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("c_10" in e for e in summary.errors))

    def test_20_preflight_detects_duplicate_vector_chunk_id(self):
        """Duplicate vector chunk_id across multiple vector files must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Add an extra vector file that reuses chunk_id 'c_0'
            extra_v = tmp_path / "embeddings" / "vectors" / "extra_duplicate.json"
            with open(extra_v, "w") as f:
                json.dump(
                    {
                        "chunk_id": "c_0",
                        "chunk_hash": "hash_0000",
                        "embedding": [0.01] * 768,
                        "embedding_metadata": {"model": "gemini-embedding-001"},
                    },
                    f,
                )

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("Duplicate vector chunk_id" in e for e in summary.errors))

    def test_21_preflight_detects_orphaned_vector_file(self):
        """Vector file with chunk_id not present in chunks manifest must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Add an orphaned vector file
            orphaned_v = tmp_path / "embeddings" / "vectors" / "c_orphaned.json"
            with open(orphaned_v, "w") as f:
                json.dump(
                    {
                        "chunk_id": "c_orphaned",
                        "chunk_hash": "hash_orphaned",
                        "embedding": [0.01] * 768,
                        "embedding_metadata": {"model": "gemini-embedding-001"},
                    },
                    f,
                )

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("orphaned vector" in e.lower() for e in summary.errors))

    def test_22_preflight_detects_empty_chunk_content(self):
        """Chunk file with empty or whitespace-only text must fail preflight."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Overwrite chunk c_0 with empty chunk_text
            chunk_file = tmp_path / "chunks" / "c_0.json"
            with open(chunk_file, "w") as f:
                json.dump({"chunk_id": "c_0", "chunk_text": "   ", "chunk_hash": "hash_0000"}, f)

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("empty content" in e for e in summary.errors))

    def test_23_preflight_detects_incomplete_vector_set(self):
        """Preflight must reject when vector IDs do not match the complete manifest set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # Rename vector file c_1 to c_unexpected, giving same count (145) but mismatched set
            v1 = tmp_path / "embeddings" / "vectors" / "c_1.json"
            v1.unlink()
            v_unexp = tmp_path / "embeddings" / "vectors" / "c_unexp.json"
            with open(v_unexp, "w") as f:
                json.dump(
                    {
                        "chunk_id": "c_unexp",
                        "chunk_hash": "hash_0001",
                        "embedding": [0.01] * 768,
                        "embedding_metadata": {"model": "gemini-embedding-001"},
                    },
                    f,
                )

            service = RAGIngestionService(data_dir=tmp_path)
            summary = service.run_preflight()
            self.assertFalse(summary.valid)
            self.assertTrue(any("missing vector files" in e.lower() or "orphaned" in e.lower() for e in summary.errors))


class TestRAGIngestionExecutionMocked(unittest.TestCase):
    """Tests for transactional ingestion, SQL parameters, FK order, and idempotency."""

    def setUp(self):
        self.service = RAGIngestionService()

    def test_10_ingest_all_executes_documents_before_chunks(self):
        """Strict foreign key order: documents must be inserted before rag_chunks."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)  # ingestion_id

        result = self.service.ingest_all(conn=mock_conn)
        self.assertTrue(result.success)

        sql_queries = [call_args[0][0] for call_args in mock_cur.executemany.call_args_list]

        # First executemany must be documents
        self.assertIn("INSERT INTO public.documents", sql_queries[0])
        # Subsequent executemany calls must be rag_chunks
        for q in sql_queries[1:]:
            self.assertIn("INSERT INTO public.rag_chunks", q)

    def test_11_ingest_all_uses_parameterized_queries(self):
        """All queries must use %s parameterization with zero raw interpolation."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        self.service.ingest_all(conn=mock_conn)

        for call_args in mock_cur.executemany.call_args_list:
            sql = call_args[0][0]
            self.assertIn("VALUES (%s", sql)
            self.assertNotIn("doc_stop_", sql)
            self.assertNotIn("doc_corridor_", sql)

    def test_12_ingest_all_conflict_targets_match_schema_pks(self):
        """
        Verify exact conflict targets:
          - public.documents: ON CONFLICT (document_id)
          - public.rag_chunks: ON CONFLICT (chunk_id)
        And verify that primary keys are NOT updated in DO UPDATE SET.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        self.service.ingest_all(conn=mock_conn)

        executed_queries = [call_args[0][0] for call_args in mock_cur.executemany.call_args_list]

        # 1. documents query
        doc_q = next(q for q in executed_queries if "INSERT INTO public.documents" in q)
        self.assertIn("ON CONFLICT (document_id) DO UPDATE SET", doc_q)
        self.assertNotIn("document_id = EXCLUDED.document_id", doc_q)

        # 2. rag_chunks query
        chunk_q = next(q for q in executed_queries if "INSERT INTO public.rag_chunks" in q)
        self.assertIn("ON CONFLICT (chunk_id) DO UPDATE SET", chunk_q)
        self.assertNotIn("chunk_id = EXCLUDED.chunk_id", chunk_q)

    def test_13_ingest_all_uses_jsonb_adaptation(self):
        """Document and chunk metadata dictionaries must be wrapped in Jsonb."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_cur.fetchone.return_value = (1,)

        captured_rows = {}

        def mock_executemany(sql, rows):
            if "INSERT INTO public.documents" in sql:
                captured_rows["documents"] = rows
            elif "INSERT INTO public.rag_chunks" in sql:
                captured_rows.setdefault("rag_chunks", []).extend(rows)

        mock_cur.executemany.side_effect = mock_executemany
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        self.service.ingest_all(conn=mock_conn)

        doc_rows = captured_rows.get("documents", [])
        self.assertEqual(len(doc_rows), EXPECTED_DOCUMENTS_COUNT)
        for row in doc_rows:
            metadata_param = row[6]
            self.assertIsInstance(metadata_param, Jsonb)

        chunk_rows = captured_rows.get("rag_chunks", [])
        self.assertEqual(len(chunk_rows), EXPECTED_CHUNKS_COUNT)
        for row in chunk_rows:
            metadata_param = row[6]
            self.assertIsInstance(metadata_param, Jsonb)

    def test_14_ingest_all_preserves_vector_dimensions_and_values(self):
        """Vector embedding objects must have exact 768 dimensions and match source values."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_cur.fetchone.return_value = (1,)

        captured_chunks = []

        def mock_executemany(sql, rows):
            if "INSERT INTO public.rag_chunks" in sql:
                captured_chunks.extend(rows)

        mock_cur.executemany.side_effect = mock_executemany
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        self.service.ingest_all(conn=mock_conn)

        self.assertEqual(len(captured_chunks), EXPECTED_CHUNKS_COUNT)
        for row in captured_chunks:
            embedding_val = row[7]
            if hasattr(embedding_val, "dimensions"):
                self.assertEqual(embedding_val.dimensions(), EXPECTED_EMBEDDING_DIMENSIONS)
            elif isinstance(embedding_val, list):
                self.assertEqual(len(embedding_val), EXPECTED_EMBEDDING_DIMENSIONS)
            elif isinstance(embedding_val, str):
                self.assertTrue(embedding_val.startswith("["))
                self.assertTrue(embedding_val.endswith("]"))
                self.assertEqual(len(embedding_val.split(",")), EXPECTED_EMBEDDING_DIMENSIONS)

    def test_15_ingest_all_rolls_back_on_error(self):
        """Database exception must cause transaction rollback and log failure."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.transaction.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        def side_effect(sql, *args, **kwargs):
            if "INSERT INTO public.rag_chunks" in sql:
                raise psycopg.Error("Simulated database failure on rag_chunks")
            return None

        mock_cur.executemany.side_effect = side_effect

        result = self.service.ingest_all(conn=mock_conn)
        self.assertFalse(result.success)
        self.assertIn("Simulated database failure on rag_chunks", result.error_message)


class TestRAGStrictLoading(unittest.TestCase):
    """Tests for strengthened load_documents and load_chunks_and_vectors methods."""

    def test_24_load_chunks_and_vectors_raises_on_missing_chunk_file(self):
        """load_chunks_and_vectors must raise FileNotFoundError if chunk file is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            (tmp_path / "chunks" / "c_0.json").unlink()

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(FileNotFoundError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("Chunk file missing for 'c_0'", str(ctx.exception))

    def test_25_load_chunks_and_vectors_raises_on_missing_vector_file(self):
        """load_chunks_and_vectors must raise FileNotFoundError if vector file is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            (tmp_path / "embeddings" / "vectors" / "c_0.json").unlink()

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(FileNotFoundError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("Vector file missing for 'c_0'", str(ctx.exception))

    def test_26_load_chunks_and_vectors_raises_on_empty_content(self):
        """load_chunks_and_vectors must raise ValueError if chunk text is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            with open(tmp_path / "chunks" / "c_0.json", "w") as f:
                json.dump({"chunk_id": "c_0", "chunk_text": "", "chunk_hash": "hash_0000"}, f)

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(ValueError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("empty content", str(ctx.exception))

    def test_27_load_chunks_and_vectors_raises_on_hash_mismatch(self):
        """load_chunks_and_vectors must raise ValueError if vector hash != manifest hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as f:
                v_data = json.load(f)
            v_data["chunk_hash"] = "mismatched_hash"
            with open(v_path, "w") as f:
                json.dump(v_data, f)

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(ValueError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("hash mismatch", str(ctx.exception))

    def test_28_load_chunks_and_vectors_raises_on_dimension_mismatch(self):
        """load_chunks_and_vectors must raise ValueError if vector dimensions != 768."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as f:
                v_data = json.load(f)
            v_data["embedding"] = [0.01] * 767
            with open(v_path, "w") as f:
                json.dump(v_data, f)

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(ValueError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("dimension", str(ctx.exception))

    def test_29_load_chunks_and_vectors_raises_on_non_finite_values(self):
        """load_chunks_and_vectors must raise ValueError if embedding has NaN or Inf."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)
            v_path = tmp_path / "embeddings" / "vectors" / "c_0.json"
            with open(v_path, "r") as f:
                v_data = json.load(f)
            v_data["embedding"][0] = float("inf")
            with open(v_path, "w") as f:
                json.dump(v_data, f)

            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(ValueError) as ctx:
                service.load_chunks_and_vectors()
            self.assertIn("non-finite", str(ctx.exception))

    def test_30_load_documents_raises_on_missing_file_or_empty_content(self):
        """load_documents must raise FileNotFoundError or ValueError if doc is missing/empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            create_mock_rag_dataset(tmp_path)

            # 1. Missing file
            (tmp_path / "documents" / "doc_0.json").unlink()
            service = RAGIngestionService(data_dir=tmp_path)
            with self.assertRaises(FileNotFoundError):
                service.load_documents()

            # 2. Empty content
            with open(tmp_path / "documents" / "doc_0.json", "w") as f:
                json.dump({"document_id": "doc_0", "content": "  ", "document_hash": "hash_0"}, f)
            with self.assertRaises(ValueError):
                service.load_documents()


class TestRAGDatabaseVerificationMocked(unittest.TestCase):
    """Tests for live database row count verification logic."""

    def setUp(self):
        self.service = RAGIngestionService()

    def test_16_verify_database_detects_discrepancy(self):
        """When row counts diverge, verify_database must report discrepancy."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (85,),   # documents (expected 86!)
            (145,),  # rag_chunks (expected 145)
            None,    # latest log
        ]

        result = self.service.verify_database(conn=mock_conn)
        self.assertFalse(result.all_matched)
        self.assertIn("documents", result.discrepancies)
        self.assertEqual(result.discrepancies["documents"]["expected"], 86)
        self.assertEqual(result.discrepancies["documents"]["actual"], 85)

    def test_17_verify_database_passes_on_matching_counts(self):
        """When row counts match exactly, verify_database passes."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (86,),   # documents
            (145,),  # rag_chunks
            (
                10, "v1.0.0-sholinganallur", "hash123", "RAG_DOCUMENTS_AND_EMBEDDINGS",
                "SUCCESS", 231, 0, None,
                datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 9, 8, 1, tzinfo=timezone.utc),
                Decimal("1.25"),
            ),
        ]

        result = self.service.verify_database(conn=mock_conn)
        self.assertTrue(result.all_matched)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(result.actual_counts["documents"], 86)
        self.assertEqual(result.actual_counts["rag_chunks"], 145)
        self.assertIsNotNone(result.latest_ingestion_log)

    def test_18_register_pgvector_calls_register_vector_with_schema_extensions(self):
        """register_pgvector must explicitly invoke register_vector with schema='extensions'."""
        class FakeRealConn:
            def __init__(self):
                self.adapters = object()

        real_like_conn = FakeRealConn()
        with patch("app.db.rag_ingestion.register_vector") as mock_reg:
            register_pgvector(real_like_conn)
            mock_reg.assert_called_once_with(real_like_conn, schema="extensions")

    def test_19_register_pgvector_raises_on_real_connection_failure(self):
        """register_pgvector must not swallow errors on a real connection and must raise RuntimeError."""
        class FakeRealConn:
            def __init__(self):
                self.adapters = object()

        real_like_conn = FakeRealConn()
        with patch("app.db.rag_ingestion.register_vector", side_effect=psycopg.ProgrammingError("Extension missing")):
            with self.assertRaises(RuntimeError) as ctx:
                register_pgvector(real_like_conn)
            self.assertIn("pgvector registration in 'extensions' schema failed", str(ctx.exception))
            self.assertIn("Extension missing", str(ctx.exception))

    def test_20_register_pgvector_mock_connection_compatibility(self):
        """register_pgvector must handle MagicMock connections gracefully without crashing."""
        mock_conn = MagicMock()
        # Should not raise any error
        register_pgvector(mock_conn)
        register_pgvector(mock_conn, schema="extensions")

    def test_21_smoke_test_pgvector_success_mock(self):
        """smoke_test_pgvector succeeds and confirms 768 dimensions without writing data."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        fake_vector = [0.01] * EXPECTED_EMBEDDING_DIMENSIONS
        mock_cur.fetchone.return_value = (fake_vector,)

        result = smoke_test_pgvector(conn=mock_conn, schema="extensions")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["schema"], "extensions")
        self.assertEqual(result["actual_dimensions"], 768)
        self.assertTrue(result["round_trip_valid"])
        self.assertTrue(result["zero_writes_verified"])

    def test_22_smoke_test_pgvector_dimension_mismatch_raises(self):
        """smoke_test_pgvector raises ValueError if returned vector dimension != 768."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        fake_vector = [0.01] * 512  # incorrect dimension!
        mock_cur.fetchone.return_value = (fake_vector,)

        with self.assertRaises(ValueError) as ctx:
            smoke_test_pgvector(conn=mock_conn, schema="extensions")
        self.assertIn("Vector dimension mismatch", str(ctx.exception))

    def test_23_smoke_test_pgvector_non_finite_value_raises(self):
        """smoke_test_pgvector raises ValueError if returned vector contains non-finite values."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        fake_vector = [0.01] * 767 + [float("nan")]
        mock_cur.fetchone.return_value = (fake_vector,)

        with self.assertRaises(ValueError) as ctx:
            smoke_test_pgvector(conn=mock_conn, schema="extensions")
        self.assertIn("non-finite value", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
