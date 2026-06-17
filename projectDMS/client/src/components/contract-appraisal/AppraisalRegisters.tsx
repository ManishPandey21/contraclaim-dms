import React, { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CheckCircle2, ListChecks, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  createAppraisalRegisters,
  listRegister,
  RegisterItem,
  RegisterKind,
  updateRegisterItem,
} from "@/services/contracts-api";

interface Props {
  reportId: string;
  organizationId?: string;
  projectId?: string;
}

function verificationBadge(status: string) {
  if (status === "verified") return <Badge className="bg-green-600">Verified</Badge>;
  if (status === "rejected") return <Badge variant="destructive">Rejected</Badge>;
  if (status === "requires_human_review")
    return <Badge variant="outline" className="border-amber-400 text-amber-700">Review</Badge>;
  return <Badge variant="outline">AI</Badge>;
}

const REGISTERS: { kind: RegisterKind; label: string }[] = [
  { kind: "obligations", label: "Obligations" },
  { kind: "risks", label: "Risks" },
  { kind: "key-dates", label: "Key Dates" },
];

function titleOf(kind: RegisterKind, r: RegisterItem): string {
  if (kind === "obligations") return r.obligation_title || "—";
  if (kind === "risks") return r.risk_title || "—";
  return r.date_title || "—";
}

const AppraisalRegisters: React.FC<Props> = ({ reportId, organizationId, projectId }) => {
  const [data, setData] = useState<Record<RegisterKind, RegisterItem[]>>({
    obligations: [],
    risks: [],
    "key-dates": [],
  });
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { organization_id: organizationId, project_id: projectId, report_id: reportId };
      const [obligations, risks, keyDates] = await Promise.all([
        listRegister("obligations", params),
        listRegister("risks", params),
        listRegister("key-dates", params),
      ]);
      setData({ obligations, risks, "key-dates": keyDates });
    } catch {
      /* registers optional until created */
    } finally {
      setLoading(false);
    }
  }, [reportId, organizationId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const onCreate = async () => {
    setCreating(true);
    try {
      const res = await createAppraisalRegisters(reportId);
      const total = Object.values(res.created).reduce((a, b) => a + b, 0);
      toast.success(`Created ${total} register entries`);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to create registers");
    } finally {
      setCreating(false);
    }
  };

  const verify = async (kind: RegisterKind, item: RegisterItem, status: string) => {
    try {
      await updateRegisterItem(kind, item._id, { verification_status: status });
      await load();
    } catch {
      toast.error("Failed to update");
    }
  };

  const totalRows = data.obligations.length + data.risks.length + data["key-dates"].length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 font-semibold">
          <ListChecks className="h-4 w-4" />
          Registers
        </h3>
        <Button size="sm" variant="outline" onClick={onCreate} disabled={creating}>
          {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ListChecks className="mr-2 h-4 w-4" />}
          {totalRows > 0 ? "Rebuild from report" : "Create registers"}
        </Button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-6 text-muted-foreground">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          Loading…
        </div>
      ) : totalRows === 0 ? (
        <p className="text-sm text-muted-foreground">
          No register entries yet. Create registers to extract obligations, risks and key dates from the cited findings.
        </p>
      ) : (
        REGISTERS.map(({ kind, label }) =>
          data[kind].length === 0 ? null : (
            <div key={kind}>
              <p className="mb-1 text-sm font-medium">
                {label} ({data[kind].length})
              </p>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Item</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead className="text-right">Verification</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data[kind].map((r) => (
                    <TableRow key={r._id}>
                      <TableCell className="max-w-[280px] truncate font-medium" title={titleOf(kind, r)}>
                        {titleOf(kind, r)}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {[r.clause_reference ? `Cl. ${r.clause_reference}` : null, r.document_name, r.page_number != null ? `p.${r.page_number}` : null]
                          .filter(Boolean)
                          .join(" · ") || "—"}
                      </TableCell>
                      <TableCell>{Math.round((r.confidence_score || 0) * 100)}%</TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          {verificationBadge(r.verification_status)}
                          {r.verification_status !== "verified" && (
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-7 w-7"
                              title="Mark verified"
                              onClick={() => verify(kind, r, "verified")}
                            >
                              <CheckCircle2 className="h-4 w-4 text-green-600" />
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          ),
        )
      )}
    </div>
  );
};

export default AppraisalRegisters;
