# Adam

Adam is a local-first, self-hosted research-paper reader with selection-aware AI.

The current vertical slice supports:

- persistent local PDF upload and library
- online paper discovery from **Find papers** or the search dialog: search arXiv and the web, review source/PDF links, choose a folder, and download into the local reader; source failures leave other results available, and identical PDFs reuse existing library copies
- optional folders with bulk paper moves, folder-scoped uploads and search, and grid/list views; removing a folder keeps its papers, notes, and chats
- in-browser PDF rendering and native text selection
- a floating reader dock with editable page and zoom controls, plus a matching editable chat text percentage
- Continue reading with browser-local saved page, zoom, and reading position
- full-window focus reading: click **Focus reading** or press `F` to hide the sidebar and anchored note cards while keeping note and chat-link icons available; use **Exit focus** or `Esc` to restore your workspace, zoom, and reading position
- local native-text extraction with PyMuPDF
- durable per-paper chat histories with create, switch, delete, and reload restoration
- automatic full-paper context for every question, with selected passages and images treated as explicit focus
- provider/model/system-prompt snapshots per conversation
- streamed responses from OpenCode Zen, using GPT-5.6 Terra by default
- optional web search through a local SearXNG container, with cited sources saved per chat and reused in follow-up questions
- provider-aware model and API-key settings for OpenCode Zen, OpenRouter, OpenAI, Anthropic, and Google, available from either screen with `Cmd/Ctrl + ,`
- Docker Compose deployment with host-mounted data

## Run with Docker

```sh
cp .env.example .env
mkdir -p secrets
# Put your OpenCode Zen key in secrets/opencode_api_key.
chmod 600 secrets/opencode_api_key
docker compose up --build
```

Open <http://localhost:3000>. PDFs and application state are stored under `./data`, outside the containers.
To run a separate copy from another checkout, give it a unique Compose project and unused ports, for example `ADAM_WEB_PORT=3001 ADAM_SEARCH_PORT=8889 docker compose -p adam-folders up --build -d`. Each checkout keeps its own `./data` and `./secrets` directories.
The Search web control is off for each new question. When enabled, Adam searches and cites useful sources in its answer. It can include up to 7,500 characters of search snippet and page text from each of five results. Earlier web sources remain available to follow-up questions in that chat. SearXNG needs no search API key; it is reachable only through Docker and localhost port 8888 for local development.
Settings changed in the app are stored in `./data/settings.json` and survive container rebuilds. Keys remain server-side and are never returned to the browser. An OpenCode Zen key entered in Settings takes precedence over the Docker secret; removing it falls back to the secret.
The Chat titles section in Settings selects a separate provider and model for automatic chat names and title regeneration. It defaults to OpenCode Zen with `gpt-6-luna`.

## Run for development

Backend, using this machine's standard Python environment:

```sh
source ~/Documents/Projects/mainenv/bin/activate
cd backend
pip install -r requirements.txt
uvicorn adam_server.main:app --reload
```

Frontend:

```sh
cd frontend
npm install
npm run dev
```

The frontend runs at <http://localhost:3000> and proxies `/api` requests to the development API at <http://localhost:8000>.
For local development, start the search service with `docker compose up -d search`; the backend uses <http://localhost:8888> by default. Paper discovery queries arXiv directly and uses this service for broader web results. No AI key is needed. Search runs only on submission; reviewing a source may load remote metadata/PDF links, and **Download & add to library** stores the PDF and extracts its text. Restricted or unavailable PDFs can still be uploaded manually. Imported papers retain their source URL, PDF URL, available identifiers, and import date. arXiv versions remain separate search results; repeat imports preserve the existing copy and its folder, notes, and chats.

## Data durability

Do not delete `./data` when upgrading. A normal update rebuilds the disposable containers while retaining this directory:

```sh
docker compose down
docker compose up --build -d
```

Stop the application before copying `./data` as a simple consistent backup.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design decisions and the incremental roadmap.

## Isolated UI review

The combined reviewed design runs separately from the original explorations:

```sh
source ~/Documents/Projects/mainenv/bin/activate
ADAM_DATA_DIR="$PWD/data/review-demo" ADAM_SEARCH_BASE_URL=http://127.0.0.1:1 PYTHONPATH="$PWD/backend" uvicorn adam_server.main:app --host 127.0.0.1 --port 8414
```

In another terminal, seed the local review backend with six clearly labeled synthetic PDFs and three folders (safe to rerun without duplicates):

```sh
source ~/Documents/Projects/mainenv/bin/activate
python scripts/seed-review-demo.py --api-base http://127.0.0.1:8414
cd frontend
ADAM_INTERNAL_API_BASE=http://127.0.0.1:8414 ADAM_INSPECTOR_PORT=9414 npm run dev -- --port 7414 --strictPort
```

Open <http://localhost:7414>. The original reference previews on 7411–7413 use other checkouts and are left running. No Docker changes are required. Demo chat needs a configured provider key for live AI; the PDF, folder, settings, selection, and saved-reading interactions work without one.

Click a page number or percentage to edit it; Enter applies a valid value, and Escape or leaving the field cancels the edit. Page numbers must be whole numbers within the paper. Paper zoom accepts 10–3200%; chat text accepts 100–200%. Invalid input stays in the field with a message. Escape clears bulk selection when no dialog or text entry is active. Continue reading appears after opening papers and stores progress in this browser; it resumes the page, position within the page, horizontal scroll, and zoom.

Checks: `npm run lint`, `npm run build`, and `npm run test:reader-controls` from `frontend/`. The existing global Cloudflare/browser type collisions remain in the default TypeScript configuration; `npx tsc --noEmit --types node --incremental false` checks the browser code without those conflicting ambient globals.

Paper search shortcuts: `Cmd/Ctrl + K` opens library search and switches between local and online search while the dialog is open. `Cmd/Ctrl + Shift + K` opens online paper discovery directly. Switching modes preserves the query and online results.
