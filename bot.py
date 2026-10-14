"""Telegram bot: a link to X becomes a forwardable post."""

import argparse
import asyncio
from contextlib import ExitStack
import logging
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import httpx
from telegram import InputFile, InputMediaPhoto, InputMediaVideo
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from xtourl import post_url

HELP = "Пришли ссылку на пост X / Twitter — верну текст, фото и видео для пересылки."


def extract_url(text):
    for candidate in re.findall(r"https?://[^\s<>]+", text):
        try:
            return post_url(candidate.rstrip(".,!?)]:;"))
        except argparse.ArgumentTypeError:
            continue
    raise ValueError(HELP)


def split_text(text, limit):
    # Telegram counts UTF-16 units; don't split an emoji in half.
    chunks, chunk, size = [], [], 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if size + width > limit:
            chunks.append("".join(chunk))
            chunk, size = [], 0
        chunk.append(char)
        size += width
    if chunk:
        chunks.append("".join(chunk))
    return chunks


async def fetch_post(client, url):
    status_id = re.search(r"/status/([0-9]+)", url).group(1)
    response = await client.get(f"https://api.fxtwitter.com/status/{status_id}", headers={"User-Agent": "xtourl/0.1"})
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 200 or not isinstance(data.get("tweet"), dict):
        raise ValueError("Пост недоступен: возможно, он удалён или закрыт.")
    return data["tweet"]


def post_content(post, url, bot_username):
    author = post.get("author") or {}
    heading = f"{author.get('name', 'X')} (@{author.get('screen_name', 'unknown')})"
    text = "\n\n".join(part for part in (heading, post.get("text"), url) if part)
    media = list((post.get("media") or {}).get("all") or [])
    quote = post.get("quote")
    if quote:
        quoted_author = quote.get("author") or {}
        text += f"\n\nЦитата @{quoted_author.get('screen_name', 'unknown')}:\n{quote.get('text', '')}"
        media.extend((quote.get("media") or {}).get("all") or [])
    poll = post.get("poll")
    if poll:
        text += "\n\nОпрос:\n" + "\n".join(
            f"• {choice['label']} — {choice.get('count', 0)} голосов"
            for choice in poll.get("choices", [])
        )
    if len(media) > 10:
        raise ValueError("В посте больше 10 вложений — один альбом Telegram их не вместит.")
    if any(item.get("type") not in ("photo", "video", "gif") for item in media):
        raise ValueError("В посте есть неподдерживаемый тип медиа.")
    return text + f"\n\nСохранено через @{bot_username}", media


async def download_media(client, item, path):
    url = urlsplit(item["url"])
    if url.scheme != "https" or url.hostname not in ("pbs.twimg.com", "video.twimg.com") or url.username or url.port:
        raise ValueError("Источник медиа не является CDN X.")
    limit = (10 if item["type"] == "photo" else 50) * 1_000_000
    async with client.stream("GET", item["url"], follow_redirects=False) as response:
        if 300 <= response.status_code < 400:
            raise ValueError("CDN перенаправил запрос: медиа не скачано.")
        response.raise_for_status()
        if int(response.headers.get("content-length", 0)) > limit:
            raise ValueError("Медиа превышает лимит Telegram: 10 МБ для фото, 50 МБ для видео.")
        size = 0
        with path.open("wb") as file:
            async for chunk in response.aiter_bytes(64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError("Медиа превышает лимит Telegram: 10 МБ для фото, 50 МБ для видео.")
                file.write(chunk)
        if not size:
            raise ValueError("CDN вернул пустой файл.")


async def send_post(bot, chat_id, text, media, paths):
    caption = text if len(split_text(text, 1024)) == 1 else None
    if not media or caption is None:
        for chunk in split_text(text, 4096):
            await bot.send_message(chat_id, chunk, disable_web_page_preview=True)
    with ExitStack() as stack:
        attachments = []
        for index, (item, path) in enumerate(zip(media, paths, strict=True)):
            file = InputFile(stack.enter_context(path.open("rb")), filename=path.name, read_file_handle=False)
            kind = InputMediaPhoto if item["type"] == "photo" else InputMediaVideo
            attachments.append(kind(file, caption=caption if index == 0 else None))
        if len(attachments) == 1:
            item = attachments[0]
            if media[0]["type"] == "photo":
                await bot.send_photo(chat_id, item.media, caption=item.caption)
            elif media[0]["type"] == "gif":
                await bot.send_animation(chat_id, item.media, caption=item.caption)
            else:
                await bot.send_video(chat_id, item.media, caption=item.caption, supports_streaming=True)
        elif attachments:
            await bot.send_media_group(chat_id, attachments)


async def help_message(update, context):
    await update.effective_message.reply_text(HELP)


async def handle_post(update, context):
    message = update.effective_message
    try:
        url = extract_url(message.text or message.caption or "")
        with TemporaryDirectory(prefix="xtourl-") as directory:
            async with httpx.AsyncClient(timeout=30) as client, asyncio.timeout(180):
                post = await fetch_post(client, url)
                text, media = post_content(post, url, context.bot.username)
                paths = []
                for index, item in enumerate(media):
                    path = Path(directory) / f"{index}.{'jpg' if item['type'] == 'photo' else 'mp4'}"
                    await download_media(client, item, path)
                    paths.append(path)
            await send_post(context.bot, message.chat_id, text, media, paths)
    except ValueError as error:
        await message.reply_text(str(error))
    except (httpx.HTTPError, TimeoutError):
        await message.reply_text("Не удалось скачать пост. Проверь ссылку или попробуй позже.")
    except TelegramError:
        await message.reply_text("Telegram не принял медиа. Часть поста могла отправиться; попробуй позже.")
    except (KeyError, TypeError, OSError):
        await message.reply_text("Не удалось обработать пост или сохранить медиа.")


async def log_error(update, context):
    # Exception messages from HTTP clients may contain the bot token.
    logging.error("Ошибка бота: %s", type(context.error).__name__)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Задай TELEGRAM_BOT_TOKEN (токен от @BotFather). См. README.md.")
    logging.basicConfig(level=logging.WARNING)
    app = Application.builder().token(token).read_timeout(120).write_timeout(120).media_write_timeout(120).build()
    app.add_handler(CommandHandler(["start", "help"], help_message))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_post))
    app.add_error_handler(log_error)
    print("Бот запущен. Для остановки нажми Ctrl+C.", flush=True)
    # shortcut: requests are processed sequentially; add a bounded queue for a public bot under load.
    app.run_polling(allowed_updates=["message"], drop_pending_updates=False)


if __name__ == "__main__":
    main()
