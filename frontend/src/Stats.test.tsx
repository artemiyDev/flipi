import {render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";

import {ApiError} from "./api";
import {
  buildForecastSeries,
  buildHeatmapGrid,
  heatmapLevel,
  heatmapQuartiles,
  Stats,
} from "./Stats";

const apiMocks = vi.hoisted(() => ({
  fetchStatsOverview: vi.fn(),
  fetchStatsHeatmap: vi.fn(),
  fetchStatsForecast: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  fetchStatsOverview: apiMocks.fetchStatsOverview,
  fetchStatsHeatmap: apiMocks.fetchStatsHeatmap,
  fetchStatsForecast: apiMocks.fetchStatsForecast,
}));

describe("statistics helpers", () => {
  it("places the current week in the last heatmap column", () => {
    const cells = buildHeatmapGrid([], "2026-08-12");

    expect(cells).toHaveLength(182);
    expect(cells.find((cell) => cell.date === "2026-08-12")).toMatchObject({column: 25, row: 2});
    expect(cells[0]).toMatchObject({date: "2026-02-16", column: 0, row: 0});
  });

  it("uses non-zero quartiles for heatmap intensity", () => {
    const quartiles = heatmapQuartiles([{date: "2026-08-01", count: 1}, {date: "2026-08-02", count: 2}, {date: "2026-08-03", count: 5}, {date: "2026-08-04", count: 9}]);

    expect(quartiles).toEqual([1, 2, 5]);
    expect(heatmapLevel(0, quartiles)).toBe(0);
    expect(heatmapLevel(9, quartiles)).toBe(4);
    expect(heatmapQuartiles([])).toBeNull();
  });

  it("restores missing forecast dates without mixing in overdue", () => {
    expect(buildForecastSeries([{date: "2026-08-13", count: 7}], "2026-08-12", 3)).toEqual([
      {date: "2026-08-12", count: 0},
      {date: "2026-08-13", count: 7},
      {date: "2026-08-14", count: 0},
    ]);
  });
});

describe("Stats", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchStatsOverview.mockResolvedValue({due_now: 3, done_today: 5, streak_days: 2, retention_30d: null, ratings_30d: {again: 1, hard: 2, good: 6, easy: 1}});
    apiMocks.fetchStatsHeatmap.mockResolvedValue({days: [{date: "2026-08-12", count: 2}]});
    apiMocks.fetchStatsForecast.mockResolvedValue({overdue: 1, days: [{date: "2026-08-12", count: 3}]});
  });

  it("renders all four blocks and the no-retention state", async () => {
    render(<Stats onUnauthorized={vi.fn()} />);

    expect(await screen.findByRole("heading", {name: "Сводка"})).toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Активность"})).toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Прогноз"})).toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Качество"})).toBeInTheDocument();
    expect(await screen.findByText("пока нет данных")).toBeInTheDocument();
  });

  it("keeps successful blocks visible when one request fails", async () => {
    apiMocks.fetchStatsHeatmap.mockRejectedValue(new Error("offline"));
    render(<Stats onUnauthorized={vi.fn()} />);

    expect(await screen.findByText("Не удалось загрузить")).toBeInTheDocument();
    expect(screen.getByText("дней подряд")).toBeInTheDocument();
    expect(screen.getByLabelText("Прогноз повторений на 30 дней")).toBeInTheDocument();
  });

  it("uses the common unauthorized handler for a 401 response", async () => {
    const onUnauthorized = vi.fn();
    apiMocks.fetchStatsForecast.mockRejectedValue(new ApiError(401));
    render(<Stats onUnauthorized={onUnauthorized} />);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledOnce());
  });
});
