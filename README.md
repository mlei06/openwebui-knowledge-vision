# Knowledge Vision

[Open WebUI community post](https://openwebui.com/posts/9bab22a6-1948-4707-a6c9-133e4c8c68b7) · [Installable source](tool.py)

Let a vision-capable model inspect the **original pixels** of an image, PDF page or PowerPoint slide from an Open WebUI knowledge base, a user upload, or the selected Open Terminal. It uses the model already answering the chat; it does not send the file to a separate vision service.

Native knowledge/chat-file search finds relevant text, and terminal tools discover filesystem paths. Knowledge Vision lets the model examine diagrams, screenshots, charts, layout and scanned pages that text extraction cannot fully describe.

## Functions

| Function | Purpose |
| --- | --- |
| `inspect_knowledge_visual(knowledge_id, file_id)` | Report the original format and total page/slide count without returning images. |
| `read_knowledge_visual(knowledge_id, file_id, start_page=1, page_count=1)` | Read an original KB file as images. |
| `inspect_attachment_visual(file_id)` | Inspect an uploaded file by its Open WebUI file/attachment ID. |
| `read_attachment_visual(file_id, start_page=1, page_count=1)` | Read an authorized user upload as images. |
| `inspect_terminal_visual(path)` | Inspect a file at an absolute path in the selected terminal. |
| `read_terminal_visual(path, start_page=1, page_count=1)` | Read a terminal file as images without changing the original. |

All three read calls return actual image inputs for one to three consecutive pages, with `total_pages` and `next_page`.

Use exact KB/file IDs returned by native knowledge discovery/search or `kb_exec`. A knowledge ID identifies a **collection**, so KB calls also need the file ID of the document inside it. For chat uploads, use the attachment's `id`/file ID or `list_chat_files`; an attachment ID is the Open WebUI file ID, not a chat/message ID. Uploaded files do not have to be indexed into a KB. Use terminal listing/search to find an **absolute** terminal path. Relative paths and URLs are rejected, and paths are sent only to the selected terminal's file API. Pages and slides are **1-based**. A standalone image has one page. Reading the last page returns `next_page: null`. Asking beyond the end returns an error with the document's page count.

Each returned image has its page number and dimensions; the outer result identifies the source type and its KB/file IDs or terminal path, plus filename. Cite that filename and page/slide number when answering. The model should treat text inside images as source material, not instructions.

## Example requests

- “Look at the network diagram in the architecture knowledge base and explain the connections.”
- “Find the failure-analysis presentation, then compare the charts on slides 2 and 3.”
- “The extracted text is confusing. Read page 7 of the PDF visually and explain the table.”
- “Read the screenshot I uploaded and explain the error.”
- “Compare slides 2 and 3 of `/home/user/reports/review.pptx` in my terminal.”

Suggested model guidance:

> Locate the source with native knowledge/chat-file search or terminal listing. Use the matching Knowledge Vision call: KB ID plus file ID for knowledge files, file ID for uploads, or absolute path for terminal files. When the question depends on a chart, diagram, screenshot, scan or page layout, inspect the page count if needed, then read only the relevant pages. Follow `next_page` when necessary. Cite filename/path and page or slide number.

## Installation and configuration

Install [`tool.py`](tool.py) as a Workspace Tool. It is a self-contained source file using Pillow, HTTPX and Pydantic already present in the tested Open WebUI environment. Enable **Native function calling** and **Vision** on a model whose provider actually accepts image inputs, then select the Tool for that model or chat. Tool installation does not automatically attach it to every preset.

**Images in knowledge bases or uploads do not require a terminal.** Terminal paths always require a selected terminal. PDF/PPTX rendering from any source requires a selected, authorized, backend-reachable **Open Terminal**:

- PDF: `pdfinfo` and `pdftoppm` from Poppler.
- PPTX: LibreOffice Impress, plus Poppler for the resulting PDF.
- Both: Python 3 and GNU `timeout`.

These are present in the tested Open Terminal 0.14.0 default image. A browser-only personal terminal is not supported. The selected terminal's normal connection authorization, user header and context routing apply. No terminal URL, credential or shell command is accepted as a model argument. Only the terminal-specific calls accept a filesystem path, which is encoded as a file-API query parameter and never executed as shell code or opened on the WebUI host. TLS certificate verification stays enabled.

| Valve | Default | Purpose |
| --- | --- | --- |
| `MAX_FILE_MB` | 25 | Maximum original file size in MiB; configurable from 1 to 50. |
| `MAX_IMAGE_SIDE` | 1600 | Maximum output dimension in pixels; configurable from 512 to 2400. |

## Access and temporary files

Every call verifies the current authenticated caller. Source-specific checks apply:

- **Knowledge:** read access to the specified KB and membership of the file in that KB. A readable file in a different collection does not authorize an inaccessible KB.
- **Attachments:** ownership of the uploaded file, administrative access, or the platform's normal read access through a shared KB/chat/model/note/channel. Knowing another user's file ID alone does not grant access.
- **Terminal:** authorization to the selected, backend-reachable connection, using the caller's user/context headers; its filesystem policy controls path access. Terminal reads are bounded snapshots. The connection and current caller are rechecked after rendering; a changed/revoked connection fails before releasing images.

KB/upload permissions and file identity are checked again after rendering. Having the Tool grants no extra access. Administrators retain the platform's administrative file access.

For KBs and uploads, the original storage key comes from the authorized file record. Model-supplied paths and URLs cannot select files on the WebUI host. Terminal paths go exclusively to the selected terminal. The tool does not change original files, KB access, extracted text or vectors.

For documents, a copy is uploaded to a unique job directory in the caller's selected terminal. A fixed renderer converts and reads only that job's files. It disables LibreOffice macros/link updates, rejects external PPTX relationships, macros and embedded objects, limits output size and execution time, and deletes source copies in the renderer's `finally` block. The client also removes the entire temporary job directory on success or failure. Cleanup is best effort if the terminal disconnects; administrative cleanup of leftover `.cache/knowledge-vision/` jobs may be needed after an outage. Do not select a shared terminal for private documents unless its filesystem isolation is appropriate for those users.

Open WebUI may retain returned page images as chat tool-result uploads. Those follow normal chat/file retention and sharing; removing a KB grant does not retract images already read into an earlier conversation. Terminal process logs contain the fixed command and page-count metadata, not the source bytes or image base64.

## Supported content and limitations

- Images: PNG, JPEG, WebP, GIF, TIFF and BMP; EXIF orientation is applied. Transparent backgrounds become white. Animated/multipage images show only the first frame. SVG is not rendered.
- PDFs: original pages rendered as JPEG, including their diagrams and embedded images. Encrypted, damaged or over-limit files can fail.
- PowerPoint: self-contained `.pptx` converted to PDF with LibreOffice, then paginated by slide. Fonts and rendering can differ from Microsoft PowerPoint. Animations, speaker notes, legacy `.ppt`, macros, external relationships (including hyperlinks), and embedded OLE objects are not supported. Export such decks to PDF first.
- Up to three pages per call, 2,000 pages per document, 40 million pixels per source image, and a 100 MiB total uncompressed PPTX archive limit. The output is resized for bounded model context; very small text may need a higher image-size valve or text retrieval.
- Rendering repeats per request; there is no shared document cache. This favors fresh authorization and cleanup over conversion speed for long decks.
- The original binary must exist as an authorized upload/KB file or readable terminal file. Markdown/MDX links and image references are **not fetched**, and extracted text cannot reconstruct a missing original. Markdown/MDX knowledge files do not automatically supply the images referenced by those pages.
- `display_file` shows a preview to the user. Knowledge Vision returns images for the model to inspect. The native middleware extracts image data URIs from the structured Tool result; a provider or custom harness that drops multimodal tool results cannot use this output.
- Document rendering runs in the selected terminal's existing process environment; it is not an additional security sandbox.

## Verification

The standalone tests use synthetic files and mocked WebUI/terminal boundaries. The original development checks also exercised real Open WebUI 0.13.3 authentication, permissions and image-result handling, and real Open Terminal 0.14.0 PDF/PPTX rendering and cleanup. Model interpretation quality depends on the selected vision model.

Source: [tool.py](tool.py). No deployment checkout is required to install this Tool.

## Running the standalone tests

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -p 'test_*.py'
```

These tests use synthetic fixtures and do not require production credentials.
