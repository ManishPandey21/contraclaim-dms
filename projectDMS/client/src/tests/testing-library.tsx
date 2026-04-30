import React, { ReactElement } from "react";
import {
  render,
  RenderOptions,
  screen,
  waitForElementToBeRemoved,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PartiesProvider } from "@/contexts/PartiesContext.new";
import { Party, Representative, Concern } from "@/types/api";

interface CustomRenderOptions extends Omit<RenderOptions, "wrapper"> {
  initialParties?: Party[];
  initialConcerns?: Concern[];
}

// Create a custom render function that includes providers
const customRender = (ui: ReactElement, options?: CustomRenderOptions) => {
  const AllProviders = ({ children }: { children: React.ReactNode }) => {
    return <PartiesProvider>{children}</PartiesProvider>;
  };

  return render(ui, { wrapper: AllProviders, ...options });
};

// Re-export everything
export * from "@testing-library/react";
export { userEvent };

// Override render method
export { customRender as render };

// Test data generators
export const mockData = {
  generateParty: (overrides = {}): Party => ({
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

  generateRepresentative: (overrides = {}): Representative => ({
    id: "test-rep-id",
    partyId: "test-party-id",
    name: "John Doe",
    email: "john@example.com",
    contactNumber: "1234567890",
    designation: "Manager",
    isPrimary: true,
    ...overrides,
  }),

  generateConcern: (overrides = {}): Concern => ({
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
  ] as Party[],

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
  ] as Representative[],

  concerns: [
    {
      id: "test-concern-1",
      name: "Test Concern",
      description: "Test Description",
      partyId: "test-party-1",
      emails: ["test@example.com"],
      createdAt: new Date().toISOString(),
    },
  ] as Concern[],
};

// Helper functions
export const testUtils = {
  async fillForm(formData: Record<string, string>) {
    for (const [label, value] of Object.entries(formData)) {
      const element = screen.getByLabelText(new RegExp(label, "i"));
      await userEvent.type(element, value);
    }
  },

  async waitForLoadingToFinish() {
    try {
      await waitForElementToBeRemoved(() => screen.queryByText(/loading/i));
    } catch (error) {
      // If element is already not present, that's fine
    }
  },

  async selectOption(labelText: string, optionText: string) {
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
        return this;
      },
      redirect: () => new Response(),
      type: "basic",
    };
  },

  mockApiError(status: number, message: string) {
    return Promise.reject({
      ok: false,
      status,
      statusText: "Error",
      message,
    });
  },
};

export { mockData, testUtils };
