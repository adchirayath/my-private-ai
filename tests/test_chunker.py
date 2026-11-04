from services.chunker import split_text


def test_split_text_empty():
    assert split_text("") == []
    assert split_text("   ") == []


def test_split_text_short():
    short_text = "This is a short document that easily fits in one chunk."
    chunks = split_text(short_text, chunk_size=1200)
    assert len(chunks) == 1
    assert chunks[0] == short_text


def test_split_text_paragraph_break():
    p1 = "Paragraph 1 with important information. " * 15
    p2 = "Paragraph 2 with separate information. " * 15
    full_text = f"{p1}\n\n{p2}"

    chunks = split_text(full_text, chunk_size=len(p1) + 50, overlap=50)
    assert len(chunks) >= 2
    # Verify paragraph separation is respected
    assert "Paragraph 1" in chunks[0]


def test_split_text_respects_sentence_boundary():
    sentences = [f"Sentence number {i} is here." for i in range(50)]
    full_text = " ".join(sentences)
    chunks = split_text(full_text, chunk_size=300, overlap=50)

    for chunk in chunks:
        # Chunks shouldn't end with awkward mid-word slices
        assert not chunk.endswith("Sente")
        assert not chunk.endswith("num")

