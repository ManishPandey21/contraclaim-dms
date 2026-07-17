import React, { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AlertCircle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  acceptSecurityTerms,
  getSecurityTermsStatus,
  SecurityTermsStatusDTO,
} from "@/services/security-terms-api";

const formatDate = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
};

const SecurityTermsPage: React.FC = () => {
  const [status, setStatus] = useState<SecurityTermsStatusDTO | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const from = typeof location.state?.from === "string" ? location.state.from : "/overview";

  useEffect(() => {
    let active = true;
    setLoading(true);
    getSecurityTermsStatus()
      .then((data) => {
        if (!active) return;
        setStatus(data);
        setError(null);
        if (!data.requires_acceptance) {
          window.dispatchEvent(new Event("security-terms-accepted"));
        }
      })
      .catch((err: any) => {
        if (!active) return;
        setError(err?.response?.data?.detail || "Unable to load security terms");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const onAccept = async () => {
    if (!accepted) {
      toast.error("Accept the terms to continue");
      return;
    }
    setSaving(true);
    try {
      await acceptSecurityTerms();
      window.dispatchEvent(new Event("security-terms-accepted"));
      toast.success("Security terms accepted");
      navigate(from === "/security-terms" ? "/overview" : from, { replace: true });
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to accept security terms");
    } finally {
      setSaving(false);
    }
  };

  const active = status?.active_version;
  const alreadyAccepted = Boolean(status && !status.requires_acceptance);

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
      <Card className="w-full max-w-5xl shadow-lg">
        <CardHeader className="border-b">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div className="flex gap-3">
              <div className="rounded-lg bg-blue-100 p-2 text-blue-700">
                <ShieldCheck className="h-6 w-6" />
              </div>
              <div>
                <h1 className="text-2xl font-semibold leading-none tracking-tight">
                  Security, Privacy &amp; Anti-Piracy Terms
                </h1>
                <CardDescription>
                  Review and accept the active terms before accessing Contraclaim DMS.
                </CardDescription>
              </div>
            </div>
            {active && (
              <div className="flex flex-wrap gap-2 md:justify-end">
                <Badge variant="outline">Version {active.version}</Badge>
                <Badge variant="outline">Effective {formatDate(active.effective_date)}</Badge>
              </div>
            )}
          </div>
        </CardHeader>

        <CardContent className="space-y-4 pt-6">
          {loading && (
            <div className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading terms
            </div>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          {!loading && active && (
            <>
              {alreadyAccepted && (
                <Alert>
                  <CheckCircle2 className="h-4 w-4" />
                  <AlertDescription>
                    You have already accepted this active terms version.
                  </AlertDescription>
                </Alert>
              )}

              <section
                className="max-h-[48vh] overflow-y-auto rounded-md border bg-white p-4 text-sm leading-6 text-slate-800"
                aria-label="Scrollable security terms"
              >
                <h2 className="mb-3 text-base font-semibold">{active.title}</h2>
                <pre className="whitespace-pre-wrap break-words font-sans">{active.body}</pre>
              </section>

              {!alreadyAccepted && (
                <div className="flex items-start gap-3 rounded-md border bg-slate-50 p-4">
                  <Checkbox
                    id="security-terms-accept"
                    checked={accepted}
                    onCheckedChange={(value) => setAccepted(value === true)}
                  />
                  <Label htmlFor="security-terms-accept" className="text-sm leading-5">
                    I have read and agree to the active Security, Privacy & Anti-Piracy Terms.
                  </Label>
                </div>
              )}
            </>
          )}
        </CardContent>

        <CardFooter className="justify-end gap-2 border-t pt-4">
          {alreadyAccepted ? (
            <Button onClick={() => navigate(from === "/security-terms" ? "/overview" : from, { replace: true })}>
              Continue
            </Button>
          ) : (
            <Button onClick={onAccept} disabled={!accepted || saving || loading || Boolean(error)}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Accept and Continue
            </Button>
          )}
        </CardFooter>
      </Card>
    </main>
  );
};

export default SecurityTermsPage;
