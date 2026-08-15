import {fireEvent, render, screen} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {App} from "./App";

const apiMocks = vi.hoisted(() => ({
  fetchArchivedDecks: vi.fn(),
  fetchCatalog: vi.fn(),
  fetchDecks: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

describe("app navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchDecks.mockResolvedValue([]);
    apiMocks.fetchArchivedDecks.mockResolvedValue([]);
    apiMocks.fetchCatalog.mockResolvedValue([]);
  });

  it("opens the catalog from the empty study state", async () => {
    render(<App />);

    fireEvent.click(await screen.findByRole("button", {name: "Из каталога"}));
    expect(await screen.findByRole("heading", {name: "Каталог"})).toBeInTheDocument();
  });

  it("opens the catalog from the empty decks state", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", {name: "Колоды"}));
    fireEvent.click(await screen.findByRole("button", {name: "Из каталога"}));
    expect(await screen.findByRole("heading", {name: "Каталог"})).toBeInTheDocument();
  });

  it("opens help with its first section expanded", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", {name: "Помощь"}));
    expect(screen.getAllByText("Быстрый старт")).toHaveLength(1);
    expect(screen.getByText("Flipi — карточки с интервальными повторениями. Вы отвечаете на вопрос, оцениваете, насколько легко вспомнили, — а приложение само решает, когда показать карточку снова.")).toBeInTheDocument();
    expect(screen.getByText("Быстрый старт").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("Резервная копия и восстановление — в боте: /backup и /restore")).toBeInTheDocument();
  });
});
