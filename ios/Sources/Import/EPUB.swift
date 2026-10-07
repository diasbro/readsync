// pipeline/extract_epub.py on the phone, rule for rule: chapters from the table of contents (nav, else NCX) or
// h1–h4, every text-bearing block (text outside paragraphs, list items with their numbers, quotes, tables, verse,
// epigraphs), notes taken out of the reading flow, pictures in the text and alone, the cover as images/cover.<ext>.

import Foundation

enum EPUB {
    static let headings = ["h1": 1, "h2": 2, "h3": 3, "h4": 4]
    static let skipTags: Set<String> = ["script", "style", "nav", "head", "title", "template"]
    /// `extract_text.BLOCK_TAGS`: elements that hold blocks.
    static let blockTags: Set<String> = [
        "address", "article", "aside", "blockquote", "body", "caption", "center", "dd", "details", "dialog", "dir",
        "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "hgroup", "hr", "html", "li", "main", "menu", "nav", "ol", "p", "pre", "section", "summary", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    ]
    static let inlineTags: Set<String> = [
        "a", "span", "sup", "sub", "em", "strong", "i", "b", "small", "big", "u", "s", "abbr", "cite", "code", "q",
        "font", "mark", "del", "ins", "dfn", "kbd", "var", "samp", "tt", "label", "time",
    ]
    static let markBits = ["em": 1, "i": 1, "strong": 2, "b": 2, "sup": 4, "sub": 8]
    static let closers: Set<Unicode.Scalar> = Set(".,;:!?…)]}»”’".unicodeScalars)
    static let quotes: Set<Unicode.Scalar> = ["\"", "'"]  // a closer only when it closes: no letter or digit after it
    static let noteTypes: Set<String> = ["footnote", "endnote", "rearnote", "note"]
    static let noteRoles: Set<String> = ["doc-footnote", "doc-endnote", "doc-rearnote", "doc-note"]
    static let notesTypes: Set<String> = ["footnotes", "endnotes", "rearnotes"]
    static let notesRoles: Set<String> = ["doc-endnotes", "doc-footnotes"]
    static let notesClasses: Set<String> = ["notes", "footnotes", "endnotes", "rearnotes"]
    static let noteClass = Regex("^(?:(foot|end|rear|side)?note([-_]?(text|body|item|\\d+))?|fn\\d*|fnote\\d*|notetext)$")
    static let verseTypes: Set<String> = ["z3998:poem", "z3998:verse", "z3998:song", "z3998:hymn"]
    static let maxNote = 10000
    static let titleEnd: Set<Unicode.Scalar> = Set(".!?:;…—-,".unicodeScalars)
    static let listMark = Regex("\\d+[.)]\\s|[•·▪◦‣*]\\s")  // the item's own number or bullet; "A. ", "I. ", "— " are text
    static let integer = Regex("^\\s*(-?[0-9]{1,100})\\s*$")
    static let maxDepth = 100  // elements: deeper nesting is flattened into the element at this depth
    static let maxUnpacked = 500_000_000  // extract_text.MAX_UNPACKED
    static let tooBig = "epub распаковывается больше чем в 500 МБ: это не книга"
    /// Charsets a document may declare that both importers read alike: TXT's code page tables.
    static let charsets: [String: String] = [
        "utf-8": "utf-8", "utf8": "utf-8", "windows-1251": "cp1251", "cp1251": "cp1251", "x-cp1251": "cp1251",
        "koi8-r": "koi8_r", "koi8r": "koi8_r", "ibm866": "cp866", "cp866": "cp866", "x-mac-cyrillic": "mac_cyrillic",
        "iso-8859-1": "latin-1", "latin1": "latin-1", "latin-1": "latin-1",
    ]
    static let declared = Regex("<\\?xml[^>]*?[ \\t\\r\\n]encoding[ \\t\\r\\n]*=[ \\t\\r\\n]*[\"']([A-Za-z0-9._:-]+)[\"']")
    static let metaCharset = Regex("<meta[^>]*?charset[ \\t\\r\\n]*=[ \\t\\r\\n]*[\"']?([A-Za-z0-9._:-]+)", ignoreCase: true)
    static let verseClass = Regex("verse|stanza|poem")
    static let ends: Set<String> = ["body", "html", "#document"]
    /// Site chrome of Wikisource exports and the like: never read.
    static let boilerplate: Set<String> = [
        "ws-noexport", "noprint", "licensecontainer", "license", "mw-editsection", "navbox", "metadata", "ws-header",
        "headertemplate", "header_notes", "wst-auxtoc",
    ]
    static let layout: Set<String> = ["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dl", "table", "blockquote", "pre"]
    static let maxCell = 100  // characters: a longer cell is a column of text, not data

    static func extract(_ data: Data, stem: String) throws -> ImportedBook {
        let zip: Zip
        do { zip = try Zip(data) } catch { throw BookImport.failure(error) }
        if zip.entries.reduce(0, { $0 + $1.size }) > maxUnpacked { throw BookImport.Failure(message: tooBig) }
        let pkg = try Package(zip)
        try refuseDRM(zip, docs: pkg.docs)
        var book = ImportedBook(title: pkg.title.isEmpty ? stem : pkg.title, author: pkg.author)
        if !pkg.language.isEmpty { book.language = pkg.language }

        let notes = Notes(pkg)
        let w = Walker(pkg, notes, note: nil)
        var starts: [String: [(String, Int)]] = [:]
        for e in pkg.toc {
            if !e.frag.isEmpty, let el = pkg.ids[e.doc]?[e.frag] {
                w.starts[ObjectIdentifier(el), default: []].append((e.title, e.level))
            } else {
                starts[e.doc, default: []].append((e.title, e.level))
            }
        }
        var cssFiles: [String: String] = [:]
        for doc in pkg.docs {
            guard let root = pkg.soups[doc] else { continue }
            // the document's CSS in order: linked files and <style> elements
            var sheets: [String] = []
            for el in root.descendants() where el.name == "link" || el.name == "style" {
                if el.name == "style" {
                    var css = ""
                    for c in el.children {
                        switch c {
                        case .raw(let t), .text(let t): css += t
                        default: break
                        }
                    }
                    sheets.append(css)
                    continue
                }
                guard (el.attrs["rel"] ?? "").lowercased().contains("stylesheet"), let href = el.attrs["href"], !href.isEmpty,
                    let full = pkg.link(href, doc).path
                else { continue }
                if cssFiles[full] == nil { cssFiles[full] = (try? zip.read(full)).map { String(decoding: $0, as: UTF8.self) } ?? "" }
                sheets.append(cssFiles[full]!)
            }
            let body = root.descendants("body").first ?? root
            w.doc = doc
            w.sheet = Style.Sheet(sheets)
            w.gap = 0
            for (title, level) in starts[doc] ?? [] { w.openChapter(title, level) }
            w.walk(body, Style.inherit(.init(), w.own(body)), ("p", nil))
        }
        w.trail()
        if w.chapters.isEmpty { w.chapters.append(.init(id: "s0", title: "", level: 1, firstBlock: 0)) }
        book.chapters = w.chapters
        book.blocks = w.blocks
        for n in notes.list {
            let nw = Walker(pkg, notes, note: n.container)
            nw.doc = n.doc
            nw.block(n.container, .init(), ("p", nil))
            book.setNote(n.id, noteBody(nw, cut: leadingBacklink(nw)))
        }
        if let c = pkg.cover, let bytes = try? zip.read(c) {
            let ext = Path.splitext(c).ext.lowercased()
            pkg.files.append(("cover" + (ext.isEmpty ? ".jpg" : ext), bytes))
        }
        book.images = pkg.files
        return book
    }

    /// Chapters encrypted for a store's reader cannot be read; obfuscated fonts are no obstacle.
    private static func refuseDRM(_ zip: Zip, docs: [String]) throws {
        guard let data = try? zip.read("META-INF/encryption.xml") else { return }
        let xml = String(decoding: data, as: UTF8.self)
        let uris = Regex("URI=\"([^\"]+)\"").all(xml).compactMap { $0[1].map { Path.normpath($0.removingPercentEncoding ?? $0) } }
        if uris.contains(where: { u in docs.contains { $0 == u || $0.hasSuffix("/" + u) } }) {
            throw BookImport.Failure(message: "Книга защищена от копирования")
        }
    }

    // MARK: small rules

    static func tokens(_ el: Node, _ attr: String) -> Set<String> { Set(Py.split((el.attrs[attr] ?? "").lowercased())) }
    static func classes(_ el: Node) -> [String] { Py.split(el.attrs["class"] ?? "") }

    /// Python's `str.isalnum()` for one character.
    static func isAlnum(_ c: Unicode.Scalar) -> Bool {
        switch c.properties.generalCategory {
        case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter: return true
        default: return c.properties.numericType != nil
        }
    }

    /// `urllib.parse.unquote(s, errors="replace")`.
    static func unquote(_ s: String) -> String {
        guard s.contains("%") else { return s }
        let t = Array(s.unicodeScalars)
        var out = String.UnicodeScalarView()
        var bytes: [UInt8] = []
        func hex(_ c: Unicode.Scalar) -> UInt8? {
            switch c.value {
            case 0x30...0x39: return UInt8(c.value - 0x30)
            case 0x41...0x46: return UInt8(c.value - 0x41 + 10)
            case 0x61...0x66: return UInt8(c.value - 0x61 + 10)
            default: return nil
            }
        }
        func flush() {
            if !bytes.isEmpty { out.append(contentsOf: String(decoding: bytes, as: UTF8.self).unicodeScalars) }
            bytes.removeAll()
        }
        var i = 0
        while i < t.count {
            if t[i] == "%", i + 2 < t.count, let h = hex(t[i + 1]), let l = hex(t[i + 2]) {
                bytes.append(h << 4 | l)
                i += 3
                continue
            }
            if t[i].isASCII {
                bytes.append(UInt8(t[i].value))
            } else {
                flush()
                out.append(t[i])
            }
            i += 1
        }
        flush()
        return String(out)
    }

    /// (decoded path, decoded fragment) of a link.
    static func hrefPath(_ href: String) -> (path: String, frag: String) {
        let s = Py.strip(href)
        guard let i = s.firstIndex(of: "#") else { return (unquote(s), "") }
        return (unquote(String(s[..<i])), unquote(String(s[s.index(after: i)...])))
    }

    /// The attributes of one OPF tag, in either quote style, entities resolved; the first of a name counts.
    static func attrsOf(_ s: String) -> [String: String] {
        var out: [String: String] = [:]
        for m in Regex("([\\w:.-]+)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)')").all(s) {
            guard let k = m[1], out[k] == nil else { continue }
            out[k] = HTMLText.unescape(m[2] ?? m[3] ?? "")
        }
        return out
    }

    /// Every `<name …>` of the OPF with any namespace prefix, as attributes.
    static func tags(_ xml: String, _ name: String) -> [[String: String]] {
        Regex("<(?:[\\w-]+:)?\(name)\\b([^>]*)>").all(xml).map { attrsOf($0[1] ?? "") }
    }

    /// `_tag`: the first element of that name in the OPF, entities resolved, its whitespace collapsed.
    static func tag(_ xml: String, _ name: String) -> String {
        guard let m = Regex("<(?:[\\w-]+:)?\(name)\\b[^>]*>(.*?)</(?:[\\w-]+:)?\(name)\\s*>", dotAll: true).search(xml),
            let v = m[1]
        else { return "" }
        return Py.collapse(HTMLText.unescape(v))
    }

    static func joinTitle(_ lines: [String]) -> String {
        var out = ""
        for x in lines {
            let p = Py.collapse(x)
            guard !p.isEmpty else { continue }
            out = out.isEmpty ? p : out + (out.unicodeScalars.last.map { titleEnd.contains($0) } == true ? " " : ". ") + p
        }
        return out
    }

    static func lines(_ t: Py.Text) -> [String] {
        t.split(separator: "\n", omittingEmptySubsequences: false).map { Py.string(Array($0)) }
    }

    /// A note link's text without its brackets: `{721}` -> `721`, `[1]` -> `1`.
    static func marker(_ a: Node) -> String {
        var t = Array(Py.collapse(a.allText).unicodeScalars)
        let brackets: Set<Unicode.Scalar> = ["[", "]", "(", ")", "{", "}"]
        while let f = t.first, brackets.contains(f) { t.removeFirst() }
        while let l = t.last, brackets.contains(l) { t.removeLast() }
        return Py.strip(Py.string(t))
    }

    static func isBoilerplate(_ el: Node) -> Bool {
        classes(el).contains { boilerplate.contains($0.lowercased()) }
            || boilerplate.contains((el.attrs["id"] ?? "").lowercased()) || tokens(el, "epub:type").contains("toc")
    }

    /// Wikisource's "About this digital edition" page, which an export adds at its end.
    static func creditsPage(_ root: Node) -> Bool {
        root.descendants("title").first.map { Py.strip($0.allText) == "MediaWiki:Wsexport_about" } == true
            || root.descendants().contains { $0.attrs["id"] == "ws-contributor" }
    }

    /// A table's own rows, in order (not those of a table inside it).
    static func tableRows(_ t: Node) -> [Node] {
        var out: [Node] = []
        func rows(_ el: Node) {
            for c in el.elements {
                if c.name == "tr" { out.append(c) } else if c.name != "table" { rows(c) }
            }
        }
        rows(t)
        return out
    }

    /// A notes section (endnotes, footnotes) by type, role or class.
    static func plural(_ x: Node) -> Bool {
        !tokens(x, "epub:type").isDisjoint(with: notesTypes) || !tokens(x, "role").isDisjoint(with: notesRoles)
            || classes(x).contains { notesClasses.contains($0.lowercased()) }
    }

    /// `decode`: a document's text by its byte order mark, else the charset its XML declaration or <meta> names,
    /// else UTF-8.
    static func decode(_ data: Data) -> String {
        let b = [UInt8](data)
        if b.starts(with: [0xEF, 0xBB, 0xBF]) { return String(decoding: b[3...], as: UTF8.self) }
        if b.starts(with: [0xFF, 0xFE]) || b.starts(with: [0xFE, 0xFF]) {
            let little = b[0] == 0xFF
            var units: [UInt16] = []
            var i = 2
            while i + 1 < b.count {
                units.append(little ? UInt16(b[i]) | UInt16(b[i + 1]) << 8 : UInt16(b[i]) << 8 | UInt16(b[i + 1]))
                i += 2
            }
            var s = String(decoding: units, as: UTF16.self)
            if b.count % 2 == 1 { s.unicodeScalars.append("\u{FFFD}") }
            return s
        }
        let head = String(String.UnicodeScalarView(b.prefix(1024).map { Unicode.Scalar($0) }))
        let label = (declared.search(head) ?? metaCharset.search(head))?[1].map { $0.lowercased() }
        switch label.flatMap({ charsets[$0] }) {
        case "latin-1"?:
            return String(String.UnicodeScalarView(b.map { Unicode.Scalar($0) }))
        case let name? where name != "utf-8":
            let high = TXT.codePages.first { $0.name == name }!.high
            return String(String.UnicodeScalarView(b.map { $0 < 0x80 ? Unicode.Scalar($0) : Unicode.Scalar(high[Int($0) - 0x80]) ?? "\u{FFFD}" }))
        default:
            return String(decoding: b, as: UTF8.self)
        }
    }

    /// `parse`: a document as tag soup, nesting deeper than maxDepth flattened: the element at that depth keeps,
    /// in order, the text and the empty elements (a picture, a line break) of everything below it, a block's set
    /// off by spaces. Taken apart without recursion, so a pathological chapter costs no stack.
    static func parse(_ data: Data) -> Node {
        let root = autoreleasepool { HTMLText.parse(decode(data)) }
        var stack: [(Node, Int)] = [(root, 0)]
        while let (el, d) = stack.popLast() {
            for c in el.elements {
                if d + 1 < maxDepth {
                    stack.append((c, d + 1))
                    continue
                }
                var out: [Node.Child] = []
                var todo = Array(c.children.reversed())
                c.children = []
                while let x = todo.popLast() {
                    if case .element(let n) = x, !n.children.isEmpty {
                        let block = blockTags.contains(n.name)  // a block's content stays apart from its neighbours'
                        if block { todo.append(.text(" ")) }
                        todo.append(contentsOf: n.children.reversed())
                        if block { todo.append(.text(" ")) }
                        n.children = []
                    } else {
                        out.append(x)
                    }
                }
                c.children = out
            }
        }
        return root
    }

    /// `image_name`: a picture's file name, the zip name's last part in NFC, each run of characters other than
    /// letters, digits (Unicode categories L*, N*) and `._-` one `_`.
    static func imageName(_ path: String) -> String {
        var out = String.UnicodeScalarView()
        var run = false
        for ch in Path.basename(path).precomposedStringWithCanonicalMapping.unicodeScalars {
            let keep: Bool
            switch ch.properties.generalCategory {
            case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter, .decimalNumber,
                .letterNumber, .otherNumber:
                keep = true
            default:
                keep = ch == "." || ch == "_" || ch == "-"
            }
            if keep {
                out.append(ch)
                run = false
            } else if !run {
                out.append("_")
                run = true
            }
        }
        return String(out)
    }

    /// Python's `str(int(s))` for `-?[0-9]+`.
    static func canonical(_ s: String) -> String {
        let neg = s.hasPrefix("-")
        let digits = s.drop { $0 == "-" }.drop { $0 == "0" }
        return digits.isEmpty ? "0" : (neg ? "-" : "") + digits
    }

    /// n + 1 for a number written as `canonical` writes it, of any length.
    static func successor(_ n: String) -> String {
        if n.hasPrefix("-") {
            var d = Array(n.utf8.dropFirst())
            var i = d.count - 1
            while d[i] == 0x30 {
                d[i] = 0x39
                i -= 1
            }
            d[i] -= 1
            let m = canonical(String(decoding: d, as: UTF8.self))
            return m == "0" ? "0" : "-" + m
        }
        var d = Array(n.utf8)
        var i = d.count - 1
        while i >= 0, d[i] == 0x39 {
            d[i] = 0x30
            i -= 1
        }
        if i < 0 { d.insert(0x31, at: 0) } else { d[i] += 1 }
        return String(decoding: d, as: UTF8.self)
    }

    // MARK: the package

    struct TocEntry {
        var doc: String
        var frag: String
        var title: String
        var level: Int
    }

    final class Package {
        let zip: Zip
        var canon: [String: String] = [:]  // Swift compares strings canonically: an NFC href finds an NFD entry
        var lower: [String: String] = [:]
        var imageNames: [String: String] = [:]  // zip name -> file name in images/
        var docs: [String] = []
        var title = "", author = "", language = ""
        var cover: String?
        var soups: [String: Node] = [:]
        var ids: [String: [String: Node]] = [:]
        var order: [ObjectIdentifier: Int] = [:]
        var parent: [ObjectIdentifier: Node] = [:]
        var toc: [TocEntry] = []
        var files: [(name: String, data: Data)] = []

        struct Item {
            var attrs: [String: String]
            var path: String?
            var href: String { attrs["href"] ?? "" }
        }

        init(_ zip: Zip) throws {
            self.zip = zip
            for n in zip.names {
                canon[n] = n  // the last of a name, as zipfile reads it
                lower[n.lowercased()] = n
            }
            guard let container = resolve("META-INF/container.xml"), let cdata = try? zip.read(container),
                let m = Regex("full-path\\s*=\\s*[\"']([^\"']+)[\"']").search(String(decoding: cdata, as: UTF8.self)),
                let full = m[1], let opf = resolve(HTMLText.unescape(full)), let odata = try? zip.read(opf)
            else { throw BookImport.Failure(message: BookImport.corrupt) }
            let base = Path.dirname(opf)
            let xml = EPUB.decode(odata)
            var items: [String: Item] = [:]
            var order: [Item] = []
            for a in EPUB.tags(xml, "item") {
                guard let id = a["id"], let href = a["href"] else { continue }
                let it = Item(attrs: a, path: resolve(Path.join(base, EPUB.hrefPath(href).path)))
                if items[id] == nil { order.append(it) }
                items[id] = it
            }
            func isDoc(_ it: Item) -> Bool {
                let lower = it.href.lowercased()
                return (it.attrs["media-type"] ?? "").contains("html") || lower.hasSuffix(".xhtml") || lower.hasSuffix(".html")
                    || lower.hasSuffix(".htm")
            }
            let nav = order.first { Py.split($0.attrs["properties"] ?? "").contains("nav") }
            let skip: Set<String> = nav?.path.map { [$0] } ?? []  // the table of contents is not read as text
            for ref in EPUB.tags(xml, "itemref") {
                if let it = items[ref["idref"] ?? ""], isDoc(it), let p = it.path, !docs.contains(p), !skip.contains(p) {
                    docs.append(p)
                }
            }
            if docs.isEmpty {  // no usable spine: the manifest's documents in order
                for it in order where isDoc(it) {
                    if let p = it.path, !docs.contains(p), !skip.contains(p) { docs.append(p) }
                }
            }
            title = EPUB.tag(xml, "title")
            author = EPUB.tag(xml, "creator")
            language = EPUB.tag(xml, "language")
            var cov: Item?
            if let meta = EPUB.tags(xml, "meta").first(where: { $0["name"] == "cover" }) {
                cov = items[meta["content"] ?? ""]
            }
            if cov == nil { cov = order.first { Py.split($0.attrs["properties"] ?? "").contains("cover-image") } }
            cover = cov?.path
            var k = 0
            for d in docs {
                let bytes: Data
                do { bytes = try zip.read(d) } catch { throw BookImport.failure(error) }
                let root = EPUB.parse(bytes)
                if EPUB.creditsPage(root) { continue }
                soups[d] = root
                var map: [String: Node] = [:]
                func walk(_ n: Node) {
                    for e in n.elements {
                        self.parent[ObjectIdentifier(e)] = n
                        self.order[ObjectIdentifier(e)] = k
                        k += 1
                        for key in [e.attrs["id"], e.name == "a" ? e.attrs["name"] : nil] {
                            if let key, !key.isEmpty, map[key] == nil { map[key] = e }
                        }
                        walk(e)
                    }
                }
                walk(root)
                ids[d] = map
            }
            docs = docs.filter { soups[$0] != nil }
            let spine = EPUB.tags(xml, "spine").first ?? [:]
            let ncx = items[spine["toc"] ?? ""] ?? order.first { $0.attrs["media-type"] == "application/x-dtbncx+xml" }
            if let p = nav?.path { toc = navToc(p) }
            if toc.isEmpty, let p = ncx?.path { toc = ncxToc(p) }
            // headings per level, those of a notes section left out; the top level that has two or more is the
            // chapters' (deeper ones are headings inside a chapter)
            var heads: [Int: Int] = [:]
            for d in docs {
                for h in soups[d]?.descendants() ?? [] {
                    if let level = EPUB.headings[h.name], !ancestors(h).contains(where: EPUB.plural) { heads[level, default: 0] += 1 }
                }
            }
            let top = heads.keys.sorted().first { heads[$0]! >= 2 }.map { heads[$0]! } ?? 0
            let few = toc.count < 2 || Set(toc.map(\.doc)).count * 2 < docs.count
            if !heads.isEmpty && (few || toc.count * 2 < top) { toc = [] }  // a contents of files: chapters from headings
        }

        /// A path inside the zip as it is spelled there: exact, else ignoring case.
        func resolve(_ path: String) -> String? {
            var p = Substring(Path.normpath(path))
            while p.hasPrefix("/") { p = p.dropFirst() }
            let s = String(p)
            return canon[s] ?? lower[s.lowercased()]
        }

        func link(_ href: String, _ doc: String) -> (path: String?, frag: String) {
            let (path, frag) = EPUB.hrefPath(href)
            return (path.isEmpty ? doc : resolve(Path.join(Path.dirname(doc), path)), frag)
        }

        func soup(_ path: String) -> Node? {
            if let s = soups[path] { return s }
            return (try? zip.read(path)).map(EPUB.parse)
        }

        func ancestors(_ n: Node) -> [Node] {
            var out: [Node] = []
            var x = parent[ObjectIdentifier(n)]
            while let p = x {
                out.append(p)
                x = parent[ObjectIdentifier(p)]
            }
            return out
        }

        private func entry(_ href: String, _ doc: String, _ title: String, _ level: Int, _ out: inout [TocEntry]) {
            let (path, frag) = link(href, doc)
            if let path, soups[path] != nil { out.append(TocEntry(doc: path, frag: frag, title: title, level: min(level, 4))) }
        }

        private func navToc(_ path: String) -> [TocEntry] {
            var out: [TocEntry] = []
            guard let root = soup(path) else { return out }
            let navs = root.descendants("nav")
            guard let nav = navs.first(where: { EPUB.tokens($0, "epub:type").contains("toc") }) ?? navs.first,
                let ol = nav.descendants("ol").first
            else { return out }
            func walk(_ ol: Node, _ level: Int) {
                for li in ol.elements where li.name == "li" {
                    if let label = li.elements.first(where: { $0.name != "ol" }) {
                        let a = label.name == "a" ? label : label.descendants("a").first
                        if let h = a?.attrs["href"], !h.isEmpty { entry(h, path, EPUB.joinTitle([label.allText]), level, &out) }
                    }
                    if let sub = li.first("ol") { walk(sub, level + 1) }
                }
            }
            walk(ol, 1)
            return out
        }

        private func ncxToc(_ path: String) -> [TocEntry] {
            var out: [TocEntry] = []
            guard let map = soup(path)?.descendants("navmap").first else { return out }
            func walk(_ el: Node, _ level: Int) {
                for p in el.elements where p.name == "navpoint" {
                    if let src = p.first("content")?.attrs["src"], !src.isEmpty {
                        entry(src, path, EPUB.joinTitle([p.first("navlabel")?.allText ?? ""]), level, &out)
                    }
                    walk(p, level + 1)
                }
            }
            walk(map, 1)
            return out
        }

        /// Save a picture an <img> or svg <image> shows into images/ (once, under a name of its own); its src.
        func image(_ el: Node, _ doc: String) -> String? {
            let x = el.attrs["xlink:href"].flatMap { $0.isEmpty ? nil : $0 }
            guard let src = el.name == "img" ? el.attrs["src"] : x ?? el.attrs["href"], !src.isEmpty,
                let full = resolve(Path.join(Path.dirname(doc), EPUB.hrefPath(src).path))
            else { return nil }
            if let name = imageNames[full] { return "images/\(name)" }
            guard let bytes = try? zip.read(full) else { return nil }
            var name = EPUB.imageName(full)
            let (root, ext) = Path.splitext(name)
            let taken = Set(imageNames.values.map { $0.lowercased() })
            var k = 1
            while taken.contains(name.lowercased()) {  // the same name in another folder
                k += 1
                name = "\(root)-\(k)\(ext)"
            }
            imageNames[full] = name
            files.append((name, bytes))
            return "images/\(name)"
        }
    }

    // MARK: notes

    /// Which links are note references and which elements are their notes (KOReader's and foliate's rules).
    final class Notes {
        let pkg: Package
        var links: [ObjectIdentifier: (id: String, m: String)] = [:]
        var containers: Set<ObjectIdentifier> = []
        var list: [(id: String, container: Node, doc: String)] = []
        var marks: [ObjectIdentifier: Set<String>] = [:]
        var near: [ObjectIdentifier: Set<ObjectIdentifier>] = [:]

        init(_ pkg: Package) {
            self.pkg = pkg
            var found: [(a: Node, c: Node, doc: String, frag: String)] = []
            for doc in pkg.docs {
                for a in pkg.soups[doc]?.descendants("a") ?? [] where a.attrs["href"] != nil {
                    if let got = judge(a, doc) { found.append((a, got.c, got.doc, got.frag)) }
                }
            }
            let held = Set(found.map { ObjectIdentifier($0.c) })
            found = found.filter { f in !pkg.ancestors(f.a).contains { held.contains(ObjectIdentifier($0)) } }
            var used: Set<String> = []
            var by: [ObjectIdentifier: String] = [:]
            for f in found {
                let cid = ObjectIdentifier(f.c)
                if by[cid] == nil {
                    let first = f.c.attrs["id"].flatMap { $0.isEmpty ? nil : $0 } ?? f.frag
                    var nid = first, k = 1
                    while used.contains(nid) {
                        k += 1
                        nid = "\(first)-\(k)"
                    }
                    used.insert(nid)
                    by[cid] = nid
                    list.append((nid, f.c, f.doc))
                    containers.insert(cid)
                }
                let m = EPUB.marker(f.a)
                links[ObjectIdentifier(f.a)] = (by[cid]!, m)
                marks[cid, default: []].insert(m)
            }
            // per note: its references, the inline elements around them and their blocks, where a back link points
            for f in found {
                var x: Node? = f.a
                while let n = x {
                    near[ObjectIdentifier(f.c), default: []].insert(ObjectIdentifier(n))
                    if !EPUB.inlineTags.contains(n.name) { break }
                    x = pkg.parent[ObjectIdentifier(n)]
                }
            }
        }

        private func judge(_ a: Node, _ doc: String) -> (c: Node, doc: String, frag: String)? {
            let types = EPUB.tokens(a, "epub:type"), roles = EPUB.tokens(a, "role")
            if !types.isDisjoint(with: ["backlink", "link"]) || !roles.isDisjoint(with: ["doc-backlink", "doc-link"])
                || pkg.ancestors(a).contains(where: { $0.name == "nav" })
            {
                return nil
            }
            let (tdoc, frag) = pkg.link(a.attrs["href"] ?? "", doc)
            guard let tdoc, !tdoc.isEmpty, !frag.isEmpty, let target = pkg.ids[tdoc]?[frag] else { return nil }
            var block = target
            while EPUB.inlineTags.contains(block.name), let p = pkg.parent[ObjectIdentifier(block)] { block = p }
            if EPUB.ends.contains(block.name) { return nil }
            var note: Node?
            var x: Node? = block
            while let n = x, !EPUB.ends.contains(n.name) {
                if noteLike(n) {
                    note = n
                    break
                }
                if ["section", "article", "main"].contains(n.name) || plural(n) { break }
                x = pkg.parent[ObjectIdentifier(n)]
            }
            let strong = note != nil || inNotes(block)
            let c = note ?? block
            if c === a || pkg.ancestors(a).contains(where: { $0 === c }) || pkg.ancestors(c).contains(where: { $0 === a }) {
                return nil
            }
            var sup = false
            var p = pkg.parent[ObjectIdentifier(a)]
            for _ in 0..<3 {  // the link inside a <sup>, or inside inline elements inside one
                guard let q = p, EPUB.inlineTags.contains(q.name) else { break }
                if q.name == "sup" {
                    sup = true
                    break
                }
                p = pkg.parent[ObjectIdentifier(q)]
            }
            let kids = a.elements
            sup = sup
                || (kids.count == 1 && kids[0].name == "sup"
                    && !a.children.contains {
                        if case .text(let t) = $0 { return !Py.strip(t).isEmpty }
                        return false
                    })
            if !(strong || sup || types.contains("noteref") || roles.contains("doc-noteref")) { return nil }
            let text = c.allText
            let hasImage = c.descendants().contains { $0.name == "img" || $0.name == "image" }
            if text.unicodeScalars.count > EPUB.maxNote || (Py.strip(text).isEmpty && !hasImage) { return nil }
            if !strong {
                let heads: Set<String> = ["h1", "h2", "h3", "h4", "h5", "h6"]
                if heads.contains(c.name) || c.descendants().contains(where: { heads.contains($0.name) }) { return nil }
                if (pkg.order[ObjectIdentifier(c)] ?? 0) < (pkg.order[ObjectIdentifier(a)] ?? 0) { return nil }  // a link back
            }
            return (c, tdoc, frag)
        }

        func plural(_ x: Node) -> Bool { EPUB.plural(x) }

        func inNotes(_ x: Node) -> Bool { plural(x) || pkg.ancestors(x).contains { plural($0) } }

        func noteLike(_ x: Node) -> Bool {
            if x.name == "aside" || !EPUB.tokens(x, "epub:type").isDisjoint(with: EPUB.noteTypes)
                || !EPUB.tokens(x, "role").isDisjoint(with: EPUB.noteRoles)
            {
                return true
            }
            if EPUB.classes(x).contains(where: { EPUB.noteClass.search($0.lowercased()) != nil }) { return true }
            guard x.name == "li", let p = pkg.parent[ObjectIdentifier(x)], p.name == "ol" || p.name == "ul" else { return false }
            return inNotes(x)
        }

        /// A link inside a note back to the text that refers to it (or typed as one).
        func backlink(_ a: Node, _ doc: String, _ note: Node) -> Bool {
            if EPUB.tokens(a, "epub:type").contains("backlink") || EPUB.tokens(a, "role").contains("doc-backlink") { return true }
            let m = EPUB.marker(a)
            if !(marks[ObjectIdentifier(note)] ?? []).contains(m), m.unicodeScalars.contains(where: EPUB.isAlnum) {
                return false  // a cross-reference in words ("par. 4"), not the marker or an arrow
            }
            let (tdoc, frag) = pkg.link(a.attrs["href"] ?? "", doc)
            var t = tdoc.flatMap { !$0.isEmpty && !frag.isEmpty ? pkg.ids[$0]?[frag] : nil }
            let close = near[ObjectIdentifier(note)] ?? []
            while let n = t, EPUB.inlineTags.contains(n.name), let p = pkg.parent[ObjectIdentifier(n)] {
                if close.contains(ObjectIdentifier(n)) { return true }
                t = p
            }
            return t.map { close.contains(ObjectIdentifier($0)) } ?? false
        }
    }

    // MARK: inline text

    /// `extract_text.inline_text`'s result.
    struct Rich {
        var text: Py.Text = []
        var marks: [[[Int]]] = [[], [], [], []]  // em, strong, sup, sub
        var notes: [(pos: Int, id: String, m: String)] = []
        var pics: [(pos: Int, src: String)] = []

        /// Every offset moved by `d` (the text changes apart).
        mutating func shift(_ d: Int) {
            marks = marks.map { r in
                r.compactMap { g in
                    let a = max(g[0] + d, 0), b = g[1] + d
                    return b > a ? [a, b] : nil
                }
            }
            notes = notes.map { (max($0.pos + d, 0), $0.id, $0.m) }
            pics = pics.map { (max($0.pos + d, 0), $0.src) }
        }
    }

    /// `extract_text._Flat`: whitespace collapsed on the way, a mark merged with the one it touches.
    struct Flat {
        var out: Py.Text = []
        var marks: [[[Int]]] = [[], [], [], []]
        var notes: [(pos: Int, id: String, m: String)] = []
        var pics: [(pos: Int, src: String)] = []
        let cell: Bool
        var lead = 0, start = true, space = false, breaks = 0, afterNote = false
        var last: Unicode.Scalar?
        var lastMarks = 0
        var quote: (at: Int, n: Int)?  // the space before a quote after a note link

        init(cell: Bool) { self.cell = cell }

        mutating func text(_ s: String, _ m: Int, _ pre: Bool) {
            for var ch in s.unicodeScalars {
                if ch == "\u{A0}" { ch = " " }
                if start {
                    if ch == " " {
                        out.append(" ")
                        lead += 1
                        continue
                    }
                    start = false
                }
                if ch == "\n" && pre {
                    br()
                } else if Py.isSpace(ch) {
                    space = true
                } else {
                    put(ch, m)
                }
            }
        }

        mutating func br() {
            if cell { space = true } else { breaks += 1 }
        }

        mutating func put(_ ch: Unicode.Scalar, _ m: Int) {
            if quote != nil {
                if space || breaks > 0 || !EPUB.isAlnum(ch) { unquote() }  // `сказал {1}" и` -> `сказал" и`
                quote = nil
            }
            if out.count > lead {
                var sep: Py.Text = []
                if afterNote && EPUB.closers.contains(ch) {
                    sep = []  // `Кит {1}.` -> `Кит.`
                } else if breaks > 0 {
                    sep = Py.Text(repeating: "\n", count: breaks)
                } else if space || (afterNote && (last.map(EPUB.isAlnum) ?? false) && EPUB.isAlnum(ch)) {
                    sep = [" "]  // `организме[69]не` -> `организме не`
                }
                if !sep.isEmpty && afterNote && EPUB.quotes.contains(ch) { quote = (out.count, sep.count) }
                let both = lastMarks & m
                for c in sep { emit(c, both) }
            }
            space = false
            breaks = 0
            afterNote = false
            emit(ch, m)
            last = ch
            lastMarks = m
        }

        /// Take out the space before a quote that turned out to close: as if a closer had followed the note link.
        mutating func unquote() {
            guard let (w, n) = quote else { return }
            out.removeSubrange(w..<(w + n))
            func at(_ x: Int) -> Int { x <= w ? x : max(w, x - n) }
            marks = marks.map { rs in
                var o: [[Int]] = []
                for r in rs {
                    let a = at(r[0]), b = at(r[1])
                    if b <= a { continue }
                    if let l = o.last, l[1] == a { o[o.count - 1][1] = b } else { o.append([a, b]) }
                }
                return o
            }
            notes = notes.map { (at($0.pos), $0.id, $0.m) }
            pics = pics.map { (at($0.pos), $0.src) }
        }

        mutating func finish() {
            if quote != nil {
                unquote()
                quote = nil
            }
        }

        mutating func emit(_ ch: Unicode.Scalar, _ m: Int) {
            let pos = out.count
            for k in 0..<4 where m & (1 << k) != 0 {
                if let l = marks[k].last, l[1] == pos { marks[k][marks[k].count - 1][1] = pos + 1 } else { marks[k].append([pos, pos + 1]) }
            }
            out.append(ch)
        }

        mutating func note(_ id: String, _ m: String) {
            notes.append((out.count, id, m))
            afterNote = true
        }
    }

    // MARK: the walker

    typealias Mode = (kind: String, stanza: Int?)

    /// Blocks of the reading flow (or, `note` set, of one note's body) from the documents' elements.
    final class Walker {
        let pkg: Package
        let notes: Notes
        let note: Node?
        var blocks: [ImportedBook.Block] = []
        var chapters: [ImportedBook.Chapter] = []
        var pending: [ImportedBook.Image] = []
        var gap = 0  // empty paragraphs since the last block: blank lines in the book
        var sheet = Style.Sheet([])
        var doc = ""
        var stanzaN = 0
        var prefix: String?  // a list item's number or bullet, for its first block
        var starts: [ObjectIdentifier: [(String, Int)]] = [:]
        let toc: Bool
        private var blockMemo: [ObjectIdentifier: Bool] = [:]
        var pre: Node?  // the <pre> whose blocks are being read: their line ends stay
        var waiting: [(pos: Int, id: String, m: String)] = []  // note links of a paragraph holding nothing else, before any block

        init(_ pkg: Package, _ notes: Notes, note: Node?) {
            self.pkg = pkg
            self.notes = notes
            self.note = note
            toc = !pkg.toc.isEmpty && note == nil
        }

        // hooks of the inline text
        func skip(_ el: Node) -> Bool {
            if EPUB.skipTags.contains(el.name) || el === note { return EPUB.skipTags.contains(el.name) }
            if notes.containers.contains(ObjectIdentifier(el)) || EPUB.isBoilerplate(el) {
                return true
            }
            guard let note, el.name == "a" else { return false }
            return notes.backlink(el, doc, note)
        }

        func noteRef(_ a: Node) -> (id: String, m: String)? { note == nil ? notes.links[ObjectIdentifier(a)] : nil }

        func inline(_ el: Node?, nodes: [Node.Child]? = nil, cell: Bool = false) -> Rich {
            var f = Flat(cell: cell)
            func walk(_ c: Node.Child, _ marks: Int, _ pre: Bool) {
                switch c {
                case .text(let t): f.text(t, marks, pre)
                case .comment, .raw: break
                case .element(let n):
                    if ["script", "style", "template"].contains(n.name) || skip(n) { return }
                    if n.name == "br" {
                        f.br()
                        return
                    }
                    if n.name == "img" || n.name == "image" || n.name == "svg" {
                        for im in n.name == "svg" ? n.descendants("image") : [n] {
                            if let src = pkg.image(im, doc) { f.pics.append((f.out.count, src)) }
                        }
                        return
                    }
                    if n.name == "a", let ref = noteRef(n) {
                        f.note(ref.id, ref.m)
                        return
                    }
                    let block = EPUB.blockTags.contains(n.name)
                    if block { f.space = true }
                    let inner = marks | (EPUB.markBits[n.name] ?? 0)
                    for cc in n.children { walk(cc, inner, pre || n.name == "pre") }
                    if block { f.space = true }
                }
            }
            for c in nodes ?? el?.children ?? [] { walk(c, 0, el?.name == "pre" || pre != nil) }  // inside a <pre>: line ends kept
            f.finish()
            return Rich(text: f.out, marks: f.marks, notes: f.notes, pics: f.pics)
        }

        func own(_ el: Node) -> Style.Decls { sheet.own(el.name, EPUB.classes(el), el.attrs["style"]) }

        func hasBlock(_ el: Node) -> Bool {
            let k = ObjectIdentifier(el)
            if let v = blockMemo[k] { return v }
            let v = el.elements.contains { EPUB.blockTags.contains($0.name) || hasBlock($0) }
            blockMemo[k] = v
            return v
        }

        func isBlock(_ el: Node) -> Bool { EPUB.blockTags.contains(el.name) || hasBlock(el) }

        func openChapter(_ title: String, _ level: Int) {
            trail()
            gap = 0
            chapters.append(.init(id: "s\(chapters.count)", title: title, level: level, firstBlock: blocks.count))
        }

        /// Pictures waiting for a block at a chapter's (or the book's) end close the block before them.
        func trail() {
            guard !pending.isEmpty, !blocks.isEmpty else { return }
            blocks[blocks.count - 1].images += pending.map { ImportedBook.Image(src: $0.src, size: $0.size, after: true) }
            pending.removeAll()
        }

        func openAt(_ el: Node, deep: Bool) {
            guard !starts.isEmpty else { return }
            for x in deep ? [el] + el.descendants() : [el] {
                for (title, level) in starts.removeValue(forKey: ObjectIdentifier(x)) ?? [] { openChapter(title, level) }
            }
        }

        func atChapterStart() -> Bool {
            guard let c = chapters.last else { return false }
            return blocks[min(c.firstBlock, blocks.count)...].allSatisfy { $0.kind == "title" || $0.kind == "subtitle" }
        }

        /// The kind (and stanza) of the paragraphs inside a container.
        func enter(_ el: Node, _ mode: Mode) -> Mode {
            var (kind, stanza) = mode
            let cls = EPUB.classes(el).joined(separator: " "), types = EPUB.tokens(el, "epub:type")
            if EPUB.verseClass.search(cls) != nil || !types.isDisjoint(with: EPUB.verseTypes) {
                kind = "verse"
            } else if cls.contains("epigraph") || types.contains("epigraph") {
                kind = "epigraph"
            } else if cls.contains("text-author") {
                kind = "author"
            } else if el.name == "blockquote" && kind == "p" {
                kind = note == nil && atChapterStart() ? "epigraph" : "cite"
            }
            if cls.contains("stanza") {
                stanzaN += 1
                stanza = stanzaN
            }
            return (kind, stanza)
        }

        func walk(_ el: Node, _ ctx: Style.Decls, _ mode: Mode) {
            let outer = pre
            if el.name == "pre" { pre = el }
            var run: [Node.Child] = []
            var n = "1"  // the number as text: any length, as Python's int
            if el.name == "ol", let v = EPUB.integer.search(el.attrs["start"] ?? "")?[1] { n = EPUB.canonical(v) }
            for c in el.children {
                switch c {
                case .element(let e) where isBlock(e):
                    flush(run, ctx, mode)
                    run = []
                    if e.name == "li" && ["ol", "ul", "menu", "dir"].contains(el.name) {
                        if el.name == "ol" {
                            if let v = EPUB.integer.search(e.attrs["value"] ?? "")?[1] { n = EPUB.canonical(v) }
                            prefix = "\(n). "
                            n = EPUB.successor(n)
                        } else {
                            prefix = "• "
                        }
                        block(e, ctx, mode)
                        prefix = nil
                    } else {
                        block(e, ctx, mode)
                    }
                case .element, .text: run.append(c)
                case .comment, .raw: break
                }
            }
            flush(run, ctx, mode)
            pre = outer
        }

        /// Text and inline elements between blocks: a paragraph of the container's kind.
        func flush(_ run: [Node.Child], _ ctx: Style.Decls, _ mode: Mode) {
            for x in run { if case .element(let e) = x, !skip(e) { openAt(e, deep: true) } }
            if !run.isEmpty { add(nil, mode.kind, ctx, mode, nodes: run) }
        }

        func block(_ c: Node, _ ctx: Style.Decls, _ mode: Mode) {
            let n = c.name
            if skip(c) { return }
            if n == "hr" {
                openAt(c, deep: false)
                return
            }
            if EPUB.headings[n] != nil || n == "h5" || n == "h6" {
                openAt(c, deep: true)
                let r = inline(c)
                if let level = EPUB.headings[n], note == nil {
                    gap = 0  // a blank line before a chapter is not the chapter's
                    let title = EPUB.joinTitle(EPUB.lines(r.text))
                    if !toc && !title.isEmpty { openChapter(title, level) }  // a heading of nothing but a picture opens none
                }
                add(c, EPUB.headings[n] != nil && note == nil ? "title" : "subtitle", ctx, mode, r: r)
            } else if n == "p" {
                openAt(c, deep: true)
                let cls = EPUB.classes(c).joined(separator: " "), types = EPUB.tokens(c, "epub:type")
                let kind =
                    EPUB.verseClass.search(cls) != nil || !types.isDisjoint(with: EPUB.verseTypes)
                    ? "verse"
                    : cls.contains("epigraph") || types.contains("epigraph")
                        ? "epigraph" : cls.contains("text-author") ? "author" : mode.kind
                add(c, kind, ctx, mode)
            } else if n == "table" && dataTable(c) {
                openAt(c, deep: true)
                table(c, ctx)
            } else {
                openAt(c, deep: false)
                let inner = enter(c, mode)
                if hasBlock(c) {
                    walk(c, Style.inherit(ctx, own(c)), inner)
                } else {
                    openAt(c, deep: true)
                    add(c, inner.kind, ctx, inner)
                }
            }
        }

        private func push(_ r: Rich, kind: String, stanza: Int?, sentences: [[Int]], audio: Bool, rows: [[[Int]]] = []) {
            if note == nil && chapters.isEmpty { chapters.append(.init(id: "s0", title: "", level: 1, firstBlock: 0)) }
            let notes = waiting.map { (pos: 0, id: $0.id, m: $0.m) } + r.notes
            waiting = []
            var marks: [Int: String] = [:]
            for (k, x) in notes.enumerated() where !x.m.isEmpty { marks[k] = x.m }
            blocks.append(
                .init(
                    images: pending, id: "b\(blocks.count)", kind: kind, chapter: chapters.count - 1, stanza: stanza,
                    text: Py.string(r.text), em: r.marks[0], strong: r.marks[1], sup: r.marks[2], sub: r.marks[3],
                    notes: notes.map { ($0.pos, $0.id) }, noteMarks: marks, sentences: sentences, audio: audio,
                    pics: r.pics, rows: rows))
        }

        func add(_ el: Node?, _ kind0: String, _ ctx: Style.Decls, _ mode: Mode, nodes: [Node.Child]? = nil, r r0: Rich? = nil) {
            var r = r0 ?? inline(el, nodes: nodes)
            var kind = kind0
            if Py.stripped(r.text).isEmpty {
                pending += r.pics.map { ImportedBook.Image(src: $0.src) }
                if !r.notes.isEmpty {
                    anchor(r.notes)
                } else if let el, el.name == "p", !el.descendants().contains(where: { ["img", "image", "svg"].contains($0.name) }) {
                    gap += 1
                }
                return
            }
            if kind != "verse" {
                var lead = 0
                while lead < r.text.count, r.text[lead] == " " { lead += 1 }
                if lead > 0 {
                    r.shift(-lead)
                    r.text.removeFirst(lead)
                }
                let ls = EPUB.lines(r.text)
                if (kind == "p" || kind == "cite") && ls.filter({ !Py.strip($0).isEmpty }).count >= 4
                    && ls.allSatisfy({ $0.unicodeScalars.count <= 60 })
                {
                    kind = "verse"  // four short lines or more parted by <br>: a poem (two or three: an address)
                }
            }
            if let p = prefix {
                if !EPUB.listMark.matches(Py.string(r.text)) {
                    let t = Array(p.unicodeScalars)
                    r.shift(t.count)
                    r.text = t + r.text
                }
                prefix = nil
            }
            var stanza: Int?
            if kind == "verse" {
                if let s = mode.stanza {
                    stanza = s
                } else if r.text.contains("\n") {
                    stanzaN += 1
                    stanza = stanzaN
                }
            }
            push(r, kind: kind, stanza: stanza, sentences: Py.blockSentences(r.text, kind: kind), audio: true)
            if note == nil { blocks[blocks.count - 1].st = Style.block(kind, el.map(own) ?? .init(), ctx, gap: gap) }
            gap = 0
            pending.removeAll()
        }

        /// Note links of a paragraph with no text of its own: at the end of the block before, else at the start of
        /// the next one.
        func anchor(_ ns: [(pos: Int, id: String, m: String)]) {
            guard !blocks.isEmpty else {
                waiting += ns
                return
            }
            let i = blocks.count - 1, end = blocks[i].text.unicodeScalars.count
            for x in ns {
                if !x.m.isEmpty { blocks[i].noteMarks[blocks[i].notes.count] = x.m }
                blocks[i].notes.append((end, x.id))
            }
        }

        /// A table of data (kind "table"), not one that lays a page out: that one is read as its cells' blocks.
        func dataTable(_ t: Node) -> Bool {
            if !EPUB.tokens(t, "role").isDisjoint(with: ["presentation", "none"])
                || pkg.ancestors(t).contains(where: { $0.name == "table" })
            {
                return false
            }
            let rows = EPUB.tableRows(t)
            let cells = rows.flatMap { $0.elements.filter { $0.name == "td" || $0.name == "th" } }
            if cells.contains(where: { $0.descendants().contains { EPUB.layout.contains($0.name) } }) { return false }
            let heads = Set(t.elements.map(\.name))
            if heads.contains("caption") || heads.contains("thead") || cells.contains(where: { $0.name == "th" }) { return true }
            let cols = rows.map { $0.elements.filter { $0.name == "td" || $0.name == "th" }.count }.max() ?? 0
            return rows.count >= 2 && cols >= 2 && cells.allSatisfy { Py.collapse($0.allText).unicodeScalars.count <= EPUB.maxCell }
        }

        func table(_ t: Node, _ ctx: Style.Decls) {
            if let cap = t.first("caption") { add(cap, "p", ctx, ("p", nil)) }
            let trs = EPUB.tableRows(t)
            var r = Rich()
            var outRows: [[[Int]]] = []
            var sentences: [[Int]] = []
            for tr in trs {
                let cells = tr.elements.filter { $0.name == "td" || $0.name == "th" }
                var got = cells.map { inline($0, cell: true) }
                for i in got.indices {
                    var lead = 0
                    while lead < got[i].text.count, got[i].text[lead] == " " { lead += 1 }
                    if lead > 0 {
                        got[i].shift(-lead)
                        got[i].text.removeFirst(lead)
                    }
                }
                if !got.contains(where: { !$0.text.isEmpty || !$0.pics.isEmpty }) { continue }
                if !outRows.isEmpty { r.text.append("\n") }
                var row: [[Int]] = []
                let start = r.text.count
                for (i, (c, g0)) in zip(cells, got).enumerated() {
                    if i > 0 { r.text.append("\t") }
                    let base = r.text.count
                    var g = g0
                    g.shift(base)
                    r.text += g.text
                    for k in 0..<4 { r.marks[k] += g.marks[k] }
                    r.notes += g.notes
                    r.pics += g.pics
                    row.append(c.name == "th" ? [base, r.text.count, 1] : [base, r.text.count])
                }
                outRows.append(row)
                var a = start, e = r.text.count
                while a < e, r.text[a] == "\t" || r.text[a] == " " { a += 1 }
                while e > a, r.text[e - 1] == "\t" || r.text[e - 1] == " " { e -= 1 }
                if e > a { sentences.append([a, e]) }
            }
            if sentences.isEmpty {
                pending += r.pics.map { ImportedBook.Image(src: $0.src) }
                return
            }
            push(r, kind: "table", stanza: nil, sentences: sentences, audio: false, rows: outRows)
            if note == nil { blocks[blocks.count - 1].st = Style.block("table", own(t), ctx, gap: gap) }
            gap = 0
            pending.removeAll()
        }
    }

    /// `leading_backlink`: whether a note's first text is in a back link, which its walker leaves out.
    static func leadingBacklink(_ w: Walker) -> Bool {
        guard let note = w.note else { return false }
        func first(_ el: Node, _ inside: Bool) -> Bool? {
            for c in el.children {
                switch c {
                case .text(let t):
                    if !Py.strip(t).isEmpty { return inside }
                case .element(let e):
                    if let got = first(e, inside || (e.name == "a" && w.notes.backlink(e, w.doc, note))) { return got }
                case .comment, .raw: continue
                }
            }
            return nil
        }
        return first(note, false) ?? false
    }

    /// A note's blocks as one text: paragraphs parted by a blank line, the lines of one stanza by a line break.
    /// `cut`: the note opens with a back link, so what is left of its number ("1. Text" -> ". Text") goes.
    static func noteBody(_ w: Walker, cut leading: Bool) -> ImportedBook.Note {
        var text: Py.Text = []
        var marks: [[[Int]]] = [[], [], [], []]
        var pics: [(pos: Int, src: String)] = []
        var kinds: [(a: Int, b: Int, kind: String)] = []
        var prev: (kind: String, stanza: Int?)?
        for b in w.blocks {
            let kind = b.kind == "title" ? "subtitle" : b.kind == "table" ? "p" : b.kind
            let sep: Py.Text =
                prev == nil ? [] : kind == "verse" && prev!.kind == "verse" && b.stanza == prev!.stanza ? ["\n"] : ["\n", "\n"]
            let base = text.count + sep.count
            let bt = Array(b.text.unicodeScalars)
            text += sep + bt
            for (k, r) in [b.em, b.strong, b.sup, b.sub].enumerated() { marks[k] += r.map { [$0[0] + base, $0[1] + base] } }
            pics += b.images.map { (base, $0.src) }
            pics += b.pics.map { ($0.pos + base, $0.src) }
            if kind != "p" {
                if sep == ["\n"], let l = kinds.last, l.kind == "verse", l.b == base - 1 {
                    kinds[kinds.count - 1].b = text.count
                } else {
                    kinds.append((base, text.count, kind))
                }
            }
            prev = (kind, b.stanza)
        }
        pics += w.pending.map { (text.count, $0.src) }
        // a number left of a removed back link: "1. Text" -> ". Text"; a note's own "..." stays
        let cutSet: Set<Unicode.Scalar> = [".", ")", "]", ":", " "]
        var cut = 0
        while leading, cut < text.count, cutSet.contains(text[cut]) { cut += 1 }
        if cut > 0 {
            text.removeFirst(cut)
            marks = marks.map { r in
                r.compactMap { g in
                    let a = max(g[0] - cut, 0), e = g[1] - cut
                    return e > a ? [a, e] : nil
                }
            }
            pics = pics.map { (max($0.pos - cut, 0), $0.src) }
            kinds = kinds.compactMap { k in
                let a = max(k.a - cut, 0), e = k.b - cut
                return e > a ? (a, e, k.kind) : nil
            }
        }
        return ImportedBook.Note(
            text: Py.string(text), em: marks[0], strong: marks[1], sup: marks[2], sub: marks[3], pics: pics, kinds: kinds)
    }
}

/// The few `posixpath` functions the epub paths need.
enum Path {
    static func dirname(_ p: String) -> String {
        guard let i = p.lastIndex(of: "/") else { return "" }
        var head = String(p[...i])
        if !head.allSatisfy({ $0 == "/" }) {
            while head.hasSuffix("/") { head.removeLast() }
        }
        return head
    }

    static func basename(_ p: String) -> String {
        p.lastIndex(of: "/").map { String(p[p.index(after: $0)...]) } ?? p
    }

    static func join(_ a: String, _ b: String) -> String {
        if b.hasPrefix("/") || a.isEmpty { return b }
        return a.hasSuffix("/") ? a + b : a + "/" + b
    }

    static func normpath(_ p: String) -> String {
        if p.isEmpty { return "." }
        let initial = p.hasPrefix("//") && !p.hasPrefix("///") ? 2 : p.hasPrefix("/") ? 1 : 0
        var out: [Substring] = []
        for comp in p.split(separator: "/", omittingEmptySubsequences: true) where comp != "." {
            if comp != ".." || (initial == 0 && out.isEmpty) || out.last == ".." {
                out.append(comp)
            } else if !out.isEmpty {
                out.removeLast()
            }
        }
        let s = String(repeating: "/", count: initial) + out.joined(separator: "/")
        return s.isEmpty ? "." : s
    }

    static func splitext(_ p: String) -> (root: String, ext: String) {
        let base = basename(p)
        guard let dot = base.lastIndex(of: ".") else { return (p, "") }
        // leading dots belong to the name
        if base[..<dot].allSatisfy({ $0 == "." }) { return (p, "") }
        let extLen = base.distance(from: dot, to: base.endIndex)
        return (String(p.dropLast(extLen)), String(base[dot...]))
    }
}
