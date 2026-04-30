import React, { useState, ChangeEvent, useEffect } from "react";
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
import { useNavigate } from "react-router-dom"; // Import useNavigate
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

const UploadPage = () => {
  const navigate = useNavigate(); // Initialize useNavigate
  const { toast } = useToast();
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
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [compressionEnabled, setCompressionEnabled] = useState(false);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [pathStructure, setPathStructure] = useState<string>(""); // Add state for pathStructure
  const getToken = () => {
    return localStorage.getItem("accessToken");
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

  // Fetch organizations and projects on component mount
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      try {
        const token = getToken();
        if (!token) {
          throw new Error("User Not authenticated");
        }

        // Updated API endpoints
        const orgResponse = await fetch("/api/organizations", {
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
        });

        // Validate response format
        if (
          !orgResponse.ok ||
          !orgResponse.headers.get("Content-Type")?.includes("application/json")
        ) {
          throw new Error(
            `Failed to fetch organizations: ${orgResponse.status}`
          );
        }

        const orgData = await orgResponse.json();
        setOrganizations(orgData);

        // Updated API endpoints
        const projResponse = await fetch("/api/projects", {
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
        });

        // Validate response format
        if (
          !projResponse.ok ||
          !projResponse.headers
            .get("Content-Type")
            ?.includes("application/json")
        ) {
          throw new Error(`Failed to fetch projects: ${projResponse.status}`);
        }

        const projData = await projResponse.json();
        setProjects(projData);
      } catch (err: any) {
        setError(err.message);
        toast({
          title: "Error",
          description: err.message,
          variant: "destructive",
        });
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, []);

  // Calculate pathStructure whenever organizationId or projectId changes
  useEffect(() => {
    if (
      organizationId &&
      projectId &&
      organizations.length > 0 &&
      projects.length > 0
    ) {
      const org = organizations.find((o) => o._id === organizationId);
      const project = projects.find((p) => p._id === projectId);

      if (org && project) {
        const orgShort = shortenName(org.name);
        const projectShort = shortenName(project.name);
        setPathStructure(`${orgShort}/${projectShort}/`);
      } else {
        setPathStructure("generating-path...");
      }
    } else {
      setPathStructure("generating-path..."); // Reset if IDs are empty
    }
  }, [organizationId, projectId, organizations, projects]);

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
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
      // Get organization and project details
      const org = organizations.find((o) => o._id === organizationId);
      const project = projects.find((p) => p._id === projectId);

      if (!org || !project) {
        throw new Error("Invalid organization or project selection");
     
    // Shorten names utility function
    const shortenName = (name: string, maxLength = 10) => {
      return (
          name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-") // Replace special chars with hyphens
          .replace(/(^-+|-+$)/g, "") // Trim hyphens
            .substring(0, maxLength) // Truncate
          .replace(/-+/g, "-") // Remove consecutive hyphens
          .replace(/(^-|-$)/g, "") || "untitled"
      ); // Fallback

      // Shorten names utility function
      const shortenName = (name: string, maxLength = 10) => {
        return (
          name
            .toLowerCase()      };
    .replace(/[^a-z0-9]+g, "-") /Replace special chars with hyphens
            .replace(/(^-+|-+$)/g, "") // Trim hyphens
            .substring(0, maxLength) // Truncate
            .replace(/-+/g, "-") // Remove consecutive hyphens
            .replace(/(^-|-$)/g, "") || "untitled"
        ); // Fallback
      };

      // 
    // Generate path components
    const orgShort = shortenName(org.name);
    const projectShort = shortenName(project.name);
    const year = uploadDate.getFullYear();
    const month = String(uploadDate.getMonth() + 1).padStart(2, "0");
      const pathStructure = `${orgShort}/${projectShort}/${year}/${month}`;

 cotr= news.forErh(( => {
      files.forEach((file) => {
      });formData.append("file", file.file);
    });tada (these woul typiclly come from form inpus) nd from selected options
    // Add metadata (these would tcally copt
    formData.append("letterNo", letterNo);
    formData.append("organization_id", organizationId);
    formData.append("project_id", projectId);
    formData.append("uploadType", uploadType);oLcaleDate
      rmData.append("date", letterDate || new Date().toLocaleDateString());
      rmData.append("pathStructure", patture);
      rmData.append("subject", sub
      rmData.append("from", fr
      ach(s.forEach((subTag) => formData.append("subTags", subTag));
      rmData.append("status", status);
      rmData.append("compressionEnablesionEnabled.toString());

      nst response = await fetch("/api/documents", {
        // add api in endpoint
      method: "POST",
        //baddoapi in endpoint
        dy: formData,
        aders: {
        Authorization: `Bearer ${token}`,
        },
         Do NOT set Content-Type header when using FormData, the browser will set it
      })

      if (!response.ok) {
      throw new Error(`Failed to upload: ${response.status}`);
      } data = await response.json();
      nsole.log("Upload successful:", data);

       Redirect to DocumentViewerPage with the new document's ID
         navigate("/documents/${datai"g the API returns the document ID as '_id'
      navigate(`/documentviewer/${data._id}`);
Redirect t DocumentVieerPagewith the new docment's ID
      //   navigate(`/doumnt/${data._id}`); // Asumingthe API eturnsth doumentID as '_d'
      navigate(`/documentviewer/${data._id}`);

to    ast({
        tle: "Success",
        scription: "F"F ues successfully!","
    });      setFiles([]);
 seee")Fs([]);
  sLttNo("");
      setOrganizationId("");
 serntsItProj"cId("";
  setPseIOrEabl(flse
    sesetCompressionEnabled(false);      setCompressionEnabled(false);
  } catch (error: any) {
    console.error("E"ror uploading f"errs"
    toast({
      title: "Error",
      description: `Error uploading fis: os.message}`,
      variant: "destructive",
    });
th.pow(k, i)).toFixed(2)) + " " + sizes[i];
  };

  if (isLoading) {
    return <div>Loading...</div>;
  }
  if (error) {
    return <div>Error: {error}</div>;
  }

  return (
    <div className="container mx-auto py-8 animate-fade-in">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Upload Documents</h1>
      </div>

      <Tabs defaultValue="upload" className="mb-8">
        <TabsList className="grid w-full md:w-[400px] grid-cols-3">
          <TabsTrigger value="upload">Upload</TabsTrigger>
          <TabsTrigger value="url">From URL</TabsTrigger>
          <TabsTrigger value="bulk">Bulk Upload</TabsTrigger>
        </TabsList>

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
                <Button className="mr-2" onClick={handleUpload}>
                  Upload
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
                    className="border-2 border-dashed rounded-lg p-8 text-center hover:bg-accent transition-colors cursor-pointer"
                    onClick={() =>
                      document.getElementById("folderInput")?.click()
                    }
                  >
                    <input
                      type="file"
                      id="folderInput"
                      className="hidden"
                      multiple
                      // Using data attributes instead of non-standard HTML attributes
                      data-directory=""
                      data-webkitdirectory=""
                    />
                    <Upload className="h-8 w-8 text-muted-foreground mb-3 mx-auto" />
                    <p>Upload an entire folder</p>
                    <Button size="sm" className="mt-2">
                      Select Folder
                    </Button>
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
                      <Button size="sm" variant="outline">
                        Download Template
                      </Button>
                      <Button size="sm">Upload CSV</Button>
                    </div>
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
                      <Checkbox id="autoProcess" />
                      <Label
                        htmlFor="autoProcess"
                        className="text-sm font-normal"
                      >
                        Auto-process documents
                      </Label>
                    </div>
                    <div className="flex items-center space-x-2">
                      <Checkbox id="extractMeta" />
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
              <Button className="mr-2">Start Bulk Upload</Button>
              <Button variant="outline">Cancel</Button>
            </CardFooter>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default UploadPage;
