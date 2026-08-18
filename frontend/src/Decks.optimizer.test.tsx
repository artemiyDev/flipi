import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {DeckScreen} from "./Decks";

const apiMocks = vi.hoisted(() => ({
  fetchDeck: vi.fn(),
  optimizeDeck: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

const detail = {
  id: 7,
  name: "Spanish",
  description: null,
  is_archived: false,
  fsrs_optimized_at: null,
  review_count: 400,
  counts: {new: 3, learning: 2, review: 1},
  settings: {
    new_cards_per_day: 20,
    reviews_per_day: 200,
    desired_retention: 0.9,
    learning_steps_minutes: [1, 10],
    relearning_steps_minutes: [10],
    maximum_interval_days: 36500,
    bury_siblings: false,
    enable_fuzzing: true,
    option_preset: "balanced",
  },
};

describe("FSRS optimizer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDeck.mockResolvedValue(detail);
  });

  it("disables optimization until the deck has enough history", async () => {
    apiMocks.fetchDeck.mockResolvedValueOnce({...detail, review_count: 399});
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);

    expect(await screen.findByText("История: 399 повторений")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Оптимизировать"})).toBeDisabled();
    expect(screen.getByText("нужно ≥400 повторений")).toBeInTheDocument();
  });

  it("optimizes the deck and refreshes the scheduler state", async () => {
    apiMocks.fetchDeck.mockResolvedValueOnce(detail).mockResolvedValueOnce({...detail, fsrs_optimized_at: "2026-08-17T12:00:00+00:00"});
    apiMocks.optimizeDeck.mockResolvedValue({review_count: 400, optimized_at: "2026-08-17T12:00:00+00:00"});
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", {name: "Оптимизировать"}));

    await waitFor(() => expect(apiMocks.optimizeDeck).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.getByText("Персонализирован · 17.08.2026")).toBeInTheDocument());
  });

  it("shows an availability hint when the server lacks the optimizer", async () => {
    apiMocks.optimizeDeck.mockRejectedValueOnce(new ApiError(503));
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", {name: "Оптимизировать"}));

    expect(await screen.findByRole("status")).toHaveTextContent("Недоступно на сервере");
  });
});
