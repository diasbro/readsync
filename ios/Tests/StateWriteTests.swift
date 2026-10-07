// This phone's writes of the reading state: none into a book folder deleted meanwhile, a refused one said so,
// and a sentence counted in another edition of the text not taken.

import XCTest

@testable import Readsync

final class StateWriteTests: XCTestCase {
    private let fm = FileManager.default

    private func bookDir() throws -> URL {
        let dir = try scratchFolder().appendingPathComponent("book", isDirectory: true)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data("files = \"book.json:5\"".utf8).write(to: dir.appendingPathComponent("book.toml"))
        return dir
    }

    // «Отовсюду», or the Mac deleted the book: a position or a session written after it makes no folder of state
    func testNoWriteIntoADeletedBook() throws {
        let dir = try bookDir()
        XCTAssertTrue(ReadingState.write(shared: dir, edition: "e1", patch: ["pos": 1.0, "posAt": 1.0]))
        try fm.removeItem(at: dir)
        XCTAssertFalse(ReadingState.write(shared: dir, edition: "e1", patch: ["pos": 2.0, "posAt": 2.0]))
        _ = ReadingState.addSession(shared: dir, edition: "e1", day: "2026-10-07", sec: 30, words: 50)
        XCTAssertNil(ReadingState.put(shared: dir, edition: "e1", patch: ["shelf": "done", "shelfAt": 3.0]))
        XCTAssertFalse(fm.fileExists(atPath: dir.path))
    }

    // the state folder of a book that is there is still made on its first write
    func testFirstWriteMakesTheStateFolder() throws {
        let dir = try bookDir()
        XCTAssertNotNil(ReadingState.put(shared: dir, edition: "e1", patch: ["shelf": "reading", "shelfAt": 1.0]))
        XCTAssertEqual(try fm.contentsOfDirectory(atPath: dir.appendingPathComponent("state").path), ["\(Device.id).json"])
    }

    // this phone's file is there but cannot be read now, nothing remembered: nothing is written, and `put` says so
    func testRefusedPutIsNotSuccess() throws {
        let dir = try bookDir()
        let state = dir.appendingPathComponent("state", isDirectory: true)
        try fm.createDirectory(at: state, withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: state.appendingPathComponent("\(Device.id).json"))
        XCTAssertFalse(ReadingState.write(shared: dir, edition: "e1", patch: ["sent": 5, "sentAt": 1000.0, "sentPct": 10]))
        XCTAssertNil(ReadingState.put(shared: dir, edition: "e1", patch: ["shelf": "done", "shelfAt": 1000.0]))
    }

    // a page showing the text the book had before (`sentEdition`) does not move the sentence of the new one
    func testSentenceOfAnotherEditionIsNotTaken() throws {
        let dir = try bookDir()
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 7, "sentAt": 100.0, "sentPct": 7, "sentEdition": "e2"])
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 3, "sentAt": 200.0, "sentPct": 3, "sentEdition": "e1"])
        var st = ReadingState.load(shared: dir, edition: "e2")
        XCTAssertEqual(ReadingState.num(st["sent"]), 7)
        // other keys of the same patch are taken
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 4, "sentAt": 300.0, "sentEdition": "e1", "mode": "pages", "modeAt": 300.0])
        st = ReadingState.load(shared: dir, edition: "e2")
        XCTAssertEqual(ReadingState.num(st["sent"]), 7)
        XCTAssertEqual(st["mode"] as? String, "pages")
        // none sent (an older page): the phone's own edition, as before
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 9, "sentAt": 400.0, "sentPct": 9])
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 9)
    }

    // an outdated copy read here: its sentences count for the library's percent while they are the newest
    func testOutdatedCopyIsMeasuredByItsOwnSentence() throws {
        let dir = try bookDir()
        ReadingState.put(shared: dir, edition: "e1", patch: ["sent": 40, "sentAt": 500.0, "sentPct": 40, "mode": "pages", "modeAt": 1.0])
        let library = ReadingState.load(shared: dir, edition: "e2")
        XCTAssertNil(library["sent"])  // the merge for the new edition drops it, as the contract says
        let own = ReadingState.load(shared: dir, edition: "e1")
        let st = Shelf.state(library, local: own)
        XCTAssertEqual(ReadingState.num(st["sentPct"]), 40)
        let book = Book(slug: "b", toml: "edition = \"e2\"\nfiles = \"book.json:5\"")
        XCTAssertEqual(Shelf.measure(book, st).fraction, 0.4, accuracy: 1e-9)
        // the new edition read since: its own sentence stands
        let newer: [String: Any] = ["sent": 2, "sentAt": 900.0, "sentPct": 2]
        XCTAssertEqual(ReadingState.num(Shelf.state(newer, local: own)["sentPct"]), 2)
        XCTAssertEqual(ReadingState.num(Shelf.state(library, local: nil)["sentPct"]), 0)
    }

    // a text extracted again on the Mac leaves editions.json: the old edition's sentence is read through it, and a
    // page still showing the old text saves its sentence in the new numbering
    func testSentenceOfAMappedEditionIsTranslated() throws {
        let dir = try bookDir()
        ReadingState.put(shared: dir, edition: "e1", patch: ["sent": 3, "sentAt": 100.0, "sentPct": 20, "sentEdition": "e1"])
        try Data(#"{"edition": "e2", "maps": {"e1": [0, 1, 1, 2, 4], "e0": "broken"}}"#.utf8)
            .write(to: dir.appendingPathComponent("editions.json"))
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e1")["sent"]), 3)
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 2)
        XCTAssertEqual(ReadingState.editions(dir).maps.keys.sorted(), ["e1"])
        XCTAssertEqual(ReadingState.editions(dir).edition, "e2")
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 4, "sentAt": 200.0, "sentPct": 30, "sentEdition": "e1"])
        let own = try JSONSerialization.jsonObject(
            with: Data(contentsOf: dir.appendingPathComponent("state/\(Device.id).json"))) as? [String: Any]
        XCTAssertEqual(ReadingState.num(own?["sent"]), 4)
        XCTAssertEqual(own?["sentEdition"] as? String, "e2")
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 9, "sentAt": 300.0, "sentEdition": "e0"])
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 4)
    }

    // editions.json names the edition its maps lead to: a text replaced since (the new book.toml here before the
    // stale map is deleted) is another edition, and the map is not used for it, nor is a file naming none
    func testAMapLeadingToAnotherEditionIsNotUsed() throws {
        let dir = try bookDir()
        try Data(#"{"edition": "e2", "maps": {"e1": [0, 5, 9, 12]}}"#.utf8).write(to: dir.appendingPathComponent("editions.json"))
        ReadingState.put(shared: dir, edition: "e3", patch: ["sent": 2, "sentAt": 100.0, "sentEdition": "e1"])
        XCTAssertNil(ReadingState.load(shared: dir, edition: "e3")["sent"])
        try Data(#"{"e1": [0, 5, 9, 12]}"#.utf8).write(to: dir.appendingPathComponent("editions.json"))
        XCTAssertTrue(ReadingState.editions(dir).maps.isEmpty)
        ReadingState.put(shared: dir, edition: "e1", patch: ["sent": 2, "sentAt": 200.0, "sentEdition": "e1"])
        XCTAssertNil(ReadingState.load(shared: dir, edition: "e2")["sent"])
    }

    // the merged sentence names the edition it counts in: a page that loaded another text can tell
    func testTheMergedSentenceNamesItsEdition() throws {
        let dir = try bookDir()
        try Data(#"{"edition": "e2", "maps": {"e1": [0, 3, 4]}}"#.utf8).write(to: dir.appendingPathComponent("editions.json"))
        XCTAssertNil(ReadingState.put(shared: dir, edition: "e2", patch: ["pos": 1.0, "posAt": 1.0])?["sentEdition"])
        let merged = ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 1, "sentAt": 2.0, "sentEdition": "e1"])
        XCTAssertEqual(ReadingState.num(merged?["sent"]), 3)
        XCTAssertEqual(merged?["sentEdition"] as? String, "e2")
        XCTAssertEqual(ReadingState.load(shared: dir, edition: "e2")["sentEdition"] as? String, "e2")
    }

    // as state.py `put`: a sentence naming an edition that is no string is dropped (a falsy one names none), and
    // a `finished` that is no list is not taken
    func testWriteTakesWhatStatePyTakes() throws {
        let dir = try bookDir()
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 7, "sentAt": 100.0, "sentPct": 7, "sentEdition": "e2"])
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 3, "sentAt": 200.0, "sentEdition": 5])
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 7)
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 4, "sentAt": 300.0, "sentEdition": ["e1"]])
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 7)
        ReadingState.put(shared: dir, edition: "e2", patch: ["sent": 9, "sentAt": 400.0, "sentEdition": 0])
        XCTAssertEqual(ReadingState.num(ReadingState.load(shared: dir, edition: "e2")["sent"]), 9)
        ReadingState.put(shared: dir, edition: "e2", patch: ["finished": ["2026-10-01"], "finishedAt": 100.0])
        ReadingState.put(shared: dir, edition: "e2", patch: ["finished": "2026-10-02", "finishedAt": 200.0])
        XCTAssertEqual(ReadingState.load(shared: dir, edition: "e2")["finished"] as? [String], ["2026-10-01"])
    }

    // a session that could not be written says so: the narrator keeps it for the next save
    func testSessionNotWrittenSaysSo() throws {
        let dir = try bookDir()
        XCTAssertTrue(ReadingState.session(shared: dir, day: "2026-10-07", sec: 30, words: 50))
        XCTAssertTrue(ReadingState.session(shared: dir, day: "2026-10-07", sec: 1, words: 0))  // too short to count
        try fm.removeItem(at: dir)
        XCTAssertFalse(ReadingState.session(shared: dir, day: "2026-10-07", sec: 30, words: 50))
    }

    // a value from the page JSON has no form for is dropped, not thrown as an exception
    func testNoNumberThatIsNotFiniteReachesJSON() throws {
        let dir = try bookDir()
        let bad: [String: Any] = ["sent": 2, "sentAt": 1.0, "sentPct": Double.nan, "opened": Double.infinity, "openedAt": 1.0]
        XCTAssertNotNil(ReadingState.put(shared: dir, edition: "e1", patch: bad))
        let st = ReadingState.load(shared: dir, edition: "e1")
        XCTAssertEqual(ReadingState.num(st["sent"]), 2)
        XCTAssertNil(st["opened"])
        let data = try XCTUnwrap(JSONSafe.data(["a": [1, Double.nan, Float.infinity] as [Any], "b": Date(), "c": true, "d": NSNull()] as [String: Any]))
        let back = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual((back["a"] as? [Any])?.count, 1)
        XCTAssertNil(back["b"])
        XCTAssertEqual(back["c"] as? Bool, true)
        XCTAssertNil(JSONSafe.data(Double.nan))
    }

    // a state file of this device joined into its copy in another library: newer per key, the days not summed twice
    func testJoinedState() {
        let mine: [String: Any] = ["sent": 5, "sentAt": 300.0, "sentPct": 5, "sentEdition": "e1", "mode": "pages", "modeAt": 10.0,
                                   "stats": ["days": ["d1": ["sec": 10, "words": 1]]]]
        let there: [String: Any] = ["sent": 2, "sentAt": 200.0, "sentPct": 2, "sentEdition": "e2", "mode": "audio", "modeAt": 20.0,
                                    "stats": ["days": ["d1": ["sec": 10, "words": 1], "d2": ["sec": 4, "words": 2]]]]
        let out = ReadingState.joined(mine, into: there)
        XCTAssertEqual(ReadingState.num(out["sent"]), 5)
        XCTAssertEqual(out["sentEdition"] as? String, "e1")  // the sentence with the text it counts in
        XCTAssertEqual(ReadingState.num(out["sentPct"]), 5)
        XCTAssertEqual(out["mode"] as? String, "audio")
        let days = (out["stats"] as? [String: Any])?["days"] as? [String: [String: Any]]
        XCTAssertEqual(ReadingState.num(days?["d1"]?["sec"]), 10)
        XCTAssertEqual(ReadingState.num(days?["d2"]?["sec"]), 4)
        XCTAssertEqual(ReadingState.joined(mine, into: [:])["mode"] as? String, "pages")
    }
}
