import os
os.environ["DATABASE_URL"] = "mongodb://localhost:27017/contraclaim"
os.environ["LOCAL_MONGODB_URI"] = "mongodb://localhost:27017/contraclaim"
os.environ["MONGODB_URI"] = "mongodb://localhost:27017/contraclaim"
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
cfg = DocumentProcessingConfig()
print(cfg.mongo_uri)
