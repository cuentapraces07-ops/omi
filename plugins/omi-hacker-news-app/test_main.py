"""Hermetic Hacker News text-cleaning regressions.

Import the production module with framework-only stubs, then exercise its real
cleaner and discussion handler. No network, credentials, or third-party runtime
packages are required.
"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, patch


def load_app():
    class FastAPI:
        def __init__(self, **kwargs):
            pass

        def get(self, *args, **kwargs):
            return lambda handler: handler

        post = get

    class BaseModel:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class HTTPError(Exception):
        pass

    httpx = ModuleType("httpx")
    httpx.AsyncClient = object
    httpx.HTTPError = HTTPError
    fastapi = ModuleType("fastapi")
    fastapi.FastAPI = FastAPI
    responses = ModuleType("fastapi.responses")
    responses.HTMLResponse = str
    pydantic = ModuleType("pydantic")
    pydantic.BaseModel = BaseModel

    spec = importlib.util.spec_from_file_location("hacker_news_app", Path(__file__).with_name("main.py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "httpx": httpx,
            "fastapi": fastapi,
            "fastapi.responses": responses,
            "pydantic": pydantic,
        },
    ):
        spec.loader.exec_module(module)
    return module


app = load_app()


class CleanTextTests(unittest.TestCase):
    def test_preserves_escaped_literal_angle_brackets(self):
        cases = {
            "Use &lt;vector&gt; for this.": "Use <vector> for this.",
            "if a &lt; b and c &gt; d": "if a < b and c > d",
            "&#60;b&#62;literal&#60;/b&#62;": "<b>literal</b>",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(app._clean_text(raw), expected)

    def test_removes_real_markup_but_preserves_code_text(self):
        raw = "<p>Hello &amp; goodbye</p><p><code>&lt;vector&gt;</code><br>next</p>"
        self.assertEqual(app._clean_text(raw), "Hello & goodbye\n\n`<vector>`\nnext")


class InputValidationTests(unittest.TestCase):
    def test_safe_payload_rejects_non_dict_values(self):
        for value in (None, [], "payload", 7):
            with self.subTest(value=value):
                self.assertEqual(app._safe_payload(value), {})

    def test_safe_limit_rejects_booleans_and_clamps_values(self):
        cases = [
            (None, 10),
            ("", 10),
            ("  ", 10),
            (True, 10),
            (False, 10),
            ("7", 7),
            (0, 1),
            (-4, 1),
            (999, 20),
            ("not-a-number", 10),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(app._safe_limit(value), expected)

    def test_positive_item_id_rejects_bool_empty_and_non_positive_values(self):
        self.assertEqual(app._positive_item_id(" 42 "), 42)
        for value in (True, False, "", "  ", "1.5", 0, -1, 42.0, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(app._positive_item_id(value))

    def test_format_story_omits_invalid_hn_fallback_url(self):
        formatted = app._format_story({"title": "No id", "url": "https://example.test"}, 1)
        self.assertIn("https://example.test", formatted)
        self.assertNotIn("id=None", formatted)


class DiscussionHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_discussion_preserves_escaped_text_in_post_and_comment(self):
        item = {
            "title": "Escaped text",
            "author": "alice",
            "points": 7,
            "url": "https://example.test/story",
            "text": "<p>Use &lt;vector&gt; here.</p>",
            "children": [
                {
                    "author": "bob",
                    "text": "<p>if a &lt; b and c &gt; d</p>",
                }
            ],
        }
        provider = AsyncMock(return_value=item)
        with patch.object(app, "_request_json", provider):
            response = await app.get_discussion({"item_id": 9995409, "comment_limit": 5})

        self.assertIsNone(response.error)
        self.assertIn("Post text:\nUse <vector> here.", response.result)
        self.assertIn("1. bob: if a < b and c > d", response.result)
        provider.assert_awaited_once_with("/items/9995409")

    async def test_discussion_rejects_invalid_item_ids_before_network(self):
        provider = AsyncMock()
        with patch.object(app, "_request_json", provider):
            for value in (True, "", "0", 0, -3, "not-an-id"):
                with self.subTest(value=value):
                    response = await app.get_discussion({"item_id": value})
                    self.assertEqual(response.error, "item_id must be a positive integer")
        provider.assert_not_awaited()

    async def test_handlers_tolerate_non_dict_bodies_and_provider_payloads(self):
        provider = AsyncMock(side_effect=[None, {"hits": [None, {"title": "Story"}]}, []])
        with patch.object(app, "_request_json", provider):
            front_page = await app.get_front_page(None)
            search = await app.search_stories({"query": "omi"})
            discussion = await app.get_discussion({"item_id": "42"})

        self.assertIsNone(front_page.error)
        self.assertEqual(front_page.result, "No Hacker News front page stories were returned.")
        self.assertIsNone(search.error)
        self.assertIn("Story", search.result)
        self.assertEqual(discussion.error, "Hacker News returned an invalid item")
        provider.assert_any_await("/items/42")

    async def test_discussion_ignores_non_dict_comments(self):
        item = {
            "title": "Safe discussion",
            "children": [None, {"author": "alice", "text": "hello"}],
        }
        with patch.object(app, "_request_json", AsyncMock(return_value=item)):
            response = await app.get_discussion({"item_id": 42})

        self.assertIsNone(response.error)
        self.assertIn("1. alice: hello", response.result)


if __name__ == "__main__":
    unittest.main()
