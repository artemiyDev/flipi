import {useCallback, useEffect, useState} from "react";

import {ApiError, fetchCatalog, installCatalogDeck, type CatalogDeck} from "./api";

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function Catalog({onClose, onUnauthorized}: {onClose: () => void; onUnauthorized: () => void}): JSX.Element {
  const [decks, setDecks] = useState<CatalogDeck[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchCatalog().then(setDecks).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError("Не удалось загрузить каталог.");
      }
    });
  }, [onUnauthorized]);

  useEffect(load, [load]);

  const install = (deck: CatalogDeck) => {
    setInstalling(deck.slug);
    setError(null);
    installCatalogDeck(deck.slug).then(() => {
      setDecks((current) => current?.map((item) => item.slug === deck.slug ? {...item, installed: true} : item) ?? null);
      setNotice("Колода появилась в списке — можно учить");
    }).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else if (requestError instanceof ApiError && requestError.status === 409) {
        load();
      } else {
        setError("Не удалось добавить колоду.");
      }
    }).finally(() => setInstalling(null));
  };

  return <section className="catalog-screen">
    <header className="screen-header"><button className="close" aria-label="К колодам" onClick={onClose}>×</button><h1>Каталог</h1></header>
    {notice && <p className="catalog-notice" role="status">{notice}</p>}
    {error && <p className="field-error" role="alert">{error}</p>}
    {decks === null ? <p className="hint centered">Загрузка…</p> : decks.length === 0 ? <p className="hint">В каталоге пока нет колод.</p> : <div className="catalog-list">
      {decks.map((deck) => <article className="catalog-deck" key={deck.slug}>
        <div><h2>{deck.title}</h2><p>{deck.description}</p><small>{deck.notes_count} карточек · {deck.language}</small><div className="catalog-tags">{deck.tags.map((tag) => <span key={tag}>{tag}</span>)}</div></div>
        <button className="primary" disabled={deck.installed || installing === deck.slug} onClick={() => install(deck)}>{deck.installed ? "Добавлено ✓" : installing === deck.slug ? "Добавляем…" : "Добавить"}</button>
      </article>)}
    </div>}
  </section>;
}
