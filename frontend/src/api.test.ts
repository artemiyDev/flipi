import {beforeEach, describe, expect, it, vi} from "vitest";

import {fetchDecks, fetchMedia, fetchNextCard, fetchSharedDeck, installSharedDeck, shareDeck, submitAnswer} from "./api";

describe("API client", () => {
  beforeEach(() => {
    window.Telegram = {WebApp: {initData: "signed-data", ready: vi.fn(), expand: vi.fn(), openTelegramLink: vi.fn(), themeParams: {}, onEvent: vi.fn(), colorScheme: "light"}};
    vi.stubGlobal("fetch", vi.fn((url: string) => Promise.resolve(
      url.startsWith("/api/media/")
        ? new Response(new Blob(["media"]), {status: 200})
        : new Response(JSON.stringify([]), {status: 200}),
    )));
  });

  it("sends initData with every API request", async () => {
    await fetchDecks();
    await fetchNextCard("all");
    await submitAnswer(7, 3, 1200, "7ac1a9ba-8571-4287-a600-8f535fad1e08");
    await fetchMedia(4);
    await shareDeck(7);
    await fetchSharedDeck("token");
    await installSharedDeck("token");

    for (const [, options] of vi.mocked(fetch).mock.calls) {
      expect(new Headers(options?.headers).get("X-Telegram-Init-Data")).toBe("signed-data");
    }
  });

  it("sends the answer request UUID in JSON", async () => {
    await submitAnswer(7, 3, 1200, "7ac1a9ba-8571-4287-a600-8f535fad1e08");

    const [, options] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(options?.body))).toEqual({
      card_id: 7,
      rating: 3,
      elapsed_ms: 1200,
      request_id: "7ac1a9ba-8571-4287-a600-8f535fad1e08",
    });
  });
});
