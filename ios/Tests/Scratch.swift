// What tests of a whole Shelf share: a library of their own in a temporary Documents, settings in a suite of
// their own, and the app's own data (its Documents, its staging folders) left alone.

import XCTest

@testable import Readsync

extension XCTestCase {
    func scratchFolder(_ name: String = "readsync-test") throws -> URL {
        let fm = FileManager.default
        let dir = fm.temporaryDirectory.appendingPathComponent("\(name)-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        addTeardownBlock { try? fm.removeItem(at: dir) }
        return dir
    }

    func scratchDefaults() throws -> UserDefaults {
        let name = "readsync-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        addTeardownBlock {
            defaults.removePersistentDomain(forName: name)
            try? FileManager.default.removeItem(at: URL.libraryDirectory.appending(path: "Preferences/\(name).plist"))
        }
        return defaults
    }

    /// A shelf over `docs` (a new empty one when none). After the test, once its downloads are done, the local
    /// copies of those books go: local copies are the app's own, in one folder for every shelf.
    @MainActor
    func scratchShelf(docs: URL? = nil, defaults: UserDefaults? = nil) throws -> Shelf {
        let docs = try docs ?? scratchFolder("readsync-docs")
        let books = docs.appendingPathComponent("books", isDirectory: true)
        let shelf = Shelf(defaults: try defaults ?? scratchDefaults(), docs: docs, sweep: false)
        addTeardownBlock { @MainActor in
            let end = Date().addingTimeInterval(5)
            while shelf.copies.values.contains(where: { if case .fetching = $0 { return true } else { return false } }), Date() < end {
                try? await Task.sleep(for: .milliseconds(20))
            }
            for name in (try? FileManager.default.contentsOfDirectory(atPath: books.path)) ?? [] {
                try? FileManager.default.removeItem(at: Shelf.localDir(name))
            }
        }
        return shelf
    }
}
