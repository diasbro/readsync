// pipeline/extract_style.py on the phone: a block's own look as the book gives it, book.json's optional `st`.
// Alignment, first-line indent and left margin from an epub's CSS (class and element selectors only) and
// inline styles, blank lines before a block from empty paragraphs and fb2's <empty-line/>. The same rules,
// step for step, so book.json stays byte for byte what the pipeline writes.

import Foundation

/// `st`: lengths in tenths of an em, as the pipeline keeps them until it writes them.
struct BlockStyle {
    var a: String? = nil
    var i: Int? = nil
    var m: Int? = nil
    var g: Int? = nil
}

enum Style {
    private static let align = ["left": "l", "start": "l", "right": "r", "end": "r", "center": "c", "justify": "j"]
    private static let kindAlign = ["title": "c", "subtitle": "c", "author": "r", "verse": "l"]
    private static let headings: Set<String> = ["title", "subtitle"]
    private static let indentMax = 30, indentIgnore = 100, marginMax = 100, marginIgnore = 300, gapMax = 3
    private static let length = Regex("^([0-9]*\\.?[0-9]+)(em|rem|px|pt|%)?$")
    private static let selector = Regex("^([A-Za-z][A-Za-z0-9]*)?((?:\\.[A-Za-z0-9_-]+)*)$")

    /// The declarations that matter: text-align, text-indent, margin-left, padding-left.
    struct Decls {
        var a: String? = nil
        var i: Int? = nil
        var ml: Int? = nil
        var pl: Int? = nil

        var isEmpty: Bool { a == nil && i == nil && ml == nil && pl == nil }

        /// `dict.update`: what the other one sets wins.
        mutating func update(_ o: Decls) {
            if let v = o.a { a = v }
            if let v = o.i { i = v }
            if let v = o.ml { ml = v }
            if let v = o.pl { pl = v }
        }
    }

    /// `tenths`: a CSS length in tenths of an em (16px to the em, % of a line about 30em long).
    static func tenths(_ value: String) -> Int? {
        guard let m = length.search(value), let n = m[1], let v = Double(n.hasPrefix(".") ? "0" + n : n) else {
            return nil
        }
        guard let unit = m[2] else { return v == 0 ? 0 : nil }
        let t: Double
        switch unit {
        case "em", "rem": t = v * 10
        case "px": t = v * 10 / 16
        case "pt": t = v * 10 / 12
        default: t = v * 3
        }
        return Int((min(t, 1e6) + 0.5).rounded(.down))
    }

    /// `str.partition(":")`, by code point as Python counts.
    private static func partition(_ s: String) -> (String, String)? {
        let u = s.unicodeScalars
        guard let c = u.firstIndex(of: ":") else { return nil }
        return (String(u[..<c]), String(u[u.index(after: c)...]))
    }

    static func declarations(_ text: String) -> Decls {
        var out = Decls()
        for decl in text.components(separatedBy: ";") {
            guard let (rawProp, rawValue) = partition(decl) else { continue }
            let prop = Py.strip(rawProp).lowercased()
            var value = Py.strip(rawValue.lowercased().replacingOccurrences(of: "!important", with: ""))
            if prop == "text-align" {
                if let a = align[value] { out.a = a }
                continue
            }
            guard ["text-indent", "margin-left", "padding-left", "margin", "padding"].contains(prop) else { continue }
            if prop == "margin" || prop == "padding" {
                let parts = Py.split(value)
                guard (1...4).contains(parts.count) else { continue }
                value = parts[[0, 1, 1, 3][parts.count - 1]]
            }
            guard let t = tenths(value) else { continue }
            if prop == "text-indent" {
                out.i = t
            } else if prop.hasPrefix("margin") {
                out.ml = t
            } else {
                out.pl = t
            }
        }
        return out
    }

    /// `rules`: (selector list, declarations) of the top-level rules, at-rules skipped; comments go, a quoted
    /// string is kept whole (its braces and semicolons are its text).
    static func rules(_ css: String) -> [(String, String)] {
        let t = Array(css.unicodeScalars.drop { $0 == "\u{FEFF}" })
        var out: [(String, String)] = []
        var depth = 0
        var prelude = String.UnicodeScalarView(), body = String.UnicodeScalarView()
        var sel = ""
        var nested = false  // the rule holds rules of its own
        var i = 0
        let n = t.count
        while i < n {
            let ch = t[i]
            i += 1
            if ch == "/", i < n, t[i] == "*" {
                var end = i + 1
                while end + 1 < n, !(t[end] == "*" && t[end + 1] == "/") { end += 1 }
                i = end + 1 < n ? end + 2 : n
                continue
            }
            if ch == "\"" || ch == "'" {
                var j = i
                while j < n, t[j] != ch, t[j] != "\n" { j += t[j] == "\\" ? 2 : 1 }
                j = j < n && t[j] == ch ? min(j + 1, n) : min(j, n)
                if depth == 0 { prelude.append(contentsOf: t[(i - 1)..<j]) } else { body.append(contentsOf: t[(i - 1)..<j]) }
                i = j
                continue
            }
            if depth == 0 {
                if ch == "{" {
                    depth = 1
                    sel = Py.strip(String(prelude))
                    prelude = String.UnicodeScalarView()
                    body = String.UnicodeScalarView()
                    nested = false
                } else if ch == ";" || ch == "}" {
                    prelude = String.UnicodeScalarView()
                } else {
                    prelude.append(ch)
                }
            } else if ch == "{" {
                depth += 1
                nested = true
                body.append(ch)
            } else if ch == "}" {
                depth -= 1
                if depth == 0 {
                    if !sel.hasPrefix("@"), !nested { out.append((sel, String(body))) }
                } else {
                    body.append(ch)
                }
            } else {
                body.append(ch)
            }
        }
        return out
    }

    /// `Sheet`: a document's rules in order; `own` is what applies to one element.
    final class Sheet {
        private struct Rule {
            let spec: Int
            let order: Int
            let elem: String?
            let classes: Set<String>
            let decls: Decls
        }

        private var rules: [Rule] = []
        private var cache: [String: Decls] = [:]

        init(_ sheets: [String]) {
            for css in sheets {
                for (selList, body) in Style.rules(css) {
                    let decls = Style.declarations(body)
                    if decls.isEmpty { continue }
                    for sel in selList.components(separatedBy: ",") {
                        guard let m = Style.selector.search(Py.strip(sel)) else { continue }
                        let elem = m[1].flatMap { $0.isEmpty ? nil : $0.lowercased() }
                        let cls = m[2] ?? ""
                        if elem == nil, cls.isEmpty { continue }
                        let classes = Set(cls.split(separator: ".", omittingEmptySubsequences: false).dropFirst().map(String.init))
                        rules.append(
                            Rule(spec: classes.count * 10 + (elem == nil ? 0 : 1), order: rules.count, elem: elem,
                                 classes: classes, decls: decls))
                    }
                }
            }
        }

        func own(_ name: String, _ classes: [String], _ inline: String?) -> Decls {
            let key = name + "\u{0}" + classes.joined(separator: " ")
            var out: Decls
            if let hit = cache[key] {
                out = hit
            } else {
                let have = Set(classes)
                out = Decls()
                let matched = rules.filter { ($0.elem == nil || $0.elem == name) && $0.classes.isSubset(of: have) }
                    .sorted { ($0.spec, $0.order) < ($1.spec, $1.order) }
                for r in matched { out.update(r.decls) }
                cache[key] = out
            }
            if let s = inline, !s.isEmpty { out.update(Style.declarations(s)) }
            return out
        }
    }

    /// `inherit`: text-align and text-indent pass on to the elements inside, margins do not.
    static func inherit(_ ctx: Decls, _ own: Decls) -> Decls {
        Decls(a: own.a ?? ctx.a, i: own.i ?? ctx.i)
    }

    /// `block_style`: `st` for a block of this kind, nil when the book says nothing its kind does not.
    static func block(_ kind: String, _ own: Decls, _ ctx: Decls, gap: Int = 0) -> BlockStyle? {
        let src = headings.contains(kind) ? own : Decls(a: own.a ?? ctx.a, i: own.i ?? ctx.i)
        var st = BlockStyle()
        if let a = src.a, !a.isEmpty, a != kindAlign[kind] { st.a = a }
        if let i = src.i, i <= indentIgnore, i > 0 || kind == "p" { st.i = min(i, indentMax) }
        let m = (own.ml ?? 0) + (own.pl ?? 0)
        if m > 0, m <= marginIgnore { st.m = min(m, marginMax) }
        if gap > 0 { st.g = min(gap, gapMax) }
        return st.a == nil && st.i == nil && st.m == nil && st.g == nil ? nil : st
    }
}
