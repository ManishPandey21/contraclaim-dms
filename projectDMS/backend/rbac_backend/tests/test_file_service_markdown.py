import asyncio
from types import SimpleNamespace

from rbac_backend.services.file_service import FileService


def _save_summary(service: FileService, content: str, upload_type: str) -> None:
    asyncio.run(
        service.save_summary(
            content=content,
            source_path="C:/uploads/Reply Letter.pdf",
            path_structure="organisation-a/project-b",
            upload_type=upload_type,
        )
    )


def test_incoming_summary_is_markdown_and_preserves_document_structure(tmp_path):
    service = FileService(SimpleNamespace(uploads_dir=str(tmp_path)))
    extracted_content = (
        "# Reply to Notice\n\n"
        "This is the first paragraph.\n\n"
        "- First reply point\n"
        "- Second reply point\n\n"
        "| Clause | Position |\n"
        "| --- | --- |\n"
        "| GCC 44 | Extension requested |\n\n"
        "[Notice reference](https://example.test/notices/42)\n"
    )

    _save_summary(service, extracted_content, "incoming")

    summary_path = tmp_path / "organisation-a" / "project-b" / "incoming.md"
    summary = summary_path.read_text(encoding="utf-8")

    assert summary_path.exists()
    assert not summary_path.with_suffix(".txt").exists()
    assert "## Source: Reply Letter.pdf" in summary
    assert extracted_content.rstrip() in summary


def test_outgoing_and_default_summaries_use_markdown_filenames(tmp_path):
    service = FileService(SimpleNamespace(uploads_dir=str(tmp_path)))

    _save_summary(service, "Outgoing correspondence", "outgoing")
    _save_summary(service, "Project correspondence", "other")

    summary_dir = tmp_path / "organisation-a" / "project-b"
    assert (summary_dir / "outgoing.md").read_text(encoding="utf-8").endswith("\n---\n")
    assert (summary_dir / "projectid.md").read_text(encoding="utf-8").endswith("\n---\n")
    assert not list(summary_dir.glob("*.txt"))
