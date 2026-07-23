import asyncio

try:
    from .mongodb_reader import MongoDBReader
    from .langchain_vector_service import LangChainVectorService
    from ..config.document_processing_config import DocumentProcessingConfig
except ImportError:  # pragma: no cover - script compatibility
    from services.mongodb_reader import MongoDBReader
    from services.langchain_vector_service import LangChainVectorService
    from config.document_processing_config import DocumentProcessingConfig

async def sync_data():
    config = DocumentProcessingConfig()
    mongodb_reader = MongoDBReader(config)
    langchain_service = LangChainVectorService(config)

    data = await mongodb_reader.read_contraclaim_data()
    payloads = []
    for document in data:
        payload = {
            "text": document.get("full_text", ""),
            "metadata": {
                "document_id": str(document["_id"]),
                "chunk_index": 0,  # For simplicity, we're not chunking here
            }
        }
        payloads.append(payload)

    if langchain_service.enabled:
        await langchain_service.replace_document(payloads)

    await mongodb_reader.close_connection()

if __name__ == "__main__":
    asyncio.run(sync_data())
