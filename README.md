# readsync

Читалка, в которой текст идёт вровень с аудиокнигой: подсвечивается звучащее предложение и слово.
Книги без аудио читаются страницами. Всё локально, в браузере.

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
