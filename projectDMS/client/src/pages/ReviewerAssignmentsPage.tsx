import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useTenant } from "@/contexts/TenantContext";
import { formatDateIN } from "@/lib/format-in";
import { errorMessage } from "@/lib/plain-language";
import { REVIEWER_ROLE_LABELS, defaultExpiry, reviewerRolesForScope } from "@/lib/reviewer-roles";
import { enhancedApi } from "@/services/enhanced-api";
import {
  fetchLegacyReviewerRoles,
  fetchReviewerAssignments,
  grantReviewerAssignment,
  revokeReviewerAssignment,
  type ReviewerAssignment,
  type ReviewerScopeType,
} from "@/services/reviewer-assignments-api";
import { getCurrentUserProfile } from "@/services/session-api";

const SCOPE_TEXT: Record<ReviewerScopeType, { label: string; help: string }> = {
  platform: {
    label: "Platform",
    help: "Signs off skill packs for every organisation. Granted by a Super Admin, to someone else.",
  },
  organization: {
    label: "This organisation",
    help: "Answers approval steps on this organisation's arbitration cases. It gives no say over skill packs.",
  },
  project: {
    label: "This project",
    help: "Answers approval steps on this project's arbitration cases only.",
  },
};

const STATE_TEXT: Record<ReviewerAssignment["state"], string> = {
  active: "Active",
  expired: "Ended",
  revoked: "Withdrawn",
};

const personName = (u: { first_name?: string; last_name?: string; username?: string; email?: string }) =>
  [u.first_name, u.last_name].filter(Boolean).join(" ").trim() || u.username || u.email || "Unnamed user";

/**
 * Who holds which reviewer role, where, until when and on whose word.
 *
 * A reviewer role is never part of a user's role list. It is granted here, for
 * one scope, with a reason, and it ends on a date or when it is withdrawn.
 */
const ReviewerAssignmentsPage: React.FC = () => {
  const client = useQueryClient();
  const { selectedOrganizationId, selectedProjectId } = useTenant();
  const me = useQuery({ queryKey: ["session", "profile"], queryFn: () => getCurrentUserProfile(), staleTime: 60_000 });
  const myRoles = Array.isArray(me.data?.roles) ? me.data.roles : String(me.data?.roles ?? "").split(",");
  const isSuperAdmin = myRoles.map((role) => role.trim().toLowerCase().replace(/[\s_-]/g, "")).includes("superadmin");
  const scopes: ReviewerScopeType[] = [
    ...(isSuperAdmin ? (["platform"] as const) : []),
    ...(selectedOrganizationId ? (["organization"] as const) : []),
    ...(selectedProjectId ? (["project"] as const) : []),
  ];
  const [chosen, setScope] = useState<ReviewerScopeType | null>(null);
  // Until the person picks one, the first scope they can act at.
  const scope: ReviewerScopeType = chosen && scopes.includes(chosen) ? chosen : (scopes[0] ?? "organization");
  const [showEnded, setShowEnded] = useState(false);
  const [form, setForm] = useState({ user_id: "", role_key: "", reason: "", expires_at: defaultExpiry() });

  const target = {
    scope_type: scope,
    organization_id: scope === "organization" ? selectedOrganizationId || undefined : undefined,
    project_id: scope === "project" ? selectedProjectId || undefined : undefined,
  };
  const listKey = ["reviewer-assignments", scope, target.organization_id ?? "", target.project_id ?? "", showEnded] as const;
  const ready = scopes.includes(scope);
  const list = useQuery({
    queryKey: listKey,
    queryFn: () => fetchReviewerAssignments({ ...target, include_inactive: showEnded }),
    enabled: ready,
    retry: false,
  });
  const people = useQuery({ queryKey: ["users", "names"], queryFn: () => enhancedApi.getUsers(), staleTime: 60_000, retry: false });
  const legacy = useQuery({
    queryKey: ["reviewer-assignments", "legacy", scope === "platform" ? "" : selectedOrganizationId],
    queryFn: () => fetchLegacyReviewerRoles(scope === "platform" ? undefined : selectedOrganizationId || undefined),
    enabled: ready,
    retry: false,
  });
  const candidates = useMemo(
    () =>
      (people.data ?? [])
        .map((user) => ({ id: String(user.id || user._id || ""), name: personName(user) }))
        .filter((user) => user.id)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [people.data]
  );
  const refresh = () => client.invalidateQueries({ queryKey: ["reviewer-assignments"] });

  const grant = useMutation({
    mutationFn: () =>
      grantReviewerAssignment({
        ...target,
        user_id: form.user_id,
        role_key: form.role_key,
        reason: form.reason.trim(),
        expires_at: scope === "platform" && !form.expires_at ? undefined : `${form.expires_at}T23:59:59Z`,
      }),
    onSuccess: (created) => {
      toast.success(`${created.user_name} now holds ${created.role_label}.`);
      setForm((current) => ({ ...current, user_id: "", reason: "" }));
      void refresh();
    },
    onError: (failure) => toast.error(errorMessage(failure)),
  });
  const revoke = useMutation({
    mutationFn: (input: { id: string; reason: string }) => revokeReviewerAssignment(input.id, input.reason),
    onSuccess: () => {
      toast.success("Withdrawn. It stops counting from the next action.");
      void refresh();
    },
    onError: (failure) => toast.error(errorMessage(failure)),
  });

  if (scopes.length === 0) {
    return (
      <div className="container mx-auto max-w-4xl p-2">
        <h1 className="text-xl font-semibold">Reviewer roles</h1>
        <p role="status" className="mt-3 rounded-md border bg-muted/40 p-3 text-sm">
          Select an organisation to see and grant its reviewer roles.
        </p>
      </div>
    );
  }
  const needsExpiry = scope !== "platform";
  const canGrant =
    form.user_id !== "" && form.role_key !== "" && form.reason.trim().length >= 5 && (!needsExpiry || form.expires_at !== "");
  const outstanding = (legacy.data?.holders ?? []).filter((holder) => !holder.has_assignment);

  return (
    <div className="container mx-auto max-w-4xl space-y-6 p-2">
      <div>
        <h1 className="text-xl font-semibold">Reviewer roles</h1>
        <p className="text-sm text-muted-foreground">
          A reviewer role lets a named person give a specialist approval. It carries no other access. It is granted
          for one scope, for a reason, by someone else, and it ends on a date or when it is withdrawn.
        </p>
      </div>

      <div role="tablist" aria-label="Scope" className="flex flex-wrap gap-2">
        {scopes.map((item) => (
          <Button
            key={item}
            role="tab"
            aria-selected={scope === item}
            size="sm"
            variant={scope === item ? "default" : "outline"}
            onClick={() => {
              setScope(item);
              setForm((current) => ({ ...current, role_key: "", expires_at: item === "platform" ? "" : defaultExpiry() }));
            }}
          >
            {SCOPE_TEXT[item].label}
          </Button>
        ))}
      </div>
      <p className="text-sm">{SCOPE_TEXT[scope].help}</p>

      <section aria-labelledby="holders-heading" className="rounded-lg border">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
          <h2 id="holders-heading" className="text-sm font-semibold">
            Who holds a reviewer role here
          </h2>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={showEnded} onChange={(event) => setShowEnded(event.target.checked)} />
            Show ended and withdrawn
          </label>
        </div>
        {list.isLoading && <p className="p-4 text-sm text-muted-foreground">Loading…</p>}
        {list.isError && (
          <p role="alert" className="p-4 text-sm text-destructive">
            {errorMessage(list.error)}
          </p>
        )}
        {list.data && list.data.length === 0 && <p className="p-4 text-sm">Nobody holds a reviewer role at this scope.</p>}
        {list.data && list.data.length > 0 && (
          <ul className="divide-y">
            {list.data.map((item) => (
              <li key={item.id} className="flex flex-wrap items-start justify-between gap-3 p-4 text-sm">
                <div>
                  <p className="font-medium">
                    {item.user_name} · {item.role_label}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {STATE_TEXT[item.state]} · granted {formatDateIN(item.granted_at)}
                    {item.expires_at ? ` · ends ${formatDateIN(item.expires_at)}` : " · no end date"}
                  </p>
                  <p className="text-xs">Reason: {item.reason}</p>
                  {item.revoke_reason && <p className="text-xs">Withdrawn because: {item.revoke_reason}</p>}
                </div>
                {item.state === "active" && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={revoke.isPending}
                    aria-label={`Withdraw ${item.role_label} from ${item.user_name}`}
                    onClick={() => {
                      const reason = window.prompt(`Why is ${item.role_label} being withdrawn from ${item.user_name}?`);
                      if (reason && reason.trim().length >= 5) revoke.mutate({ id: item.id, reason: reason.trim() });
                      else if (reason !== null) toast.error("Give a reason of at least five characters.");
                    }}
                  >
                    Withdraw
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="grant-heading" className="space-y-3 rounded-lg border p-4">
        <h2 id="grant-heading" className="text-sm font-semibold">
          Grant a reviewer role
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor="grant-person">Person</Label>
            <select
              id="grant-person"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={form.user_id}
              onChange={(event) => setForm((current) => ({ ...current, user_id: event.target.value }))}
            >
              <option value="">Choose a person</option>
              {candidates.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-muted-foreground">You cannot grant a role to yourself.</p>
          </div>
          <div className="space-y-1">
            <Label htmlFor="grant-role">Reviewer role</Label>
            <select
              id="grant-role"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={form.role_key}
              onChange={(event) => setForm((current) => ({ ...current, role_key: event.target.value }))}
            >
              <option value="">Choose a role</option>
              {reviewerRolesForScope(scope).map((role) => (
                <option key={role} value={role}>
                  {REVIEWER_ROLE_LABELS[role]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="grant-expiry">{needsExpiry ? "Ends on" : "Ends on (optional)"}</Label>
            <Input
              id="grant-expiry"
              type="date"
              value={form.expires_at}
              onChange={(event) => setForm((current) => ({ ...current, expires_at: event.target.value }))}
            />
            {needsExpiry && <p className="text-xs text-muted-foreground">At most twelve months. Grant it again after that.</p>}
          </div>
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="grant-reason">Reason</Label>
            <Textarea
              id="grant-reason"
              rows={2}
              value={form.reason}
              placeholder="Why this person holds this role"
              onChange={(event) => setForm((current) => ({ ...current, reason: event.target.value }))}
            />
          </div>
        </div>
        <Button disabled={!canGrant || grant.isPending} onClick={() => grant.mutate()}>
          Grant role
        </Button>
        {!canGrant && (
          <p className="text-xs text-muted-foreground">
            Choose a person and a role, give a reason{needsExpiry ? " and an end date" : ""}.
          </p>
        )}
      </section>

      {outstanding.length > 0 && (
        <section aria-labelledby="legacy-heading" className="space-y-2 rounded-lg border border-amber-600 bg-amber-50 p-4 text-amber-950">
          <h2 id="legacy-heading" className="text-sm font-semibold">
            Held without a grant
          </h2>
          <p className="text-sm">
            These people have a reviewer role on their role list from before grants existed. It still counts on
            arbitration cases and never on skill packs. Grant the role above to each person who should keep it;
            nothing is converted automatically.
          </p>
          <ul className="list-disc pl-5 text-sm">
            {outstanding.map((holder) => (
              <li key={`${holder.user_id}-${holder.role_key}`}>
                {holder.user_name}: {REVIEWER_ROLE_LABELS[holder.role_key] ?? holder.role_key}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

export default ReviewerAssignmentsPage;
