import React, { ReactElement } from "react";
import type { RenderOptions } from "@testing-library/react";
import type { Party, Representative, Concern } from "@/types/api";

// Types for our custom render
interface CustomRenderOptions extends Omit<RenderOptions, "wrapper"> {
  initialParties?: Party[];
  initialConcerns?: Concern[];
}

// Types for our test data
interface TestData {
  generateParty: (overrides?: Partial<Party>) => Party;
  generateRepresentative: (
    overrides?: Partial<Representative>
  ) => Representative;
  generateConcern: (overrides?: Partial<Concern>) => Concern;
  parties: Party[];
  representatives: Representative[];
  concerns: Concern[];
}

// Types for our test utilities
interface TestUtils {
  fillForm: (formData: Record<string, string>) => Promise<void>;
  waitForLoadingToFinish: () => Promise<void>;
  selectOption: (labelText: string, optionText: string) => Promise<void>;
  mockApiResponse: <T>(data: T) => Response;
  mockApiError: (status: number, message: string) => Promise<never>;
}

// Test data implementation
export const mockData: TestData = {
  generateParty: (overrides = {}) => ({
    id: "test-party-id",
    name: "Test Organization",
    type: "Organization",
    contactEmail: "test@example.com",
    contactPhone: "1234567890",
    createdAt: new Date().toISOString(),
    representatives: [],
    projects: [],
    ...overrides,
  }),

  generateRepresentative: (overrides = {}) => ({
    id: "test-rep-id",
    partyId: "test-party-id",
    name: "John Doe",
    email: "john@example.com",
    contactNumber: "1234567890",
    designation: "Manager",
    isPrimary: true,
    ...overrides,
  }),

  generateConcern: (overrides = {}) => ({
    id: "test-concern-id",
    name: "Test Concern",
    description: "Test Description",
    partyId: "test-party-id",
    emails: ["test@example.com"],
    createdAt: new Date().toISOString(),
    ...overrides,
  }),

  parties: [
    {
      id: "test-party-1",
      name: "Test Organization",
      type: "Organization",
      contactEmail: "test@example.com",
      contactPhone: "1234567890",
      createdAt: new Date().toISOString(),
      representatives: [],
      projects: [],
    },
    {
      id: "test-party-2",
      name: "Individual Person",
      type: "Individual",
      contactEmail: "person@example.com",
      contactPhone: "0987654321",
      createdAt: new Date().toISOString(),
      representatives: [],
      projects: [],
    },
  ],

  representatives: [
    {
      id: "test-rep-1",
      partyId: "test-party-1",
      name: "John Doe",
      email: "john@example.com",
      contactNumber: "1234567890",
      designation: "Manager",
      isPrimary: true,
    },
    {
      id: "test-rep-2",
      partyId: "test-party-1",
      name: "Jane Smith",
      email: "jane@example.com",
      contactNumber: "0987654321",
      designation: "Assistant",
      isPrimary: false,
    },
  ],

  concerns: [
    {
      id: "test-concern-1",
      name: "Test Concern",
      description: "Test Description",
      partyId: "test-party-1",
      emails: ["test@example.com"],
      createdAt: new Date().toISOString(),
    },
  ],
};

// Test utilities implementation
export const testUtils: TestUtils = {
  async fillForm(formData: Record<string, string>) {
    const { screen, userEvent } = await import("@testing-library/react");
    for (const [label, value] of Object.entries(formData)) {
      const element = screen.getByLabelText(new RegExp(label, "i"));
      await userEvent.type(element, value);
    }
  },

  async waitForLoadingToFinish() {
    const { screen, waitForElementToBeRemoved } = await import(
      "@testing-library/react"
    );
    try {
      await waitForElementToBeRemoved(() => screen.queryByText(/loading/i));
    } catch (error) {
      // If element is already not present, that's fine
    }
  },

  async selectOption(labelText: string, optionText: string) {
    const { screen, userEvent } = await import("@testing-library/react");
    const select = screen.getByLabelText(new RegExp(labelText, "i"));
    await userEvent.click(select);
    const option = screen.getByText(new RegExp(optionText, "i"));
    await userEvent.click(option);
  },

  mockApiResponse<T>(data: T): Response {
    return {
      ok: true,
      json: () => Promise.resolve(data),
      status: 200,
      statusText: "OK",
      headers: new Headers(),
      body: null,
      bodyUsed: false,
      arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
      blob: () => Promise.resolve(new Blob()),
      formData: () => Promise.resolve(new FormData()),
      text: () => Promise.resolve(""),
      clone: function () {
        return this as Response;
      },
      redirected: false,
      type: "basic",
      url: "",
    };
  },

  mockApiError(status: number, message: string): Promise<never> {
    return Promise.reject({
      ok: false,
      status,
      statusText: "Error",
      message,
    });
  },
};

// Custom render function
export async function render(ui: ReactElement, options?: CustomRenderOptions) {
  const { render } = await import("@testing-library/react");
  const { PartiesProvider } = await import("@/contexts/PartiesContext.new");

  const AllProviders = ({ children }: { children: React.ReactNode }) => {
    return <PartiesProvider>{children}</PartiesProvider>;
  };

  return render(ui, { wrapper: AllProviders, ...options });
}

// Re-export testing library utilities
export async function getTestingLibrary() {
  const testingLibrary = await import("@testing-library/react");
  const userEventLib = await import("@testing-library/user-event");
  return { ...testingLibrary, userEvent: userEventLib.default };
}
