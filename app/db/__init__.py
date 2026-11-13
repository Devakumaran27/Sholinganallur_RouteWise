"""
Database module for Supabase PostgreSQL connection management, pgvector operations,
and structured GTFS dataset ingestion.
"""

from app.db.connections import get_connection, get_sync_connection, mask_database_url
from app.db.gtfs_ingestion import GTFSIngestionService
from app.db.rag_ingestion import (
    RAGIngestionResult,
    RAGIngestionService,
    RAGPreflightSummary,
    RAGVerificationResult,
)

__all__ = [
    "get_connection",
    "get_sync_connection",
    "mask_database_url",
    "GTFSIngestionService",
    "RAGIngestionService",
    "RAGPreflightSummary",
    "RAGIngestionResult",
    "RAGVerificationResult",
]
