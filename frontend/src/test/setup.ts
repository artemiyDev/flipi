import "@testing-library/jest-dom/vitest";
import {cleanup} from "@testing-library/react";
import {afterEach} from "vitest";

afterEach(cleanup);

if (!URL.createObjectURL) {
  Object.defineProperty(URL, "createObjectURL", {configurable: true, value: () => ""});
}
if (!URL.revokeObjectURL) {
  Object.defineProperty(URL, "revokeObjectURL", {configurable: true, value: () => undefined});
}
