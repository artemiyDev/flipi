export interface Deck {
  id: number;
  name: string;
  new_count: number;
  learning_count: number;
  review_count: number;
}

export interface DeckSettings {
  new_cards_per_day: number;
  reviews_per_day: number;
  desired_retention: number;
  learning_steps_minutes: number[];
  relearning_steps_minutes: number[];
  maximum_interval_days: number;
  bury_siblings: boolean;
  enable_fuzzing: boolean;
  option_preset: string;
}

export interface DeckDetail {
  id: number;
  name: string;
  description: string | null;
  is_archived: boolean;
  fsrs_optimized_at: string | null;
  review_count: number;
  settings: DeckSettings;
  counts: Progress;
}

export interface ArchivedDeck {
  id: number;
  name: string;
}

export type DeckSettingsPatch = Partial<Omit<DeckSettings, "option_preset">>;

export interface Progress {
  new: number;
  learning: number;
  review: number;
}

export interface DailyGoals {
  streak: {
    done: number;
    target: number;
    achieved: boolean;
  };
  full: {
    remaining: number;
    achieved: boolean;
  };
}

export interface LeechAlert {
  review_lapses: number;
  auto_suspended: true;
}

export interface StudyAnswer {
  ok: true;
  state: string;
  due: string;
  replayed: boolean;
  leech: LeechAlert | null;
}

export interface Media {
  id: number;
  name: string;
  content_type: string | null;
}

export interface StatsOverview {
  due_now: number;
  done_today: number;
  streak_days: number;
  retention_30d: number | null;
  ratings_30d: Record<"again" | "hard" | "good" | "easy", number>;
}

export interface StatsDay {
  date: string;
  count: number;
}

export interface StatsHeatmap {
  days: StatsDay[];
}

export interface StatsForecast {
  overdue: number;
  days: StatsDay[];
}

export interface StudyCard {
  card_id: number;
  deck_id: number;
  deck_name: string;
  learn_ahead: {
    scheduled_for: string;
    seconds_early: number;
  } | null;
  progress: Progress;
  goals: DailyGoals;
  question_html: string;
  answer_html: string;
  card_css: string | null;
  media: Media[];
  intervals: Record<"again" | "hard" | "good" | "easy", string>;
}

export interface StudyDone {
  card_id: null;
  done_today: number;
  goals: DailyGoals;
}

export interface CardSearchItem {
  card_id: number;
  note_id: number;
  deck_id: number;
  deck_name: string;
  preview: string;
  state: string;
  due: string;
  suspended: boolean;
  buried: boolean;
  flag: CardFlag | null;
  is_leech: boolean;
  review_lapses: number;
}

export interface CardSearchPage {
  total: number;
  items: CardSearchItem[];
}

export type CardFlag = "red" | "orange" | "green" | "blue" | "purple";

export interface CardDetail {
  card_id: number;
  note_id: number;
  deck_id: number;
  deck_name: string;
  question_html: string;
  answer_html: string;
  card_css: string | null;
  media: Media[];
  fields: Record<string, string>;
  front: string;
  back: string;
  tags: string[];
  state: string;
  due: string;
  lapses: number;
  review_lapses: number;
  is_leech: boolean;
  suspended: boolean;
  buried_until: string | null;
  flag: CardFlag | null;
}

export interface ImportResult {
  added: number;
  updated: number;
  unchanged: number;
  decks_created: string[];
  media_saved: number;
}

export interface CatalogDeck {
  slug: string;
  title: string;
  description: string;
  language: string;
  tags: string[];
  notes_count: number;
  installed: boolean;
}

export interface SharedDeckPreview {
  title: string;
  description: string | null;
  cards_count: number;
  author: string;
  installed: boolean;
  own: boolean;
}

export type NextCard = StudyCard | StudyDone;

export class ApiError extends Error {
  constructor(public readonly status: number, public readonly detail?: string) {
    super(`API request failed with status ${status}`);
  }
}

function telegramInitData(): string {
  return window.Telegram?.WebApp.initData ?? "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-Telegram-Init-Data", telegramInitData());
  if (init?.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api${path}`, {...init, headers});
  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Error responses without a JSON body do not provide a user-facing detail.
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export function fetchDecks(): Promise<Deck[]> {
  return request<Deck[]>("/decks");
}

export function fetchCatalog(): Promise<CatalogDeck[]> {
  return request<CatalogDeck[]>("/catalog");
}

export function installCatalogDeck(slug: string): Promise<{deck_id: number; added: number}> {
  return request(`/catalog/${encodeURIComponent(slug)}/install`, {method: "POST", body: JSON.stringify({})});
}

export function importFile(file: File, deckId: number | "auto"): Promise<ImportResult> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("deck_id", String(deckId));
  return request<ImportResult>("/import", {method: "POST", body: formData});
}

export function createCard(payload: {deck_id: number; type?: "basic" | "cloze"; front: string; back: string; tags?: string[]; reverse: boolean}): Promise<{note_id: number; cards_created: number}> {
  return request("/cards", {method: "POST", body: JSON.stringify(payload)});
}

export function searchCards(query: string, limit: number, offset: number): Promise<CardSearchPage> {
  const params = new URLSearchParams({q: query, limit: String(limit), offset: String(offset)});
  return request(`/cards/search?${params}`);
}

export function fetchCard(id: number): Promise<CardDetail> {
  return request(`/cards/${id}`);
}

export function updateNote(id: number, payload: {front?: string; back?: string; tags?: string[]}): Promise<{ok: true}> {
  return request(`/notes/${id}`, {method: "PATCH", body: JSON.stringify(payload)});
}

export async function deleteNote(id: number): Promise<void> {
  const headers = new Headers({"X-Telegram-Init-Data": telegramInitData()});
  const response = await fetch(`/api/notes/${id}`, {method: "DELETE", headers});
  if (!response.ok) {
    throw new ApiError(response.status);
  }
}

export function setCardSuspended(id: number, value: boolean): Promise<{ok: true}> {
  return request(`/cards/${id}/suspend`, {method: "POST", body: JSON.stringify({value})});
}

export function resumeLeech(id: number, expectedReviewLapses: number): Promise<{ok: true}> {
  return request(`/cards/${id}/leech/resume`, {
    method: "POST",
    body: JSON.stringify({expected_review_lapses: expectedReviewLapses}),
  });
}

export function deferLeech(id: number, expectedReviewLapses: number): Promise<{ok: true}> {
  return request(`/cards/${id}/leech/later`, {
    method: "POST",
    body: JSON.stringify({expected_review_lapses: expectedReviewLapses}),
  });
}

export function buryCard(id: number): Promise<{ok: true}> {
  return request(`/cards/${id}/bury`, {method: "POST", body: JSON.stringify({})});
}

export function setCardFlag(id: number, color: CardFlag | null): Promise<{ok: true}> {
  return request(`/cards/${id}/flag`, {method: "POST", body: JSON.stringify({color})});
}

export function resetCard(id: number): Promise<{ok: true}> {
  return request(`/cards/${id}/reset`, {method: "POST", body: JSON.stringify({})});
}

export function setCardDue(id: number, date: string): Promise<{ok: true}> {
  return request(`/cards/${id}/due`, {method: "POST", body: JSON.stringify({date})});
}

export function createDeck(name: string, description?: string): Promise<DeckDetail> {
  return request("/decks", {method: "POST", body: JSON.stringify({name, ...(description ? {description} : {})})});
}

export function fetchDeck(id: number): Promise<DeckDetail> {
  return request(`/decks/${id}`);
}

export function optimizeDeck(id: number): Promise<{review_count: number; optimized_at: string}> {
  return request(`/decks/${id}/optimize`, {method: "POST", body: JSON.stringify({})});
}

export function renameDeck(id: number, name: string): Promise<DeckDetail> {
  return request(`/decks/${id}`, {method: "PATCH", body: JSON.stringify({name})});
}

export function fetchArchivedDecks(): Promise<ArchivedDeck[]> {
  return request("/decks/archived");
}

export function archiveDeck(id: number): Promise<DeckDetail> {
  return request(`/decks/${id}/archive`, {method: "POST", body: JSON.stringify({})});
}

export function restoreDeck(id: number): Promise<DeckDetail> {
  return request(`/decks/${id}/restore`, {method: "POST", body: JSON.stringify({})});
}

export function updateDeckSettings(id: number, settings: DeckSettingsPatch): Promise<DeckDetail> {
  return request(`/decks/${id}/settings`, {method: "PATCH", body: JSON.stringify(settings)});
}

export function applyDeckPreset(id: number, name: string): Promise<DeckDetail> {
  return request(`/decks/${id}/preset`, {method: "POST", body: JSON.stringify({name})});
}

export function shareDeck(id: number): Promise<{token: string; link: string | null}> {
  return request(`/decks/${id}/share`, {method: "POST", body: JSON.stringify({})});
}

export function fetchSharedDeck(token: string): Promise<SharedDeckPreview> {
  return request(`/share/${encodeURIComponent(token)}`);
}

export function installSharedDeck(token: string): Promise<{deck_id: number; added: number}> {
  return request(`/share/${encodeURIComponent(token)}/install`, {method: "POST", body: JSON.stringify({})});
}

export function fetchNextCard(deckId: number | "all"): Promise<NextCard> {
  return request<NextCard>(`/study/next?deck_id=${deckId}`);
}

export function fetchStatsOverview(): Promise<StatsOverview> {
  return request<StatsOverview>("/stats/overview");
}

export function fetchStatsHeatmap(): Promise<StatsHeatmap> {
  return request<StatsHeatmap>("/stats/heatmap?weeks=26");
}

export function fetchStatsForecast(): Promise<StatsForecast> {
  return request<StatsForecast>("/stats/forecast?days=30");
}

export function submitAnswer(
  cardId: number,
  rating: 1 | 2 | 3 | 4,
  elapsedMs: number,
  requestId: string,
): Promise<StudyAnswer> {
  return request("/study/answer", {
    method: "POST",
    body: JSON.stringify({card_id: cardId, rating, elapsed_ms: elapsedMs, request_id: requestId}),
  });
}

export async function fetchMedia(id: number): Promise<Blob> {
  const headers = new Headers({"X-Telegram-Init-Data": telegramInitData()});
  const response = await fetch(`/api/media/${id}`, {headers});
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response.blob();
}
