#!/usr/bin/env python3
"""Optional Omarchy desktop alerts for unread Rakazo bot replies (Python stdlib only)."""

import argparse
import http.cookiejar
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Redirect refused; use the final Rakazo origin")


def private_json(path):
    if path.stat().st_mode & 0o077:
        raise ValueError("Private JSON files must have mode 0600")
    return json.loads(path.read_text())


class Client:
    def __init__(self, url, login):
        origin = urllib.parse.urlsplit(url)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
            or (origin.scheme == "http" and origin.hostname not in ("localhost", "127.0.0.1", "::1"))
        ):
            raise ValueError("Use an HTTPS origin, or HTTP on loopback, without credentials or a path")
        self.url = url.rstrip("/")
        self.http = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirects(),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        self.request("/api/auth/sign-in/email", {
            "email": login["email"], "password": login["password"],
        })

    def request(self, path, payload, space=None):
        headers = {"Content-Type": "application/json", "Origin": self.url}
        if space:
            headers["x-rakazo-space-id"] = space
        request = urllib.request.Request(self.url + path, json.dumps(payload).encode(), headers)
        try:
            with self.http.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Auth error bodies may echo credentials; never include them in logs.
            raise RuntimeError(f"Request failed: HTTP {error.code}; response body omitted") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Connection failed; check the Rakazo origin and network") from None

    def rpc(self, procedure, value=None, space=None):
        result = self.request("/rpc/" + procedure, {"json": value or {}}, space)
        if not isinstance(result, dict) or "json" not in result:
            raise RuntimeError("Invalid RPC response")
        return result["json"]


def unread_replies(client, spaces):
    replies = {}
    for space in spaces:
        for bot in space["bots"]:
            if not bot["unread"]:
                continue
            target = {"botId": bot["id"]}
            while True:
                page = client.rpc("threads/messages", target, space=space["id"])
                messages = [message for message in page["messages"] if message["role"] == "bot"]
                if messages:
                    latest = max(messages, key=lambda message: message["seq"])
                    replies[page["threadId"]] = {"id": latest["id"], "name": bot["name"]}
                    break
                before = page["olderCursor"]
                if before is None:
                    break
                if "before" in target and before >= target["before"]:
                    raise RuntimeError("Message pagination did not advance")
                target = {"botId": bot["id"], "before": before}
    return replies


def announce(replies, previous, notify):
    pending = [reply for thread, reply in replies.items() if previous.get(thread) != reply["id"]]
    if pending:
        names = ", ".join(dict.fromkeys(reply["name"] for reply in pending))
        notify(names)
    return {thread: reply["id"] for thread, reply in replies.items()}


def desktop_notice(names, url):
    subprocess.run([
        "omarchy-notification-send", "--app-name=Rakazo", "--icon=mail-unread",
        "--urgency=critical", "--expire-time=0", "Rakazo: unread replies",
        escape(names) + "\nReplies are waiting. Click to open Rakazo.",
        "--exec", "omarchy-launch-or-focus-webapp", "Rakazo", url,
    ], check=True, timeout=10)


def save_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix=".unread-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(state, output)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Rakazo web origin, not the internal API port")
    parser.add_argument("--login-file", required=True, type=Path, help="0600 JSON with email and password")
    parser.add_argument("--state-file", required=True, type=Path, help="Private state file for this account/origin")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    client = Client(args.url, private_json(args.login_file))
    previous = private_json(args.state_file) if args.state_file.exists() else {}
    while True:
        navigation = client.rpc("spaces/list")
        replies = unread_replies(client, navigation["spaces"])
        current = announce(replies, previous, lambda names: desktop_notice(names, client.url))
        if current != previous:
            save_state(args.state_file, current)
        previous = current
        if args.once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
