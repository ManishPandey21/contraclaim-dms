import React, { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Building, HardDrive, Cloud, Server, Save, Check, Lock, Loader2 } from 'lucide-react';
import { useToast } from '@/hooks/use-toast';
import {
  getOrgStorageSettings,
  updateOrgStorageSettings,
  StorageProviderConfig,
  StorageProviderId,
  StorageBasePaths,
} from '@/services/storage-settings-api';
import { listOrganizations, Organization } from '@/services/organizations-api';

interface ProviderOption {
  id: StorageProviderId;
  name: string;
  icon: React.ReactNode;
  description: string;
  enabled: boolean;
}

const AVAILABLE_PROVIDERS: ProviderOption[] = [
  {
    id: 'local',
    name: 'Local Disk',
    icon: <HardDrive className="h-4 w-4" />,
    description: 'Store files on the local server disk',
    enabled: true,
  },
  {
    id: 's3',
    name: 'Amazon S3',
    icon: <Cloud className="h-4 w-4" />,
    description: 'Store files in Amazon S3 bucket',
    enabled: true,
  },
  {
    id: 'azure',
    name: 'Azure Blob Storage',
    icon: <Cloud className="h-4 w-4" />,
    description: 'Store files in Azure Blob Storage',
    enabled: false,
  },
  {
    id: 'gcs',
    name: 'Google Cloud Storage',
    icon: <Cloud className="h-4 w-4" />,
    description: 'Store files in Google Cloud Storage',
    enabled: false,
  },
  {
    id: 'custom',
    name: 'Custom Server',
    icon: <Server className="h-4 w-4" />,
    description: 'Store files on a custom server',
    enabled: false,
  },
];

const defaultProviderState = (): StorageProviderConfig[] =>
  AVAILABLE_PROVIDERS.map((option) => ({
    id: option.id,
    enabled: option.enabled,
    primary: option.id === 'local',
    bucket: undefined,
    prefix: undefined,
    region: undefined,
    endpoint: undefined,
    extra: {},
  }));

const OrganizationStorageSettings = () => {
  const { toast } = useToast();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string>(() => window.localStorage.getItem('org_id') || '');
  const [shortName, setShortName] = useState('');
  const [isShortNameSet, setIsShortNameSet] = useState(false);
  const [isSuperAdmin] = useState(true); // This would come from auth context
  const [providers, setProviders] = useState<StorageProviderConfig[]>(defaultProviderState());
  const [basePaths, setBasePaths] = useState<StorageBasePaths>({
    incoming: '/ORG/PROJ/incoming',
    outgoing: '/ORG/PROJ/outgoing',
    contracts: '/ORG/PROJ/contracts',
  });
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const selectedLocations = useMemo(
    () => providers.filter((p) => p.enabled).map((p) => p.id),
    [providers]
  );

  const primaryProvider = useMemo(() => {
    const primary = providers.find((p) => p.primary);
    if (primary?.id) return primary.id;
    return selectedLocations[0] || 'local';
  }, [providers, selectedLocations]);

  const mergeProviders = (apiProviders: StorageProviderConfig[]) => {
    const map = new Map<StorageProviderId, StorageProviderConfig>();
    apiProviders.forEach((p) => map.set(p.id, { ...p }));
    const merged =
      AVAILABLE_PROVIDERS.map<StorageProviderConfig>((option) => {
        const existing = map.get(option.id);
        return {
          id: option.id,
          enabled: existing ? existing.enabled : option.enabled,
          primary: existing ? existing.primary : option.id === 'local',
          bucket: existing?.bucket,
          prefix: existing?.prefix,
          region: existing?.region,
          endpoint: existing?.endpoint,
          extra: existing?.extra ?? {},
        };
      }) || defaultProviderState();
    // Ensure at least one enabled
    if (!merged.some((p) => p.enabled)) {
      const local = merged.find((p) => p.id === 'local');
      if (local) local.enabled = true;
    }
    // Ensure single primary
    const primary = merged.find((p) => p.primary && p.enabled);
    if (!primary) {
      const fallback = merged.find((p) => p.enabled) || merged[0];
      if (fallback) fallback.primary = true;
    } else {
      merged.forEach((p) => {
        p.primary = p.id === primary.id;
      });
    }
    setProviders(merged);
  };

  const loadSettings = async (orgId: string) => {
    if (!orgId) return;
    setLoading(true);
    try {
      const data = await getOrgStorageSettings(orgId);
      setShortName(data.org_short_name || '');
      setIsShortNameSet(Boolean(data.org_short_name));
      setBasePaths(data.base_paths);
      mergeProviders(data.providers || []);
    } catch (error: any) {
      // Fallback to defaults to keep UI usable even if backend route is unavailable
      setProviders(defaultProviderState());
      toast({
        title: 'Failed to load settings',
        description: error?.message || 'Unable to fetch organization storage settings.',
        variant: 'destructive',
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
        if (!selectedOrgId && orgs.length) {
          const first = orgs[0]._id;
          setSelectedOrgId(first);
          window.localStorage.setItem('org_id', first);
          await loadSettings(first);
        } else if (selectedOrgId) {
          await loadSettings(selectedOrgId);
        }
      } catch (error: any) {
        toast({
          title: 'Failed to load organizations',
          description: error?.message || 'Unable to fetch organization list.',
          variant: 'destructive',
        });
      }
    };
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  const handleLocationToggle = (locationId: string) => {
    setProviders((prev) => {
      const next = prev.map((p) =>
        p.id === locationId ? { ...p, enabled: !p.enabled } : { ...p }
      );
      const enabledCount = next.filter((p) => p.enabled).length;
      if (enabledCount === 0) {
        toast({
          title: 'At least one location required',
          description: 'You must have at least one storage location selected.',
          variant: 'destructive',
        });
        return prev;
      }
      // Ensure primary remains valid
      if (!next.some((p) => p.primary && p.enabled)) {
        const fallback = next.find((p) => p.enabled);
        if (fallback) {
          next.forEach((p) => (p.primary = p.id === fallback.id));
        }
      }
      return next;
    });
  };

  const handleSave = () => {
    if (!selectedOrgId) {
      toast({
        title: 'Organization not selected',
        description: 'Please select an organization to configure storage.',
        variant: 'destructive',
      });
      return;
    }
    // Ensure at least one enabled provider before saving
    const enabledProviders = providers.filter((p) => p.enabled);
    if (!enabledProviders.length) {
      const defaults = defaultProviderState();
      setProviders(defaults);
    }
    setSaving(true);
    updateOrgStorageSettings(selectedOrgId, {
      org_short_name: shortName || undefined,
      providers: providers.length ? providers : defaultProviderState(),
      base_paths: basePaths,
    })
      .then((saved) => {
        setIsShortNameSet(Boolean(saved.org_short_name));
        mergeProviders(saved.providers || []);
        setBasePaths(saved.base_paths);
        toast({
          title: 'Settings saved',
          description: 'Organization storage settings have been updated successfully.',
        });
      })
      .catch((error: any) => {
        toast({
          title: 'Failed to save settings',
          description: error?.message || 'Please try again.',
          variant: 'destructive',
        });
      })
      .finally(() => setSaving(false));
  };

  const handlePrimaryChange = (value: string) => {
    setProviders((prev) =>
      prev.map((p) => ({
        ...p,
        primary: p.id === value,
      }))
    );
  };

  const handleOrgChange = (value: string) => {
    setSelectedOrgId(value);
    window.localStorage.setItem('org_id', value);
    loadSettings(value);
  };

  const canEditShortName = !isShortNameSet || isSuperAdmin;
  const disableForm = !selectedOrgId || loading;
  const previewPaths = useMemo(() => {
    const org = (shortName || 'ORG').trim().toUpperCase() || 'ORG';
    const fill = (tpl: string) => tpl.replace(/ORG/gi, org);
    return {
      incoming: fill(basePaths.incoming),
      outgoing: fill(basePaths.outgoing),
      contracts: fill(basePaths.contracts),
    };
  }, [basePaths, shortName]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Select Organization</CardTitle>
          <CardDescription>Choose which organization’s storage settings to edit.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label className="text-sm font-medium">Organization</Label>
          <Select value={selectedOrgId} onValueChange={handleOrgChange}>
            <SelectTrigger className="max-w-md">
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
          {!organizations.length && (
            <p className="text-xs text-muted-foreground">No organizations found. Please create one first.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-lg">
              <Building className="h-5 w-5 text-primary" />
            </div>
            <div>
              <CardTitle>Organization Storage Settings</CardTitle>
              <CardDescription>
                Configure default file storage settings for your entire organization
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Organization Short Name */}
          <div className="space-y-3">
            <Label htmlFor="org-short-name" className="text-sm font-medium flex items-center gap-2">
              <Building className="h-4 w-4 text-muted-foreground" />
              Organization Short Name
              {isShortNameSet && !isSuperAdmin && (
                <Lock className="h-3 w-3 text-muted-foreground" />
              )}
            </Label>
            <div className="flex items-center gap-3">
              <Input
                id="org-short-name"
                value={shortName}
                onChange={(e) => setShortName(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ''))}
                placeholder="ACME"
                className="max-w-[200px] uppercase"
                disabled={!canEditShortName || disableForm}
                maxLength={10}
              />
              {isShortNameSet && (
                <Badge variant={isSuperAdmin ? "secondary" : "outline"} className="flex items-center gap-1">
                  <Lock className="h-3 w-3" />
                  {isSuperAdmin ? "Super Admin" : "Locked"}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              A short identifier for your organization (max 10 characters).
              {isShortNameSet && " Once set, only Super Admins can modify this."}
            </p>
          </div>



          {/* Primary Storage Provider */}
          <div className="space-y-3 pt-4 border-t">
            <Label className="text-sm font-medium">Primary Storage Provider</Label>
            <Select value={primaryProvider} onValueChange={handlePrimaryChange}>
              <SelectTrigger className="max-w-md">
                <SelectValue placeholder="Select primary provider" />
              </SelectTrigger>
              <SelectContent>
                {AVAILABLE_PROVIDERS.filter(loc => loc.enabled).map(location => (
                  <SelectItem key={location.id} value={location.id}>
                    <div className="flex items-center gap-2">
                      {location.icon}
                      <span>{location.name}</span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The primary provider is used as the main storage location
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Multi-Location Storage */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Multi-Location Storage</CardTitle>
          <CardDescription>
            Enable saving files to multiple storage locations simultaneously for redundancy and accessibility
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid gap-4">
              {AVAILABLE_PROVIDERS.map(location => (
                <div
                  key={location.id}
                  className={`flex items-center justify-between p-4 rounded-lg border transition-all ${
                    selectedLocations.includes(location.id)
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-muted-foreground/50'
                  } ${!location.enabled ? 'opacity-50' : ''}`}
                >
                    <div className="flex items-center gap-3">
                      <Checkbox
                        id={`org-${location.id}`}
                        checked={selectedLocations.includes(location.id)}
                        onCheckedChange={() => handleLocationToggle(location.id)}
                      disabled={!location.enabled}
                    />
                    <div className="flex items-center gap-2">
                      <div className={`p-2 rounded-md ${
                        selectedLocations.includes(location.id)
                          ? 'bg-primary/20 text-primary'
                          : 'bg-muted text-muted-foreground'
                      }`}>
                        {location.icon}
                      </div>
                      <div>
                        <Label
                          htmlFor={`org-${location.id}`}
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
                    {location.id === primaryProvider && (
                      <Badge variant="secondary" className="bg-primary/10 text-primary">
                        Primary
                      </Badge>
                    )}
                    {!location.enabled && (
                      <Badge variant="outline" className="text-muted-foreground">
                        Not Configured
                      </Badge>
                    )}
                    {selectedLocations.includes(location.id) && location.enabled && (
                      <Check className="h-4 w-4 text-primary" />
                    )}
                  </div>
                </div>
              ))}
            </div>

                {selectedLocations.length > 1 && (
                  <div className="mt-4 p-4 bg-muted/50 rounded-lg">
                    <p className="text-sm text-muted-foreground">
                      <strong className="text-foreground">Multi-location enabled:</strong> Files will be saved to{' '}
                      {selectedLocations.length} locations simultaneously ({selectedLocations.join(', ')})
                    </p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Computed Folder Paths</CardTitle>
          <CardDescription>Paths are generated from the organization short name; projects append their own short name.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Incoming</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">{previewPaths.incoming}</code>
          </div>
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Outgoing</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">{previewPaths.outgoing}</code>
          </div>
          <div className="p-3 bg-muted/50 rounded-lg border">
            <p className="text-sm font-medium mb-1">Contracts</p>
            <code className="text-xs bg-background px-2 py-1 rounded border block">{previewPaths.contracts}</code>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={handleSave} disabled={saving || loading || !selectedOrgId} className="flex items-center gap-2">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          {saving ? 'Saving...' : 'Save Organization Settings'}
        </Button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading organization storage settings...
        </div>
      )}
    </div>
  );
};

export default OrganizationStorageSettings;
