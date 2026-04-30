import React from "react";
import { MemoryRouter } from "react-router-dom";
import { render, RenderOptions } from "@testing-library/react";
import { PartiesProvider } from "@/contexts/PartiesContext.new";

interface TestWrapperProps {
  children: React.ReactNode;
  initialEntries?: string[];
}

interface CustomRenderOptions extends Omit<RenderOptions, "wrapper"> {
  route?: string;
}

export const TestWrapper: React.FC<TestWrapperProps> = ({
  children,
  initialEntries = ["/"],
}) => {
  return (
    <MemoryRouter initialEntries={initialEntries}>
      <PartiesProvider>{children}</PartiesProvider>
    </MemoryRouter>
  );
};

export const renderWithProviders = (
  ui: React.ReactElement,
  options: CustomRenderOptions = {}
) => {
  const { route = "/", ...renderOptions } = options;

  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <TestWrapper initialEntries={[route]}>{children}</TestWrapper>
  );

  return {
    ...render(ui, { wrapper: Wrapper, ...renderOptions }),
  };
};

// Re-export everything
export * from "@testing-library/react";
export { renderWithProviders as render };
