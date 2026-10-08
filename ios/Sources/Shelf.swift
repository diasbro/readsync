// The library on the phone: the app's own folder in Files (Documents/books), or a folder of the reader's
// choosing, the one shared through iCloud Drive with «Синхронизация». Text books are added here from files;
// audiobooks come only from the shared library. A book is read and played from a copy the app keeps for
// itself, so iOS never takes it away mid-chapter.

import Combine
import Foundation
import SwiftUI
import UIKit

/// A book as its book.toml describes it. The manifest (`files`) says what a whole copy weighs.
struct Book: Identifiable, Hashable {
    let slug: String
    var id: String { slug }
    var title = ""
    var author = ""
    var narrator = ""
    var bookID = ""
    var edition = ""
    var files: [String: Int64] = [:]
    /// Where the main text ends, as the pipeline measured it: the first sentence of the back matter, and the
    /// end of its last word in the narration. None in a book stamped before they were.
    var textEnd: Int?
    var audioEnd: Double?

    var audioName: String? { ["audio.m4a", "audio.mp3"].first { files[$0] != nil } }
    var hasAudio: Bool { audioName != nil && files["timing.json"] != nil }
    var bytes: Int64 { files.values.reduce(0, +) }

    init(slug: String, toml: String) {
        self.slug = slug
        let t = Toml.parse(toml)
        title = t["title"] ?? slug
        author = t["author"] ?? ""
        narrator = t["narrator"] ?? ""
        bookID = t["id"] ?? ""
        edition = t["edition"] ?? ""
        textEnd = t["text_end"].flatMap { Int($0) }
        audioEnd = t["audio_end"].flatMap { Double($0) }
        for item in (t["files"] ?? "").split(separator: ",") {
            let parts = item.split(separator: ":")
            if parts.count == 2, let n = Int64(parts[1]) { files[String(parts[0])] = n }
        }
    }
}

enum Toml {
    /// The flat `key = "value"` lines readsync writes; nothing else of TOML is needed here.
    static func parse(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        for line in text.split(whereSeparator: \.isNewline) {
            guard let eq = line.firstIndex(of: "=") else { continue }
            let key = line[..<eq].trimmingCharacters(in: .whitespaces)
            var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            if value.count >= 2, value.hasPrefix("\""), value.hasSuffix("\"") {
                value = String(value.dropFirst().dropLast())
                    .replacingOccurrences(of: "\\\"", with: "\"")
                    .replacingOccurrences(of: "\\\\", with: "\\")
            }
            out[key] = value
        }
        return out
    }
}

/// How far into a book the reader is, by any device, and when it was last opened.
struct Progress: Equatable {
    var fraction = 0.0
    var opened = 0.0
    var pages = false  // last read as pages: the way back in is reading, not listening
    var status = BookStatus.none
    var rereading = false
    var finished: [String] = []
    var finishedOn: String?  // the day it was last finished, `YYYY-MM-DD`
}

/// `textOnly`: a copy of an audiobook without its audio, read as pages.
enum Copy: Equatable {
    case absent, fetching(Double), here, textOnly, outdated, failed(String)

    /// A copy the reader can open.
    var isReadable: Bool { self == .here || self == .textOnly || self == .outdated }

    var isFailed: Bool {
        if case .failed = self { return true }
        return false
    }
}

@MainActor
final class Shelf: ObservableObject {
    static let shared = Shelf()

    @Published private(set) var books: [Book] = []
    @Published private(set) var copies: [String: Copy] = [:]
    @Published private(set) var progress: [String: Progress] = [:]
    @Published private(set) var folderName = ""
    /// What each local copy weighs, by its own manifest: the library's «Загружено» line.
    @Published private(set) var localBytes: [String: Int64] = [:]
    @Published var message = ""
    /// A folder was chosen but cannot be reached now: nothing read here is saved.
    @Published private(set) var folderLost = false
    /// Books of the shared library, as the last refresh found them: «Удалить → Отовсюду» is for these.
    @Published var inLibrary: Set<String> = []
    /// The chosen folder is in iCloud Drive: «Синхронизация» shares the library with the other devices.
    @Published private(set) var inICloud = false
    /// The round «Синхронизация» is going through: «Синхронизация · 4 из 15» over the books. None between rounds.
    @Published private(set) var round: SyncRound?

    private let fm = FileManager.default
    private let defaults: UserDefaults
    /// The app's Documents: its own library is `books` in it. Another folder only in tests.
    private let ownDocs: URL
    private let sweeps: Bool  // staging folders left by a killed app are cleared: not by a test's shelf
    private var scoped: URL?
    private var refreshGen = 0  // one per refresh begun: an older one never lays its list over a newer one's
    private var listedGen = 0  // the refresh whose list `books` shows
    private var measuredGen = 0  // the refresh whose measure `progress` comes from
    private var readSeq = 0  // one per read of the reading state: per book the newest read stands
    private var progressRead: [String: Int] = [:]  // the read each entry of `progress` came from
    private var listed = false  // a refresh has listed the library: a duplicate is looked for in it
    private var syncing = false
    private var syncingAll = false  // the round is the launch argument's one go
    private var syncingNow: String?  // the book the round is copying now
    private var roundWait: Waiter?  // the round waiting for that copy: a cancel lets it go on at once
    private var launched = false  // the first refresh has run: `-fetchAll YES` acts once, after it
    private var jobs: [String: CopyJob] = [:]
    /// Books a round failed to copy, with their manifest then (`signature`): the next rounds leave them
    /// until it changes; a download by hand always tries.
    private var failedRound: [String: String] = [:]
    private var tasks: [String: Task<Void, Never>] = [:]  // a fetch still ending: the next one of the book waits for it
    private var watch: AnyCancellable?
    private let bookmarkKey = "libraryBookmark"
    private let folderLostMessage = "Нет доступа к папке библиотеки — прогресс не сохраняется"
    /// The library's «Скрывать прочитанные» and «Синхронизация». Not in AppSettings: the reader's save
    /// there keeps only the keys it knows of and would switch them off.
    nonisolated static let dropReadKey = "dropRead"
    nonisolated static let syncKey = "sync"
    private let skipKey = "syncSkip"

    /// Books the reader took off this phone: «Синхронизация» leaves them until one is downloaded by hand.
    private(set) var skip: Set<String> {
        get { Set(defaults.stringArray(forKey: skipKey) ?? []) }
        set { defaults.set(newValue.sorted(), forKey: skipKey) }
    }

    /// Where the app keeps its own copies; made once.
    nonisolated static let localRoot: URL = {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("books", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true  // the Mac holds the originals
        var copy = dir
        try? copy.setResourceValues(values)
        return dir
    }()

    nonisolated static func localDir(_ slug: String) -> URL { localRoot.appendingPathComponent(slug, isDirectory: true) }

    /// The shared library: the chosen folder, or the app's own folder in Files. None while the chosen one
    /// cannot be reached: reading state written to the app's folder instead would be lost to the other devices.
    var sharedRoot: URL? {
        if let scoped { return scoped }
        return folderLost ? nil : ownDocs
    }

    /// The app's own folder in Files (На iPhone → readsync).
    nonisolated static var docs: URL { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0] }

    /// The app's own library, made on the first launch with nothing to choose: На iPhone → readsync → books.
    nonisolated static var ownBooks: URL { docs.appendingPathComponent("books", isDirectory: true) }

    /// This shelf's own library: Documents/books.
    var ownLibrary: URL { ownDocs.appendingPathComponent("books", isDirectory: true) }

    /// «Источник: books» in the library's menu; whether it is in iCloud the «Синхронизация» switch tells.
    var sourceLabel: String { Self.sourceLabel(name: Self.sourceName(scoped, docs: ownDocs), iCloud: inICloud, lost: folderLost) }

    /// The chosen folder's name as Files shows it: the app's own Documents is «readsync» there.
    nonisolated static func sourceName(_ url: URL?, docs: URL) -> String? {
        guard let url else { return nil }
        return url.standardizedFileURL.path == docs.standardizedFileURL.path ? "readsync" : url.lastPathComponent
    }

    /// Where the library is read from: the chosen folder, or with none chosen the app's own books folder in
    /// Files. Only a folder in iCloud says so, in brackets; no device is named.
    nonisolated static func sourceLabel(name: String?, iCloud: Bool, lost: Bool) -> String {
        if lost { return "Источник недоступен" }
        return "Источник: \(name ?? "books")" + (iCloud ? " (iCloud)" : "")
    }

    /// A folder in iCloud Drive: so its resource values say, or its path (under Mobile Documents) does.
    nonisolated static func isICloud(path: String, ubiquitous: Bool?) -> Bool {
        ubiquitous == true || path.contains("/Mobile Documents/")
    }

    nonisolated static func isICloud(_ url: URL) -> Bool {
        return isICloud(path: url.path, ubiquitous: (try? url.resourceValues(forKeys: [.isUbiquitousItemKey]))?.isUbiquitousItem)
    }

    /// A folder of the reader's choosing, not the app's own one in Files.
    var folderChosen: Bool { scoped != nil || folderLost }

    /// The folder that holds the book folders.
    var booksRoot: URL? {
        guard let sharedRoot else { return nil }
        return Self.booksRoot(of: sharedRoot, chosen: scoped != nil)
    }

    /// The app's own library keeps its books in `books`, always; a chosen folder in its `books` subfolder
    /// when it has one (the Mac's `readsync` folder), or in `readsync/books` (iCloud Drive itself chosen),
    /// else in itself.
    nonisolated static func booksRoot(of root: URL, chosen: Bool) -> URL {
        let inner = root.appendingPathComponent("books", isDirectory: true)
        if !chosen || FileManager.default.fileExists(atPath: inner.path) { return inner }
        let library = root.appendingPathComponent("readsync/books", isDirectory: true)
        return FileManager.default.fileExists(atPath: library.path) ? library : root
    }

    /// The app's own library put in its one place, Documents/books, on a launch with no folder chosen: book
    /// folders left in Documents itself (put there by hand) or in Documents/readsync (a move into iCloud Drive
    /// begun and called off) go there, and the emptied `readsync` goes. Only folders with a book.toml move;
    /// a name already taken in `books` leaves the book where it is. How many moved.
    @discardableResult
    nonisolated static func settleOwnLibrary(docs: URL) -> Int {
        let fm = FileManager.default
        let books = docs.appendingPathComponent("books", isDirectory: true)
        let gathered = docs.appendingPathComponent("readsync", isDirectory: true)
        let gatheredBooks = gathered.appendingPathComponent("books", isDirectory: true)
        try? fm.createDirectory(at: books, withIntermediateDirectories: true)
        var moved = 0
        for dir in [docs, gathered, gatheredBooks] {
            for item in (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [] {
                let name = item.lastPathComponent
                guard !name.hasPrefix("."), fm.fileExists(atPath: item.appendingPathComponent("book.toml").path) else { continue }
                let dest = books.appendingPathComponent(name, isDirectory: true)
                if fm.fileExists(atPath: dest.path) {
                    print("readsync: \(name) stays in \(dir.lastPathComponent): the library has one of that name")
                    continue
                }
                do {
                    try fm.moveItem(at: item, to: dest)
                    moved += 1
                } catch {
                    print("readsync: \(name) not moved into the library: \(error)")
                }
            }
        }
        for dir in [gatheredBooks, gathered] {
            let left = ((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []).filter { !$0.hasPrefix(".") }
            if fm.fileExists(atPath: dir.path), left.isEmpty { try? fm.removeItem(at: dir) }
        }
        return moved
    }

    /// The own library made one folder, Documents/readsync with `books` in it: what the move of earlier
    /// versions handed the system's picker. A library left so is put back by `settleOwnLibrary`.
    nonisolated static func gatherForMove(docs: URL) throws -> URL {
        let fm = FileManager.default
        settleOwnLibrary(docs: docs)
        let books = docs.appendingPathComponent("books", isDirectory: true)
        let outer = docs.appendingPathComponent("readsync", isDirectory: true)
        let inner = outer.appendingPathComponent("books", isDirectory: true)
        try fm.createDirectory(at: outer, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: inner.path) {
            try fm.moveItem(at: books, to: inner)
            return outer
        }
        for item in try fm.contentsOfDirectory(at: books, includingPropertiesForKeys: nil) where !item.lastPathComponent.hasPrefix(".") {
            let dest = inner.appendingPathComponent(item.lastPathComponent, isDirectory: true)
            if !fm.fileExists(atPath: dest.path) { try fm.moveItem(at: item, to: dest) }
        }
        return outer
    }

    /// `docs` and `sweep`: tests keep to a folder of their own and leave the app's staging folders alone.
    init(defaults: UserDefaults = .standard, docs: URL = Shelf.docs, sweep: Bool = true) {
        self.defaults = defaults
        ownDocs = docs
        sweeps = sweep
        defaults.register(defaults: [Self.syncKey: true])
        restoreFolder()
        sweepStaging()
        // the narrator let go of a book: an update it held back can come in now
        watch = Player.shared.$book.map { $0?.slug }.removeDuplicates().dropFirst()
            .sink { [weak self] _ in Task { await self?.sync() } }
    }

    // ---- the folder ----

    private func restoreFolder() {
        guard let data = defaults.data(forKey: bookmarkKey) else {
            folderName = "readsync"
            Self.settleOwnLibrary(docs: ownDocs)  // a user of a chosen folder is left as they are
            return
        }
        var stale = false
        if let url = try? URL(resolvingBookmarkData: data, options: [], relativeTo: nil, bookmarkDataIsStale: &stale),
            url.startAccessingSecurityScopedResource() || fm.isReadableFile(atPath: url.path)
        {
            let wasLost = folderLost
            scoped = url
            folderLost = false
            // the narrator's book was loaded with nowhere to write: its state goes to the folder now back
            if wasLost { Player.shared.libraryMoved() }
            if message == folderLostMessage { message = "" }
            folderName = url.lastPathComponent
            inICloud = Self.isICloud(url)
            if stale, let fresh = try? url.bookmarkData() { defaults.set(fresh, forKey: bookmarkKey) }
        } else {
            print("readsync: the library folder's bookmark did not resolve")  // seen with `devicectl … --console`
            folderLost = true
            message = folderLostMessage
            folderName = "папка недоступна"
            inICloud = false
        }
    }

    @discardableResult
    func choose(folder url: URL) -> Bool {
        // a folder in the app's own space needs no security scope (asking for one says no): readable is enough
        let started = url.startAccessingSecurityScopedResource()
        guard started || fm.isReadableFile(atPath: url.path) else {
            message = "Нет доступа к этой папке"
            return false
        }
        adopt(url, started: started)
        return true
    }

    /// A folder opened for «Синхронизация»: the library it has, or, with none in it, `readsync/books` made
    /// there, as «Перенести библиотеку» does; then chosen.
    func chooseLibrary(_ url: URL) async -> Bool {
        let started = url.startAccessingSecurityScopedResource()
        guard started || fm.isReadableFile(atPath: url.path) else {
            message = "Нет доступа к этой папке"
            return false
        }
        _ = await Task.detached { try? Self.libraryBooks(in: url) }.value
        adopt(url, started: started)
        return true
    }

    /// `started`: access to `url` was just begun once more; the folder chosen again keeps only its first.
    private func adopt(_ url: URL, started: Bool) {
        if scoped == url {
            if started { url.stopAccessingSecurityScopedResource() }
        } else {
            scoped?.stopAccessingSecurityScopedResource()
        }
        scoped = url
        folderLost = false
        message = ""
        folderName = url.lastPathComponent
        inICloud = Self.isICloud(url)
        if let data = try? url.bookmarkData() { defaults.set(data, forKey: bookmarkKey) }
        Player.shared.libraryMoved()  // the narrator's book writes its state where it is now
        Task { await refresh() }
    }

    /// «Вернуться к своей библиотеке»: the chosen folder let go, the app's own library read again.
    func forgetFolder() {
        scoped?.stopAccessingSecurityScopedResource()
        scoped = nil
        defaults.removeObject(forKey: bookmarkKey)
        folderLost = false
        message = ""
        folderName = "readsync"
        inICloud = false
        Self.settleOwnLibrary(docs: ownDocs)
        Player.shared.libraryMoved()
        Task { await refresh() }
    }

    // ---- moving the library into iCloud Drive ----

    /// «Перенести библиотеку»: the reader picks a folder (in iCloud Drive), and the books of this library go
    /// into the library there, one by one; a book it has already stays where it is. Nothing there is
    /// replaced or deleted. The picked folder is the library's from then on. One line on how it went.
    func moveLibrary(into picked: URL) async -> String {
        guard let from = booksRoot else { return "Нет доступа к папке библиотеки" }
        let started = picked.startAccessingSecurityScopedResource()
        guard started || fm.isReadableFile(atPath: picked.path) else { return "Нет доступа к этой папке" }
        await Player.shared.flush()  // no write of the reading state on its way to the old place
        if scoped == nil { Self.settleOwnLibrary(docs: ownDocs) }  // stray books of the own library with the rest
        let moved = await Task.detached { try? Self.moveBooks(from: from, into: picked) }.value
        guard let moved else {
            if started { picked.stopAccessingSecurityScopedResource() }
            return "Не удалось создать папку библиотеки"
        }
        adopt(picked, started: started)
        let line = Self.movedLine(moved)
        return inICloud ? line : line + "\nПапка не в iCloud Drive — синхронизация выключена"
    }

    /// Where the books go in a picked folder: the library it has (its `books`, its `readsync/books`, or the
    /// folder itself when it holds books), else a new one: `books` in a `readsync`, `readsync/books` in any other.
    nonisolated static func libraryBooks(in picked: URL) throws -> URL {
        let found = booksRoot(of: picked, chosen: true)
        if found != picked || holdsBooks(picked) { return found }
        let made = picked.appendingPathComponent(picked.lastPathComponent == "readsync" ? "books" : "readsync/books", isDirectory: true)
        var error: NSError?
        var failure: Error?
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: made, options: [], error: &error) { u in
            do { try FileManager.default.createDirectory(at: u, withIntermediateDirectories: true) } catch { failure = error }
        }
        if let failure = failure ?? error { throw failure }
        return made
    }

    /// A folder with a book folder in it: one with its book.toml, downloaded or not yet.
    nonisolated static func holdsBooks(_ dir: URL) -> Bool {
        Coordinated.list(dir).filter { !$0.lastPathComponent.hasPrefix(".") }.contains { d in
            let names = (try? FileManager.default.contentsOfDirectory(atPath: d.path)) ?? []
            return names.contains("book.toml") || names.contains(".book.toml.icloud")
        }
    }

    /// The books of `from` moved into the library in `picked`: none it has by `id` or by folder name, which
    /// stay where they are. How many moved, were there already, and did not move.
    nonisolated static func moveBooks(from: URL, into picked: URL) throws -> (moved: Int, already: Int, failed: Int) {
        let to = try libraryBooks(in: picked)
        guard from.standardizedFileURL != to.standardizedFileURL else { return (0, bookIDs(from, coordinated: false).count, 0) }
        let there = Dictionary(bookIDs(to, coordinated: true).filter { !$0.id.isEmpty }.map { ($0.id, $0.slug) }) { a, _ in a }
        var taken = Set((try? FileManager.default.contentsOfDirectory(atPath: to.path)) ?? [])
        var n = (moved: 0, already: 0, failed: 0)
        for book in bookIDs(from, coordinated: false).sorted(by: { $0.slug < $1.slug }) {
            let src = from.appendingPathComponent(book.slug, isDirectory: true)
            if !book.id.isEmpty, let slug = there[book.id] {
                // there already: what this device read of it here joins its state there
                mergeOwnState(from: src, into: to.appendingPathComponent(slug, isDirectory: true))
                n.already += 1
                continue
            }
            // a name taken by another book: this one goes in beside it, as <slug>-2
            let name = freeName(book.slug, taken: taken)
            taken.insert(name)
            if moveOne(book.slug, from: src, to: to.appendingPathComponent(name, isDirectory: true)) {
                n.moved += 1
            } else {
                n.failed += 1
            }
        }
        return n
    }

    /// `slug`, or `slug-2`, `slug-3`… when the name is taken.
    nonisolated static func freeName(_ slug: String, taken: Set<String>) -> String {
        guard taken.contains(slug) else { return slug }
        var i = 2
        while taken.contains("\(slug)-\(i)") { i += 1 }
        return "\(slug)-\(i)"
    }

    /// This device's state file of a book moved over one the library has: copied there when it has none, else
    /// joined key by key, the newer value winning, as the merge does.
    nonisolated static func mergeOwnState(from src: URL, into dest: URL, device: String = Device.id) {
        let fm = FileManager.default
        let name = "state/\(device).json"
        guard let data = try? Data(contentsOf: src.appendingPathComponent(name)),
            let mine = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return }
        let target = dest.appendingPathComponent(name)
        let placeholder = dest.appendingPathComponent("state/.\(device).json.icloud")
        var there: [String: Any] = [:]
        if fm.fileExists(atPath: target.path) || fm.fileExists(atPath: placeholder.path) {
            // there but not readable now: left as it is rather than written over
            guard let d = Coordinated.read(target), let obj = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any]
            else { return }
            there = obj
        }
        guard let out = JSONSafe.data(ReadingState.joined(mine, into: there)) else { return }
        if !Coordinated.write(out, to: target) { print("readsync: the state of \(dest.lastPathComponent) not joined") }
    }

    /// «Перенесено: 3»; «Перенесено: 2, уже были: 1»; and the ones that did not move.
    nonisolated static func movedLine(_ n: (moved: Int, already: Int, failed: Int)) -> String {
        var line = "Перенесено: \(n.moved)"
        if n.already > 0 { line += ", уже были: \(n.already)" }
        if n.failed > 0 { line += ", не перенесены: \(n.failed)" }
        return line
    }

    /// The books of the app's own library to bring into the library opened now: none it has by `id`, none
    /// whose folder name it has taken.
    nonisolated static func booksToBring(from own: URL, to library: URL) -> [String] {
        let fm = FileManager.default
        guard own.standardizedFileURL != library.standardizedFileURL else { return [] }
        let there = bookIDs(library, coordinated: true)
        let ids = Set(there.map(\.id).filter { !$0.isEmpty })
        let taken = Set((try? fm.contentsOfDirectory(atPath: library.path)) ?? [])
        return bookIDs(own, coordinated: false)
            .filter { !taken.contains($0.slug) && ($0.id.isEmpty || !ids.contains($0.id)) }
            .map(\.slug).sorted()
    }

    /// The book folders of `dir` with their `id`s; `coordinated`: a folder in iCloud Drive.
    nonisolated static func bookIDs(_ dir: URL, coordinated: Bool) -> [(slug: String, id: String)] {
        let fm = FileManager.default
        let dirs = coordinated ? Coordinated.list(dir) : ((try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
        return dirs.filter { !$0.lastPathComponent.hasPrefix(".") }.compactMap { d in
            let toml = d.appendingPathComponent("book.toml")
            let data = coordinated ? Coordinated.read(toml) : try? Data(contentsOf: toml)
            guard let text = data.flatMap({ String(data: $0, encoding: .utf8) }) else { return nil }
            return (d.lastPathComponent, Book(slug: d.lastPathComponent, toml: text).bookID)
        }
    }

    /// The book folders moved, with their reading state, from `own` into `library`, as iCloud sees a move.
    /// How many moved. Never over a folder already there: a name taken meanwhile leaves the book where it was.
    nonisolated static func bring(_ slugs: [String], from own: URL, to library: URL) -> Int {
        slugs.filter { slug in
            moveOne(slug, from: own.appendingPathComponent(slug, isDirectory: true),
                    to: library.appendingPathComponent(slug, isDirectory: true))
        }.count
    }

    /// One book folder moved, as iCloud sees a move; never over a folder already there.
    nonisolated static func moveOne(_ slug: String, from src: URL, to dest: URL) -> Bool {
        if FileManager.default.fileExists(atPath: dest.path) { return false }  // nor does moveItem go over one
        var moved = false
        var error: NSError?
        let c = NSFileCoordinator(filePresenter: nil)
        c.coordinate(writingItemAt: src, options: .forMoving, writingItemAt: dest, options: .forReplacing, error: &error) { a, b in
            do {
                try FileManager.default.moveItem(at: a, to: b)
                c.item(at: a, didMoveTo: b)
                moved = true
            } catch {
                print("readsync: \(slug) not moved into the library: \(error)")
            }
        }
        return moved
    }

    /// «Перенести»: the books added here go into the library opened now; one line on how it went.
    func bring(_ slugs: [String], already: Int) async -> String {
        guard let library = booksRoot else { return "Нет доступа к папке библиотеки" }
        await Player.shared.flush()
        let own = ownLibrary
        let moved = await Task.detached { Self.bring(slugs, from: own, to: library) }.value
        Player.shared.libraryMoved()
        await refresh()
        return Self.broughtLine(moved: moved, already: already + slugs.count - moved)
    }

    /// «Перенесено: 3»; «Перенесено: 2, одна уже была».
    nonisolated static func broughtLine(moved: Int, already: Int) -> String {
        let line = "Перенесено: \(moved)"
        if already <= 0 { return line }
        return line + ", " + (already == 1 ? "одна уже была" : "\(already) уже были")
    }

    /// The books of the app's own library, by folder.
    nonisolated static func ownBookCount(_ own: URL = ownBooks) -> Int {
        let fm = FileManager.default
        return ((try? fm.contentsOfDirectory(at: own, includingPropertiesForKeys: nil)) ?? [])
            .filter { fm.fileExists(atPath: $0.appendingPathComponent("book.toml").path) }.count
    }

    // ---- adding books from files ----

    /// Files being made into books, one after another, off the main thread: «Добавляю…» rows over the list.
    @Published private(set) var adding: [Adding] = []
    /// Files that did not become books, told once the last of the files queued with them is done.
    @Published var importNotice: ImportNotice?
    /// Books just added that the library has under the same title and author: the reader decides, one at a time.
    @Published private(set) var duplicates: [Duplicate] = []
    /// A book to open as soon as it is on the shelf: the one file added alone.
    @Published var toOpen: String?
    private var importing = false
    private var batch = 0  // files queued since the queue was last empty: one alone opens when made
    private var failures: [(name: String, why: String)] = []
    private var dropped: Set<UUID> = []  // called off while being made: the book goes once it is

    /// «Добавить книгу», the picker or «Открыть в readsync»: each file a row, made in turn.
    func add(_ urls: [URL]) {
        let files = urls.filter(\.isFileURL)
        guard !files.isEmpty else { return }
        adding += files.map { Adding(url: $0, name: Self.importName($0)) }
        batch += files.count
        if !importing {
            importing = true  // now, not in the task: a second add before it starts must not start a second drain
            Task { await drainImports() }
        }
    }

    /// A row's square: a file not started yet is dropped; one being made is undone when it is.
    func cancelAdd(_ id: UUID) {
        guard let item = adding.first(where: { $0.id == id }) else { return }
        if item.started {
            dropped.insert(id)
        } else {
            batch -= 1  // one alone left of the files queued together still opens
            Self.clearInbox(item.url, docs: ownDocs)
        }
        adding.removeAll { $0.id == id }
    }

    private func drainImports() async {
        importing = true
        defer { importing = false }
        // a duplicate is looked for in the library as it is, not in the empty one of a launch just begun
        if !listed { await refresh() }
        while let i = adding.firstIndex(where: { !$0.started }) {
            adding[i].started = true
            let item = adding[i]
            guard let root = booksRoot else {
                adding.removeAll { $0.id == item.id }
                Self.clearInbox(item.url, docs: ownDocs)
                failures.append((item.url.lastPathComponent, "Нет доступа к папке библиотеки"))
                continue
            }
            try? fm.createDirectory(at: root, withIntermediateDirectories: true)
            let url = item.url, inbox = ownDocs
            let result = await Task.detached { Self.runImport(url, into: root, docs: inbox) }.value
            if dropped.remove(item.id) != nil {
                if case .success(let made) = result { await Task.detached { Self.discard(made.slug, in: root) }.value }
                continue
            }
            switch result {
            case .failure(let why):
                print("readsync: \(url.lastPathComponent) not added: \(why.message)")
                failures.append((url.lastPathComponent, why.message))
            case .success(let made):
                if let twin = Self.duplicate(title: made.title, author: made.author, slug: made.slug, in: books) {
                    duplicates.append(Duplicate(slug: made.slug, title: made.title, twin: twin.slug, root: root, alone: batch == 1))
                } else {
                    await landed(made.slug, open: batch == 1)
                }
            }
            // the row goes once the book is on the shelf: never a moment of an empty library between them
            adding.removeAll { $0.id == item.id }
            dropped.remove(item.id)  // called off while landing: it is in already
        }
        importNotice = Self.failureNotice(failures)
        failures = []
        batch = 0
    }

    /// A book made: on the shelf, its copy on the way, opened when it came alone.
    private func landed(_ slug: String, open: Bool) async {
        await refresh()
        guard let book = books.first(where: { $0.slug == slug }) else { return }
        fetch(book)
        if open { toOpen = slug }
    }

    /// The reader's answer to «… уже есть»: `keep` the new one, or not; `openTwin` the one already there.
    func settle(_ d: Duplicate, keep: Bool, openTwin: Bool = false) {
        duplicates.removeAll { $0.id == d.id }
        if keep {
            Task { await landed(d.slug, open: d.alone) }
        } else {
            let (slug, root) = (d.slug, d.root)
            // listed or copied meanwhile (a refresh, a round): nothing of it stays
            if let job = jobs.removeValue(forKey: slug) { job.cancel() }
            try? fm.removeItem(at: Self.localDir(slug))
            copies[slug] = nil  // no copy left: not «fetching» for ever
            localBytes[slug] = nil
            Task {
                await Task.detached { Self.discard(slug, in: root) }.value
                await refresh()
            }
            if openTwin { toOpen = d.twin }
        }
    }

    /// A book made from a file: the file read where it is (another app's, or iCloud Drive's, which is fetched
    /// first), and a copy handed in by another app cleared once read.
    nonisolated static func runImport(_ url: URL, into root: URL, docs: URL = Shelf.docs) -> Result<BookImport.Result, Failure> {
        let open = url.startAccessingSecurityScopedResource()
        defer {
            if open { url.stopAccessingSecurityScopedResource() }
            clearInbox(url, docs: docs)
        }
        var result: Result<BookImport.Result, Failure> = .failure(Failure(message: "Файл повреждён"))
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: url, options: .withoutChanges, error: &error) { u in
            do {
                result = .success(try BookImport.make(from: u, into: root))
            } catch let why as BookImport.Failure {
                result = .failure(Failure(message: why.message))
            } catch {
                result = .failure(Failure(message: importReason(error)))
            }
        }
        if let error { result = .failure(Failure(message: importReason(error))) }
        return result
    }

    /// What an error the importer did not word itself means to the reader.
    nonisolated static func importReason(_ error: Error) -> String {
        let e = error as NSError
        if (e.domain == NSCocoaErrorDomain && e.code == NSFileWriteOutOfSpaceError)
            || (e.domain == NSPOSIXErrorDomain && e.code == Int(ENOSPC))
        {
            return "Недостаточно места"
        }
        if e.domain == NSCocoaErrorDomain && [NSFileReadNoPermissionError, NSFileWriteNoPermissionError].contains(e.code) {
            return "Нет доступа к файлу"
        }
        return "Файл повреждён"
    }

    /// A copy another app handed in (Documents/Inbox) goes once it is read or called off.
    nonisolated static func clearInbox(_ url: URL, docs: URL) {
        let inbox = docs.appendingPathComponent("Inbox").standardizedFileURL.path + "/"
        if url.standardizedFileURL.path.hasPrefix(inbox) { try? FileManager.default.removeItem(at: url) }
    }

    nonisolated static func discard(_ slug: String, in root: URL) {
        let dir = root.appendingPathComponent(slug, isDirectory: true)
        if !Coordinated.delete(dir) { print("readsync: \(slug) not taken back out of the library") }
    }

    /// The row's name before the book is read: the file's, without its extensions, `_` as spaces.
    nonisolated static func importName(_ url: URL) -> String {
        var name = url.lastPathComponent
        for ext in [".fb2.zip", ".epub", ".fb2", ".zip", ".txt", ".pdf"] where name.lowercased().hasSuffix(ext) {
            name = String(name.dropLast(ext.count))
            break
        }
        name = name.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? url.lastPathComponent : name
    }

    /// A book of the same title and author already in the library, the new one aside.
    nonisolated static func duplicate(title: String, author: String, slug: String, in books: [Book]) -> Book? {
        func key(_ s: String) -> String {
            s.lowercased().replacingOccurrences(of: "ё", with: "е").split(whereSeparator: \.isWhitespace).joined(separator: " ")
        }
        guard !key(title).isEmpty else { return nil }
        return books.first { $0.slug != slug && key($0.title) == key(title) && key($0.author) == key(author) }
    }

    /// One alert for the files that were not added: the file and why, or how many and a line each.
    nonisolated static func failureNotice(_ list: [(name: String, why: String)]) -> ImportNotice? {
        guard let first = list.first else { return nil }
        if list.count == 1 { return ImportNotice(title: first.name, message: first.why) }
        return ImportNotice(title: "Не добавлены: \(list.count)", message: list.map { "\($0.name) — \($0.why)" }.joined(separator: "\n"))
    }

    // ---- what is on the shelf ----

    func refresh() async {
        if folderLost { restoreFolder() }  // the folder may be back (iCloud signed in again, say)
        if !folderLost { message = "" }  // a failure told before is told again by its row
        refreshGen += 1
        let gen = refreshGen
        let root = booksRoot
        let pending = Set(duplicates.map(\.slug))  // a book waiting for «… уже есть» is not the library's yet
        let found = await Task.detached { () -> [Book] in
            guard let root else { return [] }
            let fm = FileManager.default
            let dirs = (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
            var out: [Book] = []
            for dir in dirs where !dir.lastPathComponent.hasPrefix(".") {
                let toml = dir.appendingPathComponent("book.toml")
                guard let text = Coordinated.read(toml).flatMap({ String(data: $0, encoding: .utf8) }) else { continue }
                let book = Book(slug: dir.lastPathComponent, toml: text)
                // no manifest yet: still being made on the Mac
                if !book.files.isEmpty, !pending.contains(book.slug) { out.append(book) }
            }
            return out.sorted { $0.title.localizedCompare($1.title) == .orderedAscending }
        }.value
        // another folder chosen meanwhile, or a later refresh already in: this list is out of date
        guard booksRoot == root, gen > listedGen else { return }
        listedGen = gen
        listed = true
        // a copy whose book left the Mac's library (or a folder that cannot be reached right now) stays
        // readable until the reader removes it
        let shared = Set(found.map(\.slug))
        inLibrary = shared
        let local = ((try? fm.contentsOfDirectory(atPath: Self.localRoot.path)) ?? [])
            .filter { !$0.hasPrefix(".") }
            .compactMap(Self.localCopy)
        localBytes = Dictionary(uniqueKeysWithValues: local.map { ($0.slug, $0.bytes) })
        let localOnly = local.filter { !shared.contains($0.slug) }
        books = (found + localOnly).sorted { $0.title.localizedCompare($1.title) == .orderedAscending }
        for book in found {
            let now = localCopy(of: book)
            if now == .absent, copy(of: book.slug).isFailed { continue }  // the row keeps saying why
            copies[book.slug] = now
        }
        for book in localOnly where !isFetching(book.slug) { copies[book.slug] = .here }
        let all = books, dirs = Dictionary(uniqueKeysWithValues: all.compactMap { b in sharedDir(b.slug).map { (b.slug, $0) } })
        readSeq += 1
        let seq = readSeq
        let measured = await Task.detached { () -> [String: Progress] in
            var out: [String: Progress] = [:]
            let covers = (try? FileManager.default.contentsOfDirectory(atPath: Self.coverRoot.path)) ?? []
            for book in all {
                guard let dir = dirs[book.slug] else { continue }
                Self.cacheCover(book.slug, from: dir, covers: covers)
                out[book.slug] = Self.measure(book, Self.state(of: book, in: dir))
            }
            return out
        }.value
        guard booksRoot == root, gen > measuredGen else { return }
        measuredGen = gen
        Player.forgetMissingCovers()
        // a book measured again after this read began (a status just set) keeps that newer measure
        var next = measured
        for (slug, at) in progressRead where at > seq { next[slug] = progress[slug] }
        for slug in measured.keys where (progressRead[slug] ?? 0) < seq { progressRead[slug] = seq }
        progress = next
        if defaults.bool(forKey: Self.dropReadKey) {
            let here = Set(copies.filter { $0.value.isReadable }.map(\.key))
            for slug in Self.readToDrop(measured, local: here, loaded: Player.shared.slug, now: ReadingState.nowMs) {
                removeHere(slug, remember: false)  // «Синхронизация» leaves it while it is read
            }
        }
        // launched with `-fetchAll YES`: every book onto the phone once, whatever the toggle says
        let once = !launched && defaults.bool(forKey: "fetchAll")
        launched = true
        Task { await sync(all: once) }
    }

    private func isFetching(_ slug: String) -> Bool {
        if case .fetching = copies[slug] { return true }
        return false
    }

    private func localCopy(of book: Book) -> Copy {
        if isFetching(book.slug) { return copies[book.slug]! }
        return Self.copyState(local: Self.localCopy(book.slug), shared: book)
    }

    /// A local copy against the book as the Mac has it now.
    nonisolated static func copyState(local mine: Book?, shared book: Book) -> Copy {
        guard let mine else { return .absent }
        // the same edition with other sizes is new timing from the Mac's precise alignment
        guard mine.edition == book.edition else { return .outdated }
        if mine.files == book.files { return .here }
        if mine.audioName == nil, let audio = book.audioName, mine.files == book.files.filter({ $0.key != audio }) {
            return .textOnly
        }
        return .outdated
    }

    /// A book's merged state as the library measures it. A copy of an older edition read here stamps its
    /// sentences with that edition: the newer of those and the library's own is the place.
    nonisolated static func state(of book: Book, in dir: URL) -> [String: Any] {
        let local = localCopy(book.slug)?.edition
        return state(ReadingState.load(shared: dir, edition: book.edition),
                     local: local.flatMap { $0 != book.edition ? ReadingState.load(shared: dir, edition: $0) : nil })
    }

    /// `st` with the sentence of `local` (the merge for the copy's own edition) when that one is newer.
    nonisolated static func state(_ st: [String: Any], local: [String: Any]?) -> [String: Any] {
        guard let local, ReadingState.num(local["sentAt"]) > ReadingState.num(st["sentAt"]) else { return st }
        var out = st
        for key in ["sent", "sentAt", "sentPct"] { out[key] = local[key] }
        return out
    }

    /// How far into a book its merged state is, and what the reader made of it. The local timing gives the
    /// narration's length: without a copy, the page position is all there is.
    nonisolated static func measure(_ book: Book, _ st: [String: Any]) -> Progress {
        var p = Progress(opened: ReadingState.num(st["opened"]), pages: st["mode"] as? String == "pages")
        let duration = Self.duration(book.slug)
        let atEnd: Bool
        if book.hasAudio, duration > 0, st["mode"] as? String != "pages" {
            let pos = ReadingState.num(st["pos"])
            p.fraction = min(1, pos / duration)
            atEnd = pos >= duration - 60
        } else {
            p.fraction = min(1, ReadingState.num(st["sentPct"]) / 100)
            atEnd = p.fraction >= 0.99
        }
        (p.status, p.rereading, p.finishedOn) = ReadingState.status(st, audio: book.hasAudio, atEnd: atEnd)
        p.finished = st["finished"] as? [String] ?? []
        return p
    }

    /// Read books finished and last opened a week ago or more, whose copies go when «Скрывать прочитанные» is
    /// on. The reading state stays in the shared library; the book the narrator holds stays too.
    nonisolated static func readToDrop(_ progress: [String: Progress], local: Set<String>, loaded: String?, now: Double)
        -> [String]
    {
        let week = 7 * 86_400_000.0  // `opened` is in milliseconds, as the reader writes it
        return progress.filter { slug, p in
            guard local.contains(slug), slug != loaded, p.status == .done, now - p.opened >= week else { return false }
            // finished before the dates were kept and never read since: the week of `opened` is all there is
            return p.finishedOn.flatMap(day).map { now - $0 >= week } ?? true
        }.map(\.key).sorted()
    }

    /// Midnight UTC of a `YYYY-MM-DD` day, in milliseconds: the days the reader writes are UTC days.
    nonisolated static func day(_ s: String) -> Double? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f.date(from: s).map { $0.timeIntervalSince1970 * 1000 }
    }

    func copy(of slug: String) -> Copy { copies[slug] ?? .absent }

    /// Covers of books not on the phone yet: a small copy each, so the list is not a row of letters.
    nonisolated static var coverRoot: URL {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("covers")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    nonisolated static func cachedCover(_ slug: String) -> URL? {
        cachedCover(slug, in: (try? FileManager.default.contentsOfDirectory(atPath: coverRoot.path)) ?? [])
    }

    /// The same among `names`, the covers folder listed once for many books.
    nonisolated static func cachedCover(_ slug: String, in names: [String]) -> URL? {
        // exactly <slug>.<ext>: the cover of "a.b" is not the cover of "a"
        names.first { name in
            guard name.hasPrefix(slug + ".") else { return false }
            let ext = name.dropFirst(slug.count + 1)
            return !ext.isEmpty && !ext.contains(".")
        }.map { coverRoot.appendingPathComponent($0) }
    }

    /// `covers`: the covers folder as listed at the start of the refresh.
    nonisolated static func cacheCover(_ slug: String, from shared: URL, covers: [String]) {
        let local = localDir(slug).appendingPathComponent("images")
        if cachedCover(slug, in: covers) != nil
            || ((try? FileManager.default.contentsOfDirectory(atPath: local.path)) ?? []).contains(where: { $0.hasPrefix("cover.") })
        {
            return
        }
        let images = shared.appendingPathComponent("images", isDirectory: true)
        // a cover not downloaded yet is its `.cover.x.icloud` placeholder: the coordinated copy fetches it
        guard let cover = Coordinated.list(images).map(ReadingState.realName).first(where: { $0.lastPathComponent.hasPrefix("cover.") })
        else { return }
        _ = Coordinated.copy(cover, to: coverRoot.appendingPathComponent("\(slug).\(cover.pathExtension)"))
    }

    /// The length of the narration, from the head of the local timing.json (the whole file is megabytes).
    nonisolated static func duration(_ slug: String) -> Double {
        guard let h = FileHandle(forReadingAtPath: localDir(slug).appendingPathComponent("timing.json").path) else { return 0 }
        defer { try? h.close() }
        let head = String(decoding: h.readData(ofLength: 300), as: UTF8.self)
        guard let r = head.range(of: #""duration":\s*([\d.]+)"#, options: .regularExpression) else { return 0 }
        return Double(head[r].split(separator: ":").last!.trimmingCharacters(in: .whitespaces)) ?? 0
    }

    /// «Читаю сейчас»: the books being read, the one to come back to first.
    var reading: [Book] {
        Self.reading(books, progress: progress, readable: Set(copies.filter { $0.value.isReadable }.map(\.key)))
    }

    /// Every book being read, the last opened first; on top, the card, the last opened that has a copy on
    /// this phone, so one tap opens it (none has: the last opened).
    nonisolated static func reading(_ books: [Book], progress: [String: Progress], readable: Set<String>) -> [Book] {
        let all = books.filter { isCurrent(progress[$0.slug]) }.enumerated().sorted { a, b in
            let (x, y) = (progress[a.element.slug]?.opened ?? 0, progress[b.element.slug]?.opened ?? 0)
            return x != y ? x > y : a.offset < b.offset
        }.map(\.element)
        guard let card = all.firstIndex(where: { readable.contains($0.slug) }), card > 0 else { return all }
        return [all[card]] + all.enumerated().filter { $0.offset != card }.map(\.element)
    }

    /// Being read: so marked (a reread from its start too), or, with no status, opened and begun, not finished.
    /// A book opened by mistake and closed again does not take the place; a read or put-aside one never does.
    nonisolated static func isCurrent(_ p: Progress?) -> Bool {
        guard let p else { return false }
        switch p.status {
        case .reading: return true
        case .none: return p.opened > 0 && inProgress(p.fraction)
        case .paused, .done: return false
        }
    }

    nonisolated static func inProgress(_ fraction: Double) -> Bool { fraction > 0 && fraction < 0.99 }

    /// The library below «Читаю сейчас»: every book but the current one and the read ones, as `books` orders
    /// them; then the read ones, the latest finished first.
    nonisolated static func sections(_ books: [Book], progress: [String: Progress], current: String?)
        -> (all: [Book], read: [Book])
    {
        sections(books, progress: progress, reading: Set([current].compactMap { $0 }))
    }

    /// The same, with every book of «Читаю сейчас» above.
    nonisolated static func sections(_ books: [Book], progress: [String: Progress], reading: Set<String>)
        -> (all: [Book], read: [Book])
    {
        let done = { (b: Book) in progress[b.slug]?.status == .done }
        let read = books.filter(done).enumerated().sorted { a, b in
            let (x, y) = (progress[a.element.slug]?.finishedOn ?? "", progress[b.element.slug]?.finishedOn ?? "")
            return x != y ? x > y : a.offset < b.offset
        }.map(\.element)
        return (books.filter { !done($0) && !reading.contains($0.slug) }, read)
    }

    /// The year of `date` as a UTC day names it, as `finishedOn` does.
    nonisolated static func utcYear(_ date: Date) -> String {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return String(c.component(.year, from: date))
    }

    /// The read books last finished in `year` («в 2026 — 7»).
    nonisolated static func readIn(_ year: String, _ read: [Book], progress: [String: Progress]) -> Int {
        read.filter { progress[$0.slug]?.finishedOn?.hasPrefix(year + "-") == true }.count
    }

    /// The copy the reader opens: the local one, whatever the shared library says now.
    func localBook(_ slug: String) -> Book? { Self.localCopy(slug) }

    // ---- getting a copy ----

    /// The task ends once the copy is in or has failed. `textOnly`: an audiobook without its audio; left
    /// out, a copy keeps the kind it is. `byHand`: the reader asked for it, so «Синхронизация» keeps it again.
    @discardableResult
    func fetch(_ book: Book, textOnly: Bool? = nil, byHand: Bool = true) -> Task<Void, Never>? {
        if case .fetching = copy(of: book.slug) { return nil }
        if byHand { skip.remove(book.slug) }
        guard let root = booksRoot else {
            print("readsync: \(book.slug) not copied: no library folder")
            message = folderLostMessage
            return nil
        }
        let textOnly = Self.textOnly(asked: textOnly, local: Self.localCopy(book.slug), shared: book)
        if Player.shared.slug == book.slug { Player.shared.stop() }  // its files are about to be swapped under it
        sweepStaging()
        copies[book.slug] = .fetching(0)
        let source = root.appendingPathComponent(book.slug, isDirectory: true)
        let job = CopyJob(), before = tasks[book.slug]
        jobs[book.slug] = job
        let task = Task.detached { [self] in
            await before?.value  // a cancelled fetch of the same book still clearing its staging folder
            let result = job.cancelled
                ? .failure(Failure(message: "", cancelled: true))
                : Self.copyBook(book.slug, from: source, textOnly: textOnly, job: job) { done in
                    Task { @MainActor in
                        if self.jobs[book.slug] === job { self.copies[book.slug] = .fetching(done) }
                    }
                }
            await MainActor.run {
                let shelf = self
                if shelf.jobs[book.slug] === job { shelf.jobs[book.slug] = nil }
                guard !job.cancelled, shelf.isFetching(book.slug) else { return }  // cancelled or removed meanwhile
                switch result {
                case .success(let copied):
                    // against the manifest the copy was made from: a newer one stamped meanwhile is in already
                    let mine = Self.localCopy(book.slug)
                    shelf.copies[book.slug] = mine.map { Self.copyState(local: $0, shared: copied) } ?? .here
                    shelf.localBytes[book.slug] = mine?.bytes
                    shelf.failedRound[book.slug] = nil
                    Player.forgetCover(book.slug)
                    if !shelf.folderLost { shelf.message = "" }
                case .failure(let why):
                    print("readsync: \(book.slug) not copied: \(why.message)")
                    // not tried again by «Синхронизация» until its book.toml changes
                    if !byHand { shelf.failedRound[book.slug] = Self.signature(book) }
                    // a copy still here is what it was: a text-only one asked for its audio is still text-only
                    shelf.copies[book.slug] = Self.localCopy(book.slug).map { Self.copyState(local: $0, shared: book) }
                        ?? .failed(why.message)
                    shelf.message = "«\(book.title)» не скачалась: \(why.message)"
                }
            }
        }
        tasks[book.slug] = task
        Task {
            await task.value
            if tasks[book.slug] == task { tasks[book.slug] = nil }
        }
        return task
    }

    /// A download stopped half way: the copy is as it was, and «Синхронизация» leaves the book alone.
    func cancel(_ slug: String) {
        guard let job = jobs[slug] else { return }
        job.cancel()
        jobs[slug] = nil
        skip.insert(slug)
        if slug == syncingNow { roundWait?.release() }  // the round goes on, whatever the copy is stuck in
        copies[slug] = books.first { $0.slug == slug }.map { Self.copyState(local: Self.localCopy(slug), shared: $0) } ?? .absent
    }

    /// The books «Синхронизация» copies now: those not on the phone, failed, or older than the library's,
    /// but none the reader took off it, none open in the reader or held by the narrator, and none `done`
    /// (the read ones, while «Скрывать прочитанные» is on).
    /// `failed`: books a round failed to copy, by the manifest they had then; left until it changes.
    nonisolated static func toSync(
        _ books: [Book], copies: [String: Copy], skip: Set<String>, busy: Set<String>, done: Set<String> = [],
        failed: [String: String] = [:]
    ) -> [Book] {
        books.filter { book in
            guard !skip.contains(book.slug), !done.contains(book.slug) else { return false }
            if let sig = failed[book.slug], sig == signature(book) { return false }
            switch copies[book.slug] ?? .absent {
            case .absent, .failed: return true
            case .outdated: return !busy.contains(book.slug)
            default: return false
            }
        }
    }

    /// What a book.toml says of the copy: its edition and files. A new stamp on the Mac changes it.
    nonisolated static func signature(_ book: Book) -> String {
        book.edition + "|" + book.files.sorted { $0.key < $1.key }.map { "\($0.key):\($0.value)" }.joined(separator: ",")
    }

    /// A copy without the audio: as asked, or, left to the copy, the kind the phone's copy is now.
    nonisolated static func textOnly(asked: Bool?, local: Book?, shared: Book) -> Bool {
        shared.audioName != nil && (asked ?? (local.map { $0.audioName == nil } ?? false))
    }

    /// Why a round stops before its next book: the folder went or another was chosen, or the phone has no
    /// room for the book and a margin.
    enum SyncStop: Equatable { case folder, changed, space }

    nonisolated static let roomMargin: Int64 = 200 << 20

    nonisolated static func syncStop(started: URL, now: URL?, reachable: Bool, free: Int64?, need: Int64) -> SyncStop? {
        guard let now else { return .folder }
        if now.standardizedFileURL != started.standardizedFileURL { return .changed }
        if !reachable { return .folder }
        if let free, free < need + roomMargin { return .space }
        return nil
    }

    /// What the phone has room for, as iOS counts it for a download the reader asked for.
    nonisolated static var freeSpace: Int64? {
        (try? localRoot.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage
    }

    /// «Синхронизация»: every book that wants copying, one after another: all at once, iCloud would download
    /// every book's audio side by side and none would be readable for a long while. A book the reader
    /// downloads by hand is not the round's: its own ring shows it. `all`: the launch argument's one go,
    /// toggle or not, taken-off books too.
    func sync(all: Bool = false) async {
        // a source outside iCloud has nothing to keep in step with: sync waits until the library is moved there
        guard all || (defaults.bool(forKey: Self.syncKey) && inICloud), let root = booksRoot else {
            return
        }
        if syncing {
            // switched on again while the last round ends its book: the line is back at once
            if round == nil {
                round = SyncRound(current: syncingNow)
                replan()
            }
            return
        }
        syncing = true
        syncingAll = all
        var asked: [String: [URL]] = [:]
        var short = false  // a book left for want of room: the line says so to the end of the round
        defer {
            syncing = false
            syncingAll = false
            syncingNow = nil
            round = nil
            // what iCloud was asked to download for the books not copied goes back to the cloud (a copied
            // one's is gone already); one being downloaded by hand keeps it
            let left = asked.filter { !isFetching($0.key) }.values.flatMap { $0 }
            if !left.isEmpty { Task.detached { for url in left { try? FileManager.default.evictUbiquitousItem(at: url) } } }
        }
        round = SyncRound()
        replan()
        // iCloud is asked for every file still to come that the phone has room for, all at once: it downloads
        // them on its own, also while this app is suspended under a locked screen, and the copies that follow
        // take seconds, not minutes
        let order = round?.order ?? []
        let sizes = order.map { (slug: $0, size: round?.sizes[$0] ?? 0) }
        var wanted: [(slug: String, dir: URL, names: [String])] = []
        for slug in Self.prefetch(sizes, free: Self.freeSpace) {
            guard let book = books.first(where: { $0.slug == slug }) else { continue }
            let textOnly = Self.textOnly(asked: nil, local: Self.localCopy(slug), shared: book)
            wanted.append((slug, root.appendingPathComponent(slug, isDirectory: true), Self.parts(of: book, textOnly: textOnly).map(\.name)))
        }
        // off the main thread: listing a book's images is a coordinated read, which a write elsewhere holds up
        asked = await Task.detached { Self.ask(wanted) }.value
        while syncingAll || defaults.bool(forKey: Self.syncKey) {
            if round == nil { round = SyncRound() }
            replan()
            guard let slug = round?.next, let book = books.first(where: { $0.slug == slug }) else {
                return
            }
            let stop = Self.syncStop(
                started: root, now: booksRoot, reachable: fm.fileExists(atPath: root.path), free: Self.freeSpace,
                need: round?.sizes[slug] ?? book.bytes)
            switch stop {
            case .folder:
                message = folderLostMessage
                return
            case .changed:
                Task { await self.sync() }  // a round of the new folder, once this one is gone
                return
            case .space:
                // this one is left; the round goes on with the books that fit
                message = "Недостаточно места"
                short = true
                round?.pass(slug)
                continue
            case nil:
                break
            }
            round?.current = slug
            syncingNow = slug
            if let task = fetch(book, byHand: false) {
                let wait = Waiter()
                roundWait = wait
                await wait.until(task)
                roundWait = nil
            }
            syncingNow = nil
            if short, message.isEmpty { message = "Недостаточно места" }  // a copy that came in cleared it
            let gone = sharedDir(slug) == nil
            // called off by the reader, or gone from the library: not the round's any more; a failed one was
            // tried, and comes again next round
            if skip.contains(slug) || gone { round?.drop(slug) } else { round?.finish(slug) }
            if gone { forgetGone(book) }
        }
    }

    /// The books of a round iCloud is asked to download at its start, in its order: as many as the phone has
    /// room for, each counted twice (iCloud's download, then the copy) above the margin. All when not known.
    nonisolated static func prefetch(_ books: [(slug: String, size: Int64)], free: Int64?) -> [String] {
        guard let free else { return books.map(\.slug) }
        var room = free - roomMargin
        var out: [String] = []
        for (slug, size) in books {
            guard 2 * size <= room else { continue }  // too big: the smaller ones after it may fit
            room -= 2 * size
            out.append(slug)
        }
        return out
    }

    /// iCloud asked to download the files of each book, its images too: what was asked, by book.
    nonisolated static func ask(_ books: [(slug: String, dir: URL, names: [String])]) -> [String: [URL]] {
        var asked: [String: [URL]] = [:]
        for (slug, dir, names) in books {
            let urls = names.map { dir.appendingPathComponent($0) }
                + askImages(dir).map { dir.appendingPathComponent("images", isDirectory: true).appendingPathComponent($0) }
            for url in urls { try? FileManager.default.startDownloadingUbiquitousItem(at: url) }
            asked[slug] = urls
        }
        return asked
    }

    /// «Синхронизация» switched off: the book being copied finishes, no other starts, and the line goes.
    func stopSync() {
        round = nil
    }

    /// The round's books brought up to date: those that still want copying, in the library's order, none
    /// downloading by hand, none whose folder left the library.
    private func replan() {
        guard var r = round else { return }
        let busy = Set([Player.shared.slug, Player.shared.pageSlug].compactMap { $0 })
        let all = syncingAll
        let pending = Self.toSync(
            books, copies: copies, skip: all ? [] : skip, busy: busy, done: all ? [] : readLeft, failed: failedRound
        ).filter { sharedDir($0.slug) != nil }
        r.plan(pending.map { book in
            let textOnly = Self.textOnly(asked: nil, local: Self.localCopy(book.slug), shared: book)
            return (slug: book.slug, size: Self.parts(of: book, textOnly: textOnly).reduce(Int64(0)) { $0 + $1.size })
        })
        round = r
    }

    /// A book whose folder left the library while it was being copied: its row goes, unless a copy of it is
    /// on the phone, which stays readable. Its folder is never made again.
    private func forgetGone(_ book: Book) {
        if message.hasPrefix("«\(book.title)»") { message = "" }
        if Self.localCopy(book.slug) != nil {
            copies[book.slug] = .here
            return
        }
        books.removeAll { $0.slug == book.slug }
        copies[book.slug] = nil
        progress[book.slug] = nil
        inLibrary.remove(book.slug)
    }

    /// The read books «Синхронизация» leaves while «Скрывать прочитанные» is on: read again, they come back.
    private var readLeft: Set<String> {
        guard defaults.bool(forKey: Self.dropReadKey) else { return [] }
        return Set(progress.filter { $0.value.status == .done }.map(\.key))
    }

    /// Staging folders (`.<slug>.new`) a fetch left when the app was killed in the middle of it: hundreds of
    /// megabytes each. The one a fetch is filling now stays.
    private func sweepStaging() {
        guard sweeps else { return }
        for name in (try? fm.contentsOfDirectory(atPath: Self.localRoot.path)) ?? []
        where name.hasPrefix(".") && name.hasSuffix(".new") && !isFetching(String(name.dropFirst().dropLast(4))) {
            try? fm.removeItem(at: Self.localRoot.appendingPathComponent(name, isDirectory: true))
        }
    }

    struct Failure: Error {
        let message: String
        var cancelled = false
    }

    /// A name of one file or folder in its folder, never a path out of it: no "/", no "..", no leading ".".
    nonisolated static func isPlainName(_ name: String) -> Bool {
        !name.isEmpty && !name.contains("/") && !name.contains("..") && !name.hasPrefix(".")
    }

    /// The files of the manifest a copy takes, smallest first; a text-only copy leaves the audio.
    nonisolated static func parts(of book: Book, textOnly: Bool) -> [(name: String, size: Int64)] {
        book.files.filter { !textOnly || $0.key != book.audioName }
            .sorted { ($0.value, $0.key) < ($1.value, $1.key) }
            .map { (name: $0.key, size: $0.value) }
    }

    /// The manifest with `files` as given: a text-only copy says what it holds, so the reader opens it as pages.
    nonisolated static func manifest(_ toml: String, files: [(name: String, size: Int64)]) -> String {
        let line = "files = \"" + files.map { "\($0.name):\($0.size)" }.joined(separator: ",") + "\""
        var lines = toml.components(separatedBy: "\n")
        if let i = lines.firstIndex(where: { $0.split(separator: "=", maxSplits: 1).first?.trimmingCharacters(in: .whitespaces) == "files" }) {
            lines[i] = line
        } else {
            lines.insert(line, at: lines.last == "" ? lines.count - 1 : lines.count)
        }
        return lines.joined(separator: "\n")
    }

    /// The images of a book folder, each asked of iCloud at once: it downloads them side by side, so the copy
    /// does not wait for a thousand small files one after another. A file not downloaded yet is its
    /// `.name.icloud` placeholder; its real name is the one returned.
    nonisolated static func askImages(_ dir: URL) -> [String] {
        let images = dir.appendingPathComponent("images", isDirectory: true)
        let names = Set(Coordinated.list(images).map { ReadingState.realName($0).lastPathComponent })
            .filter { !$0.hasPrefix(".") }
            .sorted()
        for name in names { try? FileManager.default.startDownloadingUbiquitousItem(at: images.appendingPathComponent(name)) }
        return names
    }

    /// Copy the files the reader needs, check them against the manifest, then swap the copy in whole.
    /// `textOnly`: everything but the audio.
    nonisolated static func copyBook(
        _ slug: String, from source: URL, textOnly: Bool = false, job: CopyJob? = nil,
        progress: @escaping (Double) -> Void
    ) -> Result<Book, Failure> {
        let fm = FileManager.default
        let stopped = Failure(message: "", cancelled: true)
        // the manifest as it is now, read once: the files are checked against it and it is the one kept
        guard let toml = Coordinated.read(source.appendingPathComponent("book.toml"), job: job),
            let text = String(data: toml, encoding: .utf8)
        else { return .failure(job?.cancelled == true ? stopped : Failure(message: "нет book.toml")) }
        let book = Book(slug: slug, toml: text)
        let staging = localRoot.appendingPathComponent(".\(slug).new", isDirectory: true)
        try? fm.removeItem(at: staging)
        var whole = false
        defer { if !whole { try? fm.removeItem(at: staging) } }  // half a copy is hundreds of megabytes
        do {
            try fm.createDirectory(at: staging, withIntermediateDirectories: true)
            let wanted = parts(of: book, textOnly: textOnly)
            let images = source.appendingPathComponent("images", isDirectory: true)
            let names = askImages(source)
            // an image counts in the ring as a small file: the manifest does not know its size
            let imageWeight: Int64 = 8_192
            let total = Double(max(wanted.reduce(0) { $0 + $1.size } + Int64(names.count) * imageWeight, 1))
            var done: Int64 = 0
            for (name, size) in wanted {
                // a file of the book's folder, never a path out of it
                guard isPlainName(name) else {
                    return .failure(Failure(message: "в book.toml чужое имя файла: \(name)"))
                }
                // copied as a file, not read into memory: the audio alone is hundreds of megabytes; the ring
                // moves with every few megabytes of it
                let dest = staging.appendingPathComponent(name)
                let base = done
                guard Coordinated.copy(source.appendingPathComponent(name), to: dest, job: job, progress: { n in
                    progress(Double(base + min(n, size)) / total)
                }) else {
                    if job?.cancelled == true { return .failure(stopped) }
                    print("readsync: \(slug): \(name) did not come from iCloud")
                    return .failure(Failure(message: "файлы ещё не пришли"))
                }
                let got = ((try? fm.attributesOfItem(atPath: dest.path))?[.size] as? NSNumber)?.int64Value ?? -1
                if got != size {
                    print("readsync: \(slug): \(name) is \(got) bytes, the manifest says \(size)")
                    return .failure(Failure(message: "книга ещё синхронизируется"))
                }
                done += size
                progress(Double(done) / total)
            }
            if job?.cancelled == true { return .failure(stopped) }
            if !names.isEmpty {
                let dest = staging.appendingPathComponent("images", isDirectory: true)
                try fm.createDirectory(at: dest, withIntermediateDirectories: true)
                var told = Double(done) / total
                for (i, name) in names.enumerated() {
                    if job?.cancelled == true { return .failure(stopped) }
                    // read by its real name, an image not downloaded yet is waited for; a copy short of one is
                    // not a whole copy
                    guard let data = Coordinated.read(images.appendingPathComponent(name), job: job) else {
                        if job?.cancelled == true { return .failure(stopped) }
                        print("readsync: \(slug): images/\(name) did not come from iCloud")
                        return .failure(Failure(message: "файлы ещё не пришли"))
                    }
                    try data.write(to: dest.appendingPathComponent(name))
                    // the ring moves every 50 images or 2%, not once per image
                    let now = Double(done + Int64(i + 1) * imageWeight) / total
                    if (i + 1) % 50 == 0 || now - told >= 0.02 || i == names.count - 1 {
                        told = now
                        progress(now)
                    }
                }
            }
            // the manifest last, as on the Mac: a copy with it is a whole copy
            let kept = textOnly ? Data(manifest(text, files: wanted).utf8) : toml
            try kept.write(to: staging.appendingPathComponent("book.toml"))
            let dest = localDir(slug)
            let swap = {
                if fm.fileExists(atPath: dest.path) {
                    _ = try fm.replaceItemAt(dest, withItemAt: staging)  // one step: the old copy stays until the new one is in
                } else {
                    try fm.moveItem(at: staging, to: dest)
                }
            }
            // the last check and the swap as one: a removal of the copy waits for the swap and deletes what it put
            if let job {
                guard try job.commit(swap) else { return .failure(stopped) }
            } else {
                try swap()
            }
            whole = true
            // iCloud's own download of the book's files goes back to the cloud: the phone keeps them once, in
            // the copy. The manifest stays: every refresh reads it. In the background: one call per file waits
            // for iCloud, and a book of a thousand images would hold the round with its ring full.
            let downloaded = book.files.keys.map { source.appendingPathComponent($0) }
                + names.map { images.appendingPathComponent($0) }
            Task.detached(priority: .background) {
                for url in downloaded { try? FileManager.default.evictUbiquitousItem(at: url) }
            }
            return .success(book)
        } catch {
            whole = false
            return .failure(Failure(message: error.localizedDescription))
        }
    }

    // ---- taking books away ----

    /// «Удалить → Только здесь» (and «Скрывать прочитанные»): the copy goes, the book stays in the library.
    /// `remember`: «Синхронизация» leaves it until it is downloaded by hand; without, until it is read again.
    func removeHere(_ slug: String, remember: Bool = true) {
        if Player.shared.slug == slug { Player.shared.stop() }  // the narrator must not play a file that is gone
        if let job = jobs.removeValue(forKey: slug) { job.cancel() }
        if slug == syncingNow { roundWait?.release() }  // as a cancel: the round goes on at once
        try? fm.removeItem(at: Self.localDir(slug))
        copies[slug] = .absent
        localBytes[slug] = nil
        // with the library out of reach nothing says whether it has the book: kept off all the same
        if remember, inLibrary.contains(slug) || booksRoot == nil { skip.insert(slug) }
        if !inLibrary.contains(slug) {
            books.removeAll { $0.slug == slug }  // a copy of a book the library no longer has: nothing is left
            copies[slug] = nil
        }
    }

    // ---- the reader's status of a book ----

    /// «Читаю», «Отложена», «Прочитана» from the library. «Прочитана» adds today to the book's dates; a read
    /// book set back to «Читаю» or «Отложена» was not finished after all, and loses its latest date.
    func setStatus(_ book: Book, _ status: BookStatus) {
        write(book) { st in
            let at = ReadingState.nowMs
            var patch: [String: Any] = ["shelf": status.rawValue, "shelfAt": at]
            var finished = st["finished"] as? [String] ?? []
            if status == .done {
                if !finished.contains(ReadingState.today) { finished.append(ReadingState.today) }
            } else if let last = finished.max(), (st["shelf"] as? String) == "done" {
                finished.remove(at: finished.lastIndex(of: last)!)
            } else {
                return patch
            }
            patch["finished"] = finished
            patch["finishedAt"] = at
            return patch
        }
    }

    /// «Перечитать»: back to «Читаю» from the start; the dates it was read on stay.
    func reread(_ book: Book) {
        if Player.shared.slug == book.slug { Player.shared.seek(0) }
        write(book) { _ in
            let at = ReadingState.nowMs
            return ["shelf": "reading", "shelfAt": at, "pos": 0, "posAt": at, "sent": 0, "sentAt": at, "sentPct": 0]
        }
    }

    /// A change to the book's state worked out from its merged state, written to this phone's file; the
    /// library then shows it.
    private func write(_ book: Book, patch: @escaping @Sendable ([String: Any]) -> [String: Any]) {
        guard let dir = sharedDir(book.slug) else { return }  // the folder cannot be reached: nowhere to write
        Task.detached {
            let st = ReadingState.load(shared: dir, edition: book.edition)
            guard let out = ReadingState.put(shared: dir, edition: book.edition, patch: patch(st)) else {
                // this phone's file could not be read (not downloaded, offline): nothing was saved
                await MainActor.run { self.message = "«\(book.title)»: не сохранено, попробуй ещё раз" }
                return
            }
            let merged = JSONBox(out)
            await MainActor.run { Player.shared.stateChanged(book.slug, merged.value as? [String: Any] ?? [:]) }
            await self.remeasure(book)
        }
    }

    /// One book's progress read again, after a write.
    func remeasure(_ book: Book) async {
        // the book as the library has it, as a refresh measures it: not an older edition's local copy
        let book = books.first { $0.slug == book.slug } ?? book
        guard let dir = sharedDir(book.slug) else { return }
        readSeq += 1
        let seq = readSeq
        let p = await Task.detached { Self.measure(book, Self.state(of: book, in: dir)) }.value
        guard seq > progressRead[book.slug] ?? 0 else { return }  // a newer read is in already
        progress[book.slug] = p
        progressRead[book.slug] = seq
    }

    /// «Удалить → Отовсюду»: the book's folder leaves the shared library, so every device loses it.
    func removeEverywhere(_ book: Book) {
        let dir = sharedDir(book.slug)
        removeHere(book.slug)
        skip.remove(book.slug)
        books.removeAll { $0.slug == book.slug }
        copies[book.slug] = nil
        progress[book.slug] = nil
        inLibrary.remove(book.slug)
        if let cover = Self.cachedCover(book.slug) { try? fm.removeItem(at: cover) }
        Player.forgetCover(book.slug)
        guard let dir else { return }
        Task.detached {
            let ok = Coordinated.delete(dir)
            if !ok { print("readsync: \(book.slug) not deleted from the library") }
            await MainActor.run {
                if !ok { self.message = "«\(book.title)» не удалилась из библиотеки" }
                Task { await self.refresh() }
            }
        }
    }

    /// The shared folder of one book: where its reading state lives. None while the chosen folder cannot be
    /// reached, or when the library has no such book any more: the state is then neither read nor written,
    /// and a book deleted elsewhere does not come back as a folder of state.
    func sharedDir(_ slug: String) -> URL? {
        guard let dir = booksRoot?.appendingPathComponent(slug, isDirectory: true) else { return nil }
        return fm.fileExists(atPath: dir.path) ? dir : nil
    }
}

/// A file being made into a book: its row in the library until it is one.
struct Adding: Identifiable, Equatable {
    let id = UUID()
    let url: URL
    let name: String
    var started = false
}

/// A book just added whose title and author the library has already (`twin`).
struct Duplicate: Identifiable, Equatable {
    let id = UUID()
    let slug: String
    let title: String
    let twin: String
    let root: URL
    let alone: Bool  // added alone: opened once kept
}

struct ImportNotice: Equatable {
    let title: String
    let message: String
}

/// The books one round of «Синхронизация» copies, one at a time: «Синхронизация · 4 из 15».
struct SyncRound: Equatable {
    private(set) var order: [String] = []
    private(set) var sizes: [String: Int64] = [:]
    private(set) var finished: Set<String> = []  // copied, or tried and failed
    private(set) var passed: Set<String> = []  // no room for it: left for this round
    var current: String?

    init(current: String? = nil) {
        self.current = current
        if let current { order = [current] }
    }

    /// The books still to copy, as the library has them now: those done and the one being copied stay,
    /// the rest follow `pending`.
    mutating func plan(_ pending: [(slug: String, size: Int64)]) {
        let kept = order.filter { finished.contains($0) || $0 == current }
        order = kept + pending.map(\.slug).filter { !kept.contains($0) && !passed.contains($0) }
        for (slug, size) in pending { sizes[slug] = size }
        sizes = sizes.filter { order.contains($0.key) }
    }

    var next: String? { order.first { !finished.contains($0) && $0 != current } }

    mutating func finish(_ slug: String) {
        finished.insert(slug)
        if current == slug { current = nil }
    }

    /// No room for it on the phone: left by this round, which goes on with the rest; not counted.
    mutating func pass(_ slug: String) {
        drop(slug)
        passed.insert(slug)
    }

    /// Called off, or gone from the library: not counted at all.
    mutating func drop(_ slug: String) {
        order.removeAll { $0 == slug }
        finished.remove(slug)
        sizes[slug] = nil
        if current == slug { current = nil }
    }

    /// The books done and all of them, and how far the round is by bytes, the one being copied with its ring.
    func count(copies: [String: Copy]) -> (done: Int, total: Int, fraction: Double) {
        let done = order.filter(finished.contains)
        let all = order.reduce(Int64(0)) { $0 + (sizes[$1] ?? 0) }
        var now = 0.0
        if let current, case .fetching(let p) = copies[current] { now = p }
        let fraction: Double
        if all > 0 {
            let bytes = Double(done.reduce(Int64(0)) { $0 + (sizes[$1] ?? 0) }) + now * Double(sizes[current ?? ""] ?? 0)
            fraction = bytes / Double(all)
        } else {
            fraction = order.isEmpty ? 0 : (Double(done.count) + now) / Double(order.count)
        }
        return (done.count, order.count, min(1, fraction))
    }
}

/// The end of a copy, or the reader calling it off: whichever comes first.
@MainActor final class Waiter {
    private var waiting: CheckedContinuation<Void, Never>?
    private var released = false

    func until(_ task: Task<Void, Never>) async {
        Task { await task.value; self.release() }
        await withCheckedContinuation { c in
            if released { c.resume() } else { waiting = c }
        }
    }

    func release() {
        released = true
        waiting?.resume()
        waiting = nil
    }
}

/// One download, to be called off: a wait for iCloud is given up, a copy stops at its next piece.
final class CopyJob: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var coordinator: NSFileCoordinator?

    var cancelled: Bool { lock.withLock { stopped } }

    /// Waits for a `commit` under way.
    func cancel() {
        let c: NSFileCoordinator? = lock.withLock {
            stopped = true
            return coordinator
        }
        c?.cancel()
    }

    /// `body` run unless the job is called off, with no cancel in between: one that comes waits for it.
    /// False when it was called off.
    func commit(_ body: () throws -> Void) throws -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if stopped { return false }
        try body()
        return true
    }

    /// The coordinator now waiting for iCloud; false once the job is called off.
    func waiting(on c: NSFileCoordinator?) -> Bool {
        lock.withLock {
            coordinator = c
            return !stopped
        }
    }
}

/// Reads and writes that iCloud's file provider sees: a file not downloaded yet is fetched first, and a
/// write is announced so it gets uploaded.
enum Coordinated {
    /// `job`: a wait for iCloud it can give up, as a copy's.
    static func read(_ url: URL, job: CopyJob? = nil) -> Data? {
        var out: Data?
        var error: NSError?
        let c = NSFileCoordinator(filePresenter: nil)
        guard job?.waiting(on: c) ?? true else { return nil }
        defer { _ = job?.waiting(on: nil) }
        c.coordinate(readingItemAt: url, options: [], error: &error) { u in
            out = try? Data(contentsOf: u)
        }
        return out
    }

    /// A copy in pieces of a few megabytes: `progress` hears the bytes copied, and a job called off stops it.
    static func copy(_ url: URL, to dest: URL, job: CopyJob? = nil, progress: ((Int64) -> Void)? = nil) -> Bool {
        var ok = false
        var error: NSError?
        let c = NSFileCoordinator(filePresenter: nil)
        guard job?.waiting(on: c) ?? true else { return false }
        defer { _ = job?.waiting(on: nil) }
        c.coordinate(readingItemAt: url, options: [], error: &error) { u in
            guard let input = try? FileHandle(forReadingFrom: u),
                FileManager.default.createFile(atPath: dest.path, contents: nil),
                let output = try? FileHandle(forWritingTo: dest)
            else { return }
            defer {
                try? input.close()
                try? output.close()
            }
            var copied: Int64 = 0
            while true {
                if job?.cancelled == true { return }
                let chunk: Data?
                do { chunk = try input.read(upToCount: 4 << 20) } catch { return }
                guard let chunk, !chunk.isEmpty else { break }  // nil at the end of the file
                guard (try? output.write(contentsOf: chunk)) != nil else { return }
                copied += Int64(chunk.count)
                progress?(copied)
            }
            ok = true
        }
        return ok
    }

    /// A folder deleted so that iCloud deletes it on every device.
    static func delete(_ url: URL) -> Bool {
        var ok = false
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: url, options: .forDeleting, error: &error) { u in
            ok = (try? FileManager.default.removeItem(at: u)) != nil
        }
        return ok
    }

    /// The folder the file goes in is made if missing, but not the folders above it: a write into a book
    /// folder deleted meanwhile fails instead of making it again.
    static func write(_ data: Data, to url: URL) -> Bool {
        var ok = false
        var error: NSError?
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: false)
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: url, options: .forReplacing, error: &error) {
            u in
            ok = (try? data.write(to: u, options: .atomic)) != nil
        }
        return ok
    }

    static func list(_ dir: URL) -> [URL] {
        var out: [URL] = []
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: dir, options: [], error: &error) { u in
            out = (try? FileManager.default.contentsOfDirectory(at: u, includingPropertiesForKeys: nil)) ?? []
        }
        return out
    }
}
