import "@testing-library/jest-dom/vitest";

if (!URL.createObjectURL) {
  Object.defineProperty(URL, "createObjectURL", {configurable: true, value: () => ""});
}
if (!URL.revokeObjectURL) {
  Object.defineProperty(URL, "revokeObjectURL", {configurable: true, value: () => undefined});
}
