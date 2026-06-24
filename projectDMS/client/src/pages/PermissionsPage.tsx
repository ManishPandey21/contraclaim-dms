import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Shield,
  Users,
  FolderArchive,
  FileText,
  Building,
  Lock,
  Plus,
  FileSignature,
  Scale,
  ClipboardList,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import { useStepUp } from "@/hooks/useStepUp";
import { enhancedApi as api } from "@/services/enhanced-api";
import { ENTITY_PERMISSIONS } from "@/constants/entityPermissions";

const PermissionsPage = () => {
  const [selectedRole, setSelectedRole] = useState("all");
  const [roles, setRoles] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [permissionsMatrix, setPermissionsMatrix] = useState({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createRoleOpen, setCreateRoleOpen] = useState(false);
  const [newRole, setNewRole] = useState({
    name: "",
    description: "",
    level: "project",
    permissions: [],
  });
  const [editRoleOpen, setEditRoleOpen] = useState(false);
  const [editRole, setEditRole] = useState<any | null>(null);
  const { toast } = useToast();
  const { requestToken, StepUpDialog } = useStepUp();

  // Helper to normalize role id across potential API variations
  const getRoleId = (role: any) =>
    (role && (role as any)._id) ?? (role && (role as any).id);

  const deriveRoleLevel = (role: any) => {
    // If backend provides level, prefer it
    const raw = (role?.level || "").toString().toLowerCase();
    if (raw === "system" || raw === "organization" || raw === "project") {
      return raw;
    }

    // Fallback heuristics based on role id/name
    const source = `${role?._id || role?.id || ""} ${role?.name || ""}`.toLowerCase();
    if (source.includes("super")) return "system";
    if (source.includes("org")) return "organization";
    if (source.includes("project")) return "project";
    // Default to project-level to keep permissions scoped conservatively
    return "project";
  };

  const fetchRoles = async () => {
    try {
      const data = await api.getRoles();
      // Normalize missing level from backend so edit dialog shows the right selection
      const normalized = (data || []).map((role: any) => ({
        ...role,
        level: deriveRoleLevel(role),
      }));
      setRoles(normalized);
    } catch (error) {
      console.error("Error fetching roles:", error);
      toast({
        title: "Error",
        description: "Failed to fetch roles.",
        variant: "destructive",
      });
    }
  };

  const fetchPermissions = async () => {
    try {
      const data = await api.getPermissions();
      setPermissions(data);
    } catch (error) {
      console.error("Error fetching permissions:", error);
      toast({
        title: "Error",
        description: "Failed to fetch permissions.",
        variant: "destructive",
      });
    }
  };

  const fetchRolePermissions = async (roleId) => {
    try {
      const data = await api.getRolePermissions(roleId);
      // Extract permission identifiers; prefer stable permission 'name'
      const permissionIds = data.map((p: any) => p.name ?? p._id ?? p.id);

      // Update the permissions matrix for this role
      setPermissionsMatrix((prevMatrix) => ({
        ...prevMatrix,
        [roleId]: permissionIds,
      }));
    } catch (error) {
      console.error(`Error fetching permissions for role ${roleId}:`, error);
    }
  };

  // Handlers for create, edit, delete roles
  const openCreateRole = () => setCreateRoleOpen(true);

  const handleCreateRole = async () => {
    try {
      if (!newRole.name.trim()) {
        toast({
          title: "Validation",
          description: "Role name is required.",
          variant: "destructive",
        });
        return;
      }
      const payload = {
        name: newRole.name.trim(),
        description: newRole.description || "",
        level: newRole.level || "basic",
        permissions: newRole.permissions || [],
      };
      const stepUpToken = await requestToken(
        "platform.role.manage",
        "Confirm role creation",
        "Enter your password to create a role."
      );
      await api.createRole(payload as any, { stepUpToken });
      toast({
        title: "Role created",
        description: `${payload.name} was created.`,
      });
      setCreateRoleOpen(false);
      setNewRole({
        name: "",
        description: "",
        level: "project",
        permissions: [],
      });
      await fetchRoles();
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Failed to create role.",
        variant: "destructive",
      });
    }
  };

  const openEditRole = (role: any) => {
    setEditRole({
      _id: getRoleId(role),
      name: role.name || "",
      description: role.description || "",
      level: deriveRoleLevel(role),
    });
    setEditRoleOpen(true);
  };

  const handleUpdateRole = async () => {
    if (!editRole) return;
    try {
      const roleId = editRole._id;
      const payload: any = {
        name: editRole.name,
        description: editRole.description,
        level: editRole.level,
      };
      const stepUpToken = await requestToken(
        "platform.role.manage",
        "Confirm role update",
        "Enter your password to update this role."
      );
      await api.updateRole(roleId, payload, { stepUpToken });
      toast({
        title: "Role updated",
        description: `${payload.name || roleId} was updated.`,
      });
      setEditRoleOpen(false);
      setEditRole(null);
      await fetchRoles();
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Failed to update role.",
        variant: "destructive",
      });
    }
  };

  const handleDeleteRole = async (roleId: string) => {
    try {
      const stepUpToken = await requestToken(
        "platform.role.manage",
        "Confirm role deletion",
        "Enter your password to delete this role."
      );
      await api.deleteRole(roleId, { stepUpToken });
      toast({
        title: "Role deleted",
        description: `Role ${roleId} deleted.`,
      });
      await fetchRoles();
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Failed to delete role.",
        variant: "destructive",
      });
    }
  };

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      await fetchRoles();
      await fetchPermissions();
      setLoading(false);
    };

    fetchData();
    // Initial RBAC bootstrap intentionally runs once; mutations refresh roles explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // Fetch permissions for each role when roles are loaded
    if (roles.length > 0) {
      roles.forEach((role) => fetchRolePermissions(getRoleId(role)));
    }
  }, [roles]);

  const permissionGroups = [
    {
      id: "client-dms",
      name: "Client DMS",
      icon: FileText,
      permissions: [
        { id: "dms.document.view", name: "View Documents" },
        { id: "dms.document.upload", name: "Upload Documents" },
        { id: "dms.document.edit_metadata", name: "Edit Document Metadata" },
        { id: "dms.document.delete", name: "Delete Documents" },
        { id: "dms.document.download", name: "Download Documents" },
        { id: "dms.document.bulk_download", name: "Bulk Download Documents" },
        { id: "dms.document.link_reference", name: "Link References" },
        { id: "dms.status.update", name: "Update Status" },
        { id: "dms.comment.add", name: "Add Comments" },
        { id: "dms.dashboard.view", name: "View Dashboard" },
        { id: "dms.report.view", name: "View Reports" },
        { id: "dms.user.manage", name: "Manage Client Users" },
        { id: "dms.project.manage", name: "Manage Projects" },
        { id: "dms.audit.view", name: "View Audit Trail" },
        { id: "dms.admin", name: "DMS Admin" },
      ],
    },
    {
      id: "contract-controls",
      name: "Contract Controls",
      icon: FileSignature,
      permissions: [
        { id: "dms.contract.master.view", name: "View Contract Master" },
        { id: "dms.contract.master.manage", name: "Manage Contract Master" },
        { id: "dms.variation.view", name: "View Variations" },
        { id: "dms.variation.create", name: "Create Variations" },
        { id: "dms.variation.edit", name: "Edit Variations" },
        { id: "dms.variation.delete", name: "Delete Variations" },
        { id: "dms.variation.approve", name: "Approve Variations" },
        { id: "dms.variation.export", name: "Export Variations" },
        { id: "dms.bankguarantee.view", name: "View Bank Guarantees" },
        { id: "dms.bankguarantee.create", name: "Create Bank Guarantees" },
        { id: "dms.bankguarantee.edit", name: "Edit Bank Guarantees" },
        { id: "dms.bankguarantee.delete", name: "Delete Bank Guarantees" },
        { id: "dms.bankguarantee.export", name: "Export Bank Guarantees" },
        { id: "dms.bankguarantee.extend", name: "Extend Bank Guarantees" },
        { id: "dms.bankguarantee.release", name: "Release Bank Guarantees" },
        { id: "dms.ipc.view", name: "View IPC / Bills" },
        { id: "dms.ipc.create", name: "Create IPC / Bills" },
        { id: "dms.ipc.edit", name: "Edit IPC / Bills" },
        { id: "dms.ipc.delete", name: "Delete IPC / Bills" },
        { id: "dms.ipc.approve", name: "Approve IPC / Bills" },
        { id: "dms.ipc.export", name: "Export IPC / Bills" },
        { id: "dms.keydate.view", name: "View Key Dates" },
        { id: "dms.keydate.create", name: "Create Key Dates" },
        { id: "dms.keydate.edit", name: "Edit Key Dates" },
        { id: "dms.keydate.delete", name: "Delete Key Dates" },
        { id: "dms.keydate.manage", name: "Manage Key Dates" },
        { id: "dms.keydate.export", name: "Export Key Dates" },
        { id: "dms.keydate.eot_submit", name: "Submit EOT Applications" },
        { id: "dms.keydate.eot_approve", name: "Approve EOT Applications" },
        { id: "dms.keydate.achievement", name: "Record Key-date Achievement" },
      ],
    },
    {
      id: "claims-appraisal",
      name: "Claims & Appraisal",
      icon: Scale,
      permissions: [
        { id: "dms.claim.view", name: "View Claims" },
        { id: "dms.claim.create", name: "Create Claims" },
        { id: "dms.claim.edit", name: "Edit Claims" },
        { id: "dms.claim.delete", name: "Delete Claims" },
        { id: "dms.claim.manage", name: "Manage Claims" },
        { id: "dms.claim.assess", name: "Assess Claims (AI)" },
        { id: "dms.contract.appraisal.view", name: "View Contract Appraisal" },
        { id: "dms.contract.appraisal.generate", name: "Generate Contract Appraisal" },
        { id: "dms.contract.appraisal.edit", name: "Edit Contract Appraisal" },
        { id: "dms.contract.appraisal.approve", name: "Approve Contract Appraisal" },
        { id: "dms.contract.appraisal.reject", name: "Reject Contract Appraisal" },
        { id: "dms.contract.appraisal.export", name: "Export Contract Appraisal" },
        { id: "dms.contract.appraisal.create_registers", name: "Create Registers from Appraisal" },
      ],
    },
    {
      id: "tasks",
      name: "Tasks",
      icon: ClipboardList,
      permissions: [
        { id: "dms.task.view", name: "View Tasks" },
        { id: "dms.task.create", name: "Create Tasks" },
        { id: "dms.task.edit", name: "Edit Tasks" },
        { id: "dms.task.delete", name: "Delete Tasks" },
        { id: "dms.task.manage", name: "Manage Tasks" },
      ],
    },
    {
      id: "drafting",
      name: "ContraClaim Expert Drafting",
      icon: Shield,
      permissions: [
        { id: "drafting.request.view", name: "View Drafting Requests" },
        { id: "drafting.request.create", name: "Create Drafting Requests" },
        { id: "drafting.request.accept", name: "Accept Drafting Requests" },
        { id: "drafting.request.assign", name: "Assign Drafting Work" },
        { id: "drafting.draft.create", name: "Create Drafts" },
        { id: "drafting.draft.edit", name: "Edit Drafts" },
        { id: "drafting.draft.submit_for_review", name: "Submit Draft for Review" },
        { id: "drafting.review.perform", name: "Perform Review" },
        { id: "drafting.review.approve", name: "Approve Review" },
        { id: "drafting.review.return_for_revision", name: "Return for Revision" },
        { id: "drafting.final.view", name: "View Final Drafts" },
        { id: "drafting.audit.view", name: "View Drafting Audit" },
        { id: "drafting.admin", name: "Drafting Admin" },
      ],
    },
    {
      id: "billing",
      name: "Billing & Entitlements",
      icon: Lock,
      permissions: [
        { id: "billing.plan.view", name: "View Plans" },
        { id: "billing.plan.manage", name: "Manage Plans" },
        { id: "subscription.entitlement.manage", name: "Manage Entitlements" },
        { id: "subscription.usage.view", name: "View Usage" },
        { id: "subscription.archive_access", name: "Archive Access" },
        { id: "subscription.offboarding_export", name: "Offboarding Export" },
      ],
    },
    {
      id: "legacy-documents",
      name: "Legacy Document Permissions",
      icon: FileText,
      permissions: [
        { id: "documents:read", name: "View Documents" },
        { id: "documents:create", name: "Draft Documents" },
        { id: "documents:update", name: "Edit Documents" },
        { id: "documents:delete", name: "Delete Documents" },
        { id: "documents:approve", name: "Approve Documents" },
        { id: "documents:share", name: "Share Documents" },
        { id: "documents:upload", name: "Upload Documents" },
        { id: "documents:download_all", name: "Download Complete Project Documents" },
        { id: "documents:comment", name: "Add Inputs/Comments" },
      ],
    },
    {
      id: "projects",
      name: "Projects",
      icon: FolderArchive,
      permissions: [
        { id: ENTITY_PERMISSIONS.projects.read, name: "View Projects" },
        { id: ENTITY_PERMISSIONS.projects.create, name: "Create Projects" },
        { id: ENTITY_PERMISSIONS.projects.update, name: "Edit Projects" },
        { id: ENTITY_PERMISSIONS.projects.delete, name: "Delete Projects" },
        { id: "projects:assign", name: "Assign Members" },
      ],
    },
    {
      id: "users",
      name: "Users & Roles",
      icon: Users,
      permissions: [
        { id: "users:read", name: "View Users" },
        { id: "users:create", name: "Create Users" },
        { id: "users:update", name: "Edit Users" },
        { id: "users:delete", name: "Delete Users" },
        { id: "roles:assign", name: "Assign/Reset Roles" },
      ],
    },
    {
      id: "organizations",
      name: "Organizations",
      icon: Building,
      permissions: [
        { id: ENTITY_PERMISSIONS.organizations.read, name: "View Organizations" },
        { id: ENTITY_PERMISSIONS.organizations.create, name: "Create Organizations" },
        { id: ENTITY_PERMISSIONS.organizations.update, name: "Edit Organizations" },
        { id: ENTITY_PERMISSIONS.organizations.delete, name: "Delete Organizations" },
      ],
    },
  ];

  const handleCheckboxChange = (roleId, permissionId, checked) => {
    setPermissionsMatrix((prevMatrix) => {
      const updatedRolePermissions = checked
        ? [...(prevMatrix[roleId] || []), permissionId]
        : (prevMatrix[roleId] || []).filter((id) => id !== permissionId);

      return {
        ...prevMatrix,
        [roleId]: Array.from(new Set(updatedRolePermissions)),
      };
    });
  };

  const handleSaveChanges = async () => {
    try {
      setSaving(true);
      const targets =
        selectedRole === "all"
          ? roles
          : roles.filter((r) => getRoleId(r) === selectedRole);
      const stepUpToken = await requestToken(
        "platform.role.manage",
        "Confirm permission changes",
        "Enter your password to save role permission changes."
      );

      await Promise.all(
        targets.map((role) => {
          const roleId = getRoleId(role);
          const perms = permissionsMatrix[roleId] || [];
          return api.updateRole(roleId, { permissions: perms } as any, {
            stepUpToken,
          });
        })
      );

      toast({
        title: "Permissions saved",
        description: "Role permissions have been updated.",
      });
    } catch (error: any) {
      toast({
        title: "Save failed",
        description: error?.message || "Unable to save permissions.",
        variant: "destructive",
      });
    } finally {
      setSaving(false);
      // Refresh to reflect backend state
      if (roles.length > 0) {
        roles.forEach((role) => fetchRolePermissions(getRoleId(role)));
      }
    }
  };

  const getRoleLevelBadge = (level) => {
    switch (level) {
      case "system":
        return <Badge className="bg-purple-500">System</Badge>;
      case "organization":
        return <Badge className="bg-blue-500">Organization</Badge>;
      case "project":
        return <Badge className="bg-green-500">Project</Badge>;
      default:
        return null;
    }
  };

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      {StepUpDialog}
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Permissions Management</h1>
        <Button onClick={handleSaveChanges} disabled={saving}>
          {saving ? "Saving..." : "Save Changes"}
        </Button>
      </div>

      <Tabs defaultValue="roles" className="mb-8">
        <TabsList className="grid w-full md:w-[400px] grid-cols-2">
          <TabsTrigger value="roles">Roles</TabsTrigger>
          <TabsTrigger value="permissions">Permissions</TabsTrigger>
        </TabsList>

        <TabsContent value="roles" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Role Management</CardTitle>
              <CardDescription>Create and manage user roles</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-4">
                {roles.map((role) => (
                  <Card
                    key={getRoleId(role)}
                    className="w-full md:w-[calc(50%-1rem)] lg:w-[calc(33.333%-1rem)]"
                  >
                    <CardHeader className="pb-2">
                      <div className="flex justify-between items-start">
                        <div>
                          <div className="flex items-center gap-2 mb-1">
                            <CardTitle className="text-lg">
                              {role.name}
                            </CardTitle>
                            {getRoleLevelBadge(role.level)}
                          </div>
                          <CardDescription>{role.description}</CardDescription>
                        </div>
                        <Shield className="text-primary h-5 w-5" />
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="flex justify-end gap-2 mt-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openEditRole(role)}
                        >
                          Edit
                        </Button>
                        {getRoleId(role) !== "superadmin" && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-destructive"
                            onClick={() => handleDeleteRole(getRoleId(role))}
                          >
                            Delete
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}

                <Card className="w-full md:w-[calc(50%-1rem)] lg:w-[calc(33.333%-1rem)] border-dashed border-2 flex items-center justify-center">
                  <CardContent
                    className="p-6 text-center cursor-pointer hover:bg-accent"
                    onClick={() => setCreateRoleOpen(true)}
                  >
                    <div className="flex flex-col items-center gap-2">
                      <div className="rounded-full bg-primary/10 p-3">
                        <Shield className="text-primary h-6 w-6" />
                      </div>
                      <CardTitle className="text-lg">Create New Role</CardTitle>
                      <CardDescription>
                        Add a custom role with specific permissions
                      </CardDescription>
                    </div>
                  </CardContent>
                </Card>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="permissions" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Permission Matrix</CardTitle>
              <CardDescription>
                Configure permissions for each role
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center justify-between mb-6">
                <div className="flex gap-2 items-center">
                  <p className="text-sm text-muted-foreground">
                    Roles by level:
                  </p>
                  <div className="flex gap-2">
                    <Badge className="bg-purple-500">System</Badge>
                    <Badge className="bg-blue-500">Organization</Badge>
                    <Badge className="bg-green-500">Project</Badge>
                  </div>
                </div>
                <Select value={selectedRole} onValueChange={setSelectedRole}>
                  <SelectTrigger className="w-48">
                    <SelectValue placeholder="Select a role" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Roles</SelectItem>
                    {roles.map((role) => (
                      <SelectItem key={getRoleId(role)} value={getRoleId(role)}>
                        {role.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-8">
                {loading ? (
                  <p>Loading permissions...</p>
                ) : (
                  permissionGroups.map((group) => (
                    <div key={group.id}>
                      <div className="flex items-center gap-2 mb-4">
                        <group.icon className="h-5 w-5 text-primary" />
                        <h3 className="text-lg font-medium">{group.name}</h3>
                      </div>

                      <div className="rounded-md border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead className="w-[300px]">
                                Permission
                              </TableHead>
                              {(selectedRole === "all"
                                ? roles
                                : roles.filter(
                                    (r) => getRoleId(r) === selectedRole
                                  )
                              ).map((role) => (
                                <TableHead
                                  key={getRoleId(role)}
                                  className="text-center whitespace-nowrap"
                                >
                                  <div className="flex flex-col items-center">
                                    <span>{role.name}</span>
                                    {/* <span className="text-xs">
                                    {getRoleLevelBadge(role.level)}
                                  </span> */}
                                  </div>
                                </TableHead>
                              ))}
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {group.permissions.map((permission) => (
                              <TableRow key={permission.id}>
                                <TableCell className="font-medium">
                                  {permission.name}
                                </TableCell>
                                {(selectedRole === "all"
                                  ? roles
                                  : roles.filter(
                                      (r) => getRoleId(r) === selectedRole
                                    )
                                ).map((role) => (
                                  <TableCell
                                    key={getRoleId(role)}
                                    className="text-center"
                                  >
                                    <Checkbox
                                      checked={
                                        permissionsMatrix[
                                          getRoleId(role)
                                        ]?.includes(permission.id) ?? false
                                      }
                                      onCheckedChange={(checked) =>
                                        handleCheckboxChange(
                                          getRoleId(role),
                                          permission.id,
                                          checked
                                        )
                                      }
                                      className="mx-auto"
                                    />
                                  </TableCell>
                                ))}
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Create Role Dialog */}
      <Dialog open={createRoleOpen} onOpenChange={setCreateRoleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Role</DialogTitle>
            <DialogDescription>Define a new role.</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="new-role-name">Name</Label>
              <Input
                id="new-role-name"
                value={newRole.name}
                onChange={(e) =>
                  setNewRole({ ...newRole, name: e.target.value })
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-role-level">Level</Label>
              <Select
                value={newRole.level}
                onValueChange={(v) => setNewRole({ ...newRole, level: v })}
              >
                <SelectTrigger id="new-role-level">
                  <SelectValue placeholder="Select level" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="system">System</SelectItem>
                  <SelectItem value="organization">Organization</SelectItem>
                  <SelectItem value="project">Project</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="new-role-desc">Description</Label>
              <Textarea
                id="new-role-desc"
                value={newRole.description}
                onChange={(e) =>
                  setNewRole({ ...newRole, description: e.target.value })
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateRoleOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreateRole}>Create</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Role Dialog */}
      <Dialog open={editRoleOpen} onOpenChange={setEditRoleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Role</DialogTitle>
          </DialogHeader>
          {editRole && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="edit-role-name">Name</Label>
                <Input
                  id="edit-role-name"
                  value={editRole.name}
                  onChange={(e) =>
                    setEditRole({ ...editRole, name: e.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-role-level">Level</Label>
                <Select
                  value={editRole.level}
                  onValueChange={(v) => setEditRole({ ...editRole, level: v })}
                >
                  <SelectTrigger id="edit-role-level">
                    <SelectValue placeholder="Select level" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="system">System</SelectItem>
                    <SelectItem value="organization">Organization</SelectItem>
                    <SelectItem value="project">Project</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit-role-desc">Description</Label>
                <Textarea
                  id="edit-role-desc"
                  value={editRole.description}
                  onChange={(e) =>
                    setEditRole({ ...editRole, description: e.target.value })
                  }
                />
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditRoleOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleUpdateRole}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PermissionsPage;
