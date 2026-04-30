from __future__ import annotations

from functools import lru_cache

from ..config.document_processing_config import DocumentProcessingConfig
from .embeddings import EmbeddingClient
from .generator import LLMGenerator
from .vector_client import VectorClient


@lru_cache(maxsize=1)
def get_retrieval_config() -> DocumentProcessingConfig:
    return DocumentProcessingConfig()


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    return EmbeddingClient(get_retrieval_config())


@lru_cache(maxsize=1)
def get_vector_client() -> VectorClient:
    return VectorClient(get_retrieval_config())


@lru_cache(maxsize=1)
def get_llm_generator() -> LLMGenerator:
    return LLMGenerator(get_retrieval_config())

