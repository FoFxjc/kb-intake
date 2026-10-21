"""A local OpenAI-compatible chat server for the LLM flow tests (127.0.0.1, a real HTTP round trip): answers a
summary, a description or a chart table depending on the prompt, counts requests, or fails every request."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TABLE = {"title": "Revenue", "columns": ["Year", "SGD m"], "rows": [["2024", 10.5], ["2025", 12]], "units": "SGD m",
         "notes": "read from the bars"}


class FakeLlm:
    def __init__(self, status: int = 200):
        self.status, self.requests, self.keys = status, [], set()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append(body)
                fake.keys.add(self.headers.get("Authorization"))
                if fake.status != 200:
                    self.send_response(fake.status)
                    self.end_headers()
                    return
                prompt = body["messages"][0]["content"][-1]["text"]
                if "JSON object" in prompt:
                    text = "```json\n" + json.dumps(TABLE) + "\n```"
                elif "page of a PDF" in prompt:
                    text = "<think>looking</think>A bar chart of revenue by year, in SGD m."
                else:
                    text = "An annual report: business review first, financial statements later."
                out = json.dumps({"choices": [{"message": {"content": text}}], "model": body["model"],
                                  "usage": {"total_tokens": 42}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
