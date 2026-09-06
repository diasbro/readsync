# Архитектура readsync

Один процесс `serve.py` (stdlib, порт 8765) отдаёт статичную читалку из `reader/` и данные
книг из `books/`, хранит состояние чтения и запускает пайплайн фоновыми задачами.

```
books/<slug>/
  book.toml      метаданные (slug, title, author, narrator, источники)
  book.json      текст: chapters[], blocks[] (kind, text, em, notes, sentences, images), notes{}
  timing.json    слова: [block, charStart, charEnd, t0, t1], монотонно по t0
  audio.m4a      воспроизведение; audio16k.wav только на время выравнивания
  images/        иллюстрации и cover.*
  state.json     pos, sent, mode, opened, shelf, stats (последний писатель побеждает по <key>At)
books/settings.json   общие настройки читалки
books/wishlist.json   список «Хочу прочитать»
```

Пайплайн (`pipeline/`): извлечение текста по формату → `merge_books.py` для томов →
`anchors.py` по субтитрам → `timing_from_anchors.py` → опционально `align.py` (MMS).
`add_book.py` оркестрирует всё это, умеет несколько томов и несколько аудиочастей, и умеет
добавить аудио к готовой книге.

Читалка (`reader/app.js`): одна IIFE. Аудиорежим: 10 Гц опрос `audio.currentTime`, бинарный
поиск слова, классы `.cur` на слове, предложении, блоке, автоскролл с зоной чтения. Режим книги:
`#text` в CSS-колонках с `column-fill: auto`, разворот = шаг `scrollLeft`, позиция по индексу
предложения. Переключение режимов идёт через предложение, никогда через пиксели.

Подробности выбора стратегии выравнивания: `docs/adr/0001-caption-anchors-plus-mms.md`.
