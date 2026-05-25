import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Utility to reload modules with fresh state after changing globals/env
async function importConfigModule() {
  const mod = await import("../config/api");
  return mod;
}

async function importAxiosApiModule() {
  const mod = await import("../services/api");
  return mod;
}

async function importAuthModule() {
  const mod = await import("../services/auth");
  return mod;
}

async function importHttpModule() {
  vi.doUnmock("../services/http");
  const mod = await import("../services/http");
  return mod;
}

async function importEnhancedApiModule() {
  vi.doUnmock("../services/http");
  // Mock auth helpers used by enhanced-api to avoid token prompts/refresh logic interfering
  vi.doMock("../services/auth", () => ({
    ensureValidToken: vi.fn().mockResolvedValue(undefined),
    refreshToken: vi.fn().mockResolvedValue("new-token"),
    logoutAndRedirect: vi.fn(),
    redirectToLoginAfterSessionExpiry: vi.fn(),
  }));
  const mod = await import("../services/enhanced-api");
  return mod;
}

async function importEmailServiceModule() {
  vi.doUnmock("../services/http");
  const mod = await import("../services/email-service");
  return mod;
}

async function importSessionApiModule() {
  const mod = await import("../services/session-api");
  return mod;
}

declare global {
  interface Window {
    __API_BASE_URL__?: string;
  }
}

const originalEnv = { ...(import.meta.env as any) };

beforeEach(() => {
  // Reset globals
  (globalThis as any).window = window;
  window.localStorage.clear();
  window.__API_BASE_URL__ = undefined;
  (window as any).confirm = vi.fn().mockReturnValue(true);

  // Reset import.meta.env to original baseline without VITE_API_BASE_URL
  (import.meta as any).env = { ...originalEnv };
  delete (import.meta as any).env.VITE_API_BASE_URL;

  vi.resetModules();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

afterEach(() => {
  // Cleanup fetch mock if any
  (globalThis as any).fetch && vi.restoreAllMocks();
  window.__API_BASE_URL__ = undefined;
});

describe("API base URL configuration", () => {
  it("uses runtime override window.__API_BASE_URL__ when set", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";
    const { API_BASE_URL, joinApiUrl, resolveApiBaseUrl } =
      await importConfigModule();

    expect(resolveApiBaseUrl()).toBe("https://runtime.example.com/api");
    expect(API_BASE_URL).toBe("https://runtime.example.com/api");
    expect(joinApiUrl("/users")).toBe("https://runtime.example.com/api/users");
    expect(joinApiUrl("users")).toBe("https://runtime.example.com/api/users");
  });

  it("falls back to '/api' when no env or runtime override is provided", async () => {
    // Ensure both env and runtime are unset
    delete (import.meta as any).env.VITE_API_BASE_URL;
    window.__API_BASE_URL__ = undefined;

    const { API_BASE_URL, joinApiUrl, resolveApiBaseUrl } =
      await importConfigModule();

    expect(resolveApiBaseUrl()).toBe("/api");
    expect(API_BASE_URL).toBe("/api");
    expect(joinApiUrl("/login")).toBe("/api/login");
  });

  it("axios API instance uses API_BASE_URL", async () => {
    window.__API_BASE_URL__ = "https://api.example.com/api";
    const { api } = await importAxiosApiModule();
    expect(api.defaults.baseURL).toBe("https://api.example.com/api");
  });

  it("uses the same-origin dev proxy for loopback API overrides to preserve auth cookies", async () => {
    window.__API_BASE_URL__ = "http://127.0.0.1:8000/api";

    const { API_BASE_URL, joinApiUrl, resolveApiBaseUrl } =
      await importConfigModule();

    expect(resolveApiBaseUrl()).toBe("/api");
    expect(API_BASE_URL).toBe("/api");
    expect(joinApiUrl("/me")).toBe("/api/me");
  });

  it("auth.refreshToken posts to {API_BASE_URL}/refresh via joinApiUrl", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const postSpy = vi.fn().mockResolvedValue({
      data: { access_token: "tok2" },
    });
    vi.doMock("../services/http", () => ({
      publicApi: {
        post: postSpy,
      },
    }));

    // In tests, localStorage is a mock with no backing store.
    // Ensure getItem returns a token for 'accessToken' key.
    (window.localStorage.getItem as any) = vi
      .fn()
      .mockImplementation((k: string) => (k === "accessToken" ? "tok1" : null));
    // Stub setItem as a no-op to avoid unexpected behavior
    (window.localStorage.setItem as any) = vi.fn();

    const { refreshToken } = await importAuthModule();
    await refreshToken();

    expect(postSpy).toHaveBeenCalledTimes(1);
    const url = (postSpy.mock.calls[0] || [])[0];
    expect(url).toBe("/refresh");
  });

  it("auth.ensureValidToken is a no-op for HttpOnly cookie sessions", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const postSpy = vi.fn().mockResolvedValue({
      data: { access_token: "tok2" },
    });
    vi.doMock("../services/http", () => ({
      publicApi: {
        post: postSpy,
      },
    }));

    const { ensureValidToken } = await importAuthModule();
    await ensureValidToken(120);

    expect(window.confirm).not.toHaveBeenCalled();
    expect(postSpy).not.toHaveBeenCalled();
  });

  it("authenticatedFetch strips stale bearer headers and includes cookies", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const baseFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ ok: true }),
    } as any);
    window.fetch = baseFetch as any;
    (globalThis as any).fetch = baseFetch;

    const { authenticatedFetch } = await importHttpModule();
    (globalThis as any).fetch = window.fetch;

    await authenticatedFetch("https://runtime.example.com/api/users", {
      headers: {
        Authorization: "Bearer stale",
        "Content-Type": "application/json",
      },
    });

    expect(baseFetch).toHaveBeenCalledTimes(1);
    const [, init] = baseFetch.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("include");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("axios clients strip stale bearer headers during cookie-authenticated session checks", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const { publicApi } = await importHttpModule();
    const adapter = vi.fn().mockResolvedValue({
      data: { email: "superadmin@example.com", roles: ["superadmin"] },
      status: 200,
      statusText: "OK",
      headers: {},
      config: {},
    });

    publicApi.defaults.headers.common.Authorization = "Bearer stale";
    publicApi.defaults.adapter = adapter;

    await publicApi.get("/me", { withCredentials: true });

    expect(adapter).toHaveBeenCalledTimes(1);
    const config = adapter.mock.calls[0][0];
    const headers = new Headers(config.headers);
    expect(headers.get("Authorization")).toBeNull();
    expect(config.withCredentials).toBe(true);
  });

  it("deduplicates concurrent /me session profile requests", async () => {
    const getSpy = vi.fn().mockResolvedValue({
      data: { email: "superadmin@example.com", roles: ["superadmin"] },
    });
    vi.doMock("../services/http", () => ({
      publicApi: {
        get: getSpy,
        post: vi.fn(),
      },
    }));

    const { getCurrentUserProfile } = await importSessionApiModule();
    const [first, second, third] = await Promise.all([
      getCurrentUserProfile(),
      getCurrentUserProfile(),
      getCurrentUserProfile(),
    ]);

    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(first.email).toBe("superadmin@example.com");
    expect(second).toEqual(first);
    expect(third).toEqual(first);
  });

  it("EnhancedApiService request() prefixes endpoints with API_BASE_URL", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    // Mock fetch to return a valid JSON list of users
    const mockFetch = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue([{ id: "1", username: "u1" }]),
    } as any);

    const { enhancedApi } = await importEnhancedApiModule();
    await enhancedApi.getUsers();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const url = (mockFetch.mock.calls[0] || [])[0];
    expect(url).toBe("https://runtime.example.com/api/users");
  });

  it("EnhancedApiService sends step-up tokens on role mutations", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const mockFetch = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ id: "role-1", name: "Role" }),
    } as any);

    const { enhancedApi } = await importEnhancedApiModule();
    await enhancedApi.updateRole(
      "role-1",
      { permissions: ["dms.document.view"] } as any,
      { stepUpToken: "step-token" }
    );

    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-Step-Up-Token")).toBe("step-token");
  });

  it("plan settings updates send step-up tokens", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const adapter = vi.fn().mockResolvedValue({
      data: { organizations: [], projects: [], plans: [], subscriptions: [], effective: { organizations: {}, projects: {} } },
      status: 200,
      statusText: "OK",
      headers: {},
      config: {},
    });

    const { api } = await importAxiosApiModule();
    api.defaults.adapter = adapter;

    const { updatePlanSettingsScope } = await import("../services/plan-settings-api");
    await updatePlanSettingsScope(
      {
        organization_id: "org-1",
        project_id: null,
        mode: "plan",
        plan_code: "dms",
        status: "active",
      },
      { stepUpToken: "step-token" }
    );

    const config = adapter.mock.calls[0][0];
    const headers = new Headers(config.headers);
    expect(headers.get("X-Step-Up-Token")).toBe("step-token");
  });

  it("redirects expired user creation sessions to login instead of the public landing page", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const redirectToLoginAfterSessionExpiry = vi.fn();
    vi.doMock("../services/auth", () => ({
      ensureValidToken: vi.fn().mockResolvedValue(undefined),
      refreshToken: vi.fn().mockRejectedValue(new Error("expired")),
      redirectToLoginAfterSessionExpiry,
    }));

    vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: false,
      status: 401,
      text: vi.fn().mockResolvedValue('{"detail":"Could not validate credentials"}'),
    } as any);

    const { enhancedApi } = await import("../services/enhanced-api");

    await expect(
      enhancedApi.createUser({
        username: "new.user",
        first_name: "New",
        last_name: "User",
        email: "new.user@example.com",
        password: "TempPass123!",
        roles: ["projectuser"],
        projects: ["project-1"],
        permissions: [],
      })
    ).rejects.toThrow("Session expired. Redirecting to login.");

    expect(redirectToLoginAfterSessionExpiry).toHaveBeenCalledTimes(1);
  });

  it("EmailService uses API_BASE_URL for suggestions and share", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    // First call: suggestions
    const mockFetch = vi.spyOn(globalThis, "fetch" as any).mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue([]),
    } as any);

    const { emailService } = await importEmailServiceModule();

    await emailService.getEmailSuggestions("john", "org1", "proj1");
    expect(mockFetch).toHaveBeenCalled();
    const suggestionsUrl = (mockFetch.mock.calls[0] || [])[0] as string;
    expect(
      suggestionsUrl.startsWith(
        "https://runtime.example.com/api/email/suggestions"
      )
    ).toBe(true);

    // Second call: share document
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({
        message: "ok",
        document_name: "doc.pdf",
        attachments_count: 1,
      }),
    } as any);

    await emailService.shareDocument({
      recipient_email: "a@b.com",
      subject: "s",
      message: "m",
      document_id: "doc1",
      include_linked_documents: false,
    });

    const shareUrl = (mockFetch.mock.calls[1] || [])[0] as string;
    expect(shareUrl).toBe(
      "https://runtime.example.com/api/email/share-document"
    );
  });

  it("Representatives axios helpers use joinApiUrl for absolute pathing", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const axiosModule = await import("axios");
    const axiosDefault: any = (axiosModule as any).default;

    const postSpy = vi
      .spyOn(axiosDefault, "post")
      .mockResolvedValue({ data: {} } as any);
    const getSpy = vi
      .spyOn(axiosDefault, "get")
      .mockResolvedValue({ data: {} } as any);
    const putSpy = vi
      .spyOn(axiosDefault, "put")
      .mockResolvedValue({ data: {} } as any);
    const deleteSpy = vi
      .spyOn(axiosDefault, "delete")
      .mockResolvedValue({ data: {} } as any);

    const {
      createProjectRepresentative,
      getProjectRepresentatives,
      updateProjectRepresentative,
      deleteProjectRepresentative,
      createPartyRepresentative,
      getPartyRepresentatives,
      updatePartyRepresentative,
      deletePartyRepresentative,
    } = await importEnhancedApiModule();

    // Project-level reps
    await createProjectRepresentative("p1", {
      name: "r1",
      level: "project",
    } as any);
    await getProjectRepresentatives("p1");
    await updateProjectRepresentative("p1", "r1", { name: "r1b" } as any);
    await deleteProjectRepresentative("p1", "r1");

    // Party-level reps
    await createPartyRepresentative("party1", {
      name: "x",
      level: "project",
    } as any);
    await getPartyRepresentatives("party1");
    await updatePartyRepresentative("party1", "r2", { name: "y" } as any);
    await deletePartyRepresentative("party1", "r2");

    // Validate axios was called with fully-qualified URLs
    expect(postSpy).toHaveBeenCalledTimes(2);
    expect(getSpy).toHaveBeenCalledTimes(2);
    expect(putSpy).toHaveBeenCalledTimes(2);
    expect(deleteSpy).toHaveBeenCalledTimes(2);

    // First set: project
    expect(postSpy).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/projects\/p1\/representatives$/
      ),
      expect.anything()
    );
    expect(getSpy).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/projects\/p1\/representatives$/
      )
    );
    expect(putSpy).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/projects\/p1\/representatives\/r1$/
      ),
      expect.anything()
    );
    expect(deleteSpy).toHaveBeenNthCalledWith(
      1,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/projects\/p1\/representatives\/r1$/
      )
    );

    // Second set: party
    expect(postSpy).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/parties\/party1\/representatives$/
      ),
      expect.anything()
    );
    expect(getSpy).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/parties\/party1\/representatives$/
      )
    );
    expect(putSpy).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/parties\/party1\/representatives\/r2$/
      ),
      expect.anything()
    );
    expect(deleteSpy).toHaveBeenNthCalledWith(
      2,
      expect.stringMatching(
        /^https:\/\/runtime\.example\.com\/api\/parties\/party1\/representatives\/r2$/
      )
    );
  });
});
