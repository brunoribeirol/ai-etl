import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

// eslint-config-next@16.x ships flat-config-native arrays directly (no more
// legacy `{extends: [...]}` shape), so the `@eslint/eslintrc` FlatCompat
// bridge this file used under eslint-config-next@15.5.23 is no longer
// needed — spread the two config arrays in directly, same as
// `create-next-app@latest`'s default eslint.config.mjs.
const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    settings: {
      react: {
        // eslint-config-next's default `settings.react.version` is
        // "detect", which makes eslint-plugin-react@7.37.5 call
        // `context.getFilename()` to locate the nearest `react` package —
        // a method ESLint 10's flat-config Linter context no longer
        // implements (`contextOrFilename.getFilename is not a function`).
        // Pinning the version explicitly instead (kept in sync with the
        // `react` dependency below) skips that code path entirely; this is
        // an eslint-plugin-react/ESLint 10 compat gap, not a project rule
        // change, so drop this override once eslint-plugin-react ships a
        // fix upstream.
        version: "19.2.8", // must match `react` in package.json
      },
    },
  },
  {
    ignores: [".next/**", "out/**", "build/**", "next-env.d.ts"],
  },
];

export default eslintConfig;
