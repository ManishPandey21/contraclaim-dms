/**
 * Dashboard API service - fetches aggregated document statistics
 * from the backend /api/dashboard/stats endpoint.
 */

import { API_BASE_URL } from "../config/api";
import {
  ensureValidToken,
  refreshToken,
  redirectToLoginAfterSessionExpiry,
} from "./auth";
import { authenticatedFetch } from "./http";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StatusCount {
  status: string;
  count: number;
  color: string;
}

export interface RecentActivity {
  id: string;
  letter: string;
  previousStatus: string;
  newStatus: string;
  timestamp: string;
  user: string;
}

export interface RecentDocument {
  id: string;
  documentNumber: string;
  filename: string;
  subject: string;
  direction: string;
  letterDate: string;
  uploadedAt: string;
}

export interface ActionableLetter {
  id: string;
  title: string;
  organization: string;
  project: string;
  status: string;
  updatedAt: string;
  updatedBy: string;
  assignedTo: string;
}

export interface OrganizationStat {
  id: string;
  name: string;
  totalLetters: number;
  replyOverdue: number;
  underReview: number;
}

export interface ProjectStat {
  id: string;
  name: string;
  organization: string;
  organizationId: string;
  totalLetters: number;
  inputRequired: number;
  closed: number;
}

export interface DashboardStats {
  totalLetters: number;
  totalDocuments?: number;
  inputRequiredCount: number;
  incomingCount?: number;
  replyOverdueCount: number;
  outgoingCount?: number;
  underReviewCount: number;
  draftInProcessCount?: number;
  letterStatuses: StatusCount[];
  documentStatuses?: StatusCount[];
  recentActivity: RecentActivity[];
  recentDocuments?: RecentDocument[];
  actionableLetters: ActionableLetter[];
  organizations: OrganizationStat[];
  projects: ProjectStat[];
}

// ---------------------------------------------------------------------------
// API call
// ---------------------------------------------------------------------------

export async function getDashboardStats(params?: {
  search?: string;
  status?: string;
}): Promise<DashboardStats> {
  await ensureValidToken(120);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Build query string
  const searchParams = new URLSearchParams();
  if (params?.search) searchParams.set("search", params.search);
  if (params?.status && params.status !== "all")
    searchParams.set("status", params.status);
  const qs = searchParams.toString();

  const url = `${API_BASE_URL}/dashboard/stats${qs ? `?${qs}` : ""}`;

  const doFetch = async () =>
    authenticatedFetch(url, { method: "GET", headers });

  let response = await doFetch();

  // If unauthorized, attempt a single refresh then retry
  if (response.status === 401) {
    try {
      await refreshToken();
      response = await doFetch();
    } catch {
      redirectToLoginAfterSessionExpiry();
      throw new Error("Session expired. Redirecting to login.");
    }
  }

  if (response.status === 403) {
    let detail = "Permission denied";
    try {
      const parsed = await response.json();
      detail = parsed?.detail || parsed?.message || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(errorText || `HTTP error! status: ${response.status}`);
  }

  return response.json();
}
