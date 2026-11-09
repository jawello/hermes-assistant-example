import base64

from gmail_mcp.mime import extract_text, find_part, list_attachments


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_reads_a_single_part_plain_body():
    payload = {"mimeType": "text/plain", "body": {"data": b64("hello")}}
    assert extract_text(payload) == "hello"


def test_prefers_plain_text_nested_below_an_alternative():
    # multipart/mixed → multipart/alternative → [plain, html] is what Gmail
    # actually sends for a normal message with an attachment.
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": b64("the words")}},
                    {"mimeType": "text/html", "body": {"data": b64("<p>the words</p>")}},
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "a1"}},
        ],
    }
    assert extract_text(payload) == "the words"


def test_falls_back_to_html_with_tags_stripped():
    payload = {
        "mimeType": "text/html",
        "body": {"data": b64("<p>Invoice <b>4200</b> is due</p>")},
    }
    assert extract_text(payload) == "Invoice 4200 is due"


def test_html_fallback_drops_script_and_style_content():
    # Marketing mail is mostly CSS. Without this the "body" handed to the
    # model is a stylesheet.
    html = "<style>.a{color:red}</style><script>var x=1</script><p>Real text</p>"
    payload = {"mimeType": "text/html", "body": {"data": b64(html)}}
    assert extract_text(payload) == "Real text"


def test_html_entities_are_unescaped():
    payload = {"mimeType": "text/html", "body": {"data": b64("<p>a &amp; b&nbsp;c</p>")}}
    assert extract_text(payload) == "a & b c"


def test_attachment_only_message_yields_empty_string():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "application/pdf", "body": {"attachmentId": "a1"}}],
    }
    assert extract_text(payload) == ""


def test_undecodable_bytes_do_not_raise():
    # Real mail carries mislabelled charsets. Losing a character is fine;
    # crashing the poller on one message is not.
    payload = {
        "mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(b"\xff\xfeok").decode("ascii")},
    }
    assert "ok" in extract_text(payload)


def test_output_is_truncated_to_the_limit():
    payload = {"mimeType": "text/plain", "body": {"data": b64("x" * 100)}}
    assert len(extract_text(payload, limit=10)) == 10


def test_parts_below_the_depth_limit_are_ignored():
    # The depth limit's observable effect: content deeper than depth 20 is not
    # extracted. Build a tree with text at depth 5 (included) and text at
    # depth 25 (excluded). Removing the guard makes this test fail.

    # Create a text part at depth 5
    at_depth_5 = {"mimeType": "text/plain", "body": {"data": b64("marker_depth_5")}}
    current_5 = at_depth_5
    for _ in range(4):  # Wrap 4 times to reach depth 5
        current_5 = {"mimeType": "multipart/mixed", "parts": [current_5]}

    # Create a text part at depth 25
    at_depth_25 = {"mimeType": "text/plain", "body": {"data": b64("marker_depth_25")}}
    current_25 = at_depth_25
    for _ in range(24):  # Wrap 24 times to reach depth 25
        current_25 = {"mimeType": "multipart/mixed", "parts": [current_25]}

    # Build a root with both paths
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [current_5, current_25],
    }

    result = extract_text(payload)
    # Depth 5 should be included, depth 25 should be excluded
    assert "marker_depth_5" in result
    assert "marker_depth_25" not in result


def test_html_comment_with_embedded_angle_bracket_is_stripped():
    # <[^>]+> stops at the first >, so <!-- price > 100 --> leaks " 100 -->"
    payload = {
        "mimeType": "text/html",
        "body": {"data": b64("<p>a</p><!-- price > 100 --><p>b</p>")},
    }
    text = extract_text(payload)
    assert "100" not in text
    assert "-->" not in text
    assert "a" in text and "b" in text


def test_outlook_conditional_comment_is_removed():
    # Outlook conditional comments are common in marketing mail.
    html = "<p>start</p><!--[if mso]><p>IE only</p><![endif]--><p>end</p>"
    payload = {"mimeType": "text/html", "body": {"data": b64(html)}}
    text = extract_text(payload)
    assert "IE only" not in text
    assert "start" in text and "end" in text


def test_windows_1251_body_with_charset_header_decodes_correctly():
    # Windows-1251 is common in Russian mail. Gmail provides charset in headers.
    # Cyrillic "Привет" (hello) in windows-1251: bytes and charset declaration.
    cyrillic_bytes = "Привет".encode("windows-1251")
    cyrillic_b64 = base64.urlsafe_b64encode(cyrillic_bytes).decode("ascii")
    payload = {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": "text/plain; charset=\"windows-1251\""}],
        "body": {"data": cyrillic_b64},
    }
    text = extract_text(payload)
    assert "Привет" in text


def test_unknown_charset_falls_back_to_utf8_without_raising():
    # Unknown charset should not raise; fall back to UTF-8.
    payload = {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": "text/plain; charset=\"x-nonsense\""}],
        "body": {"data": b64("hello")},
    }
    assert extract_text(payload) == "hello"


def test_missing_content_type_header_decodes_as_utf8():
    # Without a Content-Type header, should still decode UTF-8 as before.
    payload = {"mimeType": "text/plain", "body": {"data": b64("hello")}}
    assert extract_text(payload) == "hello"


MIXED = {
    "partId": "",
    "mimeType": "multipart/mixed",
    "parts": [
        {
            "partId": "0",
            "mimeType": "multipart/related",
            "parts": [
                {"partId": "0.0", "mimeType": "text/html", "body": {"data": b64("<img src='cid:logo'>")}},
                {
                    "partId": "0.1",
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "headers": [{"name": "Content-ID", "value": "<logo>"}],
                    "body": {"attachmentId": "A0", "size": 900},
                },
            ],
        },
        {
            "partId": "1",
            "mimeType": "application/pdf",
            "filename": "invoice.pdf",
            "headers": [{"name": "Content-Disposition", "value": "inline; filename=invoice.pdf"}],
            "body": {"attachmentId": "A1", "size": 184213},
        },
        {"partId": "2", "mimeType": "image/jpeg", "filename": "photo.jpg", "body": {"attachmentId": "A2", "size": 5000}},
    ],
}


def test_lists_real_attachments_and_skips_cid_images():
    # The logo is pulled into the HTML via cid:, nobody sent it. The PDF is
    # marked inline the way Apple Mail sends real attachments, and stays.
    assert list_attachments(MIXED) == [
        {"part_id": "1", "filename": "invoice.pdf", "mime_type": "application/pdf", "size": 184213},
        {"part_id": "2", "filename": "photo.jpg", "mime_type": "image/jpeg", "size": 5000},
    ]


def test_a_message_without_files_has_no_attachments():
    assert list_attachments({"mimeType": "text/plain", "body": {"data": b64("hi")}}) == []


def test_find_part_walks_the_tree():
    assert find_part(MIXED, "0.1")["filename"] == "logo.png"
    assert find_part(MIXED, "7") is None
