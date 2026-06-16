import React, { useState, ChangeEvent, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useNavigate } from "react-router-dom";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Upload,
  X,
  FileText,
  Image,
  RefreshCw,
  Link as LinkIcon,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import enhancedApi, {
  BulkUploadStatus,
  Organization as OrgModel,
} from "@/services/enhanced-api";

interface UploadFile {
  id: string;
  file: File;
  name: string;
  size: number;
  type: string;
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

type FileWithRelativePath = File & { webkitRelativePath?: string };

const UploadPage: React.FC = () => {
  const navigate = useNavigate();
  const { toast } = useToast();

  // Single upload states
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [uploadType, setUploadType] = useState<"incoming" | "outgoing">(
    "incoming"
  );
  const [letterNo, setLetterNo] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [letterDate, setLetterDate] = useState("");
  const [subject, setSubject] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [subTags, setSubTags] = useState<string[]>([]);
  const [status, setStatus] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(true);
  const [compressionEnabled, setCompressionEnabled] = useState(false);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [pathStructure, setPathStructure] = useState<string>("");
  const [bulkOrganizationId, setBulkOrganizationId] = useState<string>("");
  const [bulkProjectId, setBulkProjectId] = useState<string>("");
  const [bulkPathStructure, setBulkPathStructure] =
    useState<string>("generating-path...");
  const [bulkFolderStructurePreview, setBulkFolderStructurePreview] = useState<
    string[]
  >([]);
  const [isFolderDragOver, setIsFolderDragOver] = useState(false);

  // Controls
  const [uploading, setUploading] = useState<boolean>(false);

  // Bulk upload states
  const [bulkFiles, setBulkFiles] = useState<FileWithRelativePath[]>([]);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [bulkJobId, setBulkJobId] = useState<string | null>(null);
  const [bulkStatus, setBulkStatus] = useState<BulkUploadStatus | null>(null);
  const [bulkUploading, setBulkUploading] = useState<boolean>(false);
  const pollRef = useRef<number | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);

  // Normalize date for API (YYYY-MM-DD)
  const formatDateForApi = (d: string) => {
    if (!d) return new Date().toISOString().slice(0, 10);
    try {
      const dt = new Date(d);
      if (!isNaN(dt.getTime())) return dt.toISOString().slice(0, 10);
    } catch {}
    return new Date().toISOString().slice(0, 10);
  };

  // Add this utility function near the top of the file
  const shortenName = (name: string, maxLength: number = 10): string => {
    return (
      name
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-") // Replace special chars with hyphens
        .replace(/(^-+|-+$)/g, "") // Trim hyphens
        .substring(0, maxLength) // Truncate
        .replace(/-+/g, "-") // Remove consecutive hyphens
        .replace(/(^-|-$)/g, "") || "untitled"
    ); // Fallback
  };

  // Fetch organizations and projects
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      try {
        const orgs = await enhancedApi.getOrganizations();
        // Map to local Organization interface
        const mappedOrgs: Organization[] = (orgs as OrgModel[]).map((o) => ({
          _id: (o as any)._id,
          name: (o as any).name,
        }));
        setOrganizations(mappedOrgs);

        const projs = await enhancedApi.getProjects();
        const mappedProjs: Project[] = projs.map((p: any) => ({
          _id: p._id,
          name: p.name,
          organization_id: p.organization_id,
        }));
        setProjects(mappedProjs);
      } catch (err: any) {
        setError(err?.message || "Failed to load organizations/projects");
        toast({
          title: "Error",
          description: err?.message || "Failed to load organizations/projects",
          variant: "destructive",
        });
      } finally {
        setIsLoading(false);
      }
    };
    fetchData();
  }, [toast]);

  // Update pathStructure preview when org/project changes
  useEffect(() => {
    if (
      organizationId &&
      projectId &&
      organizations.length &&
      projects.length
    ) {
      const org = organizations.find((o) => o._id === organizationId);
      const project = projects.find((p) => p._id === projectId);
      if (org && project) {
        setPathStructure(
          `${shortenName(org.name)}/${shortenName(project.name)}/`
        );
      } else {
        setPathStructure("generating-path...");
      }
    } else {
      setPathStructure("generating-path...");
    }
  }, [organizationId, projectId, organizations, projects]);

  useEffect(() => {
    if (
      bulkOrganizationId &&
      bulkProjectId &&
      organizations.length &&
      projects.length
    ) {
      const org = organizations.find((o) => o._id === bulkOrganizationId);
      const project = projects.find((p) => p._id === bulkProjectId);
      if (org && project) {
        setBulkPathStructure(
          `${shortenName(org.name)}/${shortenName(project.name)}/`
        );
        return;
      }
    }
    setBulkPathStructure("generating-path...");
  }, [bulkOrganizationId, bulkProjectId, organizations, projects]);

  useEffect(() => {
    if (folderInputRef.current) {
      folderInputRef.current.setAttribute("webkitdirectory", "");
      folderInputRef.current.setAttribute("directory", "");
      folderInputRef.current.setAttribute("mozdirectory", "");
    }
  }, []);

  const handleBulkOrganizationChange = (value: string) => {
    setBulkOrganizationId(value);
    setBulkProjectId("");
  };

  const processFolderSelection = (fileList: FileList | File[]) => {
    const incomingFiles = Array.from(fileList) as FileWithRelativePath[];
    if (!incomingFiles.length) {
      setBulkFiles([]);
      setBulkFolderStructurePreview([]);
      setIsFolderDragOver(false);
      return;
    }

    // Check if this is a folder upload by looking for webkitRelativePath
    const hasRelativePaths = incomingFiles.some(
      (file) => file.webkitRelativePath
    );
    const filesMissingRelativePath = incomingFiles.some(
      (file) => !file.webkitRelativePath
    );

    // If some files have relative paths and some don't, it's likely a mixed selection
    if (hasRelativePaths && filesMissingRelativePath) {
      setBulkFiles([]);
      setBulkFolderStructurePreview([]);
      setIsFolderDragOver(false);
      toast({
        title: "Mixed File Selection",
        description:
          "Please select either individual files or a complete folder, not both.",
        variant: "destructive",
      });
      return;
    }

    // If no files have relative paths, this might be individual file selection or unsupported browser
    if (!hasRelativePaths) {
      // Check if user is trying to upload a folder but browser doesn't support it
      if (incomingFiles.length > 10) {
        // Heuristic: many files might indicate folder attempt
        toast({
          title: "Folder Upload Not Supported",
          description:
            "Your browser doesn't support folder uploads. Please use Chrome, Edge, or Firefox, or compress the folder into a ZIP file.",
          variant: "destructive",
        });
      } else {
        toast({
          title: "Individual Files Detected",
          description:
            "For bulk upload, please select a folder or use the regular upload tab for individual files.",
          variant: "destructive",
        });
      }
      setBulkFiles([]);
      setBulkFolderStructurePreview([]);
      setIsFolderDragOver(false);
      return;
    }

    // Sort files by their relative path for consistent processing
    const sortedFiles = [...incomingFiles].sort((a, b) => {
      const aPath = (a.webkitRelativePath || a.name).toLowerCase();
      const bPath = (b.webkitRelativePath || b.name).toLowerCase();
      return aPath.localeCompare(bPath);
    });

    // Validate that all files come from a single root folder
    const rootFolders = new Set<string>();
    sortedFiles.forEach((file) => {
      const relativePath = file.webkitRelativePath || "";
      if (relativePath.includes("/")) {
        const topLevel = relativePath.split("/")[0];
        rootFolders.add(topLevel);
      } else {
        rootFolders.add("__root__");
      }
    });

    if (rootFolders.size > 1) {
      setBulkFiles([]);
      setBulkFolderStructurePreview([]);
      setIsFolderDragOver(false);
      toast({
        title: "Multiple Root Folders",
        description:
          "Please select a single folder. Multiple root folders detected in your selection.",
        variant: "destructive",
      });
      return;
    }

    // Filter out system files and hidden files
    const filteredFiles = sortedFiles.filter((file) => {
      const fileName = file.name.toLowerCase();
      const relativePath = file.webkitRelativePath || "";

      // Skip system files
      if (
        fileName.startsWith(".") ||
        fileName === "thumbs.db" ||
        fileName === "desktop.ini"
      ) {
        return false;
      }

      // Skip files in hidden directories
      if (relativePath.includes("/.") || relativePath.includes("\\.")) {
        return false;
      }

      return true;
    });

    if (filteredFiles.length === 0) {
      setBulkFiles([]);
      setBulkFolderStructurePreview([]);
      setIsFolderDragOver(false);
      toast({
        title: "No Valid Files",
        description:
          "No valid files found in the selected folder. Please ensure the folder contains document files.",
        variant: "destructive",
      });
      return;
    }

    // Generate folder structure preview
    const folderPaths = new Set<string>();
    filteredFiles.forEach((file) => {
      const relativePath = file.webkitRelativePath || "";
      if (relativePath.includes("/")) {
        const folderPath = relativePath.substring(
          0,
          relativePath.lastIndexOf("/")
        );
        folderPaths.add(folderPath);
      } else {
        folderPaths.add("(root)");
      }
    });

    const folderPreview = Array.from(folderPaths)
      .filter(Boolean)
      .sort((a, b) => {
        // Sort with root first, then alphabetically
        if (a === "(root)") return -1;
        if (b === "(root)") return 1;
        return a.localeCompare(b);
      });

    setBulkFiles(filteredFiles);
    setBulkFolderStructurePreview(
      folderPreview.length ? folderPreview : ["(root)"]
    );
    setIsFolderDragOver(false);

    // Clear the input value to allow re-selection of the same folder
    if (folderInputRef.current) {
      folderInputRef.current.value = "";
    }

    toast({
      title: "Folder Selected",
      description: `Successfully selected ${filteredFiles.length} files from the folder structure.`,
    });
  };

  const handleFolderChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!event.target.files) return;
    processFolderSelection(event.target.files);
  };

  const handleFolderDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsFolderDragOver(false);

    const { files, items } = event.dataTransfer;

    // Check if items are available (better folder detection)
    if (items && items.length > 0) {
      // Check if any item is a directory
      const hasDirectories = Array.from(items).some(
        (item) =>
          item.kind === "file" &&
          item.webkitGetAsEntry &&
          item.webkitGetAsEntry()?.isDirectory
      );

      if (hasDirectories) {
        // Handle directory drop using webkitGetAsEntry API
        handleDirectoryDrop(items);
        return;
      }
    }

    // Fallback to regular file handling
    if (files && files.length) {
      processFolderSelection(files);
    }
  };

  const handleDirectoryDrop = async (items: DataTransferItemList) => {
    const files: FileWithRelativePath[] = [];

    const processEntry = async (entry: any, path = "") => {
      if (entry.isFile) {
        return new Promise<void>((resolve) => {
          entry.file((file: File) => {
            const fileWithPath = file as FileWithRelativePath;
            fileWithPath.webkitRelativePath = path + file.name;
            files.push(fileWithPath);
            resolve();
          });
        });
      } else if (entry.isDirectory) {
        const dirReader = entry.createReader();
        return new Promise<void>((resolve) => {
          dirReader.readEntries(async (entries: any[]) => {
            for (const childEntry of entries) {
              await processEntry(childEntry, path + entry.name + "/");
            }
            resolve();
          });
        });
      }
    };

    try {
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === "file") {
          const entry = item.webkitGetAsEntry();
          if (entry) {
            await processEntry(entry);
          }
        }
      }

      if (files.length > 0) {
        processFolderSelection(files);
      }
    } catch (error) {
      console.error("Error processing directory drop:", error);
      toast({
        title: "Folder Drop Error",
        description:
          "Failed to process the dropped folder. Please try selecting the folder using the file picker.",
        variant: "destructive",
      });
    }
  };

  const handleFolderDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    if (!isFolderDragOver) {
      setIsFolderDragOver(true);
    }
  };

  const handleFolderDragLeave = () => {
    if (isFolderDragOver) {
      setIsFolderDragOver(false);
    }
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    const selectedFiles = Array.from(e.target.files);
    const newFiles = selectedFiles.map((file) => ({
      id: Math.random().toString(36).substr(2, 9),
      file,
      name: file.name,
      size: file.size,
      type: file.type,
    }));
    setFiles((prev) => [...prev, ...newFiles]);
  };

  const handleUpload = async () => {
    if (files.length === 0) {
      toast({
        title: "Warning",
        description: "Please select files to upload.",
        variant: "destructive",
      });
      return;
    }
    if (!organizationId || !projectId) {
      toast({
        title: "Warning",
        description: "Please select organization and project",
        variant: "destructive",
      });
      return;
    }

    try {
      setUploading(true);
      let firstDocId: string | null = null;

      // Upload sequentially to match backend single-file contract
      for (let i = 0; i < files.length; i++) {
        const f = files[i].file;
        const autoLetter =
          letterNo || `AUTO-${new Date().getFullYear()}-${Date.now()}-${i + 1}`;

        const doc = await enhancedApi.uploadDocument({
          file: f,
          organization_id: organizationId,
          project_id: projectId,
          uploadType,
          letterNo: autoLetter,
          date: formatDateForApi(letterDate),
          subject,
          from,
          to,
          tags,
          subTags,
          status: status || "draft",
          ocrEnabled,
          compressionEnabled,
        });

        const id = (doc as any)?._id || (doc as any)?.id;
        if (!firstDocId && id) firstDocId = id;
      }

      if (firstDocId) {
        navigate(`/documentviewer/${firstDocId}`);
      }

      toast({
        title: "Success",
        description: `Uploaded ${files.length} file${
          files.length > 1 ? "s" : ""
        } successfully.`,
      });

      // Reset
      setFiles([]);
      setLetterNo("");
      setOcrEnabled(true);
      setCompressionEnabled(false);
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || "Upload failed",
        variant: "destructive",
      });
    } finally {
      setUploading(false);
    }
  };

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  if (isLoading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  // H7: the "From URL" / cloud-import UI is not wired to any backend import
  // service. Shipping it as-is is a misleading dead-end, and building it for real
  // requires SSRF protection (URL allow-listing, content validation, AV scan).
  // Feature-flag it off until a secure import endpoint exists; flip to true then.
  const URL_IMPORT_ENABLED = false;

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Upload Documents</h1>
      </div>

      <Tabs defaultValue="upload" className="mb-8">
        <TabsList
          className={`grid w-full md:w-[400px] ${
            URL_IMPORT_ENABLED ? "grid-cols-3" : "grid-cols-2"
          }`}
        >
          <TabsTrigger value="upload">Upload</TabsTrigger>
          {URL_IMPORT_ENABLED && <TabsTrigger value="url">From URL</TabsTrigger>}
          <TabsTrigger value="bulk">Bulk Upload</TabsTrigger>
        </TabsList>

        {/* Single Upload */}
        <TabsContent value="upload" className="mt-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <Card className="md:col-span-2">
              <CardHeader>
                <CardTitle>Upload Files</CardTitle>
                <CardDescription>
                  Drag and drop files or click to browse
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div
                  className="border-2 border-dashed rounded-lg p-12 text-center hover:bg-accent transition-colors cursor-pointer"
                  onClick={() => document.getElementById("fileInput")?.click()}
                >
                  <input
                    type="file"
                    id="fileInput"
                    className="hidden"
                    multiple
                    onChange={handleFileChange}
                    accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.gif"
                  />
                  <div className="flex flex-col items-center">
                    <Upload className="h-12 w-12 text-muted-foreground mb-4" />
                    <h3 className="text-lg font-medium mb-2">
                      Drag files here or click to browse
                    </h3>
                    <p className="text-sm text-muted-foreground mb-4">
                      {uploadType === "incoming"
                        ? "Support for PDF, DOC, DOCX, TXT, JPG, PNG, GIF images files"
                        : "Support for PDF, DOC, DOCX, TXT, JPG, PNG, GIF images files"}
                    </p>
                    <Button>Select Files</Button>
                  </div>
                </div>

                {files.length > 0 && (
                  <>
                    <div className="mt-6">
                      <h3 className="text-lg font-medium mb-4">Upload Queue</h3>
                      <div className="space-y-4">
                        {files.map((file) => (
                          <div
                            key={file.id}
                            className="flex items-center p-3 border rounded-md"
                          >
                            <div className="mr-3">
                              {file.file.type.includes("image") ? (
                                <Image className="h-8 w-8 text-blue-500" />
                              ) : (
                                <FileText className="h-8 w-8 text-blue-500" />
                              )}
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-sm font-medium truncate">
                                {file.name}
                              </p>
                              <div className="flex items-center">
                                <span className="text-xs text-muted-foreground whitespace-nowrap">
                                  {formatFileSize(file.size)}
                                </span>
                              </div>
                            </div>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="ml-2"
                              onClick={() => removeFile(file.id)}
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </div>
                        ))}
                      </div>
                      <div className="flex justify-between mt-4">
                        <p className="text-sm text-muted-foreground">
                          {files.length} file{files.length !== 1 ? "s" : ""}{" "}
                          selected
                        </p>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setFiles([])}
                        >
                          <RefreshCw className="h-4 w-4 mr-2" />
                          Clear All
                        </Button>
                      </div>
                    </div>
                    <div className="mt-4 p-3 border rounded-md bg-muted">
                      <p className="text-sm text-muted-foreground">
                        Files will be saved to:{" "}
                        <span className="font-mono text-primary">
                          {pathStructure || "generating-path..."}
                        </span>
                      </p>
                    </div>
                  </>
                )}
              </CardContent>

              <CardContent>
                <div className="flex justify-between mt-4">
                  <div className="space-y-1">
                    <Label htmlFor="ocr">Enable OCR</Label>
                    <p className="text-sm text-muted-foreground">
                      Extract text from documents
                    </p>
                  </div>
                  <Switch
                    id="ocr"
                    checked={ocrEnabled}
                    onCheckedChange={setOcrEnabled}
                  />
                </div>

                <div className="flex justify-between mt-4">
                  <div className="space-y-1">
                    <Label htmlFor="compression">Compress files</Label>
                    <p className="text-sm text-muted-foreground">
                      Reduce file size for storage
                    </p>
                  </div>
                  <Switch
                    id="compression"
                    checked={compressionEnabled}
                    onCheckedChange={setCompressionEnabled}
                  />
                </div>
              </CardContent>

              <CardFooter>
                <Button
                  className="mr-2"
                  onClick={handleUpload}
                  disabled={uploading}
                >
                  {uploading ? "Uploading..." : "Upload"}
                </Button>
                <Button variant="outline">Cancel</Button>
              </CardFooter>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Upload Settings</CardTitle>
                <CardDescription>
                  Configure your upload preferences
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="space-y-2">
                  <Label htmlFor="fileType">File Type</Label>
                  <Select
                    value={uploadType}
                    onValueChange={(value: "incoming" | "outgoing") =>
                      setUploadType(value)
                    }
                  >
                    <SelectTrigger id="fileType">
                      <SelectValue placeholder="Select file type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="incoming">Incoming</SelectItem>
                      <SelectItem value="outgoing">Outgoing</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="organization">Organization</Label>
                  <Select
                    onValueChange={setOrganizationId}
                    value={organizationId || ""}
                  >
                    <SelectTrigger id="organization">
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

                <div className="space-y-2">
                  <Label htmlFor="project">Project</Label>
                  <Select
                    onValueChange={setProjectId}
                    value={projectId || ""}
                    disabled={!organizationId}
                  >
                    <SelectTrigger id="project">
                      <SelectValue placeholder="Select project" />
                    </SelectTrigger>
                    <SelectContent>
                      {projects
                        .filter(
                          (project) =>
                            project.organization_id === organizationId
                        )
                        .map((project) => (
                          <SelectItem key={project._id} value={project._id}>
                            {project.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="letterNo">Letter Number</Label>
                  <Input
                    type="text"
                    id="letterNo"
                    placeholder="Enter letter number"
                    value={letterNo}
                    onChange={(e) => setLetterNo(e.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="letterDate">Letter Date</Label>
                  <Input
                    type="date"
                    id="letterDate"
                    placeholder="Enter letter date"
                    value={letterDate}
                    onChange={(e) => setLetterDate(e.target.value)}
                  />
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* From URL (H7: disabled — not wired to a backend import service) */}
        {URL_IMPORT_ENABLED && (
        <TabsContent value="url" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Import from URL</CardTitle>
              <CardDescription>
                Import documents from web addresses or cloud storage
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="docUrl">Document URL</Label>
                <div className="flex">
                  <div className="relative flex-grow">
                    <LinkIcon
                      className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500"
                      size={16}
                    />
                    <Input
                      id="docUrl"
                      placeholder="https://example.com/document.pdf"
                      className="pl-10"
                    />
                  </div>
                  <Button className="ml-2">Import</Button>
                </div>
                <p className="text-sm text-muted-foreground">
                  Enter the URL of a document or file to import
                </p>
              </div>

              <div className="pt-4">
                <h3 className="text-sm font-medium mb-2">
                  Connect to cloud storage
                </h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Button variant="outline" className="h-20 flex flex-col">
                    <svg
                      className="h-6 w-6 mb-1"
                      viewBox="0 0 87 66"
                      fill="none"
                      xmlns="http://www.w3.org/2000/svg"
                    >
                      <path
                        d="M55.5 12L42 36H70.5L84 12H55.5Z"
                        fill="#0066DA"
                      />
                      <path d="M42 36L28.5 12L0 12L14 36H42Z" fill="#00AC47" />
                      <path d="M56 38H42H14V62H70V38H56Z" fill="#EA4335" />
                      <path d="M28.5 12L42 36L56 12H28.5Z" fill="#00832D" />
                      <path
                        d="M70.5 12L56 12L56 38H84V36L70.5 12Z"
                        fill="#2684FC"
                      />
                      <path d="M42 36L28 36V62L42 36Z" fill="#C5221F" />
                    </svg>
                    <span className="text-xs">Google Drive</span>
                  </Button>
                  <Button variant="outline" className="h-20 flex flex-col">
                    <svg
                      className="h-6 w-6 mb-1"
                      viewBox="0 0 24 24"
                      fill="none"
                      xmlns="http://www.w3.org/2000/svg"
                    >
                      <path
                        d="M18.5 14.25L17.13 11L15.75 14.25H18.5Z"
                        fill="#0061FF"
                      />
                      <path
                        d="M8.25 14.25L6.88 11L5.5 14.25H8.25Z"
                        fill="#0061FF"
                      />
                      <path
                        d="M13.38 11L12 7.75L10.63 11H13.38Z"
                        fill="#0061FF"
                      />
                      <path
                        d="M12 18.25L10.63 15H13.38L12 18.25Z"
                        fill="#0061FF"
                      />
                      <path
                        d="M8.25 11L6.88 7.75L5.5 11H8.25Z"
                        fill="#0061FF"
                      />
                      <path
                        d="M18.5 11L17.13 7.75L15.75 11H18.5Z"
                        fill="#0061FF"
                      />
                    </svg>
                    <span className="text-xs">Dropbox</span>
                  </Button>
                  <Button variant="outline" className="h-20 flex flex-col">
                    <svg
                      className="h-6 w-6 mb-1"
                      viewBox="0 0 24 24"
                      fill="none"
                      xmlns="http://www.w3.org/2000/svg"
                    >
                      <path
                        d="M12 2L6 8V16L12 22L18 16V8L12 2Z"
                        fill="#5E5CE6"
                      />
                      <path d="M12 22V12L6 8V16L12 22Z" fill="#4B48C8" />
                      <path d="M12 22L18 16V8L12 12V22Z" fill="#4B48C8" />
                      <path d="M12 2L6 8L12 12L18 8L12 2Z" fill="#7069FA" />
                    </svg>
                    <span className="text-xs">OneDrive</span>
                  </Button>
                  <Button variant="outline" className="h-20 flex flex-col">
                    <svg
                      className="h-6 w-6 mb-1"
                      viewBox="0 0 24 24"
                      fill="none"
                      xmlns="http://www.w3.org/2000/svg"
                    >
                      <path d="M5 5H19V19H5V5Z" fill="#888888" />
                      <path d="M9 9H15V15H9V9Z" fill="white" />
                    </svg>
                    <span className="text-xs">More Services</span>
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
        )}

        {/* Bulk Upload */}
        <TabsContent value="bulk" className="mt-6">
          <Card>
            <CardHeader>
              <CardTitle>Bulk Upload</CardTitle>
              <CardDescription>
                Upload multiple documents at once with metadata
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="space-y-6">
                <div className="space-y-2">
                  <Label>Folder Upload</Label>
                  <div
                    className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer ${
                      isFolderDragOver
                        ? "border-primary bg-primary/5"
                        : "hover:bg-accent"
                    }`}
                    role="button"
                    tabIndex={0}
                    onClick={() => folderInputRef.current?.click()}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        folderInputRef.current?.click();
                      }
                    }}
                    onDrop={handleFolderDrop}
                    onDragOver={handleFolderDragOver}
                    onDragEnter={handleFolderDragOver}
                    onDragLeave={handleFolderDragLeave}
                  >
                    <input
                      ref={folderInputRef}
                      type="file"
                      id="folderInput"
                      className="hidden"
                      multiple
                      webkitdirectory=""
                      directory=""
                      onChange={handleFolderChange}
                    />
                    <Upload className="h-8 w-8 text-muted-foreground mb-3 mx-auto" />
                    <p>Upload an entire folder</p>
                    <Button
                      size="sm"
                      className="mt-2"
                      onClick={() => folderInputRef.current?.click()}
                    >
                      Select Folder
                    </Button>
                    {bulkFiles.length > 0 && (
                      <p className="mt-3 text-xs text-muted-foreground">
                        {bulkFiles.length} file
                        {bulkFiles.length === 1 ? "" : "s"} detected
                      </p>
                    )}
                  </div>
                  <div className="mt-3 rounded-md border bg-muted p-3 text-left">
                    <p className="text-sm text-muted-foreground">
                      Files will be saved to{" "}
                      <span className="font-mono text-primary">
                        {bulkPathStructure || "generating-path..."}
                      </span>
                    </p>
                  </div>
                  {bulkFolderStructurePreview.length > 0 && (
                    <div className="rounded-md border bg-muted/60 p-3 text-left">
                      <p className="text-sm font-medium">Detected folders</p>
                      <ul className="mt-2 max-h-32 overflow-auto text-xs font-mono text-muted-foreground space-y-1">
                        {bulkFolderStructurePreview.map((folder) => (
                          <li key={folder}>{folder}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                <div className="rounded-lg border p-4 space-y-4">
                  <div>
                    <h3 className="text-lg font-medium">Upload Settings</h3>
                    <p className="text-sm text-muted-foreground">
                      Choose where bulk uploads are stored
                    </p>
                  </div>
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="bulkOrganization">Organization</Label>
                      <Select
                        onValueChange={handleBulkOrganizationChange}
                        value={bulkOrganizationId || ""}
                      >
                        <SelectTrigger id="bulkOrganization">
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
                    <div className="space-y-2">
                      <Label htmlFor="bulkProject">Project</Label>
                      <Select
                        onValueChange={setBulkProjectId}
                        value={bulkProjectId || ""}
                        disabled={!bulkOrganizationId}
                      >
                        <SelectTrigger id="bulkProject">
                          <SelectValue placeholder="Select project" />
                        </SelectTrigger>
                        <SelectContent>
                          {projects
                            .filter(
                              (project) =>
                                project.organization_id === bulkOrganizationId
                            )
                            .map((project) => (
                              <SelectItem key={project._id} value={project._id}>
                                {project.name}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>CSV Metadata Upload</Label>
                  <div className="border rounded-lg p-4">
                    <p className="text-sm mb-3">
                      Upload a CSV file with document metadata for batch
                      processing
                    </p>
                    <div className="flex items-center space-x-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={async () => {
                          try {
                            const blob =
                              await enhancedApi.downloadBulkUploadTemplate();
                            const url = window.URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = "bulk-upload-template.csv";
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            window.URL.revokeObjectURL(url);
                          } catch (error: any) {
                            toast({
                              title: "Error",
                              description:
                                error?.message || "Failed to download template",
                              variant: "destructive",
                            });
                          }
                        }}
                      >
                        Download Template
                      </Button>
                      <Button
                        size="sm"
                        onClick={() =>
                          document.getElementById("csvInput")?.click()
                        }
                      >
                        Upload CSV
                      </Button>
                      <input
                        type="file"
                        id="csvInput"
                        className="hidden"
                        accept=".csv"
                        onChange={(e) => {
                          if (!e.target.files || e.target.files.length === 0)
                            return;
                          setCsvFile(e.target.files[0]);
                        }}
                      />
                    </div>
                    {csvFile && (
                      <p className="text-xs mt-1 truncate" title={csvFile.name}>
                        Selected CSV: {csvFile.name}
                      </p>
                    )}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label>Batch Processing Options</Label>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                      <Checkbox id="createFolders" />
                      <Label
                        htmlFor="createFolders"
                        className="text-sm font-normal"
                      >
                        Maintain folder structure
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <Checkbox id="autoProcess" defaultChecked />
                      <Label
                        htmlFor="autoProcess"
                        className="text-sm font-normal"
                      >
                        Auto-process documents
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <Checkbox id="extractMeta" defaultChecked />
                      <Label
                        htmlFor="extractMeta"
                        className="text-sm font-normal"
                      >
                        Extract metadata from files
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <Checkbox id="notifyComplete" defaultChecked />
                      <Label
                        htmlFor="notifyComplete"
                        className="text-sm font-normal"
                      >
                        Notify when complete
                      </Label>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>

            <CardFooter>
              <Button
                className="mr-2"
                disabled={bulkUploading}
                onClick={async () => {
                  if (!bulkOrganizationId || !bulkProjectId) {
                    toast({
                      title: "Warning",
                      description: "Please select organization and project",
                      variant: "destructive",
                    });
                    return;
                  }
                  if (!csvFile) {
                    toast({
                      title: "Warning",
                      description: "Please select a CSV metadata file",
                      variant: "destructive",
                    });
                    return;
                  }
                  if (bulkFiles.length === 0) {
                    toast({
                      title: "Warning",
                      description: "Please select folder files to upload",
                      variant: "destructive",
                    });
                    return;
                  }
                  // Validate folder structure before upload
                  const filesWithoutPaths = bulkFiles.filter(
                    (file) => !file.webkitRelativePath
                  );
                  if (filesWithoutPaths.length > 0) {
                    toast({
                      title: "Folder Structure Missing",
                      description: `${filesWithoutPaths.length} files are missing folder path information. Please reselect the folder to capture directory structure.`,
                      variant: "destructive",
                    });
                    return;
                  }

                  // Additional validation: ensure we have a reasonable file structure
                  if (bulkFiles.length === 0) {
                    toast({
                      title: "No Files Selected",
                      description:
                        "Please select a folder containing files to upload.",
                      variant: "destructive",
                    });
                    return;
                  }

                  try {
                    setBulkUploading(true);
                    const response = await enhancedApi.startBulkUpload({
                      csvFile,
                      files: bulkFiles,
                      organization_id: bulkOrganizationId,
                      project_id: bulkProjectId,
                    });
                    setBulkJobId(response.job_id);
                    setBulkStatus(null);
                    setBulkOrganizationId("");
                    setBulkProjectId("");
                    toast({
                      title: "Bulk Upload Started",
                      description: `Job ID: ${response.job_id}`,
                    });

                    if (pollRef.current) {
                      clearInterval(pollRef.current);
                    }
                    pollRef.current = window.setInterval(async () => {
                      if (!response.job_id) return;
                      try {
                        const status = await enhancedApi.getBulkUploadStatus(
                          response.job_id
                        );
                        setBulkStatus(status);
                        if (
                          status.status === "completed" ||
                          status.status === "completed_with_errors" ||
                          status.status === "failed"
                        ) {
                          clearInterval(pollRef.current!);
                          pollRef.current = null;
                          setBulkUploading(false);
                          toast({
                            title: "Bulk Upload Completed",
                            description: `Success: ${status.successful_uploads}, Failed: ${status.failed_uploads}`,
                            variant:
                              status.failed_uploads > 0
                                ? "destructive"
                                : "default",
                          });
                        }
                      } catch (error: any) {
                        clearInterval(pollRef.current!);
                        pollRef.current = null;
                        setBulkUploading(false);
                        toast({
                          title: "Error",
                          description:
                            error?.message ||
                            "Failed to get bulk upload status",
                          variant: "destructive",
                        });
                      }
                    }, 3000);
                  } catch (error: any) {
                    setBulkUploading(false);
                    toast({
                      title: "Error",
                      description: error?.message || "Bulk upload failed",
                      variant: "destructive",
                    });
                  }
                }}
              >
                Start Bulk Upload
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setBulkFiles([]);
                  setCsvFile(null);
                  setBulkJobId(null);
                  setBulkStatus(null);
                  setBulkUploading(false);
                  setBulkFolderStructurePreview([]);
                  setIsFolderDragOver(false);
                  if (folderInputRef.current) {
                    folderInputRef.current.value = "";
                  }
                  if (pollRef.current) {
                    clearInterval(pollRef.current);
                    pollRef.current = null;
                  }
                }}
              >
                Cancel
              </Button>
            </CardFooter>

            {bulkStatus && (
              <CardContent className="mt-4">
                <p>
                  Status: <strong>{bulkStatus.status}</strong>
                </p>
                <p>
                  Processed: {bulkStatus.processed_files} /{" "}
                  {bulkStatus.total_files}
                </p>
                <p>
                  Successful: {bulkStatus.successful_uploads} | Failed:{" "}
                  {bulkStatus.failed_uploads}
                </p>
                {bulkStatus.error_message && (
                  <p className="text-red-600">
                    Error: {bulkStatus.error_message}
                  </p>
                )}
              </CardContent>
            )}
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default UploadPage;
