# services/openai_service.py

import asyncio
import logging
import os
import mimetypes
from typing import Any, Iterable, List, Optional

from openai import AsyncOpenAI

from ..config.document_processing_config import DocumentProcessingConfig
from ..utils.exceptions import DocumentProcessingError

logger = logging.getLogger(__name__)

class OpenAIService:
    """Service for OpenAI API interactions"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = self._initialize_client()
    
    def _initialize_client(self) -> AsyncOpenAI:
        """Initialize OpenAI client"""
        try:
            api_key = self._get_api_key()
            if not api_key:
                raise DocumentProcessingError("OpenAI API key not configured")
            
            return AsyncOpenAI(api_key=api_key, timeout=self.config.openai_timeout)
            
        except ImportError:
            raise DocumentProcessingError("OpenAI library not available")
        except Exception as e:
            raise DocumentProcessingError(f"Failed to initialize OpenAI client: {e}")
    
    def _get_api_key(self) -> Optional[str]:
        """Get OpenAI API key from settings or environment"""
        return self.config.openai_api_key
    
    async def upload_file(self, file_path: str, max_retries: int = 3) -> str:
        """
        Upload file to OpenAI and return file ID.
        
        Args:
            file_path: Path to file to upload
            max_retries: Maximum number of retry attempts
            
        Returns:
            OpenAI file ID
            
        Raises:
            DocumentProcessingError: If upload fails
        """
        for attempt in range(max_retries):
            try:
                loop = asyncio.get_event_loop()
                filename = os.path.basename(file_path)
                safe_filename = filename.lower() if filename else "document.pdf"
                mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

                # Stream the file to the API via an open handle instead of reading
                # the entire file into memory (M9): the SDK reads it incrementally.
                file_handle = await loop.run_in_executor(None, lambda: open(file_path, "rb"))
                try:
                    response = await self._client.files.create(
                        file=(safe_filename, file_handle, mime_type),
                        purpose="assistants"
                    )
                finally:
                    await loop.run_in_executor(None, file_handle.close)

                return response.id
                
            except Exception as e:
                if attempt == max_retries - 1:
                    raise DocumentProcessingError(f"File upload failed after {max_retries} retries: {e}")
                
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    async def process_document(self, file_id: str) -> str:
        """
        Process document using OpenAI and return extracted content.
        
        Args:
            file_id: OpenAI file ID
            
        Returns:
            Extracted content from document
            
        Raises:
            DocumentProcessingError: If processing fails
        """
        try:
            extraction_prompt = self._get_extraction_prompt()
            
            response = await self._client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": extraction_prompt},
                            {"type": "file", "file": {"file_id": file_id}}
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            if not response.choices or not response.choices[0].message.content:
                raise DocumentProcessingError("No content extracted from document")

            content = response.choices[0].message.content
            normalized_content = self._normalize_message_content(content)
            if not normalized_content.strip():
                raise DocumentProcessingError("No textual content extracted from document")

            return normalized_content
            
        except Exception as e:
            # Log the detailed error for debugging
            logger.error(f"OpenAI API call failed with file_id {file_id}: {str(e)}")
            logger.error(f"Model used: {self._get_model_name()}")
            raise DocumentProcessingError(f"Document processing failed: {e}")

    def _normalize_message_content(self, content: Any) -> str:
        """
        Flatten chat completion message content into a plain string, regardless of SDK structure.

        Recent OpenAI SDK versions often return a list of content parts instead of a single string.
        This helper gathers all textual fragments so downstream parsers always receive text.
        """
        if isinstance(content, str):
            return content

        parts: List[str] = []

        if isinstance(content, Iterable):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue

                if isinstance(item, dict):
                    text_value = item.get("text") or item.get("content") or item.get("value")
                    if isinstance(text_value, str):
                        parts.append(text_value)
                    continue

                text_attr = getattr(item, "text", None)
                if isinstance(text_attr, str):
                    parts.append(text_attr)
                    continue

                content_attr = getattr(item, "content", None)
                if isinstance(content_attr, str):
                    parts.append(content_attr)
                    continue

                value_attr = getattr(item, "value", None)
                if isinstance(value_attr, str):
                    parts.append(value_attr)
                    continue

        if parts:
            return "\n".join(part for part in parts if part)

        return str(content)

    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings using OpenAI API"""
        try:
            response = await self._client.embeddings.create(
                model=self.config.openai_embedding_model,
                input=texts
            )
            
            embeddings = [data.embedding for data in response.data]
            return embeddings
            
        except Exception as e:
            raise DocumentProcessingError(f"Embedding creation failed: {e}")
    
    async def cleanup_file(self, file_id: str) -> None:
        """Clean up uploaded file from OpenAI"""
        try:
            await self._client.files.delete(file_id)
        except Exception as e:
            # Log but don't raise - cleanup is best-effort
            pass
    
    def _get_extraction_prompt(self) -> str:
        """Get extraction prompt for document processing"""
        return (
            "You are a Contract expert extracting structured metadata from letters. "
            "Extract the following fields from the attached PDF and return them in this exact format:\n\n"
            "1) Date: [extracted date or 'Not found' formatted as DD-MM-YYYY]\n"
            "2) Letter No.: [extracted letter number or 'Not found']\n"
            "3) From (Company): [sender company or 'Not found']\n"
            "4) To (Company): [recipient company or 'Not found']\n"
            "5) Subject: [document subject or 'Not found']\n"
            "6) References: [list each reference on a new line with - prefix or 'Not found']\n"
            "7) Summary: [3-7 concise bullet points with - prefix summarizing the content]\n"
            "8) Key Words: [comma-separated list of key contractual words mentioned]\n"
            "9) Contractual Clauses: [comma-separated list of specific mentioned contractual clauses]\n"
            "10) Full content: [cleaned text of the full letter]\n"
        )
    
    def _get_model_name(self) -> str:
        """Get model name from config or default"""
        return getattr(self.config, 'openai_model', 'gpt-4o')
