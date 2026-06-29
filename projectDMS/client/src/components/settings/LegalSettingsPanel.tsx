import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Gavel, Loader2, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  activateSecurityTermsVersion,
  createSecurityTermsVersion,
  getMySecurityTermsAcceptances,
  getSecurityTermsStatus,
  listSecurityTermsVersions,
  SecurityTermsAcceptanceDTO,
  SecurityTermsStatusDTO,
  SecurityTermsVersionDTO,
} from "@/services/security-terms-api";
import useRBAC from "@/hooks/useRBAC";

const fmt = (value?: string | null) => {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const today = () => new Date().toISOString().slice(0, 10);

const LegalSettingsPanel: React.FC = () => {
  const { roles, can } = useRBAC();
  const canManage = roles.includes("superadmin") || can("settings:edit");
  const [status, setStatus] = useState<SecurityTermsStatusDTO | null>(null);
  const [acceptances, setAcceptances] = useState<SecurityTermsAcceptanceDTO[]>([]);
  const [versions, setVersions] = useState<SecurityTermsVersionDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    version: "",
    title: "Security, Privacy & Anti-Piracy Terms",
    effective_date: today(),
    body: "",
    is_active: true,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [statusRes, acceptedRes] = await Promise.all([
        getSecurityTermsStatus(),
        getMySecurityTermsAcceptances(),
      ]);
      setStatus(statusRes);
      setAcceptances(acceptedRes);
      if (canManage) {
        setVersions(await listSecurityTermsVersions());
      }
      setError(null);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to load legal settings");
    } finally {
      setLoading(false);
    }
  }, [canManage]);

  useEffect(() => {
    void load();
  }, [load]);

  const createVersion = async () => {
    if (!form.version.trim() || !form.body.trim() || !form.effective_date) {
      toast.error("Version, effective date, and terms body are required");
      return;
    }
    setSaving(true);
    try {
      await createSecurityTermsVersion({
        version: form.version.trim(),
        title: form.title.trim() || "Security, Privacy & Anti-Piracy Terms",
        body: form.body,
        effective_date: new Date(form.effective_date).toISOString(),
        is_active: form.is_active,
      });
      toast.success(form.is_active ? "Terms version created and activated" : "Terms version created");
      setForm({
        version: "",
        title: "Security, Privacy & Anti-Piracy Terms",
        effective_date: today(),
        body: "",
        is_active: true,
      });
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to create terms version");
    } finally {
      setSaving(false);
    }
  };

  const activateVersion = async (versionId?: string) => {
    if (!versionId) return;
    setSaving(true);
    try {
      await activateSecurityTermsVersion(versionId);
      toast.success("Terms version activated");
      await load();
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Failed to activate terms version");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12 text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Loading legal settings
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-blue-600" />
            Active Terms
          </CardTitle>
          <CardDescription>Current security terms version and your acceptance status.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {status?.active_version && (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">Version {status.active_version.version}</Badge>
                <Badge variant="outline">Effective {fmt(status.active_version.effective_date)}</Badge>
                <Badge className={status.requires_acceptance ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}>
                  {status.requires_acceptance ? "Acceptance pending" : "Accepted"}
                </Badge>
              </div>
              <div className="max-h-64 overflow-y-auto rounded-md border bg-white p-4 text-sm leading-6">
                <h3 className="mb-2 font-semibold">{status.active_version.title}</h3>
                <pre className="whitespace-pre-wrap break-words font-sans">{status.active_version.body}</pre>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-green-600" />
            Accepted Terms
          </CardTitle>
          <CardDescription>Acceptance records stored for your account and organization.</CardDescription>
        </CardHeader>
        <CardContent>
          {acceptances.length === 0 ? (
            <p className="text-sm text-muted-foreground">No accepted terms records found.</p>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left">
                  <tr>
                    <th className="px-3 py-2">Version</th>
                    <th className="px-3 py-2">Accepted At</th>
                    <th className="px-3 py-2">IP Address</th>
                    <th className="px-3 py-2">Method</th>
                    <th className="px-3 py-2">Hash</th>
                  </tr>
                </thead>
                <tbody>
                  {acceptances.map((item) => (
                    <tr key={item._id || item.id || `${item.terms_version}-${item.terms_hash}`} className="border-t">
                      <td className="px-3 py-2">{item.terms_version}</td>
                      <td className="px-3 py-2">{fmt(item.accepted_at)}</td>
                      <td className="px-3 py-2">{item.ip_address || "-"}</td>
                      <td className="px-3 py-2">{item.acceptance_method}</td>
                      <td className="max-w-[220px] truncate px-3 py-2 font-mono text-xs" title={item.terms_hash}>
                        {item.terms_hash}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Gavel className="h-5 w-5 text-purple-600" />
              Terms Version Management
            </CardTitle>
            <CardDescription>
              Creating or activating a new version requires all users to accept it before dashboard access.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid gap-4 md:grid-cols-3">
              <div>
                <Label htmlFor="terms-version">Version</Label>
                <Input
                  id="terms-version"
                  value={form.version}
                  onChange={(event) => setForm((prev) => ({ ...prev, version: event.target.value }))}
                  placeholder="2026.2"
                />
              </div>
              <div>
                <Label htmlFor="terms-title">Title</Label>
                <Input
                  id="terms-title"
                  value={form.title}
                  onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="terms-effective-date">Effective Date</Label>
                <Input
                  id="terms-effective-date"
                  type="date"
                  value={form.effective_date}
                  onChange={(event) => setForm((prev) => ({ ...prev, effective_date: event.target.value }))}
                />
              </div>
            </div>
            <div>
              <Label htmlFor="terms-body">Terms Body</Label>
              <Textarea
                id="terms-body"
                value={form.body}
                onChange={(event) => setForm((prev) => ({ ...prev, body: event.target.value }))}
                rows={10}
                className="font-mono text-sm"
                placeholder="Paste the full Security, Privacy & Anti-Piracy Terms for this version."
              />
            </div>
            <div className="flex items-center gap-3">
              <Checkbox
                id="terms-active"
                checked={form.is_active}
                onCheckedChange={(value) => setForm((prev) => ({ ...prev, is_active: value === true }))}
              />
              <Label htmlFor="terms-active">Activate immediately after save</Label>
            </div>
            <Button onClick={createVersion} disabled={saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save Terms Version
            </Button>

            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left">
                  <tr>
                    <th className="px-3 py-2">Version</th>
                    <th className="px-3 py-2">Effective</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Hash</th>
                    <th className="px-3 py-2 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((version) => (
                    <tr key={version._id || version.id || version.version} className="border-t">
                      <td className="px-3 py-2">{version.version}</td>
                      <td className="px-3 py-2">{fmt(version.effective_date)}</td>
                      <td className="px-3 py-2">
                        {version.is_active ? <Badge>Active</Badge> : <Badge variant="outline">Inactive</Badge>}
                      </td>
                      <td className="max-w-[240px] truncate px-3 py-2 font-mono text-xs" title={version.terms_hash}>
                        {version.terms_hash}
                      </td>
                      <td className="px-3 py-2 text-right">
                        {!version.is_active && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => activateVersion(version._id || version.id)}
                            disabled={saving}
                          >
                            Activate
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default LegalSettingsPanel;
