import React, { useState, ChangeEvent, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useForm } from "react-hook-form";
import {
  ChevronRight,
  Download,
  File,
  Folder,
  FolderPlus,
  Upload,
  Loader2,
  Plus,
  FolderOpen,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { enhancedApi as api, Organization, Project } from "@/services/enhanced-api";

// Adjusted types based on backend and actual usage
type FolderFormValues = {
  organization: string; // This will be organization._id
  project: string; // This will be project._id
  direction: "Incoming" | "Outgoing";
  year: string;
  month: string;
};

type FileUploadValues = {
  letterNumber: string;
  file: FileList;
};

interface FolderItem {
  id: string; // MongoDB _id
  name: string;
  path: string;
  type: "folder" | "file";
  children?: FolderItem[];
  size?: number;
  created_at?: string; // ISO string
  file_extension?: string;
}

// Helper to shorten names (e.g., "Alpha Corp" -> "AC")
const shortenName = (
  name: string,
  organizations: Organization[],
  projects: Project[]
): string => {
  const org = organizations.find((o) => o.name === name);
  if (org) return org.shortName || org.name.substring(0, 2).toUpperCase();
  const proj = projects.find((p) => p.name === name);
  if (proj) return proj.projectCode || proj.name.substring(0, 2).toUpperCase();
  // Fallback for direction/month, or if no specific shortening is defined
  return name.replace(/\s/g, ""); // Remove spaces
};

// Function to add 'id' and 'type' for frontend display consistency, if not directly from API
const addIdAndType = (item: any): FolderItem => ({
  id: item._id || item.path || Math.random().toString(36).substring(2, 9), // Use _id if available, fallback to path or random
  name: item.name,
  path: item.path,
  type: item.type || (item.children ? "folder" : "file"), // Infer type if missing
  children: item.children ? item.children.map(addIdAndType) : [],
  size: item.size,
  created_at: item.created_at,
  file_extension: item.file_extension,
});

const FolderStructurePage: React.FC = () => {
  const { toast } = useToast();
  const {
    register: registerCreate,
    handleSubmit: handleCreateSubmit,
    formState: { errors: createErrors },
    reset: resetCreateForm,
  } = useForm<FolderFormValues>();
  const {
    register: registerUpload,
    handleSubmit: handleUploadSubmit,
    formState: { errors: uploadErrors },
    reset: resetUploadForm,
  } = useForm<FileUploadValues>();

  const [folderStructure, setFolderStructure] = useState<FolderItem[]>([]);
  const [currentPath, setCurrentPath] = useState<string[]>([]); // Array of path segments
  const [uploading, setUploading] = useState(false);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [loading, setLoading] = useState(true);

  // State for organizations and projects
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingOrganizations, setLoadingOrganizations] = useState(true);
  const [loadingProjects, setLoadingProjects] = useState(true);

  // Effect to fetch organizations and projects
  useEffect(() => {
    const fetchOrganizations = async () => {
      try {
        setLoadingOrganizations(true);
        const orgs = await api.getOrganizations();
        setOrganizations(orgs);
      } catch (error) {
        console.error("Error fetching organizations:", error);
        toast({
          title: "Error",
          description: "Failed to load organizations.",
          variant: "destructive",
        });
      } finally {
        setLoadingOrganizations(false);
      }
    };

    const fetchProjects = async () => {
      try {
        setLoadingProjects(true);
        const projs = await api.getProjects();
        setProjects(projs);
      } catch (error) {
        console.error("Error fetching projects:", error);
        toast({
          title: "Error",
          description: "Failed to load projects.",
          variant: "destructive",
        });
      } finally {
        setLoadingProjects(false);
      }
    };

    fetchOrganizations();
    fetchProjects();
  }, [toast]);

  // Effect to fetch initial folder structure (root)
  useEffect(() => {
    const fetchRootFolderStructure = async () => {
      setLoading(true);
      try {
        const response = await fetch("/api/folder_structure");
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data: FolderItem[] = await response.json();
        setFolderStructure(data); // Root items are directly in the array
        setCurrentPath([]); // Start at root
      } catch (error) {
        console.error("Error fetching root folder structure:", error);
        toast({
          title: "Error",
          description: "Failed to load folder structure.",
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    };
    fetchRootFolderStructure();
  }, [toast]);

  // Determine current folder's contents based on currentPath
  const currentContents = React.useMemo(() => {
    let contents = folderStructure;
    let found = true;

    for (const segment of currentPath) {
      const nextFolder = contents.find(
        (item) => item.type === "folder" && item.name === segment
      );
      if (nextFolder && nextFolder.children) {
        contents = nextFolder.children;
      } else {
        found = false;
        break;
      }
    }
    return found ? contents : [];
  }, [folderStructure, currentPath]);

  // Navigate into a folder
  const navigateToFolder = async (path: string) => {
    setLoading(true);
    try {
      // Backend endpoint now fetches a specific folder's contents, not just root
      const response = await fetch(`/api/folder_structure/${path}`);
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data: FolderItem = await response.json();
      // When navigating, we replace the entire folderStructure state with the fetched folder and its children
      // This simplifies currentContents logic for nested views
      setFolderStructure([data]);
      // Update currentPath to reflect the new depth
      setCurrentPath(
        path.split("/").filter((segment) => segment && segment !== "uploads")
      );
    } catch (error) {
      console.error("Error navigating to folder:", error);
      toast({
        title: "Error",
        description: "Failed to navigate to folder.",
        variant: "destructive",
      });
      // Fallback: If navigation fails, try to fetch root again or clear state
      setFolderStructure([]);
      setCurrentPath([]);
    } finally {
      setLoading(false);
    }
  };

  // Navigate back up the path
  const navigateUp = async () => {
    if (currentPath.length === 0) {
      // Already at root, just re-fetch root
      setLoading(true);
      try {
        const response = await fetch("/api/folder_structure");
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data: FolderItem[] = await response.json();
        setFolderStructure(data);
        setCurrentPath([]);
      } catch (error) {
        console.error("Error fetching root folder structure:", error);
        toast({
          title: "Error",
          description: "Failed to load folder structure.",
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
      return;
    }

    const newPathSegments = currentPath.slice(0, -1);
    setCurrentPath(newPathSegments);

    if (newPathSegments.length === 0) {
      // Navigating back to root, refetch root items
      setLoading(true);
      try {
        const response = await fetch("/api/folder_structure");
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data: FolderItem[] = await response.json();
        setFolderStructure(data);
      } catch (error) {
        console.error("Error fetching root folder structure:", error);
        toast({
          title: "Error",
          description: "Failed to load folder structure.",
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    } else {
      // Navigate to the parent folder
      const parentPath = `uploads/${newPathSegments.join("/")}`;
      await navigateToFolder(parentPath);
    }
  };

  const handleCreateFolder = async (data: FolderFormValues) => {
    setCreatingFolder(true);
    try {
      const organization = organizations.find(
        (o) => o._id === data.organization
      );
      const project = projects.find((p) => p._id === data.project);

      if (!organization || !project) {
        throw new Error("Invalid organization or project selected.");
      }

      const orgShort = shortenName(organization.name, organizations, projects);
      const projectShort = shortenName(project.name, organizations, projects);
      const directionShort = shortenName(
        data.direction,
        organizations,
        projects
      );
      const monthShort = shortenName(data.month, organizations, projects);

      // Construct the full path for the new folder
      const newFolderPath = `uploads/${orgShort}/${projectShort}/${directionShort}/${data.year}/${monthShort}`;

      const folderStructureData = {
        name: data.month, // The name of the month folder being created
        path: newFolderPath,
        type: "folder",
        children: [], // No children upon creation
        organization_id: data.organization, // Pass the organization_id
        project_id: data.project, // Pass the project_id
      };

      const response = await fetch("/api/folder_structure", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(folderStructureData),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to create folder.");
      }

      const result = await response.json();
      toast({
        title: "Success",
        description: result.message,
      });

      resetCreateForm();
      // Re-fetch the current folder contents to update the UI
      if (currentPath.length === 0) {
        // If at root, re-fetch root
        const rootResponse = await fetch("/api/folder_structure");
        if (rootResponse.ok) {
          const rootData: FolderItem[] = await rootResponse.json();
          setFolderStructure(rootData);
        }
      } else {
        // Re-fetch the current folder to see the new sub-folder
        const currentFullPath = `uploads/${currentPath.join("/")}`;
        await navigateToFolder(currentFullPath);
      }
    } catch (error: any) {
      console.error("Error creating folder:", error);
      toast({
        title: "Error",
        description: error.message || "Failed to create folder.",
        variant: "destructive",
      });
    } finally {
      setCreatingFolder(false);
    }
  };

  const handleUploadFile = async (data: FileUploadValues) => {
    setUploading(true);
    try {
      if (!data.file || data.file.length === 0) {
        throw new Error("No file selected.");
      }

      const file = data.file[0];
      const formData = new FormData();
      formData.append("file", file);
      // Backend expects these as query parameters
      formData.append("name", data.letterNumber);
      formData.append("file_extension", ".pdf"); // Assuming only PDF
      // The `path` for upload should be the full path of the *current folder*
      const currentFullPath =
        currentPath.length === 0
          ? "uploads"
          : `uploads/${currentPath.join("/")}`;
      formData.append("path", currentFullPath);

      // Extract organization_id and project_id from the current path or state
      // This is a simplification; ideally, you'd have these from user context or stored state
      const orgSegment = currentPath[0];
      const projSegment = currentPath[1];

      const currentOrg = organizations.find(
        (o) => shortenName(o.name, organizations, projects) === orgSegment
      );
      const currentProj = projects.find(
        (p) => shortenName(p.name, organizations, projects) === projSegment
      );

      if (!currentOrg || !currentProj) {
        throw new Error(
          "Could not determine organization or project from current path for upload."
        );
      }

      // Append organization_id and project_id to formData for backend query params
      formData.append("organization_id", currentOrg._id);
      formData.append("project_id", currentProj._id);

      const response = await fetch(
        `/api/upload_file?name=${encodeURIComponent(
          data.letterNumber
        )}&path=${encodeURIComponent(
          currentFullPath
        )}&file_extension=${encodeURIComponent(
          ".pdf"
        )}&organization_id=${encodeURIComponent(
          currentOrg._id
        )}&project_id=${encodeURIComponent(currentProj._id)}`,
        {
          method: "POST",
          body: formData,
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to upload file.");
      }

      const result = await response.json();
      toast({
        title: "Success",
        description: result.message,
      });

      resetUploadForm();
      // Re-fetch the current folder contents to update the UI
      await navigateToFolder(currentFullPath);
    } catch (error: any) {
      console.error("Error uploading file:", error);
      toast({
        title: "Error",
        description: error.message || "Failed to upload file.",
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  const handleDownload = async (
    itemPath: string,
    itemName: string,
    itemType: string
  ) => {
    try {
      const response = await fetch(
        `/api/folder_structure/download?path=${encodeURIComponent(itemPath)}`,
        {
          method: "GET",
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || `Failed to download ${itemType}.`);
      }

      // For files, download directly. For folders, response is a zip.
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = itemType === "folder" ? `${itemName}.zip` : itemName; // Use itemName for file download
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      toast({
        title: "Success",
        description: `${itemType} downloaded successfully.`,
      });
    } catch (error: any) {
      console.error(`Error downloading ${itemType}:`, error);
      toast({
        title: "Error",
        description: error.message || `Failed to download ${itemType}.`,
        variant: "destructive",
      });
    }
  };

  const handleOpenFile = async (filePath: string) => {
    try {
      const response = await fetch(
        `/api/folder_structure/open_file?file_path=${encodeURIComponent(
          filePath
        )}`,
        {
          method: "GET",
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || "Failed to open file.");
      }

      const data = await response.json();
      if (data.presigned_url) {
        window.open(data.presigned_url, "_blank");
        toast({
          title: "Success",
          description: "File opened in a new tab.",
        });
      } else {
        throw new Error("Presigned URL not received.");
      }
    } catch (error: any) {
      console.error("Error opening file:", error);
      toast({
        title: "Error",
        description: error.message || "Failed to open file.",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="flex min-h-screen">
      {/* Sidebar for Forms */}
      <div className="w-1/3 bg-gray-100 p-6 space-y-6">
        {/* Create Folder Card */}
        <Card>
          <CardHeader>
            <CardTitle>Create New Folder</CardTitle>
            <CardDescription>
              Generate a new folder structure
              (Org/Project/Direction/Year/Month).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={handleCreateSubmit(handleCreateFolder)}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="organization">Organization</Label>
                <Select {...registerCreate("organization", { required: true })}>
                  <SelectTrigger id="organization">
                    <SelectValue
                      placeholder={
                        loadingOrganizations
                          ? "Loading..."
                          : "Select an organization"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {loadingOrganizations ? (
                      <SelectItem value="" disabled>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Loading organizations...
                      </SelectItem>
                    ) : (
                      organizations.map((org) => (
                        <SelectItem key={org._id} value={org._id}>
                          {org.name}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
                {createErrors.organization && (
                  <p className="text-red-500 text-sm">
                    Organization is required.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="project">Project</Label>
                <Select {...registerCreate("project", { required: true })}>
                  <SelectTrigger id="project">
                    <SelectValue
                      placeholder={
                        loadingProjects ? "Loading..." : "Select a project"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {loadingProjects ? (
                      <SelectItem value="" disabled>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Loading projects...
                      </SelectItem>
                    ) : (
                      projects.map((proj) => (
                        <SelectItem key={proj._id} value={proj._id}>
                          {proj.name}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
                {createErrors.project && (
                  <p className="text-red-500 text-sm">Project is required.</p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="direction">Direction</Label>
                <RadioGroup
                  onValueChange={(value: "Incoming" | "Outgoing") =>
                    registerCreate("direction").onChange({
                      target: { value },
                    })
                  }
                  defaultValue="Incoming"
                  className="flex items-center space-x-4"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="Incoming" id="r1" />
                    <Label htmlFor="r1">Incoming</Label>
                  </div>
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="Outgoing" id="r2" />
                    <Label htmlFor="r2">Outgoing</Label>
                  </div>
                </RadioGroup>
                {createErrors.direction && (
                  <p className="text-red-500 text-sm">Direction is required.</p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="year">Year</Label>
                <Input
                  id="year"
                  type="number"
                  placeholder="e.g., 2023"
                  {...registerCreate("year", {
                    required: true,
                    pattern: /^\d{4}$/,
                  })}
                />
                {createErrors.year && (
                  <p className="text-red-500 text-sm">
                    Year is required and must be 4 digits.
                  </p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="month">Month</Label>
                <Select {...registerCreate("month", { required: true })}>
                  <SelectTrigger id="month">
                    <SelectValue placeholder="Select a month" />
                  </SelectTrigger>
                  <SelectContent>
                    {[
                      "January",
                      "February",
                      "March",
                      "April",
                      "May",
                      "June",
                      "July",
                      "August",
                      "September",
                      "October",
                      "November",
                      "December",
                    ].map((month) => (
                      <SelectItem key={month} value={month}>
                        {month}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {createErrors.month && (
                  <p className="text-red-500 text-sm">Month is required.</p>
                )}
              </div>

              <Button
                type="submit"
                className="w-full"
                disabled={
                  creatingFolder || loadingOrganizations || loadingProjects
                }
              >
                {creatingFolder ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating...
                  </>
                ) : (
                  <>
                    <FolderPlus className="mr-2 h-4 w-4" />
                    Create Folder
                  </>
                )}
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Upload File Card */}
        <Card>
          <CardHeader>
            <CardTitle>Upload File</CardTitle>
            <CardDescription>
              Upload a file to the current folder.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4">
              <div className="flex items-center justify-between">
                <p className="text-sm text-gray-500">
                  Current Upload Path:{" "}
                  <span className="font-medium">
                    {currentPath.length === 0 ? "Root" : currentPath.join("/")}
                  </span>
                </p>
              </div>
              <div className="space-y-4">
                <form
                  onSubmit={handleUploadSubmit(handleUploadFile)}
                  className="space-y-4"
                >
                  <div className="space-y-2">
                    <Label htmlFor="letterNumber">Letter/Document Number</Label>
                    <Input
                      id="letterNumber"
                      placeholder="e.g., ABC-2023-001"
                      {...registerUpload("letterNumber", { required: true })}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="file">Select File (PDF)</Label>
                    <Input
                      id="file"
                      type="file"
                      accept=".pdf"
                      {...registerUpload("file", { required: true })}
                    />
                    <p className="text-xs text-gray-500">
                      File will be renamed to {"{Letter Number}.pdf"}
                    </p>
                  </div>

                  <Button
                    type="submit"
                    variant="outline"
                    className="w-full"
                    disabled={uploading}
                  >
                    {uploading ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Uploading...
                      </>
                    ) : (
                      <>
                        <Upload className="mr-2 h-4 w-4" />
                        Upload File
                      </>
                    )}
                  </Button>
                </form>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Main Content Area - Folder Structure Display */}
      <div className="flex-1 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Folder Structure</CardTitle>
            <CardDescription>
              Browse and manage your organized documents.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {/* Breadcrumbs */}
            <Breadcrumb className="mb-4">
              <BreadcrumbList>
                <BreadcrumbItem>
                  <BreadcrumbLink
                    onClick={navigateUp}
                    className="cursor-pointer"
                  >
                    Root
                  </BreadcrumbLink>
                </BreadcrumbItem>
                {currentPath.map((segment, index) => (
                  <React.Fragment key={index}>
                    <BreadcrumbSeparator>
                      <ChevronRight />
                    </BreadcrumbSeparator>
                    <BreadcrumbItem>
                      <BreadcrumbLink
                        onClick={() =>
                          navigateToFolder(
                            `uploads/${currentPath
                              .slice(0, index + 1)
                              .join("/")}`
                          )
                        }
                        className="cursor-pointer"
                      >
                        {segment}
                      </BreadcrumbLink>
                    </BreadcrumbItem>
                  </React.Fragment>
                ))}
              </BreadcrumbList>
            </Breadcrumb>

            <Separator className="mb-4" />

            {loading ? (
              <div className="flex justify-center items-center h-40">
                <Loader2 className="h-8 w-8 animate-spin text-gray-500" />
                <span className="ml-2 text-gray-500">Loading...</span>
              </div>
            ) : (
              <ScrollArea className="h-[calc(100vh-280px)] pr-4">
                {currentContents.length === 0 ? (
                  <p className="text-gray-500">This folder is empty.</p>
                ) : (
                  <div className="grid gap-2">
                    {currentContents.map((item) => (
                      <div
                        key={item.id}
                        className="flex items-center justify-between p-2 rounded-md hover:bg-gray-50 transition-colors"
                      >
                        <div
                          className="flex items-center gap-2 cursor-pointer"
                          onClick={() =>
                            item.type === "folder"
                              ? navigateToFolder(item.path)
                              : handleOpenFile(item.path)
                          }
                        >
                          {item.type === "folder" ? (
                            <FolderOpen className="h-5 w-5 text-blue-500" />
                          ) : (
                            <File className="h-5 w-5 text-gray-500" />
                          )}
                          <span>{item.name}</span>
                          {item.type === "file" && item.file_extension && (
                            <span className="text-xs text-gray-500 ml-1">
                              ({item.file_extension.substring(1).toUpperCase()})
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          {item.type === "file" && item.size && (
                            <span className="text-sm text-gray-500">
                              {(item.size / 1024).toFixed(2)} KB
                            </span>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              handleDownload(item.path, item.name, item.type)
                            }
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                          {/* Add a delete button for demonstration */}
                          {/* <Button variant="ghost" size="sm" onClick={() => handleDelete(item.path, item.type)}>
                            <Trash2 className="h-4 w-4 text-red-500" />
                          </Button> */}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default FolderStructurePage;
