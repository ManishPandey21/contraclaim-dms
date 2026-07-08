import { api } from "./api";

export interface Organization {
  _id: string;
  name: string;
  panNumber?: string;
  gstNumber?: string;
  address?: string;
  city?: string;
  state?: string;
  pinCode?: string;
  adminName?: string;
  adminEmail?: string;
  adminContact?: string;
  billingEnabled?: boolean;
  projectsCount?: number;
  employeesCount?: number;
  lettersCount?: number;
}

export interface OrganizationListResponse {
  organizations: Organization[];
  total: number;
  page: number;
  limit: number;
  has_next?: boolean;
  has_prev?: boolean;
}

// The organization list feeds dropdowns on many pages but rarely changes.
// Cache it module-wide (with in-flight dedupe) so simultaneous mounts share
// one request instead of each page burning the API rate budget.
const ORG_CACHE_TTL_MS = 5 * 60 * 1000;
let orgListCache: { data: Organization[]; fetchedAt: number } | null = null;
let orgListInFlight: Promise<Organization[]> | null = null;

export function invalidateOrganizationsCache(): void {
  orgListCache = null;
  orgListInFlight = null;
}

export async function listOrganizations(options?: {
  forceRefresh?: boolean;
}): Promise<Organization[]> {
  const force = options?.forceRefresh === true;
  if (!force) {
    if (orgListCache && Date.now() - orgListCache.fetchedAt < ORG_CACHE_TTL_MS) {
      return orgListCache.data;
    }
    if (orgListInFlight) {
      return orgListInFlight;
    }
  }
  const request = fetchOrganizations()
    .then((data) => {
      orgListCache = { data, fetchedAt: Date.now() };
      return data;
    })
    .finally(() => {
      orgListInFlight = null;
    });
  orgListInFlight = request;
  return request;
}

async function fetchOrganizations(): Promise<Organization[]> {
  const { data } = await api.get<Organization[] | OrganizationListResponse>(
    "/organizations"
  );

  if (Array.isArray(data)) {
    return data as Organization[];
  }

  const list = (data as any)?.organizations ?? [];
  if (Array.isArray(list)) {
    return list.map((item: any) => ({
      _id: item._id ?? item.id,
      name: item.name,
      panNumber: item.panNumber,
      gstNumber: item.gstNumber,
      address: item.address,
      city: item.city,
      state: item.state,
      pinCode: item.pinCode,
      adminName: item.adminName,
      adminEmail: item.adminEmail,
      adminContact: item.adminContact,
      billingEnabled: item.billingEnabled,
      projectsCount: item.projectsCount ?? item.project_count,
      employeesCount: item.employeesCount ?? item.user_count,
      lettersCount: item.lettersCount ?? item.document_count,
    })) as Organization[];
  }

  return [];
}

export async function getOrganization(id: string): Promise<Organization> {
  const { data } = await api.get(`/organizations/${id}`);
  const item: any = data;
  const normalized: Organization = {
    _id: item._id ?? item.id,
    name: item.name,
    panNumber: item.panNumber,
    gstNumber: item.gstNumber,
    address: item.address,
    city: item.city,
    state: item.state,
    pinCode: item.pinCode,
    adminName: item.adminName,
    adminEmail: item.adminEmail,
    adminContact: item.adminContact,
    billingEnabled: item.billingEnabled,
    projectsCount: item.projectsCount ?? item.project_count,
    employeesCount: item.employeesCount ?? item.user_count,
    lettersCount: item.lettersCount ?? item.document_count,
  };
  return normalized;
}

export async function createOrganization(
  payload: Partial<Organization>
): Promise<Organization> {
  const { data } = await api.post("/organizations", payload);
  invalidateOrganizationsCache();
  return data as Organization;
}

export async function updateOrganization(
  id: string,
  payload: Partial<Organization>
): Promise<Organization> {
  const { data } = await api.put(`/organizations/${id}`, payload);
  invalidateOrganizationsCache();
  return data as Organization;
}

export async function deleteOrganization(
  id: string
): Promise<{ message: string } | { detail?: string }> {
  const { data } = await api.delete(`/organizations/${id}`);
  invalidateOrganizationsCache();
  return data;
}
