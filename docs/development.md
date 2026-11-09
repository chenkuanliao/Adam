# Development

[← Back to README](../README.md)

Run the backend and frontend directly for development. You need Python 3.12, Node.js 22.13 or later, and Docker for the search service. [ARCHITECTURE.md](../ARCHITECTURE.md) describes the design.

## Run the app

Backend (FastAPI, port 8000):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
uvicorn adam_server.main:app --reload
```

Frontend (port 3000):

```sh
cd frontend
npm install
npm run dev
```

The frontend runs at <http://localhost:3000> and proxies `/api` requests to the development API at <http://localhost:8000>.

Start the search service with `docker compose up -d search`. The backend uses it at <http://localhost:8888> by default. Paper discovery queries arXiv directly and uses this service for broader web results.

When run outside Docker, the API process listens for the Sign in with ChatGPT callback on `127.0.0.1:1455` itself. See [ChatGPT subscription](chatgpt-subscription.md).

## Isolated review instance

A review instance uses its own data directory and ports, so it never touches your real library:

```sh
source .venv/bin/activate
ADAM_DATA_DIR="$PWD/data/review-demo" ADAM_SEARCH_BASE_URL=http://127.0.0.1:1 PYTHONPATH="$PWD/backend" uvicorn adam_server.main:app --host 127.0.0.1 --port 8414
```

In another terminal, seed it and start a frontend against it:

```sh
source .venv/bin/activate
python scripts/seed-review-demo.py --api-base http://127.0.0.1:8414
cd frontend
ADAM_INTERNAL_API_BASE=http://127.0.0.1:8414 ADAM_INSPECTOR_PORT=9414 npm run dev -- --port 7414 --strictPort
```

Open <http://localhost:7414>. No Docker changes are required. Chat needs a configured provider key for live AI; the PDF, folder, settings, selection, and saved-reading interactions work without one.

## Demo data

All demo data is clearly labelled as synthetic. None of these scripts run automatically.

| Script | What it adds |
| --- | --- |
| `scripts/seed-review-demo.py` | Six synthetic PDFs in three folders, through the API. Safe to rerun without duplicates. |
| `scripts/seed-search-demo.py` | A synthetic paper with saved chats, for exercising chat search. |
| `scripts/seed-usage-demo.py` | Synthetic usage records for the Usage & cost page. `--clear` removes them. Run it inside the API container; the script header shows how. |

## Checks

Backend:

```sh
cd backend
python -m pytest -q
```

Frontend, from `frontend/`:

```sh
npm run lint
npm run build
npm run test:search
npm run test:reader-controls
npm run test:paper-notes
npm run test:usage
```

The existing global Cloudflare/browser type collisions remain in the default TypeScript configuration. `npx tsc --noEmit --types node --incremental false` checks the browser code without those conflicting ambient globals.

The paper-note browser tests target an isolated review server seeded with `scripts/seed-review-demo.py` and use an installed Google Chrome:

```sh
cd frontend
ADAM_TEST_BASE_URL=http://localhost:7426 npm run test:paper-notes:browser
```

They rewrite the synthetic **Sparse Attention for Long-Context Retrieval** and **Reliable Tool Use in Research Assistants** notes. Use a demo instance, not a library with personal notes.

## README media

The screenshots and GIFs in `docs/images/` were captured from a demo instance seeded with the synthetic papers above, at 1440×900. The chat answer shown is scripted for the demo.
