/**
 * The organisation catalogue — seven orthogonal dimensions, kept apart.
 *
 * They are separate columns because they are separate facts, and any two of
 * them can disagree. An instrument can be legally applicable while its content
 * is blocked; it can be classified and current while applying to nothing; it can
 * be applicable with a stale projection. A single "status" column would have to
 * pick one of those to report and silently drop the rest, and the one it picked
 * would be read as the whole answer.
 *
 * On narrow viewports the columns collapse into a stacked card, but every
 * dimension survives the collapse — responsive layout may change where a fact
 * appears, never whether it does (UI-01).
 *
 * Catalogue membership is not evidence. An organisation instrument applying to
 * nothing is a healthy row, rendered plainly, with `evidence_ready` reading
 * "No".
 */

import React from "react";

import type { CatalogueItem } from "@/services/contract-master-v1-api";
import { StateBadge } from "./StateBadge";

export interface CatalogueTableProps {
  items: CatalogueItem[];
  onOpen?: (contractDocumentId: string) => void;
}

const HEADERS = [
  "Instrument",
  "Type",
  "Scope",
  "Applicability",
  "Projection",
  "Content",
  "State",
] as const;

export function CatalogueTable({ items, onOpen }: CatalogueTableProps) {
  if (items.length === 0) {
    return (
      <p className="rounded border border-slate-200 p-4 text-sm text-slate-600">
        No instruments in this organisation catalogue.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-slate-600">
          <tr>
            {HEADERS.map((header) => (
              <th key={header} scope="col" className="py-2 pr-4 font-medium">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.contract_document_id}
              data-testid={`catalogue-row-${item.contract_document_id}`}
              className="border-t border-slate-100 align-top"
            >
              <td className="py-2 pr-4">
                {onOpen ? (
                  <button className="underline" onClick={() => onOpen(item.contract_document_id)}>
                    {item.contract_document_id}
                  </button>
                ) : (
                  <span>{item.contract_document_id}</span>
                )}
                <span
                  data-testid="evidence-ready"
                  className="ml-2 text-xs text-slate-500"
                >
                  evidence: {item.evidence_ready ? "Yes" : "No"}
                </span>
              </td>
              <td data-testid="type" className="py-2 pr-4">
                {item.contract_document_type ?? "unclassified"}
                <span className="ml-1 text-xs text-slate-500">
                  (rev {item.classification_revision})
                </span>
              </td>
              <td data-testid="scope" className="py-2 pr-4">
                {item.scope_level ?? "—"}
              </td>
              <td data-testid="applicability" className="py-2 pr-4">
                {item.applicability_count}
              </td>
              <td data-testid="projection" className="py-2 pr-4">
                {item.projection_status ?? "—"}
              </td>
              <td data-testid="content" className="py-2 pr-4">
                {item.content_consumable ? "available" : "blocked"}
              </td>
              <td className="py-2">
                <StateBadge state={item.viewer_state} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default CatalogueTable;
