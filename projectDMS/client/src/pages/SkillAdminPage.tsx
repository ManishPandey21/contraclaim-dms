import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Upload } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { errorMessage } from "@/hooks/useLetterBench";
import {
  deprecateSkillVersion,
  fetchSkillAdminList,
  importSkillBundle,
  publishSkillVersion,
} from "@/services/letter-bench-api";
import type { SkillVersionSummary } from "@/types/letterBench";

const ADMIN_KEY = ["letter-bench", "skills-admin"] as const;

const DEFAULT_MANIFEST = JSON.stringify(
  {
    roles_supported: ["contractor", "engineer", "employer"],
    native_role: null,
    contract_forms: ["FIDIC", "CPWD", "NHAI_EPC"],
    output_schema_key: "variation_evaluation.v1",
    required_inputs: [],
    chains_to: [],
    validators: [],
    use_when: "",
  },
  null,
  2
);

const STATUS_TEXT = { draft: "Draft", published: "Published", deprecated: "Deprecated" } as const;

const SkillAdminPage: React.FC = () => {
  const client = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ADMIN_KEY, queryFn: () => fetchSkillAdminList() });
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState("");
  const [kind, setKind] = useState("evaluation");
  const [manifest, setManifest] = useState(DEFAULT_MANIFEST);
  const [evalFor, setEvalFor] = useState<SkillVersionSummary | null>(null);
  const [evaluation, setEvaluation] = useState({ suite: "", score: "", threshold: "0.85", case_count: "", signed_off_by: "" });
  const refresh = () => client.invalidateQueries({ queryKey: ADMIN_KEY });

  const importBundle = useMutation({
    mutationFn: importSkillBundle,
    onSuccess: () => {
      toast.success("Imported as a draft. Publish it once its evaluation passes.");
      setFile(null);
      setVersion("");
      void refresh();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The bundle was rejected")),
  });
  const publish = useMutation({
    mutationFn: (target: SkillVersionSummary) =>
      publishSkillVersion(target.skill_key, target.version, {
        suite: evaluation.suite,
        score: Number(evaluation.score),
        threshold: Number(evaluation.threshold),
        case_count: Number(evaluation.case_count),
        signed_off_by: evaluation.signed_off_by || null,
      }),
    onSuccess: () => {
      toast.success("Published");
      setEvalFor(null);
      void refresh();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The version could not be published")),
  });
  const deprecate = useMutation({
    mutationFn: (target: SkillVersionSummary) => deprecateSkillVersion(target.skill_key, target.version),
    onSuccess: () => {
      toast.success("Deprecated. Runs that used it stay readable.");
      void refresh();
    },
    onError: (failure) => toast.error(errorMessage(failure, "The version could not be deprecated")),
  });

  const versions = [...(data?.versions ?? [])].sort(
    (a, b) => a.skill_key.localeCompare(b.skill_key) || b.version.localeCompare(a.version, undefined, { numeric: true })
  );
  const evaluationReady =
    evaluation.suite.trim() && evaluation.score !== "" && evaluation.case_count !== "" && evaluation.threshold !== "";

  return (
    <div className="container mx-auto max-w-5xl space-y-6 p-2">
      <div className="flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/letters" aria-label="Back to letters">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
        <div>
          <h1 className="text-xl font-semibold">Drafting skills</h1>
          <p className="text-sm text-muted-foreground">
            A skill is the method the drafting engine follows. Published versions never change; a correction is
            a new version.
          </p>
        </div>
      </div>

      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {errorMessage(error, "Skills could not be loaded. You may not have permission to manage them.")}
        </p>
      )}

      {data && (
        <section aria-labelledby="skill-versions-heading" className="rounded-lg border">
          <h2 id="skill-versions-heading" className="border-b px-4 py-3 text-sm font-semibold">
            Versions
          </h2>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Skill</TableHead>
                <TableHead>Version</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Evaluation</TableHead>
                <TableHead>Hash</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {versions.map((item) => (
                <TableRow key={`${item.skill_key}-${item.version}`}>
                  <TableCell>
                    <div className="font-medium">{item.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {item.skill_key} · {item.kind}
                    </div>
                  </TableCell>
                  <TableCell className="tabular-nums">{item.version}</TableCell>
                  <TableCell>{STATUS_TEXT[item.status]}</TableCell>
                  <TableCell className="text-xs">
                    {item.eval_report
                      ? `${item.eval_report.suite}: ${item.eval_report.score.toFixed(2)} of ${item.eval_report.threshold.toFixed(2)} needed, ${item.eval_report.case_count} case${item.eval_report.case_count === 1 ? "" : "s"}${
                          item.eval_report.signed_off_by ? "" : " · not signed off"
                        }`
                      : "Not evaluated"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{item.content_hash.slice(0, 12)}</TableCell>
                  <TableCell className="text-right">
                    {item.status === "draft" && (
                      <Button size="sm" variant="outline" onClick={() => setEvalFor(item)}>
                        Publish…
                      </Button>
                    )}
                    {item.status === "published" && (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={deprecate.isPending}
                        onClick={() => {
                          if (window.confirm(`Deprecate ${item.name} ${item.version}? New letters will stop using it.`)) {
                            deprecate.mutate(item);
                          }
                        }}
                      >
                        Deprecate
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </section>
      )}

      {evalFor && (
        <section aria-labelledby="publish-heading" className="space-y-3 rounded-lg border p-4">
          <h2 id="publish-heading" className="text-sm font-semibold">
            Publish {evalFor.name} {evalFor.version}
          </h2>
          <p className="text-sm text-muted-foreground">
            Record the result of the golden-set evaluation. A version below its threshold cannot be published.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {(
              [
                ["suite", "Evaluation suite", "text"],
                ["case_count", "Cases run", "number"],
                ["score", "Score (0 to 1)", "number"],
                ["threshold", "Threshold (0 to 1)", "number"],
                ["signed_off_by", "Signed off by", "text"],
              ] as const
            ).map(([key, label, type]) => (
              <div key={key} className="space-y-1">
                <label htmlFor={`eval-${key}`} className="text-xs font-medium">
                  {label}
                </label>
                <Input
                  id={`eval-${key}`}
                  type={type}
                  step={type === "number" ? "0.01" : undefined}
                  value={evaluation[key]}
                  onChange={(event) => setEvaluation((current) => ({ ...current, [key]: event.target.value }))}
                />
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <Button disabled={!evaluationReady || publish.isPending} onClick={() => publish.mutate(evalFor)}>
              Publish
            </Button>
            <Button variant="ghost" onClick={() => setEvalFor(null)}>
              Cancel
            </Button>
          </div>
        </section>
      )}

      <section aria-labelledby="import-heading" className="space-y-3 rounded-lg border p-4">
        <h2 id="import-heading" className="text-sm font-semibold">
          Import a skill bundle
        </h2>
        <p className="text-sm text-muted-foreground">
          A zip with one SKILL.md and optional reference notes (.md or .txt). It is stored as a draft and is
          not used for any letter until it is published.
        </p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <label htmlFor="skill-file" className="text-xs font-medium">
              Bundle (.zip)
            </label>
            <Input
              id="skill-file"
              type="file"
              accept=".zip,application/zip"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="skill-version" className="text-xs font-medium">
              Version
            </label>
            <Input
              id="skill-version"
              value={version}
              placeholder="1.1.0"
              onChange={(event) => setVersion(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="skill-kind" className="text-xs font-medium">
              Kind
            </label>
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger id="skill-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="analysis">Analysis</SelectItem>
                <SelectItem value="evaluation">Evaluation</SelectItem>
                <SelectItem value="drafting">Drafting</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-1">
          <label htmlFor="skill-manifest" className="text-xs font-medium">
            Manifest (JSON)
          </label>
          <Textarea
            id="skill-manifest"
            value={manifest}
            rows={10}
            spellCheck={false}
            className="font-mono text-xs"
            onChange={(event) => setManifest(event.target.value)}
          />
        </div>
        <Button
          disabled={!file || !/^\d+\.\d+\.\d+$/.test(version) || importBundle.isPending}
          onClick={() => file && importBundle.mutate({ file, version, kind, manifest })}
        >
          <Upload className="mr-2 h-4 w-4" aria-hidden="true" />
          Import as draft
        </Button>
      </section>
    </div>
  );
};

export default SkillAdminPage;
