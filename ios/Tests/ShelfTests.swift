// Room on the phone: a text-only copy, and the rule that takes finished books off it.

import XCTest

@testable import Readsync

final class ShelfTests: XCTestCase {
    private let toml = """
        title = "Тест"
        id = "0123456789abcdef0123456789abcdef"
        edition = "e1"
        files = "book.json:5,timing.json:7,audio.m4a:11"

        """

    func testTextOnlyLeavesTheAudio() {
        let book = Book(slug: "b", toml: toml)
        XCTAssertEqual(Shelf.parts(of: book, textOnly: true).map(\.name), ["book.json", "timing.json"])
        XCTAssertEqual(Shelf.parts(of: book, textOnly: false).map(\.name), ["book.json", "timing.json", "audio.m4a"])
        // a book without audio is the same either way
        let text = Book(slug: "t", toml: "files = \"book.json:5\"")
        XCTAssertEqual(Shelf.parts(of: text, textOnly: true).map(\.name), ["book.json"])
    }

    func testTextOnlyCopy() throws {
        let fm = FileManager.default
        let slug = "zz-test-\(UUID().uuidString)"
        let source = fm.temporaryDirectory.appendingPathComponent(slug, isDirectory: true)
        defer {
            try? fm.removeItem(at: source)
            try? fm.removeItem(at: Shelf.localDir(slug))
        }
        try fm.createDirectory(at: source.appendingPathComponent("images"), withIntermediateDirectories: true)
        try Data(toml.utf8).write(to: source.appendingPathComponent("book.toml"))
        try Data(count: 5).write(to: source.appendingPathComponent("book.json"))
        try Data(count: 7).write(to: source.appendingPathComponent("timing.json"))
        try Data(count: 11).write(to: source.appendingPathComponent("audio.m4a"))
        try Data(count: 3).write(to: source.appendingPathComponent("images/cover.jpg"))

        guard case .success = Shelf.copyBook(slug, from: source, textOnly: true, progress: { _ in }) else {
            return XCTFail("the copy failed")
        }
        let local = Shelf.localDir(slug)
        let names = Set(try fm.contentsOfDirectory(atPath: local.path))
        XCTAssertEqual(names, ["book.toml", "book.json", "timing.json", "images"])
        XCTAssertTrue(fm.fileExists(atPath: local.appendingPathComponent("images/cover.jpg").path))

        // the local manifest lists what is there: the reader sees no audio and opens pages
        let mine = try XCTUnwrap(Shelf.localCopy(slug))
        XCTAssertFalse(mine.hasAudio)
        XCTAssertEqual(mine.files, ["book.json": 5, "timing.json": 7])
        XCTAssertEqual(mine.edition, "e1")
        XCTAssertEqual(mine.title, "Тест")
        let shared = Book(slug: slug, toml: toml)
        XCTAssertEqual(Shelf.copyState(local: mine, shared: shared), .textOnly)

        // with the audio it is a whole copy again
        guard case .success = Shelf.copyBook(slug, from: source, textOnly: false, progress: { _ in }) else {
            return XCTFail("the copy with audio failed")
        }
        XCTAssertTrue(fm.fileExists(atPath: local.appendingPathComponent("audio.m4a").path))
        XCTAssertEqual(Shelf.copyState(local: Shelf.localCopy(slug), shared: shared), .here)
    }

    func testCopyState() {
        let shared = Book(slug: "b", toml: toml)
        XCTAssertEqual(Shelf.copyState(local: nil, shared: shared), .absent)
        XCTAssertEqual(Shelf.copyState(local: shared, shared: shared), .here)
        let text = Book(slug: "b", toml: toml.replacingOccurrences(of: ",audio.m4a:11", with: ""))
        XCTAssertEqual(Shelf.copyState(local: text, shared: shared), .textOnly)
        // a new edition on the Mac: a text-only copy is out of date like any other
        let newer = Book(slug: "b", toml: toml.replacingOccurrences(of: "e1", with: "e2"))
        XCTAssertEqual(Shelf.copyState(local: text, shared: newer), .outdated)
        // the same edition with other sizes (new timing)
        let retimed = Book(slug: "b", toml: toml.replacingOccurrences(of: "timing.json:7", with: "timing.json:8"))
        XCTAssertEqual(Shelf.copyState(local: text, shared: retimed), .outdated)
    }

    func testManifestReplacesOnlyFiles() {
        let out = Shelf.manifest(toml, files: [("book.json", 5)])
        XCTAssertEqual(Toml.parse(out)["files"], "book.json:5")
        XCTAssertEqual(Toml.parse(out)["edition"], "e1")
        XCTAssertEqual(Toml.parse(Shelf.manifest("title = \"x\"\n", files: [("a", 1)]))["files"], "a:1")
    }

    func testReadToDrop() {
        let day = 86_400_000.0
        let now = 100 * day
        let at = { (fraction: Double, daysAgo: Double) in Readsync.Progress(fraction: fraction, opened: now - daysAgo * day) }
        let progress: [String: Readsync.Progress] = [
            "done-long-ago": at(1, 8),
            "done-a-week-ago": at(0.99, 7),
            "done-yesterday": at(1, 1),
            "almost": at(0.98, 30),
            "playing": at(1, 30),
            "not-here": at(1, 30),
        ]
        let local: Set = ["done-long-ago", "done-a-week-ago", "done-yesterday", "almost", "playing"]
        XCTAssertEqual(
            Shelf.readToDrop(progress, local: local, loaded: "playing", now: now), ["done-a-week-ago", "done-long-ago"])
        XCTAssertEqual(Shelf.readToDrop(progress, local: local, loaded: nil, now: now).count, 3)
        XCTAssertEqual(Shelf.readToDrop([:], local: local, loaded: nil, now: now), [])
    }

    func testToSync() {
        let book = { (slug: String) in Book(slug: slug, toml: "edition = \"e1\"\nfiles = \"book.json:5\"") }
        let books = ["absent", "failed", "outdated", "here", "text", "fetching", "taken-off", "loaded", "open"].map(book)
        let copies: [String: Copy] = [
            "failed": .failed("x"), "outdated": .outdated, "here": .here, "text": .textOnly, "fetching": .fetching(0.5),
            "taken-off": .absent, "loaded": .outdated, "open": .outdated,
        ]
        let picked = Shelf.toSync(books, copies: copies, skip: ["taken-off"], busy: ["loaded", "open"]).map(\.slug)
        XCTAssertEqual(picked, ["absent", "failed", "outdated"])
        // nothing busy: the held-back updates come in too
        XCTAssertEqual(
            Shelf.toSync(books, copies: copies, skip: ["taken-off"], busy: []).map(\.slug),
            ["absent", "failed", "outdated", "loaded", "open"])
        // a book taken off is left even once it is out of date or failed
        XCTAssertEqual(Shelf.toSync([book("x")], copies: ["x": .failed("y")], skip: ["x"], busy: []), [])
        XCTAssertEqual(Shelf.toSync([book("x")], copies: ["x": .outdated], skip: ["x"], busy: []), [])
    }

    @MainActor
    func testRemovedHereIsNotSynced() throws {
        let name = "readsync-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer {
            defaults.removePersistentDomain(forName: name)
            try? FileManager.default.removeItem(at: URL.libraryDirectory.appending(path: "Preferences/\(name).plist"))
        }
        let shelf = Shelf(defaults: defaults)
        let slug = "zz-test-\(UUID().uuidString)", other = "zz-test-\(UUID().uuidString)"
        shelf.inLibrary = [slug]
        shelf.removeHere(slug)
        XCTAssertEqual(shelf.skip, [slug])
        // a copy of a book the library no longer has leaves nothing to skip
        shelf.removeHere(other)
        XCTAssertEqual(shelf.skip, [slug])
        // kept across launches
        XCTAssertEqual(Shelf(defaults: defaults).skip, [slug])
        let book = Book(slug: slug, toml: "files = \"book.json:5\"")
        XCTAssertEqual(Shelf.toSync([book], copies: [:], skip: shelf.skip, busy: []), [])
        // «Синхронизация» is on unless switched off
        XCTAssertTrue(defaults.bool(forKey: Shelf.syncKey))
    }

    func testCurrentIsBegunNotFinished() {
        XCTAssertFalse(Shelf.inProgress(0))  // opened by mistake
        XCTAssertTrue(Shelf.inProgress(0.004))
        XCTAssertTrue(Shelf.inProgress(0.5))
        XCTAssertFalse(Shelf.inProgress(0.99))
        XCTAssertFalse(Shelf.inProgress(1))
    }

    func testCopyProgressAndCancel() throws {
        let fm = FileManager.default
        let dir = fm.temporaryDirectory.appendingPathComponent("zz-copy-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: dir) }
        let src = dir.appendingPathComponent("a"), dest = dir.appendingPathComponent("b")
        try Data(count: 9 << 20).write(to: src)
        var seen: [Int64] = []
        XCTAssertTrue(Coordinated.copy(src, to: dest) { seen.append($0) })
        XCTAssertEqual(seen, [4 << 20, 8 << 20, 9 << 20])  // the ring moves within a file
        XCTAssertEqual(try fm.attributesOfItem(atPath: dest.path)[.size] as? Int, 9 << 20)
        let job = CopyJob()
        job.cancel()
        XCTAssertFalse(Coordinated.copy(src, to: dir.appendingPathComponent("c"), job: job))
    }

    func testSizeInRussian() {
        XCTAssertEqual(size(524_500_000).replacingOccurrences(of: "\u{00A0}", with: " "), "524,5 МБ")
    }

    func testBooksWord() {
        XCTAssertEqual([1, 2, 5, 11, 12, 21, 22, 25, 111, 104].map(booksWord),
            ["книга", "книги", "книг", "книг", "книг", "книга", "книги", "книг", "книг", "книги"])
    }
}
