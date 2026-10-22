import type { BlogArticle } from "../types";

/**
 * Source: editorial series "ContraClaim DMS Construction-Claims Blog Series",
 * article 4 of 5. Body preserved from the source draft; the separate LinkedIn
 * version is not published here.
 */
export const howToBuildDefensibleProjectChronology: BlogArticle = {
  id: "article-defensible-project-chronology",
  type: "article",
  title: "How to Build a Defensible Project Chronology",
  slug: "how-to-build-defensible-project-chronology",
  excerpt:
    "A chronology is often treated as a table of dates prepared shortly before a claim is submitted. A defensible project chronology is more demanding. It is a controlled map of what happened, what the contemporaneous records prove, what remains disputed and how the sequence relates to contractual obligations and project impact.",
  category: "claims-evidence",
  tags: [
    "Contract Administration",
    "Chronology",
    "Causation",
    "Delay Analysis",
    "Source Traceability",
    "Document Review",
  ],
  author: "ContraClaim Editorial",
  publishedAt: "2026-08-06",
  status: "published",
  order: 4,
  seoTitle: "How to Build a Defensible Project Chronology | ContraClaim",
  metaDescription:
    "Build a source-led construction claim chronology that separates fact from inference, exposes contradictions and supports causation analysis.",
  ogDescription:
    "One material event per row, a pinpoint source for every fact, and a clear line between what a record proves and what a party merely asserted. A ten-step method.",
  // Editorial cross-links from the series link plan.
  relatedSlugs: [
    "what-ai-should-and-should-not-do-contractual-drafting",
  ],
  body: `A chronology is often treated as a table of dates prepared shortly before a claim is submitted. A defensible project chronology is more demanding. It is a controlled map of what happened, what the contemporaneous records prove, what remains disputed and how the sequence relates to contractual obligations and project impact.

Its purpose is not to make one party's case look inevitable. Its purpose is to let a reviewer test the sequence efficiently.

## Why ordinary timelines break down

Construction projects produce several kinds of time:

- date a document was created;
- date it was issued;
- date it was received;
- date an event occurred;
- effective date of an instruction;
- data date of a programme update;
- start and finish of a physical impact; and
- date a later witness or claim document says the event occurred.

Treating these as interchangeable creates false sequences. A letter dated 12 June may describe an instruction allegedly given orally on 7 June, issued by email on 13 June and received through the document system on 14 June. A reliable chronology preserves those distinctions.

## Step 1: Define the issue before collecting dates

Start with a precise chronology question, such as:

> What sequence of design submissions, reviews, instructions and site constraints affected the start of base-slab works in Station A between 1 March and 30 September?

This sets boundaries for contract package, location, discipline, date range, milestone and event type. A chronology of "all project events" usually becomes too large to validate and too vague to support a decision.

## Step 2: Define the source hierarchy

Create the source set before drawing conclusions. It may include:

- executed contract and amendments;
- issued correspondence and notices;
- instructions, variations and determinations;
- programmes, updates and narratives;
- daily, weekly and monthly reports;
- RFIs, drawings, submissions and approvals;
- minutes and action trackers;
- inspection, quality and measurement records;
- photographs and metadata;
- procurement and delivery records;
- resource and cost records; and
- later claims, responses and witness accounts.

Record whether a document is original, copy, draft, issued, received, accepted, disputed or superseded. A later claim narrative may identify an issue, but it should not silently replace the contemporaneous source.

## Step 3: Extract atomic events

Each row should contain one material event. Avoid entries such as "Between March and July, the contractor repeatedly requested approval and the employer failed to respond." That is a conclusion spanning several communications.

Break it into testable events: submission issued; receipt recorded; response due; reminder issued; reviewer requested information; revised submission issued; approval given; downstream work started.

Atomic entries make omissions, gaps and contradictions visible.

## Step 4: Use a consistent chronology schema

The following fields are a practical minimum:

| Field | Purpose |
|---|---|
| Event ID | Stable reference used across analysis and drafting |
| Event date/time | When the event occurred, with precision stated |
| Record date | When the supporting record was created or issued |
| Event type | Notice, submission, instruction, progress, decision, impact, mitigation, etc. |
| Neutral event statement | Concise factual description |
| Party/author | Originator or actor |
| Contract/work package/location | Scope controls |
| Clause/obligation | Contractual link, if established |
| Programme activity/milestone | Schedule link, if established |
| Source reference | Document ID and pinpoint reference |
| Evidence status | Contemporaneous, retrospective, corroborated, disputed or missing |
| Party position | Assertion kept separate from fact |
| Reviewer note | Inference, contradiction or follow-up question |
| Related event IDs | Predecessor, response, instruction, impact or mitigation link |

Do not force a clause or activity link when it has not been established. "Unmapped" is more defensible than a confident but unsupported relationship.

## Step 5: Separate fact, assertion and inference

Consider these three statements:

- **Fact:** Letter C-104, issued 18 April, requested access to Shaft 2 by 25 April.
- **Assertion:** The letter stated that lack of access was delaying Activity SH-210.
- **Inference:** If SH-210 was critical on the reliable contemporaneous programme, the access constraint may have affected the milestone.

Combining all three into "Employer delayed the critical shaft works from 25 April" conceals the analytical steps that still require proof.

Use neutral language for the event, record each party's position separately and label analysis as analysis.

## Step 6: Normalise dates without erasing uncertainty

Use one date format and project time zone. Preserve the original date string where ambiguity exists. Identify whether a date is exact, inferred, approximate or a range.

If two sources conflict, retain both. Do not select the date that best supports the intended case without recording the contradiction and the basis for preferring one source.

## Step 7: Link events causally—but do not confuse sequence with causation

Chronology answers "what happened in what order." Causation requires more: contractual responsibility, the affected work, programme logic, resource consequences, mitigation and alternative causes.

Useful links include:

- request → response;
- instruction → changed work;
- submission → review → resubmission → approval;
- constraint → affected activity → mitigation → actual progress;
- notice → particulars → assessment → determination; and
- cost record → resource → event.

Events occurring one after another are not necessarily causally connected. The chronology should help analysis, not pre-judge it.

## Step 8: Test the chronology against the programme and physical record

For each alleged delay period, ask:

- Was the affected activity planned to proceed at that time?
- Were predecessor activities complete?
- Was access, design, material and resource readiness demonstrated?
- What did the contemporaneous update show as critical or near-critical?
- Did another constraint affect the same work?
- What mitigation or resequencing occurred?
- Do daily reports, photographs and measurements support the progress dates?

A chronology that cannot be reconciled with programme and physical evidence remains a correspondence timeline, not a delay analysis.

## Step 9: Run contradiction and completeness reviews

Search deliberately for records that weaken the emerging account: later submissions, changed logic, contractor-caused constraints, prior approvals, contrary minutes, resource shortages or cost recovery elsewhere.

Then run completeness checks:

- missing reply to a referenced letter;
- instruction mentioned but not located;
- programme activity with no physical evidence;
- notice with no follow-up particulars;
- event with no identified end date;
- cost with no source transaction; or
- material date supported only by a later narrative.

A chronology gains credibility when it exposes uncertainty rather than concealing it.

## Step 10: Review, version and freeze the relied-upon chronology

Assign reviewers by discipline: contracts, planning, engineering, commercial and legal where appropriate. Record comments and resolutions. Give the issued chronology a version, date, scope and source cut-off date.

Later evidence may require a revision, but the team should be able to reproduce which chronology supported each claim version or decision. Never silently rewrite historical entries.

## Where technology—and AI—can help

Software can extract candidate dates, recognise document references, group related communications, identify missing replies and create links for human review. It can accelerate the clerical work.

It should not silently decide which conflicting account is true, convert an allegation into a fact or declare that an event caused critical delay. Those judgments require verified evidence and appropriately qualified reviewers.

ContraClaim DMS is designed to help teams build source-linked chronologies, preserve the distinction between records and analysis, and maintain review history as the project evidence develops.

**Explore how ContraClaim DMS can turn a list of dates into a controlled, reviewable project chronology.**

## Sources and further reading

- [Society of Construction Law — Delay and Disruption Protocol, 2nd edition](https://www.scl.org.uk/sites/default/files/documents/SCL_Delay_Protocol_2nd_Edition_Final.pdf)
- [AACE International — Recommended Practice 29R-03: Forensic Schedule Analysis](https://web.aacei.org/docs/default-source/toc/toc_29r-03.pdf)

*This article provides general contract-administration information. A chronology is not, by itself, proof of contractual entitlement or delay. The contract, governing law and appropriate expert analysis must be applied to the verified facts.*`,
};
