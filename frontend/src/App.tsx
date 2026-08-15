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
import {Stats} from "./Stats";

type Tab = "study" | "stats";

function ProgressCounts({progress}: {progress: Progress}): JSX.Element {
  return <span className="counts">
    <span className="count-new">{progress.new}</span>
    <span className="count-learning">{progress.learning}</span>
    <span className="count-review">{progress.review}</span>
  </span>;
}

function Decks({onStudy, onUnauthorized}: {onStudy: (deckId: number | "all") => void; onUnauthorized: () => void}): JSX.Element {
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
    return <p className="hint centered">Создайте колоду в боте</p>;
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
  const [deckId, setDeckId] = useState<number | "all" | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const showUnauthorized = useCallback(() => setUnauthorized(true), []);

  if (unauthorized) {
    return <main className="hint centered">Откройте приложение из Telegram</main>;
  }
  if (deckId !== null) {
    return <main><Session deckId={deckId} onClose={() => setDeckId(null)} onUnauthorized={() => setUnauthorized(true)} /></main>;
  }
  return <main>
    {tab === "study" ? <Decks onStudy={setDeckId} onUnauthorized={showUnauthorized} /> : <Stats onUnauthorized={showUnauthorized} />}
    <nav><button className={tab === "study" ? "active" : ""} onClick={() => setTab("study")}>Учить</button><button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>Статистика</button></nav>
  </main>;
}
