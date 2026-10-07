// pipeline/extract_pdf.py on the phone. PDFKit reads the text layer and gives its lines with their place and
// font size; from there the rules are the pipeline's: running heads, page numbers (lone numbers that count on
// with the pages) and a table of contents go, footnotes at a page's foot become notes linked at their marker (or
// where the page's text ends), headings are lines in a larger font (or
// "Глава 1" alone on a line) standing apart, paragraphs end at a wider gap or an indented line (or, in a book
// with neither, at a short line that ends a sentence), and a word broken by a hyphen at a line end is joined
// unless it is a hyphenated word. PDFKit does not tell bold faces apart, so a heading in the text's own size is
// only found by the pipeline. A scan has no text layer and is refused; nothing is recognised.

import Foundation
import PDFKit

#if canImport(UIKit)
    import UIKit
    private typealias PlatformFont = UIFont
#else
    import AppKit
    private typealias PlatformFont = NSFont
#endif

enum PDF {
    struct Line {
        var text: String
        var x0: Double  // left and right ends of the text, in points
        var x1: Double
        var y: Double  // the line's bottom, growing up the page as in PDF
        var size: Double  // the font size most of its characters have
        var bold = false
    }

    private static let pageNo = Regex(
        "^[\\[\\-–—\\s]*(?:([0-9]{1,4})|([ivxlcdm]{1,7})|стр\\.?\\s*([0-9]{1,4}))[\\]\\-–—\\s.]*$", ignoreCase: true)
    private static let roman = Regex("^m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})$")
    private static let romanValue: [Character: Int] = ["i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000]
    private static let near = [-2, -1, 1, 2]  // the pages a page number is checked against
    private static let endsSentence = Regex("[.!?…:][»”\"')\\]]*$")
    private static let digits = Regex("\\d+")
    private static let repeats = 0.5
    private static let tocLine = Regex("^\\S.*?(?:(?:\\s?\\.){4,}|(?:\\s?…){2,})\\s*[0-9]{1,4}$")
    private static let tocFront = 10  // a page of contents goes whole only among the first tenth of the pages
    static let keywordHeading = Regex(
        "^(?:Глава|ГЛАВА|Часть|ЧАСТЬ|Пролог|Эпилог|Chapter|CHAPTER|Part|PART)\\b[^.!?,;:]{0,40}$")
    private static let noteStart = Regex("^(\\*{1,4})\\s*(?=[^\\s*])|^([0-9]{1,3})(?:[.)]\\s*|\\s+)(?=\\S)")
    private static let sceneBreak = Regex("^(?:\\* ?){3,}$")
    private static let gluedAfter = Set("»”\"')]".unicodeScalars)
    private static let stops = Set(".,;:!?".unicodeScalars)
    private static let abbreviation = 3
    private static let hyphens: Set<Unicode.Scalar> = ["-", "\u{2010}", "\u{2011}", "\u{00AD}"]
    private static let particles: Set<String> = ["то", "либо", "нибудь", "таки"]
    private static let firstParts: Set<String> = ["кое", "кой"]
    /// Python's `[^\\W\\d_]`: letters and the numbers that are not digits (², ½, Ⅳ).
    private static let letter = "[\\p{L}\\p{Nl}\\p{No}]"
    private static let word = Regex(letter + "+(?:[-\\u2010\\u2011]" + letter + "+)*")
    private static let brokenWord = Regex("(" + letter + "+(?:[-\\u2010\\u2011]" + letter + "+)*)([-\\u2010\\u2011\\u00AD])$")
    private static let firstWord = Regex("^" + letter + "+")
    private static let innerHyphen = Regex("[-\\u2010\\u2011]")
    private static let mark: UInt32 = 0xF0000  // note markers in the text until the paragraphs are built: from here on

    static func extract(_ data: Data, stem: String) throws -> ImportedBook {
        guard let doc = PDFDocument(data: data) else { throw BookImport.Failure(message: BookImport.corrupt) }
        if doc.isLocked, !doc.unlock(withPassword: "") { throw BookImport.Failure(message: "PDF под паролем") }
        var pages: [[Line]] = []
        for i in 0..<doc.pageCount {
            autoreleasepool { pages.append(doc.page(at: i).map(lines) ?? []) }
        }
        if pages.joined().reduce(0, { $0 + Py.split($1.text).count }) < 10 {
            throw BookImport.Failure(message: "В PDF нет текста — похоже на скан")
        }
        let attrs = doc.documentAttributes ?? [:]
        let title = Py.strip(attrs[PDFDocumentAttribute.titleAttribute] as? String ?? "")
        let author = Py.strip(attrs[PDFDocumentAttribute.authorAttribute] as? String ?? "")
        return book(pages, title: title.isEmpty ? stem : title, author: author)
    }

    // MARK: lines from PDFKit

    /// A page's lines, top to bottom: PDFKit's line selections, those on one baseline put together.
    static func lines(_ page: PDFPage) -> [Line] {
        guard let all = page.selection(for: page.bounds(for: .mediaBox)) else { return [] }
        var segs: [(line: Line, sizes: Tally<Double>)] = []
        for sel in all.selectionsByLine() {
            let text = Py.collapse(sel.string ?? "")
            if text.isEmpty { continue }
            let b = sel.bounds(for: page)
            var sizes = Tally<Double>()
            if let a = sel.attributedString {
                a.enumerateAttribute(.font, in: NSRange(location: 0, length: a.length)) { v, r, _ in
                    guard let f = v as? PlatformFont else { return }
                    let chars = (a.string as NSString).substring(with: r).unicodeScalars.filter { !Py.isSpace($0) }.count
                    sizes.add((Double(f.pointSize) * 2).rounded(.toNearestOrEven) / 2, chars)
                }
            }
            let size = sizes.top ?? (Double(b.height) / 1.2 * 2).rounded(.toNearestOrEven) / 2
            segs.append((Line(text: text, x0: Double(b.minX), x1: Double(b.maxX), y: Double(b.minY), size: size), sizes))
        }
        segs.sort {
            ($0.line.y.rounded(.toNearestOrEven), -$0.line.x0) > ($1.line.y.rounded(.toNearestOrEven), -$1.line.x0)
        }
        var out: [(line: Line, sizes: Tally<Double>)] = []
        for s in segs {
            if var last = out.last, abs(last.line.y - s.line.y) < 0.5 * max(last.line.size, s.line.size) {
                let gap = s.line.x0 - last.line.x1 > 0.15 * max(last.line.size, s.line.size)
                last.line.text += gap ? " " + s.line.text : s.line.text
                last.line.x0 = min(last.line.x0, s.line.x0)
                last.line.x1 = max(last.line.x1, s.line.x1)
                for k in s.sizes.keys { last.sizes.add(k, s.sizes[k]) }
                last.line.size = last.sizes.top ?? last.line.size
                out[out.count - 1] = last
            } else {
                out.append(s)
            }
        }
        return out.map(\.line)
    }

    // MARK: from lines to a book (extract_pdf.book_from_lines)

    static func book(_ pages: [[Line]], title: String, author: String) -> ImportedBook {
        let clean = pages.map { page in
            page.map { ln -> Line in
                var l = ln
                l.text = Py.collapse(l.text)
                return l
            }.filter { !$0.text.isEmpty }
        }
        let lined = stripTOC(stripFurniture(clean))
        let lay = layout(lined)
        let (body, notes) = splitNotes(lined, lay)
        let paras = paragraphs(body, lay, vocabulary(body.map { $0.map(\.text) }))
        return build(paras, notes, title: title, author: author, body: lay.body)
    }

    private static func key(_ line: String) -> String { digits.replace(line.lowercased(), with: "#") }

    /// `page_number`: the number a line that is only a number states; nil for any other line and for letters
    /// that only look like a Roman number (civil, mild, dim).
    static func pageNumber(_ text: String) -> Int? {
        guard let m = pageNo.search(text) else { return nil }
        guard let r = m[2] else { return Int((m[1] ?? m[3]) ?? "") }
        let t = r.lowercased()
        guard roman.matches(t) else { return nil }
        let v = t.map { romanValue[$0]! }
        return v.indices.reduce(0) { $0 + (($1 + 1 < v.count && v[$1] < v[$1 + 1]) ? -v[$1] : v[$1]) }
    }

    /// `_folio`: the page number the line at the top or the foot of a page may be: not one in a larger font, nor
    /// one right over a heading (a chapter's numeral).
    private static func folio(_ page: [Line], top: Bool, _ body: Double) -> Int? {
        guard let ln = top ? page.first : page.last, let v = pageNumber(ln.text), ln.size < 1.15 * body else { return nil }
        if top, page.count > 1, page[1].size >= 1.15 * body || page[1].bold { return nil }
        return v
    }

    /// Page numbers and the running head or foot, the lines a page repeats from its neighbours. A lone number is a
    /// page number when it counts on with the pages around it at the same place.
    static func stripFurniture(_ pages: [[Line]]) -> [[Line]] {
        var sizes = Tally<Double>()
        for page in pages { for ln in page { sizes.add(ln.size, ln.text.unicodeScalars.count) } }
        let body = sizes.top ?? 10
        var edges = Tally<String>()
        for page in pages {
            var seen = Set<String>()
            for ln in [page.first, page.last].compactMap({ $0 }) where seen.insert(ln.text).inserted {
                if pageNumber(ln.text) == nil { edges.add(key(ln.text), 1) }
            }
        }
        let floor = max(3.0, Double(pages.count) * repeats)
        let running = Set(edges.keys.filter { Double(edges[$0]) >= floor })
        var keep = pages
        func heads() {
            for p in keep.indices {
                while let l = keep[p].first, running.contains(key(l.text)) { keep[p].removeFirst() }
                while let l = keep[p].last, running.contains(key(l.text)) { keep[p].removeLast() }
            }
        }
        heads()
        for top in [true, false] {
            let nums = keep.map { folio($0, top: top, body) }
            for (pi, v) in nums.enumerated() {
                guard let v else { continue }
                let seq = near.contains { d in
                    guard pi + d >= 0, pi + d < nums.count, let w = nums[pi + d] else { return false }
                    return w - v == d
                }
                if seq {
                    if top { keep[pi].removeFirst() } else { keep[pi].removeLast() }
                }
            }
        }
        heads()  // a running head under or over the number
        return keep
    }

    /// A table of contents goes: its entries anywhere, and a page among the first tenth of the book whose lines
    /// are mostly entries.
    static func stripTOC(_ pages: [[Line]]) -> [[Line]] {
        let front = max(1, (pages.count + tocFront - 1) / tocFront)
        return pages.enumerated().map { pi, page in
            let n = page.filter { tocLine.matches($0.text) }.count
            return pi < front && n * 2 > page.count ? [] : page.filter { !tocLine.matches($0.text) }
        }
    }

    struct Layout {
        var body: Double
        var leading: Double
        var left: [Double]
        var right: [Double]
    }

    static func layout(_ pages: [[Line]]) -> Layout {
        var sizes = Tally<Double>()
        for page in pages { for ln in page { sizes.add(ln.size, ln.text.unicodeScalars.count) } }
        let body = sizes.top ?? 10
        var gaps = Tally<Double>()
        for page in pages where page.count > 1 {
            for (a, b) in zip(page, page.dropFirst()) where a.size == body && b.size == body {
                let d = a.y - b.y
                if 0.8 * body < d, d < 2.5 * body { gaps.add((d * 2).rounded(.toNearestOrEven) / 2, 1) }
            }
        }
        let leading = gaps.top ?? 1.2 * body
        var lefts: [Double] = [], rights: [Double] = []
        for parity in 0..<2 {
            let lines = pages.enumerated().filter { $0.offset % 2 == parity }.flatMap { $0.element }.filter { $0.size == body }
            var xs = Tally<Double>()
            for ln in lines { xs.add(ln.x0.rounded(.toNearestOrEven), 1) }
            lefts.append(xs.keys.filter { Double(xs[$0]) >= 0.15 * Double(lines.count) }.min() ?? 0)
            let ends = lines.map(\.x1).sorted()
            rights.append(ends.isEmpty ? 0 : ends[Int(Double(ends.count) * 0.9)])
        }
        return Layout(
            body: body, leading: leading, left: pages.indices.map { lefts[$0 % 2] }, right: pages.indices.map { rights[$0 % 2] })
    }

    private static func isDigit(_ c: Unicode.Scalar) -> Bool { (0x30...0x39).contains(c.value) }

    /// `_glued_number`: a number at `i` stands as a note's marker: after a lower-case letter (not CO2, B12), a
    /// closing quote or bracket, or punctuation, but not after the full stop of a short abbreviation (т.1, гл.2).
    private static func gluedNumber(_ t: Py.Text, _ i: Int) -> Bool {
        let c = t[i - 1]
        if Py.isLower(c) || gluedAfter.contains(c) { return true }
        guard stops.contains(c) else { return false }
        if c == "." {
            var j = i - 1
            while j > 0, isAlpha(t[j - 1]) { j -= 1 }
            return !(1...abbreviation).contains(i - 1 - j)
        }
        return true
    }

    /// `_joined_marker`: where a footnote's marker stands in a line (scalar offsets): `*` glued to what is before
    /// it, a number glued as `gluedNumber` says and not part of a longer number.
    private static func joinedMarker(_ t: Py.Text, _ marker: Py.Text) -> (Int, Int)? {
        let star = marker.first == "*"
        var i = 0
        while i + marker.count <= t.count {
            let e = i + marker.count
            if Array(t[i..<e]) == marker {
                if star {
                    if i > 0, !Py.isSpace(t[i - 1]), t[i - 1] != "*", e == t.count || t[e] != "*" { return (i, e) }
                } else if i > 0, !isDigit(t[i - 1]), e == t.count || !isDigit(t[e]), gluedNumber(t, i) {
                    return (i, e)
                }
            }
            i += 1
        }
        return nil
    }

    /// `_sentinels`: code points to stand for note markers in the text, from `mark` on, any the book uses skipped.
    private struct Sentinels {
        var used: Set<UInt32>
        var next = PDF.mark
        mutating func take() -> Unicode.Scalar {
            while used.contains(next) || next & 0xFFFE == 0xFFFE { next += 1 }
            next += 1
            return Unicode.Scalar(next - 1)!
        }
    }

    /// Footnotes out of the pages (extract_pdf.split_notes): the pages without them and (marker, text, sentinel)
    /// per note.
    static func splitNotes(_ pages: [[Line]], _ lay: Layout) -> ([[Line]], [(String, String, Unicode.Scalar)]) {
        var notes: [(marker: String, sentinel: Unicode.Scalar, lines: [String])] = []
        var out: [[Line]] = []
        var marks = Sentinels(used: Set(pages.joined().flatMap { $0.text.unicodeScalars.map(\.value).filter { $0 >= mark } }))
        var last: Int? = nil  // the last numbered note's number
        var openNote = false
        for page in pages {
            var k = page.count
            while k > 0, page[k - 1].size <= 0.85 * lay.body { k -= 1 }
            var body = Array(page[..<k])
            let foot = Array(page[k...])
            let gap = !body.isEmpty && !foot.isEmpty && body[body.count - 1].y - foot[0].y > 1.3 * lay.leading
            if !gap || sceneBreak.matches(foot[0].text) || !(noteStart.matches(foot[0].text) || openNote) {
                out.append(page)
                openNote = false
                continue
            }
            for ln in foot {
                let ns = ln.text as NSString
                let m = noteStart.re.firstMatch(in: ln.text, options: .anchored, range: NSRange(location: 0, length: ns.length))
                var marker: String? = nil
                var numbered = false
                if let m {
                    for g in 1...2 where m.range(at: g).location != NSNotFound {
                        marker = ns.substring(with: m.range(at: g))
                        numbered = g == 2
                    }
                }
                if let mk = marker, numbered, let l = last, let v = Int(mk), v != 1, v != l + 1 {
                    marker = nil  // out of order: a line of the last note that starts with a number
                }
                guard let marker, let m else {
                    if !notes.isEmpty { notes[notes.count - 1].lines.append(ln.text) }
                    continue
                }
                if numbered { last = Int(marker) }
                let mk = Array(marker.unicodeScalars)
                var hit: (Int, (Int, Int))? = nil
                for (i, b) in body.enumerated() {
                    if let r = joinedMarker(Array(b.text.unicodeScalars), mk) {
                        hit = (i, r)
                        break
                    }
                }
                let sentinel = marks.take()
                notes.append((marker, sentinel, [ns.substring(from: m.range.location + m.range.length)]))
                // a marker the text does not show: the note is linked where the page's text ends
                let end = body[body.count - 1].text.unicodeScalars.count
                let (i, (a, e)) = hit ?? (body.count - 1, (end, end))
                let t = Array(body[i].text.unicodeScalars)
                body[i].text = Py.string(Array(t[..<a]) + [sentinel] + Array(t[e...]))
            }
            out.append(body)
            openNote = !notes.isEmpty && endsSentence.search(notes[notes.count - 1].lines.last ?? "") == nil
        }
        let vocab = vocabulary(notes.map(\.lines))
        let joined = notes.map { n -> (String, String, Unicode.Scalar) in
            var text = n.lines[0]
            for t in n.lines.dropFirst() { text = join(text, t, vocab) }
            return (n.marker, finish(text), n.sentinel)
        }
        return (out, joined)
    }

    private static func endsWithHyphen(_ s: String) -> Bool {
        Py.rstrip(s).unicodeScalars.last.map { hyphens.contains($0) } ?? false
    }

    private static func words(_ s: String) -> [String] { word.all(s).compactMap { $0[0] } }

    /// The book's words as it spells them away from line ends, lower case.
    static func vocabulary(_ pages: [[String]]) -> [String: Int] {
        var out: [String: Int] = [:]
        let lines = pages.flatMap { $0 }
        for (i, t) in lines.enumerated() {
            var found = words(t)
            if !found.isEmpty, endsWithHyphen(t) { found.removeLast() }
            if !found.isEmpty, i > 0, endsWithHyphen(lines[i - 1]) { found.removeFirst() }
            for w in found {
                let key = w.lowercased().replacingOccurrences(of: "\u{2010}", with: "-")
                    .replacingOccurrences(of: "\u{2011}", with: "-")
                out[key, default: 0] += 1
            }
        }
        return out
    }

    private static func isAlpha(_ c: Unicode.Scalar) -> Bool {
        switch c.properties.generalCategory {
        case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter: return true
        default: return false
        }
    }

    /// Two lines of one paragraph as one text (extract_pdf.join).
    static func join(_ a0: String, _ b0: String, _ vocab: [String: Int]) -> String {
        let a = Py.rstrip(a0)
        let b = String(String.UnicodeScalarView(b0.unicodeScalars.drop(while: Py.isSpace)))
        if a.isEmpty { return b }
        if b.isEmpty { return a }
        guard let first = b.unicodeScalars.first, isAlpha(first),
            let m = brokenWord.re.firstMatch(in: a, range: NSRange(a.startIndex..., in: a))
        else { return a + " " + b }
        let na = a as NSString
        let head = na.substring(to: m.range(at: 2).location)
        let hyphen = na.substring(with: m.range(at: 2))
        if hyphen == "\u{00AD}" { return head + b }
        if !Py.isLower(first) { return head + "-" + b }
        let keep = keepsHyphen(na.substring(with: m.range(at: 1)), (firstWord.search(b)?[0] ?? nil) ?? "", vocab)
        return head + (keep ? "-" : "") + b
    }

    /// `keeps_hyphen`: a hyphen between `left` and `right` at a line end is the word's own: a particle after it,
    /// кое/кой before it, or the book spells the word with it more often than without (TXT joins by it too).
    static func keepsHyphen(_ left: String, _ right: String, _ vocab: [String: Int]) -> Bool {
        let firstPart = (innerHyphen.re.split(left).last ?? left).lowercased()
        let whole = (left + right).lowercased(), hyph = (left + "-" + right).lowercased()
        return particles.contains(right.lowercased()) || firstParts.contains(firstPart)
            || vocab[hyph, default: 0] > vocab[whole, default: 0]
    }

    /// A paragraph's text as the book keeps it: hyphens are plain, soft hyphens and doubled spaces gone.
    static func finish(_ s: String) -> String {
        Py.collapse(
            s.replacingOccurrences(of: "\u{2010}", with: "-").replacingOccurrences(of: "\u{2011}", with: "-")
                .replacingOccurrences(of: "\u{00AD}", with: ""))
    }

    struct Para {
        var text: String
        var kind: String
        var size: Double
    }

    private static func isFull(_ ln: Line, _ lay: Layout, _ pi: Int) -> Bool {
        ln.x1 >= lay.right[pi] - 0.08 * (lay.right[pi] - lay.left[pi])
    }

    private static func isCentred(_ ln: Line, _ lay: Layout, _ pi: Int) -> Bool {
        let lo = lay.left[pi], hi = lay.right[pi]
        let width = max(hi - lo, 1)
        return ln.x0 - lo > 0.1 * width && abs((ln.x0 + ln.x1) / 2 - (lo + hi) / 2) < 0.1 * width
    }

    /// Lines into paragraphs and headings (extract_pdf.paragraphs).
    static func paragraphs(_ pages: [[Line]], _ lay: Layout, _ vocab: [String: Int]) -> [Para] {
        let flat = pages.enumerated().flatMap { p in p.element.map { (p.offset, $0) } }
        func indent(_ pi: Int, _ ln: Line) -> Double { ln.x0 - lay.left[pi] }
        func starts(_ k: Int) -> Bool {
            if k == 0 { return true }
            let (pi, ln) = flat[k], (ppi, prev) = flat[k - 1]
            if ppi == pi, prev.y - ln.y > 1.4 * (lay.leading / lay.body) * max(prev.size, ln.size) { return true }
            let ind = indent(pi, ln)
            guard 0.6 * ln.size < ind, ind < 4 * ln.size else { return false }
            return ind > indent(ppi, prev) + 0.6 * ln.size || (!isFull(prev, lay, ppi) && endsSentence.search(prev.text) != nil)
        }
        let marks = flat.indices.dropFirst().filter(starts).count
        let byPlace = Double(marks) >= 0.02 * Double(flat.count)
        func ends(_ k: Int) -> Bool {
            let (pi, ln) = flat[k]
            return !byPlace && !isFull(ln, lay, pi) && endsSentence.search(ln.text) != nil
        }
        let heading = flat.map { (_, ln) in
            ((ln.size >= 1.15 * lay.body || ln.bold) && ln.text.unicodeScalars.count <= 120) || keywordHeading.matches(ln.text)
        }
        let front = flat.first { $0.1.size == lay.body && !$0.1.bold && isFull($0.1, lay, $0.0) }?.0 ?? 0
        func apartBefore(_ k: Int) -> Bool {
            if k == 0 || !byPlace { return true }
            return starts(k) || heading[k - 1] || flat[k - 1].0 != flat[k].0 || ends(k - 1)
        }
        func apartAfter(_ j: Int) -> Bool {
            j >= flat.count || !byPlace || starts(j) || heading[j] || flat[j].0 != flat[j - 1].0
        }
        var out: [Para] = []
        var buf = ""
        var k = 0
        while k < flat.count {
            let (pi, ln) = flat[k]
            if heading[k], buf.isEmpty || !endsWithHyphen(buf), apartBefore(k) {
                var j = k + 1
                while j < flat.count, heading[j], flat[j].0 == pi, flat[j].1.size == ln.size, flat[j].1.bold == ln.bold,
                    flat[j - 1].1.y - flat[j].1.y < 2.0 * ln.size, !keywordHeading.matches(flat[j].1.text)
                {
                    j += 1
                }
                if apartAfter(j) {
                    if !buf.isEmpty {
                        out.append(Para(text: finish(buf), kind: "p", size: lay.body))
                        buf = ""
                    }
                    var title = ln.text
                    for (_, nxt) in flat[(k + 1)..<j] { title = join(title, nxt.text, vocab) }
                    title = finish(title)
                    let caps = title.unicodeScalars.contains(where: isAlpha) && title == title.uppercased()
                    let chapter =
                        ln.size >= 1.15 * lay.body || isCentred(ln, lay, pi) || caps || k == 0 || flat[k - 1].0 != pi
                        || keywordHeading.matches(title)
                    out.append(Para(text: title, kind: chapter && pi >= front ? "title" : "subtitle", size: ln.size))
                    k = j
                    continue
                }
            }
            if !buf.isEmpty, starts(k) || ends(k - 1), !endsWithHyphen(buf) {
                out.append(Para(text: finish(buf), kind: "p", size: lay.body))
                buf = ""
            }
            buf = buf.isEmpty ? ln.text : join(buf, ln.text, vocab)
            k += 1
        }
        if !buf.isEmpty { out.append(Para(text: finish(buf), kind: "p", size: lay.body)) }
        return out
    }

    private static func isAlnum(_ c: Unicode.Scalar) -> Bool { isAlpha(c) || c.properties.numericType != nil }

    /// Paragraphs -> the book: titles start chapters, note markers become links (extract_pdf.build).
    static func build(
        _ paras: [Para], _ notes: [(String, String, Unicode.Scalar)], title: String, author: String, body: Double
    ) -> ImportedBook {
        var book = ImportedBook(title: title, author: author)
        book.chapters = [.init(id: "s0", title: "", level: 1, firstBlock: 0)]
        let sizes = Set(paras.filter { $0.kind == "title" }.map { $0.size > 1.1 * body })
        let twoLevels = sizes.count == 2
        var sentinel: [Unicode.Scalar: Int] = [:]
        for (n, note) in notes.enumerated() { sentinel[note.2] = n }
        for p in paras {
            var text: Py.Text = []
            var links: [(pos: Int, n: Int)] = []
            for c in p.text.unicodeScalars {
                if let n = sentinel[c] {
                    links.append((text.count, n))
                    book.setNote("n\(n + 1)", notes[n].1)
                } else {
                    text.append(c)
                }
            }
            for li in links.indices {
                let i = links[li].pos
                if i > 0, i < text.count, isAlnum(text[i - 1]), isAlnum(text[i]) {
                    text.insert(" ", at: i)
                    for o in links.indices where links[o].pos > i { links[o].pos += 1 }
                }
            }
            text = Py.stripped(text)
            if text.isEmpty { continue }
            let s = Py.string(text)
            if p.kind == "title" {
                book.chapters.append(
                    .init(
                        id: "s\(book.chapters.count)", title: s, level: !twoLevels || p.size > 1.1 * body ? 1 : 2,
                        firstBlock: book.blocks.count))
            }
            var block = ImportedBook.Block(
                id: "b\(book.blocks.count)", kind: p.kind, chapter: book.chapters.count - 1, text: s,
                sentences: Py.blockSentences(text, kind: p.kind))
            for (k, l) in links.enumerated() {
                block.notes.append((min(l.pos, text.count), "n\(l.n + 1)"))
                block.noteMarks[k] = notes[l.n].0
            }
            book.blocks.append(block)
        }
        if book.chapters[0].title.isEmpty, book.chapters.count == 1 || book.chapters[1].firstBlock == 0 {
            if book.chapters.count > 1 { book.chapters.removeFirst() }
            for i in book.blocks.indices { book.blocks[i].chapter = max(0, book.blocks[i].chapter - 1) }
        }
        return book
    }
}

/// Python's Counter for most_common(1): counts kept in the order keys first came, the first of equals wins.
private struct Tally<K: Hashable> {
    private(set) var keys: [K] = []
    private var counts: [K: Int] = [:]

    mutating func add(_ k: K, _ n: Int) {
        if counts[k] == nil { keys.append(k) }
        counts[k, default: 0] += n
    }

    subscript(_ k: K) -> Int { counts[k] ?? 0 }

    var top: K? {
        var best: K? = nil
        for k in keys where best == nil || counts[k]! > counts[best!]! { best = k }
        return best
    }
}

extension NSRegularExpression {
    /// `re.split`: the pieces between matches.
    fileprivate func split(_ s: String) -> [String] {
        let ns = s as NSString
        var out: [String] = []
        var at = 0
        for m in matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            out.append(ns.substring(with: NSRange(location: at, length: m.range.location - at)))
            at = m.range.location + m.range.length
        }
        out.append(ns.substring(from: at))
        return out
    }
}
