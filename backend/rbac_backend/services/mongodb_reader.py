import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

try:
    from ..config.document_processing_config import DocumentProcessingConfig
except ImportError:  # pragma: no cover - script compatibility
    from config.document_processing_config import DocumentProcessingConfig

class MongoDBReader:
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self.client = AsyncIOMotorClient(self.config.mongo_uri)
        self.db = self.client[self.config.database_name]

    async def read_contraclaim_data(self):
        collection = self.db['contraclaim']
        cursor = collection.find({})
        documents = await cursor.to_list(length=1000)  # Adjust the length as needed
        return documents

    async def close_connection(self):
        self.client.close()

# Example usage
async def main():
    config = DocumentProcessingConfig()  # Assuming this is how you initialize your config
    reader = MongoDBReader(config)
    data = await reader.read_contraclaim_data()
    print(data)
    await reader.close_connection()

if __name__ == "__main__":
    asyncio.run(main())
