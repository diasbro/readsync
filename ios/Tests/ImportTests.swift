// The phone's importer against the pipeline: tests/import_vectors holds made-up books in every format and the
// book.json the Python pipeline makes of each (tests/make_import_vectors.py). Offsets are UTF-16 on the phone,
// code points in Python; the vectors stay in the BMP, where the two are the same numbers.

import CryptoKit
import UIKit
import XCTest

@testable import Readsync

final class ImportTests: XCTestCase {
    private var vectors: URL {
        Bundle(for: Self.self).url(forResource: "import_vectors", withExtension: nil)!
    }

    private func cases() throws -> [[String: Any]] {
        let data = try Data(contentsOf: vectors.appendingPathComponent("expected.json"))
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
    }

    private func source(_ name: String) throws -> Data { try Data(contentsOf: vectors.appendingPathComponent(name)) }

    private func tempDir() -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("import-test-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func sha(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

    func testBooksAreWhatThePipelineMakes() throws {
        var checked = 0
        for c in try cases() {
            guard let file = c["file"] as? String, let expected = c["book"] as? String else { continue }
            let book = try BookImport.read(try source(file), name: file)
            let want = try source(expected)
            let mine = book.json()
            try compare(mine, want, file)
            XCTAssertEqual(String(decoding: mine, as: UTF8.self), String(decoding: want, as: UTF8.self), "\(file): bytes")
            var images: [String: String] = [:]
            for (name, data) in book.images { images[name] = sha(data) }
            XCTAssertEqual(images, c["images"] as? [String: String], "\(file): images")
            XCTAssertEqual(TextEnd.of(book), c["text_end"] as? Int, "\(file): text_end")
            XCTAssertEqual(BookImport.fragmentNote(book), c["fragment_note"] as? String, "\(file): fragment_note")
            XCTAssertEqual(book.words, c["words"] as? Int, "\(file): words")
            checked += 1
        }
        XCTAssertGreaterThanOrEqual(checked, 10)
    }

    /// Field by field first, so a difference says where it is.
    private func compare(_ mine: Data, _ want: Data, _ file: String) throws {
        let a = try XCTUnwrap(JSONSerialization.jsonObject(with: mine) as? [String: Any], file)
        let b = try XCTUnwrap(JSONSerialization.jsonObject(with: want) as? [String: Any], file)
        for key in ["title", "author", "notes", "chapters"] {
            XCTAssertEqual(a[key] as? NSObject, b[key] as? NSObject, "\(file): \(key)")
        }
        let ba = a["blocks"] as? [[String: Any]] ?? [], bb = b["blocks"] as? [[String: Any]] ?? []
        XCTAssertEqual(ba.count, bb.count, "\(file): blocks")
        for (i, (x, y)) in zip(ba, bb).enumerated() where (x as NSDictionary) != (y as NSDictionary) {
            XCTFail("\(file): block \(i)\n mine \(x)\n want \(y)")
            break
        }
    }

    func testSentencesAreSplitAsThePipelineSplitsThem() throws {
        let data = try source("sentences.json")
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 30)
        for c in cases {
            let text = try XCTUnwrap(c["text"] as? String)
            XCTAssertEqual(Py.splitSentences(Array(text.unicodeScalars)), c["sentences"] as? [[Int]], text)
        }
        let two = Array("Первое. Второе.".unicodeScalars)
        for kind in ["p", "cite", "epigraph", "verse", "annotation", "author"] {
            XCTAssertEqual(Py.blockSentences(two, kind: kind), [[0, 7], [8, 15]], kind)
        }
        for kind in ["title", "subtitle"] { XCTAssertEqual(Py.blockSentences(two, kind: kind), [[0, 15]], kind) }
    }

    /// Every optional key of the model, written as extract_text.dump_book writes it.
    func testTheModelIsWrittenAsThePipelineWritesIt() throws {
        var book = ImportedBook(title: "Модель", author: "Никто")
        book.chapters = [.init(id: "s0", title: "Глава", level: 1, firstBlock: 0)]
        let text = "Вода 4³ и H₂O, сказал Кит. Твёрдо."
        book.blocks = [
            .init(
                images: [.init(src: "images/a.png", size: (3, 4))], id: "b0", kind: "p", chapter: 0, text: text,
                em: [[0, 4]], strong: [[27, 33]], sup: [[6, 7]], sub: [[11, 12]],
                notes: [(25, "n1"), (34, "n2")], noteMarks: [0: "721"],
                sentences: Py.splitSentences(Array(text.unicodeScalars)),
                pics: [(5, "images/b.png")], st: BlockStyle(a: "c")),
            .init(
                images: [.init(src: "images/c.png", after: true)], id: "b1", kind: "table", chapter: 0,
                text: "Имя\tЧисло\nЛёд\t4", sentences: [[0, 9], [10, 15]], audio: false,
                rows: [[[0, 3, 1], [4, 9, 1]], [[10, 13], [14, 15]]]),
        ]
        book.setNote("n1", "Простое примечание.")
        book.setNote(
            "n2",
            ImportedBook.Note(
                text: "Стих один\nстрока два\n\nВторой абзац 2⁵.", em: [[0, 4]], strong: [[5, 9]], sup: [[36, 37]],
                sub: [[10, 16]], pics: [(22, "images/d.png")], kinds: [(0, 20, "verse")]))
        book.setNote("n3", ImportedBook.Note(text: "Один абзац.", em: [[0, 4]]))
        XCTAssertEqual(String(decoding: book.json(), as: UTF8.self), String(decoding: try source("model.book.json"), as: UTF8.self))
    }

    func testOtherCodePagesReadAsTheirCP1251Twin() throws {
        var checked = 0
        for c in try cases() {
            guard let file = c["file"] as? String, let twin = c["same_as"] as? String else { continue }
            let a = try BookImport.read(try source(file), name: file)
            let b = try BookImport.read(try source(twin), name: twin)
            XCTAssertEqual(a.blocks.map(\.text), b.blocks.map(\.text), file)
            XCTAssertEqual(a.chapters.map(\.title), b.chapters.map(\.title), file)
            checked += 1
        }
        XCTAssertGreaterThanOrEqual(checked, 2)
    }

    /// tests/import_vectors/txt.json: small plain texts, a rule each, and the book.json the pipeline makes of them.
    func testPlainTextsAreWhatThePipelineMakes() throws {
        let data = try source("txt.json")
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 15)
        for c in cases {
            let name = try XCTUnwrap(c["name"] as? String)
            let bytes = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(c["data"] as? String)), name)
            let mine = TXT.extract(bytes, stem: name).json()
            let want = Data(try XCTUnwrap(c["book"] as? String).utf8)
            try compare(mine, want, name)
            XCTAssertEqual(String(decoding: mine, as: UTF8.self), String(decoding: want, as: UTF8.self), name)
        }
    }

    /// tests/import_vectors/epub.json: small epubs, a rule each, and the book.json, pictures or error the pipeline
    /// makes of them. Read on a 512 KB stack, the import's own, which deep nesting must not overflow.
    func testSmallEpubsAreWhatThePipelineMakes() throws {
        let data = try source("epub.json")
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 10)
        for c in cases {
            let name = try XCTUnwrap(c["name"] as? String)
            let bytes = try XCTUnwrap(Data(base64Encoded: try XCTUnwrap(c["data"] as? String)), name)
            var got: Result<ImportedBook, Error>?
            let done = DispatchSemaphore(value: 0)
            let thread = Thread {
                got = Result { try EPUB.extract(bytes, stem: name) }
                done.signal()
            }
            thread.stackSize = 512 * 1024
            thread.qualityOfService = .userInteractive  // the test waits on it
            thread.start()
            done.wait()
            let result = try XCTUnwrap(got, name)
            if let error = c["error"] as? String {
                XCTAssertThrowsError(try result.get(), name) { XCTAssertEqual(($0 as? BookImport.Failure)?.message, error, name) }
                continue
            }
            let book = try result.get()
            let mine = book.json(), want = Data(try XCTUnwrap(c["book"] as? String).utf8)
            try compare(mine, want, name)
            XCTAssertEqual(String(decoding: mine, as: UTF8.self), String(decoding: want, as: UTF8.self), name)
            var images: [String: String] = [:]
            for (n, d) in book.images { images[n] = sha(d) }
            XCTAssertEqual(images, c["images"] as? [String: String], "\(name): images")
            XCTAssertEqual(book.images.count, images.count, "\(name): each picture once")
        }
    }

    /// Every byte order mark, whatever text follows it.
    func testByteOrderMarksDecide() throws {
        let text = "Глава 1\n\nЁжик 😀 съел яблоко."
        let marked: [(String.Encoding, [UInt8])] = [
            (.utf8, [0xEF, 0xBB, 0xBF]), (.utf16LittleEndian, [0xFF, 0xFE]), (.utf16BigEndian, [0xFE, 0xFF]),
            (.utf32LittleEndian, [0xFF, 0xFE, 0, 0]), (.utf32BigEndian, [0, 0, 0xFE, 0xFF]),
        ]
        for (enc, bom) in marked {
            let data = Data(bom) + (try XCTUnwrap(text.data(using: enc)))
            XCTAssertEqual(TXT.decode(data), text, "\(enc)")
        }
        XCTAssertEqual(TXT.decode(Data([0xFF, 0xFE, 0x3F, 0x04, 0x41])), "п\u{FFFD}")
    }

    func testStubsAndArchivesWithoutABookAreRefusedAndLeaveNothing() throws {
        let library = tempDir()
        var checked = 0
        for c in try cases() {
            guard let file = c["file"] as? String, let error = c["error"] as? String else { continue }
            let url = tempDir().appendingPathComponent(file)
            try source(file).write(to: url)
            XCTAssertThrowsError(try BookImport.make(from: url, into: library), file) { e in
                let message = (e as? BookImport.Failure)?.message ?? "\(e)"
                XCTAssertTrue(message.contains(error), "\(file): \(message)")
            }
            checked += 1
        }
        XCTAssertEqual(checked, 3)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: library.path), [])
    }

    func testMakeWritesAWholeBookFolder() throws {
        let library = tempDir()
        let fm = FileManager.default
        for c in try cases() {
            guard let file = c["file"] as? String, c["book"] != nil else { continue }
            let url = tempDir().appendingPathComponent(file)
            try source(file).write(to: url)
            let result = try BookImport.make(from: url, into: library)
            let dir = library.appendingPathComponent(result.slug)
            XCTAssertEqual(result.title, c["title"] as? String, file)
            XCTAssertEqual(result.author, c["author"] as? String, file)
            XCTAssertTrue(result.slug.hasPrefix(BookImport.slugify(result.title) + "-"), result.slug)
            XCTAssertNotNil(result.slug.range(of: "^[a-z0-9-]+-[0-9a-f]{4}$", options: .regularExpression), result.slug)

            let tomlText = try String(contentsOf: dir.appendingPathComponent("book.toml"), encoding: .utf8)
            let toml = Toml.parse(tomlText)
            let json = try Data(contentsOf: dir.appendingPathComponent("book.json"))
            XCTAssertEqual(toml["slug"], result.slug)
            XCTAssertEqual(toml["title"], c["title"] as? String)
            XCTAssertEqual(toml["language"], "ru")
            XCTAssertEqual(toml["text_source"], file)
            XCTAssertEqual(toml["files"], "book.json:\(json.count)")
            XCTAssertEqual(toml["text_end"], "\(c["text_end"] as? Int ?? -1)")
            XCTAssertEqual(toml["fragment_note"], (c["fragment_note"] as? String).flatMap { $0.isEmpty ? nil : $0 })
            XCTAssertNotNil(toml["id"]?.range(of: "^[0-9a-f]{32}$", options: .regularExpression))
            XCTAssertNotNil(toml["edition"]?.range(of: "^[0-9a-f]{32}$", options: .regularExpression))
            XCTAssertTrue(result.slug.hasSuffix("-" + (toml["id"] ?? "").prefix(4)), result.slug)
            // the shelf sees it as a whole text book
            let book = Book(slug: result.slug, toml: tomlText)
            XCTAssertEqual(book.files, ["book.json": Int64(json.count)])
            XCTAssertFalse(book.hasAudio)
            XCTAssertEqual(try source(c["book"] as! String).count, json.count, file)
            let images = (try? fm.contentsOfDirectory(atPath: dir.appendingPathComponent("images").path)) ?? []
            XCTAssertEqual(Set(images), Set((c["images"] as? [String: String] ?? [:]).keys), file)
        }
        // nothing left in staging from these
        let staging = (try? fm.contentsOfDirectory(atPath: BookImport.stagingRoot.path)) ?? []
        XCTAssertEqual(staging, [])
    }

    func testTheSameFileTwiceMakesTwoBooks() throws {
        let library = tempDir()
        let url = tempDir().appendingPathComponent("povest.fb2")
        try source("povest.fb2").write(to: url)
        let a = try BookImport.make(from: url, into: library)
        let b = try BookImport.make(from: url, into: library)
        XCTAssertNotEqual(a.slug, b.slug)
        XCTAssertTrue(a.slug.hasPrefix("povest-o-mayake-"), a.slug)
    }

    func testOffsetsAreUTF16OutsideTheBMP() throws {
        let line = "Смайлик 😀 стоит тут. Второе предложение с курсивом."
        var text = (0..<120).map { _ in "Обычное предложение для объёма текста." }.joined(separator: " ")
        text = line + "\n\n" + text
        let book = TXT.extract(Data(text.utf8), stem: "emoji")
        let json = try JSONSerialization.jsonObject(with: book.json()) as! [String: Any]
        let first = (json["blocks"] as! [[String: Any]])[0]
        let sentences = first["sentences"] as! [[Int]]
        let ns = (first["text"] as! String) as NSString  // UTF-16, as JavaScript's slice counts
        let parts = sentences.map { ns.substring(with: NSRange(location: $0[0], length: $0[1] - $0[0])) }
        XCTAssertEqual(parts, ["Смайлик 😀 стоит тут.", "Второе предложение с курсивом."])
    }

    /// 3000 nested emphasis and citations read on a stack as small as the import's detached task gets, as
    /// tests/test_fb2.py reads them: the text is kept, one paragraph past `FB2.depth` levels.
    func testDeepNestingReadsOnASmallStack() {
        let em = "<p>" + String(repeating: "<emphasis>", count: 3000) + "Текст."
            + String(repeating: "</emphasis>", count: 3000)
        let cite = String(repeating: "<cite>", count: 3000) + "<p>Цитата.</p><p>Вторая.</p>"
            + String(repeating: "</cite>", count: 3000)
        let xml = "<?xml version=\"1.0\" encoding=\"utf-8\"?><FictionBook><description><title-info><book-title>T"
            + "</book-title></title-info></description><body><section><title><p>Глава</p></title>\(em) хвост</p>\(cite)"
            + "<p>После.</p></section></body></FictionBook>"
        var blocks: [(String, String)]? = nil
        let done = expectation(description: "read")
        let thread = Thread {
            blocks = (try? FB2.extract(Data(xml.utf8), stem: "deep"))?.blocks.map { ($0.kind, $0.text) }
            done.fulfill()
        }
        thread.stackSize = 512 * 1024
        thread.start()
        wait(for: [done], timeout: 60)
        XCTAssertEqual(blocks?.map(\.0), ["title", "p", "cite", "p"])
        XCTAssertEqual(blocks?.map(\.1), ["Глава", "Текст. хвост", "Цитата.Вторая.", "После."])
    }

    func testSlugLikeTheLibrary() {
        XCTAssertEqual(BookImport.slugify("Мастер и Маргарита"), "master-i-margarita")
        XCTAssertEqual(BookImport.slugify("Щука, ёж и Шарль"), "schuka-ezh-i-sharl")
        XCTAssertEqual(BookImport.slugify("!!!"), "book")
        XCTAssertEqual(BookImport.title(fromFileName: "Tolstoy_Voyna.fb2.zip"), "Tolstoy Voyna")
        // tests/test_library.py: NFC first, each code point, the cut keeps a dash it lands on
        XCTAssertEqual(BookImport.slugify("Чаи\u{306}ка"), "chayka")
        XCTAssertEqual(BookImport.slugify("е\u{301}ль"), "e-l")
        XCTAssertEqual(BookImport.slugify(String(repeating: "a", count: 47) + " b"), String(repeating: "a", count: 47) + "-")
    }

    /// manifest.toml_str and clean_title: control characters never reach book.toml raw.
    func testTomlValuesAreEscapedAsTheManifestEscapesThem() {
        XCTAssertEqual(BookImport.escape("a\u{1}b\"c\\d\u{7f}\n"), "a\\u0001b\\\"c\\\\d\\u007f\\u000a")
        XCTAssertEqual(BookImport.cleanTitle(" Война\u{1}и\n мир\u{7f} "), "Война и мир")
    }

    /// add_book.fragment_note's `\w` is Python's: letters, numbers and _, no combining marks.
    func testFragmentPhraseAsPythonFindsIt() {
        func note(_ text: String) -> String {
            var book = ImportedBook(title: "T", author: "")
            book.blocks = [.init(id: "b0", kind: "p", chapter: 0, text: text, sentences: [])]
            return BookImport.fragmentNote(book)
        }
        XCTAssertEqual(note("Конец ознакомительного фрагмента. Купите книгу."), "Конец ознакомительного фрагмента")
        XCTAssertEqual(note("Это ознакомительный фрагмент книги."), "ознакомительный фрагмент")
        XCTAssertEqual(note("Это ознакомительно\u{301}го фрагмента часть."), "")
    }

    /// add_book.sniff lets a text in UTF-16 or UTF-32 through, with or without a byte order mark; other zero bytes
    /// are a binary file.
    func testWideTextsAreTextAndBinariesAreNot() throws {
        let text = "Глава 1\n\nТекст и ещё текст, и ещё."
        let be32 = Data([0, 0, 0xFE, 0xFF]) + (try XCTUnwrap(text.data(using: .utf32BigEndian)))
        XCTAssertEqual(try BookImport.sniff(be32, hint: "a.txt"), .txt)
        XCTAssertEqual(try BookImport.sniff(try XCTUnwrap(text.data(using: .utf16LittleEndian)), hint: "a.txt"), .txt)
        XCTAssertEqual(try BookImport.sniff(try XCTUnwrap(text.data(using: .utf16BigEndian)), hint: "a.txt"), .txt)
        XCTAssertEqual(TXT.decode(try XCTUnwrap(text.data(using: .utf16LittleEndian))), text)
        XCTAssertThrowsError(try BookImport.sniff(Data([0, 1, 2, 3, 0, 0, 7]), hint: "a.txt"))
    }

    /// extract_style.rules: braces and semicolons inside a quoted string are its text, comments go.
    func testStylesheetStringsAndComments() {
        let a = Style.rules("a.x::after { content: \"}\"; } p.c { text-align: center; } p.i { text-indent: 2em }")
        XCTAssertEqual(a.map(\.0), ["a.x::after", "p.c", "p.i"])
        XCTAssertEqual(a.map(\.1), [" content: \"}\"; ", " text-align: center; ", " text-indent: 2em "])
        let b = Style.rules("@font-face { src: url(\"a}.ttf\"); } /* p { */ p.d{margin:1em} r{content:'/* x */'}")
        XCTAssertEqual(b.map(\.0), ["p.d", "r"])
        XCTAssertEqual(b.map(\.1), ["margin:1em", "content:'/* x */'"])
    }

    /// tests/import_vectors/pdf.json: pages of lines as a PDF's text layer gives them, and the book.json
    /// extract_pdf.book_from_lines makes of them: page numbers, chapter numerals, contents, notes and markers.
    func testPDFLinesAreWhatThePipelineMakes() throws {
        let data = try source("pdf.json")
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 2)
        for c in cases {
            let name = try XCTUnwrap(c["name"] as? String)
            let pages = try XCTUnwrap(c["pages"] as? [[[Any]]], name).map { page in
                page.map { l in
                    PDF.Line(
                        text: l[0] as! String, x0: (l[1] as! NSNumber).doubleValue, x1: (l[2] as! NSNumber).doubleValue,
                        y: (l[3] as! NSNumber).doubleValue, size: (l[4] as! NSNumber).doubleValue,
                        bold: (l[5] as! NSNumber).boolValue)
                }
            }
            let mine = PDF.book(pages, title: name, author: "").json()
            let want = Data(try XCTUnwrap(c["book"] as? String).utf8)
            try compare(mine, want, name)
            XCTAssertEqual(String(decoding: mine, as: UTF8.self), String(decoding: want, as: UTF8.self), name)
        }
    }

    func testZipChecksItsCRCAndRefusesZip64() throws {
        var data = try source("sbornik.fb2.zip")
        let zip = try Zip(data)
        let entry = try XCTUnwrap(zip.entries.first)
        XCTAssertEqual(entry.method, 8)
        XCTAssertNotEqual(entry.flags & 8, 0)  // written as a stream: sizes after the data
        XCTAssertNoThrow(try zip.read(entry))
        // one byte of the deflated data changed
        let local = { (i: Int) in Int(data[entry.offset + i]) | Int(data[entry.offset + i + 1]) << 8 }
        let start = entry.offset + 30 + local(26) + local(28) + 10
        data[start] ^= 0x55
        XCTAssertThrowsError(try Zip(data).read(entry))
        // an end record that points to zip64
        var z64 = try source("prochee.zip")
        let eocd = z64.count - 22
        z64[eocd + 10] = 0xFF
        z64[eocd + 11] = 0xFF
        XCTAssertThrowsError(try Zip(z64)) { XCTAssertEqual($0 as? Zip.Problem, .zip64) }
    }

    func testAScanIsRefused() throws {
        let pdf = UIGraphicsPDFRenderer(bounds: CGRect(x: 0, y: 0, width: 200, height: 200)).pdfData { ctx in
            for _ in 0..<3 {
                ctx.beginPage()
                UIColor.gray.setFill()
                UIRectFill(CGRect(x: 20, y: 20, width: 100, height: 100))
            }
        }
        XCTAssertThrowsError(try BookImport.read(pdf, name: "scan.pdf")) {
            XCTAssertEqual(($0 as? BookImport.Failure)?.message, "В PDF нет текста — похоже на скан")
        }
    }

    /// pipeline/extract_pdf.py join and finish (tests/test_pdf.py): a word broken for the line is joined, a
    /// hyphenated word keeps its hyphen, Unicode hyphens become plain.
    func testPDFLineEndsAsThePipelineJoinsThem() {
        let none: [String: Int] = [:]
        XCTAssertEqual(PDF.join("означает подчи-", "нённый, или", none), "означает подчинённый, или")
        XCTAssertEqual(PDF.join("подчи\u{2010}", "нённый", none), "подчинённый")
        XCTAssertEqual(PDF.join("подчи\u{00AD}", "нённый", none), "подчинённый")
        XCTAssertEqual(PDF.join("и вот -", "дальше", none), "и вот - дальше")
        XCTAssertEqual(PDF.join("чем-", "то материальным", none), "чем-то материальным")
        XCTAssertEqual(PDF.join("кое-", "как", none), "кое-как")
        XCTAssertEqual(PDF.join("Нью-", "Йорк", none), "Нью-Йорк")
        XCTAssertEqual(PDF.join("по-", "мощь", none), "помощь")
        let v = PDF.vocabulary([["Он сказал это по-русски, и все поняли."]])
        XCTAssertEqual(PDF.join("сказал по-", "русски", v), "сказал по-русски")
        // letters as Python's [^\W\d_] has them: numbers that are not digits too (², Ⅳ)
        XCTAssertEqual(PDF.vocabulary([["слово² и Ⅳ-й том"]]), ["слово²": 1, "и": 1, "ⅳ-й": 1, "том": 1])
        XCTAssertEqual(PDF.finish("нео\u{2010}буддизм и чем\u{2011}то,  со\u{00AD}всем"), "нео-буддизм и чем-то, совсем")
    }

    func testOtherFormatsAreRefused() {
        XCTAssertThrowsError(try BookImport.read(Data([0, 1, 2, 3, 0, 0, 7]), name: "book.mobi")) {
            XCTAssertEqual(($0 as? BookImport.Failure)?.message, BookImport.unsupported)
        }
        XCTAssertThrowsError(try BookImport.read(Data("<html><body><p>Текст</p></body></html>".utf8), name: "x.html")) {
            XCTAssertEqual(($0 as? BookImport.Failure)?.message, BookImport.unsupported)
        }
        XCTAssertThrowsError(try BookImport.read(Data("<FictionBook><body>".utf8), name: "broken.fb2")) {
            XCTAssertEqual(($0 as? BookImport.Failure)?.message, BookImport.corrupt)
        }
    }

    func testOffsetsPastTheBMPAreMarked() throws {
        let plain = String(decoding: try BookImport.read(Data(("Глава\n\n" + String(repeating: "Слово текста. ", count: 300)).utf8), name: "a.txt").json(), as: UTF8.self)
        XCTAssertFalse(plain.contains("\"offsets\""))
        let emoji = String(decoding: try BookImport.read(Data(("Глава\n\nТекст 😀. " + String(repeating: "Слово текста. ", count: 300)).utf8), name: "b.txt").json(), as: UTF8.self)
        XCTAssertTrue(emoji.hasSuffix(", \"offsets\": \"utf16\"}"))
    }

    /// make_import_vectors.markup_tree: the same nodes as BeautifulSoup's, neighbouring strings as one.
    private func tree(_ n: Node) -> [Any] {
        let lists: Set<String> = ["class", "rel", "rev", "accept-charset", "headers", "accesskey", "dropzone"]
        var out: [Any] = []
        for c in n.children {
            switch c {
            case .text(let t):
                if let last = out.last as? String { out[out.count - 1] = last + t } else { out.append(t) }
            case .comment(let t): out.append(["#comment", t])
            case .raw(let t):
                if let last = out.last as? [String], last.count == 2, last[0] == "#raw" {
                    out[out.count - 1] = ["#raw", last[1] + t]
                } else {
                    out.append(["#raw", t])
                }
            case .element(let e):
                let attrs = e.attrOrder.map { k in [k, lists.contains(k) ? Py.split(e.attrs[k] ?? "").joined(separator: " ") : e.attrs[k] ?? ""] }
                out.append([e.name, attrs, tree(e)])
            }
        }
        return out
    }

    func testMarkupIsReadAsBeautifulSoupReadsIt() throws {
        let data = try source("markup.json")
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 20)
        for c in cases {
            let html = try XCTUnwrap(c["html"] as? String)
            let want = try XCTUnwrap(c["tree"] as? NSArray)
            XCTAssertEqual(tree(HTMLText.parse(html)) as NSArray, want, html)
        }
    }

    /// `html.unescape`, which the OPF's values and attributes go through.
    func testEntitiesAsPythonResolvesThem() {
        XCTAssertEqual(HTMLText.unescape("&alpha; &ampx; &copy2024 &a; &notin; &notit; &fjlig; &NBSP; &amp"), "α &x; ©2024 &a; ∉ ¬it; fj &NBSP; &")
        XCTAssertEqual(HTMLText.unescape("&#1;&#11;&#x7f;&#xFFFE;&#0;&#x80;&#x81;&#xD800;&#99999999999;&#65"), "\u{FFFD}€\u{81}\u{FFFD}\u{FFFD}A")
        XCTAssertEqual(HTMLText.numericEntities("&alpha; &NotEqualTilde; &amp; &zz;"), "&#945; &#8770;&#824; &amp; &amp;zz;")
    }

    /// ElementTree keeps attributes in order and fb2's href is the first `…:href`; XMLParser hands a dictionary.
    func testTheFirstHrefInTheDocumentCounts() throws {
        let xml = "<a><b xlink:href=\"#one\" l:href=\"#two\"/><b l:href=\"#two\" xlink:href=\"#one\"/><!-- <c> --><b l:href=\"#x\"/></a>"
        let b = try XML.parse(Data(xml.utf8)).elements
        XCTAssertEqual(b.map(\.href), ["#one", "#two", "#x"])
    }

    /// A tree thousands of levels deep is built, read and let go on the import's 512 KB stack.
    func testDeepTreesNeedNoDeepStack() {
        let done = expectation(description: "deep")
        var counts: [Int] = []
        let thread = Thread {
            let xml = "<a>" + String(repeating: "<b>", count: 20000) + "x" + String(repeating: "</b>", count: 20000) + "</a>"
            if let root = try? XML.parse(Data(xml.utf8)) {
                counts.append(root.descendants().count)
                counts.append(root.allText.count)
            }
            let html = String(repeating: "<p>", count: 20000) + "x"
            let doc = HTMLText.parse(html)
            counts.append(doc.descendants().count)
            counts.append(doc.strippedText.count)
            done.fulfill()
        }
        thread.stackSize = 512 * 1024
        thread.start()
        wait(for: [done], timeout: 60)
        XCTAssertEqual(counts, [20000, 1, HTMLText.maxDepth, 1])
    }
}
