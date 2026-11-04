from services.rag import BM25Index, is_greeting_or_smalltalk


def test_is_greeting_or_smalltalk():
    # Greetings should be detected
    assert is_greeting_or_smalltalk("hi") is True
    assert is_greeting_or_smalltalk("Hi!") is True
    assert is_greeting_or_smalltalk("hello there") is True
    assert is_greeting_or_smalltalk("Good morning") is True
    assert is_greeting_or_smalltalk("how are you?") is True
    assert is_greeting_or_smalltalk("thanks") is True
    assert is_greeting_or_smalltalk("yes") is True
    assert is_greeting_or_smalltalk("no") is True
    assert is_greeting_or_smalltalk("ok") is True
    assert is_greeting_or_smalltalk("sure") is True

    # Real queries should NOT be detected as greetings
    assert is_greeting_or_smalltalk("What is the project budget?") is False
    assert is_greeting_or_smalltalk("Who is Priya Sharma?") is False
    assert is_greeting_or_smalltalk("hi, what is the deadline?") is False
    assert is_greeting_or_smalltalk("") is False


def test_bm25_empty():
    bm25 = BM25Index()
    bm25.fit([])
    assert bm25.get_scores("query") == []


def test_bm25_keyword_ranking():
    docs = [
        "The project manager is Priya Sharma and the budget is INR 150000.",
        "General office supplies include pens, notebooks, and paper clips.",
        "Software architecture uses FastAPI and ChromaDB vector store.",
    ]
    bm25 = BM25Index()
    bm25.fit(docs)

    scores = bm25.get_scores("Priya Sharma budget 150000")
    # First document should score highest
    assert scores[0] > scores[1]
    assert scores[0] > scores[2]


def test_bm25_non_matching_query():
    docs = ["Apple banana orange", "Dog cat elephant"]
    bm25 = BM25Index()
    bm25.fit(docs)

    scores = bm25.get_scores("completely unrelated query terms")
    assert all(s == 0.0 for s in scores)

