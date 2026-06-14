import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import enhancedApi from "@/services/enhanced-api";
import { API_BASE_URL } from "@/config/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Loader2, UploadCloud } from "lucide-react";

type Organization = { _id: string; name: string };
type Project = { _id: string; name: string; organization_id: string };

const shorten = (name: string, max = 14) =>
  (name || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "")
    .slice(0, max)
    .replace(/-+/g, "-") || "untitled";

const UploadPage: React.FC = () => {
  const navigate = useNavigate();

  // selections
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [organizationId, setOrganizationId] = useState<string>(
    () => window.localStorage.getItem("org_id") || ""
  );
  const [projectId, setProjectId] = useState<string>(
    () => window.localStorage.getItem("proj_id") || ""
  );

  // form
  const [files, setFiles] = useState<File[]>([]);
  const [uploadType, setUploadType] = useState<"incoming" | "outgoing">(
    "incoming"
  );
  const [letterNo, setLetterNo] = useState<string>("");
  const [letterDate, setLetterDate] = useState<string>("");
  const [subject, setSubject] = useState<string>("");

  const [ocrEnabled, setOcrEnabled] = useState<boolean>(true);
  const [compressionEnabled, setCompressionEnabled] = useState<boolean>(false);

  // loading states
  const [loadingOrgs, setLoadingOrgs] = useState<boolean>(false);
  const [loadingProjects, setLoadingProjects] = useState<boolean>(false);
  const [uploading, setUploading] = useState<boolean>(false);

  // derived path structure
  const pathStructure = useMemo(() => {
    const org = organizations.find((o) => o._id === organizationId);
    const proj = projects.find((p) => p._id === projectId);
    if (!org || !proj) return "";
    return `${shorten(org.name)}/${shorten(proj.name)}/`;
  }, [organizations, projects, organizationId, projectId]);

  // Fetch organizations (normalized by enhanced-api)
  useEffect(() => {
    const run = async () => {
      setLoadingOrgs(true);
      try {
        const list = await enhancedApi.getOrganizations();
        setOrganizations(list);
      } catch (e) {
        console.error("Failed to fetch organizations", e);
        setOrganizations([]);
      } finally {
        setLoadingOrgs(false);
      }
    };
    run();
  }, []);

  // Fetch projects (use enhancedApi and filter by org)
  useEffect(() => {
    const run = async () => {
      if (!organizationId) {
        setProjects([]);
        setProjectId("");
        return;
      }
      setLoadingProjects(true);
      try {
        const all = await enhancedApi.getProjects();
        const filtered = (all || []).filter(
          (p: any) => String(p.organization_id) === String(organizationId)
        ) as Project[];
        setProjects(filtered);
        if (!filtered.find((p) => p._id === projectId)) {
          setProjectId("");
        }
      } catch (e) {
        console.error("Failed to fetch projects", e);
        setProjects([]);
        setProjectId("");
      } finally {
        setLoadingProjects(false);
      }
    };
    run();
  }, [organizationId]);

  // persist selections
  useEffect(() => {
    if (organizationId) window.localStorage.setItem("org_id", organizationId);
    if (projectId) window.localStorage.setItem("proj_id", projectId);
  }, [organizationId, projectId]);

  // file selection
  const onSelectFiles = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files ? Array.from(e.target.files) : [];
      if (list.length === 0) return;
      setFiles((prev) => {
        const key = (f: File) =>
          `${f.name}__${f.size}__${(f as any).lastModified ?? ""}`;
        const existing = new Set(prev.map(key));
        const merged: File[] = [...prev];
        for (const f of list) {
          const k = key(f);
          if (!existing.has(k)) {
            merged.push(f);
            existing.add(k);
          }
        }
        // allow re-selecting the same file
        e.target.value = "";
        return merged;
      });
    },
    []
  );

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const canUpload = useMemo(
    () =>
      Boolean(organizationId && projectId && files.length > 0 && !uploading),
    [organizationId, projectId, files.length, uploading]
  );

  const handleUpload = useCallback(async () => {
    if (!organizationId) {
      alert("Select an organization");
      return;
    }
    if (!projectId) {
      alert("Select a project");
      return;
    }
    if (files.length === 0) {
      alert("Select at least one file");
      return;
    }

    try {
      setUploading(true);

      // upload first file, then you can iterate more if needed
      const f = files[0];
      const payload = {
        file: f,
        organization_id: organizationId,
        project_id: projectId,
        uploadType,
        letterNo: letterNo || `AUTO-${Date.now()}`,
        date: letterDate || new Date().toISOString().slice(0, 10),
        subject: subject || f.name,
        status: "draft",
        pathStructure: pathStructure || "",
        ocrEnabled,
        compressionEnabled,
      };

      const doc = await enhancedApi.uploadDocument(payload as any);
      const id = (doc as any)?._id || (doc as any)?.id;
      if (id) {
        navigate(`/documentviewer/${id}`);
      } else {
        alert("Uploaded but response did not include document id");
      }
    } catch (e: any) {
      console.error(e);
      alert(e?.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [
    files,
    organizationId,
    projectId,
    uploadType,
    letterNo,
    letterDate,
    subject,
    ocrEnabled,
    compressionEnabled,
    pathStructure,
    navigate,
  ]);

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UploadCloud className="h-5 w-5" />
            Upload Documents
          </CardTitle>
          <CardDescription>
            Attach a file and select an organization and project
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Organization / Project */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Organization</Label>
              <select
                className="w-full h-10 border rounded px-2"
                value={organizationId}
                onChange={(e) => {
                  setOrganizationId(e.target.value);
                  setProjectId("");
                }}
              >
                <option value="">
                  {loadingOrgs ? "Loading..." : "Select organization"}
                </option>
                {organizations.map((o) => (
                  <option key={o._id} value={o._id}>
                    {o.name}
                  </option>
                ))}
              </select>
              {loadingOrgs && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading organizations...
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label>Project</Label>
              <select
                className="w-full h-10 border rounded px-2"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                disabled={!organizationId || loadingProjects}
              >
                <option value="">
                  {!organizationId
                    ? "Select organization first"
                    : loadingProjects
                    ? "Loading..."
                    : "Select project"}
                </option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {loadingProjects && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading projects...
                </p>
              )}
            </div>
          </div>

          {/* Basic fields */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label>File Type</Label>
              <select
                className="w-full h-10 border rounded px-2"
                value={uploadType}
                onChange={(e) =>
                  setUploadType(e.target.value as "incoming" | "outgoing")
                }
              >
                <option value="incoming">Incoming</option>
                <option value="outgoing">Outgoing</option>
              </select>
            </div>
            <div className="space-y-2">
              <Label>Letter Number</Label>
              <Input
                value={letterNo}
                onChange={(e) => setLetterNo(e.target.value)}
                placeholder="Enter letter number"
              />
            </div>
            <div className="space-y-2">
              <Label>Letter Date</Label>
              <Input
                type="date"
                value={letterDate}
                onChange={(e) => setLetterDate(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Subject</Label>
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Enter subject"
            />
          </div>

          {/* Files */}
          <div className="space-y-2">
            <Label>Files</Label>
            <input
              type="file"
              onChange={onSelectFiles}
              className="block w-full"
            />
            {files.length > 0 && (
              <ul className="text-sm border rounded p-2 divide-y">
                {files.map((f, i) => (
                  <li
                    key={`${f.name}-${i}`}
                    className="flex items-center justify-between py-1"
                  >
                    <span className="truncate">{f.name}</span>
                    <button
                      type="button"
                      onClick={() => removeFile(i)}
                      className="text-xs px-2 py-0.5 border rounded hover:bg-gray-50"
                    >
                      remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* Path preview and toggles */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label className="text-sm">Computed save path</Label>
              <div className="mt-1 text-xs font-mono border rounded px-2 py-2 bg-gray-50">
                {pathStructure ||
                  "Select organization and project to compute path"}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={ocrEnabled}
                  onChange={(e) => setOcrEnabled(e.target.checked)}
                />
                Enable OCR
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={compressionEnabled}
                  onChange={(e) => setCompressionEnabled(e.target.checked)}
                />
                Compress files
              </label>
            </div>
          </div>
        </CardContent>
        <CardFooter>
          <Button
            onClick={handleUpload}
            disabled={!canUpload}
            className="w-full md:w-auto"
          >
            {uploading ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : null}
            {uploading
              ? "Uploading..."
              : `Upload${files.length > 1 ? " (first file)" : ""}`}
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
};

export default UploadPage;
