import {useEffect, useState} from "react";

import {
  ApiError,
  fetchStatsForecast,
  fetchStatsHeatmap,
  fetchStatsOverview,
  type StatsDay,
  type StatsForecast,
  type StatsHeatmap,
  type StatsOverview,
} from "./api";

const HEATMAP_WEEKS = 26;
const FORECAST_DAYS = 30;

export interface HeatmapCell {
  date: string;
  count: number;
  column: number;
  row: number;
}

interface LoadState<T> {
  data: T | null;
  failed: boolean;
}

function localDateKey(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function addDays(date: string, amount: number): string {
  const parsed = new Date(`${date}T00:00:00Z`);
  parsed.setUTCDate(parsed.getUTCDate() + amount);
  return parsed.toISOString().slice(0, 10);
}

function mondayOfWeek(date: string): string {
  const weekday = new Date(`${date}T00:00:00Z`).getUTCDay();
  return addDays(date, -((weekday + 6) % 7));
}

export function buildHeatmapGrid(
  days: StatsDay[],
  today = localDateKey(new Date()),
  weeks = HEATMAP_WEEKS,
): HeatmapCell[] {
  const counts = new Map(days.map(({date, count}) => [date, count]));
  const firstDay = addDays(mondayOfWeek(today), -(weeks - 1) * 7);

  return Array.from({length: weeks * 7}, (_, index) => ({
    date: addDays(firstDay, index),
    count: counts.get(addDays(firstDay, index)) ?? 0,
    column: Math.floor(index / 7),
    row: index % 7,
  }));
}

export function heatmapQuartiles(days: StatsDay[]): [number, number, number] | null {
  const values = days.map(({count}) => count).filter((count) => count > 0).sort((left, right) => left - right);
  if (values.length === 0) {
    return null;
  }
  return [0.25, 0.5, 0.75].map((percentile) => values[Math.ceil(percentile * values.length) - 1]) as [number, number, number];
}

export function heatmapLevel(count: number, quartiles: [number, number, number] | null): number {
  if (count === 0 || quartiles === null) {
    return 0;
  }
  if (count <= quartiles[0]) {
    return 1;
  }
  if (count <= quartiles[1]) {
    return 2;
  }
  if (count <= quartiles[2]) {
    return 3;
  }
  return 4;
}

export function buildForecastSeries(
  days: StatsDay[],
  today = localDateKey(new Date()),
  length = FORECAST_DAYS,
): StatsDay[] {
  const counts = new Map(days.map(({date, count}) => [date, count]));
  return Array.from({length}, (_, index) => {
    const date = addDays(today, index);
    return {date, count: counts.get(date) ?? 0};
  });
}

function LoadMessage({failed}: {failed: boolean}): JSX.Element {
  return <p className="hint">{failed ? "Не удалось загрузить" : "Загрузка…"}</p>;
}

function Summary({overview, failed}: {overview: StatsOverview | null; failed: boolean}): JSX.Element {
  return <section className="stats-block" aria-labelledby="stats-summary">
    <h2 id="stats-summary">Сводка</h2>
    {overview === null ? <LoadMessage failed={failed} /> : <div className="stats-summary">
      <div><strong>{overview.due_now}</strong><span>к повторению</span></div>
      <div><strong>{overview.done_today}</strong><span>сегодня</span></div>
      <div><strong>{overview.streak_days}</strong><span>дней подряд</span></div>
    </div>}
  </section>;
}

function Heatmap({heatmap, failed}: {heatmap: StatsHeatmap | null; failed: boolean}): JSX.Element {
  const cells = heatmap === null ? [] : buildHeatmapGrid(heatmap.days);
  const quartiles = heatmap === null ? null : heatmapQuartiles(heatmap.days);

  return <section className="stats-block" aria-labelledby="stats-heatmap">
    <h2 id="stats-heatmap">Активность</h2>
    {heatmap === null ? <LoadMessage failed={failed} /> : <div className="heatmap-scroll">
      <div className="heatmap" role="img" aria-label="Активность за 26 недель">
        {cells.map((cell) => <span
          aria-label={`${cell.date}: ${cell.count}`}
          className={`heatmap-cell heatmap-level-${heatmapLevel(cell.count, quartiles)}`}
          key={cell.date}
          style={{gridColumn: cell.column + 1, gridRow: cell.row + 1}}
          title={`${cell.date}: ${cell.count}`}
        />)}
      </div>
    </div>}
  </section>;
}

function Forecast({forecast, failed}: {forecast: StatsForecast | null; failed: boolean}): JSX.Element {
  const series = forecast === null ? [] : buildForecastSeries(forecast.days);
  const values = forecast === null ? [] : [forecast.overdue, ...series.map(({count}) => count)];
  const maximum = Math.max(...values, 1);
  const barWidth = 300 / (FORECAST_DAYS + 1);
  const lastDate = series.at(-1)?.date.slice(8, 10);

  return <section className="stats-block" aria-labelledby="stats-forecast">
    <h2 id="stats-forecast">Прогноз</h2>
    {forecast === null ? <LoadMessage failed={failed} /> : <svg className="forecast" viewBox="0 0 300 120" role="img" aria-label="Прогноз повторений на 30 дней">
      {[forecast.overdue, ...series.map(({count}) => count)].map((count, index) => {
        const height = Math.round((count / maximum) * 82);
        return <rect
          className={index === 0 ? "forecast-overdue" : "forecast-future"}
          height={height}
          key={index}
          width={Math.max(barWidth - 2, 1)}
          x={index * barWidth + 1}
          y={96 - height}
        />;
      })}
      <text className="forecast-label" x={barWidth / 2} y="112" textAnchor="middle">долг</text>
      <text className="forecast-label" x={barWidth * 1.5} y="112" textAnchor="middle">{series[0].date.slice(8, 10)}</text>
      <text className="forecast-label" x={barWidth * (FORECAST_DAYS + 0.5)} y="112" textAnchor="middle">{lastDate}</text>
      <text className="forecast-maximum" x="298" y="12" textAnchor="end">{maximum}</text>
    </svg>}
  </section>;
}

function Quality({overview, failed}: {overview: StatsOverview | null; failed: boolean}): JSX.Element {
  if (overview === null) {
    return <section className="stats-block" aria-labelledby="stats-quality"><h2 id="stats-quality">Качество</h2><LoadMessage failed={failed} /></section>;
  }
  const ratings = [
    ["again", "Снова"], ["hard", "Трудно"], ["good", "Хорошо"], ["easy", "Легко"],
  ] as const;
  const total = Object.values(overview.ratings_30d).reduce((sum, count) => sum + count, 0);

  return <section className="stats-block" aria-labelledby="stats-quality">
    <h2 id="stats-quality">Качество</h2>
    {overview.retention_30d === null ? <p className="hint">пока нет данных</p> : <strong className="retention">{Math.round(overview.retention_30d * 100)} %</strong>}
    {overview.retention_30d !== null && total > 0 && <div className="rating-breakdown">
      {ratings.map(([key, label]) => <div className="rating-row" key={key}>
        <span>{label}</span><div className="rating-track"><i className={`rating-${key}`} style={{width: `${(overview.ratings_30d[key] / total) * 100}%`}} /></div><b>{overview.ratings_30d[key]}</b>
      </div>)}
    </div>}
  </section>;
}

export function Stats({onUnauthorized}: {onUnauthorized: () => void}): JSX.Element {
  const [overview, setOverview] = useState<LoadState<StatsOverview>>({data: null, failed: false});
  const [heatmap, setHeatmap] = useState<LoadState<StatsHeatmap>>({data: null, failed: false});
  const [forecast, setForecast] = useState<LoadState<StatsForecast>>({data: null, failed: false});

  useEffect(() => {
    let active = true;
    const load = <T,>(request: () => Promise<T>, setState: (state: LoadState<T>) => void) => {
      request().then((data) => {
        if (active) {
          setState({data, failed: false});
        }
      }).catch((error: unknown) => {
        if (!active) {
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          onUnauthorized();
          return;
        }
        setState({data: null, failed: true});
      });
    };

    load(fetchStatsOverview, setOverview);
    load(fetchStatsHeatmap, setHeatmap);
    load(fetchStatsForecast, setForecast);
    return () => {
      active = false;
    };
  }, [onUnauthorized]);

  return <div className="stats">
    <Summary overview={overview.data} failed={overview.failed} />
    <Heatmap heatmap={heatmap.data} failed={heatmap.failed} />
    <Forecast forecast={forecast.data} failed={forecast.failed} />
    <Quality overview={overview.data} failed={overview.failed} />
  </div>;
}
