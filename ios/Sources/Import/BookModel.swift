// The book.json model the pipeline writes (pipeline/extract_text.py), built on the phone. The text rules are
// ports of the Python ones and work on Unicode scalars, as Python counts; offsets turn into UTF-16 code units,
// what the reader's `text.slice` counts, only when the book is written out.

import Foundation

struct ImportedBook {
    struct Chapter {
        var id: String
        var title: String
        var level: Int
        var firstBlock: Int
    }

    struct Image {
        var src: String
        var size: (w: Int, h: Int)? = nil
        var after = false  // trails the block it is attached to (at a chapter's or the book's end)
    }

    struct Block {
        var images: [Image] = []
        var id: String
        var kind: String
        var chapter: Int
        var stanza: Int? = nil
        var text: String
        var em: [[Int]] = []  // Unicode scalar offsets, as in Python, until written
        var strong: [[Int]] = []
        var sup: [[Int]] = []
        var sub: [[Int]] = []
        var notes: [(pos: Int, id: String)] = []
        var noteMarks: [Int: String] = [:]  // notes[i]'s marker as the source shows it ("m"), when it had one
        var sentences: [[Int]]
        var audio = true
        var pics: [(pos: Int, src: String)] = []  // pictures inside the text, each a place in it (fb2)
        var rows: [[[Int]]] = []  // a table's cells per row, each [a, b] in text, [a, b, 1] for a header cell
        var st: BlockStyle? = nil  // the book's own look (Style.swift), written last and only when there is one
    }

    /// A note's body: plain text, or text with marks, pictures and paragraph kinds (written as an object).
    struct Note {
        var text: String
        var em: [[Int]] = []
        var strong: [[Int]] = []
        var sup: [[Int]] = []
        var sub: [[Int]] = []
        var pics: [(pos: Int, src: String)] = []
        var kinds: [(a: Int, b: Int, kind: String)] = []  // paragraphs that are not plain p

        /// Written as a bare string: nothing but one paragraph of text.
        var isPlain: Bool {
            em.isEmpty && strong.isEmpty && sup.isEmpty && sub.isEmpty && pics.isEmpty && kinds.isEmpty
                && !text.unicodeScalars.contains("\n")
        }
    }

    var title: String
    var author: String
    var chapters: [Chapter] = []
    var blocks: [Block] = []
    var notes: [(id: String, note: Note)] = []  // in the order the book gives them
    var language = "ru"
    /// Files for images/: a later one of the same name replaces an earlier one, as on disk.
    var images: [(name: String, data: Data)] = []

    mutating func setNote(_ id: String, _ text: String) { setNote(id, Note(text: text)) }

    mutating func setNote(_ id: String, _ note: Note) {
        if let i = notes.firstIndex(where: { $0.id == id }) { notes[i].note = note } else { notes.append((id, note)) }
    }

    var words: Int { blocks.reduce(0) { $0 + Py.split($1.text).count } }

    /// book.json as `extract_text.dump_book` writes it: the same keys, order and separators.
    func json() -> Data {
        let o = JSONOut()
        o.raw("{\"title\": ").string(title).raw(", \"author\": ").string(author).raw(", \"chapters\": [")
        for (i, c) in chapters.enumerated() {
            if i > 0 { o.raw(", ") }
            o.raw("{\"id\": ").string(c.id).raw(", \"title\": ").string(c.title)
            o.raw(", \"level\": \(c.level), \"first_block\": \(c.firstBlock)}")
        }
        o.raw("], \"blocks\": [")
        for (i, b) in blocks.enumerated() {
            if i > 0 { o.raw(", ") }
            let u = Py.utf16Offsets(b.text)
            o.raw("{\"images\": [")
            for (k, im) in b.images.enumerated() {
                if k > 0 { o.raw(", ") }
                o.raw("{\"src\": ").string(im.src)
                if let s = im.size { o.raw(", \"w\": \(s.w), \"h\": \(s.h)") }
                if im.after { o.raw(", \"after\": true") }
                o.raw("}")
            }
            o.raw("], \"id\": ").string(b.id).raw(", \"kind\": ").string(b.kind)
            o.raw(", \"chapter\": \(b.chapter), \"stanza\": ").raw(b.stanza.map(String.init) ?? "null")
            o.raw(", \"text\": ").string(b.text).raw(", \"em\": ").ranges(b.em, u)
            o.marks(b.strong, b.sup, b.sub, u).raw(", \"notes\": [")
            for (k, n) in b.notes.enumerated() {
                if k > 0 { o.raw(", ") }
                o.raw("{\"pos\": \(u(n.pos)), \"id\": ").string(n.id)
                if let m = b.noteMarks[k], !m.isEmpty { o.raw(", \"m\": ").string(m) }
                o.raw("}")
            }
            o.raw("], \"sentences\": ").ranges(b.sentences, u).raw(", \"audio\": \(b.audio)")
            o.pics(b.pics, u)
            if !b.rows.isEmpty {  // a header cell's third number is a flag, not an offset
                let cell = { (c: [Int]) in
                    "[" + c.enumerated().map { String($0.offset < 2 ? u($0.element) : $0.element) }.joined(separator: ", ") + "]"
                }
                o.raw(", \"rows\": [" + b.rows.map { "[" + $0.map(cell).joined(separator: ", ") + "]" }.joined(separator: ", ") + "]")
            }
            if let st = b.st {
                var keys: [String] = []
                if let a = st.a { keys.append("\"a\": \"\(a)\"") }
                if let i = st.i { keys.append("\"i\": " + Self.em(i)) }
                if let m = st.m { keys.append("\"m\": " + Self.em(m)) }
                if let g = st.g { keys.append("\"g\": \(g)") }
                o.raw(", \"st\": {" + keys.joined(separator: ", ") + "}")
            }
            o.raw("}")
        }
        o.raw("], \"notes\": {")
        for (i, n) in notes.enumerated() {
            if i > 0 { o.raw(", ") }
            o.string(n.id).raw(": ")
            let note = n.note
            if note.isPlain {
                o.string(note.text)
                continue
            }
            let u = Py.utf16Offsets(note.text)
            o.raw("{\"text\": ").string(note.text)
            if !note.em.isEmpty { o.raw(", \"em\": ").ranges(note.em, u) }
            o.marks(note.strong, note.sup, note.sub, u).pics(note.pics, u)
            if !note.kinds.isEmpty {
                o.raw(", \"kinds\": [")
                for (k, x) in note.kinds.enumerated() {
                    if k > 0 { o.raw(", ") }
                    o.raw("[\(u(x.a)), \(u(x.b)), ").string(x.kind).raw("]")
                }
                o.raw("]")
            }
            o.raw("}")
        }
        o.raw("}")
        // the offsets are UTF-16, the pipeline's are code points: the two differ only past the BMP (an emoji),
        // and only then does the reader need to be told
        let past = { (s: String) in s.unicodeScalars.contains { $0.value > 0xFFFF } }
        if blocks.contains(where: { past($0.text) }) || notes.contains(where: { !$0.note.isPlain && past($0.note.text) }) {
            o.raw(", \"offsets\": \"utf16\"")
        }
        o.raw("}")
        return Data(o.out.utf8)
    }
}

extension ImportedBook {
    /// Tenths of an em as Python writes `t // 10` or `t / 10`: 2, 1.5.
    static func em(_ t: Int) -> String { t % 10 == 0 ? "\(t / 10)" : "\(t / 10).\(t % 10)" }
}

/// A JSON writer with Python's separators and escapes (ensure_ascii=False).
private final class JSONOut {
    var out = ""

    @discardableResult func raw(_ s: String) -> JSONOut {
        out += s
        return self
    }

    @discardableResult func string(_ s: String) -> JSONOut {
        out += "\""
        for c in s.unicodeScalars {
            switch c {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{8}": out += "\\b"
            case "\u{c}": out += "\\f"
            case _ where c.value < 0x20: out += String(format: "\\u%04x", c.value)
            default: out.unicodeScalars.append(c)
            }
        }
        out += "\""
        return self
    }

    @discardableResult func ranges(_ r: [[Int]], _ u: (Int) -> Int) -> JSONOut {
        out += "[" + r.map { "[" + $0.map { String(u($0)) }.joined(separator: ", ") + "]" }.joined(separator: ", ") + "]"
        return self
    }

    /// `, "strong": ..., "sup": ..., "sub": ...`, each only when there is one.
    @discardableResult func marks(_ strong: [[Int]], _ sup: [[Int]], _ sub: [[Int]], _ u: (Int) -> Int) -> JSONOut {
        for (key, r) in [("strong", strong), ("sup", sup), ("sub", sub)] where !r.isEmpty {
            raw(", \"\(key)\": ").ranges(r, u)
        }
        return self
    }

    @discardableResult func pics(_ pics: [(pos: Int, src: String)], _ u: (Int) -> Int) -> JSONOut {
        guard !pics.isEmpty else { return self }
        raw(", \"pics\": [")
        for (k, p) in pics.enumerated() {
            if k > 0 { raw(", ") }
            raw("{\"pos\": \(u(p.pos)), \"src\": ").string(p.src).raw("}")
        }
        return raw("]")
    }
}

/// Python's text rules, so the phone splits and trims exactly as the pipeline does.
enum Py {
    typealias Text = [Unicode.Scalar]

    /// `str.isspace()`: Unicode whitespace plus the four ASCII separators Python counts as space.
    static func isSpace(_ c: Unicode.Scalar) -> Bool {
        switch c.value {
        case 0x09...0x0D, 0x1C...0x20, 0x85, 0xA0, 0x1680, 0x2000...0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
            return true
        default: return false
        }
    }

    static func isUpper(_ c: Unicode.Scalar) -> Bool { c.properties.isUppercase }
    static func isLower(_ c: Unicode.Scalar) -> Bool { c.properties.isLowercase }
    static func isDigit(_ c: Unicode.Scalar) -> Bool {
        c.properties.numericType == .decimal || c.properties.numericType == .digit
    }

    /// `s.strip()` / `s.rstrip()` with no argument.
    static func strip(_ s: String) -> String { String(String.UnicodeScalarView(stripped(Array(s.unicodeScalars)))) }
    static func rstrip(_ s: String) -> String {
        var t = Array(s.unicodeScalars)
        while let l = t.last, isSpace(l) { t.removeLast() }
        return String(String.UnicodeScalarView(t))
    }

    static func stripped(_ t: Text) -> Text {
        var a = 0, b = t.count
        while a < b, isSpace(t[a]) { a += 1 }
        while b > a, isSpace(t[b - 1]) { b -= 1 }
        return Array(t[a..<b])
    }

    /// `s.split()`: the words between runs of whitespace.
    static func split(_ s: String) -> [String] {
        var out: [String] = []
        var cur = String.UnicodeScalarView()
        for c in s.unicodeScalars {
            if isSpace(c) {
                if !cur.isEmpty { out.append(String(cur)); cur = String.UnicodeScalarView() }
            } else {
                cur.append(c)
            }
        }
        if !cur.isEmpty { out.append(String(cur)) }
        return out
    }

    /// `re.sub(r"\s+", " ", s)`.
    static func squeeze(_ t: Text) -> Text {
        var out: Text = []
        out.reserveCapacity(t.count)
        var inSpace = false
        for c in t {
            if isSpace(c) {
                if !inSpace { out.append(" ") }
                inSpace = true
            } else {
                out.append(c)
                inSpace = false
            }
        }
        return out
    }

    /// `re.sub(r"\s+", " ", s).strip()`, the way titles and notes are tidied.
    static func collapse(_ s: String) -> String { string(stripped(squeeze(Array(s.unicodeScalars)))) }

    static func string(_ t: Text) -> String { String(String.UnicodeScalarView(t)) }

    /// Scalar offsets of `text` as UTF-16 offsets: the same numbers unless the text leaves the BMP.
    static func utf16Offsets(_ text: String) -> (Int) -> Int {
        let scalars = Array(text.unicodeScalars)
        guard scalars.contains(where: { $0.value > 0xFFFF }) else { return { $0 } }
        var prefix = [0]
        prefix.reserveCapacity(scalars.count + 1)
        for c in scalars { prefix.append(prefix.last! + (c.value > 0xFFFF ? 2 : 1)) }
        return { prefix[min(max($0, 0), prefix.count - 1)] }
    }

    // MARK: extract_text.split_sentences

    private static let abbreviations: Set<String> = [
        "т", "е", "д", "г", "гг", "стр", "см", "им", "ул", "св", "проф", "др", "пр", "тыс", "млн", "млрд",
        "с", "гл", "в", "вв", "ст", "п", "ч", "н", "к", "изд", "ок", "акад", "англ", "нем", "франц", "фр", "лат",
        "кит", "греч", "санскр", "рис", "табл", "прим", "ред", "пер", "ср", "напр", "сокр", "букв", "вып",
        "e", "g", "i", "mr", "mrs", "dr", "st", "vs", "etc", "p", "pp", "ch", "vol",
    ]

    /// One letter, also an ordinary word ("Кит. Он плыл") or one that ends a sentence as often ("5 млн. Это много"):
    /// joined only when no capital of its own script follows ("от англ. Love" stays one).
    private static let weakAbbreviations: Set<String> = [
        "т", "е", "д", "г", "с", "в", "п", "ч", "н", "к", "e", "g", "i", "p",
        "им", "кит", "ок", "ст", "гл", "изд", "пер", "ред", "нем", "лат", "англ", "франц", "рис", "букв",
        "млн", "млрд", "тыс", "стр", "др", "пр",
    ]

    private static func isEnd(_ c: Unicode.Scalar) -> Bool { c == "." || c == "!" || c == "?" || c == "…" }
    private static func isCloser(_ c: Unicode.Scalar) -> Bool {
        c == "»" || c == "\"" || c == "”" || c == ")" || c == "]"
    }
    private static func isGap(_ c: Unicode.Scalar) -> Bool { c == " " || c == "\n" }
    private static func isWordLetter(_ c: Unicode.Scalar) -> Bool {
        switch c.value {
        case 0x41...0x5A, 0x61...0x7A, 0x401, 0x410...0x44F, 0x451: return true
        default: return false
        }
    }
    private static let opener: Set<Unicode.Scalar> = ["«", "\"", "“", "'", "–", "—", "-", "(", "["]

    /// `extract_text._script`: "lat", "cyr" or "" (any other character).
    private static func script(_ c: Unicode.Scalar) -> String {
        switch c.value {
        case 0x41...0x5A, 0x61...0x7A: return "lat"
        default: return isWordLetter(c) ? "cyr" : ""
        }
    }

    /// `extract_text._answer`: the capital at `k` follows "?", "!" or "…", or a dialogue dash (at the block start,
    /// after a sentence end or a colon).
    private static func answer(_ t: Text, _ k: Int) -> Bool {
        var j = k
        while j > 0, isGap(t[j - 1]) { j -= 1 }
        let dash = j > 0 && (t[j - 1] == "—" || t[j - 1] == "–")
        if dash {
            j -= 1
            while j > 0, isGap(t[j - 1]) { j -= 1 }
            if j == 0 { return true }
        }
        while j > 0, isCloser(t[j - 1]) { j -= 1 }
        guard j > 0 else { return false }
        let c = t[j - 1]
        return c == "!" || c == "?" || c == "…" || (dash && (c == "." || c == ":"))
    }

    /// `extract_text._ends_at_letter`: the capital `t[k]` before the "." at `dot` ends the sentence before the capital
    /// at `j` unless it is an initial (before another, or before a surname on the same line that does not answer and,
    /// after a word, is in the letter's script).
    private static func endsAtLetter(_ t: Text, _ k: Int, _ dot: Int, _ j: Int) -> Bool {
        let n = t.count
        if j + 1 < n, t[j + 1] == "." { return false }
        var w = k
        while w > 0, isGap(t[w - 1]) { w -= 1 }
        let sameScript = script(t[j]) == script(t[k]) || !(w > 0 && isWordLetter(t[w - 1]))
        let surname = j + 1 < n && isWordLetter(t[j + 1]) && sameScript
        return !(surname && !t[dot..<j].contains("\n") && !answer(t, k))
    }

    /// Where the run of letters that ends at `i` begins (not before `lo`).
    private static func wordStart(_ t: Text, _ i: Int, _ lo: Int) -> Int {
        var k = i
        while k > lo, isWordLetter(t[k - 1]) { k -= 1 }
        return k
    }

    private static func lower(_ c: Unicode.Scalar) -> String { String(c).lowercased() }

    /// `t[k..<i]` is the word before a "." that ends "и т. д.", "и т. п.", "и др.", "и пр." or "etc.".
    private static func finalAbbrev(_ t: Text, _ k0: Int, _ i: Int, _ lo: Int) -> Bool {
        let word = string(Array(t[k0..<i])).lowercased()
        if word == "etc" { return true }
        var k = k0
        if word == "д" || word == "п" {  // "т." before it
            var j = k
            while j > lo, isGap(t[j - 1]) { j -= 1 }
            guard j >= lo + 2, t[j - 1] == ".", lower(t[j - 2]) == "т", wordStart(t, j - 2, lo) == j - 2 else {
                return false
            }
            k = j - 2
        } else if word != "др" && word != "пр" {
            return false
        }
        var j = k
        while j > lo, isGap(t[j - 1]) { j -= 1 }
        return j < k && j > lo && lower(t[j - 1]) == "и" && wordStart(t, j - 1, lo) == j - 1
    }

    /// `[0-9]{1,3}|[IVXLC]{1,6}`: a list number such as `12` or `IV`.
    private static func isListNumber(_ t: Text) -> Bool {
        (1...3).contains(t.count) && t.allSatisfy { ("0"..."9").contains($0) }
            || (1...6).contains(t.count) && t.allSatisfy { "IVXLC".unicodeScalars.contains($0) }
    }

    /// [start, end] scalar ranges of the sentences in `t` (Russian-aware, as the pipeline splits them).
    static func splitSentences(_ t: Text) -> [[Int]] {
        let n = t.count
        var out: [[Int]] = []
        var start = 0, i = 0
        while i < n {
            guard isEnd(t[i]) else { i += 1; continue }
            var end = i
            while end < n, isEnd(t[end]) { end += 1 }
            while end < n, isCloser(t[end]) { end += 1 }
            var j = end
            while j < n, isGap(t[j]) { j += 1 }
            if end >= n {
                out.append([start, n])
                start = n
                break
            }
            let next: Unicode.Scalar? = j < n ? t[j] : nil
            let nextUpper = next.map(isUpper) ?? false
            var abbrev = false
            if t[i] == "." {
                let k = wordStart(t, i, start)
                let word = Array(t[k..<i])
                let lowered = string(word).lowercased()
                if word.count == 1, isUpper(word[0]) {
                    abbrev = !(nextUpper && endsAtLetter(t, k, i, j))  // an initial
                } else if weakAbbreviations.contains(lowered) {
                    abbrev = !nextUpper || script(next!) != script(word[0])
                } else if abbreviations.contains(lowered) {
                    abbrev = !(nextUpper && finalAbbrev(t, k, i, start))
                } else if out.isEmpty, end == i + 1, isListNumber(stripped(Array(t[start..<i]))) {
                    abbrev = true  // a list number opening the block
                }
            }
            let starts = next.map { isUpper($0) || opener.contains($0) || isDigit($0) } ?? true  // "" in '…' is True
            if j > end, !abbrev, starts {
                out.append([start, end])
                start = j
                i = j
            } else {
                i = end
            }
        }
        if start < n { out.append([start, n]) }
        return out.compactMap { r in
            var a = r[0], b = r[1]
            while a < b, isGap(t[a]) { a += 1 }
            while b > a, isGap(t[b - 1]) { b -= 1 }
            return b > a ? [a, b] : nil
        }
    }

    /// `extract_text.block_sentences`: a heading is one sentence, every other kind is split.
    static func blockSentences(_ t: Text, kind: String) -> [[Int]] {
        kind == "title" || kind == "subtitle" ? [[0, t.count]] : splitSentences(t)
    }

    // MARK: extract_text.build_offset_map

    static func offsetMap(_ old: Text, _ new: Text) -> [Int] {
        var mapping = [Int](repeating: 0, count: old.count + 1)
        var j = 0
        var lead = 0
        while lead < old.count, old[lead] == " " { lead += 1 }
        for (i, ch) in old.enumerated() {
            mapping[i] = min(j, new.count)
            if i < lead {
                j += 1
                continue
            }
            if isSpace(ch) {
                if j < new.count, new[j] == " ", j == 0 || !isSpace(new[j - 1]), mapping[i] == j { j += 1 }
                continue
            }
            while j < new.count, new[j] != ch { j += 1 }
            j += 1
        }
        mapping[old.count] = new.count
        return mapping.map { min($0, new.count) }
    }
}

/// Regular expressions as Python's `re` reads the pipeline's patterns (ICU agrees on everything used here).
struct Regex {
    let re: NSRegularExpression

    init(_ pattern: String, ignoreCase: Bool = false, dotAll: Bool = false) {
        var o: NSRegularExpression.Options = []
        if ignoreCase { o.insert(.caseInsensitive) }
        if dotAll { o.insert(.dotMatchesLineSeparators) }
        re = try! NSRegularExpression(pattern: pattern, options: o)
    }

    /// `re.match`: anchored at the start.
    func matches(_ s: String) -> Bool {
        re.firstMatch(in: s, options: .anchored, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// `re.search`: the groups of the first match (group 0 first), nil where a group took no part.
    func search(_ s: String) -> [String?]? {
        guard let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
        return (0..<m.numberOfRanges).map { Range(m.range(at: $0), in: s).map { String(s[$0]) } }
    }

    func all(_ s: String) -> [[String?]] {
        re.matches(in: s, range: NSRange(s.startIndex..., in: s)).map { m in
            (0..<m.numberOfRanges).map { Range(m.range(at: $0), in: s).map { String(s[$0]) } }
        }
    }

    func replace(_ s: String, with template: String) -> String {
        re.stringByReplacingMatches(in: s, range: NSRange(s.startIndex..., in: s), withTemplate: template)
    }
}

/// pipeline/text_end.py, the part the phone needs: the first sentence of the reference matter at the back.
enum TextEnd {
    private static let tail = Regex(
        "примечани|комментари|библиограф|литератур|указател|словар|глоссари|об автор|о художник"
            + "|над книгой работал|благодарност|оглавлени|содержани|приложени|список сокращ"
            + "|summary|notes|index|bibliograph|acknowledg", ignoreCase: true)
    private static let main = Regex("эпилог|послеслови|заключени|финал", ignoreCase: true)
    private static let letter = Regex("^[А-ЯЁA-Z][а-яёa-z]?\\.?$")

    static func of(_ book: ImportedBook) -> Int {
        let blocks = book.blocks, chapters = book.chapters
        let counts = blocks.map(\.sentences.count)
        let total = counts.reduce(0, +)
        let starts = chapters.map { min(max(0, $0.firstBlock), blocks.count) }
        let ends = Array((Array(starts.dropFirst()) + [blocks.count]).prefix(starts.count))
        let sizes = zip(starts, ends).map { a, e in a < e ? counts[a..<e].reduce(0, +) : 0 }
        let titles = chapters.map { Py.split($0.title).joined(separator: " ") }
        let levels = chapters.map(\.level)
        let letters = titles.map { letter.matches($0) }

        func letterRun(_ j: Int) -> Int {
            var a = j, b = j
            while a > 0, letters[a - 1] { a -= 1 }
            while b + 1 < letters.count, letters[b + 1] { b += 1 }
            return b - a + 1
        }
        func underTail(_ j: Int) -> Bool {
            var level = levels[j] + 1
            for k in stride(from: j, through: 0, by: -1) where levels[k] < level {
                if tail.search(titles[k]) != nil { return true }
                level = levels[k]
            }
            return false
        }

        var first = chapters.count
        for j in stride(from: chapters.count - 1, through: 0, by: -1) {
            if main.search(titles[j]) != nil { break }
            if underTail(j) || (letters[j] && letterRun(j) >= 3) || Double(sizes[j]) < 0.001 * Double(total) {
                first = j
                continue
            }
            break
        }
        guard first < chapters.count else { return total }
        let sent = counts[..<starts[first]].reduce(0, +)
        return Double(total - sent) > 0.35 * Double(total) ? total : sent
    }
}
