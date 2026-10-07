// «Синхронизация» on the phone: the round and its line, what stops it, where the library is read from,
// «Читаю сейчас», and the lock-screen switch moved into the reader's settings.

import XCTest

@testable import Readsync

final class SyncTests: XCTestCase {
    private func book(_ slug: String, _ files: String = "book.json:5") -> Book {
        Book(slug: slug, toml: "edition = \"e1\"\nfiles = \"\(files)\"")
    }

    private func pending(_ books: [Book], copies: [String: Copy] = [:], skip: Set<String> = []) -> [(slug: String, size: Int64)] {
        Shelf.toSync(books, copies: copies, skip: skip, busy: []).map { (slug: $0.slug, size: $0.bytes) }
    }

    // ---- where the library is read from ----

    func testSourceLabel() {
        // the folder's name; «(iCloud)» only for a folder there
        XCTAssertEqual(Shelf.sourceLabel(name: "books", iCloud: true, lost: false), "Источник: books (iCloud)")
        XCTAssertEqual(Shelf.sourceLabel(name: "Книги", iCloud: false, lost: false), "Источник: Книги")
        XCTAssertEqual(Shelf.sourceLabel(name: nil, iCloud: false, lost: false), "Источник: books")  // none chosen
        XCTAssertEqual(Shelf.sourceLabel(name: "books", iCloud: true, lost: true), "Источник недоступен")
        let drive = "/private/var/mobile/Library/Mobile Documents/com~apple~CloudDocs/readsync/books"
        XCTAssertTrue(Shelf.isICloud(path: drive, ubiquitous: nil))
        XCTAssertTrue(Shelf.isICloud(path: "/x/books", ubiquitous: true))
        let mine = "/private/var/mobile/Containers/Data/Application/A1/Documents/readsync"
        XCTAssertFalse(Shelf.isICloud(path: mine, ubiquitous: false))
        XCTAssertFalse(Shelf.isICloud(path: mine, ubiquitous: nil))
    }

    // ---- the round and its line ----

    func testSyncProgressCounting() {
        let books = [book("a", "book.json:100"), book("b", "book.json:300"), book("c", "book.json:600")]
        var round = SyncRound()
        round.plan(pending(books))
        XCTAssertEqual(round.order, ["a", "b", "c"])
        var n = round.count(copies: [:])
        XCTAssertEqual(n.done, 0)
        XCTAssertEqual(n.total, 3)
        XCTAssertEqual(n.fraction, 0)
        round.current = "a"
        n = round.count(copies: ["a": .fetching(0.5)])  // half of a: 50 of 1000 bytes
        XCTAssertEqual(n.done, 0)
        XCTAssertEqual(n.fraction, 0.05, accuracy: 1e-9)
        round.finish("a")
        XCTAssertEqual(round.next, "b")
        round.current = "b"
        n = round.count(copies: ["a": .here, "b": .fetching(0.5)])
        XCTAssertEqual(n.done, 1)
        XCTAssertEqual(n.total, 3)
        XCTAssertEqual(n.fraction, 0.25, accuracy: 1e-9)  // 100 + 150 of 1000
        round.finish("b")
        round.finish("c")
        n = round.count(copies: [:])
        XCTAssertEqual(n.done, 3)
        XCTAssertEqual(n.fraction, 1)
        XCTAssertNil(round.next)
        // books of no size known: counted by books
        var light = SyncRound()
        light.plan([(slug: "x", size: 0), (slug: "y", size: 0)])
        light.finish("x")
        XCTAssertEqual(light.count(copies: [:]).fraction, 0.5)
    }

    func testCancelGoesOnToTheNextAndSkipsTheBook() {
        let books = ["a", "b", "c"].map { book($0) }
        var round = SyncRound()
        round.plan(pending(books))
        round.current = "a"
        // the ring tapped: the book joins the skip set, the round drops it and goes on with the next
        let skip: Set<String> = ["a"]
        round.drop("a")
        round.plan(pending(books, copies: ["a": .absent], skip: skip))
        XCTAssertEqual(round.next, "b")
        XCTAssertEqual(round.order, ["b", "c"])
        XCTAssertEqual(round.count(copies: [:]).total, 2)  // not counted at all
        // a later round does not start it again either
        var later = SyncRound()
        later.plan(pending(books, skip: skip))
        XCTAssertEqual(later.order, ["b", "c"])
    }

    @MainActor
    func testDownloadByHandClearsTheSkip() async throws {
        let name = "readsync-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer {
            defaults.removePersistentDomain(forName: name)
            try? FileManager.default.removeItem(at: URL.libraryDirectory.appending(path: "Preferences/\(name).plist"))
        }
        let shelf = try scratchShelf(defaults: defaults)
        let slug = "zz-test-\(UUID().uuidString)"
        shelf.inLibrary = [slug]
        shelf.removeHere(slug)
        XCTAssertEqual(shelf.skip, [slug])
        // the reader downloads it by hand: «Синхронизация» keeps it again (the copy itself fails: no such book)
        await shelf.fetch(book(slug), byHand: true)?.value
        XCTAssertEqual(shelf.skip, [])
    }

    func testFailedCopyGoesOnAndComesBackNextRound() {
        let books = ["a", "b"].map { book($0) }
        var round = SyncRound()
        round.plan(pending(books))
        round.current = "a"
        round.finish("a")  // failed: tried once this round
        let copies: [String: Copy] = ["a": .failed("файлы ещё в iCloud")]
        round.plan(pending(books, copies: copies))
        XCTAssertEqual(round.next, "b")
        XCTAssertEqual(round.count(copies: copies).done, 1)
        // not skipped: the next round tries it again
        var next = SyncRound()
        next.plan(pending(books, copies: copies))
        XCTAssertEqual(next.next, "a")
    }

    func testDownloadByHandIsNotTheRounds() {
        let books = ["a", "b", "c"].map { book($0) }
        var round = SyncRound()
        round.plan(pending(books))
        round.current = "a"
        // «b» started by hand while «a» is copied: the round leaves it to its own ring and does not count it
        let copies: [String: Copy] = ["a": .fetching(0.2), "b": .fetching(0.4)]
        round.plan(pending(books, copies: copies))
        XCTAssertEqual(round.order, ["a", "c"])
        XCTAssertEqual(round.next, "c")
        XCTAssertEqual(round.count(copies: copies).total, 2)
        // the book the round copies stays its own while a hand download of it joins the copy
        XCTAssertEqual(round.current, "a")
    }

    func testBookGoneFromTheLibraryIsDropped() {
        let books = ["a", "b", "c"].map { book($0) }
        var round = SyncRound()
        round.plan(pending(books))
        round.current = "b"
        // «c» left the library before its turn, «b» while it was copied
        round.plan(pending(books.filter { $0.slug != "c" }))
        XCTAssertEqual(round.order, ["b", "a"])  // the one being copied ahead of those still to come
        round.drop("b")
        XCTAssertEqual(round.order, ["a"])
        XCTAssertNil(round.current)
    }

    func testSwitchedOnAgainKeepsTheBookBeingCopied() {
        let round = SyncRound(current: "a")
        XCTAssertEqual(round.order, ["a"])
        XCTAssertNil(round.next)
        XCTAssertEqual(round.count(copies: ["a": .fetching(0.5)]).total, 1)
    }

    func testWhatStopsARound() {
        let root = URL(fileURLWithPath: "/lib/books")
        let gb: Int64 = 1 << 30
        XCTAssertNil(Shelf.syncStop(started: root, now: root, reachable: true, free: 2 * gb, need: gb))
        XCTAssertNil(Shelf.syncStop(started: root, now: root, reachable: true, free: nil, need: gb))  // not known: on
        XCTAssertEqual(Shelf.syncStop(started: root, now: nil, reachable: false, free: 2 * gb, need: gb), .folder)
        XCTAssertEqual(Shelf.syncStop(started: root, now: root, reachable: false, free: 2 * gb, need: gb), .folder)
        XCTAssertEqual(
            Shelf.syncStop(started: root, now: URL(fileURLWithPath: "/other"), reachable: true, free: 2 * gb, need: gb), .changed)
        // the book and a margin
        XCTAssertEqual(Shelf.syncStop(started: root, now: root, reachable: true, free: gb + 10, need: gb), .space)
        XCTAssertNil(Shelf.syncStop(started: root, now: root, reachable: true, free: gb + Shelf.roomMargin, need: gb))
    }

    // iCloud is asked at a round's start only for the books the phone has room for: each twice (its download,
    // then the copy) above the margin, in the round's order
    func testPrefetchKeepsToTheRoom() {
        let mb: Int64 = 1 << 20
        let books = [(slug: "a", size: 100 * mb), (slug: "b", size: 300 * mb), (slug: "c", size: 10 * mb)]
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin + 200 * mb), ["a"])
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin + 800 * mb), ["a", "b"])
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin + 820 * mb), ["a", "b", "c"])
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin), [])
        XCTAssertEqual(Shelf.prefetch(books, free: nil), ["a", "b", "c"])  // not known: as before
        // a book too big is passed over: the smaller ones after it are still asked for
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin + 220 * mb), ["a", "c"])
        XCTAssertEqual(Shelf.prefetch(books, free: Shelf.roomMargin + 20 * mb), ["c"])
    }

    // a book the phone has no room for is left; the round goes on with the rest and does not count it
    func testNoRoomForOneBookLeavesOnlyIt() {
        let books = [book("big", "book.json:900"), book("a", "book.json:10"), book("b", "book.json:20")]
        var round = SyncRound()
        round.plan(pending(books))
        XCTAssertEqual(round.next, "big")
        round.pass("big")
        round.plan(pending(books))  // still wanted by the library: not taken up again this round
        XCTAssertEqual(round.order, ["a", "b"])
        XCTAssertEqual(round.next, "a")
        XCTAssertEqual(round.count(copies: [:]).total, 2)
        // the next round tries it again
        var next = SyncRound()
        next.plan(pending(books))
        XCTAssertEqual(next.next, "big")
    }

    // a book a round failed to copy is left by the next rounds until its book.toml changes
    func testFailedInARoundWaitsForANewManifest() {
        let failed = book("a", "book.json:5")
        let failedThen = ["a": Shelf.signature(failed)]
        let copies: [String: Copy] = ["a": .failed("книга ещё синхронизируется")]
        XCTAssertEqual(Shelf.toSync([failed, book("b")], copies: copies, skip: [], busy: [], failed: failedThen).map(\.slug), ["b"])
        // stamped again (other sizes, or a new edition): tried again
        XCTAssertEqual(Shelf.toSync([book("a", "book.json:6")], copies: copies, skip: [], busy: [], failed: failedThen).map(\.slug), ["a"])
        let newEdition = Book(slug: "a", toml: "edition = \"e2\"\nfiles = \"book.json:5\"")
        XCTAssertEqual(Shelf.toSync([newEdition], copies: copies, skip: [], busy: [], failed: failedThen).map(\.slug), ["a"])
        // the signature does not depend on the order of the files
        XCTAssertEqual(Shelf.signature(book("x", "a:1,b:2")), Shelf.signature(book("x", "b:2,a:1")))
    }

    // a copy swapped in and a removal of it do not cross: called off first, nothing is swapped in; swapping,
    // the removal waits for it and then deletes what was put
    func testCopySwapAndCancelDoNotCross() throws {
        let job = CopyJob()
        var ran = false
        XCTAssertTrue(try job.commit { ran = true })
        XCTAssertTrue(ran)
        let off = CopyJob()
        off.cancel()
        ran = false
        XCTAssertFalse(try off.commit { ran = true })
        XCTAssertFalse(ran)

        let racing = CopyJob()
        let swapping = expectation(description: "swap begun"), swapped = expectation(description: "swap done")
        let lock = NSLock()
        var order: [String] = []
        DispatchQueue.global().async {
            _ = try? racing.commit {
                swapping.fulfill()
                Thread.sleep(forTimeInterval: 0.3)
                lock.withLock { order.append("swap") }
            }
            swapped.fulfill()
        }
        wait(for: [swapping], timeout: 2)
        racing.cancel()
        lock.withLock { order.append("cancel") }
        wait(for: [swapped], timeout: 2)
        XCTAssertEqual(lock.withLock { order }, ["swap", "cancel"])
        XCTAssertTrue(racing.cancelled)
    }

    func testTextOnlyStaysTextOnly() {
        let shared = book("a", "book.json:5,timing.json:7,audio.m4a:11")
        let textCopy = book("a", "book.json:5,timing.json:7")
        XCTAssertTrue(Shelf.textOnly(asked: nil, local: textCopy, shared: shared))  // an update keeps the kind
        XCTAssertFalse(Shelf.textOnly(asked: nil, local: shared, shared: shared))
        XCTAssertFalse(Shelf.textOnly(asked: nil, local: nil, shared: shared))  // a new one comes whole
        XCTAssertFalse(Shelf.textOnly(asked: false, local: textCopy, shared: shared))  // «Скачать со звуком»
        XCTAssertFalse(Shelf.textOnly(asked: true, local: nil, shared: book("t")))  // no audio to leave out
        XCTAssertEqual(
            Shelf.parts(of: shared, textOnly: true).reduce(Int64(0)) { $0 + $1.size }, 12)  // what the round counts
    }

    // ---- «Читаю сейчас» ----

    func testReadingBooksTogether() {
        let books = ["a", "b", "c", "d", "e"].map { Book(slug: $0, toml: "title = \"\($0)\"") }
        let progress: [String: Readsync.Progress] = [
            "a": .init(opened: 10, status: .reading),
            "b": .init(opened: 30, status: .reading),
            "c": .init(opened: 20, status: .reading),
            "d": .init(opened: 40, status: .paused),
            "e": .init(fraction: 0.4, opened: 5),  // begun, no status: being read too
        ]
        let all = Set(books.map(\.slug))
        XCTAssertEqual(Shelf.reading(books, progress: progress, readable: all).map(\.slug), ["b", "c", "a", "e"])
        // the card is the last opened with a copy here; the rest follow by opened
        XCTAssertEqual(Shelf.reading(books, progress: progress, readable: ["a", "c"]).map(\.slug), ["c", "b", "a", "e"])
        XCTAssertEqual(Shelf.reading(books, progress: progress, readable: []).map(\.slug), ["b", "c", "a", "e"])
        // «Все книги» does not repeat them
        let (rest, _) = Shelf.sections(books, progress: progress, reading: ["a", "b", "c", "e"])
        XCTAssertEqual(rest.map(\.slug), ["d"])
    }

    // ---- «Текст на экране блокировки», one of the reader's settings now ----

    func testLockTextMigration() throws {
        let old: [String: Any] = ["lockText": true, "settings": ["font": 20], "settingsAt": 100]
        let moved = try XCTUnwrap(AppSettings.migrated(old, now: 500))
        XCTAssertNil(moved["lockText"])
        let settings = try XCTUnwrap(moved["settings"] as? [String: Any])
        XCTAssertEqual(settings["lockText"] as? Bool, true)
        XCTAssertEqual(settings["font"] as? Int, 20)
        XCTAssertEqual(ReadingState.num(moved["settingsAt"]), 500)  // newer than the page's cached copy
        // no reader settings saved yet
        let bare = try XCTUnwrap(AppSettings.migrated(["lockText": false], now: 7))
        XCTAssertEqual((bare["settings"] as? [String: Any])?["lockText"] as? Bool, false)
        // the reader's own value wins; the old key just goes
        let both = try XCTUnwrap(AppSettings.migrated(["lockText": true, "settings": ["lockText": false], "settingsAt": 9], now: 50))
        XCTAssertEqual((both["settings"] as? [String: Any])?["lockText"] as? Bool, false)
        XCTAssertEqual(ReadingState.num(both["settingsAt"]), 9)
        XCTAssertNil(both["lockText"])
        // once moved, nothing more to do
        XCTAssertNil(AppSettings.migrated(moved, now: 600))
        XCTAssertNil(AppSettings.migrated([:], now: 600))
    }
}
