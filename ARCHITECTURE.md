# Architecture: Local-First AI Research-Paper Reader

Status: active implementation, 2026-09-07. Durable per-paper conversations, reload restoration, pinned model configuration, and automatic full-paper chat context are implemented. Structured section/chunk retrieval and paper notes remain next.

### Implemented baseline

- Browser library, PDF-first reader, native text selection, structured selection preview, and streamed chat UI.
- FastAPI upload/library/file/page-text/chat endpoints.
- Content-addressed PDF storage and PyMuPDF native page-text extraction.
- SQLite document/page/message schema with an initial Alembic migration.
- OpenCode Zen Gemini adapter that sends the question, selected text, and page—not the PDF. The initial default model is `gemini-3.8-flash`.
- Same-origin frontend API proxy and Docker Compose with `./data:/data` persistence.
- Automated smoke coverage for upload, deduplication, extraction, PDF serving, and missing-key behavior.

The Notes tab is visibly reserved but not active. Canonical highlight geometry, anchored-note cards, and the Tiptap paper note begin in Milestone 2.

## Product workspace and note interaction

The reader is one persistent workspace, not separate chat and notes pages. The PDF remains the dominant surface while the narrower right pane switches modes:

```text
┌───────────────────────────────┬─────────────────┐
│                               │  Chat | Notes   │
│          PDF reader           │                 │
│          (primary)            │ active mode     │
│                               │                 │
│       [floating note]         │                 │
└───────────────────────────────┴─────────────────┘
```

- Default desktop split: approximately 68% PDF / 32% side pane.
- The divider is resizable, but the PDF should retain at least 58–60% while both panes are visible.
- Switching `Chat` and `Paper note` keeps the side pane width stable so the PDF does not reflow.
- The chosen side-pane mode, divider position, PDF scroll location, and zoom are restored per document as local workspace preferences.
- On a narrow viewport, the side pane becomes a drawer/overlay; responsive behavior must not shrink the paper into an unusable column.

There are two deliberately different note concepts:

1. **Anchored note** — a small note attached to a highlight or page region. Its light-bulb marker lives at the PDF location. Opening it creates a draggable floating card above the reader workspace.
2. **Paper note** — exactly one long-form rich-text notebook per document, shown in the right-pane `Notes` mode.

Do not represent these as one polymorphic editor in the UI. They have different purposes, lifecycles, and interaction costs, even though both are stored locally and can later be searched or offered to the AI.

### Anchored-note behavior

- Highlighting or selecting a region offers `Highlight`, `Add note`, and `Ask AI`.
- An annotation with a note displays a small light-bulb marker in the page overlay or margin. Marker geometry is anchored in canonical PDF page space.
- Clicking the marker opens a floating note card rendered in a workspace-level portal, not inside the virtualized PDF page. The card therefore stays in place while the PDF scrolls.
- The card header shows page number, a jump-to-anchor action, minimize/close, and a drag handle. Its body is a simple autosaving text editor in the MVP.
- Card screen position and size are presentation state, not annotation geometry. Persist a normalized workspace position and clamp it to the current viewport after resize.
- Start with one expanded floating card at a time. Multiple simultaneous cards can be added later if real use shows that comparison outweighs clutter.
- Closing the card never deletes the anchored note. Deletion is a separate explicit action.

The anchor marker may scroll off screen while the floating card remains visible. The page label and jump action preserve orientation; a permanent connector line would add clutter and is not recommended initially.

### Paper-note editor

Use Tiptap (built on ProseMirror) with a deliberately small schema:

- paragraphs and headings
- bullet, numbered, and task lists with automatic indentation
- bold, italic, underline, strike, inline code, links, and highlights
- block quotes, code blocks, undo/redo
- Markdown-style input rules such as `# `, `## `, `- `, `1. `, `> `, and task syntax
- a small slash-command menu for block insertion

Typing `# ` at the start of a block should create a heading; a bare hashtag inside a sentence should remain ordinary text or later become a tag. This distinction avoids surprising formatting.

Store Tiptap/ProseMirror JSON as the canonical editable representation, plus derived plain text for search/AI context and optional Markdown for export. Avoid canonical HTML because sanitization, schema migration, and deterministic editing are harder. Autosave after a short debounce, show `Saving…` / `Saved`, and use an integer revision for optimistic concurrency even in the single-user version; multiple browser tabs can still overwrite one another.

The AI must not automatically receive the full paper note or every anchored note. The context builder may include explicitly referenced notes, short search-matched note excerpts, or a user-selected `Include my notes` scope. User-authored notes are labeled separately from paper evidence so the model cannot present them as claims made by the paper.

## 1. Product boundary and architectural shape

The product is a local-first PDF reading workspace with an AI context broker. Its distinctive capability is not document chat. It is the reliable conversion of a reader's immediate focus—selected text, a selected visual region, or the visible viewport—into structured context that can be combined with paper-wide retrieval.

Use a modular monolith:

```text
Browser SPA
  React + TypeScript + PDF.js
         |
         | HTTP + streamed responses
         v
Local application server
  FastAPI
  ├── document service
  ├── annotation service
  ├── context builder / retrieval
  ├── provider adapters
  └── one local ingestion worker
         |
         +-- SQLite: metadata, text, annotations, chat, jobs, FTS
         +-- local filesystem: original PDFs, derived crops, model cache
         +-- optional local models: embeddings, OCR, layout enrichment
         +-- remote LLM API: only the assembled question context
```

This should remain one deployable application. In development the SPA and API run as two processes; a production image can serve the built SPA through the FastAPI deployment or a small reverse proxy. Redis, Celery, Kubernetes, object storage, and a separate vector database are deliberately excluded.

### Primary technology decision

Use React + Vite rather than Next.js initially. This is a local authenticated-by-network-boundary SPA with no SEO or server-rendering requirement. Vite removes an unnecessary Node server from production. If remote access or server-rendered routes become real requirements, the React components and API contract can move to Next.js later.

Use `pdfjs-dist` directly, wrapping its display and text layers in our own page component. Avoid making a high-level viewer library foundational: selection geometry, viewport awareness, and custom annotation overlays are core product behavior and need direct control.

## 2. End-to-end data flows

### A. PDF upload and ingestion

1. The browser posts a PDF as multipart data.
2. The API streams it to a temporary file while computing SHA-256, validates the PDF signature and size limit, and atomically moves it to content-addressed local storage.
3. A `Document` and persistent `IngestionJob` are committed. The document is immediately openable; parsing status is visible in the UI.
4. PyMuPDF opens the PDF and records each page's CropBox/MediaBox, rotation, canonical dimensions, native text blocks, words, and word boxes.
5. A deterministic page-quality check calculates character count, printable-character ratio, replacement-character ratio, word count, text coverage, and image coverage. It marks only suspect pages for later OCR; it does not run OCR in the first slice.
6. Initial ingestion stores page text and positioned words. Later milestones add section detection, chunks, FTS rows, embeddings, and optional structure/OCR enrichment.
7. The worker updates progress after each page/stage. Failure records are retryable and never make the original PDF unavailable.

Deduplication is by file SHA-256, but importing the same bytes can either reopen the existing document or create a separate library entry later. The first version reuses the existing document.

### B. Selected text question

1. PDF.js renders a canvas and selectable text layer. A browser selection may yield several client rectangles and may cross pages.
2. For every affected page, the client converts selection rectangles from CSS/page coordinates through that page's PDF.js viewport into canonical normalized page rectangles.
3. The client sends the exact visible quote, page/rect selectors, and the question. It does not send a screenshot.
4. The backend spatially resolves the rectangles against its positioned words. It stores both the user's quote and stable word-span anchors; disagreement is retained as diagnostic metadata rather than silently changing the quote.
5. The context builder includes the exact quote first, its containing block/paragraph, modest neighboring text, section identity when known, and—after retrieval exists—relevant paper-wide chunks.
6. A provider adapter streams tokens/events. The server stores the user message, a reproducible context manifest, and the final assistant message. The UI renders the stream in the sidebar.

For the first vertical slice, steps 4–5 simply use the client quote plus page number. Spatial reconciliation and retrieval follow in later milestones.

### C. Equation, figure, or table region question

1. The user draws a rectangle over one page. The client stores/sends its canonical normalized bounding box.
2. The backend maps the box to page coordinates and renders only that region from the original PDF at approximately 150–220 DPI. Backend rendering is authoritative; a browser screenshot is not.
3. It gathers words/blocks intersecting or immediately surrounding the box, a nearby caption candidate, the current section, and optional retrieved chunks elsewhere in the paper.
4. The context builder sends the crop as an image part and text context as text parts only when the selected provider/model supports vision.
5. If it does not support vision, the UI gives a clear capability error or offers a vision model; OCR text may be a fallback but is not an equivalent interpretation of an equation or figure.
6. The saved message context references the region and a local derived crop. The whole PDF never leaves the machine.

### D. Global question without a selection

1. The message carries document ID and passive reading state (current page and visible normalized ranges), but explicitly declares that there is no selection.
2. The question drives hybrid paper-wide retrieval. Passive viewport text receives a mild boost and is included only when useful; it must not silently turn every global question into a local one.
3. The context builder includes compact document identity (title, authors, abstract when known), the best chunks, neighbor/parent context within budget, and page-aware source labels.
4. The answer is streamed with internal source references that the UI can turn into “jump to page/highlight” actions.

The UI should distinguish three scopes: **Selection**, **Here** (viewport), and **Paper**. Automatic inference can preselect `Here`, but the user always sees and can change the active scope. This prevents invisible context from making model behavior mysterious.

## 3. Subsystems and technology choices

| Subsystem | Initial choice | Why / boundary |
|---|---|---|
| Web UI | React, TypeScript, Vite | Simple local SPA; no SSR requirement. |
| PDF rendering | Mozilla `pdfjs-dist` | Browser-native rendering and text layer; direct viewport transforms and overlay ownership. |
| Client state | React state plus TanStack Query | Server data/cache only; add Zustand only if viewer interaction state becomes unwieldy. |
| API | FastAPI, Pydantic | Typed Python boundary, multipart upload, OpenAPI, straightforward streaming. |
| Streaming | `fetch()` response stream with SSE-formatted events | POST can carry structured context while the response streams; WebSocket is unnecessary. |
| Persistence | SQLite in WAL mode, SQLAlchemy 2, Alembic | One durable local database with migrations; easy backup. |
| Files | Content-addressed directories under app data | Do not put multi-megabyte PDFs and crops in SQLite. |
| Baseline extraction | PyMuPDF | Fast native text, words/blocks and geometry, page rendering/cropping. |
| Structure | Deterministic heading heuristics first; Docling as optional enrichment later | Docling adds valuable layout/table models but materially increases install, memory, and ingestion cost. |
| OCR | Page/region-gated PaddleOCR mobile pipeline, added late | Native text is preferred. OCR runs only for bad pages or requested regions and records provenance/confidence. |
| Lexical search | SQLite FTS5/BM25 | Excellent for symbols, terminology, names, and exact phrases; essentially free with SQLite. |
| Embeddings | Sentence Transformers/ONNX with BGE small | CPU-feasible local semantic retrieval. Store model/version with every embedding. |
| Vector search | Exact cosine over normalized vectors at first; optional `sqlite-vec` behind an interface | A paper has only hundreds of chunks. Exact scan is simple and fast; an extension is justified only for a larger library. |
| Provider access | Small internal interface plus provider-specific adapters | Keeps request shaping, images, streaming, usage, and error translation explicit. Avoid a large gateway dependency initially. |
| Background jobs | One database-backed worker process polling `ingestion_jobs` | Survives restarts without Redis. Do not rely on FastAPI `BackgroundTasks` for durable ingestion. |

PyMuPDF is the source of truth for backend geometry and extraction, while PDF.js is the browser renderer. Cross-engine reconciliation tests are therefore a first-class requirement.

## 4. Deterministic, local-ML, and remote-model boundary

### Deterministic software

- upload validation, hashing, storage, and job state
- PDF.js rendering, zoom, viewport measurement, and coordinate conversion
- PyMuPDF native text/word extraction and crop rendering
- text-quality heuristics and OCR gating
- annotation anchoring/restoration
- chunk assembly, FTS/BM25, rank fusion, token budgeting, deduplication
- provider request construction, audit manifests, streaming, persistence

### Local ML models

- embedding encoder for semantic retrieval
- OCR detector/recognizer only on suspect pages or regions
- optional Docling layout/table models
- optional small cross-encoder reranker later

### Remote API models

- natural-language reasoning and answer generation
- visual interpretation of selected equation/figure/table crops
- optional summarization/enrichment jobs only if explicitly enabled; never an implicit whole-document upload

## 5. Initial local models and realistic resource envelope

Model sizes below are approximate downloaded weight sizes; actual process memory depends heavily on Python/PyTorch/ONNX runtime, sequence length, and batch size.

### Embedding model: start with `BAAI/bge-small-en-v1.5`

- English-focused, roughly 33 million parameters, 384-dimensional output, 512-token input.
- Approximately 130–140 MB in FP32 weights; quantized ONNX variants are often roughly 35–70 MB depending on format.
- CPU is entirely practical. Allow roughly 0.5–1 GB incremental RAM for inference, and about 1–2 GB for the complete Python worker after runtime libraries are loaded.
- Normalize vectors and use the documented retrieval query instruction for short queries; passages are embedded without it.
- Prefer ONNX Runtime on CPU after correctness is established; begin with `sentence-transformers` because it is easier to verify.

`intfloat/e5-small-v2` is a sound alternative of similar scale, but it requires consistent `query:` / `passage:` prefixes. Pick one rather than supporting both initially. BGE's v1.5 behavior without instructions is a little harder to misuse.

### OCR model: add only at the OCR milestone

Start with PaddleOCR's English PP-OCRv5 mobile detector and mobile recognizer, not its server models. The model weights are in the tens-of-megabytes range in aggregate; Paddle/ONNX runtime dominates disk and memory. CPU inference is feasible, but budget approximately 1–2 GB available RAM and seconds per page depending on DPI and hardware. Benchmark PaddleOCR against RapidOCR/ONNX during that milestone because packaging simplicity may outweigh small quality differences.

OCR is not equation understanding. Preserve the crop and use a remote vision model for equation/figure reasoning.

### Reranker: not in the first retrieval version

If evaluation shows it helps, add `cross-encoder/ms-marco-MiniLM-L-6-v2` (roughly 23 million parameters / about 90 MB FP32). It is CPU-feasible; rerank only the top 20–30 candidates and expect roughly 0.5–1 GB incremental runtime memory. Do not add it without a small retrieval evaluation set.

Docling's layout/table pipeline may consume several GB depending on enabled models and runtime. It is an optional “enhanced parsing” profile, not a baseline requirement.

## 6. High-level database design

Use UUIDv7 or ULID public IDs and integer internal row IDs where SQLite/FTS integration benefits. All timestamps are UTC. Page numbers exposed to users are one-based.

### Documents and extracted content

- `documents`: id, sha256, original_name, media_type, byte_size, storage_path, title, authors_json, page_count, status, parser_version, created_at, updated_at
- `pages`: id, document_id, page_number, media/crop box coordinates, rotation, canonical_width_pt, canonical_height_pt, native_text, effective_text, extraction_source, quality_json
- `text_blocks`: id, page_id, reading_order, kind, text, canonical_bbox_json, style_json, section_id nullable
- `page_words`: id, page_id, reading_order, text, canonical_bbox_json, block_id, line_id, source, confidence nullable
- `sections`: id, document_id, parent_id nullable, ordinal, level, title, first_page, last_page, source, confidence
- `chunks`: id, document_id, section_id nullable, ordinal, text, token_count, first_page, last_page, parent_chunk_id nullable, chunker_version
- `chunk_spans`: chunk_id, page_id, start_word_id, end_word_id, canonical_bbox_json; one chunk can span pages/boxes
- `embeddings`: chunk_id, model_id, dimension, vector_blob, normalized, created_at; unique on chunk/model
- `search_chunks`: an FTS5 external-content table keyed to chunks

Keep `native_text` separate from `effective_text`: OCR or later parser output must not destroy provenance.

### Annotations

- `annotations`: id, document_id, kind (`highlight` or `region`), color, quote_text nullable, anchor_version, created_at, updated_at
- `annotation_rects`: id, annotation_id, page_id, ordinal, normalized x0/y0/x1/y1
- `annotation_text_anchors`: annotation_id, page_id, start_word_id, end_word_id, prefix_text, suffix_text, resolver_status
- `notes`: id, annotation_id, body, created_at, updated_at
- `paper_notes`: document_id (unique), content_json, plain_text, markdown_snapshot nullable, revision, created_at, updated_at
- `floating_note_windows`: annotation_id, normalized_x, normalized_y, width_px, height_px, minimized, updated_at

An annotation is the anchor; a note is content attached to it. Multiple rectangles are mandatory because a text highlight is usually one rectangle per line and can cross pages. Quote plus prefix/suffix is a fallback selector if extraction changes.

`floating_note_windows` is optional presentation state and may remain in browser-local storage initially. The authoritative note remains in `notes`; moving or closing a card never changes its PDF anchor.

### Conversations, providers, and jobs

- `conversations`: id, document_id, title, provider_config_id, model_id, created_at, updated_at
- `messages`: id, conversation_id, role, visible_text, status, provider_message_id nullable, usage_json, created_at
- `message_contexts`: message_id, scope, current_page, request_context_json, builder_version, token_budget
- `message_context_items`: message_id, kind, chunk_id/annotation_id/page_id nullable, rank, score_json, sent_text_hash, crop_id nullable
- `derived_assets`: id, document_id, page_id, kind, normalized_bbox_json, storage_path, sha256, media_type, created_at
- `provider_configs`: id, provider_kind, display_name, base_url nullable, secret_reference, nonsecret_config_json, enabled
- `model_catalog`: provider_config_id, model_id, capabilities_json, context_window nullable, source, refreshed_at
- `ingestion_jobs`: id, document_id, stage, status, progress, attempt_count, error_json, lease fields, created_at, updated_at

Store a secret reference, never an API key, in ordinary SQLite rows. Native installs should use the OS keychain. Docker deployments should prefer environment variables or mounted secrets. If encrypted key storage is later added, require an external master secret; “encrypted in SQLite with a key stored beside SQLite” is not meaningful protection.

## 7. Backend API surface

Initial endpoints:

```text
POST   /api/documents                         upload, return document + job
GET    /api/documents                         list local library
GET    /api/documents/{document_id}           metadata and processing state
GET    /api/documents/{document_id}/file      PDF bytes; Range and conditional requests
DELETE /api/documents/{document_id}           later; explicit destructive operation
GET    /api/documents/{document_id}/pages/{n}/text
GET    /api/jobs/{job_id}
GET    /api/jobs/{job_id}/events              progress event stream
```

Annotation and region endpoints:

```text
GET    /api/documents/{document_id}/annotations
POST   /api/documents/{document_id}/annotations
PATCH  /api/annotations/{annotation_id}
DELETE /api/annotations/{annotation_id}
POST   /api/annotations/{annotation_id}/notes
PATCH  /api/notes/{note_id}
GET    /api/documents/{document_id}/paper-note
PUT    /api/documents/{document_id}/paper-note        revision-aware autosave
POST   /api/documents/{document_id}/regions/resolve   preview overlapping text/caption
POST   /api/documents/{document_id}/regions/render    create/reuse authoritative crop
```

Conversation/provider endpoints:

```text
POST   /api/documents/{document_id}/conversations
GET    /api/conversations/{conversation_id}/messages
POST   /api/conversations/{conversation_id}/messages:stream
POST   /api/providers
POST   /api/providers/{provider_id}/validate
GET    /api/providers
GET    /api/providers/{provider_id}/models
```

The streaming message request contains `text` and a structured context envelope with an explicit `scope`, selection selectors, viewport ranges, and client geometry version. The server validates document ownership/existence and is solely responsible for deciding what is sent remotely.

Use problem-details JSON for non-streaming errors. A stream emits typed events such as `message.started`, `context.ready`, `content.delta`, `usage`, `message.completed`, and `error`. Generate idempotency keys for upload and message creation so retries do not duplicate state.

## 8. Frontend component hierarchy

```text
AppShell
├── LibraryRoute
│   ├── DocumentList
│   └── UploadDropzone
└── ReaderRoute
    ├── ReaderToolbar (page, zoom, search, selection mode)
    ├── ResizableWorkspace
    │   ├── PdfReader
    │   │   ├── VirtualizedPageList
    │   │   │   └── PdfPage
    │   │   │       ├── CanvasLayer
    │   │   │       ├── TextLayer
    │   │   │       ├── AnnotationOverlay
    │   │   │       ├── RegionSelectionOverlay
    │   │   │       └── ViewportObserver
    │   │   └── SelectionController
    │   │       └── SelectionToolbar
    │   └── SidePane
    │       ├── SidePaneModeTabs (Chat / Notes)
    │       ├── ChatMode
    │       │   ├── ContextScopeChip (Selection / Here / Paper)
    │       │   ├── ContextPreview
    │       │   ├── ConversationThread
    │       │   └── Composer / quick actions
    │       └── PaperNoteMode
    │           ├── RichTextToolbar / BubbleMenu
    │           ├── SlashCommandMenu
    │           └── PaperNoteEditor
    └── FloatingNoteLayer
        └── AnchoredNoteCard
```

Keep geometry conversion and selection state in a viewer-domain module rather than generic global state. The chat mode receives a stable `ContextEnvelope`; it should not inspect DOM selections itself.

`FloatingNoteLayer` is mounted at the workspace root so cards are independent of PDF page virtualization and scroll. Annotation markers remain inside each page's overlay.

Virtualize pages but retain annotation geometry independent of mounted DOM. A page entering the viewport recreates overlays from canonical selectors.

## 9. Coordinate and anchor model

### Canonical page space

Define one application-owned coordinate system:

- based on the unrotated page CropBox
- normalized to `[0, 1]`
- origin at top-left, x rightward, y downward
- rectangle order `{x0, y0, x1, y1}` with x0 <= x1 and y0 <= y1
- page number stored separately

Also store each page's original MediaBox, CropBox, and rotation. Do not store browser pixels, zoom values, device-pixel-ratio pixels, or a rendered canvas size as annotation truth.

PDF's native user space typically has a bottom-left origin, while a PDF.js viewport incorporates scale, rotation, and a top-left display transform. Convert each CSS-relative selection corner with the PDF.js viewport inverse (`convertToPdfPoint` or the equivalent transform), normalize against the page view box, and flip y into canonical top-left orientation. For rotated pages, transform all four corners and take bounds; never assume two diagonal points remain ordered.

Canvas backing-store pixels include device pixel ratio. Selection DOM rectangles are CSS pixels. Coordinate conversion uses CSS-relative page coordinates and the PDF.js viewport, not canvas backing dimensions.

Backend PyMuPDF coordinates have their own page/rotation conventions. Isolate those conversions in a `PageTransform` module and validate them against fixture PDFs at rotations 0/90/180/270, non-zero CropBoxes, mixed page sizes, and HiDPI rendering.

### Text anchors

A robust highlight stores all of:

1. canonical rectangles (visual restoration),
2. exact selected quote (semantic intent),
3. resolved backend word IDs/range (content linkage), and
4. short quote prefix/suffix (re-anchoring after reparsing).

PDF text is not a clean character stream. Ligatures, soft hyphens, duplicated invisible text, multi-column order, and mathematical glyph encodings make raw DOM offsets brittle. Treat offsets as hints, not the sole anchor.

### Regions and viewport

A region is normally one canonical rectangle on one page. A viewport is a list of `{page, y0, y1, visible_fraction}` entries because two pages may be visible. Debounce viewport reports and send them with a question; do not continuously persist scroll telemetry by default.

## 10. Context-building and RAG algorithm

### Chunking

Start section-aware but tolerate missing sections:

- assemble text blocks in inferred reading order
- aim for about 350–500 embedding tokens per child chunk with 50–80 tokens of overlap
- never split a block merely to hit an exact size unless it is itself too long
- preserve page spans and word/bbox provenance
- include the section title in embedding input metadata, not repeatedly in user-visible chunk text
- later add parent section chunks/summaries without changing child IDs' meaning

### Query-time algorithm

1. **Interpret scope.** `Selection` is explicit and strongest; `Here` uses viewport; `Paper` is global. Never invent a visual selection.
2. **Resolve local evidence.** Exact selected quote or image is non-negotiable context. Add containing block and limited neighbors. For a region, add overlap text and caption candidates. For `Here`, collect blocks overlapping the visible y-ranges.
3. **Build retrieval query.** Use the question as the main query. Add a short selection-derived phrase only when the question is referential (“why this?”); embedding the entire long selection can swamp the actual intent.
4. **Generate candidates.** Retrieve approximately 20 vector candidates and 20 FTS5/BM25 candidates within the document. Include lexical search from day one of paper-wide chunks; add vectors at its milestone.
5. **Fuse ranks.** Use Reciprocal Rank Fusion, which avoids pretending BM25 and cosine scores are calibrated. Apply modest deterministic boosts for the current section/page and exact entity/equation references.
6. **Expand.** For the top candidates, add one adjacent child chunk or a parent-section excerpt when needed. Deduplicate text already present in local context.
7. **Diversify.** Initially cap chunks per section; later use MMR if redundant retrieval is observed.
8. **Rerank only if justified.** A local cross-encoder can rerank the top 20 after an evaluation set demonstrates improvement.
9. **Budget.** Reserve output tokens first, then local evidence, question/history, document identity, and retrieved evidence. Trim lowest-ranked global evidence before local evidence. Summaries are hints, never replacements for primary text.
10. **Prompt.** Label each source with stable chunk/page IDs; separate user-authored notes from paper text; instruct the model to distinguish the selection from retrieved context and to say when evidence is insufficient.
11. **Persist manifest.** Record context item IDs, hashes, ranks, scores, builder version, model, and capability choices for debugging/reproducibility.

### What to add when

- First paper-wide retrieval: FTS5 + BGE vectors + RRF, child chunks, neighbor expansion.
- Later: hierarchical parent chunks and section summaries for broad synthesis questions.
- Later still: reranking based on measured failures.
- Avoid automatically generating summaries at ingestion in the MVP: it costs remote tokens, creates stale derived claims, and weakens the local-only default.

## 11. Hardest technical problems

1. **Selection fidelity across two PDF engines.** PDF.js text spans and PyMuPDF words will disagree on ordering, ligatures, whitespace, and glyph mappings. Geometry-plus-quote reconciliation needs fixtures and visible diagnostics.
2. **Coordinate correctness.** Rotation, CropBox offsets, mixed page sizes, CSS transforms, virtualization, and device pixel ratio create subtle errors that only appear on some papers.
3. **Reading order and structure.** Multi-column papers, footnotes, headers, captions, equations, and tables defeat naive `sort top-left` extraction. Structure must be replaceable and provenance-aware.
4. **Reliable OCR gating.** “Some text exists” does not mean it is usable. Garbled font encodings and mixed scanned/native pages require page-level quality signals and manual override.
5. **Context quality, not vector search.** Referential questions need the right local anchor, neighboring explanation, equation definitions, and distant evidence without overwhelming the model.
6. **Provider normalization.** Streaming, image input, token limits, system-message semantics, errors, and model capability metadata differ. The abstraction must expose differences rather than erase them.
7. **Privacy and secret handling.** Prevent accidental whole-PDF uploads, sensitive logs, remote telemetry, crop retention surprises, and false claims about API-key encryption.

## Docker deployment, persistence, and upgrades

Docker Compose is the primary self-hosted distribution path. The application image is disposable; all durable or expensive-to-recreate state lives under one application data root mounted from the host at `/data`.

Recommended host layout:

```text
adam/
├── compose.yaml
├── .env                         non-secret deployment settings
└── data/                        host-owned and backed up
    ├── database/
    │   └── adam.sqlite3
    ├── documents/               original PDFs, content-addressed
    ├── derived/                 page crops and other derived assets
    ├── models/                  downloaded local-model cache
    ├── backups/
    └── tmp/                     safe to clean when the app is stopped
```

The default Compose mapping should be a bind mount:

```yaml
volumes:
  - ./data:/data
```

A bind mount is more transparent than a Docker-managed named volume: the user can see, back up, and move the data directory without Docker-specific export commands. A named-volume example may also be documented for users who prefer Docker-managed storage.

Persist in `/data`:

- SQLite database, including annotations, notes, conversations, jobs, and configuration metadata
- original PDFs
- derived image crops and thumbnails
- embeddings/index artifacts when stored separately
- local model downloads, so container recreation does not redownload them

Do not persist application source, installed dependencies, or built frontend assets. They belong in the versioned image. Temporary upload files are written under `/data/tmp` and atomically moved into document storage after validation.

### Compose shape

Use one application container initially. It serves the built SPA, FastAPI, and a single ingestion worker managed by the application process. This avoids SQLite coordination and migration races between multiple containers. Expose one configurable port, bind to localhost by default, and include a health check.

```text
docker compose up -d
browser -> http://localhost:<port>
```

OCR/layout features can later be optional Compose profiles rather than mandatory services. Do not require a database, Redis, or model-server container.

### Upgrade contract

A normal upgrade is:

```text
back up data -> pull/build pinned image -> docker compose up -d
```

At startup, the application obtains a migration lock, checks the database schema, and applies forward Alembic migrations before accepting traffic. An upgrade must never delete original documents or silently rebuild user-authored data. Derived data carries parser/model versions and may be marked stale and regenerated in the background.

Use explicit image tags/releases rather than relying on `latest`. Downgrades are not guaranteed: restoring the pre-upgrade backup is the supported rollback path.

### Backup and recovery

Copying a live SQLite database file in WAL mode is not a reliable backup procedure. Provide an application backup command/endpoint that uses SQLite's online backup API and then archives the database together with `documents/`. The simplest manual alternative is to stop the container and copy the entire `data/` directory.

`derived/` and `models/` may be excluded from a space-efficient backup because they can be regenerated/redownloaded. The minimum complete backup is the consistent database snapshot plus `documents/`; include any non-regenerable user-added assets if those are introduced.

### Permissions and secrets

- Run as a non-root container user and document host-directory ownership. Support configurable UID/GID if cross-platform permission issues require it.
- API keys do not go in `compose.yaml`, the image, or ordinary database fields. Use an uncommitted `.env` for the simplest local deployment or mounted Docker secrets for stronger handling.
- The implemented Compose path uses `./secrets/opencode_api_key` as an ignored, owner-readable host file mounted into only the API container at `/run/secrets/opencode_api_key`; the key is not injected into the container environment.
- Backing up `/data` does not necessarily back up external secrets; document secret restoration separately.
- If exposed beyond localhost, place it behind an authenticated HTTPS reverse proxy. Dockerization alone is not an authentication boundary.

## 12. Runnable MVP roadmap

Every milestone ends with a runnable application and acceptance checks.

### Milestone 0 — shell and document opening

- Monorepo with Vite React client, FastAPI API, SQLite migrations, local data directories.
- Upload, content-addressed storage, document list, ranged PDF serving, PDF.js rendering.
- Runnable in development and through `docker compose up`, with `./data:/data` persistence and a health check.
- Acceptance: upload a PDF, recreate the container/image, and reopen the same document with its database state intact.

### Milestone 1 — first AI vertical slice

- PyMuPDF native page-text extraction.
- Browser text selection and floating `Ask AI` action.
- AI sidebar shows the exact selected quote and page.
- One provider adapter (OpenCode Zen first, Gemini 3.8 Flash) with an API key supplied by environment or local secret store.
- POST-based streamed response, message persistence, cancellation, clear provider errors.
- Acceptance: select a sentence, ask a question, and see a streamed answer without the PDF being uploaded remotely.

### Milestone 2 — persistent highlights and notes

- Canonical geometry conversion, multi-rect highlights, quote/word anchors, overlay restoration.
- Light-bulb markers and one draggable, scroll-independent anchored-note card; edit/delete actions.
- Chat/Notes side-pane modes and one autosaving rich-text paper note per document.
- Coordinate fixture tests.
- Acceptance: zoom, rotate, resize, restart, and see the highlight aligned and its note intact; scroll the PDF while an open note card stays fixed; switch between chat and the paper note without resizing the PDF.

### Milestone 3 — paper-wide text and lexical retrieval

- Blocks, basic heading/section heuristics, chunks with word/page provenance, FTS5.
- `Paper` questions with BM25, neighbor expansion, source chips and jump-to-page.
- Acceptance: answer a question whose evidence is on another page and inspect the supplied sources.

### Milestone 4 — local semantic retrieval

- BGE-small embeddings and exact cosine scan; embedding job/version state.
- BM25 + vector Reciprocal Rank Fusion and a small curated retrieval test set.
- Acceptance: semantic paraphrase questions retrieve evidence that exact search misses.

### Milestone 5 — passive reading context

- IntersectionObserver-based visible page/range reporting.
- Explicit Selection/Here/Paper scope control and context preview.
- Acceptance: “why do they do this?” with no selection uses only the visible passage plus needed retrieved context.

### Milestone 6 — rectangle selection and vision

- Region draw/resize, canonical crop rendering, overlap/caption collection.
- Vision-capability checks and one multimodal provider path.
- Acceptance: select an equation or figure and receive an answer grounded in both crop and paper text.

### Milestone 7 — selective OCR

- Page-quality classifier, PaddleOCR mobile pipeline, per-page/manual OCR actions, provenance.
- Rechunk/reindex only affected content.
- Acceptance: a mixed native/scanned PDF keeps native pages untouched and makes scanned pages searchable.

### Milestone 8 — provider expansion and secrets

- Anthropic, Gemini, OpenRouter, and generic OpenAI-compatible adapters.
- Capability/model catalog, keychain/Docker-secret paths, provider validation UI.
- Acceptance: switch providers without changing saved conversation/context contracts.

### Milestone 9 — enhanced structure and retrieval

- Optional Docling profile, table/figure entities, parent-child chunks.
- Evaluate hierarchical retrieval and the small reranker; enable only measured improvements.
- Acceptance: ingestion remains usable without optional models, and enhanced parsing can be rebuilt/versioned.

### Milestone 10 — packaging and hardening

- Release-grade Docker Compose deployment, online backup/export, upgrade migration checks, storage limits, security headers, and failure recovery. Basic Compose support already exists from Milestone 0.
- Acceptance: clean-machine install, upgrade migration, backup/restore, and interrupted-job recovery.

## Near-term decisions

Unless revised after discussion, implementation should begin with:

1. React + Vite + direct PDF.js integration.
2. FastAPI + SQLAlchemy/Alembic + SQLite WAL.
3. PyMuPDF only for the first ingestion path.
4. One database-backed worker boundary, even if the first tiny extraction runs inline during development.
5. OpenCode Zen as the first adapter, using Gemini 3.8 Flash by default, with the interface shaped for later providers.
6. Canonical normalized, unrotated CropBox coordinates plus redundant text anchors.
7. No embeddings, OCR, Docling, vector extension, or authentication in the first vertical slice.
