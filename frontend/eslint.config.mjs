import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// eslint-config-next@15.5.23 (pinned to match next@15.5.23 — see package.json
// for why) still exports the legacy eslintrc `{extends: [...]}` shape, not a
// flat-config-native array — FlatCompat bridges it into ESLint 9's flat
// config format. `create-next-app@latest`'s default eslint.config.mjs
// (direct array spread of eslint-config-next's exports) assumes a newer
// eslint-config-next that doesn't need this bridge.
const compat = new FlatCompat({
  baseDirectory: __dirname,
});

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
