// A book file made into a book folder on the phone, as pipeline/add_book.py makes one from a text source:
// the same book.json, images/ and book.toml, so a book added here reads like any other and the Mac can
// later give it audio. Everything is built in a staging folder and moved in whole; book.toml comes last.

import Foundation

enum BookImport {
    struct Result {
        let slug: String
        let title: String
        let author: String
    }

    struct Failure: Error, LocalizedError, Equatable {
        let message: String
        var errorDescription: String? { message }
    }

    static let corrupt = "Файл повреждён"
    static let unsupported = "Этот формат не открыть. Подходят fb2, epub, pdf, txt"
    static let minWords = 500  // below this it is a notice, not a book (add_book.MIN_WORDS)

    /// Turns a book file (fb2, fb2.zip, epub, txt, pdf) into a book folder `<into>/<slug>/` with book.json,
    /// images/ (cover first if any) and book.toml (slug, title, author, language, id, edition, files) as
    /// pipeline/manifest.py writes it. Throws BookImport.Failure(message:) with a short Russian message.
    static func make(from file: URL, into library: URL) throws -> Result {
        let data: Data
        do { data = try Data(contentsOf: file, options: .mappedIfSafe) } catch { throw Failure(message: "Не удалось прочитать файл") }
        let name = file.lastPathComponent
        var book = try read(data, name: name)
        let fallback = title(fromFileName: name)
        if Py.strip(book.title).isEmpty { book.title = fallback }

        let fm = FileManager.default
        let staging = stagingRoot.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? fm.removeItem(at: staging) }
        sweepStaging()
        let folder = staging.appendingPathComponent("book", isDirectory: true)
        let json = book.json()
        do {
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
            try json.write(to: folder.appendingPathComponent("book.json"))
            if !book.images.isEmpty {
                let images = folder.appendingPathComponent("images", isDirectory: true)
                try fm.createDirectory(at: images, withIntermediateDirectories: true)
                for (imageName, bytes) in book.images where safeName(imageName) {
                    try bytes.write(to: images.appendingPathComponent(imageName))
                }
            }
        } catch {
            throw failure(error)
        }

        let id = hex(), edition = hex()
        let title = cleanTitle(book.title), author = cleanTitle(book.author)
        func toml(_ slug: String) -> String {
            var meta: [(String, String)] = [("slug", slug), ("title", title), ("author", author)]
            meta += [("language", language(book.language)), ("text_source", Py.collapse(name))]
            meta.append(("fragment_note", fragmentNote(book)))
            meta += [("id", id), ("edition", edition), ("files", "book.json:\(json.count)")]
            return meta.filter { !$0.1.isEmpty }.map { "\($0.0) = \"\(escape($0.1))\"\n" }.joined()
                + "text_end = \(TextEnd.of(book))\n"
        }
        let slug = try land(folder, into: library, base: slugify(title), id: id, toml: toml)
        return Result(slug: slug, title: title, author: author)
    }

    // MARK: reading

    /// The book a file holds, checked to be a book: unwrap, sniff, extract, check_real_book.
    static func read(_ data: Data, name: String) throws -> ImportedBook {
        let (inner, hint) = try unwrap(data, hint: name)
        let stem = title(fromFileName: hint)
        let book: ImportedBook
        switch try sniff(inner, hint: hint) {
        case .fb2: book = try FB2.extract(inner, stem: stem)
        case .fb2zip: book = try FB2.extract(try fb2(inZip: inner), stem: stem)
        case .epub: book = try EPUB.extract(inner, stem: stem)
        case .pdf: book = try PDF.extract(inner, stem: stem)
        case .txt: book = TXT.extract(inner, stem: stem)
        }
        try checkRealBook(book)
        return book
    }

    enum Kind { case fb2, fb2zip, epub, pdf, txt }

    private static let foreign = [".doc", ".docx", ".rtf", ".odt", ".mobi", ".azw", ".azw3", ".djvu", ".djv", ".cbz", ".cbr"]

    /// add_book.unwrap: a single file packed in a zip (twice at most) is taken out to be sniffed; an epub
    /// and an fb2.zip stay packed. An .fbd beside a pdf is the catalog's description of it.
    static func unwrap(_ data: Data, hint: String) throws -> (Data, String) {
        var data = data, hint = hint
        for _ in 0..<2 {
            guard Zip.isZip(data), let zip = try? Zip(data) else { break }
            let files = zip.entries.filter { !$0.isDirectory && !$0.name.lowercased().hasSuffix(".fbd") }
            if zip.entry("META-INF/container.xml") != nil || files.count != 1 || files[0].name.lowercased().hasSuffix(".fb2") {
                break
            }
            do { data = try zip.read(files[0]) } catch { throw failure(error) }
            hint = files[0].name
        }
        return (data, hint)
    }

    /// add_book.sniff, by content first and by name second. html is the pipeline's, not the phone's.
    static func sniff(_ data: Data, hint: String) throws -> Kind {
        let lower = hint.lowercased()
        if foreign.contains(where: { lower.hasSuffix($0) }) { throw Failure(message: unsupported) }
        let head = Data(data.prefix(4096).drop { [0x20, 0x09, 0x0A, 0x0D, 0x0B, 0x0C].contains($0) })
        if head.starts(with: Array("%PDF-".utf8)) || lower.hasSuffix(".pdf") { return .pdf }
        if Zip.isZip(data) {
            let names = (try? Zip(data))?.names ?? []
            if names.contains("META-INF/container.xml") || lower.hasSuffix(".epub") { return .epub }
            if !names.isEmpty, !names.contains(where: { $0.lowercased().hasSuffix(".fb2") }) {
                throw Failure(message: "В архиве нет книги")
            }
            return .fb2zip
        }
        if head.range(of: Data("<FictionBook".utf8)) != nil || lower.hasSuffix(".fb2") { return .fb2 }
        let text = String(decoding: head, as: UTF8.self)
        if Regex("<(html|!doctype html|body|p)\\b", ignoreCase: true).search(text) != nil || lower.hasSuffix(".html")
            || lower.hasSuffix(".htm")
        {
            throw Failure(message: unsupported)
        }
        // a binary file of some other kind has zero bytes; a text in UTF-16 or UTF-32 has them too
        let start = data.prefix(4096)
        let marked = ([[0xFF, 0xFE], [0xFE, 0xFF], [0, 0, 0xFE, 0xFF]] as [[UInt8]]).contains { start.starts(with: $0) }
        if start.contains(0), !marked, TXT.bomlessUTF16([UInt8](start)[...]) == nil { throw Failure(message: unsupported) }
        return .txt
    }

    private static func fb2(inZip data: Data) throws -> Data {
        do {
            let zip = try Zip(data)
            guard let e = zip.entries.first(where: { $0.name.lowercased().hasSuffix(".fb2") }) else {
                throw Failure(message: "В архиве нет книги")
            }
            return try zip.read(e)
        } catch let f as Failure {
            throw f
        } catch {
            throw failure(error)
        }
    }

    private static let stub = Regex(
        "книга (заблокирована|удалена|не найдена)|удалена по требованию|доступ к книге ограничен"
            + "|страница не найдена|book (is )?blocked|not found", ignoreCase: true)

    /// add_book.check_real_book: a catalog's stub page or a few lines are not a book.
    static func checkRealBook(_ book: ImportedBook) throws {
        let words = book.words
        let head = book.blocks.prefix(8).map(\.text).joined(separator: " ")
        if stub.search(head) != nil, words < 3000 {
            let first = String(Py.strip(book.blocks.first?.text ?? "").prefix(80))
            throw Failure(message: "Вместо книги заглушка: «\(first)»")
        }
        if words < minWords { throw Failure(message: "В файле меньше 500 слов — это не книга") }
    }

    private static let fragment = Regex(
        "конец ознакомительного фрагмента|ознакомительн[\\p{L}\\p{N}_]+ фрагмент[\\p{L}\\p{N}_]*|купить полную версию",
        ignoreCase: true)  // Python's \w: letters, numbers and _, not ICU's (which has combining marks too)

    /// add_book.fragment_note: the literal phrase, if the text ends the way sample editions do.
    static func fragmentNote(_ book: ImportedBook) -> String {
        let tail = book.blocks.suffix(8).map(\.text).joined(separator: " ")
        return fragment.search(tail)?[0] ?? ""
    }

    // MARK: naming

    private static let translit: [Unicode.Scalar: String] = {
        let to = [
            "a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
            "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya",
        ]
        return Dictionary(uniqueKeysWithValues: zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя".unicodeScalars, to))
    }()

    /// library.slugify step for step: NFC, lower case, each code point transliterated, runs of anything else a
    /// dash, dashes trimmed, then cut to 48 (a dash the cut leaves at the end stays, as in Python).
    static func slugify(_ title: String) -> String {
        let s = title.precomposedStringWithCanonicalMapping.lowercased().unicodeScalars.map { translit[$0] ?? String($0) }
        let dashed = Regex("[^a-z0-9]+").replace(s.joined(), with: "-").trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let cut = String(dashed.prefix(48))
        return cut.isEmpty ? "book" : cut
    }

    /// manifest.clean_title: control characters and runs of spaces become one space.
    static func cleanTitle(_ s: String) -> String {
        Py.collapse(String(String.UnicodeScalarView(s.unicodeScalars.map { $0.value < 0x20 || $0.value == 0x7F ? " " : $0 })))
    }

    /// The name before the extension(s), `_` read as a space: what a book without a title is called.
    static func title(fromFileName name: String) -> String {
        var s = (name as NSString).lastPathComponent
        for ext in [".zip", ".fb2", ".epub", ".pdf", ".txt"] where s.lowercased().hasSuffix(ext) {
            s = String(s.dropLast(ext.count))
        }
        let t = Py.collapse(s.replacingOccurrences(of: "_", with: " "))
        return t.isEmpty ? "Без названия" : t
    }

    private static func language(_ raw: String) -> String {
        let primary = Py.strip(raw).lowercased().split(whereSeparator: { $0 == "-" || $0 == "_" }).first.map(String.init) ?? ""
        return Regex("^[a-z]{2,3}$").matches(primary) ? primary : "ru"
    }

    /// manifest.toml_str's escaping: backslashes, quotes and control characters (as \\uXXXX).
    static func escape(_ s: String) -> String {
        var out = ""
        for c in s.unicodeScalars {
            switch c {
            case "\\": out += "\\\\"
            case "\"": out += "\\\""
            case _ where c.value < 0x20 || c.value == 0x7F: out += String(format: "\\u%04x", c.value)
            default: out.unicodeScalars.append(c)
            }
        }
        return out
    }

    private static func hex() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }

    private static func safeName(_ name: String) -> Bool {
        !name.isEmpty && !name.contains("/") && !name.contains("\0") && name != "." && name != ".."
    }

    // MARK: landing

    /// Where books are built: not in Documents, which may be the library itself or synced whole.
    static var stagingRoot: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("import", isDirectory: true)
    }

    /// What an import killed midway left behind; a running one is younger than an hour.
    static func sweepStaging() {
        let fm = FileManager.default
        let old = Date().addingTimeInterval(-3600)
        let items = (try? fm.contentsOfDirectory(at: stagingRoot, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        for url in items {
            let date = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            if date < old { try? fm.removeItem(at: url) }
        }
    }

    /// The folder moved in under a free name (the title's slug plus 4 hex of the id, so a name made here never
    /// meets one the Mac made), then book.toml written into it: once it lands, the book is whole.
    private static func land(_ folder: URL, into library: URL, base: String, id: String, toml: (String) -> String) throws
        -> String
    {
        let fm = FileManager.default
        do { try fm.createDirectory(at: library, withIntermediateDirectories: true) } catch {
            throw Failure(message: "Нет доступа к папке библиотеки")
        }
        let suffixes = stride(from: 0, to: 28, by: 4).map { i -> String in
            let a = id.index(id.startIndex, offsetBy: i)
            return String(id[a..<id.index(a, offsetBy: 4)])
        }
        for suffix in suffixes {
            let slug = "\(base)-\(suffix)"
            let dest = library.appendingPathComponent(slug, isDirectory: true)
            if fm.fileExists(atPath: dest.path) { continue }
            var moved = false
            var failed: Error?
            var coordinationError: NSError?
            NSFileCoordinator().coordinate(
                writingItemAt: folder, options: .forMoving, writingItemAt: dest, options: .forReplacing,
                error: &coordinationError
            ) { from, to in
                do {
                    if fm.fileExists(atPath: to.path) { return }  // taken meanwhile: the next name
                    try fm.moveItem(at: from, to: to)
                    moved = true
                } catch { failed = error }
            }
            if let e = failed ?? coordinationError { throw failure(e) }
            guard moved else { continue }
            let data = Data(toml(slug).utf8)
            var written: Error?
            NSFileCoordinator().coordinate(writingItemAt: dest.appendingPathComponent("book.toml"), options: .forReplacing, error: &coordinationError) {
                do { try data.write(to: $0, options: .atomic) } catch { written = error }
            }
            if let e = written ?? coordinationError {
                NSFileCoordinator().coordinate(writingItemAt: dest, options: .forDeleting, error: nil) { try? fm.removeItem(at: $0) }
                throw failure(e)
            }
            return slug
        }
        throw Failure(message: "Нет доступа к папке библиотеки")
    }

    /// An error from below as one of the short messages.
    static func failure(_ error: Error) -> Failure {
        if let f = error as? Failure { return f }
        if let z = error as? Zip.Problem {
            switch z {
            case .encrypted: return Failure(message: "Архив защищён паролем")
            case .zip64: return Failure(message: "Архив слишком большой (zip64)")
            case .tooBig: return Failure(message: "Файл в архиве слишком большой")
            default: return Failure(message: corrupt)
            }
        }
        let ns = error as NSError
        if (ns.domain == NSCocoaErrorDomain && ns.code == NSFileWriteOutOfSpaceError)
            || (ns.domain == NSPOSIXErrorDomain && ns.code == Int(ENOSPC))
        {
            return Failure(message: "Недостаточно места")
        }
        if ns.domain == NSCocoaErrorDomain, [NSFileWriteNoPermissionError, NSFileReadNoPermissionError].contains(ns.code) {
            return Failure(message: "Нет доступа к папке библиотеки")
        }
        return Failure(message: corrupt)
    }
}
