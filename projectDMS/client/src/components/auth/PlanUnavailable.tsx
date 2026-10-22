import { CreditCard, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getRouteAccessDescriptor } from "@/config/rolePermissions";

type Props = {
  path: string;
  canManageSubscription: boolean;
  unavailableReason?: string | null;
  onRetry?: () => void;
};

export default function PlanUnavailable({
  path,
  canManageSubscription,
  unavailableReason,
  onRetry,
}: Props) {
  const descriptor = getRouteAccessDescriptor(path);
  const serviceUnavailable = unavailableReason === "entitlements_unavailable";

  return (
    <div className="min-h-[60vh] p-6 flex items-center justify-center">
      <Card className="max-w-2xl w-full border-amber-300">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-amber-100 p-2 text-amber-700">
              <CreditCard className="h-5 w-5" />
            </div>
            <div>
              <CardTitle>
                {serviceUnavailable ? "Plan status unavailable" : "Not included in the current plan"}
              </CardTitle>
              <CardDescription>
                {serviceUnavailable
                  ? "We could not safely confirm the subscription for the selected scope. Access remains closed until it can be verified."
                  : "This page needs a feature that is not active for the selected organisation or project."}
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-md bg-muted/60 p-3 text-sm text-muted-foreground">
            <div>
              <span className="font-medium text-foreground">Requested page:</span>{" "}
              {descriptor.normalizedPath}
            </div>
            <div>
              <span className="font-medium text-foreground">Required plan features:</span>{" "}
              {descriptor.requiredAllFeatures.join(", ") || "Commercial service access"}
            </div>
          </div>

          <p className="text-sm text-muted-foreground">
            {canManageSubscription
              ? "Review or upgrade the subscription for this scope to enable the feature."
              : "Ask your organisation administrator to review the subscription for this scope."}
          </p>

          <div className="flex flex-wrap gap-2">
            {canManageSubscription && (
              <Button asChild>
                <Link to="/subscription-management">Review subscription</Link>
              </Button>
            )}
            {onRetry && (
              <Button variant="outline" onClick={onRetry}>
                <RefreshCw className="mr-2 h-4 w-4" /> Retry
              </Button>
            )}
            <Button variant="ghost" asChild>
              <Link to="/overview">Go to Overview</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
