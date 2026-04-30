import React, { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Building, FolderClosed, Settings, HardDrive, Cloud, Server } from 'lucide-react';
import OrganizationStorageSettings from '@/components/settings/OrganizationStorageSettings';
import ProjectStorageSettings from '@/components/settings/ProjectStorageSettings';
import StorageProvidersSettings from '@/components/settings/StorageProvidersSettings';

const SettingsPage = () => {
  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-3">
        <div className="p-2 bg-primary/10 rounded-lg">
          <Settings className="h-6 w-6 text-primary" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-foreground">Settings</h1>
          <p className="text-muted-foreground">Configure your file storage preferences and providers</p>
        </div>
      </div>

      <Tabs defaultValue="organization" className="w-full">
        <TabsList className="grid w-full grid-cols-3 lg:w-[500px]">
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
      </Tabs>
    </div>
  );
};

export default SettingsPage;
