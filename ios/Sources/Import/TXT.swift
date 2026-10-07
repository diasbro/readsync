// pipeline/extract_txt.py on the phone, step for step, so book.json stays byte for byte what the pipeline writes:
// the encoding (a byte order mark, UTF-16 by its zero bytes, UTF-8 when 99% of the bytes are, else the Russian code
// page that reads most like Russian, else Latin-1),
// paragraphs from blank lines or from the indents and short lines of a hard-wrapped text, chapters from lines that
// stand alone and look like headings, scene breaks as gaps, asterisk footnotes as notes.

import Foundation

enum TXT {
    static func extract(_ data: Data, stem: String) -> ImportedBook {
        var paras = items(decode(data))
        let (refs, notes) = attachNotes(&paras)
        return assemble(paras, title: stem, refs: refs, notes: notes)
    }

    // MARK: encoding

    /// Bytes 0x80...0xFF of each code page as Python's codecs read them, U+FFFD where a page has nothing
    /// (tests/test_txt.py holds them to Python's), in the order a tie goes.
    static let codePages: [(name: String, high: [UInt16])] = [
        ("cp1251", [
            0x0402, 0x0403, 0x201A, 0x0453, 0x201E, 0x2026, 0x2020, 0x2021, 0x20AC, 0x2030, 0x0409, 0x2039,
            0x040A, 0x040C, 0x040B, 0x040F, 0x0452, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
            0xFFFD, 0x2122, 0x0459, 0x203A, 0x045A, 0x045C, 0x045B, 0x045F, 0x00A0, 0x040E, 0x045E, 0x0408,
            0x00A4, 0x0490, 0x00A6, 0x00A7, 0x0401, 0x00A9, 0x0404, 0x00AB, 0x00AC, 0x00AD, 0x00AE, 0x0407,
            0x00B0, 0x00B1, 0x0406, 0x0456, 0x0491, 0x00B5, 0x00B6, 0x00B7, 0x0451, 0x2116, 0x0454, 0x00BB,
            0x0458, 0x0405, 0x0455, 0x0457, 0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417,
            0x0418, 0x0419, 0x041A, 0x041B, 0x041C, 0x041D, 0x041E, 0x041F, 0x0420, 0x0421, 0x0422, 0x0423,
            0x0424, 0x0425, 0x0426, 0x0427, 0x0428, 0x0429, 0x042A, 0x042B, 0x042C, 0x042D, 0x042E, 0x042F,
            0x0430, 0x0431, 0x0432, 0x0433, 0x0434, 0x0435, 0x0436, 0x0437, 0x0438, 0x0439, 0x043A, 0x043B,
            0x043C, 0x043D, 0x043E, 0x043F, 0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447,
            0x0448, 0x0449, 0x044A, 0x044B, 0x044C, 0x044D, 0x044E, 0x044F,
        ]),
        ("koi8_r", [
            0x2500, 0x2502, 0x250C, 0x2510, 0x2514, 0x2518, 0x251C, 0x2524, 0x252C, 0x2534, 0x253C, 0x2580,
            0x2584, 0x2588, 0x258C, 0x2590, 0x2591, 0x2592, 0x2593, 0x2320, 0x25A0, 0x2219, 0x221A, 0x2248,
            0x2264, 0x2265, 0x00A0, 0x2321, 0x00B0, 0x00B2, 0x00B7, 0x00F7, 0x2550, 0x2551, 0x2552, 0x0451,
            0x2553, 0x2554, 0x2555, 0x2556, 0x2557, 0x2558, 0x2559, 0x255A, 0x255B, 0x255C, 0x255D, 0x255E,
            0x255F, 0x2560, 0x2561, 0x0401, 0x2562, 0x2563, 0x2564, 0x2565, 0x2566, 0x2567, 0x2568, 0x2569,
            0x256A, 0x256B, 0x256C, 0x00A9, 0x044E, 0x0430, 0x0431, 0x0446, 0x0434, 0x0435, 0x0444, 0x0433,
            0x0445, 0x0438, 0x0439, 0x043A, 0x043B, 0x043C, 0x043D, 0x043E, 0x043F, 0x044F, 0x0440, 0x0441,
            0x0442, 0x0443, 0x0436, 0x0432, 0x044C, 0x044B, 0x0437, 0x0448, 0x044D, 0x0449, 0x0447, 0x044A,
            0x042E, 0x0410, 0x0411, 0x0426, 0x0414, 0x0415, 0x0424, 0x0413, 0x0425, 0x0418, 0x0419, 0x041A,
            0x041B, 0x041C, 0x041D, 0x041E, 0x041F, 0x042F, 0x0420, 0x0421, 0x0422, 0x0423, 0x0416, 0x0412,
            0x042C, 0x042B, 0x0417, 0x0428, 0x042D, 0x0429, 0x0427, 0x042A,
        ]),
        ("cp866", [
            0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417, 0x0418, 0x0419, 0x041A, 0x041B,
            0x041C, 0x041D, 0x041E, 0x041F, 0x0420, 0x0421, 0x0422, 0x0423, 0x0424, 0x0425, 0x0426, 0x0427,
            0x0428, 0x0429, 0x042A, 0x042B, 0x042C, 0x042D, 0x042E, 0x042F, 0x0430, 0x0431, 0x0432, 0x0433,
            0x0434, 0x0435, 0x0436, 0x0437, 0x0438, 0x0439, 0x043A, 0x043B, 0x043C, 0x043D, 0x043E, 0x043F,
            0x2591, 0x2592, 0x2593, 0x2502, 0x2524, 0x2561, 0x2562, 0x2556, 0x2555, 0x2563, 0x2551, 0x2557,
            0x255D, 0x255C, 0x255B, 0x2510, 0x2514, 0x2534, 0x252C, 0x251C, 0x2500, 0x253C, 0x255E, 0x255F,
            0x255A, 0x2554, 0x2569, 0x2566, 0x2560, 0x2550, 0x256C, 0x2567, 0x2568, 0x2564, 0x2565, 0x2559,
            0x2558, 0x2552, 0x2553, 0x256B, 0x256A, 0x2518, 0x250C, 0x2588, 0x2584, 0x258C, 0x2590, 0x2580,
            0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447, 0x0448, 0x0449, 0x044A, 0x044B,
            0x044C, 0x044D, 0x044E, 0x044F, 0x0401, 0x0451, 0x0404, 0x0454, 0x0407, 0x0457, 0x040E, 0x045E,
            0x00B0, 0x2219, 0x00B7, 0x221A, 0x2116, 0x00A4, 0x25A0, 0x00A0,
        ]),
        ("mac_cyrillic", [
            0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417, 0x0418, 0x0419, 0x041A, 0x041B,
            0x041C, 0x041D, 0x041E, 0x041F, 0x0420, 0x0421, 0x0422, 0x0423, 0x0424, 0x0425, 0x0426, 0x0427,
            0x0428, 0x0429, 0x042A, 0x042B, 0x042C, 0x042D, 0x042E, 0x042F, 0x2020, 0x00B0, 0x0490, 0x00A3,
            0x00A7, 0x2022, 0x00B6, 0x0406, 0x00AE, 0x00A9, 0x2122, 0x0402, 0x0452, 0x2260, 0x0403, 0x0453,
            0x221E, 0x00B1, 0x2264, 0x2265, 0x0456, 0x00B5, 0x0491, 0x0408, 0x0404, 0x0454, 0x0407, 0x0457,
            0x0409, 0x0459, 0x040A, 0x045A, 0x0458, 0x0405, 0x00AC, 0x221A, 0x0192, 0x2248, 0x2206, 0x00AB,
            0x00BB, 0x2026, 0x00A0, 0x040B, 0x045B, 0x040C, 0x045C, 0x0455, 0x2013, 0x2014, 0x201C, 0x201D,
            0x2018, 0x2019, 0x00F7, 0x201E, 0x040E, 0x045E, 0x040F, 0x045F, 0x2116, 0x0401, 0x0451, 0x044F,
            0x0430, 0x0431, 0x0432, 0x0433, 0x0434, 0x0435, 0x0436, 0x0437, 0x0438, 0x0439, 0x043A, 0x043B,
            0x043C, 0x043D, 0x043E, 0x043F, 0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447,
            0x0448, 0x0449, 0x044A, 0x044B, 0x044C, 0x044D, 0x044E, 0x20AC,
        ]),
    ]
    private static let sample = 1 << 18
    private static let freq: [UInt32: Int] = [
        0x43E: 110, 0x435: 85, 0x430: 80, 0x438: 74, 0x43D: 67, 0x442: 63, 0x441: 55, 0x440: 47, 0x432: 45,
        0x43B: 44, 0x43A: 35, 0x43C: 32, 0x434: 30, 0x43F: 28, 0x443: 26, 0x44F: 20, 0x44B: 19, 0x44C: 17,
        0x433: 17, 0x437: 17, 0x431: 16, 0x447: 14, 0x439: 12, 0x445: 10, 0x436: 9, 0x448: 7, 0x44E: 6,
        0x446: 5, 0x449: 4, 0x44D: 3, 0x444: 3, 0x44A: 1, 0x451: 1,
    ]  // о е а и н т с р в л к м д п у я ы ь г з б ч й х ж ш ю ц щ э ф ъ ё, per thousand
    private static let neutral = Set("«»—–…„“”‘’№©°·•§\u{A0}\u{AD}".unicodeScalars)
    private static let penalty = 30
    private static let utf8Share = 99  // per cent of the bytes that must be UTF-8 for a text with a few broken ones

    static func isRuLower(_ c: Unicode.Scalar) -> Bool { (0x430...0x44F).contains(c.value) || c.value == 0x451 }
    static func isRuUpper(_ c: Unicode.Scalar) -> Bool { (0x410...0x42F).contains(c.value) || c.value == 0x401 }
    static func isLatin(_ c: Unicode.Scalar) -> Bool { (0x61...0x7A).contains(c.value) || (0x41...0x5A).contains(c.value) }
    private static func isLatinLower(_ c: Unicode.Scalar) -> Bool { (0x61...0x7A).contains(c.value) }
    private static func isLetter(_ c: Unicode.Scalar) -> Bool { isRuLower(c) || isRuUpper(c) || isLatin(c) }

    /// `russian_score`: frequent letters of Russian words count most; signs, letters inside Latin words and a change
    /// of case inside a word count against; ASCII and a lone letter say nothing.
    static func russianScore(_ s: Py.Text) -> Int {
        var score = 0
        let n = s.count
        let space: Unicode.Scalar = " "
        for i in 0..<n {
            let c = s[i]
            if c.value < 0x80 { continue }
            if !isRuLower(c), !isRuUpper(c) {
                if !neutral.contains(c) { score -= penalty }
                continue
            }
            let a = i > 0 ? s[i - 1] : space
            let b = i + 1 < n ? s[i + 1] : space
            if isLatin(a) || isLatin(b) || (isRuUpper(c) && isRuLower(a))
                || (isRuLower(c) && isRuUpper(a) && i > 1 && isRuUpper(s[i - 2]))
            {
                score -= penalty
            } else if isRuLower(a) || isRuUpper(a) || isRuLower(b) || isRuUpper(b) {
                let lower = c.value == 0x401 ? 0x451 : isRuUpper(c) ? c.value + 0x20 : c.value
                score += freq[lower] ?? 0
            }
        }
        return score
    }

    private static func singleByte(_ b: ArraySlice<UInt8>, _ high: [UInt16]?) -> Py.Text {
        b.map { x in
            guard x >= 0x80, let high else { return Unicode.Scalar(x) }
            return Unicode.Scalar(high[Int(x) - 0x80]) ?? "\u{FFFD}"
        }
    }

    private static func utf16(_ b: ArraySlice<UInt8>, little: Bool) -> String {
        var units: [UInt16] = []
        units.reserveCapacity(b.count / 2)
        var i = b.startIndex
        while i + 1 < b.endIndex {
            let x = UInt16(b[i]), y = UInt16(b[i + 1])
            units.append(little ? x | y << 8 : x << 8 | y)
            i += 2
        }
        var s = String(decoding: units, as: UTF16.self)
        if b.count % 2 == 1 { s.unicodeScalars.append("\u{FFFD}") }
        return s
    }

    private static func utf32(_ b: ArraySlice<UInt8>, little: Bool) -> String {
        var out = String.UnicodeScalarView()
        var i = b.startIndex
        while i + 3 < b.endIndex {
            let w = (0..<4).map { UInt32(b[i + $0]) }
            let v = little ? w[0] | w[1] << 8 | w[2] << 16 | w[3] << 24 : w[0] << 24 | w[1] << 16 | w[2] << 8 | w[3]
            out.append(Unicode.Scalar(v) ?? "\u{FFFD}")
            i += 4
        }
        if b.count % 4 != 0 { out.append("\u{FFFD}") }
        return String(out)
    }

    /// `bomless_utf16`: UTF-16 without a byte order mark, by the zero bytes of its spaces, digits and Latin letters
    /// all on one side of each byte pair; true when little-endian.
    static func bomlessUTF16(_ b: ArraySlice<UInt8>) -> Bool? {
        let n = min(b.count, sample) / 2 * 2
        var even = 0, odd = 0
        var i = b.startIndex
        while i < b.startIndex + n {
            if b[i] == 0 { even += 1 }
            if b[i + 1] == 0 { odd += 1 }
            i += 2
        }
        if odd > 0, odd * 20 >= n / 2, even * 10 <= odd { return true }
        if even > 0, even * 20 >= n / 2, odd * 10 <= even { return false }
        return nil
    }

    /// One UTF-8 sequence at `i`: where it ends, and whether it is one; a broken one ends after its longest valid
    /// start (Python's maximal subpart, one U+FFFD each).
    private static func utf8Step(_ b: [UInt8], _ i: Int) -> (end: Int, ok: Bool) {
        let x = b[i]
        if x < 0x80 { return (i + 1, true) }
        var need = 0
        var lo: UInt8 = 0x80, hi: UInt8 = 0xBF
        switch x {
        case 0xC2...0xDF: need = 1
        case 0xE0: (need, lo) = (2, 0xA0)
        case 0xE1...0xEC, 0xEE...0xEF: need = 2
        case 0xED: (need, hi) = (2, 0x9F)
        case 0xF0: (need, lo) = (3, 0x90)
        case 0xF1...0xF3: need = 3
        case 0xF4: (need, hi) = (3, 0x8F)
        default: return (i + 1, false)
        }
        var j = i + 1
        for k in 0..<need {
            guard j < b.count, b[j] >= (k == 0 ? lo : 0x80), b[j] <= (k == 0 ? hi : 0xBF) else { return (j, false) }
            j += 1
        }
        return (j, true)
    }

    /// `raw.decode("utf-8", "replace")` and how many bytes were not UTF-8.
    static func utf8(_ b: [UInt8]) -> (text: String, bad: Int) {
        var bad = 0
        var i = 0
        while i < b.count {
            let (j, ok) = utf8Step(b, i)
            if !ok { bad += j - i }
            i = j
        }
        if bad == 0 { return (String(decoding: b, as: UTF8.self), 0) }
        var out = String.UnicodeScalarView()
        i = 0
        while i < b.count {
            let (j, ok) = utf8Step(b, i)
            if ok {
                var v = UInt32(b[i]) & [0x7F, 0x1F, 0x0F, 0x07][j - i - 1]
                for k in (i + 1)..<j { v = v << 6 | UInt32(b[k] & 0x3F) }
                out.append(Unicode.Scalar(v)!)
            } else {
                out.append("\u{FFFD}")
            }
            i = j
        }
        return (String(out), bad)
    }

    /// `decode`: a byte order mark, else UTF-16 by its zero bytes, else UTF-8 when at least 99% of the bytes are
    /// (the rest replaced), else the code page that reads most like Russian, else Latin-1.
    static func decode(_ data: Data) -> String {
        let b = [UInt8](data)
        func starts(_ bom: [UInt8]) -> Bool { b.starts(with: bom) }
        if starts([0xFF, 0xFE, 0, 0]) { return utf32(b[4...], little: true) }
        if starts([0, 0, 0xFE, 0xFF]) { return utf32(b[4...], little: false) }
        if starts([0xEF, 0xBB, 0xBF]) { return String(decoding: b[3...], as: UTF8.self) }
        if starts([0xFF, 0xFE]) { return utf16(b[2...], little: true) }
        if starts([0xFE, 0xFF]) { return utf16(b[2...], little: false) }
        if let little = bomlessUTF16(b[...]) { return utf16(b[...], little: little) }
        let (text, bad) = utf8(b)
        if (b.count - bad) * 100 >= utf8Share * b.count { return text }
        let head = b.prefix(sample)
        var best: [UInt16]? = nil
        var top = 0
        for page in codePages {
            let score = russianScore(singleByte(head, page.high))
            if score > top { (best, top) = (page.high, score) }
        }
        return Py.string(singleByte(b[...], best))
    }

    // MARK: lines

    private static let ordinal =
        "перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|[а-яё]+надцат|двадцат|тридцат|сороков|последн"
        + "|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|(?:thir|four|fif|six|seven|eigh|nine)teen"
        + "|twenty|thirty|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last"
    /// A word ends here: the same characters for ICU as for Python's re, unlike `\b`.
    private static let end = "(?![0-9A-Za-zА-яЁё])"
    private static let keyword = Regex(
        "^(?:(?:глава|часть|книга|том|chapter|part|book) (?:[0-9]{1,3}|(?-i:[IVXLCDM]{1,7})|(?:" + ordinal
            + ")[а-яёa-z]*)" + end + "|(?:пролог|эпилог|предисловие|послесловие)" + end + ").{0,60}$", ignoreCase: true)
    private static let strong = Regex(
        "^(?:глава|часть|книга|chapter|part|book) (?:[0-9]{1,3}|(?-i:[IVXLCDM]{1,7})|(?:" + ordinal
            + ")[а-яёa-z]*)" + end + ".{0,60}$", ignoreCase: true)
    private static let prologue = Regex("^(?:пролог|эпилог|предисловие|послесловие)" + end, ignoreCase: true)
    private static let sentence = Regex("[.!?…][»”\"')\\]]*$")
    private static let number = Regex("^(?:[0-9]{1,3}|[IVXLCDM]{1,7})\\.?$")
    private static let caps = Regex("^[А-ЯЁA-Z][А-ЯЁA-Z0-9 .,:;!?«»\"'()\\-–—…]{1,79}$")
    private static let capsWord = Regex("[А-ЯЁA-Z]{2}")
    private static let bigPart = Regex("^(?:часть|книга|том|part|book)" + end, ignoreCase: true)
    private static let sceneBreak = Regex("^(?:(?:\\* ?){3,}|(?:[-–—] ?){3,})$")
    private static let endsSentence = Regex("[.!?…:][»”\"')\\]]*$")
    private static let broken = Regex("([\\p{L}\\p{Nl}\\p{No}]+(?:-[\\p{L}\\p{Nl}\\p{No}]+)*)-$")  // extract_txt.BROKEN
    private static let leading = Regex("^[\\p{L}\\p{Nl}\\p{No}]+")

    /// `heading_line`: a short line that names a chapter, never dialogue or a line running on with a comma, nor a
    /// sentence of five words or more that starts with Пролог/Эпилог/Предисловие/Послесловие.
    static func headingLine(_ s: String, caps capsOK: Bool) -> Bool {
        let words = Py.split(s).count
        if s.unicodeScalars.count > 80 || words > 8 || s.hasSuffix(",") || s.hasSuffix(";") { return false }
        if let c = s.unicodeScalars.first, c == "-" || c == "–" || c == "—" { return false }
        if keyword.matches(s), !(prologue.matches(s) && words > 4 && sentence.search(s) != nil) { return true }
        if number.matches(s) { return true }
        return capsOK && caps.matches(s) && capsWord.search(s) != nil
    }

    /// `strong_heading`: a heading that may have text right under it: a keyword and a number, or capitals ending
    /// in a letter, a digit or a full stop.
    static func strongHeading(_ s: String, caps capsOK: Bool) -> Bool {
        guard headingLine(s, caps: capsOK) else { return false }
        if strong.matches(s) { return true }
        guard capsOK, caps.matches(s), let c = s.unicodeScalars.last else { return false }
        return isLetter(c) || (0x30...0x39).contains(c.value) || c == "."
    }

    private static func indent(_ t: Py.Text) -> Int { t.prefix(while: Py.isSpace).count }

    /// `items`: the text as (kind, text) items, kind "title", "p" or "break".
    static func items(_ raw: String) -> [(kind: String, text: Py.Text)] {
        var lines: [Py.Text] = []
        var cur: Py.Text = []
        var cr = false
        for c in raw.unicodeScalars {
            if cr, c == "\n" {
                cr = false
                continue
            }
            cr = c == "\r"
            if c == "\r" || c == "\n" {
                lines.append(cur)
                cur = []
            } else {
                cur.append(c)
            }
        }
        lines.append(cur)
        if lines[0].first == "\u{FEFF}" { lines[0].removeFirst() }
        lines = lines.map { l in
            var t = l
            while let x = t.last, Py.isSpace(x) { t.removeLast() }
            return t
        }
        let full = lines.filter { !$0.isEmpty }
        if full.isEmpty { return [] }
        let lens = full.map(\.count).sorted()
        let width = lens[lens.count * 9 / 10]
        let long = lens.filter { $0 > 90 }.count
        let wide = lens.filter { 4 * $0 >= 3 * width }.count
        let wrapped = long * 10 < full.count * 3 && wide * 3 >= full.count
        var indents: [Int: Int] = [:]
        for l in full { indents[indent(l), default: 0] += 1 }
        let base = indents.min { ($0.value, -$0.key) > ($1.value, -$1.key) }!.key
        let first = lines.firstIndex { !$0.isEmpty }!
        var gaps = 0
        if first + 1 < lines.count - 1 {
            for i in (first + 1)..<(lines.count - 1) where lines[i].isEmpty && !lines[i + 1].isEmpty { gaps += 1 }
        }
        let spaced = gaps * 20 >= full.count
        var lower = 0, upper = 0
        for l in lines {
            for c in l {
                if isRuLower(c) || isLatinLower(c) { lower += 1 } else if isRuUpper(c) || isLatin(c) { upper += 1 }
            }
        }
        let capsOK = lower > upper
        let vocab = PDF.vocabulary([lines.map(Py.string)])  // how the text spells words away from line ends

        func short(_ l: Py.Text) -> Bool { 4 * l.count < 3 * width }
        func collapsed(_ l: Py.Text) -> String { Py.string(Py.stripped(Py.squeeze(l))) }
        func apart(_ j: Int) -> Bool {
            j < 0 || j >= lines.count || lines[j].isEmpty || sceneBreak.matches(collapsed(lines[j]))
        }

        var out: [(kind: String, text: Py.Text)] = []
        var buf: [Py.Text] = []
        var titleAt = -2
        func flush() {
            guard !buf.isEmpty else { return }
            out.append(("p", Array(buf.joined(separator: [" "]))))
            buf = []
        }
        for (i, ln) in lines.enumerated() {
            let s = collapsed(ln)
            if s.isEmpty {
                flush()
                continue
            }
            if sceneBreak.matches(s) {
                flush()
                out.append(("break", []))
                continue
            }
            let prev = i > 0 ? lines[i - 1] : []
            let starts =
                buf.isEmpty || !wrapped || indent(ln) > base
                || (short(prev) && endsSentence.search(Py.string(prev)) != nil)
            if spaced, !starts, apart(i + 1), short(ln), endsSentence.search(Py.string(prev)) != nil, strong.matches(s),
                headingLine(s, caps: capsOK)
            {  // a chapter heading as the last line of a paragraph, right after the end of a sentence
                flush()
                out.append(("title", Array(s.unicodeScalars)))
                titleAt = i
                continue
            }
            if starts, headingLine(s, caps: capsOK) {
                let next = i + 1 < lines.count ? lines[i + 1] : []
                let before = apart(i - 1) || titleAt == i - 1 || !spaced
                let after =
                    apart(i + 1) || headingLine(collapsed(next), caps: capsOK)
                    || (!spaced && (!wrapped || indent(next) > base || short(ln)))
                    || (spaced && strongHeading(s, caps: capsOK))  // the first line of a paragraph, text under it
                if before, after {
                    flush()
                    out.append(("title", Array(s.unicodeScalars)))
                    titleAt = i
                    continue
                }
            }
            if starts { flush() }
            let t = Array(s.unicodeScalars)
            if let last = buf.last, last.count > 1, last.last == "-", isLetter(last[last.count - 2]),
                isRuLower(t[0]) || isLatinLower(t[0])
            {  // a word hyphenated at the line end
                let left = broken.search(Py.string(last))?[1] ?? "", right = leading.search(Py.string(t))?[0] ?? ""
                let hyphen: Py.Text = PDF.keepsHyphen(left, right, vocab) ? ["-"] : []
                buf[buf.count - 1] = Array(last.dropLast()) + hyphen + t
            } else {
                buf.append(t)
            }
        }
        flush()
        return out
    }

    // MARK: notes

    private static let closers = Set(".,:;!?…»\"”')]".unicodeScalars)
    private static let openers = Set("«„“\"'([".unicodeScalars)
    private static let starWindow = 3  // a note's body comes within this many paragraphs after its marker's

    /// Python's `str.isalnum()` for one character.
    private static func isAlnum(_ c: Unicode.Scalar) -> Bool {
        switch c.properties.generalCategory {
        case .uppercaseLetter, .lowercaseLetter, .titlecaseLetter, .modifierLetter, .otherLetter: return true
        default: return c.properties.numericType != nil
        }
    }

    /// `star_runs`: (start, count, paired) of each run of asterisks; paired when it opens or closes a `*word*`.
    static func starRuns(_ t: Py.Text) -> [(start: Int, count: Int, paired: Bool)] {
        var runs: [(start: Int, count: Int, paired: Bool)] = []
        var i = 0
        let n = t.count
        while i < n {
            if t[i] != "*" {
                i += 1
                continue
            }
            var j = i
            while j < n, t[j] == "*" { j += 1 }
            runs.append((i, j - i, false))
            i = j
        }
        for r in runs.indices.dropLast() {
            let s = runs[r].start, k = runs[r].count, b = runs[r + 1]
            let opens = (s == 0 || Py.isSpace(t[s - 1]) || openers.contains(t[s - 1])) && s + k < n && !Py.isSpace(t[s + k])
            let e = b.start + b.count
            let closes = !Py.isSpace(t[b.start - 1]) && (e == n || Py.isSpace(t[e]) || closers.contains(t[e]))
            if !runs[r].paired, opens, closes, b.count == k {
                runs[r].paired = true
                runs[r + 1].paired = true
            }
        }
        return runs
    }

    /// `note_marks`: (start, count) of each asterisk footnote marker glued to the end of a word, not of a pair.
    static func noteMarks(_ t: Py.Text) -> [(start: Int, count: Int)] {
        let n = t.count
        return starRuns(t).compactMap { r in
            let j = r.start + r.count
            guard r.count <= 3, !r.paired, r.start > 0, isAlnum(t[r.start - 1]) || closers.contains(t[r.start - 1]),
                j == n || t[j] == " " || closers.contains(t[j])
            else { return nil }
            return (r.start, r.count)
        }
    }

    /// `note_body`: a paragraph that starts with one to three asterisks and then text, not a `*word*` emphasis.
    static func noteBody(_ t: Py.Text) -> (count: Int, text: Py.Text)? {
        let k = t.prefix(while: { $0 == "*" }).count
        var rest = Array(t[k...])
        if rest.first == " " { rest.removeFirst() }
        guard (1...3).contains(k), let c = rest.first, c != "*", c != " ", starRuns(t).first?.paired == false else {
            return nil
        }
        return (k, rest)
    }

    /// `attach_notes`: a body takes the latest marker before it with as many asterisks that has none yet, in one
    /// of the `starWindow` paragraphs of text before it and in the same chapter.
    static func attachNotes(_ paras: inout [(kind: String, text: Py.Text)]) -> (
        refs: [Int: [(pos: Int, id: String, m: String)]], notes: [(id: String, text: String)]
    ) {
        var pending: [(para: Int, start: Int, count: Int, seen: Int)] = []
        var found: [Int: [(start: Int, count: Int, id: String)]] = [:]
        var notes: [(id: String, text: String)] = []
        var seen = 0  // paragraphs of text so far
        for idx in paras.indices {
            if paras[idx].kind == "title" { pending.removeAll() }
            guard paras[idx].kind == "p" else { continue }
            if let body = noteBody(paras[idx].text),
                let j = pending.lastIndex(where: { $0.count == body.count && $0.seen >= seen - starWindow })
            {
                let owner = pending.remove(at: j)
                let id = "n\(notes.count + 1)"
                notes.append((id, Py.string(body.text)))
                found[owner.para, default: []].append((owner.start, owner.count, id))
                paras[idx].kind = "note"
                continue
            }
            pending += noteMarks(paras[idx].text).map { (idx, $0.start, $0.count, seen) }
            seen += 1
        }
        var refs: [Int: [(pos: Int, id: String, m: String)]] = [:]
        for (idx, marks) in found {
            let text = paras[idx].text
            var new: Py.Text = []
            var out: [(pos: Int, id: String, m: String)] = []
            var cut = 0, last = 0
            for mark in marks.sorted(by: { $0.start < $1.start }) {
                new += text[last..<mark.start]
                out.append((mark.start - cut, mark.id, String(repeating: "*", count: mark.count)))
                cut += mark.count
                last = mark.start + mark.count
            }
            paras[idx].text = new + text[last...]
            refs[idx] = out
        }
        return (refs, notes)
    }

    // MARK: book

    /// `assemble`: titles start chapters, a scene break is a gap before the next block, notes are left out.
    static func assemble(
        _ paras: [(kind: String, text: Py.Text)], title: String, author: String = "",
        refs: [Int: [(pos: Int, id: String, m: String)]] = [:], notes: [(id: String, text: String)] = []
    ) -> ImportedBook {
        var book = ImportedBook(title: title, author: author)
        book.chapters = [.init(id: "s0", title: "", level: 1, firstBlock: 0)]
        var gap = 0
        for (idx, p) in paras.enumerated() {
            if p.kind == "break" {
                gap += 1
                continue
            }
            if p.kind == "note" { continue }
            let text = Py.string(p.text)
            if p.kind == "title" {
                gap = 0  // a break before a chapter is not the chapter's
                book.chapters.append(
                    .init(
                        id: "s\(book.chapters.count)", title: text, level: bigPart.matches(text) ? 1 : 2,
                        firstBlock: book.blocks.count))
            }
            var block = ImportedBook.Block(
                id: "b\(book.blocks.count)", kind: p.kind, chapter: book.chapters.count - 1, text: text,
                sentences: Py.blockSentences(p.text, kind: p.kind))
            for (k, r) in (refs[idx] ?? []).enumerated() {
                block.notes.append((r.pos, r.id))
                block.noteMarks[k] = r.m
            }
            block.st = Style.block(p.kind, .init(), .init(), gap: gap)
            gap = 0
            book.blocks.append(block)
        }
        for n in notes { book.setNote(n.id, n.text) }
        if book.chapters[0].title.isEmpty, book.chapters.count == 1 || book.chapters[1].firstBlock == 0 {
            if book.chapters.count > 1 { book.chapters.removeFirst() }
            for i in book.blocks.indices { book.blocks[i].chapter = max(0, book.blocks[i].chapter - 1) }
        }
        return book
    }
}
