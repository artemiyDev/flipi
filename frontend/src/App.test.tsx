import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

const apiMocks = vi.hoisted(() => ({
  fetchDecks: vi.fn(),
  fetchNextCard: vi.fn(),
  submitAnswer: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  fetchDecks: apiMocks.fetchDecks,
  fetchNextCard: apiMocks.fetchNextCard,
  submitAnswer: apiMocks.submitAnswer,
}));

describe("Mini App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders deck counts, study all, and an empty state", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish::Verbs", new_count: 3, learning_count: 2, review_count: 15}]);
    apiMocks.fetchNextCard.mockResolvedValue({card_id: null, done_today: 0});
    render(<App />);

    expect(await screen.findByText("Spanish::Verbs")).toBeInTheDocument();
    expect(screen.getByText("Учить всё")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Учить всё"));
    await waitFor(() => expect(apiMocks.fetchNextCard).toHaveBeenCalledWith("all"));

    apiMocks.fetchDecks.mockResolvedValueOnce([]);
    render(<App />);
    expect(await screen.findByText("Создайте колоду в боте")).toBeInTheDocument();
  });

  it("shows an unauthorized placeholder after a 401 response", async () => {
    const {ApiError} = await import("./api");
    apiMocks.fetchDecks.mockRejectedValue(new ApiError(401));
    render(<App />);

    expect(await screen.findByText("Откройте приложение из Telegram")).toBeInTheDocument();
  });

  it("answers a card and loads the next one", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce({card_id: 7, deck_id: 1, deck_name: "Spanish", progress: {new: 1, learning: 0, review: 0}, question_html: "<b>Question</b>", answer_html: "<i>Answer</i>", card_css: null, media: [], intervals: {again: "1м", hard: "5м", good: "1д", easy: "4д"}})
      .mockResolvedValueOnce({card_id: null, done_today: 9});
    apiMocks.submitAnswer.mockResolvedValue({ok: true, state: "review", due: "2026-01-01T00:00:00Z"});
    const {container} = render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot?.textContent).toContain("Question"));
    fireEvent.click(screen.getByText("Показать ответ"));
    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot?.textContent).toContain("Answer"));
    fireEvent.click(screen.getByText("Хорошо"));
    await waitFor(() => expect(apiMocks.submitAnswer).toHaveBeenCalledWith(7, 3, expect.any(Number)));
    expect(await screen.findByText("Готово. Сегодня: 9")).toBeInTheDocument();
  });
});
