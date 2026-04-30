import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "@/services/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";
import PlanViewer from "@/components/langgraph/PlanViewer";
import { LANGGRAPH_ENABLED } from "@/config/features";
import { useLanggraphDraft } from "@/hooks/useLanggraphDraft";
import { useLetterGraphRuns } from "@/hooks/useLetterGraphRuns";
import type { LanggraphDraftResponse } from "@/types/langgraph";

interface LetterMetadata {
  subject: string;
  recipient: string;
  status?: string;
}

const LetterDraftPage: React.FC = () => {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const letterId = useMemo(() => params.id ?? "", [params]);

  const [meta, setMeta] = useState<LetterMetadata>({
    subject: "",
    recipient: "",
  });
  const [context, setContext] = useState("");
  const [points, setPoints] = useState("");

  const {
    data: runData,
    loading: runLoading,
    error: runError,
    fetchRun,
    setData: setRunData,
  } = useLetterGraphRuns();

  const {
    data: draftResponse,
    loading: drafting,
    error: draftError,
    runDraft,
    reset: resetDraft,
  } = useLanggraphDraft();

  const loadLetter = useCallback(async () => {
    if (!letterId) return;
    try {
      const { data } = await api.get(`/letters/${letterId}`);
      const subject = String(data?.subject ?? "");
      const recipient = String(data?.recipient ?? "");
      setMeta({
        subject,
        recipient,
        status: data?.status ?? undefined,
      });
      if (LANGGRAPH_ENABLED && data?.graph_status && !runData) {
        await fetchRun(letterId);
      }
    } catch (err) {
      console.error("Failed to load letter metadata", err);
    }
  }, [letterId, fetchRun, runData]);

  useEffect(() => {
    loadLetter();
  }, [loadLetter]);

  useEffect(() => {
    if (LANGGRAPH_ENABLED && letterId) {
      fetchRun(letterId).catch(() => undefined);
    }
  }, [letterId, fetchRun]);

  const latestRun: LanggraphDraftResponse | null = draftResponse ?? runData;

  const handleRun = async () => {
    if (!letterId) return;
    try {
      const response = await runDraft({
        letterId,
        subject: meta.subject,
        recipient: meta.recipient,
        context,
        points,
      });
      setRunData(response);
    } catch (err) {
      console.error(err);
    }
  };

  if (!LANGGRAPH_ENABLED) {
    return (
      <div className="mx-auto max-w-4xl space-y-6 py-10">
        <Card>
          <CardHeader>
            <CardTitle>LangGraph Drafting</CardTitle>
            <CardDescription>
              LangGraph-driven drafting is not enabled in this environment.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => navigate(-1)}>
              Go Back
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 py-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">LangGraph Draft Workspace</h1>
          <p className="text-sm text-muted-foreground">
            Review the AI-generated plan, adjust context, and rerun the pipeline
            as needed.
          </p>
        </div>
        <GraphStatusBadge status={latestRun?.status ?? runData?.status ?? null} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Draft Inputs</CardTitle>
          <CardDescription>
            Update recipient context or talking points before generating a new
            draft.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">Subject</label>
              <Input
                value={meta.subject}
                onChange={(e) =>
                  setMeta((prev) => ({ ...prev, subject: e.target.value }))
                }
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Recipient</label>
              <Input
                value={meta.recipient}
                onChange={(e) =>
                  setMeta((prev) => ({ ...prev, recipient: e.target.value }))
                }
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Context</label>
            <Textarea
              rows={4}
              value={context}
              onChange={(e) => setContext(e.target.value)}
              placeholder="Provide updates or background information for this draft run."
            />
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Key Points</label>
            <Textarea
              rows={4}
              value={points}
              onChange={(e) => setPoints(e.target.value)}
              placeholder="List bullet points or questions you want addressed."
            />
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={handleRun} disabled={drafting || !letterId}>
              {drafting ? "Running LangGraph..." : "Run LangGraph"}
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                resetDraft();
                if (letterId) {
                  fetchRun(letterId).catch(() => undefined);
                }
              }}
              disabled={drafting}
            >
              Refresh Status
            </Button>
            <Button variant="ghost" onClick={() => navigate(-1)}>
              Back
            </Button>
          </div>
          {draftError && (
            <p className="text-sm text-destructive">{draftError}</p>
          )}
          {runError && <p className="text-sm text-destructive">{runError}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Latest Draft</CardTitle>
          <CardDescription>
            Generated output from the LangGraph pipeline. Update the inputs
            above and rerun to iterate on the draft.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {runLoading && !latestRun ? (
            <Skeleton className="h-48 w-full" />
          ) : latestRun ? (
            <>
              {latestRun.warnings?.length > 0 && (
                <div className="rounded-md border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-900">
                  <p className="font-semibold">Warnings</p>
                  <ul className="mt-1 list-disc space-y-1 pl-5">
                    {latestRun.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}

              <PlanViewer
                plan={latestRun.plan}
                summaryPoints={latestRun.summary_points}
                trace={latestRun.trace}
              />

              <section className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground">
                  Draft Body
                </h3>
                <pre className="whitespace-pre-wrap rounded-md border bg-muted/40 p-4 text-sm">
                  {latestRun.draft.body}
                </pre>
              </section>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              No LangGraph run recorded yet. Provide inputs above and select
              “Run LangGraph”.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterDraftPage;
