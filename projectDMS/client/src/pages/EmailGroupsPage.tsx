import React from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { X, PlusCircle, Trash2, Save, Edit2, Loader2 } from "lucide-react";
import { emailGroupsApi, EmailGroup } from "@/services/email-groups-api";
import {
  listOrganizations,
  type Organization,
} from "@/services/organizations-api";
import { listProjects, type Project } from "@/services/projects-api";
import { emailService, EmailSuggestion } from "@/services/email-service";
import { toast } from "sonner";
import { useTenant } from "@/contexts/TenantContext";

const CATEGORIES = [
  "Project Team",
  "Client Contacts",
  "Vendors",
  "Consultants",
  "Internal",
];

type GroupForm = {
  name: string;
  category: string;
  emails: string[];
  input: string;
};

const EmailGroupsPage: React.FC = () => {
  const {
    selectedOrganizationId: selectedOrgId,
    selectedProjectId,
    selectOrganization: setSelectedOrgId,
    selectProject: setSelectedProjectId,
  } = useTenant();
  const [groups, setGroups] = React.useState<EmailGroup[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [editingId, setEditingId] = React.useState<string | null>(null);

  // Scope selection
  const [organizations, setOrganizations] = React.useState<Organization[]>([]);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [scope, setScope] = React.useState<"org" | "project">("org");

  const [form, setForm] = React.useState<GroupForm>({
    name: "",
    category: CATEGORIES[0],
    emails: [],
    input: "",
  });

  const [suggestions, setSuggestions] = React.useState<EmailSuggestion[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = React.useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = React.useState(false);

  const loadGroups = React.useCallback(async () => {
    setLoading(true);
    try {
      const list = await emailGroupsApi.list({
        organization_id: selectedOrgId || undefined,
        project_id:
          scope === "project" && selectedProjectId
            ? selectedProjectId
            : undefined,
      });
      const normalized = Array.isArray(list)
        ? list
        : list && Array.isArray((list as any).groups)
        ? (list as any).groups
        : [];
      setGroups(normalized);
    } catch (e: any) {
      toast.error("Failed to load groups", { description: e?.message });
    } finally {
      setLoading(false);
    }
  }, [selectedOrgId, selectedProjectId, scope]);

  React.useEffect(() => {
    // Load organizations once
    const init = async () => {
      try {
        const orgs = await listOrganizations();
        setOrganizations(orgs);
      } catch {
        setOrganizations([]);
      }
    };
    init();
  }, []);

  // Load projects when organization changes
  React.useEffect(() => {
    const run = async () => {
      if (!selectedOrgId) {
        setProjects([]);
        setSelectedProjectId("");
        return;
      }
      try {
        const projs = await listProjects({ organization_id: selectedOrgId });
        setProjects(projs);
        // ensure current project belongs to selected org
        if (!projs.find((p) => p._id === selectedProjectId)) {
          setSelectedProjectId("");
        }
      } catch {
        setProjects([]);
        setSelectedProjectId("");
      }
    };
    run();
  }, [selectedOrgId, selectedProjectId]);

  // Load groups when scope/org/project selection changes
  React.useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  const addEmail = (val: string) => {
    const v = val.trim().toLowerCase();
    if (!v) return;
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(v)) return;
    setForm((f) => ({ ...f, emails: Array.from(new Set([...f.emails, v])) }));
  };

  const removeEmail = (val: string) => {
    setForm((f) => ({ ...f, emails: f.emails.filter((e) => e !== val) }));
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error("Group name is required");
      return;
    }
    if (!selectedOrgId) {
      toast.error("Select an organization for the group");
      return;
    }
    if (scope === "project" && !selectedProjectId) {
      toast.error("Select a project for the project-level group");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await emailGroupsApi.update(editingId, {
          name: form.name.trim(),
          category: form.category,
          emails: form.emails,
          organization_id: selectedOrgId || undefined,
          project_id:
            scope === "project" && selectedProjectId
              ? selectedProjectId
              : undefined,
        });
        toast.success("Group updated");
      } else {
        await emailGroupsApi.create({
          name: form.name.trim(),
          category: form.category,
          emails: form.emails,
          organization_id: selectedOrgId || undefined,
          project_id:
            scope === "project" && selectedProjectId
              ? selectedProjectId
              : undefined,
        });
        toast.success("Group created");
      }
      setForm({ name: "", category: CATEGORIES[0], emails: [], input: "" });
      setEditingId(null);
      await loadGroups();
    } catch (e: any) {
      toast.error("Failed to save group", { description: e?.message });
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (g: EmailGroup) => {
    setEditingId(g._id);
    // Align scope selection with group
    if (g.project_id) {
      setScope("project");
      if (g.organization_id) setSelectedOrgId(String(g.organization_id));
      setSelectedProjectId(String(g.project_id));
    } else {
      setScope("org");
      if (g.organization_id) setSelectedOrgId(String(g.organization_id));
      setSelectedProjectId("");
    }
    setForm({
      name: g.name,
      category: g.category || CATEGORIES[0],
      emails: g.emails || [],
      input: "",
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setForm({ name: "", category: CATEGORIES[0], emails: [], input: "" });
  };

  const removeGroup = async (g: EmailGroup) => {
    if (!confirm(`Delete group "${g.name}"?`)) return;
    try {
      await emailGroupsApi.remove(g._id);
      toast.success("Group deleted");
      await loadGroups();
    } catch (e: any) {
      toast.error("Failed to delete group", { description: e?.message });
    }
  };

  const loadSuggestions = React.useCallback(
    async (q?: string) => {
      try {
        setSuggestionsLoading(true);
        const res = await emailService.resolveRecipients({
          query: q,
          include_representatives: true,
          include_parties: true,
          organization_id: selectedOrgId || undefined,
          project_id:
            scope === "project" && selectedProjectId
              ? selectedProjectId
              : undefined,
        });
        setSuggestions(res);
      } catch {
        setSuggestions([]);
      } finally {
        setSuggestionsLoading(false);
      }
    },
    [selectedOrgId, selectedProjectId, scope]
  );

  return (
    <div className="p-6">
      <div className="max-w-5xl mx-auto space-y-8">
        <div>
          <h1 className="text-xl font-semibold">Email Groups</h1>
          <p className="text-sm text-muted-foreground">
            Create named groups of recipients to quickly prefill emails.
          </p>
        </div>

        {/* Scope selection */}
        <div className="rounded-lg border bg-white p-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
            <div>
              <Label>Scope</Label>
              <div className="flex items-center gap-6 mt-2">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="scope"
                    value="org"
                    checked={scope === "org"}
                    onChange={() => setScope("org")}
                  />
                  Organization Level
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="scope"
                    value="project"
                    checked={scope === "project"}
                    onChange={() => setScope("project")}
                  />
                  Project Level
                </label>
              </div>
            </div>

            <div>
              <Label>Organization</Label>
              <select
                className="border rounded-md h-9 px-2 w-full mt-1"
                value={selectedOrgId}
                onChange={(e) => setSelectedOrgId(e.target.value)}
              >
                <option value="">Select organization</option>
                {organizations.map((o) => (
                  <option key={o._id} value={o._id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <Label>Project</Label>
              <select
                className="border rounded-md h-9 px-2 w-full mt-1"
                value={selectedProjectId}
                onChange={(e) => setSelectedProjectId(e.target.value)}
                disabled={scope !== "project" || !selectedOrgId}
              >
                <option value="">
                  {!selectedOrgId ? "Select organization first" : "All / None"}
                </option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="text-xs text-muted-foreground">
            {scope === "org"
              ? "Creating organization-level groups (available across this organization)."
              : "Creating project-level groups (limited to the selected project)."}
          </div>
        </div>

        {/* Editor */}
        <div className="rounded-lg border bg-white p-5 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
            <div>
              <Label>Group Name</Label>
              <Input
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                placeholder="e.g., Project Team"
              />
            </div>
            <div>
              <Label>Category</Label>
              <select
                className="border rounded-md h-9 px-2 w-full"
                value={form.category}
                onChange={(e) =>
                  setForm((f) => ({ ...f, category: e.target.value }))
                }
              >
                {CATEGORIES.map((c) => (
                  <option value={c} key={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex gap-2">
              <Button onClick={handleSave} disabled={saving} className="flex-1">
                {saving ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Save className="w-4 h-4 mr-2" />
                )}
                {editingId ? "Update" : "Create"}
              </Button>
              {editingId && (
                <Button variant="ghost" onClick={cancelEdit}>
                  Cancel
                </Button>
              )}
            </div>
          </div>

          <div>
            <Label>Members (emails)</Label>
            <div className="mt-1 relative">
              <div className="flex flex-wrap gap-2 p-2 border rounded-md">
                {form.emails.map((e) => (
                  <span
                    key={e}
                    className="inline-flex items-center gap-1 bg-muted px-2 py-1 rounded text-sm"
                  >
                    {e}
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-foreground"
                      onClick={() => removeEmail(e)}
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
                <input
                  className="flex-1 min-w-[240px] outline-none"
                  placeholder="Type to search or paste emails"
                  value={form.input}
                  onChange={(e) => {
                    const v = e.target.value;
                    setForm((f) => ({ ...f, input: v }));
                    setSuggestionsOpen(true);
                    loadSuggestions(v);
                  }}
                  onFocus={() => setSuggestionsOpen(true)}
                  onBlur={() => {
                    const val = form.input.trim().replace(/,$/, "");
                    if (val) addEmail(val);
                    setForm((f) => ({ ...f, input: "" }));
                    setSuggestionsOpen(false);
                  }}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === ",") {
                      ev.preventDefault();
                      const val = form.input.trim().replace(/,$/, "");
                      if (val) addEmail(val);
                      setForm((f) => ({ ...f, input: "" }));
                      setSuggestionsOpen(false);
                    }
                  }}
                  onPaste={(ev) => {
                    const text = ev.clipboardData.getData("text");
                    const parts = text
                      .split(/[\s,;]+/)
                      .map((s) => s.trim())
                      .filter(Boolean);
                    if (parts.length > 1) ev.preventDefault();
                    parts.forEach(addEmail);
                    setForm((f) => ({ ...f, input: "" }));
                    setSuggestionsOpen(false);
                  }}
                />
              </div>
              {suggestionsOpen && (
                <div className="absolute z-50 mt-1 w-full bg-white border rounded-md shadow">
                  <div className="max-h-56 overflow-auto">
                    {suggestionsLoading && (
                      <div className="p-2 text-sm text-muted-foreground">
                        Loading...
                      </div>
                    )}
                    {!suggestionsLoading && suggestions.length === 0 && (
                      <div className="p-2 text-sm text-muted-foreground">
                        No suggestions found.
                      </div>
                    )}
                    {suggestions.map((s) => (
                      <button
                        type="button"
                        key={s.email}
                        className="w-full text-left px-3 py-2 hover:bg-muted/50"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          addEmail(s.email);
                          setForm((f) => ({ ...f, input: "" }));
                          setSuggestionsOpen(false);
                        }}
                      >
                        <div className="font-medium">{s.name}</div>
                        <div className="text-sm text-muted-foreground">
                          {s.email}
                        </div>
                        {s.organization && (
                          <div className="text-xs text-muted-foreground">
                            {s.organization}
                          </div>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* List */}
        <div className="rounded-lg border bg-white">
          <div className="p-5 border-b flex items-center justify-between">
            <div className="font-medium">Groups</div>
            <Button
              variant="ghost"
              onClick={() => {
                setEditingId(null);
                setForm({
                  name: "",
                  category: CATEGORIES[0],
                  emails: [],
                  input: "",
                });
              }}
            >
              <PlusCircle className="w-4 h-4 mr-2" /> New Group
            </Button>
          </div>
          <div className="divide-y">
            {loading ? (
              <div className="p-4 text-sm text-muted-foreground">
                Loading...
              </div>
            ) : groups.length === 0 ? (
              <div className="p-4 text-sm text-muted-foreground">
                No groups found.
              </div>
            ) : (
              groups.map((g) => (
                <div
                  key={g._id}
                  className="p-4 flex items-center justify-between"
                >
                  <div>
                    <div className="font-medium">{g.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {g.category || "Uncategorized"} • {g.emails?.length || 0}{" "}
                      member(s)
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => startEdit(g)}
                    >
                      <Edit2 className="w-4 h-4 mr-2" /> Edit
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => removeGroup(g)}
                    >
                      <Trash2 className="w-4 h-4 mr-2" /> Delete
                    </Button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default EmailGroupsPage;
