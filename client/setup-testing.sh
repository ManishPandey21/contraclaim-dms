#!/bin/bash

# Run the package.json update script
node update-package.js

# Install dependencies
npm install

# Create test directories if they don't exist
mkdir -p src/tests

# Make test files executable
chmod +x vitest.config.ts
chmod +x src/tests/setup.ts
chmod +x src/tests/test-helpers.tsx

# Update TypeScript config to include test files
echo '{
  "extends": "./tsconfig.json",
  "include": ["src/**/*.ts", "src/**/*.tsx", "src/**/*.test.ts", "src/**/*.test.tsx"],
  "exclude": ["node_modules"]
}' > tsconfig.test.json

# Create a .env.test file for test environment variables
echo 'VITE_API_URL=http://localhost:8000
VITE_MOCK_API=true' > .env.test

echo "Testing setup completed successfully!"
echo "You can now run tests using: npm test"
