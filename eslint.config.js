
import globals from "globals";

export default [
  {
    languageOptions: {
      ecmaVersion: 12,
      sourceType: "module",
      globals: {
        ...globals.browser,
        "FlutterwaveCheckout": "readonly",
        "introJs": "readonly"
      }
    },
    rules: {
      "no-unused-vars": "warn"
    }
  }
];
