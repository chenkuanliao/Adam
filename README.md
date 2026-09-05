# Adam

Adam is a local-first, self-hosted research-paper reader with selection-aware AI.

The current vertical slice supports:

- persistent local PDF upload and library
- in-browser PDF rendering and native text selection
- local native-text extraction with PyMuPDF
- selected passage context in the chat sidebar
- streamed responses from OpenCode Zen, using Gemini 3.8 Flash by default
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

## Data durability

Do not delete `./data` when upgrading. A normal update rebuilds the disposable containers while retaining this directory:

```sh
docker compose down
docker compose up --build -d
```

Stop the application before copying `./data` as a simple consistent backup.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design decisions and the incremental roadmap.
