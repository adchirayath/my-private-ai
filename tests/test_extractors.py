from services.extractors import decode_text, extract_text


def test_decode_text_utf8():
    data = "Hello World! 🚀".encode("utf-8")
    assert decode_text(data) == "Hello World! 🚀"


def test_decode_text_cp1252():
    data = "Special characters: — “ ”".encode("cp1252")
    decoded = decode_text(data)
    assert "Special characters" in decoded


def test_extract_text_json():
    json_data = '{"name": "Local Coworker", "active": true}'.encode("utf-8")
    extracted = extract_text("config.json", json_data)
    assert '"name": "Local Coworker"' in extracted


def test_extract_text_html():
    html_data = "<html><head><style>body { color: red; }</style></head><body><h1>Document Title</h1><p>Main content text.</p></body></html>".encode("utf-8")
    extracted = extract_text("sample.html", html_data)
    assert "Document Title" in extracted
    assert "Main content text." in extracted
    assert "color: red" not in extracted

