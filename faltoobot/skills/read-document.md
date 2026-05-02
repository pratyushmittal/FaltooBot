---
description: Read saved documents and answer questions from the selected file.
---

Use this when the user asks about a saved document, names a document, or asks a follow-up about a document.

## Workflow

1. Pick the source document from chat history or from the file name mentioned by the user.
2. Use the saved metadata to decide read mode. Do not recompute pages/size unless metadata is missing.
3. Set paths:

```bash
doc='documents/file.pdf'
txt="${doc%.*}.txt"
mutool='{document_mutool_binary}'
pandoc='{document_pandoc_binary}'
```

4. Reuse `$txt` if it exists. Otherwise extract text with the correct binary.
5. Answer only from `$txt` or spawn a sub-agent for large whole-document work.

## Extract PDF with MuPDF

For normal PDFs with selectable text:

```bash
"$mutool" convert -F text -o "$txt" "$doc"
```

OCR example for scanned/mixed PDF pages:

```bash
"$mutool" draw -F ocr.txt -o "${doc%.*}.ocr.txt" -r 300 -t eng "$doc"
```

## Extract office documents with Pandoc

```bash
"$pandoc" "$doc" -t plain --wrap=none -o "$txt"
```

Examples:

```bash
"$pandoc" "documents/report.docx" -t plain --wrap=none -o "documents/report.txt"
"$pandoc" "documents/slides.pptx" -t plain --wrap=none -o "documents/slides.txt"
"$pandoc" "documents/sheet.xlsx" -t plain --wrap=none -o "documents/sheet.txt"
```

If a configured binary path fails, say which binary failed and ask the user to run `faltoobot configure` → `Document binaries`.

## Direct read vs spawn

Use direct read only when the task is narrow and the needed text is small enough to inspect safely.

Spawn a sub-agent when any of these are true:

- metadata says `Pages` is more than 30 and the user asks summary, inspect, analysis, risks, financials, comparison, action items, or any broad question
- extracted `.txt` is large enough to risk context exhaustion
- answering requires reading the whole document

For large documents, do not paste/read the whole `$txt` in the main session. Spawn instead.

## Spawn command

Replace `{chat_key}`, `$txt`, and the query text before running:

```bash
query_file="$(mktemp)"
cat > "$query_file" <<'QUERY'
paste the user query here
QUERY
nohup sh -c 'faltoochat "Read documents/file.txt and answer the user query from this file only. User query: $(cat "$1")" --workspace=. --new-session 2>&1 | faltoobot notify "{chat_key}" --source="document-reader:documents/file.txt"' sh "$query_file" &
```

Tell the user that the document is large and a document-reader session has been started.

## Answer rule

Mention the file name used. Answer only the user query from that file. If there is no concrete question, ask what the user wants from the file.
