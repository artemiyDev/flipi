import {useEffect, useRef, useState} from "react";

import {ApiError, fetchDecks, importFile, type Deck, type ImportResult} from "./api";

const MAX_FILE_SIZE = 20 * 1024 * 1024;

function displaySize(size: number): string {
  return `${(size / 1024 / 1024).toFixed(size >= 1024 * 1024 ? 1 : 2)} МБ`;
}

function messageForError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail) {
      return error.detail;
    }
    if (error.status === 413) {
      return "Файл слишком большой. Текущий лимит импорта: 20 МБ.";
    }
    if (error.status === 404) {
      return "Колода не найдена.";
    }
    if (error.status === 422) {
      return "Не удалось обработать файл.";
    }
  }
  return "Не удалось импортировать файл.";
}

function isApkg(file: File | null): boolean {
  return file?.name.toLowerCase().endsWith(".apkg") ?? false;
}

export function ImportScreen({initialDeckId, onClose, onUnauthorized}: {
  initialDeckId: number;
  onClose: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [deckId, setDeckId] = useState<string>(initialDeckId > 0 ? String(initialDeckId) : "");
  const [auto, setAuto] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchDecks().then((loadedDecks) => {
      setDecks(loadedDecks);
      setDeckId((selectedDeckId) => selectedDeckId || (loadedDecks.length > 0 ? String(loadedDecks[0].id) : ""));
    }).catch((requestError: unknown) => {
      if (requestError instanceof ApiError && requestError.status === 401) {
        onUnauthorized();
      } else {
        setError("Не удалось загрузить колоды.");
      }
    });
  }, [onUnauthorized]);

  const chooseFile = (selected: File | null) => {
    setFile(selected);
    setResult(null);
    setError(null);
    if (selected && selected.size > MAX_FILE_SIZE) {
      setError("Файл слишком большой. Текущий лимит импорта: 20 МБ.");
    }
    if (selected && !isApkg(selected)) {
      setAuto(false);
    }
  };

  const submit = () => {
    if (!file) {
      setError("Выберите файл для импорта.");
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      setError("Файл слишком большой. Текущий лимит импорта: 20 МБ.");
      return;
    }
    if ((!isApkg(file) || !auto) && !deckId) {
      setError("Выберите колоду.");
      return;
    }
    setError(null);
    setLoading(true);
    importFile(file, isApkg(file) && auto ? "auto" : Number(deckId)).then(setResult).catch((requestError: unknown) => {
      if (requestError instanceof ApiError && requestError.status === 401) {
        onUnauthorized();
      } else {
        setError(messageForError(requestError));
      }
    }).finally(() => setLoading(false));
  };

  const anotherFile = () => {
    setFile(null);
    setResult(null);
    setError(null);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  };

  if (decks === null) {
    return <p className="hint centered">Загрузка…</p>;
  }

  return <section className="import-screen">
    <header className="import-header"><button className="close" aria-label="К колодам" onClick={onClose}>×</button><h1>Импорт</h1></header>
    {result ? <section className="import-result">
      <h2>Импорт завершён</h2>
      <p>Добавлено {result.added} · Обновлено {result.updated} · Без изменений {result.unchanged}</p>
      {result.decks_created.length > 0 && <div><strong>Созданные колоды</strong><ul>{result.decks_created.map((name) => <li key={name}>{name}</li>)}</ul></div>}
      <p>Медиа: {result.media_saved}</p>
      <div className="form-actions"><button className="primary" onClick={anotherFile}>Ещё файл</button><button onClick={onClose}>К колодам</button></div>
    </section> : <div className="import-form">
      <label className="file-picker"><input accept=".apkg,.csv,.tsv,.txt" aria-label="Файл импорта" ref={inputRef} type="file" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} /><span>Выбрать файл</span></label>
      {file && <p className="file-details">{file.name} · {displaySize(file.size)}</p>}
      {isApkg(file) && <div className="import-mode">
        <label><input checked={auto} name="import-mode" type="radio" onChange={() => setAuto(true)} />Колоды из файла (auto)</label>
        <label><input checked={!auto} name="import-mode" type="radio" onChange={() => setAuto(false)} />В конкретную колоду</label>
      </div>}
      {(!isApkg(file) || !auto) && <label>Колода<select aria-label="Колода для импорта" value={deckId} onChange={(event) => setDeckId(event.target.value)}><option value="">Выберите колоду</option>{decks.map((deck) => <option key={deck.id} value={deck.id}>{deck.name}</option>)}</select></label>}
      {error && <p className="field-error" role="alert">{error}</p>}
      <button className="primary" disabled={loading || !file || file.size > MAX_FILE_SIZE} onClick={submit}>{loading ? "Импортирую…" : "Импортировать"}</button>
    </div>}
  </section>;
}
