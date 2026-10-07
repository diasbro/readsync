// The library in iCloud Drive, as Obsidian keeps a vault there: the books move to
// iCloud Drive/readsync/books and `booksDir` points at them, so the iPhone sees the same library and
// every device's reading position meets in it. Off by default; the reader turns it on in the menu.

import Foundation

enum Cloud {
    static let drive = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Mobile Documents/com~apple~CloudDocs")
    static var library = drive.appendingPathComponent("readsync/books")
    static var link = booksDir  // the path the server is given; a link to wherever the library is
    private static let localKey = "localLibrary"
    private static let adoptedKey = "adoptedLibrary"  // the iCloud library came from elsewhere: never move it out

    static var isAvailable: Bool { FileManager.default.fileExists(atPath: drive.path) }

    /// The iCloud library came from elsewhere: turning it off moves nothing back.
    static var isAdopted: Bool { UserDefaults.standard.bool(forKey: adoptedKey) }

    /// Whether the books the server reads are the ones in iCloud.
    static var isOn: Bool {
        link.resolvingSymlinksInPath().path == library.resolvingSymlinksInPath().path
    }

    /// Books (and the shared library files) in a folder, without the placeholder git keeps there,
    /// Finder's notes and half-written temporary files: those are leftovers, not part of the library.
    private static func entries(_ dir: URL) -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.filter { $0 != ".gitkeep" && $0 != ".DS_Store" && !$0.hasSuffix(".tmp") }
            .map { dir.appendingPathComponent($0) }
    }

    static var localBytes: Int64 {
        let local = link.resolvingSymlinksInPath()
        let walker = FileManager.default.enumerator(at: local, includingPropertiesForKeys: [.fileSizeKey])
        var total: Int64 = 0
        while let url = walker?.nextObject() as? URL {
            total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
        return total
    }

    /// Room left on the disk the books are copied back to when syncing is turned off, when the system will say.
    static var localFreeBytes: Int64? {
        let local = URL(fileURLWithPath: UserDefaults.standard.string(forKey: localKey) ?? link.path)
        let values = try? local.deletingLastPathComponent().resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage
    }

    /// Room left in the iCloud account, when the system will say.
    static var freeBytes: Int64? {
        let out = run("/usr/bin/brctl", ["quota"], timeout: 15).1
        return out.split(separator: " ").first.flatMap { Int64($0) }
    }

    /// iCloud already holds a library, from another Mac or an earlier switch.
    static var hasLibrary: Bool { !entries(library).isEmpty }

    enum Move { case done, failed(String) }

    private struct Refusal: LocalizedError {
        let errorDescription: String?
        init(_ text: String) { errorDescription = text }
    }

    /// Moves are all or nothing: a name already taken at a destination stops them before anything
    /// moves, and a failure partway (in the moves or in `then`) puts back what had moved, so the
    /// library is never split between this Mac and iCloud. `copying` leaves the originals in place;
    /// a failure then removes the copies, half-made ones too.
    private static func move(
        _ moves: [(from: URL, to: URL)], copying: Bool = false, then finish: () throws -> Void
    ) throws {
        let fm = FileManager.default
        let taken = moves.filter { fm.fileExists(atPath: $0.to.path) }.map(\.to.lastPathComponent)
        if !taken.isEmpty { throw Refusal("Там уже есть: \(taken.joined(separator: ", "))") }
        var done: [(from: URL, to: URL)] = []
        do {
            for m in moves {
                if copying {
                    try fm.copyItem(at: m.from, to: m.to)
                } else {
                    try fm.moveItem(at: m.from, to: m.to)
                }
                done.append(m)
            }
            try finish()
        } catch {
            var stuck: [String] = []
            if copying {  // nothing was at these places before: whatever is there now is a copy
                for m in moves where fm.fileExists(atPath: m.to.path) {
                    if (try? fm.removeItem(at: m.to)) == nil { stuck.append(m.to.path) }
                }
                done = []
            }
            for m in done.reversed() {
                try? fm.createDirectory(at: m.from.deletingLastPathComponent(), withIntermediateDirectories: true)
                if (try? fm.moveItem(at: m.to, to: m.from)) == nil { stuck.append(m.to.path) }
            }
            if stuck.isEmpty { throw error }
            throw Refusal("\(error.localizedDescription)\nНе вернулись на место: \(stuck.joined(separator: ", "))")
        }
    }

    /// Move this Mac's books into iCloud and serve them from there. When iCloud already holds a
    /// library, `adopt` serves that one and keeps the local books aside, untouched.
    static func turnOn(adopt: Bool) -> Move {
        let fm = FileManager.default
        let local = link.resolvingSymlinksInPath()
        do {
            try fm.createDirectory(at: library, withIntermediateDirectories: true)
            if adopt {
                var kept = local
                var aside: [(from: URL, to: URL)] = []
                if (try? fm.destinationOfSymbolicLink(atPath: link.path)) == nil, fm.fileExists(atPath: link.path) {
                    // the books sit right in the app's folder: set them aside under another name, untouched
                    kept = link.deletingLastPathComponent().appendingPathComponent("books-local")
                    aside = [(link, kept)]
                }
                try move(aside) { try pointBooks(at: library) }
                UserDefaults.standard.set(kept.path, forKey: localKey)
                UserDefaults.standard.set(true, forKey: adoptedKey)
            } else {
                // books move one by one: the local folder stays (git keeps a placeholder in a clone's books/)
                let books = entries(local).map { ($0, library.appendingPathComponent($0.lastPathComponent)) }
                try move(books) { try pointBooks(at: library) }
                UserDefaults.standard.set(local.path, forKey: localKey)
                UserDefaults.standard.set(false, forKey: adoptedKey)
            }
            log("library in iCloud: \(library.path)")
            return .done
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    /// Serve the books from this Mac again: the folder they came from, with a copy of the iCloud library.
    static func turnOff() -> Move {
        let fm = FileManager.default
        let local = URL(fileURLWithPath: UserDefaults.standard.string(forKey: localKey) ?? link.path)
        var unlinked = false
        do {
            if same(local, link), (try? fm.destinationOfSymbolicLink(atPath: link.path)) != nil {
                try fm.removeItem(at: link)  // the books lived right here before: a folder again, not a link
                unlinked = true
            }
            try fm.createDirectory(at: local, withIntermediateDirectories: true)
            // a library another Mac keeps in iCloud stays there; only this Mac's own books come back.
            // Even the books this Mac moved in are shared by now: the phone and other Macs add books
            // and keep their reading state inside every book, and what of it is this Mac's own cannot
            // be told apart cleanly. So nothing leaves iCloud: this Mac takes a copy of all of it and
            // stops syncing, the other devices go on reading as before.
            let adopted = UserDefaults.standard.bool(forKey: adoptedKey)
            let books = adopted ? [] : entries(library).map { ($0, local.appendingPathComponent($0.lastPathComponent)) }
            try move(books, copying: true) { try pointBooks(at: local) }
            log("library back on this Mac: \(local.path)")
            return .done
        } catch {
            if unlinked, entries(link).isEmpty {  // nothing came back: the link to iCloud is put back as it was
                try? fm.removeItem(at: link)
                try? fm.createSymbolicLink(at: link, withDestinationURL: library)
            }
            return .failed(error.localizedDescription)
        }
    }

    /// The same place on disk, even through a link in a parent folder (/tmp and /private/tmp).
    /// The last component is not resolved: it may be the very link being compared.
    private static func same(_ a: URL, _ b: URL) -> Bool {
        let norm = { (u: URL) in u.deletingLastPathComponent().resolvingSymlinksInPath().appendingPathComponent(u.lastPathComponent).path }
        return norm(a) == norm(b)
    }

    /// `link` is a link to wherever the library is; the server and the pipeline follow it.
    private static func pointBooks(at target: URL) throws {
        let fm = FileManager.default
        if (try? fm.destinationOfSymbolicLink(atPath: link.path)) != nil {
            try fm.removeItem(at: link)
        } else if fm.fileExists(atPath: link.path) {
            if same(target, link) { return }  // the books are served right from this folder, even an empty one
            // a real folder that still holds books is never replaced, nor one with a half-written file
            // (*.tmp) in it; only Finder's notes and the placeholder go with it
            let left = ((try? fm.contentsOfDirectory(atPath: link.path)) ?? [])
                .filter { $0 != ".gitkeep" && $0 != ".DS_Store" }
            if !left.isEmpty { throw Refusal("В \(link.path) остались: \(left.joined(separator: ", "))") }
            try fm.removeItem(at: link)
        }
        if same(target, link) { return }
        try fm.createSymbolicLink(at: link, withDestinationURL: target)
    }
}
