"""Real, working CrewAI tools — not stubs. The closed tool registry (Phase 4)
resolves any spec ToolSpec that matches one of these by name/purpose to the
real implementation instead of an emitted MOCK_MODE stub; everything else
becomes an explicit stub with a TODO (see ``_common/tools.py.j2``).

``send_message`` is the zero-cost stand-in for Slack/email: it appends a JSON
line to an ``outbox/`` folder instead of calling a real API, so generated
crews can "send" things end to end without any paid service or key.
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import requests
from crewai.tools import tool


@tool
def read_file(path: str) -> str:
    """Read and return the full text contents of a file at the given path."""
    return Path(path).read_text(encoding="utf-8")


@tool
def write_file(path: str, content: str) -> str:
    """Write text content to a file at the given path, creating parent directories as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return str(target)


@tool
def http_get(url: str) -> str:
    """Fetch a URL with an HTTP GET request and return the response body as text."""
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text


@tool
def parse_csv(path: str) -> list[dict[str, str]]:
    """Parse a CSV file at the given path into a list of row dicts keyed by header."""
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@tool
def write_markdown_report(path: str, title: str, sections: dict[str, str]) -> str:
    """Write a markdown report with a title and named sections to the given path."""
    buf = io.StringIO()
    buf.write(f"# {title}\n\n")
    for heading, body in sections.items():
        buf.write(f"## {heading}\n\n{body}\n\n")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(buf.getvalue(), encoding="utf-8")
    return str(target)


@tool
def send_message(channel: str, message: str) -> str:
    """Send a message to a channel; writes to outbox/ as a zero-cost stand-in for Slack/email."""
    outbox = Path("outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "channel": channel, "message": message}
    target = outbox / f"{channel.replace('/', '_')}.jsonl"
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return f"sent to {channel} (outbox/{target.name})"
