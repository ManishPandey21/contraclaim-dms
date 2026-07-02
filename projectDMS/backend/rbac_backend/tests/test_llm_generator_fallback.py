from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.retrieval.generator import LLMGenerator


def test_llm_generator_fallback_does_not_echo_prompt_text():
    generator = LLMGenerator(DocumentProcessingConfig(openai_api_key=None, ai_enabled=False))

    output = generator._fallback("Every sentence MUST include prompt-only guardrails.")

    assert "Every sentence MUST" not in output
    assert output.startswith("Answer unavailable:")
