import React, {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  BarChart,
  Calendar,
  Download,
  FileText,
  List as ListIcon,
} from "lucide-react";
import { format } from "date-fns";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Calendar as CalendarComponent } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import enhancedApi, {
  ReportDefinition,
  ReportPreviewResponse,
  ReportRequestPayload,
  Tag,
} from "@/services/enhanced-api";
import { Organization, Project } from "@/types/api";
import { toast } from "sonner";

const COLUMN_LABELS: Record<string, string> = {
  id: "ID",
  letter_no: "Letter Number",
  name: "Name",
  title: "Title",
  owner: "Owner ID",
  owner_name: "Owner",
  assigned_to: "Assignee ID",
  assigned_to_name: "Assignee",
  organization_id: "Organization ID",
  organization_name: "Organization",
  project_id: "Project ID",
  project_name: "Project",
  created_at: "Created",
  createdAt: "Created",
  updated_at: "Updated",
  updatedAt: "Updated",
  summary: "Summary",
  direction: "Direction",
  from: "From",
  to: "To",
  date: "Date",
  references: "References",
  tags: "Tags",
  sub_tags: "Sub Tags",
};
const ALL_ORGANIZATIONS_VALUE = "__all_organizations__";
const ALL_PROJECTS_VALUE = "__all_projects__";

const formatColumnLabel = (column: string) =>
  COLUMN_LABELS[column] ||
  column
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());

const formatCellValue = (value: unknown): string => {
  if (value === null || value === undefined || value === "") {
    return "-";
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (item === null || item === undefined) return "";
        if (typeof item === "object") return JSON.stringify(item);
        return String(item);
      })
      .filter(Boolean)
      .join(", ");
  }

  if (typeof value === "string") {
    const parsedDate = Date.parse(value);
    if (!Number.isNaN(parsedDate) && value.includes("-")) {
      return format(new Date(parsedDate), "PP");
    }
  }

  return String(value);
};

const getReportIcon = (category: ReportDefinition["category"]) => {
  switch (category) {
    case "letters":
      return <FileText className="h-5 w-5 text-blue-600" />;
    case "tasks":
      return <BarChart className="h-5 w-5 text-emerald-600" />;
    default:
      return <ListIcon className="h-5 w-5 text-indigo-600" />;
  }
};

const LETTER_STATUS_OPTIONS = [
  "Draft",
  "Received",
  "Input Required",
  "Under Review",
  "Under Process",
  "On Hold",
  "Replied",
  "Forwarded",
  "Completed",
  "Closed",
  "Reply Received",
  "No Reply Received",
  "Reply Overdue",
];

const ReportsAnalyticsPage = () => {
  const [reports, setReports] = useState<ReportDefinition[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [selectedReport, setSelectedReport] = useState<ReportDefinition | null>(
    null
  );
  const [dateRange, setDateRange] = useState<
    "all" | "week" | "fortnight" | "month" | "3months" | "custom"
  >("week");
  const [startDate, setStartDate] = useState<Date | undefined>(new Date());
  const [endDate, setEndDate] = useState<Date | undefined>(new Date());
  const [letterNo, setLetterNo] = useState("");
  const [chainDirection, setChainDirection] = useState<"up" | "down">("up");
  const [includeSelf, setIncludeSelf] = useState(true);
  const [letterDirection, setLetterDirection] = useState<
    "incoming" | "outgoing" | "both"
  >("both");
  const [letterStatuses, setLetterStatuses] = useState<string[]>([]);
  const [tagOptions, setTagOptions] = useState<Tag[]>([]);
  const [selectedTagId, setSelectedTagId] = useState("");
  const [selectedTagName, setSelectedTagName] = useState("");
  const [subtagOptions, setSubtagOptions] = useState<Array<{ _id?: string; name?: string }>>(
    []
  );
  const [selectedSubtagId, setSelectedSubtagId] = useState("");
  const [selectedSubtagName, setSelectedSubtagName] = useState("");
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [preview, setPreview] = useState<ReportPreviewResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [selectedProjectId, setSelectedProjectId] = useState(
    () => window.localStorage.getItem("proj_id") || ""
  );
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [organizationsLoading, setOrganizationsLoading] = useState(false);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState(
    () => window.localStorage.getItem("org_id") || ""
  );
  const isTagReport = useMemo(
    () =>
      selectedReport?.id === "letter-by-tags" ||
      selectedReport?.name?.toLowerCase().includes("tag"),
    [selectedReport]
  );
  const selectedProject = useMemo(
    () =>
      projects.find(
        (project) => String(project._id) === String(selectedProjectId)
      ),
    [projects, selectedProjectId]
  );
  const sortedOrganizations = useMemo(
    () =>
      [...organizations].sort((a, b) =>
        (a.name || "").localeCompare(b.name || "")
      ),
    [organizations]
  );
  const filteredProjects = useMemo(() => {
    const list = selectedOrganizationId
      ? projects.filter(
          (project) =>
            String(project.organization_id) === String(selectedOrganizationId)
        )
      : projects;
    return [...list].sort((a, b) =>
      (a.name || "").localeCompare(b.name || "")
    );
  }, [projects, selectedOrganizationId]);

  useEffect(() => {
    let active = true;
    const loadReports = async () => {
      setReportsLoading(true);
      try {
        const data = await enhancedApi.getReports();
        if (!active) return;
        setReports(data);
        setSelectedReport((current) => current ?? data[0] ?? null);
      } catch (error) {
        console.error(error);
        toast.error("Failed to load reports catalog");
      } finally {
        if (active) {
          setReportsLoading(false);
        }
      }
    };
    loadReports();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const loadProjects = async () => {
      setProjectsLoading(true);
      try {
        const data = await enhancedApi.getProjects();
        if (!active) return;
        setProjects(data);
      } catch (error) {
        console.error(error);
        toast.error("Failed to load projects");
      } finally {
        if (active) {
          setProjectsLoading(false);
        }
      }
    };
    loadProjects();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const loadOrganizations = async () => {
      setOrganizationsLoading(true);
      try {
        const data = await enhancedApi.getOrganizations();
        if (!active) return;
        setOrganizations(data);
      } catch (error) {
        console.error(error);
        toast.error("Failed to load organizations");
      } finally {
        if (active) {
          setOrganizationsLoading(false);
        }
      }
    };
    loadOrganizations();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedProjectId || projects.length === 0) {
      return;
    }
    const exists = projects.some(
      (project) => String(project._id) === String(selectedProjectId)
    );
    if (!exists) {
      setSelectedProjectId("");
    }
  }, [projects, selectedProjectId]);

  useEffect(() => {
    if (!selectedOrganizationId || organizations.length === 0) {
      return;
    }
    const exists = organizations.some(
      (org) => String(org._id) === String(selectedOrganizationId)
    );
    if (!exists) {
      setSelectedOrganizationId("");
    }
  }, [organizations, selectedOrganizationId]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    const projectOrg = selectedProject?.organization_id;
    if (projectOrg && String(projectOrg) !== String(selectedOrganizationId)) {
      setSelectedOrganizationId(String(projectOrg));
    }
  }, [selectedProjectId, selectedOrganizationId, selectedProject?.organization_id]);

  useEffect(() => {
    if (!selectedOrganizationId || !selectedProjectId) {
      return;
    }
    if (
      selectedProject?.organization_id &&
      String(selectedProject.organization_id) !== String(selectedOrganizationId)
    ) {
      setSelectedProjectId("");
    }
  }, [selectedOrganizationId, selectedProjectId, selectedProject?.organization_id]);

  useEffect(() => {
    if (!selectedReport) return;
    const defaults = selectedReport.defaultColumns ?? [];
    const priorityOrder = [
      "letter_no",
      "organization_name",
      "project_name",
      "date",
      "from",
      "to",
      "direction",
      "summary",
      "name",
      "title",
      "status",
      "created_at",
      "updated_at",
      "owner_name",
      "assigned_to_name",
      "organization_name",
      "project_name",
    ];
    const ordered = [
      ...new Set([
        ...priorityOrder.filter((column) => defaults.includes(column)),
        ...defaults,
      ]),
    ];
    setSelectedColumns(ordered);
    setPreview(null);
    if (selectedReport.id === "linked-letter-chain") {
      setDateRange("week");
      setLetterNo("");
      setChainDirection("up");
      setIncludeSelf(true);
      setLetterDirection("both");
    }
    if (!isTagReport) {
      setSelectedTagId("");
      setSelectedTagName("");
      setSubtagOptions([]);
      setSelectedSubtagId("");
      setSelectedSubtagName("");
    }
    if (selectedReport.id !== "letter-status") {
      setLetterStatuses([]);
    }
  }, [selectedReport]);

  const availableColumns = useMemo(
    () => selectedReport?.defaultColumns ?? [],
    [selectedReport]
  );

  const previewRows = preview?.rows ?? [];
  const hasPreview = previewRows.length > 0;

  const formatDate = (date: Date | undefined) => {
    return date ? format(date, "PP") : "";
  };

  const handleDateRangeChange = useCallback((value: string) => {
    setDateRange(value as typeof dateRange);

    const today = new Date();
    let start = new Date();

    switch (value) {
      case "all":
        setStartDate(undefined);
        setEndDate(undefined);
        return;
      case "week":
        start.setDate(today.getDate() - 7);
        break;
      case "fortnight":
        start.setDate(today.getDate() - 14);
        break;
      case "month":
        start.setMonth(today.getMonth() - 1);
        break;
      case "3months":
        start.setMonth(today.getMonth() - 3);
        break;
      case "custom":
        return;
    }

    setStartDate(start);
    setEndDate(today);
  }, []);

  useEffect(() => {
    if (!selectedReport || selectedReport.id === "linked-letter-chain") {
      return;
    }
    handleDateRangeChange(dateRange);
  }, [selectedReport, dateRange, handleDateRangeChange]);

  const handleColumnToggle = (columnId: string) => {
    setSelectedColumns((prev) =>
      prev.includes(columnId)
        ? prev.filter((col) => col !== columnId)
        : [...prev, columnId]
    );
  };

  const toggleLetterStatus = useCallback((status: string) => {
    setLetterStatuses((prev) =>
      prev.includes(status)
        ? prev.filter((item) => item !== status)
        : [...prev, status]
    );
  }, []);

  useEffect(() => {
    if (!isTagReport) {
      return;
    }
    let active = true;
    const loadTags = async () => {
      try {
        const data = await enhancedApi.getTags();
        if (!active) return;
        setTagOptions(data);
      } catch (error) {
        console.error(error);
        toast.error("Failed to load tags");
      }
    };
    loadTags();
    return () => {
      active = false;
    };
  }, [isTagReport]);

  useEffect(() => {
    if (!isTagReport) {
      return;
    }
    if (!selectedTagId) {
      setSubtagOptions([]);
      setSelectedSubtagId("");
      setSelectedSubtagName("");
      return;
    }
    let active = true;
    const loadSubtags = async () => {
      try {
        const data = await enhancedApi.getTagSubtags(selectedTagId);
        if (!active) return;
        setSubtagOptions(data || []);
      } catch (error) {
        console.error(error);
        toast.error("Failed to load subtags");
      }
    };
    loadSubtags();
    return () => {
      active = false;
    };
  }, [isTagReport, selectedTagId]);

  const buildRequestPayload = useCallback((): ReportRequestPayload | null => {
    if (!selectedReport) {
      toast.error("Please select a report");
      return null;
    }

    // Linked-letter-chain uses letter input instead of dates
    const isLinkedChain = selectedReport.id === "linked-letter-chain";
    const isLetterReceived = selectedReport.id === "letter-received";
    const isTagReport =
      selectedReport.id === "letter-by-tags" ||
      selectedReport.name?.toLowerCase().includes("tag");
    if (isLinkedChain && !letterNo.trim()) {
      toast.error("Please enter a letter number");
      return null;
    }
    if (!isLinkedChain && !isTagReport && (!startDate || !endDate)) {
      toast.error("Please select a valid date range");
      return null;
    }

    const tagValues = Array.from(
      new Set([selectedTagName, selectedTagId].filter(Boolean))
    );
    const subTagValues = Array.from(
      new Set([selectedSubtagName, selectedSubtagId].filter(Boolean))
    );
    const tagsPayload = isTagReport && tagValues.length ? tagValues : undefined;
    const subTagsPayload =
      isTagReport && subTagValues.length ? subTagValues : undefined;
    const resolvedProjectId = selectedProjectId || undefined;
    const resolvedOrganizationId =
      selectedOrganizationId || selectedProject?.organization_id || undefined;

    return {
      reportId: selectedReport.id,
      startDate: isLinkedChain ? undefined : startDate?.toISOString(),
      endDate: isLinkedChain ? undefined : endDate?.toISOString(),
      organizationId: resolvedOrganizationId,
      projectId: resolvedProjectId,
      limit: 200,
      letterNo: isLinkedChain ? letterNo.trim() : undefined,
      chainDirection: isLinkedChain ? chainDirection : undefined,
      includeSelf: isLinkedChain ? includeSelf : undefined,
      direction: isLetterReceived ? letterDirection : undefined,
      tags: tagsPayload,
      subTags: subTagsPayload,
      statuses: selectedReport.id === "letter-status" ? letterStatuses : undefined,
    };
  }, [
    selectedReport,
    startDate,
    endDate,
    letterNo,
    chainDirection,
    includeSelf,
      letterDirection,
    selectedTagName,
    selectedSubtagId,
    selectedSubtagName,
      letterStatuses,
      selectedProjectId,
      selectedProject?.organization_id,
      selectedOrganizationId,
  ]);

  const generatePreview = useCallback(async () => {
    const payload = buildRequestPayload();
    if (!payload) return;

    setIsGenerating(true);
    try {
      const response = await enhancedApi.previewReport(payload);
      setPreview(response);
      toast.success("Report preview generated");
    } catch (error) {
      console.error(error);
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to generate report preview"
      );
    } finally {
      setIsGenerating(false);
    }
  }, [buildRequestPayload]);

  const downloadReport = useCallback(
    async (format: "csv" | "pdf") => {
      if (format === "pdf") {
        toast.info("PDF downloads are coming soon. Please use CSV for now.");
        return;
      }
      const payload = buildRequestPayload();
      if (!payload) return;

      setIsDownloading(true);
      try {
        const blob = await enhancedApi.downloadReport(payload);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${selectedReport?.name || "report"}-${new Date()
          .toISOString()
          .slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
        toast.success("Download started");
      } catch (error) {
        console.error(error);
        toast.error(
          error instanceof Error ? error.message : "Failed to download report"
        );
      } finally {
        setIsDownloading(false);
      }
    },
    [buildRequestPayload, selectedReport?.name]
  );

  const metricsEntries = useMemo(() => {
    if (!preview?.metrics) return [];
    return Object.entries(preview.metrics);
  }, [preview]);

  const dateRangeControl = (
    <div className="space-y-2">
      <Label>Date Range</Label>
      <div className="flex flex-wrap gap-4 items-center">
          <Select value={dateRange} onValueChange={handleDateRangeChange}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Select period" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Time</SelectItem>
              <SelectItem value="week">Last Week</SelectItem>
              <SelectItem value="fortnight">Last Fortnight</SelectItem>
              <SelectItem value="month">Last Month</SelectItem>
              <SelectItem value="3months">Last 3 Months</SelectItem>
              <SelectItem value="custom">Custom Range</SelectItem>
          </SelectContent>
        </Select>

        {dateRange === "custom" && (
          <div className="flex flex-wrap gap-4">
            <div>
              <Label className="text-xs">Start Date</Label>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="w-[180px] justify-start">
                    <Calendar className="mr-2 h-4 w-4" />
                    {startDate ? formatDate(startDate) : "Select date"}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <CalendarComponent
                    mode="single"
                    selected={startDate}
                    onSelect={setStartDate}
                    initialFocus
                    className="p-3 pointer-events-auto"
                  />
                </PopoverContent>
              </Popover>
            </div>

            <div>
              <Label className="text-xs">End Date</Label>
              <Popover>
                <PopoverTrigger asChild>
                  <Button variant="outline" className="w-[180px] justify-start">
                    <Calendar className="mr-2 h-4 w-4" />
                    {endDate ? formatDate(endDate) : "Select date"}
                  </Button>
                </PopoverTrigger>
                <PopoverContent className="w-auto p-0" align="start">
                  <CalendarComponent
                    mode="single"
                    selected={endDate}
                    onSelect={setEndDate}
                    initialFocus
                    className="p-3 pointer-events-auto"
                  />
                </PopoverContent>
              </Popover>
            </div>
          </div>
        )}
      </div>
    </div>
  );

  const organizationSelectValue =
    selectedOrganizationId || ALL_ORGANIZATIONS_VALUE;
  const organizationControl = (
    <div className="space-y-2">
      <Label>Organization</Label>
      <Select
        value={organizationSelectValue}
        onValueChange={(value) =>
          setSelectedOrganizationId(
            value === ALL_ORGANIZATIONS_VALUE ? "" : value
          )
        }
        disabled={organizationsLoading}
      >
        <SelectTrigger className="w-full">
          <SelectValue
            placeholder={
              organizationsLoading ? "Loading organizations..." : "All Organizations"
            }
          />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_ORGANIZATIONS_VALUE}>
            All Organizations
          </SelectItem>
          {sortedOrganizations.length === 0 ? (
            <SelectItem value="__no-organizations" disabled>
              {organizationsLoading
                ? "Loading organizations..."
                : "No organizations available"}
            </SelectItem>
          ) : (
            sortedOrganizations.map((org) => (
              <SelectItem key={org._id} value={org._id}>
                {org.name}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );

  const projectSelectValue = selectedProjectId || ALL_PROJECTS_VALUE;
  const projectControl = (
    <div className="space-y-2">
      <Label>Project</Label>
      <Select
        value={projectSelectValue}
        onValueChange={(value) =>
          setSelectedProjectId(value === ALL_PROJECTS_VALUE ? "" : value)
        }
        disabled={projectsLoading}
      >
        <SelectTrigger className="w-full">
          <SelectValue
            placeholder={projectsLoading ? "Loading projects..." : "All Projects"}
          />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_PROJECTS_VALUE}>All Projects</SelectItem>
          {filteredProjects.length === 0 ? (
            <SelectItem value="__no-projects" disabled>
              {projectsLoading
                ? "Loading projects..."
                : "No projects available"}
            </SelectItem>
          ) : (
            filteredProjects.map((project) => (
              <SelectItem key={project._id} value={project._id}>
                {project.name}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
    </div>
  );

  return (
    <div className="container mx-auto p-6">
      <div className="flex flex-col gap-6">
        <div className="flex justify-between items-center">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BarChart className="h-6 w-6 text-docsumo-blue" />
              Reports & Analytics
            </h1>
            <p className="text-muted-foreground">
              Generate and download detailed reports
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          <div className="lg:col-span-1">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Available Reports</CardTitle>
                <CardDescription>
                  {reportsLoading
                    ? "Loading reports..."
                    : "Select a report to generate"}
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                {reports.length === 0 && !reportsLoading ? (
                  <p className="p-4 text-sm text-muted-foreground">
                    No reports are available for your account yet.
                  </p>
                ) : (
                  <ul className="divide-y">
                    {reports.map((report) => (
                      <li key={report.id}>
                        <button
                          className={`w-full px-4 py-3 text-left flex items-center gap-3 hover:bg-slate-50 transition-colors ${
                            selectedReport?.id === report.id
                              ? "bg-blue-50 text-blue-700"
                              : ""
                          }`}
                          onClick={() => setSelectedReport(report)}
                        >
                          <div className="flex-shrink-0 rounded-md bg-blue-100 p-2">
                            {getReportIcon(report.category)}
                          </div>
                          <div>
                            <h3 className="text-sm font-medium">
                              {report.name}
                            </h3>
                            <p className="text-xs text-muted-foreground">
                              {report.description}
                            </p>
                          </div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="lg:col-span-3">
            <Card className="h-full">
              <CardHeader>
                <CardTitle className="text-lg">
                  {selectedReport
                    ? selectedReport.name
                    : "Report Configuration"}
                </CardTitle>
                <CardDescription>
                  {selectedReport
                    ? selectedReport.description
                    : "Select a report from the list to configure and generate"}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {selectedReport ? (
                  <>
                    {organizationControl}
                    {projectControl}
                    {selectedReport.id === "linked-letter-chain" ? (
                      <div className="grid gap-4 md:grid-cols-3">
                        <div className="space-y-2">
                          <Label>Letter Number</Label>
                          <input
                            className="w-full border rounded-md px-3 py-2 text-sm"
                            placeholder="Enter letter number"
                            value={letterNo}
                            onChange={(e) => setLetterNo(e.target.value)}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label>Chain Direction</Label>
                          <Select
                            value={chainDirection}
                            onValueChange={(v) =>
                              setChainDirection(v as "up" | "down")
                            }
                          >
                            <SelectTrigger className="w-full">
                              <SelectValue placeholder="Select direction" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="up">Above the chain</SelectItem>
                              <SelectItem value="down">
                                Below the chain
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-2">
                          <Label>Options</Label>
                          <div className="flex items-center space-x-2">
                            <Checkbox
                              id="include-self"
                              checked={includeSelf}
                              onCheckedChange={(v) =>
                                setIncludeSelf(Boolean(v))
                              }
                            />
                            <label htmlFor="include-self" className="text-sm">
                              Include selected letter
                            </label>
                            </div>
                          </div>
                        </div>
                    ) : (selectedReport.id === "letter-by-tags" ||
                        selectedReport.name?.toLowerCase().includes("tag")) ? (
                      <div className="grid gap-4 md:grid-cols-2">
                        {dateRangeControl}
                        <div className="space-y-4">
                          <div className="space-y-2">
                            <Label>Tag</Label>
                          <Select
                            value={selectedTagId}
                            onValueChange={(value) => {
                              setSelectedTagId(value);
                              const source = Array.isArray(tagOptions) ? tagOptions : [];
                              const match = source.find((t) => t._id === value);
                              setSelectedTagName(match?.name || "");
                              setSelectedSubtagId("");
                              setSelectedSubtagName("");
                            }}
                          >
                            <SelectTrigger className="w-full">
                              <SelectValue placeholder="Select a tag" />
                            </SelectTrigger>
                            <SelectContent>
                                {!Array.isArray(tagOptions) || tagOptions.length === 0 ? (
                                  <SelectItem value="__no-tags" disabled>
                                    No tags available
                                  </SelectItem>
                                ) : (
                                  tagOptions.map((tag) => (
                                    <SelectItem key={tag._id} value={tag._id}>
                                      {tag.name}
                                    </SelectItem>
                                  ))
                                )}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="space-y-2">
                            <Label>Sub-Tag</Label>
                            <Select
                              value={selectedSubtagId}
                              onValueChange={(value) => {
                                setSelectedSubtagId(value);
                                const match = subtagOptions.find(
                                  (item) =>
                                    item._id === value || item.name === value
                                );
                                setSelectedSubtagName(match?.name || value);
                              }}
                              disabled={
                                !selectedTagId ||
                                !Array.isArray(subtagOptions) ||
                                subtagOptions.length === 0
                              }
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue
                                  placeholder={
                                    !selectedTagId
                                      ? "Select a tag first"
                                      : subtagOptions.length === 0
                                      ? "No subtags available"
                                      : "Select a subtag"
                                  }
                                />
                              </SelectTrigger>
                              <SelectContent>
                                {Array.isArray(subtagOptions) &&
                                  subtagOptions
                                    .map((subtag) => ({
                                      value: subtag._id || subtag.name || "",
                                      label: subtag.name || subtag._id || "Unnamed",
                                    }))
                                    .filter((item) => item.value)
                                    .map((item) => (
                                      <SelectItem key={item.value} value={item.value}>
                                        {item.label}
                                      </SelectItem>
                                    ))}
                              </SelectContent>
                            </Select>
                          </div>
                        </div>
                      </div>
                    ) : selectedReport.id === "letter-received" ? (
                      <div className="grid gap-4 md:grid-cols-2">
                        {dateRangeControl}
                        <div className="space-y-2">
                          <Label>Direction</Label>
                          <Select
                            value={letterDirection}
                            onValueChange={(v) =>
                              setLetterDirection(
                                v as "incoming" | "outgoing" | "both"
                              )
                            }
                          >
                            <SelectTrigger className="w-[200px]">
                              <SelectValue placeholder="Select direction" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="both">Both</SelectItem>
                              <SelectItem value="incoming">Incoming</SelectItem>
                              <SelectItem value="outgoing">Outgoing</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    ) : (
                      <>
                        {dateRangeControl}
                        {selectedReport.id === "letter-status" && (
                          <div className="space-y-2">
                            <Label>Status</Label>
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                              {LETTER_STATUS_OPTIONS.map((status) => (
                                <div
                                  key={status}
                                  className="flex items-center space-x-2"
                                >
                                  <Checkbox
                                    id={`status-${status}`}
                                    checked={letterStatuses.includes(status)}
                                    onCheckedChange={() => toggleLetterStatus(status)}
                                  />
                                  <label
                                    htmlFor={`status-${status}`}
                                    className="text-sm cursor-pointer"
                                  >
                                    {status}
                                  </label>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    )}

                    <div className="space-y-2">
                      <Label>Select Columns to Include</Label>
                      {availableColumns.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                          No columns available for this report.
                        </p>
                      ) : (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                          {availableColumns.map((column) => (
                            <div
                              key={column}
                              className="flex items-center space-x-2"
                            >
                              <Checkbox
                                id={`column-${column}`}
                                checked={selectedColumns.includes(column)}
                                onCheckedChange={() =>
                                  handleColumnToggle(column)
                                }
                              />
                              <label
                                htmlFor={`column-${column}`}
                                className="text-sm cursor-pointer"
                              >
                                {formatColumnLabel(column)}
                              </label>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="pt-4 flex flex-wrap gap-3 justify-between">
                      <Button
                        onClick={generatePreview}
                        disabled={isGenerating}
                      >
                        {isGenerating ? "Generating..." : "Generate Preview"}
                      </Button>

                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          onClick={() => downloadReport("csv")}
                          disabled={!hasPreview || isDownloading}
                        >
                          <Download className="mr-2 h-4 w-4" />
                          {isDownloading ? "Preparing..." : "Download CSV"}
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => downloadReport("pdf")}
                          disabled={!hasPreview}
                        >
                          <Download className="mr-2 h-4 w-4" />
                          Download PDF
                        </Button>
                      </div>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Select a report from the left panel to get started.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </div>

        {preview && (
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Report Preview</CardTitle>
              <CardDescription>
                Showing data from {formatDate(startDate)} to{" "}
                {formatDate(endDate)}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {metricsEntries.length > 0 && (
                <div className="grid gap-4 md:grid-cols-3">
                  <Card>
                    <CardHeader className="pb-2">
                      <CardDescription>Total Rows</CardDescription>
                      <CardTitle className="text-3xl">
                        {preview.totalRows}
                      </CardTitle>
                    </CardHeader>
                  </Card>
                  {metricsEntries.map(([key, value]) => (
                    <Card key={key}>
                      <CardHeader className="pb-2">
                        <CardDescription>
                          {formatColumnLabel(key)}
                        </CardDescription>
                      </CardHeader>
                      <CardContent>
                        {Array.isArray(value) ? (
                          <ul className="text-sm space-y-1">
                            {value.map((item: any, index: number) => (
                              <li key={`${key}-${index}`}>
                                {item.status ||
                                  item.upload_type ||
                                  item.stage ||
                                  "-"}
                                : {item.count ?? item.value ?? "-"}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <p className="text-2xl font-semibold">
                            {formatCellValue(value)}
                          </p>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}

              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {selectedColumns.map((column) => (
                        <TableHead key={column}>
                          {formatColumnLabel(column)}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {hasPreview ? (
                      previewRows.map((row, rowIndex) => (
                        <TableRow key={`preview-row-${rowIndex}`}>
                          {selectedColumns.map((column) => (
                            <TableCell
                              key={`preview-row-${rowIndex}-${column}`}
                            >
                              {formatCellValue(row[column])}
                            </TableCell>
                          ))}
                        </TableRow>
                      ))
                    ) : (
                      <TableRow>
                        <TableCell colSpan={selectedColumns.length}>
                          No rows found for the selected filters.
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default ReportsAnalyticsPage;
