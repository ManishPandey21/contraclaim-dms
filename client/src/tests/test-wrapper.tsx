import React from "react";
import { MemoryRouter } from "react-router-dom";
import { PartiesProvider } from "@/contexts/PartiesContext.new";

interface TestWrapperProps {
  children: React.ReactNode;
  initialEntries?: string[];
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
  options: { route?: string } = {}
) => {
  const { route = "/" } = options;
  return render(<TestWrapper initialEntries={[route]}>{ui}</TestWrapper>);
};
