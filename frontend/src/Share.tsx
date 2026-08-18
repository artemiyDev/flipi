import {useCallback, useEffect, useState} from "react";

import {ApiError, fetchSharedDeck, installSharedDeck, type SharedDeckPreview} from "./api";

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export function ShareInstallScreen({token, onClose, onStudy, onUnauthorized}: {
  token: string;
  onClose: () => void;
  onStudy: (deckId: number) => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [preview, setPreview] = useState<SharedDeckPreview | null>(null);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installedDeckId, setInstalledDeckId] = useState<number | null>(null);

  const load = useCallback(() => {
    setPreview(null);
    setExpired(false);
    setError(null);
    fetchSharedDeck(token).then(setPreview).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else if (requestError instanceof ApiError && requestError.status === 404) {
        setExpired(true);
      } else {
        setError(new Error("Не удалось загрузить колоду."));
      }
    });
  }, [onUnauthorized, token]);

  useEffect(load, [load]);

  const install = () => {
    setInstalling(true);
    installSharedDeck(token).then(({deck_id}) => {
      setInstalledDeckId(deck_id);
    }).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else if (requestError instanceof ApiError && requestError.status === 409) {
        load();
      } else {
        setError(new Error("Не удалось установить колоду."));
      }
    }).finally(() => setInstalling(false));
  };

  if (expired) {
    return <section className="share-screen centered"><p>Ссылка устарела</p><button className="primary" onClick={onClose}>К колодам</button></section>;
  }
  if (error) {
    return <section className="share-screen centered"><p className="hint">{error.message}</p><button className="primary" onClick={onClose}>К колодам</button></section>;
  }
  if (!preview) {
    return <p className="hint centered">Загрузка…</p>;
  }
  if (installedDeckId !== null) {
    return <section className="share-screen">
      <p className="share-success">✓ Колода добавлена</p>
      <div className="share-actions"><button className="primary" onClick={() => onStudy(installedDeckId)}>Попробовать первые 10</button><button onClick={onClose}>К колодам</button></div>
    </section>;
  }

  return <section className="share-screen">
    <h1>{preview.title}</h1>
    <p className="hint">{preview.cards_count} карточек · от {preview.author}</p>
    {preview.description && <p>{preview.description}</p>}
    {preview.own ? <><p className="hint">Это ваша колода</p><button onClick={onClose}>К колодам</button></> : preview.installed ? <div className="share-actions"><button disabled>Уже установлена</button><button onClick={onClose}>К колодам</button></div> : <div className="share-actions"><button className="primary" disabled={installing} onClick={install}>Установить</button><button onClick={onClose}>К колодам</button></div>}
  </section>;
}
