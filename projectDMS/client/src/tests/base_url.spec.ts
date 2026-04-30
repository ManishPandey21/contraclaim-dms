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

async function importEnhancedApiModule() {
  // Mock auth helpers used by enhanced-api to avoid token prompts/refresh logic interfering
  vi.doMock("../services/auth", () => ({
    ensureValidToken: vi.fn().mockResolvedValue(undefined),
    refreshToken: vi.fn().mockResolvedValue("new-token"),
    logoutAndRedirect: vi.fn(),
  }));
  const mod = await import("../services/enhanced-api");
  return mod;
}

async function importEmailServiceModule() {
  const mod = await import("../services/email-service");
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
    window.__API_BASE_URL__ = "http://localhost:8000/api";
    const { api } = await importAxiosApiModule();
    expect(api.defaults.baseURL).toBe("http://localhost:8000/api");
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

  it("auth.ensureValidToken refreshes silently without prompting", async () => {
    window.__API_BASE_URL__ = "https://runtime.example.com/api";

    const exp = Math.floor(Date.now() / 1000) + 30;
    const payload = btoa(JSON.stringify({ exp }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/g, "");
    const token = `header.${payload}.signature`;

    window.localStorage.setItem("accessToken", token);

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
    expect(postSpy).toHaveBeenCalledWith(
      "/refresh",
      undefined,
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: `Bearer ${token}`,
        }),
      })
    );
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
