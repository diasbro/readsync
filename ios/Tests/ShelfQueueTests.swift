// A whole Shelf at work on a library of its own: adding books from files (one drain, the lone book opened,
// Inbox copies cleared, duplicates asked about before they are listed, the row kept until the book is in),
// downloads ending on the shelf that started them, and taking copies off a phone whose library is out of reach.

import Combine
import XCTest

@testable import Readsync

@MainActor
final class ShelfQueueTests: XCTestCase {
    private let fm = FileManager.default

    private func txt(_ dir: URL, _ title: String) throws -> URL {
        let url = dir.appendingPathComponent("\(title).txt")
        let body = String(repeating: "Первая строка текста идёт здесь. Вторая строка текста тоже. ", count: 120)
        try Data("\(title)\n\n\(body)".utf8).write(to: url)
        return url
    }

    private func until(_ seconds: Double = 6, _ done: () -> Bool) async throws {
        let end = Date().addingTimeInterval(seconds)
        while !done(), Date() < end { try await Task.sleep(for: .milliseconds(20)) }
    }

    private func books(_ docs: URL) -> [String] {
        ((try? fm.contentsOfDirectory(atPath: docs.appendingPathComponent("books").path)) ?? []).filter { !$0.hasPrefix(".") }.sorted()
    }

    // two adds in one turn: one drain, so one notice for both files
    func testTwoAddsRunOneDrain() async throws {
        let shelf = try scratchShelf()
        let dir = try scratchFolder()
        var notices: [String] = []
        let c = shelf.$importNotice.dropFirst().sink { notices.append($0?.title ?? "nil") }
        defer { c.cancel() }
        shelf.add([dir.appendingPathComponent("a-missing.fb2")])
        shelf.add([dir.appendingPathComponent("b-missing.fb2")])
        try await until { shelf.adding.isEmpty && !notices.isEmpty }
        try await Task.sleep(for: .milliseconds(300))
        XCTAssertEqual(notices.filter { $0 != "nil" }, ["Не добавлены: 2"])
    }

    // of two files, the second called off before it starts: the first is alone, and opens
    func testCancelledQueuedFileLeavesTheOtherAlone() async throws {
        let shelf = try scratchShelf()
        let dir = try scratchFolder()
        shelf.add([try txt(dir, "Проверка один")])
        try await until { shelf.adding.isEmpty && shelf.toOpen != nil }
        XCTAssertNotNil(shelf.toOpen)
        shelf.toOpen = nil
        shelf.add([try txt(dir, "Проверка два"), try txt(dir, "Проверка три")])
        shelf.cancelAdd(try XCTUnwrap(shelf.adding.last).id)  // not started yet
        try await until { shelf.adding.isEmpty && shelf.toOpen != nil }
        XCTAssertNotNil(shelf.toOpen)
    }

    // a file handed in by another app (Documents/Inbox) and called off before it starts is not left there
    func testCalledOffInboxCopyIsCleared() async throws {
        let docs = try scratchFolder("readsync-docs")
        let shelf = try scratchShelf(docs: docs)
        let inbox = docs.appendingPathComponent("Inbox", isDirectory: true)
        try fm.createDirectory(at: inbox, withIntermediateDirectories: true)
        let handed = try txt(inbox, "Из почты")
        shelf.add([try scratchFolder().appendingPathComponent("first-missing.fb2"), handed])
        shelf.cancelAdd(try XCTUnwrap(shelf.adding.last).id)
        XCTAssertFalse(fm.fileExists(atPath: handed.path))
        try await until { shelf.adding.isEmpty }
    }

    // a book of the same title and author, added on a launch before the library was listed: asked about, not
    // listed while asked, and gone, copy and all, on «Отмена»
    func testDuplicateFoundBeforeTheFirstRefreshAndCalledOff() async throws {
        let docs = try scratchFolder("readsync-docs")
        let dir = try scratchFolder()
        let file = try txt(dir, "Повтор")
        let first = try scratchShelf(docs: docs)
        first.add([file])
        try await until { first.adding.isEmpty && first.toOpen != nil }
        let had = books(docs)
        XCTAssertEqual(had.count, 1)

        let shelf = try scratchShelf(docs: docs)  // a cold launch: nothing listed yet
        XCTAssertTrue(shelf.books.isEmpty)
        shelf.add([file])
        try await until { !shelf.duplicates.isEmpty }
        let d = try XCTUnwrap(shelf.duplicates.first)
        XCTAssertEqual(d.twin, had[0])
        XCTAssertEqual(books(docs).count, 2)
        await shelf.refresh()
        XCTAssertEqual(shelf.inLibrary, Set(had))  // not listed, so not copied either, while it is asked about
        XCTAssertFalse(shelf.books.contains { $0.slug == d.slug })
        shelf.settle(d, keep: false)
        try await until { self.books(docs) == had }
        XCTAssertEqual(books(docs), had)
        XCTAssertFalse(shelf.books.contains { $0.slug == d.slug })
        XCTAssertFalse(fm.fileExists(atPath: Shelf.localDir(d.slug).path))
    }

    // a duplicate called off while it was being copied: its row is not left «fetching» for ever
    func testDiscardedDuplicateIsNotLeftFetching() async throws {
        let docs = try scratchFolder("readsync-docs")
        let file = try txt(try scratchFolder(), "Повтор два")
        let first = try scratchShelf(docs: docs)
        first.add([file])
        try await until { first.adding.isEmpty && first.toOpen != nil }
        let shelf = try scratchShelf(docs: docs)
        shelf.add([file])
        try await until { !shelf.duplicates.isEmpty }
        let d = try XCTUnwrap(shelf.duplicates.first)
        let toml = try String(contentsOf: docs.appendingPathComponent("books/\(d.slug)/book.toml"), encoding: .utf8)
        shelf.fetch(Book(slug: d.slug, toml: toml), byHand: false)
        XCTAssertEqual(shelf.copy(of: d.slug), .fetching(0))
        shelf.settle(d, keep: false)
        XCTAssertEqual(shelf.copy(of: d.slug), .absent)
        try await until { self.books(docs).count == 1 }
    }

    // the first book of an empty library: its «Добавляю…» row stays until the book is listed (the copies the
    // app keeps of other books aside: they are the host app's, in one folder for every shelf)
    func testNoEmptyLibraryBetweenRowAndBook() async throws {
        let shelf = try scratchShelf()
        await shelf.refresh()
        XCTAssertTrue(shelf.inLibrary.isEmpty)
        var started = false, emptyBetween = false
        let c = shelf.$adding.combineLatest(shelf.$inLibrary).sink { adding, listed in
            if !adding.isEmpty { started = true } else if started, listed.isEmpty { emptyBetween = true }
        }
        defer { c.cancel() }
        shelf.add([try txt(try scratchFolder(), "Первая книга")])
        try await until { shelf.adding.isEmpty && shelf.toOpen != nil }
        XCTAssertTrue(started)
        XCTAssertEqual(shelf.inLibrary.count, 1)
        XCTAssertFalse(emptyBetween)
    }

    // a download ends on the shelf that started it
    func testFetchEndsOnItsOwnShelf() async throws {
        let shelf = try scratchShelf()
        let book = Book(slug: "zz-test-\(UUID().uuidString)", toml: "edition = \"e1\"\nfiles = \"book.json:5\"")
        await shelf.fetch(book, byHand: true)?.value
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertTrue(shelf.copy(of: book.slug).isFailed, "\(shelf.copy(of: book.slug))")
    }

    // «Скачать со звуком» on a text-only copy fails: the copy is still the text-only one, not «Есть новая версия»
    func testFailedUpdateKeepsWhatTheCopyIs() async throws {
        let shelf = try scratchShelf()
        let slug = "zz-test-\(UUID().uuidString)"
        let local = Shelf.localDir(slug)
        try fm.createDirectory(at: local, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: local) }
        try Data("edition = \"e1\"\nfiles = \"book.json:5,timing.json:7\"".utf8).write(to: local.appendingPathComponent("book.toml"))
        let shared = Book(slug: slug, toml: "edition = \"e1\"\nfiles = \"book.json:5,timing.json:7,audio.m4a:11\"")
        await shelf.fetch(shared, textOnly: false)?.value  // not in the library folder: it fails
        try await Task.sleep(for: .milliseconds(100))
        XCTAssertEqual(shelf.copy(of: slug), .textOnly)
    }

    // «Только здесь» while the library cannot be reached: the round does not bring the book back later
    func testRemovedHereWhileLibraryIsLostIsSkipped() throws {
        let defaults = try scratchDefaults()
        defaults.set(Data("not a bookmark".utf8), forKey: "libraryBookmark")
        let shelf = try scratchShelf(defaults: defaults)
        XCTAssertTrue(shelf.folderLost)
        XCTAssertNil(shelf.booksRoot)
        let slug = "zz-test-\(UUID().uuidString)"
        shelf.removeHere(slug)
        XCTAssertEqual(shelf.skip, [slug])
        shelf.removeHere("zz-other", remember: false)  // «Скрывать прочитанные»: left until read again
        XCTAssertEqual(shelf.skip, [slug])
    }
}
