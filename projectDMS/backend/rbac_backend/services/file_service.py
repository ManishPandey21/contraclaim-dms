# services/file_service.py

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional

from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)


class FileService:
    """Service for file system operations"""

    _SUMMARY_FILENAMES = {
        "incoming": "incoming.md",
        "outgoing": "outgoing.md",
    }

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config

    async def save_summary(
        self,
        content: str,
        source_path: str,
        path_structure: str,
        upload_type: str,
    ) -> None:
        """Append an extracted correspondence report to its Markdown summary."""
        try:
            summary_dir = Path(self.config.uploads_dir) / path_structure
            summary_path = summary_dir / self._summary_filename(upload_type)
            summary_dir.mkdir(parents=True, exist_ok=True)

            full_content = self._format_summary_markdown(content, source_path)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._append_text,
                summary_path,
                full_content,
            )

            logger.info("Summary saved to: %s", summary_path)

        except Exception as e:  # pragma: no cover - log and continue
            logger.error("Failed to save summary: %s", e)

    @classmethod
    def _summary_filename(cls, upload_type: str) -> str:
        """Return the canonical Markdown filename for a correspondence scope."""
        return cls._SUMMARY_FILENAMES.get(upload_type, "projectid.md")

    @staticmethod
    def _format_summary_markdown(content: str, source_path: str) -> str:
        """Wrap an extraction in Markdown without altering its document structure."""
        source_name = Path(source_path).name.replace("\r", " ").replace("\n", " ")
        body = content if content else "_No extracted content was available._"

        # Keep the extractor output verbatim. It may already contain headings,
        # paragraphs, lists, tables, or reference links; escaping or code-fencing
        # it here would discard its Markdown semantics.
        return f"\n\n## Source: {source_name}\n\n{body.rstrip()}\n\n---\n"

    @staticmethod
    def _append_text(path: Path, content: str) -> None:
        """Append UTF-8 content while closing the file handle deterministically."""
        with path.open("a", encoding="utf-8") as summary_file:
            summary_file.write(content)


class SecureFileService(FileService):
    """Secure wrapper for file service operations"""

    def __init__(
        self,
        config: Optional[DocumentProcessingConfig] = None,
        base_dir: Optional[str] = None,
    ):
        if base_dir:
            config = DocumentProcessingConfig()
            config.uploads_dir = base_dir
        elif config is None:
            config = DocumentProcessingConfig()

        super().__init__(config)
        self.base_dir = Path(self.config.uploads_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_dir = self.base_dir / "__chunks"
        self.chunk_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_segment(value: Optional[str]) -> str:
        """Normalize path segments to avoid directory traversal."""
        segment = (value or "unknown").strip() or "unknown"
        segment = segment.replace("\\", "_").replace("/", "_")
        return segment.replace("..", "_")

    def _target_dir(self, organization_id: Optional[str], project_id: Optional[str]) -> Path:
        org_segment = self._safe_segment(organization_id or "unassigned")
        project_segment = self._safe_segment(project_id or "default")
        target_dir = self.base_dir / org_segment / project_segment
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    async def store_document(
        self,
        content: bytes,
        organization_id: str,
        project_id: str,
        filename: str,
        stored_filename: Optional[str] = None,
        path_structure: Optional[str] = None,
        compression_enabled: bool = False,
    ) -> str:
        """
        Persist uploaded document bytes to a secure location and return the absolute path.
        If path_structure is provided, it is used relative to the uploads base dir.
        """
        if not filename:
            raise ValueError("Filename is required to store document")

        if compression_enabled:
            content = await self._compress_content(content, Path(filename).suffix)

        if path_structure:
            safe_segments = [
                self._safe_segment(s) for s in path_structure.strip("/").split("/") if s.strip()
            ]
            target_dir = self.base_dir.joinpath(*safe_segments)
        else:
            target_dir = self._target_dir(organization_id, project_id)

        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_name = Path(stored_filename or filename).name
        file_path = target_dir / resolved_name
        loop = asyncio.get_running_loop()

        def _write_file() -> None:
            file_path.write_bytes(content)

        await loop.run_in_executor(None, _write_file)
        logger.info("Stored document at: %s", file_path)
        return str(file_path.resolve())

    async def store_existing_file(
        self,
        source_path: Path,
        organization_id: str,
        project_id: str,
        filename: str,
        stored_filename: Optional[str] = None,
        path_structure: Optional[str] = None,
    ) -> str:
        if not filename:
            raise ValueError("Filename is required to store document")
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Source file not found: {source}")
        if path_structure:
            safe_segments = [
                self._safe_segment(s) for s in path_structure.strip("/").split("/") if s.strip()
            ]
            target_dir = self.base_dir.joinpath(*safe_segments)
        else:
            target_dir = self._target_dir(organization_id, project_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / Path(stored_filename or filename).name

        def _copy() -> None:
            shutil.copyfile(source, target_path)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _copy)
        logger.info("Stored existing file at: %s", target_path)
        return str(target_path.resolve())

    async def _compress_content(self, content: bytes, extension: str) -> bytes:
        """Compress content based on file type."""
        ext = extension.lower()
        if ext == ".pdf":
            logger.info("PDF compression placeholder - returning original")
            return content
        if ext in [".txt", ".doc", ".docx"]:
            import gzip

            try:
                return gzip.compress(content)
            except Exception as e:
                logger.error("Compression failed: %s", e)
        return content

    async def store_file(
        self,
        upload_file,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        stored_filename: Optional[str] = None,
    ) -> Path:
        """Persist an UploadFile to the secure uploads directory."""
        if not filename:
            raise ValueError("Filename is required to store upload")

        target_dir = self._target_dir(organization_id, project_id)
        target_path = target_dir / Path(stored_filename or filename).name

        data = await upload_file.read()
        await upload_file.seek(0)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, target_path.write_bytes, data)
        return target_path.resolve()

    async def store_chunk(
        self,
        chunk_file,
        upload_id: str,
        chunk_index: int,
        filename: str,
        *,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Store a chunk for later merging."""
        if chunk_index < 0:
            raise ValueError("Chunk index cannot be negative")

        chunk_folder = (
            self.chunk_dir
            / self._safe_segment(organization_id or "unassigned")
            / self._safe_segment(user_id or "anonymous")
            / self._safe_segment(upload_id)
        )
        chunk_folder.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_folder / f"{chunk_index:05d}.part"

        data = await chunk_file.read()
        await chunk_file.seek(0)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, chunk_path.write_bytes, data)
        return chunk_path.exists()

    async def store_chunk_bytes(
        self,
        data: bytes,
        upload_id: str,
        chunk_index: int,
        *,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        if chunk_index < 0:
            raise ValueError("Chunk index cannot be negative")
        chunk_folder = (
            self.chunk_dir
            / self._safe_segment(organization_id or "unassigned")
            / self._safe_segment(user_id or "anonymous")
            / self._safe_segment(upload_id)
        )
        chunk_folder.mkdir(parents=True, exist_ok=True)
        chunk_path = chunk_folder / f"{chunk_index:05d}.part"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, chunk_path.write_bytes, data)
        return chunk_path.exists()

    async def merge_chunks(
        self,
        upload_id: str,
        total_chunks: int,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        *,
        stored_filename: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Path:
        """Merge stored chunks into the final upload file."""
        if total_chunks <= 0:
            raise ValueError("total_chunks must be positive")

        chunk_folder = (
            self.chunk_dir
            / self._safe_segment(organization_id or "unassigned")
            / self._safe_segment(user_id or "anonymous")
            / self._safe_segment(upload_id)
        )
        if not chunk_folder.exists():
            raise FileNotFoundError(f"Chunk folder missing for upload {upload_id}")

        target_dir = self._target_dir(organization_id, project_id)
        final_path = target_dir / Path(stored_filename or filename).name

        def _merge() -> None:
            with final_path.open("wb") as dest:
                for idx in range(total_chunks):
                    chunk_path = chunk_folder / f"{idx:05d}.part"
                    if not chunk_path.exists():
                        raise FileNotFoundError(f"Missing chunk {idx} for upload {upload_id}")
                    with chunk_path.open("rb") as src:
                        shutil.copyfileobj(src, dest)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _merge)
        return final_path.resolve()

    async def cleanup_upload(
        self,
        upload_id: str,
        *,
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """Remove temporary artifacts for a chunked upload."""
        chunk_folder = (
            self.chunk_dir
            / self._safe_segment(organization_id or "unassigned")
            / self._safe_segment(user_id or "anonymous")
            / self._safe_segment(upload_id)
        )
        if not chunk_folder.exists():
            return

        def _cleanup() -> None:
            shutil.rmtree(chunk_folder, ignore_errors=True)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _cleanup)
