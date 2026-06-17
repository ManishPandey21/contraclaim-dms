import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertTriangle, Clock, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { getUpcoming, SlaItem, SlaState } from "@/services/sla-api";

const TYPE_LABELS: Record<string, string> = {
  eot: "Extension of Time",
  variation: "Variation",
  payment_ipc: "Payment / IPC",
  loss_expense: "Loss & Expense",
  acceleration: "Acceleration",
  defect: "Defect",
  other: "Other",
};

const KIND_LABELS: Record<string, string> = {
  response: "Response due",
  time_bar: "Time-bar (notice)",
};

const WINDOW_OPTIONS = [7, 14, 30, 60, 90];

function stateBadge(state: SlaState, daysRemaining: number) {
  if (state === "breached") {
    return (
      <Badge variant="destructive" className="gap-1">
        <AlertTriangle className="h-3 w-3" />
        Breached ({Math.abs(daysRemaining)}d over)
      </Badge>
    );
  }
  const urgent = daysRemaining <= 3;
  return (
    <Badge
      variant="outline"
      className={
        urgent
          ? "gap-1 border-amber-500 text-amber-600"
          : "gap-1 border-slate-300 text-slate-600"
      }
    >
      <Clock className="h-3 w-3" />
      {daysRemaining}d left
    </Badge>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

const SLATrackerPage: React.FC = () => {
  const [items, setItems] = useState<SlaItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(14);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getUpcoming({ days });
      setItems(data);
    } catch {
      toast.error("Failed to load SLA deadlines");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    void load();
  }, [load]);

  const { breached, approaching } = useMemo(() => {
    return {
      breached: items.filter((i) => i.state === "breached"),
      approaching: items.filter((i) => i.state === "approaching"),
    };
  }, [items]);

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">SLA &amp; Time-bar Tracker</h1>
          <p className="text-sm text-muted-foreground">
            Correspondence and contractual notice deadlines across your claims.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={String(days)}
            onValueChange={(v) => setDays(Number(v))}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOW_OPTIONS.map((d) => (
                <SelectItem key={d} value={String(d)}>
                  Next {d} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" size="icon" onClick={() => void load()}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Card className="border-red-200">
          <CardHeader className="pb-2">
            <CardDescription>Breached</CardDescription>
            <CardTitle className="text-3xl text-red-600">
              {breached.length}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card className="border-amber-200">
          <CardHeader className="pb-2">
            <CardDescription>Approaching (≤ {days}d)</CardDescription>
            <CardTitle className="text-3xl text-amber-600">
              {approaching.length}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Deadlines</CardTitle>
          <CardDescription>
            Most urgent first. Breached deadlines need immediate action.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading deadlines…
            </div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-muted-foreground">
              No deadlines within the selected window. You're on top of things.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Claim</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Deadline</TableHead>
                  <TableHead>Due date</TableHead>
                  <TableHead className="text-right">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={`${item.claim_id}-${item.kind}`}>
                    <TableCell className="font-medium">
                      <Link
                        to="/claims"
                        className="hover:underline"
                        title={item.claim_id}
                      >
                        {item.claim_ref || item.title || item.claim_id}
                      </Link>
                    </TableCell>
                    <TableCell>
                      {TYPE_LABELS[item.type || "other"] || item.type}
                    </TableCell>
                    <TableCell>{KIND_LABELS[item.kind] || item.kind}</TableCell>
                    <TableCell>{formatDate(item.due_date)}</TableCell>
                    <TableCell className="text-right">
                      {stateBadge(item.state, item.days_remaining)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default SLATrackerPage;
