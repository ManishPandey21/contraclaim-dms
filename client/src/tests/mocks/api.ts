import { vi } from "vitest";

export interface MockApiService {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  put: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
}

export const createMockApi = (): MockApiService => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  delete: vi.fn(),
  patch: vi.fn(),
});

// Create a mock API instance
export const mockApi = createMockApi();

// Mock the API module
vi.mock("@/services/api", () => ({
  api: mockApi,
}));

export const resetApiMocks = () => {
  mockApi.get.mockReset();
  mockApi.post.mockReset();
  mockApi.put.mockReset();
  mockApi.delete.mockReset();
  mockApi.patch.mockReset();
};
