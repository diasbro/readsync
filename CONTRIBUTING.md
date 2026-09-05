# Contributing

Personal project, but PRs are welcome.

- `make setup`, then `make test` and `make lint` before pushing (CI runs the same).
- Keep the reader dependency-free and the pipeline stdlib-first.
- Book data (`books/<slug>/` except `book.toml`) never goes into git.
- Most of the code was written with Claude Code; see `CLAUDE.md` for the conventions agents follow.
  Please review AI-generated changes as you would any other.
