import React, { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Switch } from '@/components/ui/switch';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  HardDrive,
  Cloud,
  Server,
  Plus,
  Settings,
  Trash2,
  CheckCircle2,
  XCircle,
  Edit,
  ExternalLink,
  Shield
} from 'lucide-react';
import { useToast } from '@/hooks/use-toast';

interface StorageProvider {
  id: string;
  name: string;
  type: 'local' | 's3' | 'azure' | 'gcs' | 'custom';
  icon: React.ReactNode;
  configured: boolean;
  enabled: boolean;
  description: string;
  config?: {
    endpoint?: string;
    bucket?: string;
    region?: string;
    path?: string;
  };
}

const StorageProvidersSettings = () => {
  const { toast } = useToast();
  const [editingProvider, setEditingProvider] = useState<StorageProvider | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);

  const [providers, setProviders] = useState<StorageProvider[]>([
    {
      id: 'local',
      name: 'Local Disk Storage',
      type: 'local',
      icon: <HardDrive className="h-5 w-5" />,
      configured: true,
      enabled: true,
      description: 'Store files directly on the server\'s local file system',
      config: {
        path: '/var/storage/documents'
      }
    },
    {
      id: 's3',
      name: 'Amazon S3',
      type: 's3',
      icon: <Cloud className="h-5 w-5" />,
      configured: true,
      enabled: true,
      description: 'Amazon Simple Storage Service (S3) for scalable cloud storage',
      config: {
        bucket: 'my-document-bucket',
        region: 'us-east-1'
      }
    },
    {
      id: 'azure',
      name: 'Azure Blob Storage',
      type: 'azure',
      icon: <Cloud className="h-5 w-5" />,
      configured: true,
      enabled: true,
      description: 'Microsoft Azure Blob Storage for enterprise cloud storage',
      config: {
        endpoint: 'https://myaccount.blob.core.windows.net'
      }
    },
    {
      id: 'gcs',
      name: 'Google Cloud Storage',
      type: 'gcs',
      icon: <Cloud className="h-5 w-5" />,
      configured: false,
      enabled: false,
      description: 'Google Cloud Storage for unified object storage'
    },
    {
      id: 'custom',
      name: 'Custom Server',
      type: 'custom',
      icon: <Server className="h-5 w-5" />,
      configured: false,
      enabled: false,
      description: 'Connect to a custom storage server via SFTP, WebDAV, or API'
    },
  ]);

  const toggleProvider = (providerId: string) => {
    setProviders(prev => prev.map(p => {
      if (p.id === providerId) {
        if (!p.configured && !p.enabled) {
          toast({
            title: "Configuration required",
            description: "Please configure this provider before enabling it.",
            variant: "destructive"
          });
          return p;
        }
        const newEnabled = !p.enabled;
        toast({
          title: newEnabled ? "Provider enabled" : "Provider disabled",
          description: `${p.name} has been ${newEnabled ? 'enabled' : 'disabled'}.`,
        });
        return { ...p, enabled: newEnabled };
      }
      return p;
    }));
  };

  const handleConfigure = (provider: StorageProvider) => {
    setEditingProvider(provider);
    setIsDialogOpen(true);
  };

  const handleSaveConfiguration = () => {
    if (editingProvider) {
      setProviders(prev => prev.map(p =>
        p.id === editingProvider.id
          ? { ...editingProvider, configured: true }
          : p
      ));
      toast({
        title: "Configuration saved",
        description: `${editingProvider.name} has been configured successfully.`,
      });
      setIsDialogOpen(false);
      setEditingProvider(null);
    }
  };

  const getProviderTypeColor = (type: string) => {
    switch (type) {
      case 'local': return 'bg-emerald-500/10 text-emerald-600 border-emerald-200';
      case 's3': return 'bg-orange-500/10 text-orange-600 border-orange-200';
      case 'azure': return 'bg-blue-500/10 text-blue-600 border-blue-200';
      case 'gcs': return 'bg-red-500/10 text-red-600 border-red-200';
      case 'custom': return 'bg-purple-500/10 text-purple-600 border-purple-200';
      default: return 'bg-muted text-muted-foreground';
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-primary/10 rounded-lg">
                <Cloud className="h-5 w-5 text-primary" />
              </div>
              <div>
                <CardTitle>Storage Providers</CardTitle>
                <CardDescription>
                  Configure and manage your storage provider connections
                </CardDescription>
              </div>
            </div>
            <Button variant="outline" className="flex items-center gap-2">
              <Plus className="h-4 w-4" />
              Add Provider
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {providers.map(provider => (
              <div
                key={provider.id}
                className={`p-5 rounded-xl border transition-all ${
                  provider.enabled
                    ? 'border-primary/30 bg-card shadow-sm'
                    : 'border-border bg-muted/30'
                }`}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-4">
                    <div className={`p-3 rounded-lg ${
                      provider.enabled
                        ? 'bg-primary/10 text-primary'
                        : 'bg-muted text-muted-foreground'
                    }`}>
                      {provider.icon}
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-foreground">{provider.name}</h3>
                        <Badge variant="outline" className={getProviderTypeColor(provider.type)}>
                          {provider.type.toUpperCase()}
                        </Badge>
                      </div>
                      <p className="text-sm text-muted-foreground max-w-md">
                        {provider.description}
                      </p>

                      {/* Configuration Details */}
                      {provider.configured && provider.config && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {provider.config.path && (
                            <Badge variant="secondary" className="font-mono text-xs">
                              Path: {provider.config.path}
                            </Badge>
                          )}
                          {provider.config.bucket && (
                            <Badge variant="secondary" className="font-mono text-xs">
                              Bucket: {provider.config.bucket}
                            </Badge>
                          )}
                          {provider.config.region && (
                            <Badge variant="secondary" className="font-mono text-xs">
                              Region: {provider.config.region}
                            </Badge>
                          )}
                          {provider.config.endpoint && (
                            <Badge variant="secondary" className="font-mono text-xs">
                              {provider.config.endpoint}
                            </Badge>
                          )}
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    {/* Status Indicator */}
                    <div className="flex items-center gap-2">
                      {provider.configured ? (
                        <div className="flex items-center gap-1.5 text-sm text-emerald-600">
                          <CheckCircle2 className="h-4 w-4" />
                          <span>Configured</span>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
                          <XCircle className="h-4 w-4" />
                          <span>Not configured</span>
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleConfigure(provider)}
                        className="flex items-center gap-1"
                      >
                        {provider.configured ? (
                          <>
                            <Edit className="h-4 w-4" />
                            Edit
                          </>
                        ) : (
                          <>
                            <Settings className="h-4 w-4" />
                            Configure
                          </>
                        )}
                      </Button>

                      <Switch
                        checked={provider.enabled}
                        onCheckedChange={() => toggleProvider(provider.id)}
                        disabled={!provider.configured}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Security Notice */}
      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="pt-6">
          <div className="flex items-start gap-3">
            <Shield className="h-5 w-5 text-primary mt-0.5" />
            <div>
              <h4 className="font-medium text-foreground">Security Notice</h4>
              <p className="text-sm text-muted-foreground mt-1">
                All storage provider credentials are encrypted at rest and in transit.
                Access keys and secrets are never exposed in logs or error messages.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Configuration Dialog */}
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              {editingProvider?.icon}
              Configure {editingProvider?.name}
            </DialogTitle>
            <DialogDescription>
              Enter the configuration details for this storage provider
            </DialogDescription>
          </DialogHeader>

          {editingProvider && (
            <div className="space-y-4 py-4">
              {editingProvider.type === 'local' && (
                <div className="space-y-2">
                  <Label htmlFor="local-path">Storage Path</Label>
                  <Input
                    id="local-path"
                    placeholder="/var/storage/documents"
                    defaultValue={editingProvider.config?.path}
                  />
                  <p className="text-xs text-muted-foreground">
                    The local file system path where documents will be stored
                  </p>
                </div>
              )}

              {editingProvider.type === 's3' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="s3-bucket">Bucket Name</Label>
                    <Input
                      id="s3-bucket"
                      placeholder="my-document-bucket"
                      defaultValue={editingProvider.config?.bucket}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="s3-region">Region</Label>
                    <Input
                      id="s3-region"
                      placeholder="us-east-1"
                      defaultValue={editingProvider.config?.region}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="s3-access-key">Access Key ID</Label>
                    <Input
                      id="s3-access-key"
                      type="password"
                      placeholder="AKIAIOSFODNN7EXAMPLE"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="s3-secret-key">Secret Access Key</Label>
                    <Input
                      id="s3-secret-key"
                      type="password"
                      placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
                    />
                  </div>
                </>
              )}

              {editingProvider.type === 'azure' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="azure-endpoint">Storage Account Endpoint</Label>
                    <Input
                      id="azure-endpoint"
                      placeholder="https://myaccount.blob.core.windows.net"
                      defaultValue={editingProvider.config?.endpoint}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="azure-container">Container Name</Label>
                    <Input
                      id="azure-container"
                      placeholder="documents"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="azure-key">Access Key</Label>
                    <Input
                      id="azure-key"
                      type="password"
                      placeholder="Your Azure storage access key"
                    />
                  </div>
                </>
              )}

              {editingProvider.type === 'gcs' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="gcs-project">Project ID</Label>
                    <Input
                      id="gcs-project"
                      placeholder="my-gcp-project"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="gcs-bucket">Bucket Name</Label>
                    <Input
                      id="gcs-bucket"
                      placeholder="my-document-bucket"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="gcs-credentials">Service Account JSON</Label>
                    <Input
                      id="gcs-credentials"
                      type="file"
                      accept=".json"
                    />
                    <p className="text-xs text-muted-foreground">
                      Upload your service account credentials JSON file
                    </p>
                  </div>
                </>
              )}

              {editingProvider.type === 'custom' && (
                <>
                  <div className="space-y-2">
                    <Label htmlFor="custom-endpoint">Server Endpoint</Label>
                    <Input
                      id="custom-endpoint"
                      placeholder="https://storage.example.com/api"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="custom-username">Username</Label>
                    <Input
                      id="custom-username"
                      placeholder="admin"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="custom-password">Password / API Key</Label>
                    <Input
                      id="custom-password"
                      type="password"
                      placeholder="Your API key or password"
                    />
                  </div>
                </>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setIsDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSaveConfiguration}>
              Save Configuration
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default StorageProvidersSettings;
