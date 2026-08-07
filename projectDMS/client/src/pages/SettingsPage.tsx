import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Bell, Building, FolderClosed, Settings, Mail, ShieldCheck } from 'lucide-react';
import OrganizationStorageSettings from '@/components/settings/OrganizationStorageSettings';
import ProjectStorageSettings from '@/components/settings/ProjectStorageSettings';
import NotificationSettings from '@/components/settings/NotificationSettings';
import SmtpSettingsPanel from '@/components/settings/SmtpSettingsPanel';
import PromptSettingsPanel from '@/components/settings/PromptSettingsPanel';
import LegalSettingsPanel from '@/components/settings/LegalSettingsPanel';
import { useRBAC } from '@/hooks/useRBAC';

const SettingsPage = () => {
  const { roles, can } = useRBAC();
  const isSuperadmin = roles.includes('superadmin');
  const canViewStorage = isSuperadmin || can('settings.storage.view');
  const canViewNotifications = isSuperadmin || can('settings.notification.view');
  const canViewSmtp = isSuperadmin || can('settings.smtp.view');
  const canViewLegal = isSuperadmin || can('settings.legal.view');
  const canViewPrompts = isSuperadmin || can('settings.prompt.view');
  const defaultSettingsTab = canViewStorage
    ? 'organization'
    : canViewNotifications
      ? 'notifications'
      : canViewSmtp
        ? 'organization-smtp'
        : canViewLegal
          ? 'legal'
          : 'ai-prompts';

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

      <Tabs defaultValue={defaultSettingsTab} className="w-full">
        <TabsList className="grid w-full grid-cols-2 gap-1 sm:grid-cols-4 lg:w-[1050px] lg:grid-cols-7">
          {canViewStorage && <TabsTrigger value="organization" className="flex items-center gap-2">
            <Building className="h-4 w-4" />
            <span className="hidden sm:inline">Organization</span>
          </TabsTrigger>}
          {canViewStorage && <TabsTrigger value="project" className="flex items-center gap-2">
            <FolderClosed className="h-4 w-4" />
            <span className="hidden sm:inline">Project</span>
          </TabsTrigger>}
          {canViewNotifications && <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" />
            <span className="hidden sm:inline">Notifications</span>
          </TabsTrigger>}
          {canViewSmtp && <TabsTrigger value="organization-smtp" className="flex items-center gap-2">
            <Mail className="h-4 w-4" />
            <span className="hidden sm:inline">Org SMTP</span>
          </TabsTrigger>}
          {canViewSmtp && <TabsTrigger value="project-smtp" className="flex items-center gap-2">
            <Mail className="h-4 w-4" />
            <span className="hidden sm:inline">Project SMTP</span>
          </TabsTrigger>}
          {canViewLegal && <TabsTrigger value="legal" className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            <span className="hidden sm:inline">Legal</span>
          </TabsTrigger>}
          {canViewPrompts && (
            <TabsTrigger value="ai-prompts" className="flex items-center gap-2">
              <Settings className="h-4 w-4" />
              <span className="hidden sm:inline">AI Prompts</span>
            </TabsTrigger>
          )}
        </TabsList>

        {canViewStorage && <TabsContent value="organization" className="mt-6">
          <OrganizationStorageSettings />
        </TabsContent>}

        {canViewStorage && <TabsContent value="project" className="mt-6">
          <ProjectStorageSettings />
        </TabsContent>}

        {canViewNotifications && <TabsContent value="notifications" className="mt-6">
          <NotificationSettings />
        </TabsContent>}

        {canViewSmtp && <TabsContent value="organization-smtp" className="mt-6">
          <SmtpSettingsPanel scope="organization" />
        </TabsContent>}

        {canViewSmtp && <TabsContent value="project-smtp" className="mt-6">
          <SmtpSettingsPanel scope="project" />
        </TabsContent>}

        {canViewLegal && <TabsContent value="legal" className="mt-6">
          <LegalSettingsPanel />
        </TabsContent>}

        {canViewPrompts && (
          <TabsContent value="ai-prompts" className="mt-6">
            <PromptSettingsPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
};

export default SettingsPage;
