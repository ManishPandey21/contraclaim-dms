import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Bell, Building, FolderClosed, Settings, Cloud, Mail, ShieldCheck } from 'lucide-react';
import OrganizationStorageSettings from '@/components/settings/OrganizationStorageSettings';
import ProjectStorageSettings from '@/components/settings/ProjectStorageSettings';
import StorageProvidersSettings from '@/components/settings/StorageProvidersSettings';
import NotificationSettings from '@/components/settings/NotificationSettings';
import SmtpSettingsPanel from '@/components/settings/SmtpSettingsPanel';
import PromptSettingsPanel from '@/components/settings/PromptSettingsPanel';
import LegalSettingsPanel from '@/components/settings/LegalSettingsPanel';
import { useRBAC } from '@/hooks/useRBAC';

const SettingsPage = () => {
  const { roles } = useRBAC();
  const isSuperadmin = roles.includes('superadmin');

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-3">
        <div className="p-2 bg-primary/10 rounded-lg">
          <Settings className="h-6 w-6 text-primary" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-foreground">Settings</h1>
          <p className="text-muted-foreground">Configure storage, notifications, and document-sharing email delivery</p>
        </div>
      </div>

      <Tabs defaultValue="organization" className="w-full">
        <TabsList className={`grid w-full grid-cols-3 gap-1 ${isSuperadmin ? 'sm:grid-cols-4 lg:w-[1200px] lg:grid-cols-8' : 'sm:grid-cols-4 lg:w-[1050px] lg:grid-cols-7'}`}>
          <TabsTrigger value="organization" className="flex items-center gap-2">
            <Building className="h-4 w-4" />
            <span className="hidden sm:inline">Organization</span>
          </TabsTrigger>
          <TabsTrigger value="project" className="flex items-center gap-2">
            <FolderClosed className="h-4 w-4" />
            <span className="hidden sm:inline">Project</span>
          </TabsTrigger>
          <TabsTrigger value="providers" className="flex items-center gap-2">
            <Cloud className="h-4 w-4" />
            <span className="hidden sm:inline">Providers</span>
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" />
            <span className="hidden sm:inline">Notifications</span>
          </TabsTrigger>
          <TabsTrigger value="organization-smtp" className="flex items-center gap-2">
            <Mail className="h-4 w-4" />
            <span className="hidden sm:inline">Org SMTP</span>
          </TabsTrigger>
          <TabsTrigger value="project-smtp" className="flex items-center gap-2">
            <Mail className="h-4 w-4" />
            <span className="hidden sm:inline">Project SMTP</span>
          </TabsTrigger>
          <TabsTrigger value="legal" className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            <span className="hidden sm:inline">Legal</span>
          </TabsTrigger>
          {isSuperadmin && (
            <TabsTrigger value="ai-prompts" className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              <span className="hidden sm:inline">AI Prompts</span>
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="organization" className="mt-6">
          <OrganizationStorageSettings />
        </TabsContent>

        <TabsContent value="project" className="mt-6">
          <ProjectStorageSettings />
        </TabsContent>

        <TabsContent value="providers" className="mt-6">
          <StorageProvidersSettings />
        </TabsContent>

        <TabsContent value="notifications" className="mt-6">
          <NotificationSettings />
        </TabsContent>

        <TabsContent value="organization-smtp" className="mt-6">
          <SmtpSettingsPanel scope="organization" />
        </TabsContent>

        <TabsContent value="project-smtp" className="mt-6">
          <SmtpSettingsPanel scope="project" />
        </TabsContent>

        <TabsContent value="legal" className="mt-6">
          <LegalSettingsPanel />
        </TabsContent>

        {isSuperadmin && (
          <TabsContent value="ai-prompts" className="mt-6">
            <PromptSettingsPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
};

export default SettingsPage;
