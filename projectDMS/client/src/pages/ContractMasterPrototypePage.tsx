/**
 * Contract Master v1 — HITL layout prototype.
 *
 * Rough on purpose. This exists so a human can react to information
 * architecture, state vocabulary and workflow shape before T26–T30 build the
 * real surfaces. It is NOT those tickets and must not be mistaken for them.
 *
 * Three rules it follows because they are the ones a prototype usually breaks:
 *
 * 1. **Fixtures match real server shapes.** Every object below is typed against
 *    `contract-master-v1-api.ts`, which mirrors the router's response models. No
 *    invented field, no convenience flag. If a value is not on the wire, it is
 *    not here.
 * 2. **Nothing is authority.** Controls are shown or hidden from the server
 *    capability response. Hiding a button is presentation; the server still
 *    refuses. There is no role test anywhere in this file.
 * 3. **Evidence readiness is read, never remembered.** It comes from each
 *    response and is recomputed server-side every time.
 *
 * Swap `USE_FIXTURES` to false to drive it against a live backend.
 */

import React, { useMemo, useState } from "react";

import {
  organizationScope,
  projectScope,
  serializeUploadScope,
  type ContractUploadScope,
} from "@/services/contract-upload-scope";
import type {
  CatalogueItem,
  ContractMasterCapabilities,
  ContractViewerState,
  EvidenceOutcome,
  InstrumentDetail,
  QueryMode,
  ReconciliationCandidate,
} from "@/services/contract-master-v1-api";

const USE_FIXTURES = true;

// --------------------------------------------------------------------------- //
// fixtures - shaped exactly like the real responses
// --------------------------------------------------------------------------- //

const CAPABILITIES: ContractMasterCapabilities = {
  can_browse_catalogue: true,
  can_view_instruments: true,
  can_manage_classification: true,
  can_manage_applicability: true,
  can_upload_organization_scope: true,
  can_upload_project_scope: true,
  can_review_migration: true,
  can_promote: true,
};

const CATALOGUE: CatalogueItem[] = [
  {
    contract_document_id: "cd-gcc",
    document_id: "doc-gcc",
    contract_document_type: "general_conditions",
    scope_level: "organization",
    classification_revision: 3,
    projection_status: "CURRENT",
    applicability_count: 0,
    content_consumable: true,
    evidence_ready: false,
    viewer_state: "CATALOGUED / UNASSIGNED",
  },
  {
    contract_document_id: "cd-pcc",
    document_id: "doc-pcc",
    contract_document_type: "particular_conditions",
    scope_level: "project",
    classification_revision: 2,
    projection_status: "PENDING",
    applicability_count: 1,
    content_consumable: true,
    evidence_ready: false,
    viewer_state: "APPLICABLE - PROJECTION PENDING",
  },
  {
    contract_document_id: "cd-boq",
    document_id: "doc-boq",
    contract_document_type: "boq",
    scope_level: "project",
    classification_revision: 1,
    projection_status: "CURRENT",
    applicability_count: 2,
    content_consumable: true,
    evidence_ready: true,
    viewer_state: "APPLICABLE - EVIDENCE READY",
  },
  {
    contract_document_id: "cd-spec",
    document_id: "doc-spec",
    contract_document_type: "technical_specification",
    scope_level: "project",
    classification_revision: 1,
    projection_status: "CURRENT",
    applicability_count: 1,
    content_consumable: false,
    evidence_ready: false,
    viewer_state: "APPLICABLE - CONTENT BLOCKED",
  },
  {
    contract_document_id: "cd-old",
    document_id: "doc-old",
    contract_document_type: "addendum",
    scope_level: "project",
    classification_revision: 4,
    projection_status: "CURRENT",
    applicability_count: 1,
    content_consumable: true,
    evidence_ready: false,
    viewer_state: "SUPERSEDED / WITHDRAWN",
  },
];

const CANDIDATES: ReconciliationCandidate[] = [
  {
    candidate_id: "contract-master-migration:contracts:doc-101",
    canonical_document_id: "doc-101",
    scope_state: "AMBIGUOUS",
    type_state: "TYPE_SUGGESTED",
    scope_hint:
      "the creation audit retained a null project, which records only that no project was in the session - not that the document is organisation-owned",
    contract_document_type: null,
    promotion_blocked_reasons: [
      "scope: AMBIGUOUS - operator adjudication required",
      "type: TYPE_SUGGESTED - operator confirmation required",
    ],
  },
  {
    candidate_id: "contract-master-migration:contracts:doc-102",
    canonical_document_id: "doc-102",
    scope_state: "PROJECT_SCOPE_CONFIRMED",
    type_state: "TYPE_UNKNOWN",
    scope_hint: null,
    promotion_blocked_reasons: ["type: TYPE_UNKNOWN - operator confirmation required"],
  },
  {
    candidate_id: "contract-master-migration:contracts:doc-103",
    canonical_document_id: "doc-103",
    scope_state: "ORG_SCOPE_CONFIRMED",
    type_state: "TYPE_RESOLVED",
    scope_hint: null,
    contract_document_type: "general_conditions",
    adjudicated_by: "alice",
    promotion_blocked_reasons: [],
  },
  {
    candidate_id: "contract-master-migration:contracts:doc-104",
    canonical_document_id: "doc-104",
    scope_state: "INVALID",
    type_state: "TYPE_UNKNOWN",
    scope_hint: "the referenced project belongs to a different organisation",
    promotion_blocked_reasons: [
      "scope: INVALID (terminal)",
      "type: TYPE_UNKNOWN - operator confirmation required",
    ],
  },
];

// --------------------------------------------------------------------------- //
// primitives
// --------------------------------------------------------------------------- //

const STATE_TONE: Record<ContractViewerState, string> = {
  // Only EVIDENCE READY gets success styling. The others name a closed gate.
  "APPLICABLE - EVIDENCE READY": "bg-green-100 text-green-900 border-green-300",
  "CATALOGUED / UNASSIGNED": "bg-slate-100 text-slate-800 border-slate-300",
  "APPLICABLE - PROJECTION PENDING": "bg-amber-100 text-amber-900 border-amber-300",
  "APPLICABLE - CONTENT BLOCKED": "bg-rose-100 text-rose-900 border-rose-300",
  "SUPERSEDED / WITHDRAWN": "bg-zinc-200 text-zinc-700 border-zinc-400",
};

function StateBadge({ state }: { state: ContractViewerState }) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${STATE_TONE[state]}`}
    >
      {state}
    </span>
  );
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="mb-8 rounded-lg border border-slate-200 bg-white p-5">
      <h2 className="mb-1 text-lg font-semibold">{title}</h2>
      {note ? <p className="mb-4 text-sm text-slate-600">{note}</p> : null}
      {children}
    </section>
  );
}

// --------------------------------------------------------------------------- //
// 1. upload scope
// --------------------------------------------------------------------------- //

function UploadScopeSurface({ capabilities }: { capabilities: ContractMasterCapabilities }) {
  // No preselection. `null` is "not answered", and it cannot be submitted.
  const [level, setLevel] = useState<"organization" | "project" | null>(null);
  const [projectId, setProjectId] = useState("");

  let scope: ContractUploadScope | null = null;
  let blocker: string | null = null;
  if (level === null) {
    blocker = "Choose a scope. Nothing is preselected, and there is no default.";
  } else if (level === "organization") {
    scope = organizationScope();
  } else if (!projectId.trim()) {
    blocker = "Project scope needs a project. Clearing it invalidates the form - it does not fall back to organisation scope.";
  } else {
    scope = projectScope(projectId);
  }

  return (
    <Panel
      title="1 · Upload scope"
      note="An explicit organisation-or-project choice. A blank project never becomes organisation scope."
    >
      <div className="flex gap-6">
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="scope"
            checked={level === "organization"}
            disabled={!capabilities.can_upload_organization_scope}
            onChange={() => setLevel("organization")}
          />
          <span>Organisation-owned</span>
        </label>
        <label className="flex items-center gap-2">
          <input
            type="radio"
            name="scope"
            checked={level === "project"}
            disabled={!capabilities.can_upload_project_scope}
            onChange={() => setLevel("project")}
          />
          <span>Belongs to one project</span>
        </label>
      </div>

      {level === "project" ? (
        <div className="mt-4">
          <label className="block text-sm font-medium">Project</label>
          <input
            className="mt-1 w-72 rounded border border-slate-300 px-2 py-1"
            value={projectId}
            placeholder="select a project"
            onChange={(event) => setProjectId(event.target.value)}
          />
        </div>
      ) : null}

      <p className="mt-4 rounded bg-slate-50 p-3 text-sm text-slate-700">
        Uploading does <strong>not</strong> apply this document to a contract. Applicability is a
        separate, confirmed act.
      </p>

      <div className="mt-4">
        <button
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
          disabled={scope === null}
        >
          Upload
        </button>
        {blocker ? <p className="mt-2 text-sm text-rose-700">{blocker}</p> : null}
        {scope ? (
          <pre className="mt-3 rounded bg-slate-900 p-3 text-xs text-slate-100">
            {JSON.stringify(serializeUploadScope(scope), null, 2)}
          </pre>
        ) : null}
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// 2. catalogue
// --------------------------------------------------------------------------- //

function CatalogueSurface({
  items,
  capabilities,
  onOpen,
}: {
  items: CatalogueItem[];
  capabilities: ContractMasterCapabilities;
  onOpen: (id: string) => void;
}) {
  if (!capabilities.can_browse_catalogue) {
    return (
      <Panel title="2 · Organisation catalogue">
        <p className="text-sm text-slate-600">
          Your account does not hold catalogue browse. This surface is not available.
        </p>
      </Panel>
    );
  }
  return (
    <Panel
      title="2 · Organisation catalogue"
      note="Organisation-owned instruments, including ones applicable to nothing. Catalogue membership is not evidence."
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-slate-600">
            <tr>
              <th className="py-2">Instrument</th>
              <th>Type</th>
              <th>Scope</th>
              <th>Applicability</th>
              <th>Projection</th>
              <th>Content</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.contract_document_id} className="border-t border-slate-100">
                <td className="py-2">
                  <button className="underline" onClick={() => onOpen(item.contract_document_id)}>
                    {item.contract_document_id}
                  </button>
                </td>
                <td>{item.contract_document_type ?? "—"}</td>
                <td>{item.scope_level ?? "—"}</td>
                <td>{item.applicability_count} contract(s)</td>
                <td>
                  {item.projection_status} (rev {item.classification_revision})
                </td>
                <td>{item.content_consumable ? "available" : "blocked"}</td>
                <td>
                  <StateBadge state={item.viewer_state} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// 3. instrument viewer
// --------------------------------------------------------------------------- //

function ViewerSurface({ item }: { item: CatalogueItem }) {
  const dimensions: Array<[string, string]> = [
    ["Scope", item.scope_level ?? "—"],
    ["Classification", `${item.contract_document_type ?? "unclassified"} (revision ${item.classification_revision})`],
    ["Applicability", `${item.applicability_count} contract(s)`],
    ["Projection", String(item.projection_status)],
    ["Content authority", item.content_consumable ? "consumable" : "blocked"],
    ["Evidence readiness", item.evidence_ready ? "ready" : "not ready"],
  ];

  return (
    <Panel
      title="3 · Instrument viewer"
      note="Seven orthogonal facts, shown separately. Readiness is recomputed from the server on every fetch and never cached."
    >
      <div className="mb-4">
        <StateBadge state={item.viewer_state} />
      </div>
      <dl className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
        {dimensions.map(([label, value]) => (
          <React.Fragment key={label}>
            <dt className="text-slate-600">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </React.Fragment>
        ))}
      </dl>

      {item.viewer_state === "APPLICABLE - CONTENT BLOCKED" ? (
        <p className="mt-4 rounded bg-rose-50 p-3 text-sm text-rose-900">
          This instrument <strong>applies</strong> and its content is <strong>unavailable</strong> —
          two simultaneous facts. It is not dropped from the list, and its text is not shown.
        </p>
      ) : null}

      {item.projection_status === "FAILED" ? (
        <p className="mt-4 rounded bg-amber-50 p-3 text-sm text-amber-900">
          Projection failed. Retry is offered; there is no fallback to the previous generation.
        </p>
      ) : null}

      <p className="mt-4 rounded bg-slate-50 p-3 text-sm text-slate-700">
        Correcting the classification will invalidate the current projection, and this instrument
        will stop being usable as evidence until it is rebuilt. That consequence is disclosed
        <em> before</em> the change, not after.
      </p>
      <p className="mt-2 text-sm text-slate-600">
        Authoritative scope has no edit control here — changing it is a different legal statement,
        not a metadata edit.
      </p>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// 4. Q&A
// --------------------------------------------------------------------------- //

function QaSurface() {
  const [mode, setMode] = useState<QueryMode | null>(null);
  const [eventDate, setEventDate] = useState("");
  const [outcome, setOutcome] = useState<EvidenceOutcome | "authority_failure" | null>(null);

  const blocked =
    mode === null
      ? "Choose a mode. There is no implicit current-state default."
      : mode === "historical" && !eventDate
        ? "A historical question needs a date. It does not default to today."
        : null;

  return (
    <Panel
      title="4 · Contract Q&A"
      note="Project, contract and mode are explicit. Browse results cannot launch a question."
    >
      <div className="flex flex-wrap items-end gap-4 text-sm">
        <div>
          <label className="block font-medium">Project</label>
          <input className="mt-1 rounded border border-slate-300 px-2 py-1" defaultValue="project-7" />
        </div>
        <div>
          <label className="block font-medium">Contract</label>
          <input className="mt-1 rounded border border-slate-300 px-2 py-1" defaultValue="contract-3" />
        </div>
        <div>
          <label className="block font-medium">Mode</label>
          <select
            className="mt-1 rounded border border-slate-300 px-2 py-1"
            value={mode ?? ""}
            onChange={(event) => setMode((event.target.value || null) as QueryMode | null)}
          >
            <option value="">— choose —</option>
            <option value="current_state">Current state</option>
            <option value="historical">As at a date</option>
          </select>
        </div>
        {mode === "historical" ? (
          <div>
            <label className="block font-medium">Event date</label>
            <input
              type="date"
              className="mt-1 rounded border border-slate-300 px-2 py-1"
              value={eventDate}
              onChange={(event) => setEventDate(event.target.value)}
            />
          </div>
        ) : null}
        <button
          className="rounded bg-slate-900 px-3 py-1.5 text-white disabled:bg-slate-300"
          disabled={blocked !== null}
          onClick={() => setOutcome("valid_empty")}
        >
          Ask
        </button>
      </div>
      {blocked ? <p className="mt-2 text-sm text-rose-700">{blocked}</p> : null}

      <div className="mt-5 space-y-3 text-sm">
        <p className="font-medium text-slate-700">The four answers, shown distinctly:</p>

        <div className="rounded border border-slate-300 bg-slate-50 p-3">
          <strong>Valid empty.</strong> No applicable evidence for this contract at this mode. This
          is a complete answer — no widening is offered, and no “search everything instead” button
          exists.
        </div>
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <strong>Degraded.</strong> Answered without <code>vector</code>. This answer is incomplete
          and says so; the failed sources are listed rather than hidden.
        </div>
        <div className="rounded border border-rose-300 bg-rose-50 p-3">
          <strong>Authority failure.</strong> Eligibility could not be resolved. Nothing was
          searched — this is not an empty result.
        </div>
        <div className="rounded border border-amber-300 bg-amber-50 p-3">
          <strong>Not ready.</strong> The projection is stale. Evidence is unavailable and does not
          fall back to the previous generation.
        </div>

        {outcome ? (
          <p className="text-slate-600">Current demo outcome: {outcome}</p>
        ) : null}

        <div className="mt-4 flex gap-2">
          {["Save", "Export", "Cite"].map((action) => (
            <button key={action} className="rounded border border-slate-300 px-3 py-1 text-slate-400" disabled>
              {action}
            </button>
          ))}
        </div>
        <p className="text-slate-600">
          Disabled because this answer’s provenance was never recorded. The server decides that, not
          the client — an unrecorded answer is a conversation, not a record.
        </p>
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// 5. migration review queue
// --------------------------------------------------------------------------- //

function MigrationSurface({ candidates }: { candidates: ReconciliationCandidate[] }) {
  return (
    <Panel
      title="5 · Migration review queue"
      note="Why each candidate cannot be promoted, per axis. Scope and type progress independently."
    >
      <div className="space-y-3">
        {candidates.map((candidate) => (
          <div key={candidate.candidate_id} className="rounded border border-slate-200 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <code className="text-xs">{candidate.canonical_document_id}</code>
              <span className="rounded border border-slate-300 px-2 py-0.5 text-xs">
                scope: {candidate.scope_state}
              </span>
              <span
                className={`rounded border px-2 py-0.5 text-xs ${
                  candidate.type_state === "TYPE_RESOLVED"
                    ? "border-green-300 bg-green-50"
                    : "border-slate-300 bg-white"
                }`}
              >
                type: {candidate.type_state}
                {candidate.type_state === "TYPE_SUGGESTED" ? " (suggestion, not confirmed)" : ""}
              </span>
            </div>

            {candidate.scope_hint ? (
              <p className="mt-2 text-xs text-slate-600">Hint: {candidate.scope_hint}</p>
            ) : null}

            {candidate.promotion_blocked_reasons.length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-xs text-rose-800">
                {candidate.promotion_blocked_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-green-800">
                Both axes resolved by {candidate.adjudicated_by}. Promotable by explicit selection.
              </p>
            )}

            <div className="mt-3 flex gap-2">
              <button className="rounded border border-slate-300 px-2 py-1 text-xs">Claim</button>
              <button className="rounded border border-slate-300 px-2 py-1 text-xs">Adjudicate</button>
              <button
                className="rounded bg-slate-900 px-2 py-1 text-xs text-white disabled:bg-slate-300"
                disabled={candidate.promotion_blocked_reasons.length > 0}
              >
                Promote this candidate
              </button>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 rounded bg-slate-50 p-3 text-sm text-slate-700">
        There is no “promote all”, no “accept suggestions”, and no bulk row action. Promotion adds an
        instrument to the catalogue — it is not described as making anything searchable.
      </p>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// page
// --------------------------------------------------------------------------- //

export default function ContractMasterPrototypePage() {
  const capabilities = CAPABILITIES;
  const [openId, setOpenId] = useState("cd-boq");
  const open = useMemo(
    () => CATALOGUE.find((item) => item.contract_document_id === openId) ?? CATALOGUE[0],
    [openId],
  );

  return (
    <div className="mx-auto max-w-5xl p-6">
      <header className="mb-6">
        <h1 className="text-2xl font-bold">Contract Master — layout prototype</h1>
        <p className="mt-1 text-sm text-slate-600">
          Rough HITL prototype for review. Fixture data, real response shapes.
          {USE_FIXTURES ? " Running on fixtures." : " Running against the live API."}
        </p>
      </header>

      <UploadScopeSurface capabilities={capabilities} />
      <CatalogueSurface items={CATALOGUE} capabilities={capabilities} onOpen={setOpenId} />
      <ViewerSurface item={open} />
      <QaSurface />
      <MigrationSurface candidates={CANDIDATES} />
    </div>
  );
}
