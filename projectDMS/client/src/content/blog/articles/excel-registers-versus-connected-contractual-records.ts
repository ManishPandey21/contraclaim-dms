import type { BlogArticle } from "../types";

/**
 * Source: editorial series "ContraClaim DMS Construction-Claims Blog Series",
 * article 3 of 5. Body preserved from the source draft; the separate LinkedIn
 * version is not published here.
 */
export const excelRegistersVersusConnectedRecords: BlogArticle = {
  id: "article-excel-registers-vs-connected-records",
  type: "article",
  title: "Excel Registers Versus Connected Contractual Records",
  slug: "excel-registers-versus-connected-contractual-records",
  excerpt:
    "The problem is not Excel itself. The problem is asking a collection of independent spreadsheets to operate as the contractual memory of a complex project.",
  category: "project-records",
  tags: [
    "Construction Technology",
    "Document Control",
    "Registers",
    "Version Control",
    "Traceability",
    "ISO 19650",
  ],
  author: "ContraClaim Editorial",
  publishedAt: "2026-08-06",
  status: "published",
  order: 3,
  seoTitle: "Excel Registers vs Connected Contract Records | ContraClaim",
  metaDescription:
    "Excel is useful, but isolated registers create version, traceability and deadline risks. Learn when construction contract records need a connected system.",
  ogDescription:
    "One delayed approval can appear in seven registers under seven names. Here is where isolated spreadsheets stop working as contractual memory — and what connected records must do instead.",
  featuredImageUrl: "/dashboard-hero.png",
  featuredImageAlt:
    "A connected contractual records workspace showing document totals, status breakdown and recent activity",
  // Editorial cross-links from the series link plan.
  relatedSlugs: [
    "seven-records-every-eot-claim-needs",
    "how-to-build-defensible-project-chronology",
  ],
  body: `Excel is one of the most useful tools in construction. It is fast, flexible, familiar and excellent for calculations, ad hoc analysis and small controlled datasets. The problem is not Excel itself. The problem is asking a collection of independent spreadsheets to operate as the contractual memory of a complex project.

A letter register, RFI register, variation register, notice register, EOT tracker, drawing log and payment register can each be accurate in isolation while the project's overall contractual position remains unclear.

## The hidden cost of separate registers

Consider one delayed design approval. It may appear under different descriptions in:

- the correspondence register;
- the drawing or submittal log;
- the RFI register;
- the programme narrative;
- the risk register;
- the notice register;
- the variation register; and
- the EOT claim workbook.

If those entries are not connected, a change to the response date or status must be repeated manually. The identifiers may differ. A team member may update one workbook but not another. The claim writer then spends time reconciling which row describes the same event and which date is authoritative.

That is not merely an efficiency problem. It affects deadlines, version integrity, causation analysis and confidence in the final submission.

## What a register can tell you—and what it cannot

An Excel register can efficiently answer: "Which notices are shown as open?"

A connected contractual record should also answer:

- Which clause and event does each notice relate to?
- What was the contractual due date and how was it calculated?
- Which issued letter is the source of the register entry?
- Was delivery acknowledged?
- Which programme activities and milestones may be affected?
- What progress, resource and cost records support the event?
- What response or determination followed?
- Who changed the status, when and why?
- Are there conflicting records or missing particulars?

The difference is the relationship between the register entry and the evidence.

## A practical comparison

| Control need | Standalone spreadsheet | Connected contractual records |
|---|---|---|
| Rapid setup and custom calculation | Strong | Requires configuration |
| Single-user analysis | Strong | May be more system than needed |
| One authoritative live status | Vulnerable to copies | Controlled source with permissions |
| Link from entry to issued document | Usually manual hyperlink | Persistent source relationship |
| Cross-register event view | Manual reconciliation | Shared event and identifiers |
| Version and change history | Depends on process | Designed audit history |
| Deadline control | Formula and manual follow-up | Rule, owner, alert and escalation workflow |
| Duplicate detection | Manual/formula based | Can use metadata and content checks |
| Access by organisation/project/role | File or folder permissions | Record-level policy can be enforced |
| Export and specialist analysis | Excellent | Should preserve export to open formats |

Connected systems are not automatically better. A poorly configured database can encode bad processes, hide errors behind a polished interface or make export difficult. The objective is controlled information management, not technology for its own sake.

## Five signs the spreadsheet model has reached its limit

### 1. Multiple "final" versions circulate

Files named \`Register_Final_v7_Updated.xlsx\` indicate that version naming is performing work that should be handled by controlled status and history.

### 2. The source document cannot be opened from the entry

If users must search folders to verify a letter number, date or amount, the register has become an unsupported assertion rather than an index into the evidence.

### 3. The same event has different names in different registers

Without a shared event ID or controlled relationship, cross-functional reporting becomes a manual interpretation exercise.

### 4. Deadline control depends on one person

A coloured cell is not an escalation process. Contractual deadlines need a defined trigger, calculation rule, owner, reviewer, status and evidence of submission.

### 5. Claims are assembled by searching rather than querying

If preparing a claim requires asking each department for "all documents about this issue," the project does not yet have a connected evidential model.

## What "connected" should mean

A connected contractual record is not simply a file with more hyperlinks. At minimum, it should support:

### Shared identity

The same organisation, project, contract, issue, event, work package, location and programme activity should use consistent identifiers across records.

### Source traceability

Every important extracted fact—date, instruction, response, quantity or amount—should point to the originating document and relevant location within it where practicable.

### Relationships

A notice should link to its event, clause, response, affected activities, evidence, cost items, claim and determination. The relationship itself should be reviewable.

### Controlled status and history

The system should record draft, reviewed, issued, received, superseded and closed states, together with actor, date and reason for material changes.

### Permission and scope

Users should see and change only the organisations, projects and records authorised for their role. Convenience must not defeat confidentiality or tenant separation.

### Native-file preservation and export

Programmes, spreadsheets containing formulae and other native records should not be flattened into PDFs alone. Searchability must coexist with original-file preservation and practical export.

These principles align with the SCL Protocol's recommendation for searchable, retrievable electronic records and collaborative document management where practicable. They also reflect ISO 19650's broader information-management concept of an agreed source for collecting, managing and disseminating project information.

## Do not abandon Excel—give it the right job

Excel remains valuable for quantity checks, reconciliations, cash-flow analysis, pivots, scenario testing and specialist calculations. The better architecture is often:

- connected records for identity, status, source links, approvals and audit history; and
- controlled Excel exports or linked analysis files for calculations and specialist review.

This preserves flexibility without making a workbook the only location where contractual meaning exists.

## From rows to evidence

ContraClaim DMS is designed to help construction teams connect correspondence, events, contractual deadlines, registers and supporting records across the claim lifecycle. The goal is not to eliminate spreadsheets. It is to ensure that a row can be traced to its source, related to the same event across functions and reviewed within a controlled project context.

**See how ContraClaim DMS can turn isolated registers into connected, source-linked contractual records.**

## Sources and further reading

- [Society of Construction Law — Delay and Disruption Protocol, 2nd edition](https://www.scl.org.uk/sites/default/files/documents/SCL_Delay_Protocol_2nd_Edition_Final.pdf)
- [ISO — ISO 19650-1: Information management using building information modelling](https://www.iso.org/obp/ui/#iso:std:iso:19650:-1:ed-1:v1:en)

*This article provides general information about project and contract-record management. System design must be matched to the contract, organisational controls, cybersecurity requirements, data-protection obligations and applicable law.*`,
};
