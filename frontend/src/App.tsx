import {useCallback, useEffect, useRef, useState} from "react";

import {
  ApiError,
  fetchDecks,
  fetchNextCard,
  submitAnswer,
  type DailyGoals,
  type Deck,
  type NextCard,
  type Progress,
  type StudyCard,
} from "./api";
import {CardBody} from "./CardBody";
import {DeckScreen, Decks} from "./Decks";
import {CardCreateScreen, CardScreen, CardsBrowser} from "./Cards";
import {ImportScreen} from "./Import";
import {Stats} from "./Stats";
import {Catalog} from "./Catalog";
import {Help} from "./Help";
import {ShareInstallScreen} from "./Share";

type Tab = "study" | "decks" | "stats";
type AnswerAttempt = {
  cardId: number;
  rating: 1 | 2 | 3 | 4;
  elapsedMs: number;
  requestId: string;
};

function ProgressCounts({progress}: {progress: Progress}): JSX.Element {
  return <span className="counts">
    <span className="count-new">{progress.new}</span>
    <span className="count-learning">{progress.learning}</span>
    <span className="count-review">{progress.review}</span>
  </span>;
}

function GoalStatus({goals}: {goals: DailyGoals}): JSX.Element {
  const streak = `Серия ${goals.streak.done}/${goals.streak.target}${goals.streak.achieved ? " ✓" : ""}`;
  const full = goals.full.achieved ? "День закрыт ✓" : `На сегодня осталось ${goals.full.remaining}`;

  return <div className="daily-goals">
    <span>{streak}</span>
    <span>{full}</span>
  </div>;
}

function StudyDecks({onStudy, onCreateDeck, onCatalog, onUnauthorized}: {onStudy: (deckId: number | "all") => void; onCreateDeck: () => void; onCatalog: () => void; onUnauthorized: () => void}): JSX.Element {
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    fetchDecks().then(setDecks).catch(setError);
  }, []);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      onUnauthorized();
    }
  }, [error, onUnauthorized]);

  if (error instanceof ApiError && error.status === 401) {
    return <></>;
  }
  if (error) {
    return <p className="hint centered">Не удалось загрузить колоды.</p>;
  }
  if (decks === null) {
    return <p className="hint centered">Загрузка…</p>;
  }
  if (decks.length === 0) {
    return <section className="hint centered"><p>Пока нет колод</p><div className="empty-actions"><button className="primary" onClick={onCatalog}>Из каталога</button><button onClick={onCreateDeck}>Создать свою</button></div><span hidden>Создайте колоду в боте</span></section>;
  }

  const total = decks.reduce(
    (sum, deck) => sum + deck.new_count + deck.learning_count + deck.review_count,
    0,
  );
  return <section className="decks">
    {total > 0 && <button className="primary wide" onClick={() => onStudy("all")}>Учить всё</button>}
    <div className="deck-list">
      {decks.map((deck) => <button className="deck" key={deck.id} onClick={() => onStudy(deck.id)}>
        <span>{deck.name}</span>
        <ProgressCounts progress={{new: deck.new_count, learning: deck.learning_count, review: deck.review_count}} />
      </button>)}
    </div>
  </section>;
}

function Session({deckId, onClose, onUnauthorized}: {deckId: number | "all"; onClose: () => void; onUnauthorized: () => void}): JSX.Element {
  const [next, setNext] = useState<NextCard | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const [startedAt, setStartedAt] = useState(0);
  const [error, setError] = useState<Error | null>(null);
  const [answerError, setAnswerError] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [answerAttempt, setAnswerAttempt] = useState<AnswerAttempt | null>(null);
  const isSubmittingRef = useRef(false);
  const answerAttemptRef = useRef<AnswerAttempt | null>(null);

  const loadNext = () => {
    const followsSubmittedAnswer = answerAttemptRef.current !== null;
    setAnswerError(false);
    if (!followsSubmittedAnswer) {
      setShowAnswer(false);
      setNext(null);
    }
    fetchNextCard(deckId).then((card) => {
      isSubmittingRef.current = false;
      answerAttemptRef.current = null;
      setIsSubmitting(false);
      setAnswerAttempt(null);
      setShowAnswer(false);
      setNext(card);
      if (card.card_id !== null) {
        setStartedAt(Date.now());
      }
    }).catch((cause: Error) => {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
      if (followsSubmittedAnswer && !(cause instanceof ApiError && cause.status === 401)) {
        setAnswerError(true);
        return;
      }
      setError(cause);
    });
  };

  useEffect(loadNext, [deckId]);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      onUnauthorized();
    }
  }, [error, onUnauthorized]);

  if (error instanceof ApiError && error.status === 401) {
    return <></>;
  }
  if (error) {
    const answerWasRejected = error instanceof ApiError && [404, 409, 422].includes(error.status);
    return <section className="done">
      <p className="hint centered">{answerWasRejected ? "Ответ отклонён. Начните сессию заново." : "Не удалось загрузить сессию."}</p>
      <button className="primary" onClick={onClose}>К колодам</button>
    </section>;
  }
  if (next === null) {
    return <p className="hint centered">Загрузка…</p>;
  }
  if (next.card_id === null) {
    const message = deckId === "all"
      ? (next.goals.full.achieved ? "На сегодня всё" : `Пока всё. Сегодня ещё ${next.goals.full.remaining}`)
      : "В этой колоде пока всё";
    return <section className="done">
      <p className="done-title">{message}</p>
      <GoalStatus goals={next.goals} />
      <button className="primary" onClick={onClose}>К колодам</button>
    </section>;
  }

  const card = next as StudyCard;
  const submitAttempt = (attempt: AnswerAttempt) => {
    if (isSubmittingRef.current) {
      return;
    }
    isSubmittingRef.current = true;
    setIsSubmitting(true);
    submitAnswer(attempt.cardId, attempt.rating, attempt.elapsedMs, attempt.requestId)
      .then(loadNext)
      .catch((cause: unknown) => {
        isSubmittingRef.current = false;
        setIsSubmitting(false);
        if (cause instanceof ApiError && cause.status === 401) {
          answerAttemptRef.current = null;
          setAnswerAttempt(null);
          setError(cause);
          return;
        }
        if (cause instanceof ApiError && [404, 409, 422].includes(cause.status)) {
          answerAttemptRef.current = null;
          setAnswerAttempt(null);
          setError(cause);
          return;
        }
        setAnswerError(true);
      });
  };
  const answer = (rating: 1 | 2 | 3 | 4) => {
    if (isSubmittingRef.current || answerAttemptRef.current !== null) {
      return;
    }
    const attempt = {
      cardId: card.card_id,
      rating,
      elapsedMs: Math.max(0, Date.now() - startedAt),
      requestId: crypto.randomUUID(),
    };
    answerAttemptRef.current = attempt;
    setAnswerAttempt(attempt);
    setAnswerError(false);
    submitAttempt(attempt);
  };
  const retryAnswer = () => {
    if (answerAttemptRef.current !== null) {
      submitAttempt(answerAttemptRef.current);
    }
  };
  const ratings: Array<[1 | 2 | 3 | 4, keyof StudyCard["intervals"], string]> = [
    [1, "again", "Снова"], [2, "hard", "Трудно"], [3, "good", "Хорошо"], [4, "easy", "Легко"],
  ];

  return <section className="session">
    <header className="session-header">
      <button className="close" aria-label="К колодам" disabled={answerAttempt !== null} onClick={onClose}>×</button>
      <span className="session-deck-name" title={card.deck_name}>{card.deck_name}</span>
      <ProgressCounts progress={card.progress} />
      <GoalStatus goals={card.goals} />
    </header>
    <CardBody questionHtml={card.question_html} answerHtml={showAnswer ? card.answer_html : undefined} cardCss={card.card_css} media={card.media} />
    {!showAnswer ? <button className="primary wide" onClick={() => setShowAnswer(true)}>Показать ответ</button> :
      <>
        {answerError && <section className="hint centered" role="alert"><p>Не удалось подтвердить ответ.</p><button className="primary" disabled={isSubmitting} onClick={retryAnswer}>Повторить отправку</button></section>}
        <div className="ratings">{ratings.map(([rating, interval, label]) => <button disabled={isSubmitting || answerAttempt !== null} key={rating} onClick={() => answer(rating)}>{label}<small>{card.intervals[interval]}</small></button>)}</div>
      </>}
  </section>;
}

export function App(): JSX.Element {
  const [tab, setTab] = useState<Tab>("study");
  // Два независимых пути: studyDeckId открывает учебную сессию (таб «Учить»),
  // openedDeckId — экран управления колодой (таб «Колоды»).
  const [studyDeckId, setStudyDeckId] = useState<number | "all" | null>(null);
  const [openedDeckId, setOpenedDeckId] = useState<number | null>(null);
  const [cardCreateDeckId, setCardCreateDeckId] = useState<number | null>(null);
  const [cardBrowserQuery, setCardBrowserQuery] = useState<string | null>(null);
  const [openedCardId, setOpenedCardId] = useState<number | null>(null);
  const [importDeckId, setImportDeckId] = useState<number | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [shareToken, setShareToken] = useState<string | null>(() => new URLSearchParams(window.location.search).get("share"));
  const [createRequest, setCreateRequest] = useState(0);
  const [unauthorized, setUnauthorized] = useState(false);
  const showUnauthorized = useCallback(() => setUnauthorized(true), []);
  const closeShareScreen = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete("share");
    window.history.replaceState(window.history.state, "", url);
    setShareToken(null);
  }, []);

  if (unauthorized) {
    return <main className="hint centered">Откройте приложение из Telegram</main>;
  }
  if (shareToken) {
    return <main><ShareInstallScreen onClose={closeShareScreen} onStudy={(deckId) => { closeShareScreen(); setStudyDeckId(deckId); }} onUnauthorized={showUnauthorized} token={shareToken} /></main>;
  }
  if (studyDeckId !== null) {
    return <main><Session deckId={studyDeckId} onClose={() => setStudyDeckId(null)} onUnauthorized={showUnauthorized} /></main>;
  }
  if (catalogOpen) {
    return <main><Catalog onClose={() => setCatalogOpen(false)} onUnauthorized={showUnauthorized} /></main>;
  }
  if (helpOpen) {
    return <main><Help onClose={() => setHelpOpen(false)} /></main>;
  }
  if (cardCreateDeckId !== null) {
    return <main><CardCreateScreen deckId={cardCreateDeckId} onClose={() => setCardCreateDeckId(null)} onUnauthorized={showUnauthorized} /></main>;
  }
  if (openedCardId !== null) {
    return <main><CardScreen cardId={openedCardId} onBack={() => setOpenedCardId(null)} onDeleted={() => setOpenedCardId(null)} onUnauthorized={showUnauthorized} /></main>;
  }
  if (cardBrowserQuery !== null) {
    return <main><CardsBrowser initialQuery={cardBrowserQuery} onClose={() => setCardBrowserQuery(null)} onOpenCard={setOpenedCardId} onUnauthorized={showUnauthorized} /></main>;
  }
  if (importDeckId !== null) {
    return <main><ImportScreen initialDeckId={importDeckId} onClose={() => { setImportDeckId(null); setOpenedDeckId(null); }} onUnauthorized={showUnauthorized} /></main>;
  }
  if (openedDeckId !== null) {
    return <main><DeckScreen deckId={openedDeckId} onAddCard={setCardCreateDeckId} onBack={() => setOpenedDeckId(null)} onBrowseCards={setCardBrowserQuery} onImport={setImportDeckId} onUnauthorized={showUnauthorized} /></main>;
  }
  const openDeckCreation = () => {
    setTab("decks");
    setCreateRequest((request) => request + 1);
  };
  return <main>
    <header className="app-header"><button aria-label="Помощь" onClick={() => setHelpOpen(true)}>?</button></header>
    {tab === "study" && <StudyDecks onCatalog={() => setCatalogOpen(true)} onCreateDeck={openDeckCreation} onStudy={setStudyDeckId} onUnauthorized={showUnauthorized} />}
    {tab === "decks" && <Decks createRequest={createRequest} onBrowseCards={() => setCardBrowserQuery("")} onCatalog={() => setCatalogOpen(true)} onImport={() => setImportDeckId(0)} onOpenDeck={setOpenedDeckId} onUnauthorized={showUnauthorized} />}
    {tab === "stats" && <Stats onUnauthorized={showUnauthorized} />}
    <nav><button className={tab === "study" ? "active" : ""} onClick={() => setTab("study")}>Учить</button><button className={tab === "decks" ? "active" : ""} onClick={() => setTab("decks")}>Колоды</button><button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>Статистика</button></nav>
  </main>;
}
