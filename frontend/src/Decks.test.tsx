import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {DeckScreen, Decks, parseLearningSteps} from "./Decks";

const apiMocks = vi.hoisted(() => ({
  applyDeckPreset: vi.fn(),
  archiveDeck: vi.fn(),
  createDeck: vi.fn(),
  fetchArchivedDecks: vi.fn(),
  fetchDeck: vi.fn(),
  fetchDecks: vi.fn(),
  renameDeck: vi.fn(),
  restoreDeck: vi.fn(),
  updateDeckSettings: vi.fn(),
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

describe("deck management", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDecks.mockResolvedValue([{id: 7, name: "Spanish", new_count: 3, learning_count: 2, review_count: 1}]);
    apiMocks.fetchArchivedDecks.mockResolvedValue([]);
    apiMocks.fetchDeck.mockResolvedValue(detail);
    apiMocks.createDeck.mockResolvedValue(detail);
    apiMocks.updateDeckSettings.mockResolvedValue(detail);
    apiMocks.applyDeckPreset.mockResolvedValue(detail);
    apiMocks.archiveDeck.mockResolvedValue({...detail, is_archived: true});
    apiMocks.restoreDeck.mockResolvedValue({...detail, is_archived: false});
  });

  it("creates a deck and shows a duplicate-name error beside the field", async () => {
    render(<Decks createRequest={0} onOpenDeck={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.click(await screen.findByText("Новая колода"));
    fireEvent.change(screen.getByLabelText("Название"), {target: {value: "French"}});
    fireEvent.click(screen.getByText("Создать"));

    await waitFor(() => expect(apiMocks.createDeck).toHaveBeenCalledWith("French", ""));
    await waitFor(() => expect(apiMocks.fetchDecks).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByText("Новая колода"));
    apiMocks.createDeck.mockRejectedValueOnce(new ApiError(409));
    fireEvent.change(screen.getByLabelText("Название"), {target: {value: "French"}});
    fireEvent.click(screen.getByText("Создать"));
    expect(await screen.findByText("Такая колода уже есть")).toBeInTheDocument();
  });

  it("renders settings and PATCHes only changed values", async () => {
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);
    expect(await screen.findByDisplayValue("20")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1 10")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Новых карточек в день"), {target: {value: "30"}});
    fireEvent.click(screen.getByText("Сохранить"));

    await waitFor(() => expect(apiMocks.updateDeckSettings).toHaveBeenCalledWith(7, {new_cards_per_day: 30}));
  });

  it("shows a settings validation error beside the rejected field", async () => {
    apiMocks.updateDeckSettings.mockRejectedValueOnce(new ApiError(422, "Invalid deck setting: desired_retention"));
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByDisplayValue("0.9");
    fireEvent.change(screen.getByLabelText("Желаемое удержание"), {target: {value: "1.5"}});
    fireEvent.click(screen.getByText("Сохранить"));

    expect(await screen.findByText("Недопустимое значение")).toBeInTheDocument();
  });

  it("applies a preset and rereads deck settings", async () => {
    render(<DeckScreen deckId={7} onBack={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByText("Сбалансированный");
    fireEvent.click(screen.getByText("Интенсивный"));

    await waitFor(() => expect(apiMocks.applyDeckPreset).toHaveBeenCalledWith(7, "intense"));
    await waitFor(() => expect(apiMocks.fetchDeck).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Сбалансированный")).toHaveClass("active");
  });

  it("archives a deck and restores it from the archive", async () => {
    const onBack = vi.fn();
    render(<DeckScreen deckId={7} onBack={onBack} onUnauthorized={vi.fn()} />);
    fireEvent.click(await screen.findByText("В архив"));
    await waitFor(() => expect(apiMocks.archiveDeck).toHaveBeenCalledWith(7));
    expect(onBack).toHaveBeenCalledOnce();

    apiMocks.fetchArchivedDecks.mockResolvedValueOnce([{id: 7, name: "Spanish"}]);
    render(<Decks createRequest={0} onOpenDeck={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.click(await screen.findByText("Архив (1)"));
    fireEvent.click(await screen.findByText("Восстановить"));
    await waitFor(() => expect(apiMocks.restoreDeck).toHaveBeenCalledWith(7));
  });
});

describe("learning steps", () => {
  it("converts a space-separated string to numbers and rejects an empty value", () => {
    expect(parseLearningSteps("1 10 15")).toEqual([1, 10, 15]);
    expect(parseLearningSteps("  ")).toBeNull();
    expect(parseLearningSteps("1, 10")).toBeNull();
  });
});
