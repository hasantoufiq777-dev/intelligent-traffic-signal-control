import { defineConfig } from 'vitest/config';

// Tests spawn the Python mock simulation, so they need more than Vitest's 5 s default.
export default defineConfig({
  test: {
    testTimeout: 60_000,
    hookTimeout: 30_000,
  },
});
