<p align="center">
  <img src="logo.png" alt="Flipi" width="200">
</p>

<h1 align="center">Flipi</h1>

<p align="center">Anki-style spaced repetition, inside Telegram.</p>

<p align="center">
  <a href="https://github.com/artemiyDev/flipi/actions/workflows/ci.yml"><img src="https://github.com/artemiyDev/flipi/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

Flipi keeps decks, cards and a review queue in a Telegram chat. Scheduling runs on
FSRS — the algorithm modern Anki uses — so intervals adapt to how well you actually
recall each card, and every review is written to a log you can export.

> The bot interface is in Russian. The codebase is in English.

## Studying

- FSRS scheduling with four grades: Again, Hard, Good, Easy.
- Study one deck or every deck at once.
- Custom study: review future cards ahead of time, or pull new cards past the
  daily limit.
- Filtered sessions built from a search query, with a preview count of matching
  and currently available cards before the session starts.
- After an answer, sibling cards of the same note are buried until tomorrow.
  Configurable per deck.

## Cards and decks

- Basic cards: front, back, tags, optional reverse card.
- Rename, archive and restore decks.
- Card actions: edit, delete note, suspend, bury, reset, flag, set due date.
- Flags in five colours: red, orange, green, blue, purple.
- Deck options: new-card limit, review limit, desired retention, bury siblings.
- Daily limits, burying and statistics all respect the user's timezone, which is
  set from the settings menu.

## Import and export

- CSV, TSV and TXT: UTF-8 and Windows-1251, up to 20 MB, with a fourth `reverse`
  column for reverse cards.
- APKG import: decks are created from the package structure, media files are
  stored in Postgres, and Anki field and card-template snapshots (`qfmt` / `afmt`)
  are kept and reused when a card is shown.
- Media references inside cards (`<img src=...>`, `[sound:...]`) survive the
  import and are sent to Telegram during a session.
- Template-generated sibling cards are grouped back into a single note.
- Duplicate front/back pairs within the same deck are skipped.
- CSV export per deck.
- `/backup` produces a full JSON dump of decks, cards, media, FSRS state and
  review logs; `/restore` loads it back, skipping notes that already exist.

## Search

A browser-style query language, usable both for search and for building a
filtered study session:

```
tag:physics
deck:"Organic Chemistry"
state:review
flag:red
is:due
is:suspended
is:buried
```

Plain text without a prefix searches card content.

## Statistics

Deck, note and card counts, due and suspended totals, and reviews and retention
over the last seven days.

## Running it

```bash
cp .env.example .env     # put your BOT_TOKEN in it
docker compose up --build
```

The bot container runs `alembic upgrade head` before starting polling, so the
schema is always current.

The Mini App API runs as the `api` service on `127.0.0.1:8094` by default.
Set `FLIPI_API_PORT` to use another local port. It validates Telegram WebApp
`initData` on every `/api` request.

### Frontend

For local Mini App development, run `npm run dev` in `frontend/`; Vite proxies
`/api` requests to `http://127.0.0.1:8094`. Run frontend tests with `npm test`.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | — | Telegram bot token. Required. |
| `DATABASE_URL` | `postgresql+asyncpg://ankibot:ankibot@db:5432/ankibot` | database connection |
| `LOG_LEVEL` | `INFO` | logging level |
| `AUTH_MAX_AGE_SECONDS` | `86400` | maximum age of Telegram WebApp initData in seconds |
| `WEB_APP_URL` | — | public HTTPS URL of the Telegram Mini App |
| `AUTO_CREATE_TABLES` | `false` | build tables from models instead of migrations — local experiments only, never in production |

### Commands

`/menu` `/cancel` `/status` `/backup` `/restore` `/help`

`/status` reports database availability and the current Alembic schema version.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The suite covers the FSRS scheduler, the browser query parser, deck options,
CSV and APKG importers, media handling, note editing, card templates and
timezone arithmetic.

## Operations

Alembic migrations for the production schema, a container healthcheck that
verifies Postgres, in-memory throttling on both messages and callback buttons,
and a global error handler that logs the failure and answers the user instead of
dropping the update.

## Stack

Python 3.12 · aiogram 3 · SQLAlchemy 2 (async) · PostgreSQL · Alembic · FSRS · Docker

## License

MIT
