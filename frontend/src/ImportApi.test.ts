import {beforeEach, describe, expect, it, vi} from "vitest";

import {importFile} from "./api";

describe("import API client", () => {
  beforeEach(() => {
    window.Telegram = {WebApp: {initData: "signed-data", ready: vi.fn(), expand: vi.fn(), themeParams: {}, onEvent: vi.fn(), colorScheme: "light"}};
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({added: 1, updated: 2, unchanged: 3, decks_created: [], media_saved: 4}), {status: 200}))));
  });

  it("sends the file and target deck as multipart data", async () => {
    const file = new File(["front,back"], "cards.csv", {type: "text/csv"});

    await importFile(file, 7);

    const [url, options] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/api/import");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options?.body as FormData;
    expect(body.get("file")).toBe(file);
    expect(body.get("deck_id")).toBe("7");
    expect(new Headers(options?.headers).get("X-Telegram-Init-Data")).toBe("signed-data");
    expect(new Headers(options?.headers).get("Content-Type")).toBeNull();
  });
});
