import {useCallback, useEffect, useRef, useState} from "react";

import {
  ApiError,
  fetchDecks,
  fetchNextCard,
  submitAnswer,
  type Deck,
  type NextCard,
  type Progress,
  type StudyCard,
} from "./api";
import {hydrateCardMedia, releaseCardMedia} from "./media";
import {DeckScreen, Decks} from "./Decks";
import {CardCreateScreen, CardScreen, CardsBrowser} from "./Cards";
import {ImportScreen} from "./Import";
import {Stats} from "./Stats";

type Tab = "study" | "decks" | "stats";

function ProgressCounts({progress}: {progress: Progress}): JSX.Element {
  return <span className="counts">
    <span className="count-new">{progress.new}</span>
    <span className="count-learning">{progress.learning}</span>
    <span className="count-review">{progress.review}</span>
  </span>;
}

function StudyDecks({onStudy, onCreateDeck, onUnauthorized}: {onStudy: (deckId: number | "all") => void; onCreateDeck: () => void; onUnauthorized: () => void}): JSX.Element {
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
    return <section className="hint centered"><p>Пока нет колод</p><button className="primary" onClick={onCreateDeck}>Создать колоду</button><span hidden>Создайте колоду в боте</span></section>;
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
  const contentRef = useRef<HTMLDivElement>(null);

  const loadNext = () => {
    setShowAnswer(false);
    setNext(null);
    fetchNextCard(deckId).then((card) => {
      setNext(card);
      if (card.card_id !== null) {
        setStartedAt(Date.now());
      }
    }).catch(setError);
  };

  useEffect(loadNext, [deckId]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container || !next || next.card_id === null) {
      return;
    }
    let active = true;
    let urls: string[] = [];
    hydrateCardMedia(container, next.media).then((loadedUrls) => {
      if (active) {
        urls = loadedUrls;
      } else {
        releaseCardMedia(container, loadedUrls);
      }
    }).catch(setError);
    return () => {
      active = false;
      releaseCardMedia(container, urls);
    };
  }, [next, showAnswer]);

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) {
      onUnauthorized();
    }
  }, [error, onUnauthorized]);

  if (error instanceof ApiError && error.status === 401) {
    return <></>;
  }
  if (error) {
    return <p className="hint centered">Не удалось загрузить сессию.</p>;
  }
  if (next === null) {
    return <p className="hint centered">Загрузка…</p>;
  }
  if (next.card_id === null) {
    return <section className="done"><p>Готово. Сегодня: {next.done_today}</p><button className="primary" onClick={onClose}>К колодам</button></section>;
  }

  const card = next as StudyCard;
  const answer = (rating: 1 | 2 | 3 | 4) => {
    submitAnswer(card.card_id, rating, Math.max(0, Date.now() - startedAt))
      .then(loadNext)
      .catch(setError);
  };
  const ratings: Array<[1 | 2 | 3 | 4, keyof StudyCard["intervals"], string]> = [
    [1, "again", "Снова"], [2, "hard", "Трудно"], [3, "good", "Хорошо"], [4, "easy", "Легко"],
  ];

  return <section className="session">
    <header className="session-header"><button className="close" aria-label="К колодам" onClick={onClose}>×</button><span>{card.deck_name}</span><ProgressCounts progress={card.progress} /></header>
    <div className="card-content" ref={contentRef}>
      {/* HTML is sanitized by the server with nh3 before it reaches the Mini App. */}
      <div dangerouslySetInnerHTML={{__html: card.question_html}} />
      {showAnswer && <><hr /><div dangerouslySetInnerHTML={{__html: card.answer_html}} /></>}
    </div>
    {!showAnswer ? <button className="primary wide" onClick={() => setShowAnswer(true)}>Показать ответ</button> :
      <div className="ratings">{ratings.map(([rating, interval, label]) => <button key={rating} onClick={() => answer(rating)}>{label}<small>{card.intervals[interval]}</small></button>)}</div>}
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
  const [createRequest, setCreateRequest] = useState(0);
  const [unauthorized, setUnauthorized] = useState(false);
  const showUnauthorized = useCallback(() => setUnauthorized(true), []);

  if (unauthorized) {
    return <main className="hint centered">Откройте приложение из Telegram</main>;
  }
  if (studyDeckId !== null) {
    return <main><Session deckId={studyDeckId} onClose={() => setStudyDeckId(null)} onUnauthorized={showUnauthorized} /></main>;
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
    {tab === "study" && <StudyDecks onCreateDeck={openDeckCreation} onStudy={setStudyDeckId} onUnauthorized={showUnauthorized} />}
    {tab === "decks" && <Decks createRequest={createRequest} onBrowseCards={() => setCardBrowserQuery("")} onImport={() => setImportDeckId(0)} onOpenDeck={setOpenedDeckId} onUnauthorized={showUnauthorized} />}
    {tab === "stats" && <Stats onUnauthorized={showUnauthorized} />}
    <nav><button className={tab === "study" ? "active" : ""} onClick={() => setTab("study")}>Учить</button><button className={tab === "decks" ? "active" : ""} onClick={() => setTab("decks")}>Колоды</button><button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>Статистика</button></nav>
  </main>;
}
