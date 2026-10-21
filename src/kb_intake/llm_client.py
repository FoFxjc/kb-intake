"""One OpenAI-compatible chat completion over urllib (intake-ui-llm-design-v1 §4). Standard library only.

`chat` POSTs `{base_url}/chat/completions` and returns the answer text with every `<think>...</think>` block
removed. A redirect is refused, never followed (the key would go with it). Timeouts, connection errors, HTTP
429/500/502/503/504 and MiniMax `base_resp` 1001/1002 are retried (`retries` extra attempts, waiting 1 s then 4 s);
every other failure is raised at once. Every failure is an
`LlmError` whose `.code` names the kind; its message is built here from the status alone, never from the request,
its headers or the response body, and it is raised outside any `except` block, so it has no __cause__ or
__context__ through which the API key could leak.
"""

import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

BACKOFF = (1.0, 4.0)  # seconds before the 2nd and 3rd attempt; later attempts reuse the last value
RETRY_HTTP = {429, 500, 502, 503, 504}
# MiniMax reports errors as HTTP 200 with base_resp.status_code != 0.
BASE_RESP = {1008: "quota", 2056: "quota", 1002: "rate_limit", 2045: "rate_limit", 1041: "rate_limit",
             1026: "refused", 1027: "refused", 1004: "auth", 2049: "auth", 1001: "timeout"}
RETRY_BASE_RESP = {1001, 1002}
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)

_sleep = time.sleep  # tests replace this so backoff does not wait


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would follow a redirect with the same headers, sending the key to the host it names: refuse it (the
    3xx comes back as an HTTPError, not retried)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class LlmError(Exception):
    """A failed call; `.code` is one of timeout, network, http_<status>, quota, rate_limit, refused, auth,
    bad_response, empty, truncated (the answer was cut at max_completion_tokens)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def __repr__(self) -> str:
        return f"LlmError({self.code!r}, {str(self)!r})"


@dataclass(frozen=True)
class Completion:
    text: str
    usage: dict = field(default_factory=dict)
    model: str = ""


def chat(base_url: str, model: str, key: str, content: list[dict], *, max_tokens: int = 2048,
         timeout: float = 120.0, retries: int = 2, opener=None) -> Completion:
    """One user message with `content` (OpenAI content parts); raises LlmError. `opener` is for tests."""
    opener = opener or _OPENER.open
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                       "max_completion_tokens": max_tokens}).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if not key.isascii():                      # urllib would raise UnicodeEncodeError carrying the key in .object
        raise LlmError("auth", "the key contains characters that cannot be sent (check for quotes or spaces)")
    error = None
    for attempt in range(retries + 1):
        if attempt:
            _sleep(BACKOFF[min(attempt, len(BACKOFF)) - 1])
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        result = _attempt(opener, req, timeout, model)
        if isinstance(result, Completion):
            return result
        error, retry = result
        if not retry:
            break
    raise error


def _attempt(opener, req, timeout: float, model: str) -> "Completion | tuple[LlmError, bool]":
    """One request: the completion, or (error, retryable). Errors are built after leaving the except blocks so
    they carry no __context__ from urllib."""
    err = None
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        e.close()
        err = (LlmError(f"http_{e.code}", f"HTTP {e.code}"), e.code in RETRY_HTTP)
    except (TimeoutError, socket.timeout):
        err = (LlmError("timeout", f"no answer within {timeout:g} s"), True)
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            err = (LlmError("timeout", f"no answer within {timeout:g} s"), True)
        else:
            err = (LlmError("network", f"connection failed ({type(e.reason).__name__})"), True)
    except (OSError, http.client.HTTPException) as e:  # sockets; a cut or malformed answer (IncompleteRead, ...)
        err = (LlmError("network", f"connection failed ({type(e).__name__})"), True)
    if err:
        return err
    return _parse(raw, model)


def _parse(raw: bytes, model: str) -> "Completion | tuple[LlmError, bool]":
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return LlmError("bad_response", "the answer is not a JSON object"), False
    base = data.get("base_resp")
    status = base.get("status_code") if isinstance(base, dict) else None
    if status not in (None, 0):
        return LlmError(BASE_RESP.get(status, "bad_response"), f"provider status {status}"), status in RETRY_BASE_RESP
    choices = data.get("choices")
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) \
        else None
    if not isinstance(message, dict):
        return LlmError("bad_response", "the answer has no choices[0].message"), False
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        return LlmError("bad_response", "the answer content is not a string"), False
    if choices[0].get("finish_reason") == "length":       # cut by max_completion_tokens: incomplete, not an answer
        return LlmError("truncated", "the answer was cut at the token limit"), False
    text = _THINK.sub("", content or "").strip()
    if not text:
        return LlmError("empty", "the answer is empty"), False
    usage = data.get("usage")
    name = data.get("model")
    return Completion(text=text, usage=usage if isinstance(usage, dict) else {},
                      model=name if isinstance(name, str) and name else model)
