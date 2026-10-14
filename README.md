# X Post Saver

Telegram-бот для сохранения публичных постов X / Twitter: отправь ссылку,
получи текст, фото, видео, GIF или альбом для пересылки. В подпись входят
автор, ссылка на оригинал и «Сохранено через @username» — имя бот получает
из Telegram, оно не зашито в исходники.

## Запуск

Нужны Python 3.11+ и [uv](https://docs.astral.sh/uv/).
Создай своего бота командой `/newbot` у [@BotFather](https://t.me/BotFather).
Токен вводится скрыто, чтобы не сохранить его в истории shell:

```bash
uv sync
read -rs -p 'Bot token: ' TELEGRAM_BOT_TOKEN
export TELEGRAM_BOT_TOKEN
uv run python bot.py
```

Открой бота в Telegram, нажми Start и отправь ссылку на пост.
Работает через long polling, публичный HTTP endpoint не нужен.
Запускай только один экземпляр с одним токеном. Остановка — Ctrl+C.
`.env` автоматически не загружается.

## Docker

```bash
docker build -t x-post-saver .
docker run --rm --env TELEGRAM_BOT_TOKEN x-post-saver
```

Контейнер работает без root. Для read-only rootfs дай ему writable `/tmp`:

```bash
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --env TELEGRAM_BOT_TOKEN x-post-saver
```

## Поведение и ограничения

- Несколько вложений отправляются альбомом. Текст длиннее лимита подписи
  отправляется отдельными сообщениями без обрезки.
- Добавляются текст и медиа непосредственно цитируемого поста,
  а также результаты опроса, если API их возвращает.
- Посты загружаются через публичный [FxTwitter API](https://docs.fxembed.com/api/introduction/).
  Нужен доступ к `api.fxtwitter.com`, `video.twimg.com`, `pbs.twimg.com`
  и `api.telegram.org`.
- Лимиты загрузки: фото 10 МБ, видео 50 МБ, альбом до 10 вложений.
  Автоматического сжатия нет.
- Закрытые и удалённые посты недоступны. Треды, вложенные цитаты,
  трансляции и X Articles отдельно не разворачиваются.
- Один запрос обрабатывается за раз. Доступ открыт всем, кто знает бота.
  Базы данных и кэша нет; временные файлы удаляются после обработки.
  При ошибке отправки часть длинного поста уже могла прийти.

## Консольное скачивание видео

Для CLI дополнительно нужен `yt-dlp` в PATH (`uv tool install yt-dlp`).
Подставь реальную ссылку вместо примера:

```bash
python3 xtourl.py 'https://x.com/example/status/123'
python3 xtourl.py 'https://x.com/example/status/123' -o ~/Videos
```

По умолчанию видео сохраняются в `downloads`, существующие файлы
не перезаписываются. CLI скачивает готовые дорожки, без ffmpeg.
Docker-образ предназначен для Telegram-бота.

## Разработка

```bash
uv run python -m unittest -v
uv export --locked --no-dev --no-emit-project --output-file requirements.txt
```

`uv.lock` фиксирует зависимости, `requirements.txt` содержит их версии
и хеши для Docker. После изменения зависимостей обнови оба файла.
Токены передавай через окружение; не коммить `.env` или скачанные медиа.
