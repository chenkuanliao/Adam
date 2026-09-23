# Adam

Adam is a local-first, self-hosted research-paper reader with selection-aware AI.

The current vertical slice supports:

- persistent local PDF upload and library
- in-browser PDF rendering and native text selection
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
The Search web control is off for each new question. When enabled, Adam searches and cites useful sources in its answer. It can include up to 7,500 characters of search snippet and page text from each of five results. Earlier web sources remain available to follow-up questions in that chat. SearXNG needs no search API key; it is reachable only through Docker and localhost port 8888 for local development.
Settings changed in the app are stored in `./data/settings.json` and survive container rebuilds. Keys remain server-side and are never returned to the browser. An OpenCode Zen key entered in Settings takes precedence over the Docker secret; removing it falls back to the secret.

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
For local development, start the search service with `docker compose up -d search`; the backend uses <http://localhost:8888> by default.

## Data durability

Do not delete `./data` when upgrading. A normal update rebuilds the disposable containers while retaining this directory:

```sh
docker compose down
docker compose up --build -d
```

Stop the application before copying `./data` as a simple consistent backup.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design decisions and the incremental roadmap.
