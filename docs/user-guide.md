# User guide

[← Back to README](../README.md)

Detailed behavior of Adam's library, reader, chats, and notes. The [README](../README.md) has the overview and setup.

## Library and folders

Click a sidebar folder row to open it and toggle its subfolders. The chevron indicates whether the branch is expanded, and your choices are remembered in this browser. Create a subfolder from an open folder, or choose a parent in the New folder dialog. Drag folders onto other folders or onto All papers / Workspace to move them to the top level. Edit folder also lets you change its parent. Descendants inherit their root folder’s color; moving a branch to the top level automatically assigns a color. Removing a folder keeps its papers in Unfiled and promotes its subfolders to the top level.

Grid and list views, sorting, and folder-scoped uploads and search are available from the library toolbar. Removing a folder keeps its papers, notes, and chats.

## Finding and importing papers

Open **Find papers** (or press **⌘/Ctrl + Shift + K**) to search arXiv and the web. Paper discovery queries arXiv directly and uses this service for broader web results. No AI key is needed. Search runs only on submission; reviewing a source may load remote metadata/PDF links, and **Download & add to library** stores the PDF and extracts its text. Restricted or unavailable PDFs can still be uploaded manually. Imported papers retain their source URL, PDF URL, available identifiers, and import date. arXiv versions remain separate search results; repeat imports preserve the existing copy and its folder, notes, and chats.

**Add from link:** Open search (or Find papers), choose **Add from link**, and paste a public HTTP/HTTPS PDF URL. **Check PDF link** downloads a temporary copy, validates the actual PDF and shows its title, page count, and size without changing your library. Choose a folder and **Download & add to library**, then **Open in reader**. URLs without a `.pdf` suffix work. Login pages, damaged/password-protected PDFs, private-network addresses, and oversized files are rejected. Saving downloads and validates the file again; duplicate copies retain their existing notes, chats, and folder.

Paper search shortcuts: `Cmd/Ctrl + K` opens library search and cycles through **My library → Find papers online → Add from link → My library** while the dialog is open. `Cmd/Ctrl + Shift + K` opens online paper discovery directly. Switching modes preserves the search query, PDF link, and online results.

## Reading

Click a page number or percentage to edit it; Enter applies a valid value, and Escape or leaving the field cancels the edit. Page numbers must be whole numbers within the paper. Paper zoom accepts 10–3200%; chat text accepts 100–200%. Invalid input stays in the field with a message. Escape clears bulk selection when no dialog or text entry is active. Continue reading appears after opening papers and stores progress in this browser; it resumes the page, position within the page, horizontal scroll, and zoom.

Click in the PDF, Chat, or Notes pane (or focus its controls with the keyboard), then press **⌘ + / −** on Mac or **Ctrl + / −** elsewhere to zoom that pane. The unshifted **=** key also zooms in. These shortcuts work while writing notes or chat drafts, use each pane's existing zoom limits, and keep the browser's page zoom unchanged.

**Find in the PDF** with **⌘/Ctrl + F**, and in the chat with **⌘/Ctrl + Shift + F**. Matches are highlighted; use next/previous to move between them and **Esc** to close. When chat history is open, the chat shortcut searches history instead.

**Focus reading:** click **Focus reading** or press **F** to hide the sidebar and anchored note cards while keeping note and chat-link icons available. Toggle **Notes** to show anchored note cards beside the PDF without leaving focus mode. Use **Exit focus** or **Esc** to restore your workspace, zoom, and reading position.

## Chats

Every question automatically includes the full paper text. Selected passages and region captures are sent as explicit focus. Each conversation keeps a snapshot of the provider, model, and system prompt it started with.

New chats stay temporary until you type a draft or send a message. Repeated **New** clicks reuse an empty chat, and empty chats do not appear in history. Draft text autosaves locally on the server, with a browser recovery copy while saves are pending; it restores when you switch chats or reload. Clearing an unsent draft removes it from history. Opening a paper with saved chats shows its history first. You can write drafts before configuring an API key.

The Search web control is off for each new question. When enabled, Adam searches and cites useful sources in its answer. It can include up to 7,500 characters of search snippet and page text from each of five results. Earlier web sources remain available to follow-up questions in that chat. SearXNG needs no search API key; it is reachable only through Docker and localhost port 8888 for local development.

Chat history search covers titles, questions, and answers for the current paper, shows matching snippets, and opens the matching chat.

## Paper notes and source quotes

Open **Notes** in the right pane. Use **A− / percentage / A+** beside the tabs to zoom the full note from 50–200%, including headings and source quotes. Click the percentage to enter a size; Enter applies it and Escape cancels. Note zoom stays independent of PDF and chat zoom and is retained when switching panes during the session. Click in the note where you want a quote. Select text in the PDF and press **Q** (or **Quote in note**): the quote inserts at the retained note cursor, splitting a paragraph when needed. Source quotes are protected blocks with their page citation. Click **Show in PDF** on a source quote to scroll to the quoted passage and briefly highlight it before it fades. New quotes retain their exact position across saves, reloads, and copy/paste; older quotes locate their text on the cited page. Drag the grip to move a quote, use its up/down buttons, or cut/copy/paste it with the surrounding writing. **+ Above** and **+ Below** add writing space beside a quote; empty paragraphs are also kept at document edges. Undo/redo includes quote insertion and moves. Existing cited quotes become protected blocks when opened; ordinary blockquotes remain editable.
