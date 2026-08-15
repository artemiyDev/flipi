import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {CardCreateScreen, CardScreen, CardsBrowser} from "./Cards";

const apiMocks = vi.hoisted(() => ({
  buryCard: vi.fn(),
  createCard: vi.fn(),
  deleteNote: vi.fn(),
  fetchCard: vi.fn(),
  fetchDecks: vi.fn(),
  resetCard: vi.fn(),
  searchCards: vi.fn(),
  setCardDue: vi.fn(),
  setCardFlag: vi.fn(),
  setCardSuspended: vi.fn(),
  updateNote: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

const card = {
  card_id: 11,
  note_id: 8,
  deck_id: 3,
  deck_name: "Spanish",
  question_html: "<b>Hola</b>",
  answer_html: "<i>Привет</i>",
  fields: {}, front: "Hola", back: "Привет", tags: ["basic"], state: "new", due: "2026-08-15T00:00:00Z", lapses: 0,
  suspended: false, buried_until: null, flag: null,
};

describe("card screens", () => {
  afterEach(() => vi.useRealTimers());
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDecks.mockResolvedValue([{id: 3, name: "Spanish", new_count: 0, learning_count: 0, review_count: 0}]);
    apiMocks.searchCards.mockResolvedValue({total: 2, items: [
      {card_id: 11, note_id: 8, deck_id: 3, deck_name: "Spanish", preview: "Hola", state: "new", due: "2026-08-15", suspended: false, buried: false, flag: "red"},
    ]});
    apiMocks.fetchCard.mockResolvedValue(card);
    apiMocks.createCard.mockResolvedValue({note_id: 8});
    apiMocks.updateNote.mockResolvedValue({ok: true});
    apiMocks.setCardSuspended.mockResolvedValue({ok: true});
    apiMocks.setCardFlag.mockResolvedValue({ok: true});
    apiMocks.deleteNote.mockResolvedValue(undefined);
  });

  it("creates a card, keeps deck and tags for another card, and shows field validation", async () => {
    render(<CardCreateScreen deckId={3} onClose={vi.fn()} onUnauthorized={vi.fn()} />);
    await screen.findByLabelText("Колода");
    fireEvent.click(screen.getByText("Сохранить и добавить ещё"));
    expect(await screen.findAllByRole("alert")).toHaveLength(2);

    fireEvent.change(screen.getByLabelText("Лицевая сторона"), {target: {value: "Hola"}});
    fireEvent.change(screen.getByLabelText("Обратная сторона"), {target: {value: "Привет"}});
    fireEvent.change(screen.getByLabelText(/Теги/), {target: {value: "basic spanish"}});
    apiMocks.createCard.mockRejectedValueOnce(new ApiError(422));
    fireEvent.click(screen.getByText("Сохранить и добавить ещё"));
    expect(await screen.findAllByText("Проверьте содержимое карточки")).toHaveLength(2);
    fireEvent.click(screen.getByText("Сохранить и добавить ещё"));

    await waitFor(() => expect(apiMocks.createCard).toHaveBeenCalledTimes(2));
    expect(apiMocks.createCard).toHaveBeenLastCalledWith({deck_id: 3, front: "Hola", back: "Привет", tags: ["basic", "spanish"], reverse: false});
    expect(screen.getByLabelText("Лицевая сторона")).toHaveValue("");
    expect(screen.getByLabelText("Обратная сторона")).toHaveValue("");
    expect(screen.getByLabelText(/Теги/)).toHaveValue("basic spanish");
  });

  it("searches after debounce, renders badges, and loads another page", async () => {
    // waitFor несовместим с активными фейковыми таймерами: его поллинг сам
    // сидит на setTimeout. Прокручиваем debounce фейковыми, затем сразу
    // возвращаем реальные.
    vi.useFakeTimers();
    render(<CardsBrowser initialQuery="" onClose={vi.fn()} onOpenCard={vi.fn()} onUnauthorized={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Поиск карточек"), {target: {value: "tag:basic"}});
    await vi.advanceTimersByTimeAsync(300);
    vi.useRealTimers();
    await waitFor(() => expect(apiMocks.searchCards).toHaveBeenCalledWith("tag:basic", 25, 0));
    expect(screen.getByText("red")).toHaveClass("flag-red");
    fireEvent.click(screen.getByText("Показать ещё"));
    await waitFor(() => expect(apiMocks.searchCards).toHaveBeenCalledWith("tag:basic", 25, 1));
  });

  it("updates notes, toggles suspension, sets and clears flags, and returns after confirmed deletion", async () => {
    const onDeleted = vi.fn();
    vi.stubGlobal("confirm", vi.fn(() => true));
    render(<CardScreen cardId={11} onBack={vi.fn()} onDeleted={onDeleted} onUnauthorized={vi.fn()} />);
    await screen.findByText("Hola", {selector: "b"});
    fireEvent.change(screen.getByLabelText("Лицевая сторона"), {target: {value: "Buenos días"}});
    fireEvent.click(screen.getByText("Сохранить"));
    await waitFor(() => expect(apiMocks.updateNote).toHaveBeenCalledWith(8, {front: "Buenos días", back: "Привет", tags: ["basic"]}));

    fireEvent.click(screen.getByText("Приостановить"));
    await waitFor(() => expect(apiMocks.setCardSuspended).toHaveBeenCalledWith(11, true));
    fireEvent.click(screen.getByText("red"));
    await waitFor(() => expect(apiMocks.setCardFlag).toHaveBeenCalledWith(11, "red"));
    cleanup();
    apiMocks.fetchCard.mockResolvedValue({...card, flag: "red"});
    render(<CardScreen cardId={11} onBack={vi.fn()} onDeleted={onDeleted} onUnauthorized={vi.fn()} />);
    fireEvent.click(await screen.findByText("Снять"));
    await waitFor(() => expect(apiMocks.setCardFlag).toHaveBeenCalledWith(11, null));

    fireEvent.click(screen.getByText("Удалить заметку"));
    await waitFor(() => expect(apiMocks.deleteNote).toHaveBeenCalledWith(8));
    expect(onDeleted).toHaveBeenCalledOnce();
  });

  it("passes unauthorized requests to the application", async () => {
    const onUnauthorized = vi.fn();
    apiMocks.fetchDecks.mockRejectedValueOnce(new ApiError(401));
    render(<CardCreateScreen deckId={3} onClose={vi.fn()} onUnauthorized={onUnauthorized} />);
    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledOnce());
  });
});
