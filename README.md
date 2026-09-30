# readsync

Ищет книги по онлайн-библиотекам и открывает их для чтения: FB2, EPUB, PDF, TXT, HTML.

## Установка (macOS)

```bash
brew install python@3.12 ffmpeg yt-dlp node
git clone https://github.com/diasbro/readsync.git && cd readsync
make setup
make serve      # http://127.0.0.1:8765
```

Приложение для верхней панели: `make dmg`, образ в `dist/readsync.dmg`.

## Разработка

`make test`, `make lint`. Соглашения — в `CLAUDE.md`.

## Лицензия

MIT. Шрифты в `reader/fonts/` под SIL OFL.
