// The library on the phone. The books live in a folder the Mac shares through iCloud Drive (or, with
// no folder chosen, in the app's own folder in Files). iCloud is only the way in: a book is read and
// played from a copy the app keeps for itself, so iOS never takes it away mid-chapter.

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
    /// What each local copy weighs, by its own manifest: the library's «На iPhone» line.
    @Published private(set) var localBytes: [String: Int64] = [:]
    @Published var message = ""
    /// A folder was chosen but cannot be reached now: nothing read here is saved.
    @Published private(set) var folderLost = false
    /// Books of the shared library, as the last refresh found them: «Удалить → Отовсюду» is for these.
    @Published var inLibrary: Set<String> = []

    private let fm = FileManager.default
    private let defaults: UserDefaults
    private var scoped: URL?
    private var syncing = false
    private var launched = false  // the first refresh has run: `-fetchAll YES` acts once, after it
    private var jobs: [String: CopyJob] = [:]
    private var tasks: [String: Task<Void, Never>] = [:]  // a fetch still ending: the next one of the book waits for it
    private var watch: AnyCancellable?
    private let bookmarkKey = "libraryBookmark"
    private let folderLostMessage = "Нет доступа к папке библиотеки — прогресс не сохраняется"
    /// The library's «Убирать прочитанные» and «Синхронизация». Not in AppSettings: the reader's save
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
        return folderLost ? nil : fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    /// A folder of the reader's choosing, not the app's own one in Files.
    var folderChosen: Bool { scoped != nil || folderLost }

    /// The folder that holds the book folders: the chosen one, or its `books` subfolder.
    var booksRoot: URL? {
        guard let sharedRoot else { return nil }
        let inner = sharedRoot.appendingPathComponent("books", isDirectory: true)
        return fm.fileExists(atPath: inner.path) ? inner : sharedRoot
    }

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
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
            if stale, let fresh = try? url.bookmarkData() { defaults.set(fresh, forKey: bookmarkKey) }
        } else {
            print("readsync: the library folder's bookmark did not resolve")  // seen with `devicectl … --console`
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
        if let data = try? url.bookmarkData() { defaults.set(data, forKey: bookmarkKey) }
        Task { await refresh() }
    }

    // ---- what is on the shelf ----

    func refresh() async {
        if folderLost { restoreFolder() }  // the folder may be back (iCloud signed in again, say)
        if !folderLost { message = "" }  // a failure told before is told again by its row
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
        let measured = await Task.detached { () -> [String: Progress] in
            var out: [String: Progress] = [:]
            for book in all {
                guard let dir = dirs[book.slug] else { continue }
                Self.cacheCover(book.slug, from: dir)
                out[book.slug] = Self.measure(book, ReadingState.load(shared: dir, edition: book.edition))
            }
            return out
        }.value
        Player.forgetMissingCovers()
        progress = measured
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

    /// Read books finished and last opened a week ago or more, whose copies go when «Убирать прочитанные» is
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

    /// The book to come back to: the last one opened that has a copy on this phone and is being read.
    var current: Book? {
        books.filter { copy(of: $0.slug).isReadable && Self.isCurrent(progress[$0.slug]) }
            .max { (progress[$0.slug]?.opened ?? 0) < (progress[$1.slug]?.opened ?? 0) }
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
        let done = { (b: Book) in progress[b.slug]?.status == .done }
        let read = books.filter(done).enumerated().sorted { a, b in
            let (x, y) = (progress[a.element.slug]?.finishedOn ?? "", progress[b.element.slug]?.finishedOn ?? "")
            return x != y ? x > y : a.offset < b.offset
        }.map(\.element)
        return (books.filter { !done($0) && $0.slug != current }, read)
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
        let textOnly = book.audioName != nil && (textOnly ?? (Self.localCopy(book.slug).map { $0.audioName == nil } ?? false))
        if Player.shared.slug == book.slug { Player.shared.stop() }  // its files are about to be swapped under it
        sweepStaging()
        copies[book.slug] = .fetching(0)
        let source = root.appendingPathComponent(book.slug, isDirectory: true)
        let job = CopyJob(), before = tasks[book.slug]
        jobs[book.slug] = job
        let task = Task.detached {
            await before?.value  // a cancelled fetch of the same book still clearing its staging folder
            let result = job.cancelled
                ? .failure(Failure(message: "", cancelled: true))
                : Self.copyBook(book.slug, from: source, textOnly: textOnly, job: job) { done in
                    Task { @MainActor in
                        if Shelf.shared.jobs[book.slug] === job { Shelf.shared.copies[book.slug] = .fetching(done) }
                    }
                }
            await MainActor.run {
                let shelf = Shelf.shared
                if shelf.jobs[book.slug] === job { shelf.jobs[book.slug] = nil }
                guard !job.cancelled, shelf.isFetching(book.slug) else { return }  // cancelled or removed meanwhile
                switch result {
                case .success:
                    let mine = Self.localCopy(book.slug)
                    shelf.copies[book.slug] = mine.map { Self.copyState(local: $0, shared: book) } ?? .here
                    shelf.localBytes[book.slug] = mine?.bytes
                    Player.forgetCover(book.slug)
                    if !shelf.folderLost { shelf.message = "" }
                case .failure(let why):
                    print("readsync: \(book.slug) not copied: \(why.message)")
                    shelf.copies[book.slug] = Self.localCopy(book.slug) == nil ? .failed(why.message) : .outdated
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
        copies[slug] = books.first { $0.slug == slug }.map { Self.copyState(local: Self.localCopy(slug), shared: $0) } ?? .absent
    }

    /// The books «Синхронизация» copies now: those not on the phone, failed, or older than the library's,
    /// but none the reader took off it, none open in the reader or held by the narrator, and none `done`
    /// (the read ones, while «Убирать прочитанные» is on).
    nonisolated static func toSync(
        _ books: [Book], copies: [String: Copy], skip: Set<String>, busy: Set<String>, done: Set<String> = []
    ) -> [Book] {
        books.filter { book in
            guard !skip.contains(book.slug), !done.contains(book.slug) else { return false }
            switch copies[book.slug] ?? .absent {
            case .absent, .failed: return true
            case .outdated: return !busy.contains(book.slug)
            default: return false
            }
        }
    }

    /// «Синхронизация»: every book that wants copying, one after another: all at once, iCloud would download
    /// every book's audio side by side and none would be readable for a long while. `all`: the launch
    /// argument's one go, toggle or not, taken-off books too.
    func sync(all: Bool = false) async {
        guard !syncing, all || defaults.bool(forKey: Self.syncKey), booksRoot != nil else { return }
        syncing = true
        defer { syncing = false }
        var tried = Set<String>()  // a failing book is tried once a round, not over and over
        // iCloud is asked for every file still to come, all at once: it downloads them on its own, also while
        // this app is suspended under a locked screen, and the copies that follow take seconds, not minutes
        if let root = booksRoot {
            let busy = Set([Player.shared.slug, Player.shared.pageSlug].compactMap { $0 })
            for book in Self.toSync(books, copies: copies, skip: all ? [] : skip, busy: busy, done: all ? [] : readLeft) {
                let textOnly = Self.localCopy(book.slug).map { $0.audioName == nil } ?? false
                for part in Self.parts(of: book, textOnly: textOnly) {
                    let url = root.appendingPathComponent(book.slug, isDirectory: true).appendingPathComponent(part.name)
                    try? fm.startDownloadingUbiquitousItem(at: url)
                }
            }
        }
        while all || defaults.bool(forKey: Self.syncKey) {
            let busy = Set([Player.shared.slug, Player.shared.pageSlug].compactMap { $0 })
            guard let book = Self.toSync(books, copies: copies, skip: all ? [] : skip, busy: busy, done: all ? [] : readLeft)
                .first(where: { !tried.contains($0.slug) })
            else { return }
            tried.insert(book.slug)
            await fetch(book, byHand: false)?.value
        }
    }

    /// The read books «Синхронизация» leaves while «Убирать прочитанные» is on: read again, they come back.
    private var readLeft: Set<String> {
        guard defaults.bool(forKey: Self.dropReadKey) else { return [] }
        return Set(progress.filter { $0.value.status == .done }.map(\.key))
    }

    /// Staging folders (`.<slug>.new`) a fetch left when the app was killed in the middle of it: hundreds of
    /// megabytes each. The one a fetch is filling now stays.
    private func sweepStaging() {
        for name in (try? fm.contentsOfDirectory(atPath: Self.localRoot.path)) ?? []
        where name.hasPrefix(".") && name.hasSuffix(".new") && !isFetching(String(name.dropFirst().dropLast(4))) {
            try? fm.removeItem(at: Self.localRoot.appendingPathComponent(name, isDirectory: true))
        }
    }

    struct Failure: Error {
        let message: String
        var cancelled = false
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

    /// Copy the files the reader needs, check them against the manifest, then swap the copy in whole.
    /// `textOnly`: everything but the audio.
    nonisolated static func copyBook(
        _ slug: String, from source: URL, textOnly: Bool = false, job: CopyJob? = nil,
        progress: @escaping (Double) -> Void
    ) -> Result<Void, Failure> {
        let fm = FileManager.default
        let stopped = Failure(message: "", cancelled: true)
        // the manifest as it is now, read once: the files are checked against it and it is the one kept
        guard let toml = Coordinated.read(source.appendingPathComponent("book.toml")),
            let text = String(data: toml, encoding: .utf8)
        else { return .failure(Failure(message: "нет book.toml")) }
        let book = Book(slug: slug, toml: text)
        let staging = localRoot.appendingPathComponent(".\(slug).new", isDirectory: true)
        try? fm.removeItem(at: staging)
        var whole = false
        defer { if !whole { try? fm.removeItem(at: staging) } }  // half a copy is hundreds of megabytes
        do {
            try fm.createDirectory(at: staging, withIntermediateDirectories: true)
            let wanted = parts(of: book, textOnly: textOnly)
            let total = Double(max(wanted.reduce(0) { $0 + $1.size }, 1))
            var done: Int64 = 0
            for (name, size) in wanted {
                // a file of the book's folder, never a path out of it
                guard !name.contains("/"), !name.contains(".."), !name.hasPrefix(".") else {
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
                    return .failure(Failure(message: "файлы ещё в iCloud"))
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
                        print("readsync: \(slug): images/\(name) did not come from iCloud")
                        return .failure(Failure(message: "файлы ещё в iCloud"))
                    }
                    try data.write(to: dest.appendingPathComponent(name))
                }
            }
            // the manifest last, as on the Mac: a copy with it is a whole copy
            let kept = textOnly ? Data(manifest(text, files: wanted).utf8) : toml
            try kept.write(to: staging.appendingPathComponent("book.toml"))
            if job?.cancelled == true { return .failure(stopped) }
            let dest = localDir(slug)
            whole = true
            if fm.fileExists(atPath: dest.path) {
                _ = try fm.replaceItemAt(dest, withItemAt: staging)  // one step: the old copy stays until the new one is in
            } else {
                try fm.moveItem(at: staging, to: dest)
            }
            // iCloud's own download of the audio goes back to the cloud: the phone keeps it once, in the copy
            if let audio = book.audioName { try? fm.evictUbiquitousItem(at: source.appendingPathComponent(audio)) }
            return .success(())
        } catch {
            whole = false
            return .failure(Failure(message: error.localizedDescription))
        }
    }

    // ---- taking books away ----

    /// «Удалить → Только с iPhone» (and «Убирать прочитанные»): the copy goes, the book stays in the library.
    /// `remember`: «Синхронизация» leaves it until it is downloaded by hand; without, until it is read again.
    func removeHere(_ slug: String, remember: Bool = true) {
        if Player.shared.slug == slug { Player.shared.stop() }  // the narrator must not play a file that is gone
        if let job = jobs.removeValue(forKey: slug) { job.cancel() }
        try? fm.removeItem(at: Self.localDir(slug))
        copies[slug] = .absent
        localBytes[slug] = nil
        if inLibrary.contains(slug) {
            if remember { skip.insert(slug) }
        } else {
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
            let merged = JSONBox(ReadingState.put(shared: dir, edition: book.edition, patch: patch(st)))
            await MainActor.run { Player.shared.stateChanged(book.slug, merged.value as? [String: Any] ?? [:]) }
            await self.remeasure(book)
        }
    }

    /// One book's progress read again, after a write.
    func remeasure(_ book: Book) async {
        guard let dir = sharedDir(book.slug) else { return }
        let p = await Task.detached { Self.measure(book, ReadingState.load(shared: dir, edition: book.edition)) }.value
        progress[book.slug] = p
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
                if !ok { Shelf.shared.message = "«\(book.title)» не удалилась из библиотеки" }
                Task { await Shelf.shared.refresh() }
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

/// One download, to be called off: a wait for iCloud is given up, a copy stops at its next piece.
final class CopyJob: @unchecked Sendable {
    private let lock = NSLock()
    private var stopped = false
    private var coordinator: NSFileCoordinator?

    var cancelled: Bool { lock.withLock { stopped } }

    func cancel() {
        let c: NSFileCoordinator? = lock.withLock {
            stopped = true
            return coordinator
        }
        c?.cancel()
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
    static func read(_ url: URL) -> Data? {
        var out: Data?
        var error: NSError?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: url, options: [], error: &error) { u in
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
