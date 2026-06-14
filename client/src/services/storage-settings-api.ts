import { api } from "./api";

export type StorageProviderId = "local" | "s3" | "azure" | "gcs" | "custom";

export interface StorageProviderConfig {
  id: StorageProviderId;
  enabled: boolean;
  primary: boolean;
  bucket?: string;
  prefix?: string;
  region?: string;
  endpoint?: string;
  extra?: Record<string, any>;
}

export interface StorageBasePaths {
  incoming: string;
  outgoing: string;
  contracts: string;
}

export interface OrganizationStorageSettings {
  _id?: string;
  org_id: string;
  org_short_name?: string;
  providers: StorageProviderConfig[];
  base_paths: StorageBasePaths;
  updatedAt?: string;
}

export interface ProjectStorageSettings {
  _id?: string;
  project_id: string;
  org_id: string;
  inherit_from_org: boolean;
  project_short_name?: string;
  providers?: StorageProviderConfig[];
  base_paths?: StorageBasePaths;
  updatedAt?: string;
}

export interface ResolvedStorageSettings {
  org_id: string;
  project_id?: string;
  org_short_name?: string;
  project_short_name?: string;
  providers: StorageProviderConfig[];
  base_paths: StorageBasePaths;
}

const defaultPaths: StorageBasePaths = {
  incoming: "/ORG/PROJ/incoming",
  outgoing: "/ORG/PROJ/outgoing",
  contracts: "/ORG/PROJ/contracts",
};

const normalizePaths = (paths?: any): StorageBasePaths => ({
  incoming: paths?.incoming || defaultPaths.incoming,
  outgoing: paths?.outgoing || defaultPaths.outgoing,
  contracts: paths?.contracts || defaultPaths.contracts,
});

const normalizeProvider = (p: any): StorageProviderConfig => ({
  id: p?.id,
  enabled: Boolean(p?.enabled ?? true),
  primary: Boolean(p?.primary ?? false),
  bucket: p?.bucket || undefined,
  prefix: p?.prefix || undefined,
  region: p?.region || undefined,
  endpoint: p?.endpoint || undefined,
  extra: p?.extra || {},
});

const normalizeOrgSettings = (
  data: any,
  orgId: string
): OrganizationStorageSettings => ({
  _id: data?._id ?? data?.id,
  org_id: data?.org_id ?? orgId,
  org_short_name: data?.org_short_name ?? data?.orgShortName ?? "",
  providers: Array.isArray(data?.providers)
    ? data.providers.map(normalizeProvider)
    : [],
  base_paths: normalizePaths(data?.base_paths ?? data?.basePaths),
  updatedAt: data?.updatedAt,
});

const normalizeProjectSettings = (
  data: any,
  projectId: string,
  orgId: string
): ProjectStorageSettings => ({
  _id: data?._id ?? data?.id,
  project_id: data?.project_id ?? projectId,
  org_id: data?.org_id ?? orgId,
  inherit_from_org: Boolean(
    data?.inherit_from_org ?? data?.inheritFromOrg ?? true
  ),
  project_short_name:
    data?.project_short_name ?? data?.projectShortName ?? "",
  providers: Array.isArray(data?.providers)
    ? data.providers.map(normalizeProvider)
    : undefined,
  base_paths: data?.base_paths
    ? normalizePaths(data.base_paths)
    : data?.basePaths
    ? normalizePaths(data.basePaths)
    : undefined,
  updatedAt: data?.updatedAt,
});

const normalizeResolved = (data: any): ResolvedStorageSettings => ({
  org_id: data?.org_id ?? "",
  project_id: data?.project_id,
  org_short_name: data?.org_short_name ?? data?.orgShortName,
  project_short_name: data?.project_short_name ?? data?.projectShortName,
  providers: Array.isArray(data?.providers)
    ? data.providers.map(normalizeProvider)
    : [],
  base_paths: normalizePaths(data?.base_paths ?? data?.basePaths),
});

export async function getOrgStorageSettings(
  orgId: string
): Promise<OrganizationStorageSettings> {
  try {
    const { data } = await api.get(`/settings/storage/org/${orgId}`);
    return normalizeOrgSettings(data, orgId);
  } catch (error: any) {
    // Fallback path for older backends
    const { data } = await api.get(`/storage-settings/org/${orgId}`);
    return normalizeOrgSettings(data, orgId);
  }
}

export async function updateOrgStorageSettings(
  orgId: string,
  payload: Partial<OrganizationStorageSettings>
): Promise<OrganizationStorageSettings> {
  const body = {
    ...payload,
    org_id: orgId,
  };
  try {
    const { data } = await api.put(`/settings/storage/org/${orgId}`, body);
    return normalizeOrgSettings(data, orgId);
  } catch (error: any) {
    const { data } = await api.put(`/storage-settings/org/${orgId}`, body);
    return normalizeOrgSettings(data, orgId);
  }
}

export async function getProjectStorageSettings(
  projectId: string,
  orgId?: string
): Promise<ProjectStorageSettings> {
  try {
    const { data } = await api.get(`/settings/storage/project/${projectId}`, {
      params: orgId ? { org_id: orgId } : undefined,
    });
    return normalizeProjectSettings(data, projectId, orgId || data?.org_id || "");
  } catch (error: any) {
    const { data } = await api.get(`/storage-settings/project/${projectId}`, {
      params: orgId ? { org_id: orgId } : undefined,
    });
    return normalizeProjectSettings(data, projectId, orgId || data?.org_id || "");
  }
}

export async function updateProjectStorageSettings(
  projectId: string,
  orgId: string,
  payload: Partial<ProjectStorageSettings>
): Promise<ProjectStorageSettings> {
  const body = {
    ...payload,
    project_id: projectId,
    org_id: orgId,
  };
  try {
    const { data } = await api.put(`/settings/storage/project/${projectId}`, body);
    return normalizeProjectSettings(data, projectId, orgId);
  } catch (error: any) {
    const { data } = await api.put(`/storage-settings/project/${projectId}`, body);
    return normalizeProjectSettings(data, projectId, orgId);
  }
}

export async function resolveStorageSettings(params: {
  org_id: string;
  project_id?: string | null;
}): Promise<ResolvedStorageSettings> {
  const query = {
    org_id: params.org_id,
    project_id: params.project_id ?? undefined,
  };
  try {
    const { data } = await api.get("/settings/storage/resolve", { params: query });
    return normalizeResolved(data);
  } catch (error: any) {
    const { data } = await api.get("/storage-settings/resolve", { params: query });
    return normalizeResolved(data);
  }
}
