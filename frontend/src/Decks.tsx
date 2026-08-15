import {useCallback, useEffect, useState} from "react";

import {
  ApiError,
  applyDeckPreset,
  archiveDeck,
  createDeck,
  fetchArchivedDecks,
  fetchDeck,
  fetchDecks,
  renameDeck,
  restoreDeck,
  updateDeckSettings,
  type ArchivedDeck,
  type Deck,
  type DeckDetail,
  type DeckSettings,
  type DeckSettingsPatch,
} from "./api";

type SettingField = keyof Omit<DeckSettings, "option_preset">;

interface SettingsFormValues {
  new_cards_per_day: string;
  reviews_per_day: string;
  desired_retention: string;
  learning_steps_minutes: string;
  relearning_steps_minutes: string;
  maximum_interval_days: string;
  bury_siblings: boolean;
  enable_fuzzing: boolean;
}

const PRESETS: Array<[string, string]> = [
  ["light", "Лёгкий"],
  ["balanced", "Сбалансированный"],
  ["intense", "Интенсивный"],
  ["exam", "Экзамен"],
];

export function parseLearningSteps(value: string): number[] | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  const parts = trimmed.split(/\s+/);
  if (parts.some((part) => !/^\d+$/.test(part))) {
    return null;
  }
  return parts.map(Number);
}

export function formatLearningSteps(steps: number[]): string {
  return steps.join(" ");
}

function ProgressCounts({counts}: {counts: DeckDetail["counts"] | {new: number; learning: number; review: number}}): JSX.Element {
  return <span className="counts">
    <span className="count-new">{counts.new}</span>
    <span className="count-learning">{counts.learning}</span>
    <span className="count-review">{counts.review}</span>
  </span>;
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function settingsFormValues(settings: DeckSettings): SettingsFormValues {
  return {
    new_cards_per_day: String(settings.new_cards_per_day),
    reviews_per_day: String(settings.reviews_per_day),
    desired_retention: String(settings.desired_retention),
    learning_steps_minutes: formatLearningSteps(settings.learning_steps_minutes),
    relearning_steps_minutes: formatLearningSteps(settings.relearning_steps_minutes),
    maximum_interval_days: String(settings.maximum_interval_days),
    bury_siblings: settings.bury_siblings,
    enable_fuzzing: settings.enable_fuzzing,
  };
}

function numberValue(value: string): number | null {
  if (!value.trim()) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function settingsPatch(settings: DeckSettings, values: SettingsFormValues): {
  patch: DeckSettingsPatch;
  errors: Partial<Record<SettingField, string>>;
} {
  const original = settingsFormValues(settings);
  const patch: DeckSettingsPatch = {};
  const errors: Partial<Record<SettingField, string>> = {};
  const numberFields: Array<keyof Pick<SettingsFormValues, "new_cards_per_day" | "reviews_per_day" | "desired_retention" | "maximum_interval_days">> = [
    "new_cards_per_day", "reviews_per_day", "desired_retention", "maximum_interval_days",
  ];

  for (const field of numberFields) {
    if (values[field] !== original[field]) {
      const value = numberValue(values[field]);
      if (value === null) {
        errors[field] = "Введите число";
      } else {
        patch[field] = value;
      }
    }
  }
  for (const field of ["learning_steps_minutes", "relearning_steps_minutes"] as const) {
    if (values[field] !== original[field]) {
      const value = parseLearningSteps(values[field]);
      if (value === null) {
        errors[field] = "Введите числа через пробел";
      } else {
        patch[field] = value;
      }
    }
  }
  for (const field of ["bury_siblings", "enable_fuzzing"] as const) {
    if (values[field] !== original[field]) {
      patch[field] = values[field];
    }
  }
  return {patch, errors};
}

function CreateDeckForm({onCreated, onClose, onUnauthorized}: {
  onCreated: () => void;
  onClose: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = () => {
    setNameError(null);
    setError(null);
    setSaving(true);
    createDeck(name, description).then(() => {
      onCreated();
      onClose();
    }).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else if (requestError instanceof ApiError && requestError.status === 409) {
        setNameError("Такая колода уже есть");
      } else if (requestError instanceof ApiError && requestError.status === 422) {
        setNameError("Недопустимое имя");
      } else {
        setError(new Error("Не удалось создать колоду."));
      }
    }).finally(() => setSaving(false));
  };

  return <form className="deck-form" onSubmit={(event) => { event.preventDefault(); submit(); }}>
    <label>Название<input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label>
    {nameError && <p className="field-error" role="alert">{nameError}</p>}
    <label>Описание <span className="hint">(необязательно)</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} /></label>
    {error && <p className="field-error" role="alert">{error.message}</p>}
    <div className="form-actions"><button className="primary" disabled={saving} type="submit">Создать</button><button type="button" onClick={onClose}>Отмена</button></div>
  </form>;
}

export function Decks({onOpenDeck, onUnauthorized, createRequest}: {
  onOpenDeck: (id: number) => void;
  onUnauthorized: () => void;
  createRequest: number;
}): JSX.Element {
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [archived, setArchived] = useState<ArchivedDeck[] | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const load = useCallback(() => {
    setError(null);
    Promise.all([fetchDecks(), fetchArchivedDecks()]).then(([active, archivedDecks]) => {
      setDecks(active);
      setArchived(archivedDecks);
    }).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError(new Error("Не удалось загрузить колоды."));
      }
    });
  }, [onUnauthorized]);

  useEffect(load, [load]);
  useEffect(() => {
    if (createRequest > 0) {
      setCreateOpen(true);
    }
  }, [createRequest]);

  const restore = (id: number) => {
    restoreDeck(id).then(load).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError(new Error("Не удалось восстановить колоду."));
      }
    });
  };

  if (error) {
    return <p className="hint centered">{error.message}</p>;
  }
  if (decks === null || archived === null) {
    return <p className="hint centered">Загрузка…</p>;
  }

  return <section className="decks-management">
    <button className="primary wide" onClick={() => setCreateOpen(true)}>Новая колода</button>
    {createOpen && <CreateDeckForm onClose={() => setCreateOpen(false)} onCreated={load} onUnauthorized={onUnauthorized} />}
    {decks.length === 0 ? <p className="hint">Пока нет колод</p> : <div className="deck-list">
      {decks.map((deck) => <button className="deck" key={deck.id} onClick={() => onOpenDeck(deck.id)}>
        <span>{deck.name}</span><ProgressCounts counts={{new: deck.new_count, learning: deck.learning_count, review: deck.review_count}} />
      </button>)}
    </div>}
    <section className="archive-section">
      <button className="archive-toggle" aria-expanded={archiveOpen} onClick={() => setArchiveOpen(!archiveOpen)}>Архив ({archived.length})</button>
      {archiveOpen && <div className="archive-list">
        {archived.length === 0 ? <p className="hint">Архив пуст</p> : archived.map((deck) => <div className="archive-deck" key={deck.id}>
          <span>{deck.name}</span><button onClick={() => restore(deck.id)}>Восстановить</button>
        </div>)}
      </div>}
    </section>
  </section>;
}

function SettingInput({label, field, values, setValues, errors, type = "number"}: {
  label: string;
  field: keyof Pick<SettingsFormValues, "new_cards_per_day" | "reviews_per_day" | "desired_retention" | "learning_steps_minutes" | "relearning_steps_minutes" | "maximum_interval_days">;
  values: SettingsFormValues;
  setValues: (values: SettingsFormValues) => void;
  errors: Partial<Record<SettingField, string>>;
  type?: "number" | "text";
}): JSX.Element {
  return <label className="settings-field">{label}<input type={type} value={values[field]} onChange={(event) => setValues({...values, [field]: event.target.value})} />
    {errors[field] && <span className="field-error" role="alert">{errors[field]}</span>}
  </label>;
}

export function DeckScreen({deckId, onBack, onUnauthorized}: {
  deckId: number;
  onBack: () => void;
  onUnauthorized: () => void;
}): JSX.Element {
  const [deck, setDeck] = useState<DeckDetail | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [values, setValues] = useState<SettingsFormValues | null>(null);
  const [settingErrors, setSettingErrors] = useState<Partial<Record<SettingField, string>>>({});
  const [error, setError] = useState<Error | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchDeck(deckId).then(setDeck).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError(new Error("Не удалось загрузить колоду."));
      }
    });
  }, [deckId, onUnauthorized]);

  useEffect(load, [load]);
  useEffect(() => {
    if (deck) {
      setName(deck.name);
      setValues(settingsFormValues(deck.settings));
      setSettingErrors({});
    }
  }, [deck]);

  const updateDeck = (updated: DeckDetail) => setDeck(updated);

  const rename = () => {
    if (!deck) {
      return;
    }
    setNameError(null);
    renameDeck(deck.id, name).then((updated) => {
      updateDeck(updated);
      setRenameOpen(false);
    }).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else if (requestError instanceof ApiError && (requestError.status === 409 || requestError.status === 422)) {
        setNameError(requestError.status === 409 ? "Такая колода уже есть" : "Недопустимое имя");
      } else {
        setError(new Error("Не удалось переименовать колоду."));
      }
    });
  };

  const applyPreset = (preset: string) => {
    if (!deck) {
      return;
    }
    applyDeckPreset(deck.id, preset).then(() => fetchDeck(deck.id)).then(updateDeck).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError(new Error("Не удалось применить пресет."));
      }
    });
  };

  const saveSettings = () => {
    if (!deck || !values) {
      return;
    }
    const {patch, errors} = settingsPatch(deck.settings, values);
    setSettingErrors(errors);
    if (Object.keys(errors).length > 0 || Object.keys(patch).length === 0) {
      return;
    }
    updateDeckSettings(deck.id, patch).then(updateDeck).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
        return;
      }
      if (requestError instanceof ApiError && requestError.status === 422) {
        const field = requestError.detail?.match(/Invalid deck setting: ([a-z_]+)/)?.[1] as SettingField | undefined;
        if (field) {
          setSettingErrors({[field]: "Недопустимое значение"});
          return;
        }
      }
      setError(new Error("Не удалось сохранить настройки."));
    });
  };

  const archive = () => {
    if (!deck) {
      return;
    }
    archiveDeck(deck.id).then(onBack).catch((requestError: unknown) => {
      if (isUnauthorized(requestError)) {
        onUnauthorized();
      } else {
        setError(new Error("Не удалось архивировать колоду."));
      }
    });
  };

  if (error) {
    return <p className="hint centered">{error.message}</p>;
  }
  if (!deck || !values) {
    return <p className="hint centered">Загрузка…</p>;
  }

  return <section className="deck-screen">
    <header className="deck-header"><button className="close" aria-label="К списку колод" onClick={onBack}>×</button><div>
      {renameOpen ? <form className="rename-form" onSubmit={(event) => { event.preventDefault(); rename(); }}><input aria-label="Название колоды" value={name} onChange={(event) => setName(event.target.value)} /><button className="primary" type="submit">Сохранить</button></form> : <h1>{deck.name}</h1>}
      {nameError && <p className="field-error" role="alert">{nameError}</p>}
    </div><ProgressCounts counts={deck.counts} /></header>
    {!renameOpen && <button onClick={() => setRenameOpen(true)}>Переименовать</button>}

    <section className="settings-block"><h2>Пресет</h2><div className="presets">
      {PRESETS.map(([key, label]) => <button className={deck.settings.option_preset === key ? "active" : ""} key={key} onClick={() => applyPreset(key)}>{label}</button>)}
    </div></section>

    <section className="settings-block"><h2>Настройки</h2>
      <div className="settings-grid">
        <SettingInput errors={settingErrors} field="new_cards_per_day" label="Новых карточек в день" setValues={setValues} values={values} />
        <SettingInput errors={settingErrors} field="reviews_per_day" label="Повторов в день" setValues={setValues} values={values} />
        <SettingInput errors={settingErrors} field="desired_retention" label="Желаемое удержание" setValues={setValues} values={values} />
        <SettingInput errors={settingErrors} field="learning_steps_minutes" label="Шаги обучения (минуты)" setValues={setValues} type="text" values={values} />
        <SettingInput errors={settingErrors} field="relearning_steps_minutes" label="Шаги переобучения (минуты)" setValues={setValues} type="text" values={values} />
        <SettingInput errors={settingErrors} field="maximum_interval_days" label="Максимальный интервал (дни)" setValues={setValues} values={values} />
      </div>
      <label className="toggle"><input checked={values.bury_siblings} type="checkbox" onChange={(event) => setValues({...values, bury_siblings: event.target.checked})} />Прятать парные карточки</label>
      <label className="toggle"><input checked={values.enable_fuzzing} type="checkbox" onChange={(event) => setValues({...values, enable_fuzzing: event.target.checked})} />Разброс интервалов</label>
      <button className="primary" onClick={saveSettings}>Сохранить</button>
    </section>
    <button className="archive-button" onClick={archive}>В архив</button>
  </section>;
}
