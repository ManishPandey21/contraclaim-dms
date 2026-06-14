#!/bin/bash

# Install testing libraries and types
npm install --save-dev \
  @testing-library/react \
  @testing-library/user-event \
  @testing-library/jest-dom \
  vitest \
  jsdom \
  @types/testing-library__react \
  @types/testing-library__user-event \
  @types/testing-library__jest-dom

# Update package.json with test scripts
npm pkg set scripts.test="vitest"
npm pkg set scripts.test:watch="vitest watch"
npm pkg set scripts.test:coverage="vitest run --coverage"
