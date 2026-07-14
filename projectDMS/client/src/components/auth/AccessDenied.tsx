import { Link } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getRouteAccessDescriptor } from "@/config/rolePermissions";

type AccessDeniedProps = {
  path: string;
  fallback?: string;
};

const AccessDenied = ({ path, fallback = "/overview" }: AccessDeniedProps) => {
  const descriptor = getRouteAccessDescriptor(path);
  const required = descriptor.requiredAnyPermissions;

  const description = descriptor.isMapped
    ? "Your role does not include a permission required for this page."
    : "This page has no frontend access metadata, so it is blocked by default.";

  return (
    <div className="min-h-[60vh] p-6 flex items-center justify-center">
      <Card className="max-w-2xl w-full border-destructive/30">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-destructive/10 p-2 text-destructive">
              <ShieldAlert className="h-5 w-5" />
            </div>
            <div>
              <CardTitle>Access unavailable</CardTitle>
              <CardDescription>{description}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-md bg-muted/60 p-3 text-sm text-muted-foreground">
            <div>
              <span className="font-medium text-foreground">Requested page:</span>{" "}
              {descriptor.normalizedPath}
            </div>
            {descriptor.matchedPath && descriptor.matchedPath !== descriptor.normalizedPath && (
              <div>
                <span className="font-medium text-foreground">Matched rule:</span>{" "}
                {descriptor.matchedPath}
              </div>
            )}
          </div>

          {required.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium">One of these permissions is required:</p>
              <div className="flex flex-wrap gap-2">
                {required.map((permission) => (
                  <Badge key={permission} variant="outline">
                    {permission}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          <p className="text-sm text-muted-foreground">
            If this access should be available, ask an administrator to update your
            role or subscription entitlement. Direct API calls are still checked by
            the backend policy service.
          </p>

          <Button asChild>
            <Link to={fallback}>Go to Overview</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
};

export default AccessDenied;
