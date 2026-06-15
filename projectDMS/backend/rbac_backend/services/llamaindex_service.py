import asyncio
import hashlib
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Sequence

from llama_index.core import Document, VectorStoreIndex
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.mongodb import MongoDBAtlasVectorSearch
from pymongo import MongoClient
from pymongo.server_api import ServerApi

from ..utils.exceptions import DocumentProcessingError

logger = logging.getLogger(__name__)

class LlamaIndexVectorService:
    """
    Vector service for MongoDB 8 with local Vector Search support.
    
    Works with both Atlas and local MongoDB 8 deployments that have
    Vector Search enabled. For local deployments, ensure MongoDB 8.0+
    is running with integrated Search/Vector Search (mongot) enabled.
    """

    def __init__(
        self,
        mongo_uri: str,
        database_name: str,
        collection_name: str,
        embedding_model: str,
        openai_api_key: Optional[str] = None,
        existing_mongo_client: Optional[MongoClient] = None,
    ):
        self._mongo_uri = mongo_uri
        self._database_name = database_name
        self._collection_name = collection_name
        self._embedding_model_name = embedding_model
        
        # FIXED: Actually read OpenAI API key instead of storing the string literal
        self._openai_api_key = openai_api_key or os.environ.get('OPENAI_API_KEY')
        
        self._mongo_client: Optional[MongoClient] = existing_mongo_client
        self._owns_mongo_client: bool = existing_mongo_client is None
        self._vector_store: Optional[MongoDBAtlasVectorSearch] = None
        self._embedding_model: Optional[OpenAIEmbedding] = None
        self._index: Optional[VectorStoreIndex] = None

    @property
    def embedding_model_name(self) -> str:
        return self._embedding_model_name

    def _ensure_mongo_client(self) -> MongoClient:
        """Initialize MongoDB client with proper configuration for Atlas vs Local."""
        if self._mongo_client is None:
            try:
                # FIXED: Flip ServerApi logic - only use ServerApi for Atlas SRV URIs
                # Local mongodb:// URIs should not use ServerApi
                client_kwargs = {}
                if self._mongo_uri.startswith("mongodb+srv://"):
                    client_kwargs["server_api"] = ServerApi('1')
                    logger.info("Using ServerApi for Atlas SRV connection")
                else:
                    logger.info("Using direct connection for local MongoDB")
                
                self._mongo_client = MongoClient(self._mongo_uri, **client_kwargs)
                
                # Ping to verify connection
                self._mongo_client.admin.command("ping")
                logger.info("MongoClient initialized successfully for URI: %s", 
                          self._mongo_uri.split('@')[-1] if '@' in self._mongo_uri else self._mongo_uri)
                
            except Exception as exc:
                raise DocumentProcessingError(f"Failed to initialize Mongo client: {exc}") from exc
        
        return self._mongo_client

    def _ensure_embedding_model(self) -> OpenAIEmbedding:
        """Initialize OpenAI embedding model with proper API key handling."""
        if self._embedding_model is None:
            try:
                # FIXED: Pass API key explicitly to embedding model
                embedding_kwargs = {"model": self._embedding_model_name}
                if self._openai_api_key:
                    embedding_kwargs["api_key"] = self._openai_api_key
                
                self._embedding_model = OpenAIEmbedding(**embedding_kwargs)
                logger.info("OpenAIEmbedding model initialized: %s", self._embedding_model_name)
                
            except Exception as exc:
                raise DocumentProcessingError(f"Failed to initialize OpenAI embedding model: {exc}") from exc
        
        return self._embedding_model

    def _ensure_vector_store(self) -> MongoDBAtlasVectorSearch:
        """Initialize MongoDB vector store with proper indexing for local or Atlas."""
        if self._vector_store is None:
            try:
                client = self._ensure_mongo_client()
                db = client[self._database_name]
                collection = db[self._collection_name]

                # Create standard B-tree indexes for metadata filtering
                # Using PyMongo 4.10+ syntax: create_index instead of ensure_index
                try:
                    collection.create_index([("organization_id", 1), ("project_id", 1), ("uploadType", 1)])
                    logger.info("Created metadata indexes on collection")
                except Exception as idx_exc:
                    logger.warning("Failed to create indexes (may already exist): %s", idx_exc)

                # UPDATED COMMENT: For local MongoDB 8.0+, pre-create Search index via mongosh:
                # db.runCommand({
                #   createSearchIndexes: "<collection_name>",
                #   indexes: [{
                #     name: "vector_index",
                #     definition: {
                #       type: "vectorSearch",
                #       fields: [{
                #         type: "vector",
                #         path: "embedding",
                #         numDimensions: 1536, // for text-embedding-3-small
                #         similarity: "cosine"
                #       }]
                #     }
                #   }]
                # });
                #
                # For Atlas: Create vector search index via Atlas UI on 'embedding' field

                self._vector_store = MongoDBAtlasVectorSearch(
                    mongodb_client=client,
                    db_name=self._database_name,
                    collection_name=self._collection_name,
                    embed_model=self._ensure_embedding_model(),
                    collection=collection  # Explicitly pass collection
                )

                self._index = VectorStoreIndex.from_vector_store(vector_store=self._vector_store)
                logger.info("MongoDB vector store initialized successfully for %s", 
                          "Atlas" if self._mongo_uri.startswith("mongodb+srv://") else "local MongoDB 8+")
                
            except Exception as exc:
                raise DocumentProcessingError(f"Failed to initialize MongoDB vector store: {exc}") from exc
        
        return self._vector_store

    async def index_chunks(self, payloads: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create embeddings, push to Mongo vector store, and return metadata."""
        if not payloads:
            logger.warning("No payloads provided for indexing")
            return []

        texts = [str(item.get("text", "")) for item in payloads]
        embed_model = self._ensure_embedding_model()

        # Batch embedding (LlamaIndex handles parallelism)
        embeddings = await asyncio.to_thread(embed_model.get_text_embedding_batch, texts)

        docs: List[Document] = []
        results: List[Dict[str, Any]] = []

        for payload, embedding in zip(payloads, embeddings):
            metadata = dict(payload.get("metadata") or {})
            text = str(payload.get("text", ""))
            checksum = payload.get("checksum")

            if metadata.get("chunk_index") is None:
                metadata["chunk_index"] = len(docs)

            if checksum and not metadata.get("checksum_sha256"):
                metadata["checksum_sha256"] = checksum

            embedding_id = payload.get("embedding_id") or self._generate_embedding_id(metadata, text)
            metadata["embedding_id"] = embedding_id

            doc = Document(
                text=text,
                metadata=metadata,
                embedding=embedding,
            )

            docs.append(doc)
            results.append({
                "embedding_id": embedding_id,
                "vector_ref": None,
                "metadata": metadata,
                "text": text,
                "embedding": embedding,
            })

        vector_store = self._ensure_vector_store()

        # Add to vector store asynchronously via thread (LlamaIndex add is sync)
        def _persist_nodes():
            node_ids = vector_store.add(nodes=docs)
            return node_ids

        try:
            node_ids = await asyncio.to_thread(_persist_nodes)
            
            if len(node_ids) != len(results):
                raise DocumentProcessingError(
                    f"Vector store returned {len(node_ids)} ids for {len(results)} chunks"
                )

            for record, node_id in zip(results, node_ids):
                record["vector_ref"] = str(node_id)

            logger.info("Indexed %d chunks into MongoDB vector store", len(results))
            return results

        except Exception as exc:
            raise DocumentProcessingError(f"Failed to add documents to vector store: {exc}") from exc

    async def delete_vectors(self, vector_refs: Sequence[Optional[str]]) -> None:
        """Delete vectors by node IDs (ref_doc_id)."""
        if not vector_refs:
            return

        vector_store = self._ensure_vector_store()

        def _delete_nodes():
            for ref in vector_refs:
                if not ref:
                    continue
                try:
                    vector_store.delete(ref_doc_id=ref)
                    logger.debug("Deleted vector ref %s", ref)
                except Exception as e:
                    logger.warning("Failed to delete vector ref %s: %s", ref, e)

        try:
            await asyncio.to_thread(_delete_nodes)
        except Exception as exc:
            logger.warning("Vector store delete operation raised: %s", exc)

    async def close(self) -> None:
        """Close connections."""
        if self._index:
            self._index = None

        if self._vector_store:
            self._vector_store = None

        if self._mongo_client and self._owns_mongo_client:
            await asyncio.to_thread(self._mongo_client.close)
        self._mongo_client = None

        if self._embedding_model:
            # OpenAIEmbedding doesn't have explicit close
            self._embedding_model = None

        logger.info("LlamaIndexVectorService connections closed")

    def _generate_embedding_id(self, metadata: Dict[str, Any], text: str) -> str:
        """Generate unique ID for embedding."""
        seed = metadata.get("document_id") or metadata.get("upload_id") or "chunk"
        chunk_index = metadata.get("chunk_index", 0)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}:{chunk_index}:{checksum}"))