from ui.sse_client import SSEEvent, parse_sse


def test_parses_named_events_with_json_data() -> None:
    stream = (
        'event: meta\ndata: {"conversation_id": "c1"}\n\nevent: token\ndata: {"text": "Deux"}\n\n'
    )
    assert list(parse_sse(stream.split("\n"))) == [
        SSEEvent("meta", {"conversation_id": "c1"}),
        SSEEvent("token", {"text": "Deux"}),
    ]


def test_comments_and_crlf_are_ignored() -> None:
    lines = [": keep-alive", "", "event: done\r", 'data: {"latency_ms": 12}\r', "\r"]
    assert list(parse_sse(lines)) == [SSEEvent("done", {"latency_ms": 12})]


def test_unicode_and_last_event_without_blank_line() -> None:
    lines = ["event: token", 'data: {"text": "période d\'essai"}']
    assert list(parse_sse(lines)) == [SSEEvent("token", {"text": "période d'essai"})]


def test_default_event_name_is_message() -> None:
    assert list(parse_sse(["data: [1, 2]", ""])) == [SSEEvent("message", [1, 2])]
