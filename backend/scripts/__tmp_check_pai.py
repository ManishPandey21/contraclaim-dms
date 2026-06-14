import os
os.environ['DATABASE_URL'] = 'mongodb://localhost:27017/contraclaim'
os.environ['LOCAL_MONGODB_URI'] = 'mongodb://localhost:27017/contraclaim'
os.environ['MONGODB_URI'] = 'mongodb://localhost:27017/contraclaim'

from rbac_backend.services.document_processor import create_document_processor, DocumentProcessingConfig

config = DocumentProcessingConfig()
print('use_pydantic_ai:', config.use_pydantic_ai)
processor = create_document_processor(config)
service = processor.pydantic_ai_service
print('service enabled:', getattr(service, 'is_enabled', False))
print('service type:', type(service).__name__)
