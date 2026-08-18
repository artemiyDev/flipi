import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {ShareInstallScreen} from "./Share";

const apiMocks = vi.hoisted(() => ({
  fetchSharedDeck: vi.fn(),
  installSharedDeck: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  ...apiMocks,
}));

const preview = {
  title: "Spanish basics",
  description: "First words",
  cards_count: 12,
  author: "Анна",
  installed: false,
  own: false,
};

describe("shared deck installation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchSharedDeck.mockResolvedValue(preview);
    apiMocks.installSharedDeck.mockResolvedValue({deck_id: 42, added: 12});
  });

  it("renders the shared deck preview", async () => {
    render(<ShareInstallScreen onClose={vi.fn()} onStudy={vi.fn()} onUnauthorized={vi.fn()} token="share-token" />);

    expect(await screen.findByRole("heading", {name: "Spanish basics"})).toBeInTheDocument();
    expect(screen.getByText("12 карточек · от Анна")).toBeInTheDocument();
    expect(screen.getByText("First words")).toBeInTheDocument();
  });

  it("shows own, installed and expired states", async () => {
    apiMocks.fetchSharedDeck.mockResolvedValueOnce({...preview, own: true});
    const {unmount} = render(<ShareInstallScreen onClose={vi.fn()} onStudy={vi.fn()} onUnauthorized={vi.fn()} token="own" />);
    expect(await screen.findByText("Это ваша колода")).toBeInTheDocument();
    unmount();

    apiMocks.fetchSharedDeck.mockResolvedValueOnce({...preview, installed: true});
    render(<ShareInstallScreen onClose={vi.fn()} onStudy={vi.fn()} onUnauthorized={vi.fn()} token="installed" />);
    expect(await screen.findByRole("button", {name: "Уже установлена"})).toBeDisabled();
  });

  it("shows an expired-link message for a missing token", async () => {
    apiMocks.fetchSharedDeck.mockRejectedValueOnce(new ApiError(404));
    render(<ShareInstallScreen onClose={vi.fn()} onStudy={vi.fn()} onUnauthorized={vi.fn()} token="missing" />);

    expect(await screen.findByText("Ссылка устарела")).toBeInTheDocument();
  });

  it("installs and opens a study session for the new deck", async () => {
    const onStudy = vi.fn();
    render(<ShareInstallScreen onClose={vi.fn()} onStudy={onStudy} onUnauthorized={vi.fn()} token="share-token" />);
    fireEvent.click(await screen.findByRole("button", {name: "Установить"}));

    await waitFor(() => expect(apiMocks.installSharedDeck).toHaveBeenCalledWith("share-token"));
    fireEvent.click(await screen.findByRole("button", {name: "Попробовать первые 10"}));
    expect(onStudy).toHaveBeenCalledWith(42);
  });

  it("reloads the preview after an install conflict", async () => {
    apiMocks.installSharedDeck.mockRejectedValueOnce(new ApiError(409));
    apiMocks.fetchSharedDeck.mockResolvedValueOnce(preview).mockResolvedValueOnce({...preview, installed: true});
    render(<ShareInstallScreen onClose={vi.fn()} onStudy={vi.fn()} onUnauthorized={vi.fn()} token="share-token" />);
    fireEvent.click(await screen.findByRole("button", {name: "Установить"}));

    expect(await screen.findByRole("button", {name: "Уже установлена"})).toBeDisabled();
    expect(apiMocks.fetchSharedDeck).toHaveBeenCalledTimes(2);
  });
});
