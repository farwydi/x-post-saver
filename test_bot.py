from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock

import httpx
from telegram import Bot
from telegram.request import HTTPXRequest

from bot import download_media, extract_url, fetch_post, post_content, send_post, split_text


class PostTest(unittest.TestCase):
    def test_link_in_message(self):
        self.assertEqual(
            extract_url("Скачай https://x.com/example/status/123?s=20 пожалуйста"),
            "https://x.com/example/status/123",
        )
        with self.assertRaises(ValueError):
            extract_url("https://x.com.evil.org/a/status/123")

    def test_unicode_chunks_preserve_text(self):
        text = "a😀б" * 2000
        chunks = split_text(text, 4096)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks))

    def test_quote_and_poll(self):
        post = {
            "author": {"name": "Alice", "screen_name": "alice"}, "text": "Hello",
            "quote": {"text": "Quoted", "author": {"screen_name": "bob"},
                      "media": {"all": [{"type": "photo", "url": "https://pbs.twimg.com/a.jpg"}]}},
            "poll": {"choices": [{"label": "Yes", "count": 3}]},
        }
        text, media = post_content(post, "https://x.com/alice/status/123", "sample_bot")
        for value in ("Hello", "Quoted", "@bob", "Yes", "3 голосов"):
            self.assertIn(value, text)
        self.assertEqual(len(media), 1)
        self.assertTrue(text.endswith("\n\nСохранено через @sample_bot"))


class BotTest(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_post(self):
        def respond(request):
            self.assertEqual(str(request.url), "https://api.fxtwitter.com/status/123")
            self.assertEqual(request.headers["user-agent"], "xtourl/0.1")
            return httpx.Response(200, json={"code": 200, "tweet": {"text": "Hello"}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await fetch_post(client, "https://x.com/a/status/123"), {"text": "Hello"})

    async def test_unavailable_post(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 404})
        )) as client:
            with self.assertRaises(ValueError):
                await fetch_post(client, "https://x.com/a/status/123")

    async def test_download_and_limits(self):
        item = {"type": "video", "url": "https://video.twimg.com/a.mp4"}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a.mp4"
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"video")
            )) as client:
                await download_media(client, item, path)
                self.assertEqual(path.read_bytes(), b"video")
                for url in ("http://video.twimg.com/a", "https://localhost/a", "https://video.twimg.com.evil.org/a"):
                    with self.subTest(url=url), self.assertRaises(ValueError):
                        await download_media(client, {**item, "url": url}, path)
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers={"content-length": "50000001"})
            )) as client:
                with self.assertRaises(ValueError):
                    await download_media(client, item, path)

    async def test_stream_limit_without_content_length(self):
        with TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 10_000_001))
            )) as client:
                with self.assertRaises(ValueError):
                    await download_media(client, {"type": "photo", "url": "https://pbs.twimg.com/a.jpg"}, Path(directory) / "a")

    async def test_redirect_is_not_followed(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "http://localhost/secret"})
        with TemporaryDirectory() as directory:
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with self.assertRaises(ValueError):
                    await download_media(client, {"type": "photo", "url": "https://pbs.twimg.com/a.jpg"}, Path(directory) / "a")
        self.assertEqual(len(requests), 1)

    async def test_text_only(self):
        bot = AsyncMock()
        await send_post(bot, 123, "Hello", [], [])
        bot.send_message.assert_awaited_once_with(123, "Hello", disable_web_page_preview=True)
        bot.send_media_group.assert_not_called()

    async def test_video_and_mixed_album(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a.mp4"
            path.write_bytes(b"media")
            bot = AsyncMock()
            await send_post(bot, 123, "Caption", [{"type": "video"}], [path])
            self.assertEqual(bot.send_video.await_args.kwargs["caption"], "Caption")
            bot.send_message.assert_not_called()
            await send_post(bot, 123, "Album", [{"type": "photo"}, {"type": "video"}], [path, path])
            album = bot.send_media_group.await_args.args[1]
            self.assertEqual([item.type for item in album], ["photo", "video"])
            self.assertEqual(album[0].caption, "Album")
            self.assertIsNone(album[1].caption)

    async def test_long_caption_preserved(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a.mp4"
            path.write_bytes(b"media")
            bot = AsyncMock()
            text = "😀" * 3000
            await send_post(bot, 123, text, [{"type": "video"}], [path])
            self.assertEqual("".join(call.args[1] for call in bot.send_message.await_args_list), text)
            self.assertIsNone(bot.send_video.await_args.kwargs["caption"])

    async def test_telegram_upload_contains_file(self):
        uploads = []
        async def respond(request):
            if request.url.path.endswith("/getMe"):
                result = {"id": 1, "is_bot": True, "first_name": "Test", "username": "test_bot"}
            else:
                body = await request.aread()
                self.assertIn(b"actual video bytes", body)
                self.assertIn(b"Caption", body)
                uploads.append(request.url.path.rsplit("/", 1)[-1])
                result = {"message_id": 1, "date": 0, "chat": {"id": 123, "type": "private"}}
            return httpx.Response(200, json={"ok": True, "result": result})
        request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"actual video bytes")
            async with Bot("123:test-token", request=request) as bot:
                await send_post(bot, 123, "Caption", [{"type": "video"}], [path])
        self.assertEqual(uploads, ["sendVideo"])


if __name__ == "__main__":
    unittest.main()
