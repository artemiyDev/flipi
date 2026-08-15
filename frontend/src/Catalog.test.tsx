import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {Catalog} from "./Catalog";

const apiMocks = vi.hoisted(() => ({
  fetchCatalog: vi.fn(),
  installCatalogDeck: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

const catalogDeck = {
  slug: "spanish-basics",
  title: "Испанский: основы",
  description: "Базовые слова и фразы",
  language: "ru",
  tags: ["испанский", "начальный"],
  notes_count: 42,
  installed: false,
};

describe("catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchCatalog.mockResolvedValue([catalogDeck]);
    apiMocks.installCatalogDeck.mockResolvedValue({deck_id: 7, added: 42});
  });

  it("renders catalog entries and installs a deck", async () => {
    render(<Catalog onClose={vi.fn()} onUnauthorized={vi.fn()} />);

    expect(await screen.findByText("Испанский: основы")).toBeInTheDocument();
    expect(screen.getByText("Базовые слова и фразы")).toBeInTheDocument();
    expect(screen.getByText("42 карточек · ru")).toBeInTheDocument();
    expect(screen.getByText("испанский")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Добавить"}));

    await waitFor(() => expect(apiMocks.installCatalogDeck).toHaveBeenCalledWith("spanish-basics"));
    expect(await screen.findByText("Колода появилась в списке — можно учить")).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "Добавлено ✓"})).toBeDisabled();
  });

  it("renders an installed deck as added immediately", async () => {
    apiMocks.fetchCatalog.mockResolvedValue([{...catalogDeck, installed: true}]);
    render(<Catalog onClose={vi.fn()} onUnauthorized={vi.fn()} />);

    expect(await screen.findByRole("button", {name: "Добавлено ✓"})).toBeDisabled();
  });

  it("reloads the catalog when installation reports a conflict", async () => {
    apiMocks.installCatalogDeck.mockRejectedValueOnce(new ApiError(409));
    apiMocks.fetchCatalog.mockResolvedValueOnce([catalogDeck]).mockResolvedValueOnce([{...catalogDeck, installed: true}]);
    render(<Catalog onClose={vi.fn()} onUnauthorized={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", {name: "Добавить"}));
    await waitFor(() => expect(apiMocks.fetchCatalog).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("button", {name: "Добавлено ✓"})).toBeDisabled();
  });
});
