import "@testing-library/jest-dom";
import { expect, afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";
import React from "react";

// Mock react-quill with a simple textarea to keep tests light and deterministic
vi.mock("react-quill", () => {
  return {
    default: ({ value, onChange, placeholder }: any) =>
      React.createElement("textarea", {
        value: value ?? "",
        placeholder: placeholder ?? "",
        onChange: (e: any) => onChange?.(e.target.value),
        "data-testid": "react-quill-mock",
      }),
  };
});
// Stub Quill CSS import during tests
vi.mock("react-quill/dist/quill.snow.css", () => ({}));

// Extend Vitest's expect method with methods from react-testing-library
expect.extend(matchers as any);

// Cleanup after each test case (e.g. clearing jsdom)
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // deprecated
    removeListener: vi.fn(), // deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Mock IntersectionObserver
const mockIntersectionObserver = vi.fn();
mockIntersectionObserver.mockReturnValue({
  observe: () => null,
  unobserve: () => null,
  disconnect: () => null,
});
window.IntersectionObserver = mockIntersectionObserver;

// Mock ResizeObserver
const mockResizeObserver = vi.fn();
mockResizeObserver.mockReturnValue({
  observe: () => null,
  unobserve: () => null,
  disconnect: () => null,
});
window.ResizeObserver = mockResizeObserver;

// Mock fetch
global.fetch = vi.fn();

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, "localStorage", { value: localStorageMock });

// Mock sessionStorage
const sessionStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
};
Object.defineProperty(window, "sessionStorage", { value: sessionStorageMock });

// Add custom matchers if needed
expect.extend({
  toHaveBeenCalledWithMatch(received: any, ...expected: any[]) {
    const pass = received.mock.calls.some((call: any[]) =>
      expected.every((arg, i) => {
        if (typeof arg === "object") {
          return expect.objectContaining(arg).asymmetricMatch(call[i]);
        }
        return arg === call[i];
      })
    );

    return {
      pass,
      message: () =>
        `expected ${received.mock.calls} to contain a call matching ${expected}`,
    };
  },
});

// Declare global types for custom matchers
declare global {
  namespace Vi {
    interface JestAssertion<T = any> {
      toHaveBeenCalledWithMatch(...args: any[]): T;
    }
  }
}
