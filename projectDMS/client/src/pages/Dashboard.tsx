import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  FileText,
  MessageCircleQuestion,
  AlertCircle,
  Clock,
  CheckCircle2,
  BarChart3,
  Building,
  FolderArchive,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card, CardContent } from "@/components/ui/card";
import StatusSummary from "@/components/dashboard/StatusSummary";
import StatusBreakdown from "@/components/dashboard/StatusBreakdown";
import ActionableItems from "@/components/dashboard/ActionableItems";
import OrganizationView from "@/components/dashboard/OrganizationView";
import ProjectView from "@/components/dashboard/ProjectView";
import DashboardHeader from "@/components/dashboard/DashboardHeader";
import { getDashboardStats, DashboardStats } from "@/services/dashboard-api";

// ---------------------------------------------------------------------------
// Types matching child component interfaces
// ---------------------------------------------------------------------------

interface Letter {
  id: string;
  title: string;
  organization: string;
  project: string;
  status: string;
  updatedAt: string;
  updatedBy: string;
  assignedTo: string;
}

interface Organization {
  id: string;
  name: string;
  totalLetters: number;
  replyOverdue: number;
  underReview: number;
}

interface Project {
  id: string;
  name: string;
  organization: string; // org ID for OrganizationView filtering
  totalLetters: number;
  inputRequired: number;
  closed: number;
}

// ---------------------------------------------------------------------------
// Dashboard Component
// ---------------------------------------------------------------------------

const Dashboard = () => {
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [activeTab, setActiveTab] = useState<string>("overview");

  // API state
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Debounce timer ref
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // -------------------------------------------------------------------
  // Fetch dashboard data
  // -------------------------------------------------------------------
  const fetchData = useCallback(async (search?: string, status?: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getDashboardStats({ search, status });
      setStats(data);
    } catch (err: any) {
      console.error("Failed to load dashboard stats:", err);
      setError(err?.message || "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Debounced search + status filter
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchData(
        searchQuery || undefined,
        statusFilter !== "all" ? statusFilter : undefined,
      );
    }, 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchQuery, statusFilter, fetchData]);

  // -------------------------------------------------------------------
  // Derived data from API response
  // -------------------------------------------------------------------
  const documentStatuses =
    stats?.documentStatuses ?? stats?.letterStatuses ?? [];
  const totalLettersCount = stats?.totalLetters ?? 0;
  const totalDocumentsCount = stats?.totalDocuments ?? totalLettersCount;
  const inputRequiredCount = stats?.inputRequiredCount ?? 0;
  const incomingCount = stats?.incomingCount ?? inputRequiredCount;
  const replyOverdueCount = stats?.replyOverdueCount ?? 0;
  const outgoingCount = stats?.outgoingCount ?? replyOverdueCount;
  const draftInProcessCount =
    stats?.draftInProcessCount ??
    documentStatuses.find((status) => status.status === "Draft")?.count ??
    stats?.underReviewCount ??
    0;

  // Map API recent uploads to the shape StatusBreakdown expects
  const recentDocuments = [...(stats?.recentDocuments ?? [])]
    .sort((a, b) => {
      const aTime = new Date(a.uploadedAt || a.letterDate || 0).getTime();
      const bTime = new Date(b.uploadedAt || b.letterDate || 0).getTime();
      return bTime - aTime;
    })
    .slice(0, 6)
    .map((document) => ({
      id: document.id,
      documentNumber: document.documentNumber,
      filename: document.filename,
      subject: document.subject,
      direction: document.direction,
      letterDate: document.letterDate,
      uploadedAt: document.uploadedAt,
    }));

  // Map API actionable letters to the Letter shape
  const recentLetters: Letter[] = (stats?.actionableLetters ?? []).map((l) => ({
    id: l.id,
    title: l.title,
    organization: l.organization,
    project: l.project,
    status: l.status,
    updatedAt: l.updatedAt,
    updatedBy: l.updatedBy,
    assignedTo: l.assignedTo,
  }));

  // Map API organizations
  const organizations: Organization[] = (stats?.organizations ?? []).map(
    (o) => ({
      id: o.id,
      name: o.name,
      totalLetters: o.totalLetters,
      replyOverdue: o.replyOverdue,
      underReview: o.underReview,
    }),
  );

  // Map API projects - organization field is the org ID for filtering
  const projects: Project[] = (stats?.projects ?? []).map((p) => ({
    id: p.id,
    name: p.name,
    organization: p.organizationId || p.id, // org ID for filtering
    totalLetters: p.totalLetters,
    inputRequired: p.inputRequired,
    closed: p.closed,
  }));

  // Also create an organizations lookup that ProjectView can use to resolve names
  // ProjectView expects organizations with id matching project.organization
  const orgLookup: Organization[] =
    organizations.length > 0
      ? organizations
      : (stats?.projects ?? []).reduce<Organization[]>((acc, p) => {
          if (!acc.find((o) => o.id === p.organizationId)) {
            acc.push({
              id: p.organizationId,
              name: p.organization,
              totalLetters: 0,
              replyOverdue: 0,
              underReview: 0,
            });
          }
          return acc;
        }, []);

  // -------------------------------------------------------------------
  // Helper functions
  // -------------------------------------------------------------------
  const formatDate = (dateString: string) => {
    if (!dateString) return "";
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return dateString;
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "Input Required":
        return <MessageCircleQuestion className="text-amber-500" size={16} />;
      case "Reply Overdue":
        return <AlertCircle className="text-red-600" size={16} />;
      case "Under Review":
        return <Clock className="text-purple-600" size={16} />;
      case "Draft":
        return <Clock className="text-slate-600" size={16} />;
      case "Completed":
        return <CheckCircle2 className="text-green-600" size={16} />;
      case "Closed":
        return <CheckCircle2 className="text-gray-600" size={16} />;
      default:
        return <FileText className="text-blue-600" size={16} />;
    }
  };

  const getStatusBadge = (status: string) => {
    const colorMap: Record<string, string> = {
      Received: "bg-blue-500",
      "Input Required": "bg-amber-500",
      "On Hold": "bg-gray-500",
      "Under Review": "bg-purple-500",
      "Under Process": "bg-emerald-500",
      Replied: "bg-teal-500",
      Forwarded: "bg-pink-500",
      Completed: "bg-green-500",
      Closed: "bg-gray-500",
      "Reply Received": "bg-sky-500",
      "No Reply Received": "bg-red-400",
      "Reply Overdue": "bg-red-600",
      Draft: "bg-slate-500",
    };
    const color = colorMap[status] || "bg-gray-500";
    return <Badge className={color}>{status}</Badge>;
  };

  const filteredProjects = selectedOrg
    ? projects.filter((project) => project.organization === selectedOrg)
    : projects;

  const selectedOrgDetails = selectedOrg
    ? (organizations.find((org) => org.id === selectedOrg) ?? null)
    : null;

  const selectedProjectDetails = selectedProject
    ? (projects.find((project) => project.id === selectedProject) ?? null)
    : null;

  // -------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------
  if (loading && !stats) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-bold">
            Document Dashboard
          </h1>
        </div>
        <div className="flex items-center justify-center py-20">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <span className="ml-3 text-gray-500">Loading dashboard data...</span>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // Error state
  // -------------------------------------------------------------------
  if (error && !stats) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="flex justify-between items-center mb-4">
          <h1 className="text-2xl font-bold">
            Document Dashboard
          </h1>
        </div>
        <Card>
          <CardContent className="p-6 text-center">
            <p className="text-red-600 mb-4">{error}</p>
            <button
              onClick={() => fetchData()}
              className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Retry
            </button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // -------------------------------------------------------------------
  // Main render
  // -------------------------------------------------------------------
  return (
    <div className="space-y-6 animate-fade-in">
      <DashboardHeader
        documentStatuses={documentStatuses}
        statusFilter={statusFilter}
        setStatusFilter={setStatusFilter}
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
      />

      {/* Subtle loading indicator when refreshing */}
      {loading && stats && (
        <div className="flex items-center text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin mr-2" />
          Refreshing...
        </div>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid grid-cols-3 w-full">
          <TabsTrigger value="overview">
            <BarChart3 className="mr-2 h-4 w-4" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="organizations">
            <Building className="mr-2 h-4 w-4" />
            Organizations
          </TabsTrigger>
          <TabsTrigger value="projects">
            <FolderArchive className="mr-2 h-4 w-4" />
            Projects
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-6">
          <StatusSummary
            totalDocumentsCount={totalDocumentsCount}
            incomingCount={incomingCount}
            outgoingCount={outgoingCount}
            draftInProcessCount={draftInProcessCount}
          />

          <StatusBreakdown
            documentStatuses={documentStatuses}
            recentDocuments={recentDocuments}
            formatDate={formatDate}
          />

          <ActionableItems
            letters={recentLetters}
            formatDate={formatDate}
            getStatusIcon={getStatusIcon}
          />
        </TabsContent>

        <TabsContent value="organizations" className="space-y-6">
          <OrganizationView
            organizations={organizations}
            projects={projects}
            letterStatuses={documentStatuses}
            totalLettersCount={totalLettersCount}
            selectedOrg={selectedOrg}
            setSelectedOrg={setSelectedOrg}
            selectedOrgDetails={selectedOrgDetails}
            filteredProjects={filteredProjects}
            setSelectedProject={setSelectedProject}
          />
        </TabsContent>

        <TabsContent value="projects" className="space-y-6">
          <ProjectView
            projects={projects}
            organizations={orgLookup}
            letterStatuses={documentStatuses}
            recentLetters={recentLetters}
            totalLettersCount={totalLettersCount}
            selectedProject={selectedProject}
            setSelectedProject={setSelectedProject}
            selectedProjectDetails={selectedProjectDetails}
            formatDate={formatDate}
            getStatusBadge={getStatusBadge}
            getStatusIcon={getStatusIcon}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default Dashboard;
