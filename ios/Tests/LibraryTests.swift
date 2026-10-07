// Where the library lives on the phone: the app's own Documents/books made with nothing to choose, the
// books of older layouts moved into it, the move into iCloud Drive landing as readsync/books, the books
// added here brought into a library opened there, and the small rules of adding a book from a file.

import XCTest

@testable import Readsync

final class LibraryTests: XCTestCase {
    private let fm = FileManager.default
    private var docs: URL!

    override func setUpWithError() throws {
        docs = fm.temporaryDirectory.appendingPathComponent("readsync-docs-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: docs, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? fm.removeItem(at: docs)
    }

    /// A book folder with its manifest (and a state file, which moves with it).
    @discardableResult
    private func book(_ dir: URL, _ slug: String, id: String = "", title: String = "") throws -> URL {
        let d = dir.appendingPathComponent(slug, isDirectory: true)
        try fm.createDirectory(at: d.appendingPathComponent("state"), withIntermediateDirectories: true)
        try Data("id = \"\(id)\"\ntitle = \"\(title)\"\nfiles = \"book.json:2\"\n".utf8).write(to: d.appendingPathComponent("book.toml"))
        try Data("{}".utf8).write(to: d.appendingPathComponent("book.json"))
        try Data("{}".utf8).write(to: d.appendingPathComponent("state/phone.json"))
        return d
    }

    private func names(_ dir: URL) -> [String] {
        ((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []).filter { !$0.hasPrefix(".") }.sorted()
    }

    private var books: URL { docs.appendingPathComponent("books", isDirectory: true) }
    private var gathered: URL { docs.appendingPathComponent("readsync", isDirectory: true) }

    // ---- the own library and the migration (no folder chosen) ----

    func testNewUserGetsDocumentsBooks() {
        XCTAssertEqual(Shelf.settleOwnLibrary(docs: docs), 0)
        XCTAssertEqual(names(docs), ["books"])
        XCTAssertEqual(names(books), [])
    }

    func testBooksPutInDocumentsByHandMoveIn() throws {
        try book(docs, "a")
        try book(docs, "b")
        XCTAssertEqual(Shelf.settleOwnLibrary(docs: docs), 2)
        XCTAssertEqual(names(books), ["a", "b"])
        XCTAssertEqual(names(books.appendingPathComponent("a/state")), ["phone.json"])  // the reading state with it
        XCTAssertEqual(names(docs), ["books"])
    }

    func testMoveCalledOffComesBack() throws {
        try book(gathered, "a")  // Documents/readsync/<slug>: the old gathering
        try book(gathered.appendingPathComponent("books"), "b")  // Documents/readsync/books/<slug>: the new one
        XCTAssertEqual(Shelf.settleOwnLibrary(docs: docs), 2)
        XCTAssertEqual(names(books), ["a", "b"])
        XCTAssertFalse(fm.fileExists(atPath: gathered.path))  // the emptied readsync goes
    }

    func testTakenNameStays() throws {
        try book(books, "a", title: "there")
        try book(docs, "a", title: "by hand")
        XCTAssertEqual(Shelf.settleOwnLibrary(docs: docs), 0)
        XCTAssertEqual(names(docs), ["a", "books"])
        let toml = try String(contentsOf: books.appendingPathComponent("a/book.toml"), encoding: .utf8)
        XCTAssertTrue(toml.contains("there"))
    }

    func testOnlyBookFoldersMove() throws {
        try Data("1".utf8).write(to: docs.appendingPathComponent("debug.js"))
        try Data("{}".utf8).write(to: docs.appendingPathComponent("debug-out.json"))
        try fm.createDirectory(at: docs.appendingPathComponent("Inbox"), withIntermediateDirectories: true)
        try fm.createDirectory(at: gathered.appendingPathComponent("notes"), withIntermediateDirectories: true)
        XCTAssertEqual(Shelf.settleOwnLibrary(docs: docs), 0)
        XCTAssertEqual(names(docs), ["Inbox", "books", "debug-out.json", "debug.js", "readsync"])  // not empty: kept
        XCTAssertEqual(names(gathered), ["notes"])
    }

    func testBooksRoot() throws {
        let chosen = docs.appendingPathComponent("chosen", isDirectory: true)
        try fm.createDirectory(at: chosen, withIntermediateDirectories: true)
        // the own library: always its `books`
        XCTAssertEqual(Shelf.booksRoot(of: docs, chosen: false).lastPathComponent, "books")
        // a chosen folder: itself, or its `books` when it has one (the Mac's readsync folder)
        XCTAssertEqual(Shelf.booksRoot(of: chosen, chosen: true), chosen)
        try fm.createDirectory(at: chosen.appendingPathComponent("books"), withIntermediateDirectories: true)
        XCTAssertEqual(Shelf.booksRoot(of: chosen, chosen: true), chosen.appendingPathComponent("books", isDirectory: true))
    }

    // ---- the move into iCloud Drive ----

    func testMoveLandsAsReadsyncBooks() throws {
        Shelf.settleOwnLibrary(docs: docs)
        try book(books, "a")
        try Data("1".utf8).write(to: docs.appendingPathComponent("debug.js"))
        let moved = try Shelf.gatherForMove(docs: docs)
        XCTAssertEqual(moved.lastPathComponent, "readsync")
        XCTAssertEqual(names(moved), ["books"])
        XCTAssertEqual(names(moved.appendingPathComponent("books")), ["a"])
        XCTAssertEqual(names(docs), ["debug.js", "readsync"])  // nothing else goes with it
        // the system's picker moves that folder into iCloud Drive: iCloud Drive/readsync/books/a, the Mac's layout
        let drive = docs.appendingPathComponent("drive", isDirectory: true)
        try fm.createDirectory(at: drive, withIntermediateDirectories: true)
        try fm.moveItem(at: moved, to: drive.appendingPathComponent(moved.lastPathComponent))
        let landed = drive.appendingPathComponent("readsync", isDirectory: true)
        XCTAssertEqual(Shelf.booksRoot(of: landed, chosen: true).path, drive.appendingPathComponent("readsync/books").path)
        XCTAssertTrue(fm.fileExists(atPath: drive.appendingPathComponent("readsync/books/a/book.toml").path))
    }

    func testMoveCalledOffPutsTheLibraryBack() throws {
        Shelf.settleOwnLibrary(docs: docs)
        try book(books, "a")
        _ = try Shelf.gatherForMove(docs: docs)
        Shelf.settleOwnLibrary(docs: docs)  // `unmove`, or the next launch
        XCTAssertEqual(names(docs), ["books"])
        XCTAssertEqual(names(books), ["a"])
    }

    // ---- a library opened in iCloud Drive: the books added here brought into it ----

    func testBooksToBringAndBring() throws {
        let own = books, there = docs.appendingPathComponent("drive/readsync/books", isDirectory: true)
        try book(own, "a", id: "1111")
        try book(own, "b", id: "2222")  // the library has it by id, under another name
        try book(own, "c", id: "3333")  // its name is taken there by another book
        try book(there, "b-mac", id: "2222")
        try book(there, "c", id: "9999")
        XCTAssertEqual(Shelf.booksToBring(from: own, to: there), ["a"])
        XCTAssertEqual(Shelf.booksToBring(from: own, to: own), [])  // the same folder: nothing to bring
        XCTAssertEqual(Shelf.bring(["a"], from: own, to: there), 1)
        XCTAssertEqual(names(there), ["a", "b-mac", "c"])
        XCTAssertEqual(names(there.appendingPathComponent("a/state")), ["phone.json"])
        XCTAssertEqual(names(own), ["b", "c"])  // the ones it has stay where they were
        XCTAssertEqual(Shelf.broughtLine(moved: 3, already: 0), "Перенесено: 3")
        XCTAssertEqual(Shelf.broughtLine(moved: 2, already: 1), "Перенесено: 2, одна уже была")
        XCTAssertEqual(Shelf.broughtLine(moved: 1, already: 2), "Перенесено: 1, 2 уже были")
    }

    // ---- «Перенести библиотеку»: the books moved into a folder picked in iCloud Drive, nothing replaced ----

    func testMoveIntoAnExistingLibraryReplacesNothing() throws {
        let own = books
        try book(own, "a", id: "1111", title: "phone a")  // its name is taken there by another book
        try book(own, "b", id: "2222")  // the library has it by id, under another name
        try book(own, "c", id: "3333")
        let drive = docs.appendingPathComponent("drive", isDirectory: true)
        let library = drive.appendingPathComponent("readsync/books", isDirectory: true)
        try book(library, "a", id: "9999", title: "mac a")
        try book(library, "b-mac", id: "2222")
        let n = try Shelf.moveBooks(from: own, into: drive)
        XCTAssertEqual(n.moved, 2)
        XCTAssertEqual(n.already, 1)
        XCTAssertEqual(n.failed, 0)
        // the phone's «a» goes in beside the other book of that name, never left behind
        XCTAssertEqual(names(library), ["a", "a-2", "b-mac", "c"])
        let there = try String(contentsOf: library.appendingPathComponent("a/book.toml"), encoding: .utf8)
        XCTAssertTrue(there.contains("mac a"))  // the book already there is as it was
        let moved = try String(contentsOf: library.appendingPathComponent("a-2/book.toml"), encoding: .utf8)
        XCTAssertTrue(moved.contains("phone a"))
        XCTAssertEqual(names(library.appendingPathComponent("a/state")), ["phone.json"])
        XCTAssertEqual(names(library.appendingPathComponent("a-2/state")), ["phone.json"])  // the state goes with a book
        XCTAssertEqual(names(library.appendingPathComponent("c/state")), ["phone.json"])
        XCTAssertEqual(names(own), ["b"])  // the one it has stays here, deleted from nowhere
        XCTAssertEqual(Shelf.booksRoot(of: drive, chosen: true).path, library.path)  // the library read from now on
        XCTAssertEqual(Shelf.movedLine(n), "Перенесено: 2, уже были: 1")
        XCTAssertEqual(Shelf.movedLine((moved: 3, already: 0, failed: 0)), "Перенесено: 3")
        XCTAssertEqual(Shelf.movedLine((moved: 1, already: 0, failed: 2)), "Перенесено: 1, не перенесены: 2")
        // moved again: everything is there already, and nothing moves
        let again = try Shelf.moveBooks(from: own, into: drive)
        XCTAssertEqual(again.moved, 0)
        XCTAssertEqual(again.already, 1)
        XCTAssertEqual(names(own), ["b"])
        XCTAssertEqual(Shelf.freeName("a", taken: ["a", "a-2"]), "a-3")
        XCTAssertEqual(Shelf.freeName("a", taken: ["b"]), "a")
    }

    // a book the library has already by id: what this device read of it in the own library joins its state there
    func testStateOfABookThereAlreadyIsJoined() throws {
        let own = books, library = docs.appendingPathComponent("drive/readsync/books", isDirectory: true)
        let device = "0123456789abcdef0123456789abcdef"
        let mine = try book(own, "a", id: "1111")
        let there = try book(library, "a-mac", id: "1111")
        let only = try book(own, "b", id: "2222")
        try book(library, "b", id: "2222")
        let state = { (st: [String: Any]) in try JSONSerialization.data(withJSONObject: st) }
        try state(["pos": 50, "posAt": 300, "shelf": "reading", "shelfAt": 100,
                   "stats": ["days": ["2026-10-01": ["sec": 60, "words": 100], "2026-10-02": ["sec": 30, "words": 40]]]])
            .write(to: mine.appendingPathComponent("state/\(device).json"))
        try state(["pos": 10, "posAt": 200, "shelf": "paused", "shelfAt": 400,
                   "stats": ["days": ["2026-10-01": ["sec": 60, "words": 100]]]])
            .write(to: there.appendingPathComponent("state/\(device).json"))
        try state(["mode": "pages", "modeAt": 5]).write(to: only.appendingPathComponent("state/\(device).json"))

        Shelf.mergeOwnState(from: mine, into: there, device: device)
        Shelf.mergeOwnState(from: only, into: library.appendingPathComponent("b"), device: device)
        let read = { (dir: URL) in
            try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: dir.appendingPathComponent("state/\(device).json"))) as? [String: Any])
        }
        let joined = try read(there)
        XCTAssertEqual(ReadingState.num(joined["pos"]), 50)  // the newer of each key
        XCTAssertEqual(joined["shelf"] as? String, "paused")
        let days = try XCTUnwrap((joined["stats"] as? [String: Any])?["days"] as? [String: [String: Any]])
        XCTAssertEqual(ReadingState.num(days["2026-10-01"]?["sec"]), 60)  // in both: counted once
        XCTAssertEqual(ReadingState.num(days["2026-10-02"]?["sec"]), 30)
        // none there: copied
        XCTAssertEqual(try read(library.appendingPathComponent("b"))["mode"] as? String, "pages")
    }

    // «Открыть папку в iCloud Drive» on a folder with no library in it: readsync/books is made there, as a move makes it
    @MainActor
    func testFolderOpenedForSyncGetsALibrary() async throws {
        let shelf = try scratchShelf()
        let drive = docs.appendingPathComponent("drive", isDirectory: true)
        try fm.createDirectory(at: drive.appendingPathComponent("notes"), withIntermediateDirectories: true)
        let chosen = await shelf.chooseLibrary(drive)
        XCTAssertTrue(chosen)
        XCTAssertEqual(shelf.booksRoot?.path, drive.appendingPathComponent("readsync/books").path)
        // a folder with a library keeps it
        let mac = docs.appendingPathComponent("mac", isDirectory: true)
        try book(mac.appendingPathComponent("books"), "x", id: "1")
        let again = await shelf.chooseLibrary(mac)
        XCTAssertTrue(again)
        XCTAssertEqual(shelf.booksRoot?.path, mac.appendingPathComponent("books").path)
        XCTAssertFalse(fm.fileExists(atPath: mac.appendingPathComponent("readsync").path))
    }

    // the app's own Documents chosen as a folder is named as Files names it
    func testSourceNameOfTheAppsOwnFolder() {
        XCTAssertEqual(Shelf.sourceName(docs, docs: docs), "readsync")
        XCTAssertEqual(Shelf.sourceName(docs.appendingPathComponent("books"), docs: docs), "books")
        XCTAssertNil(Shelf.sourceName(nil, docs: docs))
    }

    func testMoveMakesTheLibraryWhereThereIsNone() throws {
        let own = books
        try book(own, "a", id: "1111")
        // any folder: readsync/books in it, the Mac's layout
        let drive = docs.appendingPathComponent("drive", isDirectory: true)
        try fm.createDirectory(at: drive.appendingPathComponent("notes"), withIntermediateDirectories: true)
        XCTAssertEqual(try Shelf.moveBooks(from: own, into: drive).moved, 1)
        XCTAssertEqual(names(drive), ["notes", "readsync"])
        XCTAssertEqual(names(drive.appendingPathComponent("readsync/books")), ["a"])
        XCTAssertEqual(Shelf.booksRoot(of: drive, chosen: true).path, drive.appendingPathComponent("readsync/books").path)
        // a folder named readsync: books in it
        try book(own, "b", id: "2222")
        let readsync = docs.appendingPathComponent("other/readsync", isDirectory: true)
        try fm.createDirectory(at: readsync, withIntermediateDirectories: true)
        XCTAssertEqual(try Shelf.moveBooks(from: own, into: readsync).moved, 1)
        XCTAssertEqual(names(readsync.appendingPathComponent("books")), ["b"])
        XCTAssertEqual(Shelf.booksRoot(of: readsync, chosen: true).path, readsync.appendingPathComponent("books").path)
        // a folder holding books itself: they go in beside them
        try book(own, "c", id: "3333")
        let shelf = docs.appendingPathComponent("shelf", isDirectory: true)
        try book(shelf, "x", id: "4444")
        XCTAssertEqual(try Shelf.moveBooks(from: own, into: shelf).moved, 1)
        XCTAssertEqual(names(shelf), ["c", "x"])
        XCTAssertEqual(Shelf.booksRoot(of: shelf, chosen: true), shelf)
        XCTAssertEqual(names(own), [])
    }

    // ---- adding a book from a file ----

    func testImportName() {
        let name = { (file: String) in Shelf.importName(URL(fileURLWithPath: "/x/" + file)) }
        XCTAssertEqual(name("Bulgakov_Master_i_Margarita.fb2.zip"), "Bulgakov Master i Margarita")
        XCTAssertEqual(name("Мастер и Маргарита.FB2"), "Мастер и Маргарита")
        XCTAssertEqual(name("logic.pdf"), "logic")
        XCTAssertEqual(name("a.epub"), "a")
        XCTAssertEqual(name("notes.txt"), "notes")
        XCTAssertEqual(name(".pdf"), ".pdf")  // nothing left: the file's own name
    }

    func testDuplicateBySameTitleAndAuthor() {
        let have = [
            Book(slug: "master", toml: "title = \"Мастер и Маргарита\"\nauthor = \"Михаил Булгаков\""),
            Book(slug: "eugene", toml: "title = \"Евгений Онегин\"\nauthor = \"Пушкин\""),
        ]
        let dup = { (t: String, a: String) in Shelf.duplicate(title: t, author: a, slug: "new", in: have)?.slug }
        XCTAssertEqual(dup("мастер  и маргарита", "Михаил Булгаков"), "master")
        XCTAssertNil(dup("Мастер и Маргарита", "Другой"))
        XCTAssertNil(dup("Собачье сердце", "Михаил Булгаков"))
        XCTAssertEqual(dup("Евгений Онегин", "Пушкин"), "eugene")
        // the new book itself is never its own twin
        XCTAssertNil(Shelf.duplicate(title: "Евгений Онегин", author: "Пушкин", slug: "eugene", in: have))
    }

    func testFailureNotice() {
        XCTAssertNil(Shelf.failureNotice([]))
        XCTAssertEqual(
            Shelf.failureNotice([("scan.pdf", "В PDF нет текста — похоже на скан")]),
            ImportNotice(title: "scan.pdf", message: "В PDF нет текста — похоже на скан"))
        XCTAssertEqual(
            Shelf.failureNotice([("a.doc", "Этот формат не открыть. Подходят fb2, epub, pdf, txt"), ("b.zip", "В архиве нет книги")]),
            ImportNotice(
                title: "Не добавлены: 2",
                message: "a.doc — Этот формат не открыть. Подходят fb2, epub, pdf, txt\nb.zip — В архиве нет книги"))
        XCTAssertEqual(Shelf.importReason(CocoaError(.fileWriteOutOfSpace)), "Недостаточно места")
        XCTAssertEqual(Shelf.importReason(CocoaError(.fileReadCorruptFile)), "Файл повреждён")
    }
}
