"""
services/chunker.py - Intelligent Text Chunking for RAG
-------------------------------------------------------
Why do we chunk text?
AI models have context limits, and search engines work best when matching
specific, focused paragraphs rather than giant 100-page files.

Why do we use overlap?
Overlap (e.g., 180 characters) ensures that ideas, names, and sentences that
happen to cross a chunk boundary aren't accidentally cut in half.
"""

import re

def split_text(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 180,
) -> list[str]:
    """
    Split long document text into overlapping chunks.
    Prioritizes natural boundaries:
      1. Paragraph breaks (\n\n)
      2. Line breaks (\n)
      3. Sentence endings (. ! ?)
      4. Word spaces (' ')
    """
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        if len(text) - start <= chunk_size:
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break

        target_end = start + chunk_size
        # Search backwards from target_end for natural break boundaries
        search_window = text[max(start, target_end - 250):target_end]

        break_pos = -1
        # 1. Try paragraph break
        rel_pos = search_window.rfind("\n\n")
        if rel_pos != -1:
            break_pos = max(start, target_end - 250) + rel_pos + 2
        else:
            # 2. Try newline
            rel_pos = search_window.rfind("\n")
            if rel_pos != -1:
                break_pos = max(start, target_end - 250) + rel_pos + 1
            else:
                # 3. Try sentence end (. ! ? followed by space and capital or digit)
                sentence_match = re.search(r"[.!?]\s+[A-Z0-9]", search_window)
                if sentence_match:
                    break_pos = max(start, target_end - 250) + sentence_match.start() + 1
                else:
                    # 4. Try word boundary
                    rel_pos = search_window.rfind(" ")
                    if rel_pos != -1:
                        break_pos = max(start, target_end - 250) + rel_pos + 1

        if break_pos <= start or break_pos > target_end:
            break_pos = target_end

        chunk = text[start:break_pos].strip()
        if chunk:
            chunks.append(chunk)

        # Move forward, stepping back by overlap
        start = max(break_pos - overlap, start + 1)

    return chunks

