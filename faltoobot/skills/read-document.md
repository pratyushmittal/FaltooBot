---
description: Read PDFs, DOCX, and other documents from saved files or websites when the user asks to summarize, inspect, extract, or answer questions from a file or link.
---

Use this when the user asks about a saved document, names a document, or asks a follow-up about a document.

Documents sent on WhatsApp are saved in the workspace. The chat history includes a message like: `User has sent a file named report.pdf of 3.2mb (32 pages).`

## Workflow

1. Pick the source document from chat history or from the file name mentioned by the user.
2. Extract the document to markdown/text next to the source file.
3. Reuse the extracted markdown/text if it already exists.
4. Answer only from the extracted content, or spawn a sub-agent for large whole-document work.

## Read PDFs with PyMuPDF

Use PyMuPDF's CLI in blocks mode. It sorts text blocks by position, which usually gives a better reading order for normal PDFs.

```bash
uv run --with pymupdf python -m pymupdf gettext \
  -mode blocks \
  -noformfeed \
  -output report.txt \
  report.pdf
```

## Read other documents with MarkItDown

Use MarkItDown for DOCX, PPTX, XLSX, HTML, CSV, JSON, XML, EPUB, and similar files. It converts many file formats to Markdown for LLM-friendly reading.

```bash
uv run --with 'markitdown[all]' markitdown report.docx -o report.md
```

## Direct read vs spawn

Use direct read when the task is narrow and the needed text is small enough to inspect safely. For large documents or broad questions, spawn a sub-agent instead of pasting the whole extracted file into the main session.

## Spawn command

Replace the file path and query text before running:

```bash
query_file="$(mktemp)"
cat > "$query_file" <<'QUERY'
paste the user query here
QUERY
nohup sh -c 'faltoochat "Read report.md and answer the user query from this file only. User query: $(cat "$1")" --workspace=. --new-session 2>&1 | faltoobot notify "{chat_key}" --source="document-reader:report.md"' sh "$query_file" &
```

Tell the user that the document is large and a document-reader session has been started.

## Answer rule

Mention the file name used. Answer only the user query from that file. If there is no concrete question, ask what the user wants from the file.
