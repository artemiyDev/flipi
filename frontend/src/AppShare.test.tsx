import {fireEvent, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

const apiMocks = vi.hoisted(() => ({
  fetchDecks: vi.fn(),
  fetchSharedDeck: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

describe("shared deck entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/?share=share-token");
    apiMocks.fetchDecks.mockResolvedValue([]);
    apiMocks.fetchSharedDeck.mockResolvedValue({title: "Spanish basics", description: null, cards_count: 12, author: "Анна", installed: false, own: false});
  });

  afterEach(() => window.history.replaceState({}, "", "/"));

  it("opens a shared deck before the app tabs and clears its query when closed", async () => {
    const replaceState = vi.spyOn(window.history, "replaceState");
    render(<App />);

    expect(await screen.findByRole("heading", {name: "Spanish basics"})).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "К колодам"}));

    expect(replaceState).toHaveBeenCalled();
    expect(window.location.search).toBe("");
  });
});
