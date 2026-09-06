# readsync

Читалка, в которой текст идёт вровень с аудиокнигой: подсвечивается предложение и слово,
которые звучат сейчас. Есть режим книги без звука, библиотека и список «хочу прочитать»,
который сам находит текст. Всё локально, в браузере.

*A local immersion-reading app: the text follows the audiobook with sentence and word
highlighting. Interface and text heuristics are Russian-first.*

## Установка (macOS)

```bash
brew install python@3.12 ffmpeg yt-dlp node
git clone git@github.com:diasbro/readsync.git && cd readsync
make setup
make serve      # http://127.0.0.1:8765
```

## Книги

Добавляются на главной: название в «Хочу прочитать» или ссылки и файлы в «Добавить вручную».
Текст: страница fantasy-worlds, HTML, FB2, EPUB, TXT. Аудио: YouTube или файл. Тома и части
склеиваются. То же из терминала:

```bash
.venv/bin/python pipeline/add_book.py my-book --text <url|файл> [--audio <url|файл>]
```

Точное выравнивание слов идёт отдельно и долго: `make align slug=my-book`. Без него подсветка
по субтитрам, этого достаточно.

Данные книг и состояние чтения лежат в `books/` и в git не попадают.

## Клавиши

Аудио: пробел · ← → предложения · Shift+← → ±10 с · r повтор · [ ] скорость · f приглушение ·
t оглавление · a вернуться · m режим книги. Книга: ← → пробел листать · Home/End · m аудио.

## Разработка

`make test`, `make lint`, `make fmt`. Устройство и соглашения: `CLAUDE.md`, `docs/`.

## Лицензия

MIT. Шрифты в `reader/fonts/` под SIL OFL.
