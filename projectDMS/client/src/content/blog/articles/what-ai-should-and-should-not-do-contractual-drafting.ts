import type { BlogArticle } from "../types";

/**
 * Source: editorial series "ContraClaim DMS Construction-Claims Blog Series",
 * article 5 of 5. Body preserved from the source draft; the separate LinkedIn
 * version is not published here.
 */
export const whatAiShouldAndShouldNotDo: BlogArticle = {
  id: "article-ai-contractual-drafting",
  type: "article",
  title: "What AI Should—and Should Not—Do in Contractual Drafting",
  slug: "what-ai-should-and-should-not-do-contractual-drafting",
  excerpt:
    "AI can read large document sets, identify references, compare wording and produce a first draft in minutes. In construction contract administration, that can reduce clerical effort dramatically.",
  category: "responsible-ai",
  tags: [
    "Construction Technology",
    "AI Governance",
    "Contractual Drafting",
    "Human Review",
    "Confidentiality",
    "NIST AI RMF",
  ],
  author: "ContraClaim Editorial",
  publishedAt: "2026-08-06",
  status: "published",
  order: 5,
  seoTitle: "AI in Contractual Drafting: Proper Uses and Limits | ContraClaim",
  metaDescription:
    "AI can accelerate contractual drafting, but it must remain source-grounded, secure and human-reviewed. Learn the proper uses, limits and approval controls.",
  ogDescription:
    "AI should assist contractual drafting but never become the contractual decision-maker. The proper uses, the prohibited ones, and a nine-gate control model.",
  featuredImageUrl: "/contract-security-governance.jpg",
  featuredImageAlt:
    "Secure contract governance imagery representing controlled, human-reviewed AI-assisted drafting",
  // Editorial cross-links from the series link plan.
  relatedSlugs: [
    "seven-records-every-eot-claim-needs",
    "how-to-build-defensible-project-chronology",
  ],
  body: `AI can read large document sets, identify references, compare wording and produce a first draft in minutes. In construction contract administration, that can reduce clerical effort dramatically.

It can also produce a fluent letter that cites the wrong clause, confuses the parties, omits a notice requirement, invents a fact or expresses a position no authorised decision-maker intended to take.

The correct question is not whether AI can draft. It is which parts of drafting can be safely assisted, what evidence the output must show and where human authority must remain non-delegable.

## The useful role: accelerate evidence-led work

### AI should extract candidate facts—with source links

AI can identify dates, letter numbers, clauses, instructions, amounts, milestones and party names across project records. Each extracted item should retain a link to the source and, where possible, the page, paragraph or text span.

The output should be treated as a candidate fact until verified. Optical-character-recognition errors, tables, handwritten notes and similar document numbers can all produce false matches.

### AI should compare records and expose inconsistencies

It can flag conflicting dates, missing referenced attachments, different versions of a clause, inconsistent amounts or a claim statement unsupported by the provided record set.

This is valuable because it directs professional attention to risk. The system should show the conflict, not silently choose the convenient version.

### AI should organise evidence around a defined issue

Given an approved issue statement, AI can group correspondence, programme references, RFIs, instructions, progress records and costs into a working evidence map. It can propose event relationships or chronology entries for review.

Organisation is not adjudication. A proposed link between an instruction and a delay event should be confirmed by a person competent to assess the contract and programme.

### AI should produce controlled first drafts

AI is well suited to creating a structured first draft from verified facts and an approved position: background, contractual basis, factual sequence, requested action, reservation of rights and attachments.

The draft should identify placeholders and unresolved issues. It should not fill gaps with plausible language. "Clause to be confirmed" is safer than an invented clause reference.

### AI should run consistency and completeness checks

Before issue, AI can check whether defined terms, dates, amounts, references and attachment lists are internally consistent. It can compare the draft with a checklist: notice deadline addressed, relied-upon clause quoted accurately, contrary correspondence considered, relief stated and authority identified.

These are quality-control prompts, not a guarantee of legal or factual correctness.

## The prohibited role: replace evidence, expertise or authority

### AI should not invent facts, sources or clauses

Generative systems can produce confident but unsupported content. A contractual drafting system should be able to say "not found in the authorised sources." Every material factual and contractual proposition should be verifiable.

### AI should not determine entitlement autonomously

Entitlement may depend on contract interpretation, amendments, governing law, factual responsibility, notice compliance, causation and professional judgment. AI may summarise competing positions or identify required elements. It should not issue a final entitlement conclusion without authorised human review.

### AI should not perform delay analysis by prose

A narrative that says an event "caused 63 days of critical delay" is not a substitute for validated programme data and an appropriate delay methodology. AI can help locate schedule references and explain an approved analysis. It should not generate a delay number merely from letters and progress reports.

### AI should not calculate quantum from unverified totals

AI can reconcile schedules, identify missing vouchers and apply an approved formula. It should not assume that every project cost incurred during a period was caused by the claimed event, nor should it classify cost without accounting and contractual review.

### AI should not hide uncertainty or contrary evidence

A trustworthy workflow distinguishes verified fact, party allegation, model inference and reviewer conclusion. It should surface documents that contradict the intended position and require resolution or disclosure as appropriate.

### AI should not receive confidential project data without controls

Before using any AI service, the organisation should know what data is transmitted, where it is processed, how long it is retained, whether it is used for model training, who can access it and how deletion, audit and incident response work.

Professional guidance such as the American Bar Association's Formal Opinion 512 highlights competence, confidentiality, communication, supervision and candour when lawyers use generative AI. While the applicable professional rules vary by jurisdiction and role, the underlying operational lesson is relevant to contractual teams: remain accountable for the work product and protect confidential information.

### AI should not send or approve contractual communications

Issuing a notice, accepting an instruction, agreeing a valuation, waiving a right or making an admission can have legal and commercial consequences. AI may prepare and route a draft. Only a properly authorised person should approve and issue it.

## A practical control model

Use a gated workflow:

| Gate | Required control |
|---|---|
| 1. Authorised sources | Limit drafting to the correct project, organisation and approved record set |
| 2. Extraction | Retain source and pinpoint reference for candidate facts |
| 3. Verification | Human confirms material dates, clauses, amounts and quotations |
| 4. Position | Authorised contract/legal reviewer approves the intended position |
| 5. Draft | AI produces text using verified facts and approved instructions |
| 6. Challenge | Check contrary evidence, missing records, ambiguity and unintended admissions |
| 7. Specialist review | Planning, commercial, technical and legal review as relevant |
| 8. Approval and issue | Authorised human approves the final version and transmission |
| 9. Audit | Preserve sources, prompts/instructions, versions, reviewers and issued record as policy permits |

NIST's AI Risk Management Framework and its Generative AI Profile support a lifecycle approach based on governance, mapping, measurement and management of risk. For contractual drafting, that translates into documented purpose, controlled data, evaluation, human oversight and monitoring—not a one-time disclaimer that "AI may make mistakes."

## What good AI-assisted drafting looks like

A well-controlled output should allow the reviewer to answer:

- Which source supports each material fact?
- Is the quoted clause from the controlling contract version?
- Which statements are allegations or inferences?
- What relevant contrary record was found?
- Who approved the legal and commercial position?
- What changed after review?
- Who had authority to issue the final communication?

If those answers are unavailable, speed has been gained at the cost of defensibility.

## AI as a co-pilot, not the contractual decision-maker

ContraClaim DMS is intended to use AI within a source-grounded, role-controlled and human-reviewed workflow: helping teams retrieve evidence, build chronologies, identify gaps and prepare drafts while keeping professional judgment and approval with accountable people.

The objective is not automated advocacy. It is faster, more consistent and more transparent contractual work.

**Explore responsible AI-assisted drafting in ContraClaim DMS—grounded in project sources and controlled by human review.**

## Sources and further reading

- [NIST — Artificial Intelligence Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [NIST — Generative Artificial Intelligence Profile (NIST AI 600-1)](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
- [American Bar Association — Formal Opinion 512 overview](https://www.americanbar.org/news/abanews/aba-news-archives/2024/07/aba-issues-first-ethics-guidance-ai-tools/)

*This article provides general information, not legal, professional-ethics, cybersecurity or data-protection advice. Applicable contracts, law, professional duties, client requirements and organisational policies must be reviewed for each use case and jurisdiction.*`,
};
