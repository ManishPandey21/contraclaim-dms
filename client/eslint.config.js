import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "archive",
      "**/archive/**",
      "DocumentsPage.tsx",
      "src/pages/*_check.tsx",
      "src/pages/*_tranculated.tsx",
      "src/pages/*1.tsx",
      "src/pages/OrganizationsPage1.tsx",
      "src/tests/setup.final*.ts",
      "src/tests/setup.new.ts",
    ],
  },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/no-unused-vars": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-namespace": "off",
      "@typescript-eslint/no-require-imports": "off",
      "@typescript-eslint/no-unnecessary-type-constraint": "off",
      "@typescript-eslint/no-unused-expressions": "off",
      "@typescript-eslint/no-empty-object-type": "off",
      "no-empty": "off",
      "no-useless-escape": "off",
      "prefer-const": "off",
      // Enforce case-sensitive imports to prevent Windows casing issues
      "no-case-declarations": "error",
    },
  },
  {
    files: [
      "src/components/ui/**/*.{ts,tsx}",
      "src/components/error-boundary/**/*.{ts,tsx}",
      "src/contexts/**/*.{ts,tsx}",
      "src/tests/**/*.{ts,tsx}",
    ],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  }
);
