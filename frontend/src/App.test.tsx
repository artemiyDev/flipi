import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

const apiMocks = vi.hoisted(() => ({
  deferLeech: vi.fn(),
  fetchCard: vi.fn(),
  fetchDecks: vi.fn(),
  fetchNextCard: vi.fn(),
  resumeLeech: vi.fn(),
  submitAnswer: vi.fn(),
}));

function dailyGoals(done = 7, remaining = 13) {
  return {
    streak: {done, target: 10, achieved: done >= 10},
    full: {remaining, achieved: remaining === 0},
  };
}

function studyCard(goals = dailyGoals(), deckName = "Spanish") {
  return {
    card_id: 7,
    deck_id: 1,
    deck_name: deckName,
    learn_ahead: null,
    progress: {new: 1, learning: 0, review: 0},
    goals,
    question_html: "<b>Question</b>",
    answer_html: "<i>Answer</i>",
    card_css: null,
    media: [],
    intervals: {again: "1м", hard: "5м", good: "1д", easy: "4д"},
  };
}

function studyDone(goals = dailyGoals(), doneToday = 1) {
  return {card_id: null, done_today: doneToday, goals};
}

function answerResult(leech: {review_lapses: number; auto_suspended: true} | null = null, replayed = false) {
  return {ok: true as const, state: "review", due: "2026-01-01T00:00:00Z", replayed, leech};
}

const cardDetail = {
  card_id: 7,
  note_id: 8,
  deck_id: 1,
  deck_name: "Spanish",
  question_html: "<b>Question</b>",
  answer_html: "<i>Answer</i>",
  card_css: null,
  media: [],
  fields: {},
  front: "Question",
  back: "Answer",
  tags: [],
  state: "relearning",
  due: "2026-01-01T00:00:00Z",
  lapses: 4,
  review_lapses: 4,
  is_leech: true,
  suspended: true,
  buried_until: null,
  flag: null,
};

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  deferLeech: apiMocks.deferLeech,
  fetchCard: apiMocks.fetchCard,
  fetchDecks: apiMocks.fetchDecks,
  fetchNextCard: apiMocks.fetchNextCard,
  resumeLeech: apiMocks.resumeLeech,
  submitAnswer: apiMocks.submitAnswer,
}));

describe("Mini App", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.deferLeech.mockResolvedValue({ok: true});
    apiMocks.fetchCard.mockResolvedValue(cardDetail);
    apiMocks.resumeLeech.mockResolvedValue({ok: true});
  });

  it("renders deck counts, study all, and an empty state", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish::Verbs", new_count: 3, learning_count: 2, review_count: 15}]);
    apiMocks.fetchNextCard.mockResolvedValue(studyDone(dailyGoals(0, 0), 0));
    render(<App />);

    expect(await screen.findByText("Spanish::Verbs")).toBeInTheDocument();
    expect(screen.getByText("Учить всё")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Учить всё"));
    await waitFor(() => expect(apiMocks.fetchNextCard).toHaveBeenCalledWith("all"));
    expect(await screen.findByText("На сегодня всё")).toBeInTheDocument();
    expect(screen.getByText("День закрыт ✓")).toBeInTheDocument();

    apiMocks.fetchDecks.mockResolvedValueOnce([]);
    render(<App />);
    expect(await screen.findByText("Создайте колоду в боте")).toBeInTheDocument();
  });

  it("shows the global remainder when the all-decks queue is temporarily empty", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard.mockResolvedValueOnce(studyDone(dailyGoals(7, 13), 9));
    render(<App />);

    fireEvent.click(await screen.findByText("Учить всё"));

    expect(await screen.findByText("Пока всё. Сегодня ещё 13")).toBeInTheDocument();
    expect(screen.getByText("Серия 7/10")).toBeInTheDocument();
    expect(screen.getByText("На сегодня осталось 13")).toBeInTheDocument();
  });

  it("shows global goals when the selected deck queue is empty", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard.mockResolvedValueOnce(studyDone(dailyGoals(7, 6), 9));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));

    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(screen.getByText("Серия 7/10")).toBeInTheDocument();
    expect(screen.getByText("На сегодня осталось 6")).toBeInTheDocument();
  });

  it("shows achieved goals in the card header and keeps a long deck name constrained", async () => {
    const longName = "Испанский язык: неправильные глаголы для продвинутого уровня";
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: longName, new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard.mockResolvedValueOnce(studyCard(dailyGoals(10, 0), longName));
    render(<App />);

    fireEvent.click(await screen.findByText(longName));

    expect(await screen.findByText("Серия 10/10 ✓")).toBeInTheDocument();
    expect(screen.getByText("День закрыт ✓")).toBeInTheDocument();
    expect(screen.getByTitle(longName)).toHaveClass("session-deck-name");
    expect(document.querySelector(".session-header > .daily-goals")).toBeInTheDocument();
    expect(screen.queryByText(/Повтор чуть раньше/)).not.toBeInTheDocument();
  });

  it.each([
    {secondsEarly: 1, minutes: 1},
    {secondsEarly: 61, minutes: 2},
  ])("shows a rounded learn-ahead hint for $secondsEarly seconds", async ({secondsEarly, minutes}) => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 0, learning_count: 1, review_count: 0}]);
    apiMocks.fetchNextCard.mockResolvedValueOnce({
      ...studyCard(),
      learn_ahead: {scheduled_for: "2026-08-21T17:10:00Z", seconds_early: secondsEarly},
    });
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));

    expect(await screen.findByText(`Повтор чуть раньше · через ${minutes} мин`)).toHaveClass("learn-ahead-hint");
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
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone(dailyGoals(10, 0), 9));
    apiMocks.submitAnswer.mockResolvedValue(answerResult());
    const {container} = render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    expect(await screen.findByText("Серия 7/10")).toBeInTheDocument();
    expect(screen.getByText("На сегодня осталось 13")).toBeInTheDocument();
    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot?.textContent).toContain("Question"));
    fireEvent.click(screen.getByText("Показать ответ"));
    await waitFor(() => expect(container.querySelector(".card-content")?.shadowRoot?.textContent).toContain("Answer"));
    fireEvent.click(screen.getByText("Хорошо"));
    await waitFor(() => expect(apiMocks.submitAnswer).toHaveBeenCalledWith(7, 3, expect.any(Number), expect.any(String)));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(screen.getByText("Серия 10/10 ✓")).toBeInTheDocument();
    expect(screen.getByText("День закрыт ✓")).toBeInTheDocument();
  });

  it("updates goals only from the next study response", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    let resolveNext: (value: ReturnType<typeof studyCard>) => void = () => undefined;
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard(dailyGoals(7, 13)))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveNext = resolve;
      }));
    apiMocks.submitAnswer.mockResolvedValue(answerResult());
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Хорошо/}));

    await waitFor(() => expect(apiMocks.fetchNextCard).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Серия 7/10")).toBeInTheDocument();
    expect(screen.getByText("На сегодня осталось 13")).toBeInTheDocument();
    expect(screen.queryByText("Серия 8/10")).not.toBeInTheDocument();

    await act(async () => {
      resolveNext(studyCard(dailyGoals(8, 12)));
    });
    expect(await screen.findByText("Серия 8/10")).toBeInTheDocument();
    expect(screen.getByText("На сегодня осталось 12")).toBeInTheDocument();
  });

  it("locks every rating and submits one UUID while an answer is pending", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    let resolveAnswer: (value: ReturnType<typeof answerResult>) => void = () => undefined;
    apiMocks.submitAnswer.mockReturnValue(new Promise((resolve) => {
      resolveAnswer = resolve;
    }));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    const goodButton = await screen.findByRole("button", {name: /Хорошо/});
    act(() => {
      fireEvent.click(goodButton);
      fireEvent.click(goodButton);
    });

    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
    expect(apiMocks.submitAnswer).toHaveBeenCalledWith(
      7,
      3,
      expect.any(Number),
      expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i),
    );
    for (const label of ["Снова", "Трудно", "Хорошо", "Легко"]) {
      expect(screen.getByRole("button", {name: new RegExp(label)})).toBeDisabled();
    }
    expect(screen.getByRole("button", {name: "К колодам"})).toBeDisabled();

    await act(async () => {
      resolveAnswer(answerResult());
    });
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
  });

  it("retries an ambiguous answer error with the same payload", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(answerResult(null, true));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Хорошо/}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось подтвердить ответ");
    const firstPayload = apiMocks.submitAnswer.mock.calls[0];
    for (const label of ["Снова", "Трудно", "Хорошо", "Легко"]) {
      expect(screen.getByRole("button", {name: new RegExp(label)})).toBeDisabled();
    }

    fireEvent.click(screen.getByRole("button", {name: "Повторить отправку"}));
    await waitFor(() => expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(2));
    expect(apiMocks.submitAnswer.mock.calls[1]).toEqual(firstPayload);
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
  });

  it("keeps the answer attempt when loading the next card fails", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer
      .mockResolvedValueOnce(answerResult())
      .mockResolvedValueOnce(answerResult(null, true));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Хорошо/}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось подтвердить ответ");
    const firstPayload = apiMocks.submitAnswer.mock.calls[0];
    fireEvent.click(screen.getByRole("button", {name: "Повторить отправку"}));

    await waitFor(() => expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(2));
    expect(apiMocks.submitAnswer.mock.calls[1]).toEqual(firstPayload);
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
  });

  it("shows the same leech rescue after an ambiguous answer retry before loading next", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(answerResult({review_lapses: 4, auto_suspended: true}, true));
    const {container} = render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось подтвердить ответ");
    const firstPayload = apiMocks.submitAnswer.mock.calls[0];

    fireEvent.click(screen.getByRole("button", {name: "Повторить отправку"}));

    expect(await screen.findByRole("heading", {name: "Карточка забыта 4 раза"})).toBeInTheDocument();
    expect(container.querySelector(".card-content")?.shadowRoot?.textContent).toContain("Answer");
    expect(apiMocks.submitAnswer.mock.calls[1]).toEqual(firstPayload);
    expect(apiMocks.fetchNextCard).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", {name: "К колодам"})).toBeDisabled();
    expect(screen.queryByRole("button", {name: /Снова/})).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: "Оставить на потом"}));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.deferLeech).toHaveBeenCalledWith(7, 4);
    expect(apiMocks.resumeLeech).not.toHaveBeenCalled();
  });

  it("handles a guarded resume conflict without retrying the answer", async () => {
    const {ApiError} = await import("./api");
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 6, auto_suspended: true}));
    apiMocks.resumeLeech.mockRejectedValueOnce(new ApiError(409));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    fireEvent.click(await screen.findByRole("button", {name: "Продолжить учить"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Карточка уже изменилась");
    expect(apiMocks.resumeLeech).toHaveBeenCalledWith(7, 6);
    expect(apiMocks.fetchNextCard).toHaveBeenCalledTimes(1);
    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", {name: "Продолжить учить"})).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", {name: "Оставить на потом"}));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.resumeLeech).toHaveBeenCalledTimes(1);
    expect(apiMocks.deferLeech).toHaveBeenCalledWith(7, 6);
    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
  });

  it("locks rescue actions during guarded resume", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 4, auto_suspended: true}));
    let resolveResume: (value: {ok: true}) => void = () => undefined;
    apiMocks.resumeLeech.mockReturnValue(new Promise((resolve) => {
      resolveResume = resolve;
    }));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    const continueButton = await screen.findByRole("button", {name: "Продолжить учить"});
    act(() => {
      fireEvent.click(continueButton);
      fireEvent.click(continueButton);
    });

    expect(apiMocks.resumeLeech).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", {name: "Возвращаем карточку…"})).toBeDisabled();
    expect(screen.queryByRole("button", {name: "Исправить карточку"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Оставить на потом"})).not.toBeInTheDocument();

    await act(async () => resolveResume({ok: true}));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
  });

  it("keeps rescue open and retries only guarded leave when it fails", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 4, auto_suspended: true}));
    apiMocks.deferLeech
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ok: true});
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    fireEvent.click(await screen.findByRole("button", {name: "Оставить на потом"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось оставить карточку на потом");
    expect(screen.getByRole("heading", {name: "Карточка забыта 4 раза"})).toBeInTheDocument();
    expect(apiMocks.deferLeech).toHaveBeenCalledWith(7, 4);
    expect(apiMocks.fetchNextCard).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", {name: "Продолжить учить"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Исправить карточку"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Повторить: оставить на потом"}));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.deferLeech).toHaveBeenCalledTimes(2);
    expect(apiMocks.resumeLeech).not.toHaveBeenCalled();
    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
  });

  it("keeps rescue open when next-card fails after guarded leave", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 4, auto_suspended: true}));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    fireEvent.click(await screen.findByRole("button", {name: "Оставить на потом"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Попробуйте ещё раз");
    expect(screen.getByRole("heading", {name: "Карточка забыта 4 раза"})).toBeInTheDocument();
    expect(apiMocks.deferLeech).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", {name: "Оставить на потом"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Исправить карточку"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Повторить продолжение"}));
    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.deferLeech).toHaveBeenCalledTimes(1);
    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
  });

  it("retries only next-card after guarded resume has committed", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 6, auto_suspended: true}));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    fireEvent.click(await screen.findByRole("button", {name: "Продолжить учить"}));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить следующую карточку");
    expect(apiMocks.resumeLeech).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", {name: "Оставить на потом"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Исправить карточку"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Повторить продолжение"}));

    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.resumeLeech).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchNextCard).toHaveBeenCalledTimes(3);
    expect(apiMocks.submitAnswer).toHaveBeenCalledTimes(1);
  });

  it("opens the leech editor and returns to the same study scope", async () => {
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard
      .mockResolvedValueOnce(studyCard())
      .mockResolvedValueOnce(studyDone());
    apiMocks.submitAnswer.mockResolvedValue(answerResult({review_lapses: 4, auto_suspended: true}));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Снова/}));
    fireEvent.click(await screen.findByRole("button", {name: "Исправить карточку"}));

    await waitFor(() => expect(apiMocks.fetchCard).toHaveBeenCalledWith(7));
    expect(await screen.findByText("Карточка часто забывается")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "К списку карточек"}));

    expect(await screen.findByText("В этой колоде пока всё")).toBeInTheDocument();
    expect(apiMocks.fetchNextCard).toHaveBeenNthCalledWith(2, 1);
    expect(apiMocks.deferLeech).not.toHaveBeenCalled();
    expect(apiMocks.resumeLeech).not.toHaveBeenCalled();
  });

  it("stops retrying a definitive answer conflict and lets the user leave", async () => {
    const {ApiError} = await import("./api");
    apiMocks.fetchDecks.mockResolvedValue([{id: 1, name: "Spanish", new_count: 1, learning_count: 0, review_count: 0}]);
    apiMocks.fetchNextCard.mockResolvedValueOnce(studyCard());
    apiMocks.submitAnswer.mockRejectedValueOnce(new ApiError(409));
    render(<App />);

    fireEvent.click(await screen.findByText("Spanish"));
    fireEvent.click(await screen.findByText("Показать ответ"));
    fireEvent.click(await screen.findByRole("button", {name: /Хорошо/}));

    expect(await screen.findByText("Ответ отклонён. Начните сессию заново.")).toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "Повторить отправку"})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "К колодам"}));
    expect(await screen.findByText("Учить всё")).toBeInTheDocument();
  });
});
