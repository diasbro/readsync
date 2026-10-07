// The narrator marks a book read where its main text ends: listened across, not jumped over.

import XCTest

@testable import Readsync

final class PlayerTests: XCTestCase {
    func testCrossedByListening() {
        XCTAssertTrue(Player.crossed(prev: 969.5, now: 970.5, threshold: 970, playing: true))
        XCTAssertTrue(Player.crossed(prev: 968, now: 970, threshold: 970, playing: true))  // at 2x, landing on it
        XCTAssertFalse(Player.crossed(prev: 900, now: 975, threshold: 970, playing: true))  // a jump past it
        XCTAssertFalse(Player.crossed(prev: 963, now: 973, threshold: 970, playing: true))  // the lock screen's +10 s
        XCTAssertFalse(Player.crossed(prev: 969.5, now: 970.5, threshold: 970, playing: false))  // paused
        XCTAssertFalse(Player.crossed(prev: 969.5, now: 970.5, threshold: 970, playing: true, done: true))  // read already
        XCTAssertFalse(Player.crossed(prev: 970.5, now: 971.5, threshold: 970, playing: true))  // past it already
        XCTAssertFalse(Player.crossed(prev: 971, now: 969, threshold: 970, playing: true))  // back across it
        XCTAssertFalse(Player.crossed(prev: 1, now: 2, threshold: nil, playing: true))  // no length known
    }

    func testReadThreshold() {
        let stamped = Book(slug: "b", toml: "audio_end = 38511.2\ntext_end = 18342\nfiles = \"audio.m4a:1\"")
        XCTAssertEqual(stamped.textEnd, 18342)
        XCTAssertEqual(try XCTUnwrap(Player.readThreshold(stamped, duration: 39_624)), 38481.2, accuracy: 0.001)
        // stamped before the end of the text was measured: a minute before the end of the file
        let old = Book(slug: "b", toml: "files = \"audio.m4a:1\"")
        XCTAssertNil(old.audioEnd)
        XCTAssertEqual(Player.readThreshold(old, duration: 39_624), 39_564)
        XCTAssertNil(Player.readThreshold(old, duration: 0))
    }

    // the rate the page asks for: 0.5…3, and none that is no number
    func testRateIsClamped() {
        XCTAssertEqual(Player.clampRate(1.5), 1.5)
        XCTAssertEqual(Player.clampRate(10), 3)
        XCTAssertEqual(Player.clampRate(0.1), 0.5)
        XCTAssertNil(Player.clampRate(0))
        XCTAssertNil(Player.clampRate(.infinity))
        XCTAssertNil(Player.clampRate(.nan))
        XCTAssertNil(Player.clampRate(-1))
    }

    // the reader's paths name a book's own file: one out of its folder is not served
    func testPathsStayInTheBook() async {
        XCTAssertTrue(Shelf.isPlainName("audio.m4a"))
        XCTAssertFalse(Shelf.isPlainName(".."))
        XCTAssertFalse(Shelf.isPlainName("a/b"))
        XCTAssertFalse(Shelf.isPlainName(".hidden"))
        XCTAssertFalse(Shelf.isPlainName(""))
        XCTAssertFalse(Shelf.isPlainName("x..y"))
        let (status, _, _) = await Files.respond(path: "/api/state/../settings", slug: "a")
        XCTAssertEqual(status, 404)
        let (nested, _, _) = await Files.respond(path: "/api/state/a/b", slug: "a")
        XCTAssertEqual(nested, 404)
    }

    // «в 2026 — N» counts by the UTC year, as the days a book was finished on are UTC days
    func testYearIsUTC() {
        XCTAssertEqual(Shelf.utcYear(Date(timeIntervalSince1970: 1_767_225_600 - 1800)), "2025")  // 2025-12-31 23:30 UTC
        XCTAssertEqual(Shelf.utcYear(Date(timeIntervalSince1970: 1_767_225_600 + 1800)), "2026")  // 2026-01-01 00:30 UTC
    }
}
