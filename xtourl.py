#!/usr/bin/env python3
"""Скачать видео из поста X через yt-dlp."""

import argparse
import re
import subprocess
from urllib.parse import urlsplit


def post_url(value):
    try:
        url = urlsplit(value.strip())
        valid = (
            url.scheme in ("http", "https")
            and url.netloc.lower() in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
            and re.fullmatch(r"/(?:[\w]+|i/web)/status/[0-9]+(?:/video/[0-9]+)?/?", url.path)
        )
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("Нужна ссылка вида https://x.com/user/status/123")
    return "https://x.com" + url.path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", type=post_url, help="ссылка на пост с видео")
    parser.add_argument("-o", "--output", default="downloads", help="папка для видео (downloads)")
    args = parser.parse_args()
    try:
        return subprocess.run([
            "yt-dlp", "--ignore-config", "--no-overwrites", "--abort-on-error",
            "-f", "best[ext=mp4]/best", "-P", args.output,
            "-o", "%(id)s.%(ext)s", "--", args.url,
        ], check=False).returncode
    except FileNotFoundError:
        parser.exit(1, "Не найден yt-dlp. Установите: uv tool install yt-dlp\n")
    except OSError as error:
        parser.exit(1, f"Не удалось запустить yt-dlp: {error}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
