import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from notify_unread_omarchy import Client, announce, private_json, save_state, unread_replies


class UnreadNotificationsTests(unittest.TestCase):
    def test_new_reply_is_announced_once_and_later_reply_announces_again(self):
        notify = Mock()
        replies = {"thread": {"id": "reply-1", "name": "Agent"}}
        state = announce(replies, {}, notify)
        self.assertEqual(state, {"thread": "reply-1"})
        notify.assert_called_once_with("Agent")
        notify.reset_mock()
        self.assertEqual(announce(replies, state, notify), state)
        notify.assert_not_called()
        replies["thread"]["id"] = "reply-2"
        self.assertEqual(announce(replies, state, notify), {"thread": "reply-2"})
        notify.assert_called_once_with("Agent")

    def test_read_thread_clears_state_without_notification(self):
        notify = Mock()
        self.assertEqual(announce({}, {"thread": "reply"}, notify), {})
        notify.assert_not_called()

    def test_failed_delivery_does_not_acknowledge_unseen_reply(self):
        previous = {"old-thread": "old-reply"}
        with self.assertRaises(RuntimeError):
            announce({"new-thread": {"id": "new-reply", "name": "Agent"}}, previous,
                     Mock(side_effect=RuntimeError("notification unavailable")))
        self.assertEqual(previous, {"old-thread": "old-reply"})

    def test_only_latest_agent_reply_in_unread_threads_is_selected(self):
        client = Mock()
        client.rpc.return_value = {"threadId": "thread", "messages": [
            {"id": "agent-new", "role": "bot", "seq": 4},
            {"id": "human", "role": "user", "seq": 5},
            {"id": "agent-old", "role": "bot", "seq": 2},
        ], "olderCursor": None}
        spaces = [{"id": "space", "bots": [
            {"id": "agent", "name": "Agent", "unread": True},
            {"id": "read-agent", "name": "Read agent", "unread": False},
        ]}]
        self.assertEqual(unread_replies(client, spaces),
                         {"thread": {"id": "agent-new", "name": "Agent"}})
        self.assertEqual(client.rpc.call_count, 1)

    def test_finds_reply_before_a_page_of_user_messages(self):
        client = Mock()
        client.rpc.side_effect = [
            {"threadId": "thread", "messages": [{"id": "human", "role": "user", "seq": 9}],
             "olderCursor": 9},
            {"threadId": "thread", "messages": [{"id": "reply", "role": "bot", "seq": 8}],
             "olderCursor": None},
        ]
        spaces = [{"id": "space", "bots": [{"id": "bot", "name": "Agent", "unread": True}]}]
        self.assertEqual(unread_replies(client, spaces),
                         {"thread": {"id": "reply", "name": "Agent"}})
        client.rpc.assert_called_with("threads/messages", {"botId": "bot", "before": 9}, space="space")

    def test_pagination_must_advance(self):
        client = Mock()
        client.rpc.return_value = {"threadId": "thread", "messages": [], "olderCursor": 9}
        spaces = [{"id": "space", "bots": [{"id": "bot", "name": "Agent", "unread": True}]}]
        with self.assertRaisesRegex(RuntimeError, "pagination"):
            unread_replies(client, spaces)

    def test_state_round_trip_is_private_and_replaces_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(path, {"old-thread": "old-reply"})
            save_state(path, {"thread": "reply"})
            self.assertEqual(private_json(path), {"thread": "reply"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_readable_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "login.json"
            path.write_text(json.dumps({"email": "test@example.com", "password": "not-a-secret"}))
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "0600"):
                private_json(path)

    def test_rejects_unsafe_origins_before_sending_credentials(self):
        for url in ("http://example.com", "https://user:password@example.com", "https://example.com/path",
                    "https://example.com?token=value", "https://example.com#fragment", "file:///tmp/api"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Client(url, {})


if __name__ == "__main__":
    unittest.main()
