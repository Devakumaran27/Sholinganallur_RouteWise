"""
Module: app.schemas.rag
Purpose: Pydantic request and response models for semantic RAG search
         and Gemini-powered grounded transit question-answering.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ==============================================================================
# RAG Semantic Retrieval Models
# ==============================================================================

class RAGSearchRequest(BaseModel):
    """Request payload for semantic similarity search on transit chunks."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language search query or question",
        examples=["Which bus connects Sholinganallur to T. Nagar directly?"],
    )
    top_k: int = Field(
        5,
        ge=1,
        le=20,
        description="Maximum number of relevant chunks to retrieve",
    )
    chunk_type: Optional[str] = Field(
        None,
        description="Optional filter by chunk type (e.g. 'CORRIDOR_GUIDE', 'ROUTE_PROFILE', 'STOP_PROFILE')",
    )

    model_config = ConfigDict(extra="forbid")


class RAGChunkItem(BaseModel):
    """Retrieved semantic chunk with metadata and similarity score."""
    chunk_id: str = Field(..., description="Unique chunk identifier")
    document_id: str = Field(..., description="Parent document identifier")
    chunk_type: str = Field(..., description="Category of transit chunk")
    title: str = Field(..., description="Descriptive chunk heading")
    content: str = Field(..., description="Full text content of the chunk")
    similarity: float = Field(..., description="Cosine similarity score (-1.0 to 1.0)")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Structured chunk metadata")

    model_config = ConfigDict(from_attributes=True)


class RAGSearchResponse(BaseModel):
    """Response envelope for RAG semantic retrieval."""
    query: str = Field(..., description="Original user search query")
    retrieved_count: int = Field(..., description="Total chunks retrieved")
    chunks: List[RAGChunkItem] = Field(default_factory=list, description="Top-K ranked semantic chunks")


# ==============================================================================
# Grounded Chat / Question-Answering Models
# ==============================================================================

class ChatRequest(BaseModel):
    """Request payload for grounded transit question answering."""
    question: str = Field(
        ...,
        min_length=2,
        max_length=1000,
        description="User transport question regarding routes, stops, or timings",
        examples=["How do I travel from Sholinganallur to T. Nagar around 9 AM?"],
    )
    top_k: int = Field(
        4,
        ge=1,
        le=10,
        description="Number of RAG chunks to retrieve for grounding context",
    )

    model_config = ConfigDict(extra="forbid")


class ChatGroundingSource(BaseModel):
    """Citation reference for grounding knowledge chunks."""
    chunk_id: str = Field(..., description="Cited knowledge chunk identifier")
    document_id: str = Field(..., description="Parent document identifier")
    title: str = Field(..., description="Title of the cited chunk")
    similarity: Optional[float] = Field(None, description="Relevance score to query")

    model_config = ConfigDict(from_attributes=True)


class ChatResponse(BaseModel):
    """Response envelope for grounded transit question answering."""
    question: str = Field(..., description="Original user question")
    answer: str = Field(..., description="Grounded, synthesized transport answer")
    grounding_sources: List[ChatGroundingSource] = Field(
        default_factory=list,
        description="Source documents and chunks used to generate the answer",
    )
    structured_context_used: Optional[Dict[str, Any]] = Field(
        None,
        description="Structured transit data referenced (e.g. direct routes, departure counts)",
    )
    limitations: List[str] = Field(
        default_factory=list,
        description="Known transit dataset limitations and operational boundaries",
    )

    model_config = ConfigDict(from_attributes=True)

