"""Phase 3.3: unit tests for the real (non-stub) built-in tools."""

import json

import pytest

from w2a.templates.builtin_tools import (
    http_get,
    parse_csv,
    read_file,
    send_message,
    write_file,
    write_markdown_report,
)


def test_read_file(tmp_path):
    p = tmp_path / "in.txt"
    p.write_text("hello world", encoding="utf-8")
    assert read_file.run(path=str(p)) == "hello world"


def test_write_file_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "out.txt"
    result = write_file.run(path=str(p), content="content here")
    assert result == str(p)
    assert p.read_text(encoding="utf-8") == "content here"


def test_http_get(monkeypatch):
    class _FakeResponse:
        text = "response body"

        def raise_for_status(self):
            return None

    captured = {}

    def _fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr("w2a.templates.builtin_tools.requests.get", _fake_get)
    result = http_get.run(url="https://example.com/data")
    assert result == "response body"
    assert captured["url"] == "https://example.com/data"


def test_http_get_raises_on_error_status(monkeypatch):
    class _FakeResponse:
        text = "not found"

        def raise_for_status(self):
            raise Exception("404")

    monkeypatch.setattr("w2a.templates.builtin_tools.requests.get", lambda url, timeout: _FakeResponse())
    with pytest.raises(Exception):
        http_get.run(url="https://example.com/missing")


def test_parse_csv(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
    rows = parse_csv.run(path=str(p))
    assert rows == [{"name": "Alice", "age": "30"}, {"name": "Bob", "age": "25"}]


def test_write_markdown_report(tmp_path):
    p = tmp_path / "report.md"
    result = write_markdown_report.run(
        path=str(p),
        title="Weekly Status",
        sections={"Shipped": "Feature X", "Blocked": "None"},
    )
    assert result == str(p)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("# Weekly Status\n")
    assert "## Shipped" in text
    assert "Feature X" in text
    assert "## Blocked" in text


def test_send_message_writes_outbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = send_message.run(channel="on-call", message="ticket #42 is urgent")
    assert result == "sent to on-call (outbox/on-call.jsonl)"

    outbox_file = tmp_path / "outbox" / "on-call.jsonl"
    assert outbox_file.exists()
    record = json.loads(outbox_file.read_text(encoding="utf-8").strip())
    assert record["channel"] == "on-call"
    assert record["message"] == "ticket #42 is urgent"


def test_send_message_appends_multiple(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    send_message.run(channel="team", message="first")
    send_message.run(channel="team", message="second")
    lines = (tmp_path / "outbox" / "team.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "first"
    assert json.loads(lines[1])["message"] == "second"


def test_send_message_sanitizes_channel_for_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    send_message.run(channel="team/urgent", message="hi")
    assert (tmp_path / "outbox" / "team_urgent.jsonl").exists()
