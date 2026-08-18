import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {CardCreateScreen} from "./Cards";

const apiMocks = vi.hoisted(() => ({
  createCard: vi.fn(),
  fetchDecks: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

describe("cloze card editor", () => {
  afterEach(() => vi.useRealTimers());

  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDecks.mockResolvedValue([{id: 3, name: "Spanish", new_count: 0, learning_count: 0, review_count: 0}]);
    apiMocks.createCard.mockResolvedValue({note_id: 8, cards_created: 2});
  });

  it("wraps selections, repeats the latest cloze number, and submits cloze payload", async () => {
    render(<CardCreateScreen deckId={3} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByLabelText("Колода");
    fireEvent.click(screen.getByText("Пропуски (cloze)"));

    const text = screen.getByLabelText("Текст") as HTMLTextAreaElement;
    fireEvent.change(text, {target: {value: "one two three"}});
    text.setSelectionRange(0, 3);
    fireEvent.click(screen.getByText("Скрыть выделенное"));
    await waitFor(() => expect(text).toHaveValue("{{c1::one}} two three"));

    const secondStart = text.value.indexOf("two");
    text.setSelectionRange(secondStart, secondStart + 3);
    fireEvent.click(screen.getByText("Скрыть выделенное"));
    await waitFor(() => expect(text).toHaveValue("{{c1::one}} {{c2::two}} three"));

    const thirdStart = text.value.indexOf("three");
    text.setSelectionRange(thirdStart, thirdStart + 5);
    fireEvent.click(screen.getByText("Тот же пропуск"));
    await waitFor(() => expect(text).toHaveValue("{{c1::one}} {{c2::two}} {{c2::three}}"));
    expect(screen.getByText("Карточек будет: 2")).toBeInTheDocument();
    expect(screen.queryByText("Обратная карточка")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(apiMocks.createCard).toHaveBeenCalledWith({
      deck_id: 3,
      type: "cloze",
      front: "{{c1::one}} {{c2::two}} {{c2::three}}",
      back: "",
      tags: [],
      reverse: false,
    }));
  });

  it("shows a client-side error instead of submitting cloze text without a deletion", async () => {
    render(<CardCreateScreen deckId={3} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByLabelText("Колода");
    fireEvent.click(screen.getByText("Пропуски (cloze)"));
    fireEvent.change(screen.getByLabelText("Текст"), {target: {value: "plain text"}});
    fireEvent.click(screen.getByText("Сохранить"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Добавьте хотя бы один пропуск");
    expect(apiMocks.createCard).not.toHaveBeenCalled();
  });
});
