import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Search, MoreHorizontal, UserPlus } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { enhancedApi as api } from "@/services/enhanced-api";
import useRBAC from "@/hooks/useRBAC";
import { useStepUp } from "@/hooks/useStepUp";

interface User {
  // id may be returned as "id" or "_id" depending on endpoint/model
  id?: string;
  _id?: string;
  username: string;
  first_name?: string;
  last_name?: string;
  email: string;
  roles: string[];
  organization_id?: string;
  organizations?: string[];
  projects?: string[];
  disabled?: boolean;
  avatar?: string;
  // New display fields from backend for convenience
  organization_name?: string | null;
  project_names?: string[];
}

interface Organization {
  _id: string;
  name: string;
}

interface Project {
  _id: string;
  name: string;
  organization_id: string;
}

const UsersPage = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [users, setUsers] = useState<User[]>([]);
  const getUserId = (u: User) => u.id || (u as any)._id || "";
  const [open, setOpen] = useState(false);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(false);
  const initialFormState = {
    username: "",
    firstName: "",
    lastName: "",
    email: "",
    role: "",
    accessType: "superUser",
    organization: "",
    project: "",
  };
  const [formData, setFormData] = useState(initialFormState);
  const [usernameManuallyEdited, setUsernameManuallyEdited] =
    useState<boolean>(false);
  const { toast } = useToast();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const { can, loading: rbacLoading } = useRBAC();
  const { requestToken, StepUpDialog } = useStepUp();
  const [accessDenied, setAccessDenied] = useState<boolean>(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetTargetUser, setResetTargetUser] = useState<User | null>(null);
  const [resetPassword, setResetPassword] = useState("");
  const [resetConfirm, setResetConfirm] = useState("");

  const DEFAULT_TEMP_PASSWORD = "TempPass123!";

  const sanitizeUsernameValue = (value: string) =>
    value
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "")
      .replace(/[^a-z0-9_.-]/g, "");

  const generateUsername = (
    firstName: string,
    lastName: string,
    email: string
  ) => {
    const primary = `${firstName} ${lastName}`.trim();
    const candidateFromName = sanitizeUsernameValue(
      primary.replace(/\s+/g, ".")
    );
    if (candidateFromName) return candidateFromName;

    const emailLocal = email.split("@")[0] || "";
    const candidateFromEmail = sanitizeUsernameValue(emailLocal);
    if (candidateFromEmail) return candidateFromEmail;

    return sanitizeUsernameValue(firstName) || "";
  };

  const resetForm = () => {
    setFormData({ ...initialFormState });
    setUsernameManuallyEdited(false);
  };

  const normalizeRoleValue = (role?: string) =>
    (role || "").toLowerCase().replace(/[\s_-]+/g, "");

  const deriveAccessAndRoleFromBackendRole = (role?: string) => {
    const normalized = normalizeRoleValue(role);
    switch (normalized) {
      case "superadmin":
        return { accessType: "superUser" as const, role: "Super Admin" };
      case "orgadmin":
      case "organizationadmin":
        return { accessType: "organization" as const, role: "Organization Admin" };
      case "orguser":
      case "organizationuser":
        return { accessType: "organization" as const, role: "Organization User" };
      case "projectadmin":
        return { accessType: "project" as const, role: "Project Admin" };
      case "projectuser":
        return { accessType: "project" as const, role: "Project User" };
      case "doccontroller":
      case "documentcontroller":
        return { accessType: "project" as const, role: "Document Controller" };
      default:
        return { accessType: "superUser" as const, role: role || "" };
    }
  };

  useEffect(() => {
    const fetchOrganizationsAndProjects = async () => {
      try {
        const orgData = await api.getOrganizations();
        setOrganizations(orgData);
        const projData = await api.getProjects();
        setProjects(projData);
      } catch (error: any) {
        toast({
          title: "Error",
          description:
            error.message || "Failed to fetch organizations or projects.",
          variant: "destructive",
        });
      } finally {
        setIsLoading(false);
      }
    };
    fetchOrganizationsAndProjects();
  }, [toast]);

  useEffect(() => {
    const fetchUsers = async () => {
      setLoading(true);
      try {
        const data = await api.getUsers();
        setUsers(data);
      } catch (error: any) {
        const msg = (error && error.message) || "Failed to fetch users.";
        // Only flag access denied when backend explicitly returns permission denial indicators.
        if (/403|not enough permissions/i.test(String(msg))) {
          setAccessDenied(true);
        } else {
          // Do not permanently block the page on transient/network errors
          setAccessDenied(false);
        }
        toast({
          title: "Error",
          description: msg,
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    };

    fetchUsers();
  }, [toast]);

  const normalizedQuery = searchQuery.trim().toLowerCase();
  const filteredUsers = users.filter((user) => {
    if (!normalizedQuery) {
      return true;
    }
    const usernameMatch = user.username
      ?.toLowerCase()
      .includes(normalizedQuery);
    const emailMatch = user.email?.toLowerCase().includes(normalizedQuery);
    const nameMatch = `${user.first_name || ""} ${user.last_name || ""}`
      .trim()
      .toLowerCase()
      .includes(normalizedQuery);
    return Boolean(usernameMatch || emailMatch || nameMatch);
  });

  const handleInputChange = (field: string, value: string) => {
    if (field === "username") {
      const sanitized = sanitizeUsernameValue(value);
      setUsernameManuallyEdited(sanitized.length > 0);
      setFormData((prev) => ({ ...prev, username: sanitized }));
      return;
    }

    setFormData((prev) => {
      const next = { ...prev, [field]: value };
      if (
        !usernameManuallyEdited &&
        (field === "firstName" || field === "lastName" || field === "email")
      ) {
        const candidate = generateUsername(
          field === "firstName" ? value : next.firstName,
          field === "lastName" ? value : next.lastName,
          field === "email" ? value : next.email
        );
        if (candidate) {
          next.username = candidate;
        }
      }
      if (field === "role") {
        const normalizedRole = normalizeRoleValue(value);
        if (["superadmin", "superuser"].includes(normalizedRole)) {
          next.accessType = "superUser";
        } else if (["orgadmin", "orguser"].includes(normalizedRole)) {
          next.accessType = "organization";
        } else if (
          ["projectadmin", "projectuser", "doccontroller"].includes(normalizedRole)
        ) {
          next.accessType = "project";
        }
      }
      return next;
    });
  };

  const mapAccessTypeAndRoleToBackendRole = () => {
    const normalizedRole = normalizeRoleValue(formData.role);

    if (normalizedRole === "superadmin") return "superadmin";

    if (formData.accessType === "organization") {
      if (normalizedRole === "orgadmin") return "orgadmin";
      if (normalizedRole === "orguser") return "orguser";
      return normalizedRole.includes("admin") ? "orgadmin" : "orguser";
    }

    if (formData.accessType === "project") {
      if (normalizedRole === "projectadmin") return "projectadmin";
      if (normalizedRole === "projectuser") return "projectuser";
      if (normalizedRole === "doccontroller" || normalizedRole.includes("document"))
        return "doccontroller";
      return normalizedRole.includes("admin") ? "projectadmin" : "projectuser";
    }

    // Default to superadmin when access type is superUser or unset
    if (normalizedRole) return normalizedRole;
    return "superadmin";
  };

  const handleAddUser = async () => {
    const firstName = formData.firstName.trim();
    const lastNameRaw = formData.lastName.trim();
    const email = formData.email.trim();
    if (!firstName) {
      toast({
        title: "Validation",
        description: "First name is required.",
        variant: "destructive",
      });
      return;
    }
    const lastName = lastNameRaw || firstName;
    if (!email) {
      toast({
        title: "Validation",
        description: "Email is required.",
        variant: "destructive",
      });
      return;
    }

    const preferredUsername =
      formData.username || `${firstName}.${lastName}` || firstName;
    const sanitizedUsername = sanitizeUsernameValue(preferredUsername);
    if (!sanitizedUsername) {
      toast({
        title: "Validation",
        description:
          "Username must include at least one alphanumeric character.",
        variant: "destructive",
      });
      return;
    }

    setLoading(true);
    try {
      const backendRole = mapAccessTypeAndRoleToBackendRole();
      const newUser = await api.createUser({
        username: sanitizedUsername,
        first_name: firstName,
        last_name: lastName,
        email,
        password: DEFAULT_TEMP_PASSWORD,
        roles: [backendRole],
        organization_id: formData.organization || undefined,
        projects: formData.project ? [formData.project] : [],
        organizations: formData.organization ? [formData.organization] : [],
        permissions: [],
        preferences: {
          emailNotifications: true,
          sharingAlerts: true,
          theme: "light",
          language: "en",
        },
      });
      setUsers((prevUsers) => [...prevUsers, newUser as User]);
      toast({
        title: "User Created",
        description: `Successfully created user: ${firstName} ${lastName}`,
      });
      setOpen(false);
      resetForm();
      setEditUser(null);
    } catch (error: any) {
      toast({
        title: "Error",
        description: error.message || "Failed to create user.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateUser = async () => {
    if (!editUser) return;
    const firstName = formData.firstName.trim();
    const lastNameRaw = formData.lastName.trim();
    if (!firstName) {
      toast({
        title: "Validation",
        description: "First name is required.",
        variant: "destructive",
      });
      return;
    }
    const lastName = lastNameRaw || firstName;
    const sanitizedUsername = sanitizeUsernameValue(
      formData.username || `${firstName}.${lastName}`
    );
    if (!sanitizedUsername) {
      toast({
        title: "Validation",
        description:
          "Username must include at least one alphanumeric character.",
        variant: "destructive",
      });
      return;
    }

    setLoading(true);
    try {
      const backendRole = mapAccessTypeAndRoleToBackendRole();
      const updatedUser = await api.updateUser(getUserId(editUser), {
        username: sanitizedUsername,
        first_name: firstName,
        last_name: lastName,
        roles: [backendRole],
        organization_id: formData.organization || undefined,
        projects: formData.project ? [formData.project] : [],
      });
      setUsers((prevUsers) =>
        prevUsers.map((u) =>
          getUserId(u) === (updatedUser as any).id ||
          getUserId(u) === (updatedUser as any)._id
            ? (updatedUser as unknown as User)
            : u
        )
      );
      toast({
        title: "User Updated",
        description: `Successfully updated user: ${formData.username}`,
      });
      setOpen(false);
      setEditUser(null);
      resetForm();
    } catch (error: any) {
      toast({
        title: "Error",
        description: error.message || "Failed to update user.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteUser = async (userId: string) => {
    setLoading(true);
    try {
      await api.deleteUser(userId);
      setUsers((prevUsers) => prevUsers.filter((u) => getUserId(u) !== userId));
      toast({
        title: "User Deleted",
        description: "User deleted successfully.",
      });
    } catch (error: any) {
      toast({
        title: "Error",
        description: error.message || "Failed to delete user.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  // Lock/unlock are step-up gated server-side (users:lock / users:unlock).
  // requestToken pops the password dialog; a cancel rejects with "cancelled".
  const handleLockToggle = async (userId: string, lock: boolean) => {
    setLoading(true);
    try {
      const action = lock ? "users.lock" : "users.unlock";
      const token = await requestToken(
        action,
        lock ? "Lock account" : "Unlock account",
        "Enter your password to confirm this account action.",
      );
      if (lock) {
        await api.lockUser(userId, { stepUpToken: token });
      } else {
        await api.unlockUser(userId, { stepUpToken: token });
      }
      toast({
        title: lock ? "Account Locked" : "Account Unlocked",
        description: `User account ${lock ? "locked" : "unlocked"} successfully.`,
      });
    } catch (error: any) {
      if (String(error?.message || "").toLowerCase().includes("cancelled")) return;
      toast({
        title: "Error",
        description:
          error?.message || `Failed to ${lock ? "lock" : "unlock"} account.`,
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (user: User) => {
    const primaryRole = user.roles?.[0] || "";
    const { accessType, role } = deriveAccessAndRoleFromBackendRole(primaryRole);
    const orgId =
      user.organization_id ||
      (Array.isArray(user.organizations) && user.organizations.length > 0
        ? user.organizations[0]
        : "");
    const projectId =
      user.projects && user.projects.length > 0 ? user.projects[0] : "";
    const cleanedLastName =
      user.last_name && user.last_name !== "-" ? user.last_name : "";

    setEditUser(user);
    setFormData({
      username: user.username,
      firstName: user.first_name || user.username || "",
      lastName: cleanedLastName,
      email: user.email,
      role,
      accessType,
      organization: orgId,
      project: projectId,
    });
    setUsernameManuallyEdited(true);
    setOpen(true);
  };

  const handleOpenReset = (user: User) => {
    setResetTargetUser(user);
    setResetPassword("");
    setResetConfirm("");
    setResetOpen(true);
  };

  const handleResetPassword = async () => {
    if (!resetTargetUser) return;
    if (resetPassword.length < 6) {
      toast({
        title: "Validation",
        description: "Password must be at least 6 characters.",
        variant: "destructive",
      });
      return;
    }
    if (resetPassword !== resetConfirm) {
      toast({
        title: "Validation",
        description: "Passwords do not match.",
        variant: "destructive",
      });
      return;
    }
    setLoading(true);
    try {
      await api.updateUser(getUserId(resetTargetUser), {
        password: resetPassword,
      });
      toast({
        title: "Password Reset",
        description: `Password updated for ${resetTargetUser.username}`,
      });
      setResetOpen(false);
      setResetTargetUser(null);
      setResetPassword("");
      setResetConfirm("");
    } catch (error: any) {
      toast({
        title: "Error",
        description: error.message || "Failed to reset password.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  // Sample data for dropdowns - these should be fetched from the backend or constants
  const roles = [
    "Super Admin",
    "Organization Admin",
    "Organization User",
    "Project Admin",
    "Project User",
    "Document Controller",
  ];

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Users Management</h1>

        <Dialog
          open={open || Boolean(editUser)}
          onOpenChange={(openState) => {
            if (!openState) {
              setEditUser(null);
              resetForm();
            }
            setOpen(openState);
          }}
        >
          {can("users:create") && (
            <DialogTrigger asChild>
              <Button
                className="flex items-center gap-2"
                onClick={() => {
                  // Ensure a clean slate when starting a new user
                  resetForm();
                  setEditUser(null);
                  setOpen(true);
                }}
              >
                <UserPlus size={16} />
                <span>Add User</span>
              </Button>
            </DialogTrigger>
          )}
          <DialogContent className="sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>
                {editUser ? "Edit User" : "Add New User"}
              </DialogTitle>
              <DialogDescription>
                {editUser
                  ? "Modify user details"
                  : "Fill in the details to create a new user account"}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="firstName" className="text-left">
                  First Name
                </Label>
                <Input
                  id="firstName"
                  value={formData.firstName}
                  onChange={(e) =>
                    handleInputChange("firstName", e.target.value)
                  }
                  className="col-span-3"
                  placeholder="Enter first name"
                />
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="lastName" className="text-left">
                  Last Name
                </Label>
                <Input
                  id="lastName"
                  value={formData.lastName}
                  onChange={(e) =>
                    handleInputChange("lastName", e.target.value)
                  }
                  className="col-span-3"
                  placeholder="Enter last name"
                />
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="username" className="text-left">
                  Username
                </Label>
                <Input
                  id="username"
                  value={formData.username}
                  onChange={(e) =>
                    handleInputChange("username", e.target.value)
                  }
                  className="col-span-3"
                  placeholder="Unique username (letters, numbers, ., _, -)"
                />
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="email" className="text-left">
                  Email ID
                </Label>
                <Input
                  id="email"
                  type="email"
                  value={formData.email}
                  onChange={(e) => handleInputChange("email", e.target.value)}
                  className="col-span-3"
                  placeholder="user@example.com"
                  disabled={Boolean(editUser)}
                  title={editUser ? "Email cannot be changed" : undefined}
                />
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="role" className="text-left">
                  Role
                </Label>
                <Select
                  value={formData.role}
                  onValueChange={(value) => handleInputChange("role", value)}
                >
                  <SelectTrigger className="col-span-3">
                    <SelectValue placeholder="Select role" />
                  </SelectTrigger>
                  <SelectContent>
                    {roles.map((role) => (
                      <SelectItem key={role} value={role}>
                        {role}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-4 items-center gap-4">
                <Label className="text-left">Access Type</Label>
                <div className="col-span-3">
                  <RadioGroup
                    value={formData.accessType}
                    onValueChange={(value) =>
                      handleInputChange("accessType", value)
                    }
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="superUser" id="superUser" />
                      <Label htmlFor="superUser">Super User</Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="organization" id="organization" />
                      <Label htmlFor="organization">Organization Level</Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="project" id="project" />
                      <Label htmlFor="project">Project Level</Label>
                    </div>
                  </RadioGroup>
                </div>
              </div>

              {/* Conditional fields based on access type */}
              {formData.accessType === "organization" && (
                <div className="grid grid-cols-4 items-center gap-4">
                  <Label htmlFor="organization" className="text-left">
                    Organization
                  </Label>
                  <Select
                    onValueChange={(value) =>
                      handleInputChange("organization", value)
                    }
                    value={formData.organization || ""}
                  >
                    <SelectTrigger className="col-span-3" id="organization">
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
                </div>
              )}

              {formData.accessType === "project" && (
                <>
                  <div className="grid grid-cols-4 items-center gap-4">
                    <Label htmlFor="organization" className="text-left">
                      Organization
                    </Label>
                    <Select
                      onValueChange={(value) => {
                        handleInputChange("organization", value);
                        // Clear project when organization changes
                        handleInputChange("project", "");
                      }}
                      value={formData.organization || ""}
                    >
                      <SelectTrigger className="col-span-3" id="organization">
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
                  </div>

                  <div className="grid grid-cols-4 items-center gap-4">
                    <Label htmlFor="project" className="text-left">
                      Project
                    </Label>
                    <Select
                      onValueChange={(value) =>
                        handleInputChange("project", value)
                      }
                      value={formData.project || ""}
                      disabled={!formData.organization}
                    >
                      <SelectTrigger className="col-span-3" id="project">
                        <SelectValue placeholder="Select project" />
                      </SelectTrigger>
                      <SelectContent>
                        {projects
                          .filter(
                            (project) =>
                              project.organization_id === formData.organization
                          )
                          .map((project) => (
                            <SelectItem key={project._id} value={project._id}>
                              {project.name}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}
            </div>

            <DialogFooter>
              <Button
                onClick={editUser ? handleUpdateUser : handleAddUser}
                disabled={loading}
              >
                {loading
                  ? editUser
                    ? "Updating..."
                    : "Creating..."
                  : editUser
                  ? "Update User"
                  : "Create User"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <Card className="mb-8">
        <CardHeader className="pb-2">
          <CardTitle>User Filters</CardTitle>
          <CardDescription>
            Search and filter users by various criteria
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col md:flex-row gap-4">
            <div className="relative flex-grow">
              <Search
                className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500"
                size={18}
              />
              <Input
                placeholder="Search users..."
                className="pl-10"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <div className="flex gap-2">
              <Button variant="outline">Filter</Button>
              <Button variant="outline">Clear</Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {(!rbacLoading && !can("users:read")) || accessDenied ? (
        <Card>
          <CardContent className="pt-6">
            <div className="py-12 text-center text-muted-foreground">
              You don't have permission to view users.
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead>Role</TableHead>
                  <TableHead>Organization</TableHead>
                  <TableHead>Projects</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center">
                      Loading users...
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredUsers.map((user, idx) => {
                    const orgName =
                      user.organization_name ??
                      organizations.find((o) => o._id === user.organization_id)
                        ?.name ??
                      user.organization_id ??
                      "N/A";

                    const projNames =
                      (user.project_names && user.project_names.length > 0
                        ? user.project_names
                        : (user.projects || []).map((pid) => {
                            const p = projects.find((proj) => proj._id === pid);
                            return p ? p.name : pid;
                          })
                      ).join(", ") || "N/A";

                    const rowKey = getUserId(user) || `${user.email}-${idx}`;
                    return (
                      <TableRow key={rowKey}>
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-3">
                            <Avatar className="h-8 w-8">
                              <AvatarImage
                                src={user.avatar || ""}
                                alt={
                                  `${
                                    user.first_name
                                      ? `${user.first_name} ${
                                          user.last_name || ""
                                        }`.trim()
                                      : user.username
                                  }` || "user"
                                }
                              />
                              <AvatarFallback>
                                {(
                                  (user.first_name && user.first_name[0]) ||
                                  user.username?.charAt(0) ||
                                  "U"
                                ).toUpperCase()}
                              </AvatarFallback>
                            </Avatar>
                            <div className="flex flex-col">
                              <span className="font-medium">
                                {`${user.first_name || ""} ${
                                  user.last_name || ""
                                }`.trim() ||
                                  user.username ||
                                  "Unnamed User"}
                              </span>
                              {user.username && (
                                <span className="text-xs text-muted-foreground">
                                  @{user.username}
                                </span>
                              )}
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>{user.email}</TableCell>
                        <TableCell>{user.roles?.join(", ") || "N/A"}</TableCell>
                        <TableCell>{orgName}</TableCell>
                        <TableCell>{projNames}</TableCell>
                        <TableCell>
                          <Badge
                            variant={!user.disabled ? "primary" : "outline"}
                          >
                            {!user.disabled ? "Active" : "Inactive"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right">
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" className="h-8 w-8 p-0">
                                <MoreHorizontal className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              {can("users:update") && (
                                <DropdownMenuItem
                                  onClick={() => handleEdit(user)}
                                >
                                  Edit
                                </DropdownMenuItem>
                              )}
                              <DropdownMenuItem>Permissions</DropdownMenuItem>
                              {can("users:update") && (
                                <DropdownMenuItem
                                  onClick={() => handleOpenReset(user)}
                                >
                                  Reset Password
                                </DropdownMenuItem>
                              )}
                              {can("users:lock") && (
                                <DropdownMenuItem
                                  onClick={() =>
                                    handleLockToggle(getUserId(user), true)
                                  }
                                >
                                  Lock Account
                                </DropdownMenuItem>
                              )}
                              {can("users:unlock") && (
                                <DropdownMenuItem
                                  onClick={() =>
                                    handleLockToggle(getUserId(user), false)
                                  }
                                >
                                  Unlock Account
                                </DropdownMenuItem>
                              )}
                              {can("users:delete") && (
                                <DropdownMenuItem
                                  className="text-destructive"
                                  onClick={() =>
                                    handleDeleteUser(getUserId(user))
                                  }
                                >
                                  Delete
                                </DropdownMenuItem>
                              )}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {can("users:update") && (
        <Dialog open={resetOpen} onOpenChange={setResetOpen}>
          <DialogContent className="sm:max-w-[500px]">
            <DialogHeader>
              <DialogTitle>Reset Password</DialogTitle>
              <DialogDescription>
                Set a new password for {resetTargetUser?.username}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="newPassword" className="text-right">
                  New Password
                </Label>
                <Input
                  id="newPassword"
                  type="password"
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  className="col-span-3"
                />
              </div>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="confirmPassword" className="text-right">
                  Confirm Password
                </Label>
                <Input
                  id="confirmPassword"
                  type="password"
                  value={resetConfirm}
                  onChange={(e) => setResetConfirm(e.target.value)}
                  className="col-span-3"
                />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setResetOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleResetPassword} disabled={loading}>
                {loading ? "Updating..." : "Update Password"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
      {StepUpDialog}
    </div>
  );
};

export default UsersPage;
