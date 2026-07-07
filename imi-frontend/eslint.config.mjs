import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      // Tracked tech debt: legacy `any`s (concentrated in the cytoscape graph
      // and older hooks). Kept visible as warnings; new code should type
      // properly. CI gates on errors only.
      "@typescript-eslint/no-explicit-any": "warn",
      // Underscore prefix = intentionally unused (standard TS convention).
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
    },
  },
  {
    // Jest tests/mocks and CJS config files legitimately use require() and
    // module mutation; loose typing is fine there.
    files: [
      "__tests__/**",
      "__mocks__/**",
      "**/__tests__/**",
      "**/*.test.*",
      "jest.config.js",
      "jest.setup.js",
      "tailwind.config.js",
    ],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
      "@next/next/no-assign-module-variable": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
];

export default eslintConfig;
