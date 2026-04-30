import React, { ReactElement } from "react";
import { render, RenderOptions } from "@testing-library/react";
import { PartiesProvider } from "@/contexts/PartiesContext.new";
import { Party, Representative, Concern } from "@/types/api";

// Create a custom render function that includes providers
const customRender = (
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">
) => {
  const AllProviders = ({ children }: { children: React.ReactNode }) => {
    return <PartiesProvider>{children}</PartiesProvider>;
  };

  return render(ui, { wrapper: AllProviders, ...options });
};

// Re-export everything
export * from "@testing-library/react";

// Override render method
export { customRender as render };

// Test data generators
export const generateMockParty = (overrides = {}): Party => ({
  id: "test-party-id",
  name: "Test Organization",
  type: "Organization",
  contactEmail: "test@example.com",
  contactPhone: "1234567890",
  createdAt: new Date().toISOString(),
  representatives: [],
  projects: [],
  ...overrides,
});

export const generateMockRepresentative = (overrides = {}): Representative => ({
  id: "test-rep-id",
  partyId: "test-party-id",
  name: "John Doe",
  email: "john@example.com",
  contactNumber: "1234567890",
  designation: "Manager",
  isPrimary: true,
  ...overrides,
});

export const generateMockConcern = (overrides = {}): Concern => ({
  id: "test-concern-id",
  name: "Test Concern",
  description: "Test Description",
  partyId: "test-party-id",
  emails: ["test@example.com"],
  createdAt: new Date().toISOString(),
  ...overrides,
});

// Mock API response helpers
export const mockApiResponse = <T extends unknown>(data: T) => {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(data),
    status: 200,
    statusText: "OK",
  });
};

export const mockApiError = (status: number, message: string) => {
  return Promise.reject({
    ok: false,
    status,
    statusText: "Error",
    message,
  });
};

// Form helpers
export const fillPartyForm = async (user: any, values: Partial<Party>) => {
  if (values.name) {
    await user.type(screen.getByLabelText(/name/i), values.name);
  }
  if (values.type) {
    await user.click(screen.getByLabelText(values.type));
  }
  if (values.contactEmail) {
    await user.type(screen.getByLabelText(/email/i), values.contactEmail);
  }
  if (values.contactPhone) {
    await user.type(screen.getByLabelText(/phone/i), values.contactPhone);
  }
};

export const fillRepresentativeForm = async (
  user: any,
  values: Partial<Representative>
) => {
  if (values.name) {
    await user.type(screen.getByLabelText(/name/i), values.name);
  }
  if (values.email) {
    await user.type(screen.getByLabelText(/email/i), values.email);
  }
  if (values.contactNumber) {
    await user.type(screen.getByLabelText(/phone/i), values.contactNumber);
  }
  if (values.designation) {
    await user.type(
      screen.getByLabelText(/position|designation/i),
      values.designation
    );
  }
  if (values.isPrimary !== undefined) {
    const checkbox = screen.getByLabelText(/primary/i);
    if (values.isPrimary && !checkbox.checked) {
      await user.click(checkbox);
    } else if (!values.isPrimary && checkbox.checked) {
      await user.click(checkbox);
    }
  }
};

// Wait helpers
export const waitForLoadingToFinish = () =>
  waitForElementToBeRemoved(() => screen.queryByText(/loading/i));

export const waitForApiCall = () =>
  new Promise((resolve) => setTimeout(resolve, 0));

// Mock data sets
export const mockParties: Party[] = [
  generateMockParty(),
  generateMockParty({
    id: "test-party-2",
    name: "Another Organization",
    type: "Organization",
  }),
  generateMockParty({
    id: "test-party-3",
    name: "Individual Person",
    type: "Individual",
  }),
];

export const mockRepresentatives: Representative[] = [
  generateMockRepresentative(),
  generateMockRepresentative({
    id: "test-rep-2",
    name: "Jane Smith",
    email: "jane@example.com",
    isPrimary: false,
  }),
];

export const mockConcerns: Concern[] = [
  generateMockConcern(),
  generateMockConcern({
    id: "test-concern-2",
    name: "Another Concern",
  }),
];
