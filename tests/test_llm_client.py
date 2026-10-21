"""llm_client.chat against a real local HTTP server on 127.0.0.1, scripted per test."""

import json
import socket
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from kb_intake import llm_client
from kb_intake.llm_client import Completion, LlmError, chat

KEY = "sk-SENTINEL"
CONTENT = [{"type": "text", "text": "Describe the chart."}]


def ok(text, usage=None, model="MiniMax-M3"):
    body = {"model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}]}
    if usage is not None:
        body["usage"] = usage
    return 200, body


def base_resp(code, msg="error"):
    return 200, {"base_resp": {"status_code": code, "status_msg": msg}}


class Server:
    """Answers each POST with the next scripted (status, body[, delay]); the last entry repeats."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                server.requests.append({"path": self.path, "headers": dict(self.headers),
                                        "body": self.rfile.read(n)})
                i = min(len(server.requests), len(server.script)) - 1
                status, body, *delay = server.script[i]
                if delay:
                    time.sleep(delay[0])
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client gave up (timeout test)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.httpd.block_on_close = False  # do not wait for a deliberately slow handler
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/"
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.01,), daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Record backoff waits instead of sleeping."""
    waits = []
    monkeypatch.setattr(llm_client, "_sleep", waits.append)
    return waits


def test_success_returns_text_usage_and_model():
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    with Server([ok("  A bar chart.  ", usage)]) as s:
        c = chat(s.url, "MiniMax-M3", KEY, CONTENT)
    assert c == Completion(text="A bar chart.", usage=usage, model="MiniMax-M3")
    assert len(s.requests) == 1


def test_usage_missing_is_empty_dict():
    with Server([ok("x")]) as s:
        assert chat(s.url, "m", KEY, CONTENT).usage == {}


def test_request_body_and_headers():
    with Server([ok("x")]) as s:
        chat(s.url, "MiniMax-M3", KEY, CONTENT, max_tokens=321)
    req = s.requests[0]
    assert req["path"] == "/v1/chat/completions"
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["content-type"] == "application/json"
    assert json.loads(req["body"]) == {"model": "MiniMax-M3",
                                       "messages": [{"role": "user", "content": CONTENT}],
                                       "max_completion_tokens": 321}


def test_reasoning_is_stripped():
    text = "<think>step 1\nstep 2</think>\nLine chart of revenue.<think>more</think> Units: SGD m."
    with Server([ok(text)]) as s:
        assert chat(s.url, "m", KEY, CONTENT).text == "Line chart of revenue. Units: SGD m."


def test_retries_503_then_succeeds(no_sleep):
    with Server([(503, {}), (503, {}), ok("done")]) as s:
        assert chat(s.url, "m", KEY, CONTENT).text == "done"
    assert len(s.requests) == 3
    assert no_sleep == [1.0, 4.0]


def test_503_always_fails_after_three_requests():
    with Server([(503, {})]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT)
    assert e.value.code == "http_503"
    assert len(s.requests) == 3


def test_400_is_not_retried():
    with Server([(400, {"error": "bad"})]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT)
    assert e.value.code == "http_400"
    assert len(s.requests) == 1


def test_minimax_rate_limit_is_retried():
    with Server([base_resp(1002), ok("fine")]) as s:
        assert chat(s.url, "m", KEY, CONTENT).text == "fine"
    assert len(s.requests) == 2


@pytest.mark.parametrize("status, code, requests", [
    (1008, "quota", 1), (2056, "quota", 1),
    (1002, "rate_limit", 3), (2045, "rate_limit", 1), (1041, "rate_limit", 1),
    (1026, "refused", 1), (1027, "refused", 1),
    (1004, "auth", 1), (2049, "auth", 1),
    (1001, "timeout", 3),
])
def test_base_resp_mapping(status, code, requests):
    with Server([base_resp(status)]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT)
    assert e.value.code == code
    assert len(s.requests) == requests


def test_base_resp_zero_with_content_is_success():
    status, body = ok("fine")
    body["base_resp"] = {"status_code": 0, "status_msg": "success"}
    with Server([(status, body)]) as s:
        assert chat(s.url, "m", KEY, CONTENT).text == "fine"


def test_slow_server_times_out():
    with Server([(*ok("late"), 1.0)]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT, timeout=0.2, retries=0)
    assert e.value.code == "timeout"


@pytest.mark.parametrize("answer", ["", "   ", "<think>only reasoning</think>", None])
def test_empty_content(answer):
    with Server([ok(answer)]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT)
    assert e.value.code == "empty"
    assert len(s.requests) == 1


@pytest.mark.parametrize("body", [b"not json", {"choices": []}, {"choices": [{"message": {"content": 3}}]},
                                  base_resp(9999)[1]])
def test_bad_response(body):
    with Server([(200, body)]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "m", KEY, CONTENT)
    assert e.value.code == "bad_response"


def closed_port_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/v1"


def test_connection_refused_is_network_and_retried(no_sleep):
    with pytest.raises(LlmError) as e:
        chat(closed_port_url(), "m", KEY, CONTENT)
    assert e.value.code == "network"
    assert no_sleep == [1.0, 4.0]


def chain_text(e: BaseException) -> str:
    """str and repr of the error and of every exception reachable through __cause__ / __context__, plus the
    formatted traceback."""
    parts, seen, todo = [], set(), [e]
    while todo:
        x = todo.pop()
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        parts += [str(x), repr(x), repr(getattr(x, "args", ()))]
        todo += [x.__cause__, x.__context__]
    parts.append("".join(traceback.format_exception(e)))
    return "\n".join(parts)


def error_cases():
    """(script or None for a closed port, kwargs, expected code) covering every error kind."""
    return [
        ([(*ok("late"), 1.0)], {"timeout": 0.2, "retries": 0}, "timeout"),
        (None, {}, "network"),
        ([(503, {"echo": KEY})], {}, "http_503"),
        ([(401, {"echo": KEY})], {}, "http_401"),
        ([base_resp(1008, KEY)], {}, "quota"),
        ([base_resp(2045, KEY)], {}, "rate_limit"),
        ([base_resp(1026, KEY)], {}, "refused"),
        ([base_resp(1004, f"invalid key {KEY}")], {}, "auth"),
        ([base_resp(1001, KEY)], {}, "timeout"),
        ([(200, f"not json {KEY}".encode())], {}, "bad_response"),
        ([ok("<think>" + KEY + "</think>")], {}, "empty"),
    ]


def test_errors_never_carry_the_key():
    seen = set()
    for script, kwargs, code in error_cases():
        if script is None:
            with pytest.raises(LlmError) as e:
                chat(closed_port_url(), "m", KEY, CONTENT, **kwargs)
        else:
            with Server(script) as s, pytest.raises(LlmError) as e:
                chat(s.url, "m", KEY, CONTENT, **kwargs)
        assert e.value.code == code
        assert KEY not in chain_text(e.value), code
        seen.add(code)
    assert seen >= {"timeout", "network", "http_503", "quota", "rate_limit", "refused", "auth", "bad_response",
                    "empty"}


def test_opener_is_injectable():
    calls = []

    def opener(req, timeout):
        calls.append((req.full_url, timeout))
        raise TimeoutError("simulated")

    with pytest.raises(LlmError) as e:
        chat("http://example.invalid/v1", "m", KEY, CONTENT, timeout=7.0, retries=1, opener=opener)
    assert e.value.code == "timeout"
    assert calls == [("http://example.invalid/v1/chat/completions", 7.0)] * 2


def test_an_answer_cut_at_the_token_limit_is_truncated():
    """OCBC keyed run 1: a large diagram came back empty; an answer cut by max_completion_tokens must not pass as
    complete (finish_reason "length"), and is not retried."""
    cut = (200, {"choices": [{"message": {"content": "A flow chart of the"}, "finish_reason": "length"}]})
    with Server([cut]) as s:
        with pytest.raises(LlmError) as e:
            chat(s.url, "M", KEY, CONTENT)
    assert e.value.code == "truncated" and len(s.requests) == 1


def test_a_broken_answer_stream_is_a_network_error():
    import http.client

    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"partial")
    with pytest.raises(LlmError) as e:
        chat("http://127.0.0.1:9/v1", "m", KEY, CONTENT, retries=0, opener=lambda req, timeout: Broken())
    assert e.value.code == "network"


def test_a_key_that_cannot_be_sent_is_auth_and_never_shown():
    key = "“sk-SENTINEL”"
    with pytest.raises(LlmError) as e:
        chat("http://127.0.0.1:9/v1", "m", key, CONTENT, retries=0)
    assert e.value.code == "auth" and "sk-SENTINEL" not in str(e.value) + repr(e.value)
    assert e.value.__cause__ is None and e.value.__context__ is None
