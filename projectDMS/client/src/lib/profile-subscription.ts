import type { Subscription } from "@/services/plan-settings-api";

export const ACTIVE_SUBSCRIPTION_STATUSES = new Set(["active", "trial", "pilot"]);

const newestFirst = (left: Subscription, right: Subscription) => {
  const timestamp = (subscription: Subscription) =>
    Date.parse(
      subscription.updated_at ||
        subscription.current_period_start ||
        subscription.starts_at ||
        subscription.created_at ||
        "",
    ) || 0;
  return timestamp(right) - timestamp(left);
};

export function resolveActiveSubscription(
  subscriptions: Subscription[],
  organizationId: string,
  projectId: string,
): Subscription | null {
  const activeForOrganization = subscriptions.filter(
    (subscription) =>
      String(subscription.organization_id) === String(organizationId) &&
      ACTIVE_SUBSCRIPTION_STATUSES.has(
        String(subscription.status).toLowerCase(),
      ),
  );

  if (projectId) {
    const projectSubscription = activeForOrganization
      .filter(
        (subscription) =>
          String(subscription.project_id || "") === String(projectId),
      )
      .sort(newestFirst)[0];
    if (projectSubscription) return projectSubscription;
  }

  return (
    activeForOrganization
      .filter((subscription) => !subscription.project_id)
      .sort(newestFirst)[0] || null
  );
}
