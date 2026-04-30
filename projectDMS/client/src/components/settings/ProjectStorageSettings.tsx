import React, { useEffect, useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  FolderClosed,
  HardDrive,
  Cloud,
  Server,
  Save,
  Check,
  RefreshCw,
  Lock,
  FolderInput,
  FolderOutput,
  FileText,
  Loader2,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import {
  getProjectStorageSettings,
  resolveStorageSettings,
  updateProjectStorageSettings,
  StorageProviderConfig,
  StorageProviderId,
  StorageBasePaths,
} from "@/services/storage-settings-api";
import { listOrganizations, Organization } from "@/services/organizations-api";
import { listProjects, Project } from "@/services/projects-api";

interface ProviderOption {
  id: StorageProviderId;
  name: string;
  description: string;
  icon: React.ReactNode;
  enabled: boolean;
}

interface ProjectOption {
  id: string;
  name: string;
  organizationId: string;
}

const PROVIDER_OPTIONS: ProviderOption[] = [
  {
    id: "local",
    name: "Local Disk",
    description: "Store files on the local server disk",
    icon: <HardDrive className="h-4 w-4" />,
    enabled: true,
  },
  {
    id: "s3",
    name: "Amazon S3",
    description: "Store files in Amazon S3 bucket",
    icon: <Cloud className="h-4 w-4" />,
    enabled: true,
  },
  {
    id: "azure",
    name: "Azure Blob Storage",
    description: "Store files in Azure Blob Storage",
    icon: <Cloud className="h-4 w-4" />,
    enabled: false,
  },
  {
    id: "gcs",
    name: "Google Cloud Storage",
    description: "Store files in Google Cloud Storage",
    icon: <Cloud className="h-4 w-4" />,
    enabled: false,
  },
  {
    id: "custom",
    name: "Custom Server",
    description: "Store files on a custom server",
    icon: <Server className="h-4 w-4" />,
    enabled: false,
  },
];

const ProjectStorageSettings = () => {
  const { toast } = useToast();
  const [orgId] = useState<string | null>(() =>
    window.localStorage.getItem("org_id")
  );
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [selectedProject, setSelectedProject] = useState<string>("");
  const [inheritFromOrg, setInheritFromOrg] = useState(true);
  const [isSuperAdmin] = useState(true); // This would come from auth context
  const [primaryProvider, setPrimaryProvider] = useState("local");
  const [providers, setProviders] = useState<StorageProviderConfig[]>(() =>
    PROVIDER_OPTIONS.map((opt) => ({
      id: opt.id,
      enabled: opt.enabled,
      primary: opt.id === "local",
      bucket: undefined,
      prefix: undefined,
      region: undefined,
      endpoint: undefined,
      extra: {},
    }))
  );
  const [resolvedPaths, setResolvedPaths] = useState<StorageBasePaths>({
    incoming: "/ORG/PROJ/incoming",
    outgoing: "/ORG/PROJ/outgoing",
    contracts: "/ORG/PROJ/contracts",
  });
  const [projectShortName, setProjectShortName] = useState("");
  const [isShortNameSet, setIsShortNameSet] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const selectedLocations = useMemo(
    () => providers.filter((p) => p.enabled).map((p) => p.id),
    [providers]
  );

  const mergeProviders = (apiProviders: StorageProviderConfig[]) => {
    const map = new Map<StorageProviderId, StorageProviderConfig>();
    apiProviders.forEach((p) => map.set(p.id, { ...p }));
    const merged = PROVIDER_OPTIONS.map<StorageProviderConfig>((opt) => {
      const existing = map.get(opt.id);
      return {
        id: opt.id,
        enabled: existing ? existing.enabled : opt.enabled,
        primary: existing ? existing.primary : opt.id === "local",
        bucket: existing?.bucket,
        prefix: existing?.prefix,
        region: existing?.region,
        endpoint: existing?.endpoint,
        extra: existing?.extra ?? {},
      };
    });
    if (!merged.some((p) => p.enabled)) {
      const local = merged.find((p) => p.id === "local");
      if (local) local.enabled = true;
    }
    const primary = merged.find((p) => p.primary && p.enabled);
    if (!primary) {
      const fallback = merged.find((p) => p.enabled);
      if (fallback) merged.forEach((p) => (p.primary = p.id === fallback.id));
    } else {
      merged.forEach((p) => (p.primary = p.id === primary.id));
    }
    setProviders(merged);
  };

  const handleLocationToggle = (locationId: string) => {
    setProviders((prev) => {
      const next = prev.map((p) =>
        p.id === locationId ? { ...p, enabled: !p.enabled } : { ...p }
      );
      const enabledCount = next.filter((p) => p.enabled).length;
      if (enabledCount === 0) {
        toast({
          title: "At least one location required",
          description: "You must have at least one storage location selected.",
          variant: "destructive",
        });
        return prev;
      }
      if (!next.some((p) => p.primary && p.enabled)) {
        const fallback = next.find((p) => p.enabled);
        if (fallback) {
          next.forEach((p) => (p.primary = p.id === fallback.id));
        }
      }
      return next;
    });
  };

  const handleShortNameChange = (value: string) => {
    const sanitized = value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    setProjectShortName(sanitized);
  };

  const handlePrimaryChange = (value: string) => {
    setPrimaryProvider(value);
    setProviders((prev) =>
      prev.map((p) => ({
        ...p,
        primary: p.id === value,
      }))
    );
  };

  const loadProjectSettings = async (
    projectId: string,
    projectOrgId?: string
  ) => {
    if (!projectId) return;
    setLoading(true);
    try {
      const targetOrgId =
        projectOrgId ||
        projects.find((p) => p.id === projectId)?.organizationId ||
        orgId ||
        "";
      const [settings, resolved] = await Promise.all([
        getProjectStorageSettings(projectId, targetOrgId),
        resolveStorageSettings({ org_id: targetOrgId, project_id: projectId }),
      ]);

      setInheritFromOrg(Boolean(settings.inherit_from_org));
      setProjectShortName(settings.project_short_name || "");
      setIsShortNameSet(Boolean(settings.project_short_name));
      mergeProviders(settings.providers || resolved.providers || []);
      setResolvedPaths(resolved.base_paths);
      const primary = (settings.providers || resolved.providers || []).find(
        (p) => p.primary
      );
      if (primary?.id) setPrimaryProvider(primary.id);
    } catch (error: any) {
      toast({
        title: "Failed to load storage settings",
        description:
          error?.message || "Unable to fetch project storage settings.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const [orgs, projs] = await Promise.all([
          listOrganizations().catch(() => []),
          listProjects(orgId ? { organization_id: orgId } : undefined).catch(
            () => []
          ),
        ]);
        setOrganizations(orgs);
        const normalizedProjects: ProjectOption[] = projs.map((p: Project) => ({
          id: p._id,
          name: p.name,
          organizationId: p.organization_id,
        }));
        setProjects(normalizedProjects);
        const initialProject = normalizedProjects[0]?.id || "";
        setSelectedProject(initialProject);
        if (initialProject) {
          await loadProjectSettings(
            initialProject,
            normalizedProjects[0]?.organizationId
          );
        }
      } catch (error: any) {
        toast({
          title: "Failed to load projects",
          description:
            error?.message || "Unable to load projects or organizations.",
          variant: "destructive",
        });
      }
    };
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId]);

  const handleSave = async () => {
    if (!selectedProject) {
      toast({
        title: "No project selected",
        description: "Please select a project first.",
        variant: "destructive",
      });
      return;
    }
    const projectOrgId =
      projects.find((p) => p.id === selectedProject)?.organizationId ||
      orgId ||
      organizations[0]?._id ||
      "";
    setSaving(true);
    try {
      const ensuredProviders =
        inheritFromOrg || providers.some((p) => p.enabled)
          ? providers
          : providers.map((p) =>
              p.id === "local"
                ? { ...p, enabled: true, primary: true }
                : { ...p, primary: false }
            );
      const payload = {
        org_id: projectOrgId,
        inherit_from_org: inheritFromOrg,
        project_short_name: projectShortName || undefined,
        providers: inheritFromOrg ? undefined : ensuredProviders,
        base_paths: inheritFromOrg ? undefined : resolvedPaths,
      };
      const saved = await updateProjectStorageSettings(
        selectedProject,
        projectOrgId,
        payload
      );
      setIsShortNameSet(Boolean(saved.project_short_name));
      if (saved.providers) mergeProviders(saved.providers);
      const resolved = await resolveStorageSettings({
        org_id: projectOrgId,
        project_id: selectedProject,
      });
      setResolvedPaths(resolved.base_paths);
      toast({
        title: "Settings saved",
        description: `Storage settings for ${
          projects.find((p) => p.id === selectedProject)?.name || "project"
        } have been updated.`,
      });
    } catch (error: any) {
      toast({
        title: "Failed to save settings",
        description: error?.message || "Please try again.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const currentProject = projects.find((p) => p.id === selectedProject);
  const currentOrganization = organizations.find(
    (o) => o._id === currentProject?.organizationId
  );
  const canEditShortName = currentProject && (!isShortNameSet || isSuperAdmin);
  const primaryCandidate =
    providers.find((p) => p.primary)?.id || primaryProvider;
  const incomingPath = resolvedPaths.incoming;
  const outgoingPath = resolvedPaths.outgoing;
  const contractsPath = resolvedPaths.contracts;

  return (
    <div className="space-y-6">
      {/* Project Selector */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-lg">
              <FolderClosed className="h-5 w-5 text-primary" />
            </div>
            <div>
              <CardTitle>Project Storage Settings</CardTitle>
              <CardDescription>
                Configure storage settings for individual projects
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-3">
            <Label className="text-sm font-medium">Select Project</Label>
            <Select
              value={selectedProject}
              onValueChange={(value) => {
                setSelectedProject(value);
                setInheritFromOrg(true);
                const proj = projects.find((p) => p.id === value);
                loadProjectSettings(value, proj?.organizationId);
              }}
            >
              <SelectTrigger className="max-w-md">
                <SelectValue placeholder="Select a project" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((project) => {
                  const org = organizations.find(
                    (o) => o._id === project.organizationId
                  );
                  return (
                    <SelectItem key={project.id} value={project.id}>
                      <div className="flex flex-col">
                        <span>{project.name}</span>
                        <span className="text-xs text-muted-foreground">
                          {org?.name || "—"}
                        </span>
                      </div>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
            {!projects.length && (
              <p className="text-xs text-muted-foreground">
                No projects found. Please create a project first.
              </p>
            )}
          </div>

          {currentProject && currentOrganization && (
            <div className="p-4 bg-muted/50 rounded-lg">
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium">{currentProject.name}</p>
                  <p className="text-sm text-muted-foreground">
                    Organization: {currentOrganization.name}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {projectShortName && (
                    <Badge variant="neutral">{projectShortName}</Badge>
                  )}
                  <Badge variant="outline">Selected</Badge>
                </div>
              </div>
            </div>
          )}

          {/* Project Short Name */}
          {currentProject && (
            <div className="space-y-3 pt-4 border-t">
              <Label
                htmlFor="project-short-name"
                className="text-sm font-medium flex items-center gap-2"
              >
                <FolderClosed className="h-4 w-4 text-muted-foreground" />
                Project Short Name
                {isShortNameSet && !isSuperAdmin && (
                  <Lock className="h-3 w-3 text-muted-foreground" />
                )}
              </Label>
              <div className="flex items-center gap-3">
                <Input
                  id="project-short-name"
                  value={projectShortName}
                  onChange={(e) => handleShortNameChange(e.target.value)}
                  placeholder="PROJ"
                  className="max-w-[200px] uppercase"
                  disabled={!canEditShortName}
                  maxLength={10}
                />
                {isShortNameSet && (
                  <Badge
                    variant={isSuperAdmin ? "neutral" : "outline"}
                    className="flex items-center gap-1"
                  >
                    <Lock className="h-3 w-3" />
                    {isSuperAdmin ? "Super Admin" : "Locked"}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                A short identifier for this project (max 10 characters).
                {isShortNameSet &&
                  " Once set, only Super Admins can modify this."}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Inherit Settings Toggle */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Inheritance Settings</CardTitle>
              <CardDescription>
                Choose whether to inherit storage settings from the organization
              </CardDescription>
            </div>
            <div className="flex items-center gap-3">
              <Label htmlFor="inherit-toggle" className="text-sm">
                Inherit from Organization
              </Label>
              <Switch
                id="inherit-toggle"
                checked={inheritFromOrg}
                onCheckedChange={setInheritFromOrg}
              />
            </div>
          </div>
        </CardHeader>
        {inheritFromOrg && (
          <CardContent>
            <div className="flex items-center gap-2 p-4 bg-primary/5 border border-primary/20 rounded-lg">
              <RefreshCw className="h-4 w-4 text-primary" />
              <p className="text-sm text-muted-foreground">
                This project is using the organization's default storage
                settings. Toggle off to configure custom settings.
              </p>
            </div>
          </CardContent>
        )}
      </Card>

      {/* Custom Project Settings */}
      {!inheritFromOrg && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Document Folder Paths</CardTitle>
              <CardDescription>
                Set custom folder paths for different document types
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Auto-generated Folder Paths Display */}
              <div className="space-y-4">
                <div className="p-3 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <FolderInput className="h-4 w-4 text-green-600" />
                    <Label className="text-sm font-medium">
                      Incoming Files
                    </Label>
                  </div>
                  <code className="text-sm bg-background px-2 py-1 rounded border block">
                    {incomingPath}
                  </code>
                  <p className="text-xs text-muted-foreground mt-1">
                    Default folder for incoming correspondence and documents
                  </p>
                </div>

                <div className="p-3 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <FolderOutput className="h-4 w-4 text-blue-600" />
                    <Label className="text-sm font-medium">
                      Outgoing Files
                    </Label>
                  </div>
                  <code className="text-sm bg-background px-2 py-1 rounded border block">
                    {outgoingPath}
                  </code>
                  <p className="text-xs text-muted-foreground mt-1">
                    Default folder for outgoing correspondence and documents
                  </p>
                </div>

                <div className="p-3 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <FileText className="h-4 w-4 text-amber-600" />
                    <Label className="text-sm font-medium">
                      Contract Documents
                    </Label>
                  </div>
                  <code className="text-sm bg-background px-2 py-1 rounded border block">
                    {contractsPath}
                  </code>
                  <p className="text-xs text-muted-foreground mt-1">
                    Default folder for contract documents and agreements
                  </p>
                </div>

                <p className="text-xs text-muted-foreground italic">
                  Folder paths are automatically generated based on Organization
                  and Project short names.
                </p>
              </div>

              <div className="space-y-3 pt-4 border-t">
                <Label className="text-sm font-medium">
                  Primary Storage Provider
                </Label>
                <Select
                  value={primaryCandidate}
                  onValueChange={handlePrimaryChange}
                >
                  <SelectTrigger className="max-w-md">
                    <SelectValue placeholder="Select primary provider" />
                  </SelectTrigger>
                  <SelectContent>
                    {PROVIDER_OPTIONS.filter((loc) => loc.enabled).map(
                      (location) => (
                        <SelectItem key={location.id} value={location.id}>
                          <div className="flex items-center gap-2">
                            {location.icon}
                            <span>{location.name}</span>
                          </div>
                        </SelectItem>
                      )
                    )}
                  </SelectContent>
                </Select>
              </div>
            </CardContent>
          </Card>

          {/* Multi-Location Storage */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Multi-Location Storage</CardTitle>
              <CardDescription>
                Select multiple storage locations for this project
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                <div className="grid gap-4">
                  {PROVIDER_OPTIONS.map((location) => (
                    <div
                      key={location.id}
                      className={`flex items-center justify-between p-4 rounded-lg border transition-all ${
                        selectedLocations.includes(location.id)
                          ? "border-primary bg-primary/5"
                          : "border-border hover:border-muted-foreground/50"
                      } ${!location.enabled ? "opacity-50" : ""}`}
                    >
                      <div className="flex items-center gap-3">
                        <Checkbox
                          id={`project-${location.id}`}
                          checked={selectedLocations.includes(location.id)}
                          onCheckedChange={() =>
                            handleLocationToggle(location.id)
                          }
                          disabled={!location.enabled}
                        />
                        <div className="flex items-center gap-2">
                          <div
                            className={`p-2 rounded-md ${
                              selectedLocations.includes(location.id)
                                ? "bg-primary/20 text-primary"
                                : "bg-muted text-muted-foreground"
                            }`}
                          >
                            {location.icon}
                          </div>
                          <div>
                            <Label
                              htmlFor={`project-${location.id}`}
                              className="font-medium cursor-pointer"
                            >
                              {location.name}
                            </Label>
                            <p className="text-xs text-muted-foreground">
                              {location.description}
                            </p>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {location.id === primaryCandidate && (
                          <Badge
                            variant="neutral"
                            className="bg-primary/10 text-primary"
                          >
                            Primary
                          </Badge>
                        )}
                        {!location.enabled && (
                          <Badge
                            variant="outline"
                            className="text-muted-foreground"
                          >
                            Not Configured
                          </Badge>
                        )}
                        {selectedLocations.includes(location.id) &&
                          location.enabled && (
                            <Check className="h-4 w-4 text-primary" />
                          )}
                      </div>
                    </div>
                  ))}
                </div>

                {selectedLocations.length > 1 && (
                  <div className="mt-4 p-4 bg-muted/50 rounded-lg">
                    <p className="text-sm text-muted-foreground">
                      <strong className="text-foreground">
                        Multi-location enabled:
                      </strong>{" "}
                      Files will be saved to {selectedLocations.length}{" "}
                      locations simultaneously
                    </p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* Computed Folder Paths - Always visible */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Computed Folder Paths</CardTitle>
          <CardDescription>
            Paths are generated from the organization short name; projects
            append their own short name.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Incoming</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">
              {incomingPath}
            </code>
          </div>
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Outgoing</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">
              {outgoingPath}
            </code>
          </div>
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Contracts</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">
              {contractsPath}
            </code>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button
          onClick={handleSave}
          disabled={saving || !selectedProject}
          className="flex items-center gap-2"
        >
          {saving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {saving ? "Saving..." : "Save Project Settings"}
        </Button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading project storage settings...
        </div>
      )}
    </div>
  );
};

export default ProjectStorageSettings;
