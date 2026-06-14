import React, { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Loader2, Mail, PlugZap, Save } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { listOrganizations, Organization } from "@/services/organizations-api";
import { listProjects, Project } from "@/services/projects-api";
import {
  getOrganizationSmtpSettings,
  getProjectSmtpSettings,
  saveOrganizationSmtpSettings,
  saveProjectSmtpSettings,
  SmtpEncryption,
  SmtpSettings,
  testOrganizationSmtpSettings,
  testProjectSmtpSettings,
} from "@/services/smtp-settings-api";

type Scope = "organization" | "project";

interface SmtpSettingsPanelProps {
  scope: Scope;
}

interface ProjectOption {
  id: string;
  name: string;
  organizationId: string;
}

interface FormState {
  host: string;
  port: string;
  username: string;
  password: string;
  sender_email: string;
  sender_name: string;
  encryption: SmtpEncryption;
  is_active: boolean;
}

const emptyForm: FormState = {
  host: "",
  port: "587",
  username: "",
  password: "",
  sender_email: "",
  sender_name: "",
  encryption: "starttls",
  is_active: true,
};

function errorMessage(error: any, fallback: string) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  return error?.message || fallback;
}

function isEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

const SmtpSettingsPanel = ({ scope }: SmtpSettingsPanelProps) => {
  const { toast } = useToast();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState(() => window.localStorage.getItem("org_id") || "");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [settings, setSettings] = useState<SmtpSettings | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const selectedProject = projects.find((project) => project.id === selectedProjectId);
  const selectedOrg = organizations.find((org) => org._id === selectedOrgId);
  const selectedProjectOrg = organizations.find((org) => org._id === selectedProject?.organizationId);
  const hasExisting = Boolean(settings?._id);

  const title = scope === "organization" ? "Organization SMTP Settings" : "Project SMTP Settings";
  const description =
    scope === "organization"
      ? "Configure the default sender and SMTP server for document-sharing emails in an organization."
      : "Configure a project-specific sender and SMTP server. Active project settings override organization settings.";

  const canLoad = scope === "organization" ? Boolean(selectedOrgId) : Boolean(selectedProjectId);

  const updateForm = (field: keyof FormState, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const applySettings = (value: SmtpSettings | null) => {
    setSettings(value);
    if (!value) {
      setForm(emptyForm);
      return;
    }
    setForm({
      host: value.host || "",
      port: String(value.port || 587),
      username: value.username || "",
      password: "",
      sender_email: value.sender_email || "",
      sender_name: value.sender_name || "",
      encryption: value.encryption || "starttls",
      is_active: Boolean(value.is_active),
    });
  };

  const loadSettings = async () => {
    if (!canLoad) return;
    setLoading(true);
    try {
      const data =
        scope === "organization"
          ? await getOrganizationSmtpSettings(selectedOrgId)
          : await getProjectSmtpSettings(selectedProjectId);
      applySettings(data);
    } catch (error: any) {
      if (error?.response?.status === 404) {
        applySettings(null);
        return;
      }
      toast({
        title: "Failed to load SMTP settings",
        description: errorMessage(error, "Unable to fetch SMTP settings."),
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const orgs = await listOrganizations();
        setOrganizations(orgs);
        const firstOrg = selectedOrgId || orgs[0]?._id || "";
        if (firstOrg && !selectedOrgId) {
          setSelectedOrgId(firstOrg);
          window.localStorage.setItem("org_id", firstOrg);
        }
        const projs = await listProjects(firstOrg ? { organization_id: firstOrg } : undefined);
        const normalized = projs.map((project: Project) => ({
          id: project._id,
          name: project.name,
          organizationId: project.organization_id,
        }));
        setProjects(normalized);
        setSelectedProjectId(normalized[0]?.id || "");
      } catch (error: any) {
        toast({
          title: "Failed to load scopes",
          description: errorMessage(error, "Unable to load organizations or projects."),
          variant: "destructive",
        });
      }
    };
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, selectedOrgId, selectedProjectId]);

  const validationError = useMemo(() => {
    if (!form.host.trim()) return "SMTP host is required.";
    const port = Number(form.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return "SMTP port must be between 1 and 65535.";
    if (!form.username.trim()) return "SMTP username is required.";
    if (!hasExisting && !form.password.trim()) return "SMTP password is required.";
    if (!isEmail(form.sender_email)) return "Enter a valid sender email address.";
    return "";
  }, [form, hasExisting]);

  const handleOrgChange = async (orgId: string) => {
    setSelectedOrgId(orgId);
    window.localStorage.setItem("org_id", orgId);
    if (scope === "project") {
      const projs = await listProjects({ organization_id: orgId });
      const normalized = projs.map((project: Project) => ({
        id: project._id,
        name: project.name,
        organizationId: project.organization_id,
      }));
      setProjects(normalized);
      setSelectedProjectId(normalized[0]?.id || "");
    }
  };

  const handleSave = async () => {
    if (validationError) {
      toast({ title: "Check SMTP settings", description: validationError, variant: "destructive" });
      return;
    }
    setSaving(true);
    try {
      const payload = {
        host: form.host.trim(),
        port: Number(form.port),
        username: form.username.trim(),
        password: form.password.trim() || undefined,
        sender_email: form.sender_email.trim(),
        sender_name: form.sender_name.trim() || undefined,
        encryption: form.encryption,
        is_active: form.is_active,
      };
      const saved =
        scope === "organization"
          ? await saveOrganizationSmtpSettings(selectedOrgId, payload, hasExisting)
          : await saveProjectSmtpSettings(selectedProjectId, payload, hasExisting);
      applySettings(saved);
      toast({ title: "SMTP settings saved", description: "Document-sharing emails will use the updated fallback priority." });
    } catch (error: any) {
      toast({
        title: "Failed to save SMTP settings",
        description: errorMessage(error, "Please check the values and try again."),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!hasExisting) {
      toast({ title: "Save settings first", description: "SMTP connection tests use the saved encrypted credentials.", variant: "destructive" });
      return;
    }
    setTesting(true);
    try {
      const result =
        scope === "organization"
          ? await testOrganizationSmtpSettings(selectedOrgId)
          : await testProjectSmtpSettings(selectedProjectId);
      toast({
        title: result.ok ? "SMTP test passed" : "SMTP test failed",
        description: result.message,
        variant: result.ok ? "default" : "destructive",
      });
    } catch (error: any) {
      toast({
        title: "SMTP test failed",
        description: errorMessage(error, "Unable to test SMTP connection."),
        variant: "destructive",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{scope === "organization" ? "Select Organization" : "Select Project"}</CardTitle>
          <CardDescription>
            {scope === "organization" ? "Choose the organization SMTP profile to edit." : "Choose the project SMTP profile to edit."}
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Organization</Label>
            <Select value={selectedOrgId} onValueChange={handleOrgChange}>
              <SelectTrigger>
                <SelectValue placeholder="Select organization" />
              </SelectTrigger>
              <SelectContent>
                {organizations.map((org) => (
                  <SelectItem key={org._id} value={org._id}>
                    {org.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {scope === "project" && (
            <div className="space-y-2">
              <Label>Project</Label>
              <Select value={selectedProjectId} onValueChange={setSelectedProjectId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select project" />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>
                      {project.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Mail className="h-5 w-5 text-primary" />
              </div>
              <div>
                <CardTitle>{title}</CardTitle>
                <CardDescription>{description}</CardDescription>
              </div>
            </div>
            <Badge variant={form.is_active ? "neutral" : "outline"}>{form.is_active ? "Active" : "Inactive"}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="text-sm text-muted-foreground">
            {scope === "organization"
              ? selectedOrg?.name || "No organization selected"
              : `${selectedProject?.name || "No project selected"}${selectedProjectOrg?.name ? ` - ${selectedProjectOrg.name}` : ""}`}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor={`${scope}-smtp-host`}>SMTP Host</Label>
              <Input id={`${scope}-smtp-host`} value={form.host} onChange={(event) => updateForm("host", event.target.value)} placeholder="smtp.example.com" disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${scope}-smtp-port`}>SMTP Port</Label>
              <Input id={`${scope}-smtp-port`} type="number" value={form.port} onChange={(event) => updateForm("port", event.target.value)} placeholder="587" disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${scope}-smtp-username`}>Username</Label>
              <Input id={`${scope}-smtp-username`} value={form.username} onChange={(event) => updateForm("username", event.target.value)} placeholder="mailer@example.com" disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${scope}-smtp-password`}>Password</Label>
              <Input id={`${scope}-smtp-password`} type="password" value={form.password} onChange={(event) => updateForm("password", event.target.value)} placeholder={settings?.password_configured ? "Leave blank to keep current password" : "SMTP password"} disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${scope}-sender-email`}>Sender Email</Label>
              <Input id={`${scope}-sender-email`} value={form.sender_email} onChange={(event) => updateForm("sender_email", event.target.value)} placeholder="noreply@example.com" disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`${scope}-sender-name`}>Sender Name</Label>
              <Input id={`${scope}-sender-name`} value={form.sender_name} onChange={(event) => updateForm("sender_name", event.target.value)} placeholder="ContraClaim DMS" disabled={!canLoad || loading} />
            </div>
            <div className="space-y-2">
              <Label>Encryption</Label>
              <Select value={form.encryption} onValueChange={(value) => updateForm("encryption", value as SmtpEncryption)} disabled={!canLoad || loading}>
                <SelectTrigger>
                  <SelectValue placeholder="Select encryption" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="starttls">STARTTLS</SelectItem>
                  <SelectItem value="ssl_tls">SSL/TLS</SelectItem>
                  <SelectItem value="none">None</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div>
                <Label htmlFor={`${scope}-smtp-active`}>Active</Label>
                <p className="text-xs text-muted-foreground">Inactive settings are skipped during SMTP fallback.</p>
              </div>
              <Switch id={`${scope}-smtp-active`} checked={form.is_active} onCheckedChange={(value) => updateForm("is_active", value)} disabled={!canLoad || loading} />
            </div>
          </div>

          {validationError && <p className="text-sm text-destructive">{validationError}</p>}

          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={handleTest} disabled={testing || loading || !hasExisting}>
              {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
              Test
            </Button>
            <Button onClick={handleSave} disabled={saving || loading || !canLoad || Boolean(validationError)}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default SmtpSettingsPanel;
