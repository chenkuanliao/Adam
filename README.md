# Adam

Adam is a local-first, self-hosted research-paper reader with selection-aware AI.

The current vertical slice supports:

- persistent local PDF upload and library
- online paper discovery from **Find papers** or the search dialog: search arXiv and the web, review source/PDF links, choose a folder, and download into the local reader; source failures leave other results available, and identical PDFs reuse existing library copies
- nested folders with drag-and-drop folder and paper moves, root color inheritance, 12 selectable icons, folder-scoped uploads and search, and grid/list views; removing a folder keeps its papers, notes, and chats
- in-browser PDF rendering and native text selection
- PDF find with `Cmd/Ctrl + F` and chat find with `Cmd/Ctrl + Shift + F`, highlighted matches, next/previous navigation, and `Escape` to close; the chat shortcut focuses history search when chat history is open
- chat history search across titles, questions, and answers for the current paper, with matching snippets and direct navigation into a matching chat
- a floating reader dock with editable page and zoom controls, plus a matching editable chat text percentage
- Continue reading with browser-local saved page, zoom, and reading position
- full-window focus reading: click **Focus reading** or press `F` to hide the sidebar and anchored note cards while keeping note and chat-link icons available; toggle **Notes** to show anchored note cards beside the PDF without leaving focus mode; use **Exit focus** or `Esc` to restore your workspace, zoom, and reading position
- local native-text extraction with PyMuPDF
- per-paper chat histories with temporary empty chats, autosaved unsent drafts, switch, delete, and reload restoration
- automatic full-paper context for every question, with selected passages and images treated as explicit focus
- provider/model/system-prompt snapshots per conversation
- streamed responses from OpenCode Zen, using GPT-5.6 Terra by default
- optional web search through a local SearXNG container, with cited sources saved per chat and reused in follow-up questions
- provider-aware model and API-key settings for OpenCode Zen, OpenRouter, OpenAI, Anthropic, and Google, available from either screen with `Cmd/Ctrl + ,`
- an optional **ChatGPT (subscription)** provider that uses your own ChatGPT Plus or Pro plan through Sign in with ChatGPT instead of an API key
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

## ChatGPT subscription (Sign in with ChatGPT)

The **ChatGPT (subscription)** provider lets the owner of this self-hosted install use their own ChatGPT plan for chat, Quick Ask, and chat titles, with no API key. It follows OpenAI's [Sign in with ChatGPT open-source token path](https://developers.openai.com/siwc/token-sharing-open-source).

- **Requirements:** a ChatGPT **Plus or Pro** plan whose workspace allows plan usage in other apps.
- **Personal use only.** OpenAI limits this path to open-source and personal local projects. Adam has one owner and no multi-user support. Do not expose Adam to other people while you are signed in.

### Setup

1. Start Adam. With Docker, `compose.yaml` publishes the sign-in callback on the host as `127.0.0.1:${ADAM_CHATGPT_CALLBACK_PORT:-1455}`. For local development, the API process listens on `127.0.0.1:1455` itself. Port 1455 must be free; if another app is using it, set `ADAM_CHATGPT_CALLBACK_PORT` to another port.
2. Open Settings (`Cmd/Ctrl + ,`) → **ChatGPT (subscription)** → **Sign in with ChatGPT**. A ChatGPT tab opens. Approve access, including plan usage.
3. ChatGPT redirects to `http://127.0.0.1:1455/auth/callback`. Adam finishes signing in and the tab says you can close it. Settings then shows your account and loads the models your plan can use (`GET /v1/models`, entries with `visibility == "list"`).
4. Pick a model and click **Apply model**. To use ChatGPT for chat titles too, choose it in **Chat titles**.

Credentials are stored in `./data/chatgpt_auth.json` with `0600` permissions and written atomically. They include the issued `oaiapp_…` client ID, a random host ID, account identity, the access, refresh, and ID tokens, and expiry times. Tokens are never logged or sent to the browser; the UI only sees status (signed-in email and expiry). Access tokens last about an hour and refresh automatically when needed. Each refresh replaces the 30-day refresh token, and refreshes are serialized. A background job refreshes at least once a day while Adam runs, so the session stays alive as long as Adam is running at least every few weeks. **Sign out** revokes the refresh token with OpenAI and deletes the local tokens.

### Using Adam from another device (Tailscale, LAN)

The callback address is `127.0.0.1`, which always means "this device". If your browser runs on another device, such as a MacBook reaching your PC over Tailscale, the final redirect fails on that device with a "can't connect" page. That failure is expected and the sign-in can still be finished:

1. In Settings → **ChatGPT (subscription)**, click **Sign in with ChatGPT** and approve access in the new tab.
2. When the tab shows the connection error, copy the **entire address** from its address bar. It starts with `http://127.0.0.1:1455/auth/callback?code=…&state=…`.
3. Back in Adam, open **Using Adam from another device?**, paste the URL into **Callback URL**, and click **Finish sign-in**.

The server checks the pasted `state` against the pending sign-in (one use, 15-minute lifetime) and completes the PKCE exchange itself. Paste the URL promptly; if it has expired, start the sign-in again.

### Preview limitations and errors

- Plan usage is a preview. Inference uses only `POST https://api.openai.com/v1/responses` with `store: false` and `stream: true`. Adam never sends parameters that plan usage rejects, including `temperature`, `top_p`, `max_output_tokens`, `previous_response_id`, and `user`, so answers use the model's default sampling.
- Audio and video input, hosted tools, and file uploads are not available on this path. Adam only sends text and selected-region images.
- **ChatGPT plan limit reached** means `subscription_sharing_usage_limit_exceeded` (HTTP 429). Adam pauses ChatGPT requests for a short while and links to [ChatGPT usage settings](https://chatgpt.com/settings/usage). OpenAI does not report a reset time, so Adam does not show one; Plus plans share a rolling limit across all apps, and an app-specific limit may also apply. Use **Resume now** in Settings to try again sooner.
- **Sign in again** means the session was revoked, expired, or disconnected from ChatGPT settings. Click **Sign in again with ChatGPT**. Adam reuses its saved app registration.
- If the account or workspace is not eligible for plan usage, or the plan scope was declined, Settings says so. Use another provider in that case.

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

Click a sidebar folder row to open it and toggle its subfolders. The chevron indicates whether the branch is expanded, and your choices are remembered in this browser. Create a subfolder from an open folder, or choose a parent in the New folder dialog. Drag folders onto other folders or onto All papers / Workspace to move them to the top level. Edit folder also lets you change its parent. Descendants inherit their root folder’s color; moving a branch to the top level automatically assigns a color. Removing a folder keeps its papers in Unfiled and promotes its subfolders to the top level.

Click a page number or percentage to edit it; Enter applies a valid value, and Escape or leaving the field cancels the edit. Page numbers must be whole numbers within the paper. Paper zoom accepts 10–3200%; chat text accepts 100–200%. Invalid input stays in the field with a message. Escape clears bulk selection when no dialog or text entry is active. Continue reading appears after opening papers and stores progress in this browser; it resumes the page, position within the page, horizontal scroll, and zoom.

Click in the PDF, Chat, or Notes pane (or focus its controls with the keyboard), then press **⌘ + / −** on Mac or **Ctrl + / −** elsewhere to zoom that pane. The unshifted **=** key also zooms in. These shortcuts work while writing notes or chat drafts, use each pane's existing zoom limits, and keep the browser's page zoom unchanged.

Checks: `npm run lint`, `npm run build`, and `npm run test:reader-controls` from `frontend/`. The existing global Cloudflare/browser type collisions remain in the default TypeScript configuration; `npx tsc --noEmit --types node --incremental false` checks the browser code without those conflicting ambient globals.

**Add from link:** Open search (or Find papers), choose **Add from link**, and paste a public HTTP/HTTPS PDF URL. **Check PDF link** downloads a temporary copy, validates the actual PDF and shows its title, page count, and size without changing your library. Choose a folder and **Download & add to library**, then **Open in reader**. URLs without a `.pdf` suffix work. Login pages, damaged/password-protected PDFs, private-network addresses, and oversized files are rejected. Saving downloads and validates the file again; duplicate copies retain their existing notes, chats, and folder.

Paper search shortcuts: `Cmd/Ctrl + K` opens library search and cycles through **My library → Find papers online → Add from link → My library** while the dialog is open. `Cmd/Ctrl + Shift + K` opens online paper discovery directly. Switching modes preserves the search query, PDF link, and online results.

New chats stay temporary until you type a draft or send a message. Repeated **New** clicks reuse an empty chat, and empty chats do not appear in history. Draft text autosaves locally on the server, with a browser recovery copy while saves are pending; it restores when you switch chats or reload. Clearing an unsent draft removes it from history. Opening a paper with saved chats shows its history first. You can write drafts before configuring an API key.

## Paper notes and source quotes

Open **Notes** in the right pane. Use **A− / percentage / A+** beside the tabs to zoom the full note from 50–200%, including headings and source quotes. Click the percentage to enter a size; Enter applies it and Escape cancels. Note zoom stays independent of PDF and chat zoom and is retained when switching panes during the session. Click in the note where you want a quote. Select text in the PDF and press **Q** (or **Quote in note**): the quote inserts at the retained note cursor, splitting a paragraph when needed. Source quotes are protected blocks with their page citation. Click **Show in PDF** on a source quote to scroll to the quoted passage and briefly highlight it before it fades. New quotes retain their exact position across saves, reloads, and copy/paste; older quotes locate their text on the cited page. Drag the grip to move a quote, use its up/down buttons, or cut/copy/paste it with the surrounding writing. **+ Above** and **+ Below** add writing space beside a quote; empty paragraphs are also kept at document edges. Undo/redo includes quote insertion and moves. Existing cited quotes become protected blocks when opened; ordinary blockquotes remain editable.

Editor regression tests: `npm run test:paper-notes` in `frontend/`. The browser test targets an isolated review server seeded with `scripts/seed-review-demo.py` and uses installed Google Chrome:

```sh
cd frontend
ADAM_TEST_BASE_URL=http://localhost:7426 npm run test:paper-notes:browser
```

The browser tests rewrite the synthetic **Sparse Attention for Long-Context Retrieval** and **Reliable Tool Use in Research Assistants** notes. Use a demo instance rather than a library with personal notes.
