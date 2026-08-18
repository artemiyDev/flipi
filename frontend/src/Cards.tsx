import {useCallback, useEffect, useRef, useState} from "react";

import {
  ApiError,
  buryCard,
  createCard,
  deleteNote,
  fetchCard,
  fetchDecks,
  resetCard,
  searchCards,
  setCardDue,
  setCardFlag,
  setCardSuspended,
  updateNote,
  type CardDetail,
  type CardFlag,
  type CardSearchItem,
  type Deck,
} from "./api";
import {CardBody} from "./CardBody";

const PAGE_SIZE = 25;
const FLAGS: CardFlag[] = ["red", "orange", "green", "blue", "purple"];
const CLOZE_RE = /{{c([1-9]\d*)::(.+?)(?:::(.*?))?}}/gs;

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function requestError(setError: (error: Error) => void, onUnauthorized: () => void, message: string, error: unknown): void {
  if (isUnauthorized(error)) {
    onUnauthorized();
  } else {
    setError(new Error(message));
  }
}

function tagsFromInput(value: string): string[] {
  return value.split(/\s+/).filter(Boolean);
}

function clozeNumbers(text: string): number[] {
  return [...new Set([...text.matchAll(CLOZE_RE)].filter((match) => match[2].trim()).map((match) => Number(match[1])))].sort((left, right) => left - right);
}

function nextClozeNumber(text: string): number {
  const used = new Set(clozeNumbers(text));
  let number = 1;
  while (used.has(number)) {
    number += 1;
  }
  return number;
}

export function CardCreateScreen({deckId, onClose, onUnauthorized}: {
  deckId: number;
  onClose: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [selectedDeckId, setSelectedDeckId] = useState(String(deckId));
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [tags, setTags] = useState("");
  const [type, setType] = useState<"basic" | "cloze">("basic");
  const [reverse, setReverse] = useState(false);
  const [lastClozeNumber, setLastClozeNumber] = useState<number | null>(null);
  const [errors, setErrors] = useState<{front?: string; back?: string}>({});
  const [error, setError] = useState<Error | null>(null);
  const [saving, setSaving] = useState(false);
  const frontRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetchDecks().then(setDecks).catch((requestErrorValue: unknown) => {
      requestError(setError, onUnauthorized, "Не удалось загрузить колоды.", requestErrorValue);
    });
  }, [onUnauthorized]);

  const submit = (again: boolean) => {
    const nextErrors = type === "cloze"
      ? (clozeNumbers(front).length ? {} : {front: "Добавьте хотя бы один пропуск"})
      : {
        ...(front.trim() ? {} : {front: "Введите лицевую сторону"}),
        ...(back.trim() ? {} : {back: "Введите обратную сторону"}),
      };
    setErrors(nextErrors);
    setError(null);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }
    setSaving(true);
    createCard({deck_id: Number(selectedDeckId), ...(type === "cloze" ? {type} : {}), front, back, tags: tagsFromInput(tags), reverse: type === "basic" && reverse}).then(() => {
      if (again) {
        setFront("");
        setBack("");
        setLastClozeNumber(null);
      } else {
        onClose();
      }
    }).catch((requestErrorValue: unknown) => {
      if (requestErrorValue instanceof ApiError && requestErrorValue.status === 422) {
        setErrors(type === "cloze" ? {front: "Добавьте хотя бы один пропуск"} : {front: "Проверьте содержимое карточки", back: "Проверьте содержимое карточки"});
      } else {
        requestError(setError, onUnauthorized, "Не удалось добавить карточку.", requestErrorValue);
      }
    }).finally(() => setSaving(false));
  };

  const wrapSelection = (sameCloze: boolean) => {
    const input = frontRef.current;
    if (!input || input.selectionStart === input.selectionEnd) {
      return;
    }
    const clozeNumber = sameCloze && lastClozeNumber !== null ? lastClozeNumber : nextClozeNumber(front);
    const selected = front.slice(input.selectionStart, input.selectionEnd);
    const nextFront = `${front.slice(0, input.selectionStart)}{{c${clozeNumber}::${selected}}}${front.slice(input.selectionEnd)}`;
    const selectionEnd = input.selectionStart + `{{c${clozeNumber}::${selected}}}`.length;
    setFront(nextFront);
    setLastClozeNumber(clozeNumber);
    setErrors((current) => ({...current, front: undefined}));
    window.requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(selectionEnd, selectionEnd);
    });
  };

  if (error) {
    return <p className="hint centered">{error.message}</p>;
  }
  if (!decks) {
    return <p className="hint centered">Загрузка…</p>;
  }

  return <section className="cards-screen"><header className="cards-header"><button className="close" aria-label="К колоде" onClick={onClose}>×</button><h1>Добавить карточку</h1></header>
    <form className="card-form" onSubmit={(event) => { event.preventDefault(); submit(false); }}>
      <label>Колода<select aria-label="Колода" value={selectedDeckId} onChange={(event) => setSelectedDeckId(event.target.value)}>{decks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name}</option>)}</select></label>
      <div className="toggle" role="group" aria-label="Тип карточки"><button type="button" className={type === "basic" ? "primary" : ""} onClick={() => setType("basic")}>Обычная</button><button type="button" className={type === "cloze" ? "primary" : ""} onClick={() => setType("cloze")}>Пропуски (cloze)</button></div>
      <label>{type === "cloze" ? "Текст" : "Лицевая сторона"}<textarea ref={frontRef} value={front} onChange={(event) => setFront(event.target.value)} /></label>
      {errors.front && <p className="field-error" role="alert">{errors.front}</p>}
      {type === "cloze" && <div className="form-actions"><button type="button" onClick={() => wrapSelection(false)}>Скрыть выделенное</button><button type="button" disabled={lastClozeNumber === null} onClick={() => wrapSelection(true)}>Тот же пропуск</button><span className="hint">Карточек будет: {clozeNumbers(front).length}</span></div>}
      <label>{type === "cloze" ? "Дополнение" : "Обратная сторона"}<textarea value={back} onChange={(event) => setBack(event.target.value)} /></label>
      {errors.back && <p className="field-error" role="alert">{errors.back}</p>}
      <label>Теги <span className="hint">(через пробел)</span><input value={tags} onChange={(event) => setTags(event.target.value)} /></label>
      {type === "basic" && <label className="toggle"><input checked={reverse} type="checkbox" onChange={(event) => setReverse(event.target.checked)} />Обратная карточка</label>}
      <div className="form-actions"><button className="primary" disabled={saving} type="submit">Сохранить</button><button disabled={saving} type="button" onClick={() => submit(true)}>Сохранить и добавить ещё</button></div>
    </form>
  </section>;
}

function CardBadges({card}: {card: CardSearchItem}): JSX.Element {
  return <span className="card-badges"><span className={`card-state state-${card.state}`}>{card.state}</span>{card.suspended && <span className="card-badge">приостановлена</span>}{card.buried && <span className="card-badge">отложена</span>}{card.flag && <span className={`card-flag flag-${card.flag}`}>{card.flag}</span>}</span>;
}

export function CardsBrowser({initialQuery, onOpenCard, onClose, onUnauthorized}: {
  initialQuery: string;
  onOpenCard: (id: number) => void;
  onClose: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [query, setQuery] = useState(initialQuery);
  const [items, setItems] = useState<CardSearchItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const load = useCallback((offset: number, append: boolean) => {
    setLoading(true);
    setError(null);
    searchCards(query, PAGE_SIZE, offset).then((page) => {
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setTotal(page.total);
    }).catch((requestErrorValue: unknown) => {
      requestError(setError, onUnauthorized, "Не удалось загрузить карточки.", requestErrorValue);
    }).finally(() => setLoading(false));
  }, [onUnauthorized, query]);

  useEffect(() => {
    const timer = window.setTimeout(() => load(0, false), 300);
    return () => window.clearTimeout(timer);
  }, [load]);

  return <section className="cards-screen"><header className="cards-header"><button className="close" aria-label="Назад" onClick={onClose}>×</button><h1>Карточки</h1></header>
    <input aria-label="Поиск карточек" placeholder="tag:… state:new is:due flag:red deck:… текст" value={query} onChange={(event) => setQuery(event.target.value)} />
    {error ? <p className="hint centered">{error.message}</p> : <><p className="hint cards-count">{items.length} из {total}</p><div className="card-list">
      {items.map((card) => <button className="card-row" key={card.card_id} onClick={() => onOpenCard(card.card_id)}><span className="card-preview">{card.preview}</span><small>{card.deck_name}</small><CardBadges card={card} /></button>)}
    </div>{loading && <p className="hint">Загрузка…</p>}{items.length < total && !loading && <button className="wide" onClick={() => load(items.length, true)}>Показать ещё</button>}</>}
  </section>;
}

export function CardScreen({cardId, onBack, onDeleted, onUnauthorized}: {
  cardId: number;
  onBack: () => void;
  onDeleted: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [card, setCard] = useState<CardDetail | null>(null);
  const [front, setFront] = useState("");
  const [back, setBack] = useState("");
  const [tags, setTags] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setError(null);
    fetchCard(cardId).then((loaded) => {
      setCard(loaded);
      setFront(loaded.front);
      setBack(loaded.back);
      setTags(loaded.tags.join(" "));
    }).catch((requestErrorValue: unknown) => {
      requestError(setError, onUnauthorized, "Не удалось загрузить карточку.", requestErrorValue);
    });
  }, [cardId, onUnauthorized]);

  useEffect(load, [load]);

  const action = (operation: () => Promise<{ok: true}>, message: string) => {
    operation().then(load).catch((requestErrorValue: unknown) => requestError(setError, onUnauthorized, message, requestErrorValue));
  };

  const save = () => {
    if (!card) {
      return;
    }
    setSaving(true);
    updateNote(card.note_id, {front, back, tags: tagsFromInput(tags)}).then(load).catch((requestErrorValue: unknown) => {
      requestError(setError, onUnauthorized, "Не удалось сохранить заметку.", requestErrorValue);
    }).finally(() => setSaving(false));
  };

  const remove = () => {
    if (!card || !window.confirm("Удалить заметку и все её карточки?")) {
      return;
    }
    deleteNote(card.note_id).then(onDeleted).catch((requestErrorValue: unknown) => {
      requestError(setError, onUnauthorized, "Не удалось удалить заметку.", requestErrorValue);
    });
  };

  if (error) {
    return <p className="hint centered">{error.message}</p>;
  }
  if (!card) {
    return <p className="hint centered">Загрузка…</p>;
  }

  return <section className="cards-screen"><header className="cards-header"><button className="close" aria-label="К списку карточек" onClick={onBack}>×</button><div><h1>{card.deck_name}</h1><span className="hint">{card.state}</span></div></header>
    <section className="card-render"><CardBody questionHtml={card.question_html} answerHtml={card.answer_html} cardCss={card.card_css} media={card.media} /></section>
    <section className="card-form"><label>Лицевая сторона<textarea value={front} onChange={(event) => setFront(event.target.value)} /></label><label>Обратная сторона<textarea value={back} onChange={(event) => setBack(event.target.value)} /></label><label>Теги <span className="hint">(через пробел)</span><input value={tags} onChange={(event) => setTags(event.target.value)} /></label><button className="primary" disabled={saving} onClick={save}>Сохранить</button></section>
    <section className="card-actions"><button onClick={() => action(() => setCardSuspended(card.card_id, !card.suspended), "Не удалось изменить состояние карточки.")}>{card.suspended ? "Вернуть" : "Приостановить"}</button><button onClick={() => action(() => buryCard(card.card_id), "Не удалось отложить карточку.")}>Отложить до завтра</button><label>Задать дату<input aria-label="Дата повторения" type="date" onChange={(event) => event.target.value && action(() => setCardDue(card.card_id, event.target.value), "Не удалось задать дату.")} /></label><div className="flag-actions"><span>Флаг</span>{FLAGS.map((flag) => <button className={`flag-${flag}`} key={flag} onClick={() => action(() => setCardFlag(card.card_id, flag), "Не удалось изменить флаг.")}>{flag}</button>)}{card.flag && <button onClick={() => action(() => setCardFlag(card.card_id, null), "Не удалось изменить флаг.")}>Снять</button>}</div><button onClick={() => { if (window.confirm("Сбросить прогресс карточки?")) action(() => resetCard(card.card_id), "Не удалось сбросить прогресс."); }}>Сбросить прогресс</button><button className="delete-button" onClick={remove}>Удалить заметку</button></section>
  </section>;
}
