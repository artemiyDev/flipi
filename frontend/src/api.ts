export interface Deck {
  id: number;
  name: string;
  new_count: number;
  learning_count: number;
  review_count: number;
}

export interface Progress {
  new: number;
  learning: number;
  review: number;
}

export interface Media {
  id: number;
  name: string;
  content_type: string | null;
}

export interface StudyCard {
  card_id: number;
  deck_id: number;
  deck_name: string;
  progress: Progress;
  question_html: string;
  answer_html: string;
  media: Media[];
  intervals: Record<"again" | "hard" | "good" | "easy", string>;
}

export interface StudyDone {
  card_id: null;
  done_today: number;
}

export type NextCard = StudyCard | StudyDone;

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(`API request failed with status ${status}`);
  }
}

function telegramInitData(): string {
  return window.Telegram?.WebApp.initData ?? "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-Telegram-Init-Data", telegramInitData());
  if (init?.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`/api${path}`, {...init, headers});
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response.json() as Promise<T>;
}

export function fetchDecks(): Promise<Deck[]> {
  return request<Deck[]>("/decks");
}

export function fetchNextCard(deckId: number | "all"): Promise<NextCard> {
  return request<NextCard>(`/study/next?deck_id=${deckId}`);
}

export function submitAnswer(
  cardId: number,
  rating: 1 | 2 | 3 | 4,
  elapsedMs: number,
): Promise<{ok: true; state: string; due: string}> {
  return request("/study/answer", {
    method: "POST",
    body: JSON.stringify({card_id: cardId, rating, elapsed_ms: elapsedMs}),
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
