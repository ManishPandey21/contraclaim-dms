### Config Module: config/prompts.py


LETTER_DRAFT_PROMPT_TEMPLATE = """
Draft a formal contractual letter based exclusively on the following information...


Primary Subject:
- {subject}


Key Facts & Context:
{key_facts}


Source Documents:
{documents}


Specific Points:
{points}


Similar Letters:
{similar_letters}


Target Letter Context:
{target_letter}


Vector Store Context:
{vector_context}


[Output structured letter here...]
"""


EXTRACT_CLAUSES_PROMPT_TEMPLATE = """
Analyze the following document content and extract:


1. Key points
2. Quoted clauses


CONTENT:
{content}
"""