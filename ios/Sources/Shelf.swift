// The library on the phone. The books live in a folder the Mac shares through iCloud Drive (or, with
// no folder chosen, in the app's own folder in Files). iCloud is only the way in: a book is read and
// played from a copy the app keeps for itself, so iOS never takes it away mid-chapter.

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
}

enum Copy: Equatable {
    case absent, fetching(Double), here, outdated, failed(String)
}

@MainActor
final class Shelf: ObservableObject {
    static let shared = Shelf()

    @Published private(set) var books: [Book] = []
    @Published private(set) var copies: [String: Copy] = [:]
    @Published private(set) var progress: [String: Progress] = [:]
    @Published private(set) var folderName = ""
    @Published var message = ""

    private let fm = FileManager.default
    private var scoped: URL?
    private var folderLost = false  // a folder was chosen but cannot be reached now
    private var fetchingAll = false
    private var launched = false  // the first refresh has run: `-fetchAll YES` acts once, after it
    private let bookmarkKey = "libraryBookmark"
    private let folderLostMessage = "Нет доступа к папке библиотеки: выбери её снова"

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
        return folderLost ? nil : fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    /// The folder that holds the book folders: the chosen one, or its `books` subfolder.
    var booksRoot: URL? {
        guard let sharedRoot else { return nil }
        let inner = sharedRoot.appendingPathComponent("books", isDirectory: true)
        return fm.fileExists(atPath: inner.path) ? inner : sharedRoot
    }

    init() {
        restoreFolder()
        sweepStaging()
    }

    // ---- the folder ----

    private func restoreFolder() {
        guard let data = UserDefaults.standard.data(forKey: bookmarkKey) else {
            folderName = "Файлы → На iPhone → readsync"
            return
        }
        var stale = false
        if let url = try? URL(resolvingBookmarkData: data, options: [], relativeTo: nil, bookmarkDataIsStale: &stale),
            url.startAccessingSecurityScopedResource()
        {
            scoped = url
            folderLost = false
            if message == folderLostMessage { message = "" }
            folderName = url.lastPathComponent
            if stale, let fresh = try? url.bookmarkData() { UserDefaults.standard.set(fresh, forKey: bookmarkKey) }
        } else {
            folderLost = true
            message = folderLostMessage
            folderName = "папка недоступна"
        }
    }

    func choose(folder url: URL) {
        guard url.startAccessingSecurityScopedResource() else {
            message = "iOS не дала доступ к этой папке"
            return
        }
        scoped?.stopAccessingSecurityScopedResource()
        scoped = url
        folderLost = false
        message = ""
        folderName = url.lastPathComponent
        if let data = try? url.bookmarkData() { UserDefaults.standard.set(data, forKey: bookmarkKey) }
        Task { await refresh() }
    }

    // ---- what is on the shelf ----

    func refresh() async {
        if folderLost { restoreFolder() }  // the folder may be back (iCloud signed in again, say)
        let root = booksRoot
        let found = await Task.detached { () -> [Book] in
            guard let root else { return [] }
            let fm = FileManager.default
            let dirs = (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
            var out: [Book] = []
            for dir in dirs where !dir.lastPathComponent.hasPrefix(".") {
                let toml = dir.appendingPathComponent("book.toml")
                guard let text = Coordinated.read(toml).flatMap({ String(data: $0, encoding: .utf8) }) else { continue }
                let book = Book(slug: dir.lastPathComponent, toml: text)
                if !book.files.isEmpty { out.append(book) }  // no manifest yet: still being made on the Mac
            }
            return out.sorted { $0.title.localizedCompare($1.title) == .orderedAscending }
        }.value
        // a copy whose book left the Mac's library (or a folder that cannot be reached right now) stays
        // readable until the reader removes it
        let shared = Set(found.map(\.slug))
        let localOnly = ((try? fm.contentsOfDirectory(atPath: Self.localRoot.path)) ?? [])
            .filter { !$0.hasPrefix(".") && !shared.contains($0) }
            .compactMap(Self.localCopy)
        books = (found + localOnly).sorted { $0.title.localizedCompare($1.title) == .orderedAscending }
        for book in found { copies[book.slug] = localCopy(of: book) }
        for book in localOnly where !isFetching(book.slug) { copies[book.slug] = .here }
        let all = books, dirs = Dictionary(uniqueKeysWithValues: all.compactMap { b in sharedDir(b.slug).map { (b.slug, $0) } })
        let measured = await Task.detached { () -> [String: Progress] in
            var out: [String: Progress] = [:]
            for book in all {
                guard let dir = dirs[book.slug] else { continue }
                Self.cacheCover(book.slug, from: dir)
                let st = ReadingState.load(shared: dir, edition: book.edition)
                var p = Progress(opened: ReadingState.num(st["opened"]))
                let duration = Self.duration(book.slug)
                if book.hasAudio, duration > 0, st["mode"] as? String != "pages" {
                    p.fraction = min(1, ReadingState.num(st["pos"]) / duration)
                } else {
                    p.fraction = min(1, ReadingState.num(st["sentPct"]) / 100)
                }
                out[book.slug] = p
            }
            return out
        }.value
        Player.forgetMissingCovers()
        progress = measured
        if !launched {
            launched = true
            // launched with `-fetchAll YES`: every book onto the phone, as the menu's «Скачать все книги»
            if UserDefaults.standard.bool(forKey: "fetchAll"), booksRoot != nil { Task { await fetchAll() } }
        }
    }

    private func isFetching(_ slug: String) -> Bool {
        if case .fetching = copies[slug] { return true }
        return false
    }

    private func localCopy(of book: Book) -> Copy {
        if isFetching(book.slug) { return copies[book.slug]! }
        guard let mine = Self.localCopy(book.slug) else { return .absent }
        // the same edition with other sizes is new timing from the Mac's precise alignment
        return mine.edition == book.edition && mine.files == book.files ? .here : .outdated
    }

    func copy(of slug: String) -> Copy { copies[slug] ?? .absent }

    /// Covers of books not on the phone yet: a small copy each, so the list is not a row of letters.
    nonisolated static var coverRoot: URL {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("covers")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    nonisolated static func cachedCover(_ slug: String) -> URL? {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: coverRoot.path)) ?? []
        // exactly <slug>.<ext>: the cover of "a.b" is not the cover of "a"
        return names.first { name in
            guard name.hasPrefix(slug + ".") else { return false }
            let ext = name.dropFirst(slug.count + 1)
            return !ext.isEmpty && !ext.contains(".")
        }.map { coverRoot.appendingPathComponent($0) }
    }

    nonisolated static func cacheCover(_ slug: String, from shared: URL) {
        let local = localDir(slug).appendingPathComponent("images")
        if cachedCover(slug) != nil
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

    /// The book to come back to: the last one opened that has a copy on this phone.
    var current: Book? {
        books.filter { copy(of: $0.slug) == .here || copy(of: $0.slug) == .outdated }
            .filter { (progress[$0.slug]?.opened ?? 0) > 0 }
            .max { (progress[$0.slug]?.opened ?? 0) < (progress[$1.slug]?.opened ?? 0) }
    }

    /// The copy the reader opens: the local one, whatever the shared library says now.
    func localBook(_ slug: String) -> Book? { Self.localCopy(slug) }

    // ---- getting a copy ----

    /// The task ends once the copy is in or has failed.
    @discardableResult
    func fetch(_ book: Book) -> Task<Void, Never>? {
        if case .fetching = copy(of: book.slug) { return nil }
        guard let root = booksRoot else {
            message = folderLostMessage
            return nil
        }
        if Player.shared.slug == book.slug { Player.shared.stop() }  // its files are about to be swapped under it
        sweepStaging()
        copies[book.slug] = .fetching(0)
        let source = root.appendingPathComponent(book.slug, isDirectory: true)
        return Task.detached {
            let result = Self.copyBook(book.slug, from: source) { done in
                Task { @MainActor in
                    if Shelf.shared.isFetching(book.slug) { Shelf.shared.copies[book.slug] = .fetching(done) }
                }
            }
            await MainActor.run {
                let shelf = Shelf.shared
                guard shelf.isFetching(book.slug) else { return }  // removed while it was coming in
                switch result {
                case .success:
                    shelf.copies[book.slug] = .here
                    Player.forgetCover(book.slug)
                case .failure(let why):
                    shelf.copies[book.slug] = Self.localCopy(book.slug) == nil ? .failed(why.message) : .outdated
                    shelf.message = why.message
                }
            }
        }
    }

    /// Every book not on the phone, or not as the Mac has it now, one after another: all at once, iCloud
    /// would download every book's audio side by side and none would be readable for a long while.
    func fetchAll() async {
        guard !fetchingAll else { return }
        fetchingAll = true
        defer { fetchingAll = false }
        for book in books {
            switch copy(of: book.slug) {
            case .absent, .failed: break
            case .outdated where book.slug != Player.shared.slug: break  // the one loaded waits for its own swipe
            default: continue
            }
            await fetch(book)?.value
        }
    }

    /// Staging folders (`.<slug>.new`) a fetch left when the app was killed in the middle of it: hundreds of
    /// megabytes each. The one a fetch is filling now stays.
    private func sweepStaging() {
        for name in (try? fm.contentsOfDirectory(atPath: Self.localRoot.path)) ?? []
        where name.hasPrefix(".") && name.hasSuffix(".new") && !isFetching(String(name.dropFirst().dropLast(4))) {
            try? fm.removeItem(at: Self.localRoot.appendingPathComponent(name, isDirectory: true))
        }
    }

    struct Failure: Error { let message: String }

    /// Copy the files the reader needs, check them against the manifest, then swap the copy in whole.
    nonisolated static func copyBook(_ slug: String, from source: URL, progress: @escaping (Double) -> Void)
        -> Result<Void, Failure>
    {
        let fm = FileManager.default
        // the manifest as it is now, read once: the files are checked against it and it is the one kept
        guard let toml = Coordinated.read(source.appendingPathComponent("book.toml")),
            let text = String(data: toml, encoding: .utf8)
        else { return .failure(Failure(message: "book.toml не читается")) }
        let book = Book(slug: slug, toml: text)
        let staging = localRoot.appendingPathComponent(".\(slug).new", isDirectory: true)
        try? fm.removeItem(at: staging)
        do {
            try fm.createDirectory(at: staging, withIntermediateDirectories: true)
            let total = Double(max(book.bytes, 1))
            var done: Int64 = 0
            for (name, size) in book.files.sorted(by: { $0.value < $1.value }) {
                // a file of the book's folder, never a path out of it
                guard !name.contains("/"), !name.contains(".."), !name.hasPrefix(".") else {
                    return .failure(Failure(message: "в book.toml чужое имя файла: \(name)"))
                }
                // copied as a file, not read into memory: the audio alone is hundreds of megabytes
                let dest = staging.appendingPathComponent(name)
                guard Coordinated.copy(source.appendingPathComponent(name), to: dest) else {
                    return .failure(Failure(message: "\(name) ещё не пришёл из iCloud"))
                }
                let got = ((try? fm.attributesOfItem(atPath: dest.path))?[.size] as? NSNumber)?.int64Value ?? -1
                if got != size {
                    return .failure(Failure(message: "книга ещё синхронизируется, попробуй позже"))
                }
                done += size
                progress(Double(done) / total)
            }
            let images = source.appendingPathComponent("images", isDirectory: true)
            // an image not downloaded yet is its `.name.icloud` placeholder: read by its real name, it is fetched
            let names = Set(Coordinated.list(images).map { ReadingState.realName($0).lastPathComponent })
                .filter { !$0.hasPrefix(".") }
            if !names.isEmpty {
                let dest = staging.appendingPathComponent("images", isDirectory: true)
                try fm.createDirectory(at: dest, withIntermediateDirectories: true)
                for name in names.sorted() {
                    // a copy short of an image is not a whole copy
                    guard let data = Coordinated.read(images.appendingPathComponent(name)) else {
                        return .failure(Failure(message: "\(name) ещё не пришёл из iCloud"))
                    }
                    try data.write(to: dest.appendingPathComponent(name))
                }
            }
            // the manifest last, as on the Mac: a copy with it is a whole copy
            try toml.write(to: staging.appendingPathComponent("book.toml"))
            let dest = localDir(slug)
            if fm.fileExists(atPath: dest.path) {
                _ = try fm.replaceItemAt(dest, withItemAt: staging)  // one step: the old copy stays until the new one is in
            } else {
                try fm.moveItem(at: staging, to: dest)
            }
            return .success(())
        } catch {
            try? fm.removeItem(at: staging)
            return .failure(Failure(message: error.localizedDescription))
        }
    }

    func remove(_ slug: String) {
        if Player.shared.slug == slug { Player.shared.stop() }  // the narrator must not play a file that is gone
        try? fm.removeItem(at: Self.localDir(slug))
        copies[slug] = Copy.absent
    }

    /// The shared folder of one book: where its reading state lives.
    /// None while the chosen folder cannot be reached: the state is then neither read nor written.
    func sharedDir(_ slug: String) -> URL? { booksRoot?.appendingPathComponent(slug, isDirectory: true) }
}

/// Reads and writes that iCloud's file provider sees: a file not downloaded yet is fetched first, and a
/// write is announced so it gets uploaded.
enum Coordinated {
    static func read(_ url: URL) -> Data? {
        var out: Data?
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: url, options: [], error: &error) { u in
            out = try? Data(contentsOf: u)
        }
        return out
    }

    static func copy(_ url: URL, to dest: URL) -> Bool {
        var ok = false
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: url, options: [], error: &error) { u in
            ok = (try? FileManager.default.copyItem(at: u, to: dest)) != nil
        }
        return ok
    }

    static func write(_ data: Data, to url: URL) -> Bool {
        var ok = false
        var error: NSError?
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
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
