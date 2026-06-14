import React, { useEffect, useState } from "react";
import { Bell, Mail, Send, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import enhancedApi, { Project } from "@/services/enhanced-api";
import {
  NotificationPreference,
  ProjectNotificationSubscription,
} from "@/types/api";

const trackedEvents = [
  { key: "new_upload", label: "Document upload" },
  { key: "bulk_upload_completed", label: "Bulk upload summary" },
  { key: "draft_saved", label: "Draft saved" },
  { key: "draft_approved", label: "Draft approved" },
  { key: "draft_rejected", label: "Draft rejected" },
  { key: "approval_assigned", label: "Approval assigned" },
  { key: "comment_added", label: "Comments" },
  { key: "reply_reminder", label: "Reply reminders" },
];

function withEventEnabled<T extends { event_settings: Record<string, any> }>(
  settings: T,
  eventKey: string,
  enabled: boolean,
): T {
  return {
    ...settings,
    event_settings: {
      ...settings.event_settings,
      [eventKey]: {
        ...(settings.event_settings?.[eventKey] || {}),
        enabled,
      },
    },
  };
}

const NotificationSettings: React.FC = () => {
  const [preferences, setPreferences] = useState<NotificationPreference | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [projectSettings, setProjectSettings] =
    useState<ProjectNotificationSubscription | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const [preferenceResponse, projectResponse] = await Promise.all([
          enhancedApi.getNotificationPreferences(),
          enhancedApi.getProjects(),
        ]);
        if (cancelled) return;
        setPreferences(preferenceResponse);
        setProjects(projectResponse);
        if (projectResponse[0]?._id) {
          setSelectedProjectId(projectResponse[0]._id);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function loadProjectSettings() {
      if (!selectedProjectId) {
        setProjectSettings(null);
        return;
      }
      const response = await enhancedApi.getProjectNotificationSettings(selectedProjectId);
      if (!cancelled) setProjectSettings(response);
    }
    void loadProjectSettings();
    return () => {
      cancelled = true;
    };
  }, [selectedProjectId]);

  const savePreferences = async (next: NotificationPreference) => {
    setPreferences(next);
    setSaving(true);
    setStatus(null);
    try {
      const saved = await enhancedApi.updateNotificationPreferences({
        default_channels: next.default_channels,
        event_settings: next.event_settings,
        digest_enabled: next.digest_enabled,
        browser_notifications_enabled: next.browser_notifications_enabled,
        email_notifications_enabled: next.email_notifications_enabled,
      });
      setPreferences(saved);
      setStatus("Notification preferences saved");
    } finally {
      setSaving(false);
    }
  };

  const saveProjectSettings = async (next: ProjectNotificationSubscription) => {
    setProjectSettings(next);
    setSaving(true);
    setStatus(null);
    try {
      const saved = await enhancedApi.updateProjectNotificationSettings(next.project_id, {
        subscribed: next.subscribed,
        event_settings: next.event_settings,
      });
      setProjectSettings(saved);
      setStatus("Project notification settings saved");
    } finally {
      setSaving(false);
    }
  };

  const toggleChannel = (channel: string, enabled: boolean) => {
    if (!preferences) return;
    const channels = new Set(preferences.default_channels);
    if (enabled) channels.add(channel);
    else channels.delete(channel);
    void savePreferences({ ...preferences, default_channels: Array.from(channels) });
  };

  const sendTestEmail = async () => {
    setSaving(true);
    setStatus(null);
    try {
      const response = await enhancedApi.sendNotificationTestEmail({ event_type: "new_upload" });
      const state = response.sent ? "sent" : response.delivery_log?.status || "skipped";
      setStatus(`Test email ${state}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !preferences) {
    return <div className="text-sm text-muted-foreground">Loading notification settings...</div>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="h-5 w-5" />
            User notifications
          </CardTitle>
          <CardDescription>Control channels and noisy event types.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-4 md:grid-cols-3">
            <div className="flex items-center justify-between rounded-md border p-3">
              <Label htmlFor="email-notifications">Email</Label>
              <Switch
                id="email-notifications"
                checked={preferences.email_notifications_enabled}
                onCheckedChange={(checked) =>
                  void savePreferences({
                    ...preferences,
                    email_notifications_enabled: checked,
                  })
                }
              />
            </div>
            <div className="flex items-center justify-between rounded-md border p-3">
              <Label htmlFor="browser-notifications">Browser</Label>
              <Switch
                id="browser-notifications"
                checked={preferences.browser_notifications_enabled}
                onCheckedChange={(checked) =>
                  void savePreferences({
                    ...preferences,
                    browser_notifications_enabled: checked,
                  })
                }
              />
            </div>
            <div className="flex items-center justify-between rounded-md border p-3">
              <Label htmlFor="digest-notifications">Digest</Label>
              <Switch
                id="digest-notifications"
                checked={preferences.digest_enabled}
                onCheckedChange={(checked) =>
                  void savePreferences({ ...preferences, digest_enabled: checked })
                }
              />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {["in_app", "websocket", "email"].map((channel) => (
              <div key={channel} className="flex items-center justify-between rounded-md border p-3">
                <Label>{channel.replace("_", " ")}</Label>
                <Switch
                  checked={preferences.default_channels.includes(channel)}
                  onCheckedChange={(checked) => toggleChannel(channel, checked)}
                />
              </div>
            ))}
          </div>

          <div className="space-y-2">
            {trackedEvents.map((event) => (
              <div key={event.key} className="flex items-center justify-between border-b py-2 last:border-b-0">
                <span className="text-sm font-medium">{event.label}</span>
                <Switch
                  checked={preferences.event_settings?.[event.key]?.enabled !== false}
                  onCheckedChange={(checked) =>
                    void savePreferences(withEventEnabled(preferences, event.key, checked))
                  }
                />
              </div>
            ))}
          </div>

          <Button type="button" variant="outline" onClick={sendTestEmail} disabled={saving}>
            <Send className="mr-2 h-4 w-4" />
            Send test email
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings2 className="h-5 w-5" />
            Project subscription
          </CardTitle>
          <CardDescription>Choose whether project events should reach you.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Select value={selectedProjectId} onValueChange={setSelectedProjectId}>
            <SelectTrigger className="max-w-lg">
              <SelectValue placeholder="Select project" />
            </SelectTrigger>
            <SelectContent>
              {projects.map((project) => (
                <SelectItem key={project._id} value={project._id}>
                  {project.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {projectSettings && (
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-md border p-3">
                <Label>Subscribed to this project</Label>
                <Switch
                  checked={projectSettings.subscribed}
                  onCheckedChange={(checked) =>
                    void saveProjectSettings({ ...projectSettings, subscribed: checked })
                  }
                />
              </div>

              {trackedEvents.slice(0, 5).map((event) => (
                <div key={event.key} className="flex items-center justify-between border-b py-2 last:border-b-0">
                  <span className="text-sm font-medium">{event.label}</span>
                  <Switch
                    checked={projectSettings.event_settings?.[event.key]?.enabled !== false}
                    onCheckedChange={(checked) =>
                      void saveProjectSettings(
                        withEventEnabled(projectSettings, event.key, checked),
                      )
                    }
                  />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {status && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Mail className="h-4 w-4" />
          {status}
        </div>
      )}
    </div>
  );
};

export default NotificationSettings;
