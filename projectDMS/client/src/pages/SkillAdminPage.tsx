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
import SkillPublishPanel from "@/components/skills/SkillPublishPanel";
import { reviewerRoleLabel } from "@/lib/skill-approvals";
import {
  deprecateSkillVersion,
  fetchSkillAdminList,
  fetchSkillReviewQueue,
  importSkillBundle,
} from "@/services/letter-bench-api";
import type { SkillReviewQueueItem, SkillVersionSummary } from "@/types/letterBench";

const ADMIN_KEY = ["letter-bench", "skills-admin"] as const;
const QUEUE_KEY = ["skills", "review-queue"] as const;

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
  const { data, isLoading, error } = useQuery({
    queryKey: ADMIN_KEY,
    queryFn: () => fetchSkillAdminList(),
    retry: false,
  });
  // A reviewer holds no publishing permission, so the list above is refused for
  // them. Their queue is what they came for, and it needs no permission.
  const queue = useQuery({ queryKey: QUEUE_KEY, queryFn: () => fetchSkillReviewQueue(), retry: false });
  const [reviewing, setReviewing] = useState<SkillReviewQueueItem | null>(null);
  const isReviewer = (queue.data?.my_roles.length ?? 0) > 0;
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState("");
  const [kind, setKind] = useState("evaluation");
  const [manifest, setManifest] = useState(DEFAULT_MANIFEST);
  const [evalFor, setEvalFor] = useState<SkillVersionSummary | null>(null);
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
  return (
    <div className="container mx-auto max-w-5xl space-y-6 p-2">
      <div className="flex items-center gap-3">
        <Button asChild variant="ghost" size="sm">
          <Link to="/letters" aria-label="Back to letters">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Link>
        </Button>
        <div>
          <h1 className="text-xl font-semibold">Skills</h1>
          <p className="text-sm text-muted-foreground">
            A skill is the method the engine follows when it analyses, evaluates or drafts. Published versions
            never change; a correction is a new version. Claim and arbitration skills need technical and legal
            approval before they can be published.
          </p>
        </div>
      </div>

      {isReviewer && (
        <section aria-labelledby="review-queue-heading" className="rounded-lg border">
          <h2 id="review-queue-heading" className="border-b px-4 py-3 text-sm font-semibold">
            Waiting for your review
          </h2>
          <p className="px-4 pt-3 text-sm text-muted-foreground">
            You hold: {queue.data!.my_roles.map(reviewerRoleLabel).join(", ")}.
          </p>
          {queue.data!.items.length === 0 ? (
            <p className="p-4 text-sm">No draft is asking for your review.</p>
          ) : (
            <ul className="divide-y">
              {queue.data!.items.map((item) => (
                <li
                  key={`${item.skill_key}-${item.version}`}
                  className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm"
                >
                  <div>
                    <p className="font-medium">
                      {item.name} <span className="tabular-nums">{item.version}</span>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {item.reviews
                        .map(
                          (review) =>
                            `${reviewerRoleLabel(review.reviewer_role)}: ${
                              review.given_by_me ? "given by you" : review.given ? "given" : "not yet given"
                            }`
                        )
                        .join(" · ")}
                    </p>
                  </div>
                  <Button size="sm" variant={item.waiting_on_me ? "default" : "outline"} onClick={() => setReviewing(item)}>
                    {item.waiting_on_me ? "Review…" : "Open"}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {reviewing && (
        <SkillPublishPanel
          key={`review-${reviewing.skill_key}-${reviewing.version}-${reviewing.content_hash}`}
          reviewOnly
          myRoles={queue.data?.my_roles ?? []}
          version={{
            skill_key: reviewing.skill_key,
            version: reviewing.version,
            name: reviewing.name,
            content_hash: reviewing.content_hash,
            required_approvals: reviewing.reviews.map(({ kind, reviewer_role }) => ({ kind, reviewer_role })),
          }}
          onClose={() => {
            setReviewing(null);
            void client.invalidateQueries({ queryKey: QUEUE_KEY });
          }}
          onPublished={() => undefined}
        />
      )}

      {isLoading && <Skeleton className="h-48 w-full" />}
      {error && !isReviewer && (
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
                  <TableCell>
                    {STATUS_TEXT[item.status]}
                    {item.status === "draft" && (item.required_approvals?.length ?? 0) > 0 && (
                      <div className="text-xs text-muted-foreground">
                        Needs: {item.required_approvals!.map((r) => reviewerRoleLabel(r.reviewer_role)).join(", ")}
                      </div>
                    )}
                  </TableCell>
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
                        {(item.required_approvals?.length ?? 0) > 0 ? "Review and publish…" : "Publish…"}
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
        <SkillPublishPanel
          key={`${evalFor.skill_key}-${evalFor.version}-${evalFor.content_hash}`}
          version={evalFor}
          onClose={() => setEvalFor(null)}
          onPublished={() => {
            setEvalFor(null);
            void refresh();
          }}
        />
      )}

      {data && (
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
      )}
    </div>
  );
};

export default SkillAdminPage;
