import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {App} from "./App";
import {ImportScreen} from "./Import";

const apiMocks = vi.hoisted(() => ({
  fetchArchivedDecks: vi.fn(),
  fetchDeck: vi.fn(),
  fetchDecks: vi.fn(),
  importFile: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

const decks = [{id: 7, name: "Spanish", new_count: 0, learning_count: 0, review_count: 0}];

function choose(file: File): void {
  fireEvent.change(screen.getByLabelText("Файл импорта"), {target: {files: [file]}});
}

describe("import screen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDecks.mockResolvedValue(decks);
    apiMocks.fetchArchivedDecks.mockResolvedValue([]);
    apiMocks.fetchDeck.mockResolvedValue({id: 7, name: "Spanish", description: null, is_archived: false, counts: {new: 0, learning: 0, review: 0}, settings: {new_cards_per_day: 20, reviews_per_day: 200, desired_retention: 0.9, learning_steps_minutes: [1], relearning_steps_minutes: [10], maximum_interval_days: 36500, bury_siblings: false, enable_fuzzing: true, option_preset: "balanced"}});
    apiMocks.importFile.mockResolvedValue({added: 2, updated: 1, unchanged: 3, decks_created: ["Spanish::Verbs"], media_saved: 4});
  });

  it("shows APKG modes and a deck list for CSV", async () => {
    render(<ImportScreen initialDeckId={0} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByText("Выбрать файл");
    choose(new File(["archive"], "cards.apkg"));
    expect(screen.getByLabelText("Колоды из файла (auto)")).toBeChecked();
    expect(screen.getByLabelText("В конкретную колоду")).toBeInTheDocument();
    choose(new File(["front,back"], "cards.csv", {type: "text/csv"}));
    expect(screen.queryByLabelText("Колоды из файла (auto)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Колода для импорта")).toHaveValue("7");
  });

  it("rejects a file larger than 20 MB without requesting import", async () => {
    render(<ImportScreen initialDeckId={7} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByText("Выбрать файл");
    choose(new File([new Uint8Array(20 * 1024 * 1024 + 1)], "large.txt"));
    expect(screen.getByRole("alert")).toHaveTextContent("Файл слишком большой");
    expect(apiMocks.importFile).not.toHaveBeenCalled();
    expect(screen.getByRole("button", {name: "Импортировать"})).toBeDisabled();
  });

  it("imports a file and shows the result card", async () => {
    render(<ImportScreen initialDeckId={7} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByText("Выбрать файл");
    const file = new File(["front,back"], "cards.csv", {type: "text/csv"});
    choose(file);
    fireEvent.click(screen.getByRole("button", {name: "Импортировать"}));
    await waitFor(() => expect(apiMocks.importFile).toHaveBeenCalledWith(file, 7));
    expect(await screen.findByText("Добавлено 2 · Обновлено 1 · Без изменений 3")).toBeInTheDocument();
    expect(screen.getByText("Spanish::Verbs")).toBeInTheDocument();
    expect(screen.getByText("Медиа: 4")).toBeInTheDocument();
  });

  it("keeps the selected file after a validation error", async () => {
    apiMocks.importFile.mockRejectedValueOnce(new ApiError(422, "В файле нет карточек."));
    render(<ImportScreen initialDeckId={7} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByText("Выбрать файл");
    choose(new File(["empty"], "cards.txt"));
    fireEvent.click(screen.getByRole("button", {name: "Импортировать"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("В файле нет карточек.");
    expect(screen.getByText(/cards\.txt/)).toBeInTheDocument();
  });

  it("opens from a deck with that deck preselected", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", {name: "Колоды"}));
    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByRole("button", {name: "Импортировать файл"}));
    expect(await screen.findByLabelText("Колода для импорта")).toHaveValue("7");
  });
});
