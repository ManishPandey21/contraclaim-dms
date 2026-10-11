/**
 * Contract Master workspace — the product surface for T26–T30.
 *
 * This page **composes**; it implements nothing. Every surface is one of the
 * components in `components/contract-master/`, and the page's only jobs are to
 * fetch from `/api/contract-master/*`, hold which section is open, and pass
 * server answers down.
 *
 * That split is deliberate rather than tidy. Each component owns one rule with a
 * plausible wrong version — an unanswered scope becoming organisation scope, a
 * remembered readiness flag, a degraded answer reading as an empty one — and
 * duplicating any of them here would create a second copy that drifts. An
 * earlier pass at this page did exactly that before the duplication was spotted;
 * the components are now the single implementation.
 *
 * Two page-level rules of its own:
 *
 * * **Every instrument read is a fresh fetch.** Readiness is never carried over
 *   from the catalogue row that opened it — the list is a snapshot, the detail
 *   is the current answer.
 * * **Capability flags decide what is rendered, never what is permitted.** The
 *   server authorises every call again, so a tampered client sees more tabs and
 *   achieves nothing.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { CatalogueTable } from "@/components/contract-master/CatalogueTable";
import { ContractQaPanel } from "@/components/contract-master/ContractQaPanel";
import { InstrumentStatePanel } from "@/components/contract-master/InstrumentStatePanel";
import { MigrationReviewQueue } from "@/components/contract-master/MigrationReviewQueue";
import { UploadScopeForm } from "@/components/contract-master/UploadScopeForm";
import {
  contractMasterApi,
  type CatalogueItem,
  type ContractMasterCapabilities,
  type InstrumentDetail,
  type ReconciliationCandidate,
} from "@/services/contract-master-v1-api";

export type WorkspaceSection = "upload" | "catalogue" | "instrument" | "qa" | "migration";

const SECTIONS: Array<[WorkspaceSection, string]> = [
  ["upload", "Upload"],
  ["catalogue", "Catalogue"],
  ["instrument", "Instrument"],
  ["qa", "Q&A"],
  ["migration", "Migration review"],
];

export interface ContractMasterWorkspacePageProps {
  organizationId?: string;
  projectId?: string;
  contractId?: string;
}

export default function ContractMasterWorkspacePage({
  organizationId = "org-A",
  projectId = "proj-A",
  contractId = "contract-1",
}: ContractMasterWorkspacePageProps) {
  const [section, setSection] = useState<WorkspaceSection>("upload");
  const [capabilities, setCapabilities] = useState<ContractMasterCapabilities | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueItem[]>([]);
  const [candidates, setCandidates] = useState<ReconciliationCandidate[]>([]);
  const [openInstrumentId, setOpenInstrumentId] = useState<string | null>(null);
  const [openInstrument, setOpenInstrument] = useState<InstrumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // A new organisation starts clean: nothing from the previous one - error,
    // capabilities, lists or open instrument - survives the switch, and a late
    // answer for the previous organisation is dropped rather than shown here.
    let current = true;
    setError(null);
    setCapabilities(null);
    setCatalogue([]);
    setCandidates([]);
    setOpenInstrumentId(null);
    setOpenInstrument(null);
    contractMasterApi
      .capabilities(organizationId)
      .then((answer) => {
        if (current) setCapabilities(answer);
      })
      .catch((cause) => {
        if (current) setError(String(cause));
      });
    return () => {
      current = false;
    };
  }, [organizationId]);

  useEffect(() => {
    if (!capabilities?.can_browse_catalogue) return;
    let current = true;
    contractMasterApi
      .catalogue(organizationId)
      .then((body) => {
        if (current) setCatalogue(body.items);
      })
      .catch((cause) => {
        if (current) setError(String(cause));
      });
    return () => {
      current = false;
    };
  }, [capabilities, organizationId]);

  useEffect(() => {
    if (!capabilities?.can_review_migration) return;
    let current = true;
    contractMasterApi
      .reconciliationCandidates(organizationId)
      .then((body) => {
        if (current) setCandidates(body.candidates);
      })
      .catch(() => {
        if (current) setCandidates([]);
      });
    return () => {
      current = false;
    };
  }, [capabilities, organizationId]);

  /**
   * Always a fresh read. The panel fetches rather than being handed the
   * catalogue row: that row's readiness was true when the list was built, which
   * is not a claim about now.
   */
  const fetchInstrument = useCallback(async (contractDocumentId: string) => {
    const detail = await contractMasterApi.instrument(contractDocumentId);
    setOpenInstrument(detail);
    return detail;
  }, []);

  const open = useCallback((contractDocumentId: string) => {
    setOpenInstrumentId(contractDocumentId);
    setSection("instrument");
  }, []);

  /**
   * Q&A launches only from an evidence-ready instrument. With nothing open the
   * panel is told it came from a browse listing with no readiness — the safe
   * reading of "we do not know".
   */
  const launchedFrom = useMemo(
    () =>
      openInstrument
        ? { source: "evidence" as const, evidenceReady: openInstrument.evidence_ready }
        : { source: "browse" as const, evidenceReady: false },
    [openInstrument],
  );

  if (!capabilities) {
    // A capabilities failure is an outage, not "no permission": show it rather
    // than waiting forever.
    if (error) {
      return (
        <p className="m-6 rounded bg-rose-50 p-3 text-sm text-rose-800" role="alert">
          {error}
        </p>
      );
    }
    return (
      <div className="p-6" data-testid="workspace-loading">
        Loading capabilities…
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-6" data-testid="contract-master-workspace">
      <h1 className="text-2xl font-bold">Contract Master</h1>

      <nav className="mt-4 flex flex-wrap gap-2 border-b border-slate-200 pb-2 text-sm">
        {SECTIONS.map(([key, label]) => {
          if (key === "catalogue" && !capabilities.can_browse_catalogue) return null;
          if (key === "migration" && !capabilities.can_review_migration) return null;
          return (
            <button
              key={key}
              className={`rounded px-3 py-1 ${
                section === key ? "bg-slate-900 text-white" : "bg-slate-100"
              }`}
              onClick={() => setSection(key)}
              data-testid={`tab-${key}`}
            >
              {label}
            </button>
          );
        })}
      </nav>

      {error ? (
        <p className="mt-4 rounded bg-rose-50 p-3 text-sm text-rose-800" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-6">
        {section === "upload" ? (
          <UploadScopeForm
            capabilities={capabilities}
            projects={[{ id: projectId, name: projectId }]}
            selectedContractId={contractId}
            onSubmit={() => setError(null)}
          />
        ) : null}

        {section === "catalogue" ? <CatalogueTable items={catalogue} onOpen={open} /> : null}

        {section === "instrument" ? (
          openInstrumentId ? (
            <InstrumentStatePanel
              contractDocumentId={openInstrumentId}
              projectId={projectId}
              fetchInstrument={fetchInstrument}
            />
          ) : (
            <p className="text-sm text-slate-600">Open an instrument from the catalogue.</p>
          )
        ) : null}

        {section === "qa" ? (
          <ContractQaPanel
            projectId={projectId}
            contractId={contractId}
            search={contractMasterApi.searchEvidence}
            // Provenance is the server's answer. Nothing here infers it, and the
            // conservative default is that it was not recorded.
            provenanceCapabilities={{ can_save: false, can_export: false, can_cite: false }}
            launchedFrom={launchedFrom}
          />
        ) : null}

        {section === "migration" ? <MigrationReviewQueue candidates={candidates} /> : null}
      </div>
    </div>
  );
}
