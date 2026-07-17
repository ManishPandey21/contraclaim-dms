import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  FileCheck2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useLetterDrafting } from "@/hooks/useLetterDrafting";
import type {
  DraftMetricKpi,
  DraftQualityDashboardResponse,
} from "@/types/letterDrafting";
import { formatDateTime } from "@/utils/dateFormat";

const windowOptions = [7, 30, 60, 90] as const;

const statusVariant = (status: DraftMetricKpi["status"]) => {
  if (status === "risk") return "danger";
  if (status === "watch") return "warning";
  return "success";
};

const kpiIcon = (key: string) => {
  if (key === "cycle_time") return Clock3;
  if (key === "unsupported_claims") return AlertTriangle;
  if (key === "source_integrity") return ShieldCheck;
  if (key === "artifact_compliance") return FileCheck2;
  if (key === "overdue_reviews") return AlertTriangle;
  return CheckCircle2;
};

const pct = (value: number) => `${Math.round((value || 0) * 100)}%`;

export default function LetterQualityDashboardPage() {
  const { getQualityDashboard } = useLetterDrafting();
  const [windowDays, setWindowDays] = useState<number>(30);
  const [dashboard, setDashboard] =
    useState<DraftQualityDashboardResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDashboard = useCallback(async (days = windowDays) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getQualityDashboard({ windowDays: days });
      setDashboard(data);
    } catch (err: any) {
      setError(err?.message ?? "Unable to load letter quality metrics.");
    } finally {
      setLoading(false);
    }
  }, [getQualityDashboard, windowDays]);

  useEffect(() => {
    void loadDashboard(windowDays);
  }, [loadDashboard, windowDays]);

  const maxTrendRuns = useMemo(
    () =>
      Math.max(
        1,
        ...(dashboard?.quality_trends ?? []).map((point) => point.runs)
      ),
    [dashboard?.quality_trends]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-slate-950">
            Letter Quality Dashboard
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Metrics for drafting cycle time, source grounding, governance, and issued artifacts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={String(windowDays)}
            onValueChange={(value) => setWindowDays(Number(value))}
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {windowOptions.map((days) => (
                <SelectItem key={days} value={String(days)}>
                  {days} days
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            className="gap-2"
            onClick={() => void loadDashboard()}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {(dashboard?.kpis ?? []).map((kpi) => {
          const Icon = kpiIcon(kpi.key);
          return (
            <Card key={kpi.key}>
              <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
                <CardTitle className="text-sm font-medium text-slate-700">
                  {kpi.label}
                </CardTitle>
                <Icon className="h-5 w-5 text-slate-500" />
              </CardHeader>
              <CardContent>
                <div className="flex items-end justify-between gap-3">
                  <p className="text-3xl font-semibold text-slate-950">
                    {kpi.formatted_value}
                  </p>
                  <Badge variant={statusVariant(kpi.status)}>
                    {kpi.status}
                  </Badge>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  Target {kpi.target ?? "-"}
                </p>
                {kpi.detail && (
                  <p className="mt-2 text-xs leading-5 text-slate-600">
                    {kpi.detail}
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <BarChart3 className="h-5 w-5 text-blue-600" />
              Quality Trend
            </CardTitle>
          </CardHeader>
          <CardContent>
            {(dashboard?.quality_trends ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">
                No draft runs found for the selected window.
              </p>
            ) : (
              <div className="space-y-3">
                {dashboard?.quality_trends.map((point) => (
                  <div key={point.date} className="grid gap-2">
                    <div className="flex items-center justify-between gap-3 text-sm">
                      <span className="font-medium text-slate-700">
                        {point.date}
                      </span>
                      <span className="text-slate-500">
                        {point.runs} runs · {point.approved} approved · {pct(point.average_confidence)} confidence
                      </span>
                    </div>
                    <div className="h-3 overflow-hidden rounded-full bg-slate-100">
                      <div
                        className="h-full rounded-full bg-blue-500"
                        style={{
                          width: `${Math.max(4, (point.runs / maxTrendRuns) * 100)}%`,
                        }}
                      />
                    </div>
                    {point.unsupported_rate > 0 && (
                      <p className="text-xs text-red-600">
                        Unsupported claim rate {pct(point.unsupported_rate)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Run Mix</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {(dashboard?.status_breakdown ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">No statuses available.</p>
            ) : (
              dashboard?.status_breakdown.map((item) => (
                <div key={item.label} className="space-y-1">
                  <div className="flex items-center justify-between text-sm">
                    <span className="capitalize text-slate-700">
                      {item.label.replaceAll("_", " ")}
                    </span>
                    <span className="text-slate-500">{item.count}</span>
                  </div>
                  <Progress value={Math.round(item.percentage * 100)} />
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Operational Summary</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm">
            <Metric label="Total runs" value={dashboard?.total_runs ?? 0} />
            <Metric label="Active runs" value={dashboard?.active_runs ?? 0} />
            <Metric label="Approved" value={dashboard?.approved_runs ?? 0} />
            <Metric label="Exported" value={dashboard?.exported_runs ?? 0} />
            <Metric label="Issued" value={dashboard?.issued_runs ?? 0} />
            <Metric
              label="Avg sources/run"
              value={(dashboard?.average_sources_per_run ?? 0).toFixed(1)}
            />
            <Metric
              label="Avg iterations"
              value={(dashboard?.average_iterations ?? 0).toFixed(1)}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Bottlenecks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(dashboard?.bottlenecks ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">No active bottlenecks.</p>
            ) : (
              dashboard?.bottlenecks.map((item) => (
                <div
                  key={item.stage}
                  className="rounded-md border p-3 text-sm"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium capitalize">
                      {item.stage.replaceAll("_", " ")}
                    </span>
                    <Badge variant="outline">{item.count}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    Avg age {item.average_age_hours.toFixed(1)}h
                  </p>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent Quality Risks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {(dashboard?.recent_risks ?? []).length === 0 ? (
              <p className="text-sm text-slate-500">
                No quality risks in the selected window.
              </p>
            ) : (
              dashboard?.recent_risks.map((risk) => (
                <div
                  key={`${risk.run_id}-${risk.risk}`}
                  className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="warning">{risk.risk}</Badge>
                    <span className="font-mono text-xs text-slate-600">
                      {risk.run_id}
                    </span>
                  </div>
                  <p className="mt-2 text-slate-700">{risk.detail}</p>
                  {risk.created_at && (
                    <p className="mt-1 text-xs text-slate-500">
                      {formatDateTime(risk.created_at)}
                    </p>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {dashboard?.generated_at && (
        <p className="text-xs text-slate-500">
          Generated {formatDateTime(dashboard.generated_at)}
        </p>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="flex items-center justify-between border-b pb-2 last:border-b-0 last:pb-0">
      <span className="text-slate-600">{label}</span>
      <span className="font-semibold text-slate-950">{value}</span>
    </div>
  );
}
