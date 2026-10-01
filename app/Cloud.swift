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

    /// Whether the books the server reads are the ones in iCloud.
    static var isOn: Bool {
        link.resolvingSymlinksInPath().path == library.resolvingSymlinksInPath().path
    }

    /// Books (and the shared library files) in a folder, without the placeholder git keeps there.
    private static func entries(_ dir: URL) -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return names.filter { $0 != ".gitkeep" && $0 != ".DS_Store" }.map { dir.appendingPathComponent($0) }
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

    /// Room left in the iCloud account, when the system will say.
    static var freeBytes: Int64? {
        let out = run("/usr/bin/brctl", ["quota"], timeout: 15).1
        return out.split(separator: " ").first.flatMap { Int64($0) }
    }

    /// iCloud already holds a library, from another Mac or an earlier switch.
    static var hasLibrary: Bool { !entries(library).isEmpty }

    enum Move { case done, failed(String) }

    /// Move this Mac's books into iCloud and serve them from there. When iCloud already holds a
    /// library, `adopt` serves that one and keeps the local books aside, untouched.
    static func turnOn(adopt: Bool) -> Move {
        let fm = FileManager.default
        let local = link.resolvingSymlinksInPath()
        do {
            try fm.createDirectory(at: library, withIntermediateDirectories: true)
            if adopt {
                var kept = local
                if (try? fm.destinationOfSymbolicLink(atPath: link.path)) == nil, fm.fileExists(atPath: link.path) {
                    // the books sit right in the app's folder: set them aside under another name, untouched
                    kept = link.deletingLastPathComponent().appendingPathComponent("books-local")
                    if fm.fileExists(atPath: kept.path) { throw CocoaError(.fileWriteFileExists) }
                    try fm.moveItem(at: link, to: kept)
                }
                UserDefaults.standard.set(kept.path, forKey: localKey)
                UserDefaults.standard.set(true, forKey: adoptedKey)
            } else {
                // books move one by one: the local folder stays (git keeps a placeholder in a clone's books/)
                for item in entries(local) {
                    let dest = library.appendingPathComponent(item.lastPathComponent)
                    if fm.fileExists(atPath: dest.path) { continue }
                    try fm.moveItem(at: item, to: dest)
                }
                UserDefaults.standard.set(local.path, forKey: localKey)
                UserDefaults.standard.set(false, forKey: adoptedKey)
            }
            try pointBooks(at: library)
            log("library in iCloud: \(library.path)")
            return .done
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    /// Bring the books back to the folder they came from and serve them there.
    static func turnOff() -> Move {
        let fm = FileManager.default
        let local = URL(fileURLWithPath: UserDefaults.standard.string(forKey: localKey) ?? link.path)
        do {
            if same(local, link), (try? fm.destinationOfSymbolicLink(atPath: link.path)) != nil {
                try fm.removeItem(at: link)  // the books lived right here before: a folder again, not a link
            }
            try fm.createDirectory(at: local, withIntermediateDirectories: true)
            // a library another Mac keeps in iCloud stays there; only this Mac's own books come back
            for item in UserDefaults.standard.bool(forKey: adoptedKey) ? [] : entries(library) {
                let dest = local.appendingPathComponent(item.lastPathComponent)
                if fm.fileExists(atPath: dest.path) { continue }
                try fm.moveItem(at: item, to: dest)
            }
            try pointBooks(at: local)
            log("library back on this Mac: \(local.path)")
            return .done
        } catch {
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
            if !entries(link).isEmpty { return }  // a real folder that still holds books is never replaced
            try fm.removeItem(at: link)
        }
        if same(target, link) { return }
        try fm.createSymbolicLink(at: link, withDestinationURL: target)
    }
}
