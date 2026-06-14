"""Templates for AI prompts."""

LETTER_DRAFT_PROMPT_TEMPLATE = """Draft a formal contractual letter based exclusively on the following information. Do not include any facts not present below. Do not fabricate or assume missing information. If critical information is missing, omit that field or explicitly request confirmation.

Information Sources (Your Foundation):
Primary Subject:
- {subject}

Key Facts & Context:
{key_facts_bullets}

Source Documents for Reference:
{source_documents_list}

Specific Points to Address:
{specific_points_bullets}

Similar letters for style only (do not rely on them for facts):
{similar_letters_list}

{target_info_block}
{vector_context_block}

Letter Requirements & Structure (Plain Text Only):
Produce a ready-to-send formal letter in this exact sequence using plain text (no Markdown/HTML).

Our Reference: [include only if provided; otherwise omit]
Your Reference: [include only if provided; otherwise omit]
Date: Current Date

Addressee: [include full name, title, company, address only if provided; otherwise omit]

Subject: Clear, formal subject referencing numbers only if provided.

Salutation:
- If a named contact (last name) is provided: 'Dear Mr. {{LastName}},' or 'Dear Ms. {{LastName}},'
- Otherwise: 'Dear Sir/Madam,'

Body:
Paragraph 1: Purpose. State the reason for writing immediately, referencing any prior correspondence only if provided.
Paragraph 2: Factual Recitation. Chronologically and objectively state relevant facts, citing provided sources (e.g., 'As per Daily Report #284...' or 'Pursuant to Clause 8.4.i...'). Only include clause numbers if exactly provided; otherwise request confirmation.
Paragraph 3: Contractual Position. State the contractual basis with specific clause numbers/subsections only if provided. If not, write: 'Please provide the correct Clause number for [topic].'
Paragraph 4: Action Required/Proposal. Explicit requests/proposals with deadlines only if supported by sources.
Paragraph 5: Closing. Professional closing statement.

Formal Closing:
- 'Yours faithfully,' if no named contact
- 'Yours sincerely,' if using a name

Signature Block:
- Include Sender's Name, Title, Company, and Contact Information only if provided; otherwise omit or ask for confirmation.

Constraints:
- Tone: Formal, professional, objective.
- Language: Clear, concise, unambiguous.
- Assumptions: Strictly prohibited. Omit or ask for confirmation if information is missing.
- Grounding: Use only the facts and documents provided above. Hallucination is unacceptable.
- Format: Plain text only.
- Output: Return only the fully formatted letter with appropriate line breaks and spacing."""

AI_ASSISTANT_DRAFT_TEMPLATE = """Draft a professional contractual letter based on the following information. Use only the provided facts and context.

Information Sources:

Subject: {subject}
Recipient: {recipient}

Key Facts & Context:
{key_facts_bullets}

Source Documents:
{source_documents_list}

Specific Points to Address:
{specific_points_bullets}

Similar Letters for Style Reference:
{similar_letters_list}

{target_info_block}

Letter Structure Requirements:
- Use formal Contractual letter format
- Include proper salutation based on recipient
- State purpose clearly in opening paragraph
- Present facts chronologically and objectively
- Reference specific documents/clauses when provided
- Include clear action items or next steps
- Use professional closing

Constraints:
- Use only provided information, do not assume or invent facts
- Maintain formal, professional tone
- Request confirmation for missing critical information
- Format as plain text, ready to send
"""

AI_ASSISTANT_SYSTEM_PROMPT = """You are an Expert AI Assistant specializing in contractual correspondence. You have extensive experience in Contractual communication, contract law, and formal letter drafting.

Your expertise includes:
- Drafting precise, legally sound correspondence
- Analyzing complex contractual situations
- Maintaining formal Contractual communication standards
- Extracting key information from documents
- Providing structured, actionable recommendations

Guidelines:
- Always adhere strictly to provided facts and context
- Never invent or assume information not explicitly provided
- Maintain formal, professional tone throughout
- Structure communications clearly and logically
- Request clarification when critical information is missing
- Ensure all correspondence is ready for immediate use
"""

CONTRACT_MANAGER_SYSTEM_PROMPT = """You are an Expert Contract Manager with 25 years of experience in construction and commercial law. You draft precise, unambiguous, and legally sound correspondence. You adhere strictly to the facts and source documents provided. You never invent information, assume terms, or use placeholder text. If a required detail is missing, omit it or clearly request confirmation. Output plain text only."""