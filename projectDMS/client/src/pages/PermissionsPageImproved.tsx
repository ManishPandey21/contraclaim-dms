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
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import { enhancedApi as api } from "@/services/enhanced-api";
import { Alert, AlertDescription } from "@/components/ui/alert";

const PermissionsPage = () => {
  const [selectedRole, setSelectedRole] = useState("all");
  const [roles, setRoles] = useState([]);
  const [permissions, setPermissions] = useState([]);
  const [permissionsMatrix, setPermissionsMatrix] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [debugInfo, setDebugInfo] = useState<any>(null);
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

  // Helper to normalize role id across potential API variations
  const getRoleId = (role: any) =>
    (role && (role as any)._id) ?? (role && (role as any).id);

  const fetchRoles = async () => {
    try {
      console.log("🔄 Fetching roles...");
      const data = await api.getRoles();
      console.log("✅ Roles fetched successfully:", data);
      setRoles(data);
      setError(null);
    } catch (error: any) {
      console.error("❌ Error fetching roles:", error);
      const errorMessage = error?.message || error?.detail || "Failed to fetch roles";
      setError(`Roles Error: ${errorMessage}`);
      setDebugInfo({
        endpoint: "/api/roles",
        error: error,
        timestamp: new Date().toISOString()
      });
      toast({
        title: "Error",
        description: `Failed to fetch roles: ${errorMessage}`,
        variant: "destructive",
      });
    }
  };

  const fetchPermissions = async () => {
    try {
      console.log("🔄 Fetching permissions...");
      const data = await api.getPermissions();
      console.log("✅ Permissions fetched successfully:", data);
      setPermissions(data);
      setError(null);
    } catch (error: any) {
      console.error("❌ Error fetching permissions:", error);
      const errorMessage = error?.message || error?.detail || "Failed to fetch permissions";
      setError(`Permissions Error: ${errorMessage}`);
      setDebugInfo({
        endpoint: "/api/permissions",
        error: error,
        timestamp: new Date().toISOString()
      });
      toast({
        title: "Error",
        description: `Failed to fetch permissions: ${errorMessage}`,
        variant: "destructive",
      });
    }
  };

  const fetchRolePermissions = async (roleId) => {
    try {
      console.log(`🔄 Fetching permissions for role ${roleId}...`);
      const data = await api.getRolePermissions(roleId);
      console.log(`✅ Role permissions fetched for ${roleId}:`, data);
      
      // Extract permission IDs (support both id and _id keys)
      const permissionIds = data.map((p: any) => p._id ?? p.id);

      // Update the permissions matrix for this role
      setPermissionsMatrix((prevMatrix) => ({
        ...prevMatrix,
        [roleId]: permissionIds,
      }));
    } catch (error: any) {
      console.error(`❌ Error fetching permissions for role ${roleId}:`, error);
      const errorMessage = error?.message || error?.detail || "Failed to fetch role permissions";
      toast({
        title: "Warning",
        description: `Failed to fetch permissions for role ${roleId}: ${errorMessage}`,
        variant: "destructive",
      });
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
      console.log("🔄 Creating role:", payload);
      await api.createRole(payload as any);
      console.log("✅ Role created successfully");
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
      console.error("❌ Error creating role:", error);
      const errorMessage = error?.message || error?.detail || "Failed to create role";
      toast({
        title: "Error",
        description: errorMessage,
        variant: "destructive",
      });
    }
  };

  const openEditRole = (role: any) => {
    setEditRole({
      _id: getRoleId(role),
      name: role.name || "",
      description: role.description || "",
      level: role.level || "basic",
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
      console.log("🔄 Updating role:", roleId, payload);
      await api.updateRole(roleId, payload);
      console.log("✅ Role updated successfully");
      toast({
        title: "Role updated",
        description: `${payload.name || roleId} was updated.`,
      });
      setEditRoleOpen(false);
      setEditRole(null);
      await fetchRoles();
    } catch (error: any) {
      console.error("❌ Error updating role:", error);
      const errorMessage = error?.message || error?.detail || "Failed to update role";
      toast({
        title: "Error",
        description: errorMessage,
        variant: "destructive",
      });
    }
  };

  const handleDeleteRole = async (roleId: string) => {
    try {
      console.log("🔄 Deleting role:", roleId);
      await api.deleteRole(roleId);
      console.log("✅ Role deleted successfully");
      toast({
        title: "Role deleted",
        description: `Role ${roleId} deleted.`,
      });
      await fetchRoles();
    } catch (error: any) {
      console.error("❌ Error deleting role:", error);
      const errorMessage = error?.message || error?.detail || "Failed to delete role";
      toast({
        title: "Error",
        description: errorMessage,
        variant: "destructive",
      });
    }
  };

  const handleRefresh = async () => {
    setError(null);
    setDebugInfo(null);
    await fetchData();
  };

  const fetchData = async () => {
    setLoading(true);
    console.log("🔄 Starting data fetch...");
    
    try {
      await Promise.all([fetchRoles(), fetchPermissions()]);
      console.log("✅ All data fetched successfully");
    } catch (error) {
      console.error("❌ Error in fetchData:", error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    // Fetch permissions for each role when roles are loaded
    if (roles.length > 0) {
      console.log("🔄 Fetching permissions for all roles...");
      roles.forEach((role) => fetchRolePermissions(getRoleId(role)));
    }
  }, [roles]);

  const permissionGroups = [
    {
      id: "documents",
      name: "Documents",
      icon: FileText,
      permissions: [
        { id: "docs:view", name: "View Documents" },
        { id: "docs:create", name: "Draft Documents" },
        { id: "docs:edit", name: "Edit Documents" },
        { id: "docs:delete", name: "Delete Documents" },
        { id: "docs:approve", name: "Approve Documents" },
        { id: "docs:share", name: "Share Documents" },
        { id: "docs:upload", name: "Upload Documents" },
        { id: "docs:comment", name: "Add Inputs/Comments" },
      ],
    },
    {
      id: "projects",
      name: "Projects",
      icon: FolderArchive,
      permissions: [
        { id: "projects:view", name: "View Projects" },
        { id: "projects:create", name: "Create Projects" },
        { id: "projects:edit", name: "Edit Projects" },
        { id: "projects:delete", name: "Delete Projects" },
        { id: "projects:assign", name: "Assign Members" },
      ],
    },
    {
      id: "users",
      name: "Users & Roles",
      icon: Users,
      permissions: [
        { id: "users:view", name: "View Users" },
        { id: "users:create", name: "Create Users" },
        { id: "users:edit", name: "Edit Users" },
        { id: "users:delete", name: "Delete Users" },
        { id: "roles:assign", name: "Assign/Reset Roles" },
      ],
    },
    {
      id: "organizations",
      name: "Organizations",
      icon: Building,
      permissions: [
        { id: "orgs:view", name: "View Organizations" },
        { id: "orgs:create", name: "Create Organizations" },
        { id: "orgs:edit", name: "Edit Organizations" },
        { id: "orgs:delete", name: "Delete Organizations" },
      ],
    },
  ];

  const handleCheckboxChange = async (roleId, permissionId, checked) => {
    try {
      console.log(`🔄 ${checked ? 'Adding' : 'Removing'} permission ${permissionId} ${checked ? 'to' : 'from'} role ${roleId}`);
      
      if (checked) {
        await api.addRolePermission(roleId, permissionId);
      } else {
        await api.removeRolePermission(roleId, permissionId);
      }

      console.log("✅ Permission updated successfully");

      // Update local state to reflect the change
      setPermissionsMatrix((prevMatrix) => {
        const updatedRolePermissions = checked
          ? [...(prevMatrix[roleId] || []), permissionId]
          : (prevMatrix[roleId] || []).filter((id) => id !== permissionId);

        return {
          ...prevMatrix,
          [roleId]: updatedRolePermissions,
        };
      });
    } catch (error: any) {
      console.error("❌ Error updating permission:", error);
      const errorMessage = error?.message || error?.detail || "Failed to update permission";
      toast({
        title: "Error",
        description: `Failed to ${checked ? "add" : "remove"} permission: ${errorMessage}`,
        variant: "destructive",
      });
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
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Permissions Management</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleRefresh} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
          <Button>Save Changes</Button>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <Alert className="mb-6" variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            <div className="flex justify-between items-center">
              <span>{error}</span>
              <Button variant="outline" size="sm" onClick={handleRefresh}>
                Retry
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      )}

      {/* Debug Info */}
      {debugInfo && (
        <Alert className="mb-6">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            <details>
              <summary className="cursor-pointer font-medium">Debug Information</summary>
              <pre className="mt-2 text-xs bg-gray-100 p-2 rounded overflow-auto">
                {JSON.stringify(debugInfo, null, 2)}
              </pre>
            </details>
          </AlertDescription>
        </Alert>
      )}

      {/* Loading State */}
      {loading && (
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="h-6 w-6 animate-spin mr-2" />
          <span>Loading permissions data...</span>
        </div>
      )}

      {/* Data Summary */}
      <div className="mb-6 p-4 bg-gray-50 rounded-lg">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="font-medium">Roles:</span> {roles.length}
          </div>
          <div>
            <span className="font-medium">Permissions:</span> {permissions.length}
          </div>
          <div>
            <span className="font-medium">Matrix Entries:</span> {Object.keys(permissionsMatrix).length}
          </div>
          <div>
            <span className="font-medium">Status:</span> 
            <span className={`ml-1 ${error ? 'text-red-600' : 'text-green-600'}`}>
              {error ? 'Error' : 'OK'}
            </span>
          </div>
        </div>
      </div>

      <Tabs defaultValue="roles" className="mb-8">
        <TabsList className="grid w-full md:w-[400px] grid-cols-2">
          <TabsTrigger value="roles">Roles ({roles.length})</TabsTrigger>
          <TabsTrigger value="permissions">Permissions ({permissions.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="roles" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Role Management</CardTitle>
              <CardDescription>Create and manage user roles</CardDescription>
            </CardHeader>
            <CardContent>
              {roles.length === 0 && !loading ? (
                <div className="text-center py-8 text-muted-foreground">
                  <Shield className="h-12 w-12 mx-auto mb-4 opacity-50" />
                  <p>No roles found. Try refreshing or check your connection.</p>
                </div>
              ) : (
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
              )}
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
                  <div className="flex items-center justify-center py-8">
                    <RefreshCw className="h-6 w-6 animate-spin mr-2" />
                    <span>Loading permissions...</span>
                  </div>
                ) : roles.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Lock className="h-12 w-12 mx-auto mb-4 opacity-50" />
                    <p>No roles available to configure permissions.</p>
                  </div>
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
