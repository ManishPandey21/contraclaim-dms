import React, { useState, useEffect } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { Card, CardContent } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { redirectToLoginAfterSessionExpiry } from "@/services/auth";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Building,
  Calendar,
  FileText,
  Users,
  ArrowUpRight,
  Folder,
  Filter as FilterIcon,
} from "lucide-react";
import { enhancedApi, Organization } from "../services/enhanced-api";
import { getCurrentUserProfile } from "@/services/session-api";
import { Project } from "../types/api";
import useRBAC from "../hooks/useRBAC";
import useHasPermission from "@/hooks/useHasPermission";
import { ENTITY_PERMISSIONS } from "@/constants/entityPermissions";
import { toast } from "sonner";

// Extended Project interface for frontend display
interface ProjectDisplay extends Project {
  letterCount?: number;
  teamSize?: number;
  status?: "Active" | "Completed" | "On Hold";
  startDate?: string;
}

type ProjectsLocationState = {
  source?: "organization";
  organizationId?: string;
  organizationName?: string;
};

const ProjectsPage = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [organizationFilter, setOrganizationFilter] = useState("all");
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const navigationState =
    (location.state as ProjectsLocationState | null) ?? null;
  const requestedOrganizationId =
    searchParams.get("org") || navigationState?.organizationId || "";
  const requestedOrganizationName =
    searchParams.get("orgName") || navigationState?.organizationName || "";
  const navigationSource =
    searchParams.get("source") || navigationState?.source || "";
  const isOrganizationScopedView =
    navigationSource === "organization" && requestedOrganizationId.length > 0;

  const { roles } = useRBAC();
  const isSuperadmin = roles.includes("superadmin");
  const canCreateProject = useHasPermission(ENTITY_PERMISSIONS.projects.create);
  const canUpdateProject = useHasPermission(ENTITY_PERMISSIONS.projects.update);
  const canDeleteProject = useHasPermission(ENTITY_PERMISSIONS.projects.delete);

  useEffect(() => {
    setOrganizationFilter(
      isOrganizationScopedView ? requestedOrganizationId : "all"
    );
  }, [isOrganizationScopedView, requestedOrganizationId]);

  const handleTokenExpiration = () => {
    redirectToLoginAfterSessionExpiry();
  };

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true);
      try {
        const [projectsData, orgsData, sessionProfile] = await Promise.all([
          enhancedApi.getProjects(),
          enhancedApi.getOrganizations(),
          getCurrentUserProfile().catch(() => null),
        ]);

        // Restrict visible data for organization-scoped roles (org admin/user)
        const normalizedRoles = roles.map((role) =>
          typeof role === "string" ? role.toLowerCase() : ""
        );
        const isSuperadminRole = normalizedRoles.includes("superadmin");
        const isOrgScopedRole = normalizedRoles.some(
          (role) => role === "orgadmin" || role === "orguser"
        );
        const isProjectScopedRole = normalizedRoles.some(
          (role) => role === "projectadmin" || role === "projectuser"
        );
        const normalizedOrgId = sessionProfile?.organization_id
          ? String(sessionProfile.organization_id)
          : null;
        const allowedProjectIds = Array.isArray(sessionProfile?.projects)
          ? sessionProfile.projects.map((p) => String(p))
          : [];

        let scopedProjects = projectsData;
        let scopedOrganizations = orgsData;

        if (!isSuperadminRole && isOrgScopedRole) {
          if (normalizedOrgId) {
            scopedProjects = projectsData.filter((project) => {
              const projectOrgId =
                project.organization_id !== undefined &&
                project.organization_id !== null
                  ? String(project.organization_id)
                  : "";
              return projectOrgId === normalizedOrgId;
            });
            scopedOrganizations = orgsData.filter((org) => {
              const orgId =
                org._id !== undefined && org._id !== null
                  ? String(org._id)
                  : "";
              return orgId === normalizedOrgId;
            });
          } else {
            scopedProjects = [];
            scopedOrganizations = [];
          }
        } else if (!isSuperadminRole && isProjectScopedRole) {
          const projectSet = new Set(
            allowedProjectIds.flatMap((p) => p.split(",")).map((p) => p.trim()).filter(Boolean)
          );
          if (projectSet.size > 0) {
            scopedProjects = projectsData.filter((p) =>
              projectSet.has(String(p._id))
            );
            const allowedOrgIds = new Set(
              scopedProjects
                .map((p) => (p.organization_id !== undefined ? String(p.organization_id) : ""))
                .filter(Boolean)
            );
            scopedOrganizations = orgsData.filter((org) =>
              allowedOrgIds.has(String(org._id))
            );
          } else {
            scopedProjects = [];
            scopedOrganizations = [];
          }
        }

        setProjects(scopedProjects);
        setOrganizations(scopedOrganizations);
      } catch (error: unknown) {
        console.error(
          "Error fetching data:",
          error instanceof Error ? error.message : "Unknown error",
          error
        );
        if (
          error instanceof Error &&
          error.message.includes("No authentication token")
        ) {
          handleTokenExpiration();
        } else {
          toast.error("Failed to fetch data. Please try again later.");
        }
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [roles]);

  const handleDeactivateProject = async (id: string) => {
    if (!canDeleteProject) {
      toast.error("You do not have permission to deactivate projects.");
      return;
    }
    try {
      const confirmed = window.confirm(
        "Are you sure you want to deactivate this project? This will hide it from lists."
      );
      if (!confirmed) return;

      await enhancedApi.deactivateProject(id);
      setProjects((prev) => prev.filter((p) => p._id !== id));
    } catch (error: unknown) {
      console.error(
        "Error deactivating project:",
        error instanceof Error ? error.message : "Unknown error"
      );
      toast.error("Failed to deactivate project. Please try again later.");
    }
  };

  const getStatusBadgeStyles = (status: Project["status"]) => {
    switch (status) {
      case "Active":
        return "bg-green-500";
      case "Completed":
        return "bg-blue-500";
      case "On Hold":
        return "bg-yellow-500";
      default:
        return "bg-gray-500";
    }
  };

  const getOrganizationById = (orgId: string) => {
    return (
      organizations.find((org) => org._id === orgId)?.name ||
      "Unknown Organization"
    );
  };

  const scopedOrganizations = organizations.filter(
    (org) => String(org._id) === requestedOrganizationId
  );
  const selectedOrganizationName =
    scopedOrganizations[0]?.name ||
    requestedOrganizationName ||
    "Selected Organization";
  const organizationOptions = isOrganizationScopedView
    ? scopedOrganizations.length > 0
      ? scopedOrganizations
      : [{ _id: requestedOrganizationId, name: selectedOrganizationName }]
    : organizations;
  const organizationSelectValue = isOrganizationScopedView
    ? requestedOrganizationId
    : organizationFilter;

  const filteredProjects = projects.filter((project) => {
    const matchesSearch =
      project.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (project.projectCode &&
        project.projectCode.toLowerCase().includes(searchQuery.toLowerCase()));

    const matchesStatus =
      statusFilter === "all" || project.status === statusFilter;
    const matchesScopedOrganization =
      !isOrganizationScopedView ||
      String(project.organization_id) === requestedOrganizationId;
    const matchesOrg =
      isOrganizationScopedView ||
      organizationFilter === "all" ||
      String(project.organization_id) === organizationFilter;

    return (
      matchesSearch &&
      matchesStatus &&
      matchesScopedOrganization &&
      matchesOrg
    );
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900"></div>
      </div>
    );
  }

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">
          {isOrganizationScopedView
            ? `Projects - ${selectedOrganizationName}`
            : "Projects"}
        </h1>
        {canCreateProject && (
          <Button
            onClick={() => navigate("/register?tab=project&mode=create")}
            className="flex items-center gap-2"
          >
            <Folder size={16} />
            Add Project
          </Button>
        )}
      </div>

      {/* Filters and Search */}
      <div className="mb-6 grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="relative col-span-2">
          <input
            type="text"
            placeholder="Search projects..."
            className="w-full px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <div className="absolute right-3 top-2.5">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-5 w-5 text-gray-400"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
              />
            </svg>
          </div>
        </div>

        <div>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-full">
              <div className="flex items-center">
                <FilterIcon className="mr-2 h-4 w-4" />
                <SelectValue placeholder="Filter by status" />
              </div>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Statuses</SelectItem>
              <SelectItem value="Active">Active</SelectItem>
              <SelectItem value="Completed">Completed</SelectItem>
              <SelectItem value="On Hold">On Hold</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div>
          <Select
            value={organizationSelectValue}
            onValueChange={setOrganizationFilter}
            disabled={isOrganizationScopedView}
          >
            <SelectTrigger className="w-full">
              <div className="flex items-center">
                <Building className="mr-2 h-4 w-4" />
                <SelectValue placeholder="Filter by organization" />
              </div>
            </SelectTrigger>
            <SelectContent>
              {!isOrganizationScopedView && (
                <SelectItem value="all">All Organizations</SelectItem>
              )}
              {organizationOptions.map((org) => (
                <SelectItem key={org._id} value={org._id}>
                  {org.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {filteredProjects.length > 0 ? (
          filteredProjects.map((project) => (
            <Card
              key={project._id}
              className="overflow-hidden hover:shadow-lg transition-shadow duration-300"
            >
              <CardContent className="p-0">
                <div className="bg-gradient-to-r from-blue-500 to-blue-600 p-4">
                  <div className="flex justify-between items-start">
                    <h2 className="text-xl font-bold text-white">
                      {project.name}
                    </h2>
                    <Badge
                      className={getStatusBadgeStyles(
                        project.status || "Active"
                      )}
                    >
                      {project.status || "Active"}
                    </Badge>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <Building size={14} className="text-blue-200" />
                    <p className="text-sm text-blue-100">
                      {getOrganizationById(project.organization_id)}
                    </p>
                  </div>
                </div>
                <div className="p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <Calendar size={16} className="text-gray-500" />
                    <p className="text-sm">
                      Started:{" "}
                      {project.startDate
                        ? new Date(project.startDate).toLocaleDateString()
                        : "Not set"}
                    </p>
                  </div>
                  <p className="text-sm font-medium">
                    {project.projectCode || "No code"}
                  </p>
                  <p className="text-sm">
                    {project.address || "No address"},{" "}
                    {project.city || "No city"}
                  </p>
                  <p className="text-sm">
                    {project.state || "No state"}, {project.pinCode || "No PIN"}
                  </p>

                  <div className="grid grid-cols-2 gap-2 mt-4 border-t pt-3">
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <FileText size={14} className="text-blue-500" />
                        <span className="font-bold">
                          {project.letterCount || 0}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500">Letters</p>
                    </div>
                    <div className="text-center">
                      <div className="flex items-center justify-center gap-1">
                        <Users size={14} className="text-blue-500" />
                        <span className="font-bold">
                          {project.teamSize || 0}
                        </span>
                      </div>
                      <p className="text-xs text-gray-500">Team Members</p>
                    </div>
                  </div>

                  <div className="mt-3 flex justify-end gap-2">
                    {canUpdateProject && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          navigate(
                            `/register?tab=project&mode=edit&projectId=${project._id}`
                          )
                        }
                      >
                        Edit
                      </Button>
                    )}
                    {isSuperadmin && canDeleteProject && (
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleDeactivateProject(project._id)}
                      >
                        Deactivate
                      </Button>
                    )}
                    <Button size="sm" asChild>
                      <Link to={`/letters?project=${project._id}`}>
                        View Letters <ArrowUpRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))
        ) : (
          <div className="col-span-3 flex items-center justify-center h-64 bg-gray-100 rounded-lg">
            <p className="text-gray-500">
              No projects found matching your search
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ProjectsPage;
