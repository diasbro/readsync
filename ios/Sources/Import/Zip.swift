// A minimal zip reader for books (fb2.zip, epub): the central directory, stored and deflated entries.
// Deflate is Apple's Compression: its COMPRESSION_ZLIB is raw DEFLATE (RFC 1951), what a zip holds.
// Zip64, encryption and other methods are refused rather than guessed at.

import Compression
import Foundation

struct Zip {
    struct Entry {
        let name: String
        let method: UInt16
        let flags: UInt16
        let crc: UInt32
        let compressedSize: Int
        let size: Int
        let offset: Int
        var isDirectory: Bool { name.hasSuffix("/") }
    }

    enum Problem: Error { case notZip, zip64, encrypted, method, corrupt, tooBig }

    static let maxEntry = 500_000_000  // one file of a book above 500 MB is not a book (extract_text.MAX_UNPACKED)

    let data: Data
    let entries: [Entry]

    static func isZip(_ data: Data) -> Bool { data.count >= 4 && data.prefix(2) == Data([0x50, 0x4B]) }

    init(_ data: Data) throws {
        let d = data.startIndex == 0 ? data : Data(data)
        self.data = d
        let n = d.count
        guard n >= 22 else { throw Problem.notZip }
        // End of Central Directory: the last signature within the comment's reach
        var eocd = -1
        var i = n - 22
        let floor = max(0, n - 22 - 0xFFFF)
        while i >= floor {
            if d[i] == 0x50, d[i + 1] == 0x4B, d[i + 2] == 0x05, d[i + 3] == 0x06 {
                eocd = i
                break
            }
            i -= 1
        }
        guard eocd >= 0 else { throw Problem.notZip }
        let count = Int(Self.u16(d, eocd + 10))
        let cdSize = Int(Self.u32(d, eocd + 12))
        let cdOffset = Int(Self.u32(d, eocd + 16))
        if count == 0xFFFF || cdSize == 0xFFFF_FFFF || cdOffset == 0xFFFF_FFFF { throw Problem.zip64 }
        guard cdOffset + cdSize <= n else { throw Problem.corrupt }
        var list: [Entry] = []
        var p = cdOffset
        for _ in 0..<count {
            guard p + 46 <= n, Self.u32(d, p) == 0x0201_4B50 else { throw Problem.corrupt }
            let flags = Self.u16(d, p + 8)
            let nameLen = Int(Self.u16(d, p + 28)), extraLen = Int(Self.u16(d, p + 30)), commentLen = Int(Self.u16(d, p + 32))
            guard p + 46 + nameLen <= n else { throw Problem.corrupt }
            let raw = d.subdata(in: (p + 46)..<(p + 46 + nameLen))
            let name =
                flags & 0x800 != 0
                ? String(decoding: raw, as: UTF8.self)
                : (String(data: raw, encoding: Self.cp437) ?? String(decoding: raw, as: UTF8.self))
            let entry = Entry(
                name: name, method: Self.u16(d, p + 10), flags: flags, crc: Self.u32(d, p + 16),
                compressedSize: Int(Self.u32(d, p + 20)), size: Int(Self.u32(d, p + 24)), offset: Int(Self.u32(d, p + 42)))
            if entry.compressedSize == 0xFFFF_FFFF || entry.size == 0xFFFF_FFFF || entry.offset == 0xFFFF_FFFF {
                throw Problem.zip64
            }
            list.append(entry)
            p += 46 + nameLen + extraLen + commentLen
        }
        entries = list
    }

    var names: [String] { entries.map(\.name) }

    /// The entry of that name; of two, the last (Python's zipfile).
    func entry(_ name: String) -> Entry? { entries.last { $0.name == name } }

    func read(_ name: String) throws -> Data {
        guard let e = entry(name) else { throw Problem.corrupt }
        return try read(e)
    }

    func read(_ e: Entry) throws -> Data {
        if e.flags & 1 != 0 { throw Problem.encrypted }
        if e.size > Self.maxEntry { throw Problem.tooBig }
        let d = data
        guard e.offset + 30 <= d.count, Self.u32(d, e.offset) == 0x0403_4B50 else { throw Problem.corrupt }
        let start = e.offset + 30 + Int(Self.u16(d, e.offset + 26)) + Int(Self.u16(d, e.offset + 28))
        guard start + e.compressedSize <= d.count else { throw Problem.corrupt }
        let packed = d.subdata(in: start..<(start + e.compressedSize))
        let out: Data
        switch e.method {
        case 0:
            out = packed
        case 8:
            out = try Self.inflate(packed, size: e.size)
        default:
            throw Problem.method
        }
        guard out.count == e.size, Self.crc32(out) == e.crc else { throw Problem.corrupt }
        return out
    }

    static func inflate(_ packed: Data, size: Int) throws -> Data {
        guard size > 0 else { return Data() }
        guard !packed.isEmpty else { throw Problem.corrupt }
        var out = Data(count: size)
        let written = out.withUnsafeMutableBytes { dst in
            packed.withUnsafeBytes { src in
                compression_decode_buffer(
                    dst.bindMemory(to: UInt8.self).baseAddress!, size,
                    src.bindMemory(to: UInt8.self).baseAddress!, packed.count, nil, COMPRESSION_ZLIB)
            }
        }
        guard written == size else { throw Problem.corrupt }
        return out
    }

    private static let crcTable: [UInt32] = (0..<256).map { n in
        var c = UInt32(n)
        for _ in 0..<8 { c = c & 1 != 0 ? 0xEDB8_8320 ^ (c >> 1) : c >> 1 }
        return c
    }

    static func crc32(_ data: Data) -> UInt32 {
        var c: UInt32 = 0xFFFF_FFFF
        data.withUnsafeBytes { buf in
            for b in buf { c = crcTable[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
        }
        return c ^ 0xFFFF_FFFF
    }

    private static let cp437 = String.Encoding(
        rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.dosLatinUS.rawValue)))

    private static func u16(_ d: Data, _ i: Int) -> UInt16 { UInt16(d[i]) | UInt16(d[i + 1]) << 8 }
    private static func u32(_ d: Data, _ i: Int) -> UInt32 {
        UInt32(d[i]) | UInt32(d[i + 1]) << 8 | UInt32(d[i + 2]) << 16 | UInt32(d[i + 3]) << 24
    }
}
