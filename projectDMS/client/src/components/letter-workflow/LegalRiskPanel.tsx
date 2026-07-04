import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AlertTriangle, ShieldCheck } from "lucide-react";
import type { LegalRiskReport } from "@/types/letterDrafting";

const CATEGORY_LABEL: Record<string, string> = {
  admission: "Possible admission",
  waiver: "Possible waiver",
  contradiction: "Possible contradiction",
  entitlement: "Entitlement language",
};

const SEVERITY_CLASS: Record<string, string> = {
  high: "bg-red-50 text-red-700 ring-1 ring-inset ring-red-200",
  caution: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200",
  info: "bg-gray-100 text-gray-700 ring-1 ring-inset ring-gray-200",
};

interface Props {
  report?: LegalRiskReport | null;
}

/**
 * Legal / Contractual Risk Review panel: flags admissions, waivers,
 * entitlement language and stance reversals. Advisory only — the human
 * decides the contractual position.
 */
const LegalRiskPanel = ({ report }: Props) => {
  if (!report) return null;

  const flags = report.flags ?? [];

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          {flags.length ? (
            <AlertTriangle className="h-4 w-4 text-amber-600" />
          ) : (
            <ShieldCheck className="h-4 w-4 text-emerald-600" />
          )}
          Legal / contractual risk review
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          {flags.length
            ? `${flags.length} flag(s) for human review — flags never block; you decide the position.`
            : "No admissions, waivers, entitlement language or contradictions detected."}
        </p>
      </CardHeader>
      {flags.length > 0 && (
        <CardContent className="space-y-2">
          {flags.map((flag) => (
            <div key={flag.flag_id} className="rounded-md border p-2">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase ${
                    SEVERITY_CLASS[flag.severity] ?? SEVERITY_CLASS.info
                  }`}
                >
                  {flag.severity}
                </span>
                <span className="text-sm font-medium">
                  {CATEGORY_LABEL[flag.category] ?? flag.category}
                </span>
              </div>
              <p className="text-xs italic text-muted-foreground">“{flag.excerpt}”</p>
              <p className="mt-1 text-xs">{flag.explanation}</p>
            </div>
          ))}
        </CardContent>
      )}
    </Card>
  );
};

export default LegalRiskPanel;
