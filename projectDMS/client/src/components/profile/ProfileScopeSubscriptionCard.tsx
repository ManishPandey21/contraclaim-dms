import { useEffect, useMemo, useState } from "react";
import { Building2, CalendarDays, CreditCard, FolderKanban } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useTenant } from "@/contexts/TenantContext";
import {
  ACTIVE_SUBSCRIPTION_STATUSES,
  resolveActiveSubscription,
} from "@/lib/profile-subscription";
import {
  getPlanCatalog,
  listSubscriptions,
  type PlanSettingsPlan,
  type Subscription,
} from "@/services/plan-settings-api";
import { formatDate } from "@/utils/dateFormat";

type SubscriptionState = {
  subscription: Subscription | null;
  plans: PlanSettingsPlan[];
  loading: boolean;
  error: string | null;
};

const humanize = (value?: string | null) =>
  value
    ? value
        .replace(/[_-]+/g, " ")
        .replace(/\b\w/g, (character) => character.toUpperCase())
    : "Not available";

const statusVariant = (status?: string | null): BadgeProps["variant"] => {
  const normalized = String(status || "").toLowerCase();
  if (ACTIVE_SUBSCRIPTION_STATUSES.has(normalized)) return "success";
  if (["pending", "created", "authenticated"].includes(normalized)) {
    return "warning";
  }
  return "neutral";
};

const Detail = ({ label, value }: { label: string; value: string }) => (
  <div className="rounded-lg border bg-slate-50 p-3">
    <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
      {label}
    </dt>
    <dd className="mt-1 text-sm font-semibold text-slate-900">{value}</dd>
  </div>
);

const ProfileScopeSubscriptionCard = () => {
  const {
    selectedOrganization,
    selectedProject,
    selectedOrganizationId,
    selectedProjectId,
    loading: tenantLoading,
    error: tenantError,
  } = useTenant();
  const [state, setState] = useState<SubscriptionState>({
    subscription: null,
    plans: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    let active = true;

    if (tenantLoading) return () => {
      active = false;
    };
    if (!selectedOrganizationId) {
      setState({
        subscription: null,
        plans: [],
        loading: false,
        error: tenantError || "No authorised organisation is selected.",
      });
      return () => {
        active = false;
      };
    }

    setState((current) => ({ ...current, loading: true, error: null }));
    void Promise.all([
      // The backend applies the authenticated user's organisation/project
      // visibility. Omitting an organisation query also supports project-only
      // users whose authorised subscription is scoped solely by project.
      listSubscriptions(),
      getPlanCatalog().catch(() => ({
        plans: [] as PlanSettingsPlan[],
        add_ons: [],
        billing_periods: [],
        currency: "",
      })),
    ])
      .then(([subscriptions, catalog]) => {
        if (!active) return;
        setState({
          subscription: resolveActiveSubscription(
            subscriptions,
            selectedOrganizationId,
            selectedProjectId,
          ),
          plans: catalog.plans,
          loading: false,
          error: null,
        });
      })
      .catch(() => {
        if (!active) return;
        setState({
          subscription: null,
          plans: [],
          loading: false,
          error: "Subscription details could not be loaded for this scope.",
        });
      });

    return () => {
      active = false;
    };
  }, [
    selectedOrganizationId,
    selectedProjectId,
    tenantError,
    tenantLoading,
  ]);

  const plan = useMemo(
    () =>
      state.plans.find(
        (candidate) => candidate.code === state.subscription?.plan_code,
      ),
    [state.plans, state.subscription?.plan_code],
  );
  const subscription = state.subscription;
  const isProjectSubscription = Boolean(subscription?.project_id);
  const startDate =
    subscription?.current_period_start ||
    subscription?.starts_at ||
    subscription?.created_at;
  const endDate = subscription?.trial
    ? subscription.trial_ends_at || subscription.current_period_end
    : subscription?.current_period_end || subscription?.ends_at;
  const endDateLabel = subscription?.trial
    ? "Trial expiry date"
    : subscription?.auto_renew
      ? "Renewal date"
      : "Expiry date";
  const subscriptionLevel = plan
    ? `${humanize(plan.family)}${plan.tier != null ? ` · Tier ${plan.tier}` : ""}`
    : humanize(subscription?.plan_code);

  return (
    <Card className="mb-8">
      <CardHeader>
        <CardTitle>Organisation, Project &amp; Subscription</CardTitle>
        <CardDescription>
          Current authorised scope. Subscription information refreshes when the
          selected organisation or project changes.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="flex items-start gap-3 rounded-lg border p-4">
            <Building2 className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Organisation
              </p>
              <p className="mt-1 font-semibold">
                {tenantLoading
                  ? "Loading…"
                  : selectedOrganization?.name || "Not selected"}
              </p>
            </div>
          </div>
          <div className="flex items-start gap-3 rounded-lg border p-4">
            <FolderKanban className="mt-0.5 h-5 w-5 text-primary" />
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Project
              </p>
              <p className="mt-1 font-semibold">
                {tenantLoading
                  ? "Loading…"
                  : selectedProject?.name || "Organisation-wide"}
              </p>
            </div>
          </div>
        </div>

        {state.loading || tenantLoading ? (
          <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            Loading active subscription…
          </div>
        ) : state.error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {state.error}
          </div>
        ) : !subscription ? (
          <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
            No active subscription is available for the selected organisation or
            project.
          </div>
        ) : (
          <section aria-labelledby="active-subscription-heading" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <CreditCard className="h-5 w-5 text-primary" />
                <div>
                  <h2 id="active-subscription-heading" className="font-semibold">
                    Active Subscription
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    {plan?.name || humanize(subscription.plan_code)}
                  </p>
                </div>
              </div>
              <Badge variant={statusVariant(subscription.status)}>
                {humanize(subscription.status)}
              </Badge>
            </div>

            <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Detail
                label="Plan name"
                value={plan?.name || humanize(subscription.plan_code)}
              />
              <Detail label="Subscription level" value={subscriptionLevel} />
              <Detail label="Status" value={humanize(subscription.status)} />
              <Detail label="Start date" value={formatDate(startDate)} />
              <Detail label={endDateLabel} value={formatDate(endDate)} />
              <Detail
                label="Applies at"
                value={
                  isProjectSubscription
                    ? `Project · ${selectedProject?.name || "Selected project"}`
                    : `Organisation · ${selectedOrganization?.name || "Selected organisation"}`
                }
              />
            </dl>

            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <CalendarDays className="h-4 w-4" />
              {isProjectSubscription
                ? "This project has its own subscription."
                : selectedProject
                  ? "The selected project uses the organisation subscription."
                  : "This subscription applies across the organisation."}
            </p>
          </section>
        )}
      </CardContent>
    </Card>
  );
};

export default ProfileScopeSubscriptionCard;
