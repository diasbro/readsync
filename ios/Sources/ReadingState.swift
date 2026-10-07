// Reading state, the same way the Mac keeps it (state.py): this phone writes only
// books/<slug>/state/<its id>.json in the shared library, reads every device's file and merges them.
// tests/state_vectors.json is the contract both sides follow.

import Foundation
import Security

/// A book's editions.json (state.py `editions_of`): the edition its maps lead to and, for each older edition, the
/// new sentence index of each of its sentences. The maps count only in the edition they lead to: an older copy of
/// the text, or a newer one whose stale map is not deleted yet, does not use them.
struct Editions {
    var edition = ""
    var maps: [String: [Int]] = [:]

    init(edition: String = "", maps: [String: [Int]] = [:]) {
        self.edition = edition
        self.maps = maps
    }

    /// From the file's JSON object; anything without an edition and maps is no map. An entry that is not a list
    /// of whole numbers is left out, as on the Mac.
    init(json: Any?) {
        guard let obj = json as? [String: Any], let edition = obj["edition"] as? String,
            let maps = obj["maps"] as? [String: Any]
        else { return }
        self.edition = edition
        for (ed, v) in maps {
            guard let list = v as? [Any] else { continue }
            let ints = list.compactMap { x -> Int? in
                guard let n = x as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
                    "qlis".contains(String(cString: n.objCType))
                else { return nil }
                return n.intValue
            }
            if ints.count == list.count { self.maps[ed] = ints }
        }
    }

    /// The maps that count in `edition` (state.py `maps_for`).
    func maps(for edition: String) -> [String: [Int]] {
        !edition.isEmpty && edition == self.edition ? maps : [:]
    }
}

/// A book's place in the reader's life: `shelf` as written, or derived when none is.
enum BookStatus: String, Sendable {
    case none, reading, paused, done
}

enum ReadingState {
    static let lww = ["pos", "sent", "mode", "opened", "shelf", "finished"]
    private static let queue = DispatchQueue(label: "readsync.state")  // one writer for this phone's file
    nonisolated(unsafe) private static var lastGood: [URL: [String: Any]] = [:]
    private static let cacheLock = NSLock()  // apart from `queue`: reads happen inside a write

    private static func cached(_ url: URL) -> [String: Any]? { cacheLock.withLock { lastGood[url] } }
    private static func remember(_ url: URL, _ st: [String: Any]) { cacheLock.withLock { lastGood[url] = st } }

    /// The merged view of several devices' state. Pure, like state.merge on the Mac. `editions`: the book's
    /// editions.json, which carries sentences of older editions over when it leads to `edition` (see `sentence`).
    /// A merged `sent` comes with `sentEdition`: `edition`, the text it counts in.
    static func merge(_ files: [[String: Any]], edition: String, editions: Editions = Editions()) -> [String: Any] {
        let maps = editions.maps(for: edition)
        var out: [String: Any] = [:]
        for st in files {
            for key in lww {
                guard let value = key == "sent" ? sentence(st, edition: edition, maps: maps) : st[key] else {
                    continue
                }
                let at = num(st[key + "At"])
                if out[key] == nil || at > num(out[key + "At"]) {
                    out[key] = value
                    out[key + "At"] = num(st[key + "At"])
                    if key == "sent" { out["sentPct"] = st["sentPct"] ?? 0 }
                }
            }
        }
        var days: [String: [String: Double]] = [:]
        for st in files {
            let fileDays = ((st["stats"] as? [String: Any])?["days"] as? [String: Any]) ?? [:]
            for (day, v) in fileDays {
                let entry = v as? [String: Any] ?? [:]
                var cur = days[day] ?? ["sec": 0, "words": 0]
                cur["sec"]! += num(entry["sec"])
                cur["words"]! += num(entry["words"])
                days[day] = cur
            }
        }
        if !days.isEmpty { out["stats"] = ["days": days] }
        if out["sent"] != nil { out["sentEdition"] = edition }
        return out
    }

    /// The status a merged state stands for, as library.py derives it (tests/status_vectors.json). `audio`: the
    /// book has a narration; `atEnd`: its position is at the end, the sign of a book finished before statuses.
    static func status(_ st: [String: Any], audio: Bool, atEnd: Bool)
        -> (status: BookStatus, rereading: Bool, finishedOn: String?)
    {
        let finished = st["finished"] as? [String] ?? []
        let days = ((st["stats"] as? [String: Any])?["days"] as? [String: Any]) ?? [:]
        let seconds = days.values.reduce(0) { $0 + num(($1 as? [String: Any])?["sec"]) }
        let status: BookStatus
        switch st["shelf"] as? String ?? "" {
        case "done": status = .done
        case "reading": status = .reading
        case "paused", "library":
            // a paused book read again is being read: its current mode's position is newer than the pause
            let key = audio && st["mode"] as? String != "pages" ? "posAt" : "sentAt"
            status = num(st[key]) > num(st["shelfAt"]) ? .reading : .paused
        case "":
            status = atEnd ? .done : seconds > 600 ? .reading : .none
        default:
            status = .none
        }
        // the day it was finished; one finished before the dates were kept: the last day it was read
        let on = finished.max() ?? (status == .done ? days.keys.max() : nil)
        return (status, status == .reading && !finished.isEmpty, on)
    }

    /// The sentence of `st` as it counts in `edition` (state.py `_sent`): as saved when it names no other
    /// edition; translated, clamped to the map, when `maps` (those of editions.json leading to `edition`) map the
    /// one it names; else nil (dropped).
    static func sentence(_ st: [String: Any], edition: String, maps: [String: [Int]]) -> Any? {
        guard let value = st["sent"] else { return nil }
        guard !edition.isEmpty, let named = st["sentEdition"], !(named is NSNull), !"\(named)".isEmpty,
            "\(named)" != edition
        else { return value }
        guard let ed = named as? String, let map = maps[ed], !map.isEmpty,
            let n = value as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), !n.doubleValue.isNaN
        else { return nil }
        return map[Int(min(max(n.doubleValue, 0), Double(map.count - 1)))]
    }

    /// The book's editions.json, read once per load through the coordinated read. Missing or not downloaded
    /// yet: no map.
    static func editions(_ dir: URL) -> Editions {
        guard let data = Coordinated.read(dir.appendingPathComponent("editions.json")) else { return Editions() }
        return Editions(json: try? JSONSerialization.jsonObject(with: data))
    }

    static func num(_ v: Any?) -> Double {
        switch v {
        case let n as NSNumber: return n.doubleValue
        case let d as Double: return d
        case let i as Int: return Double(i)
        default: return 0
        }
    }

    private static func isDeviceFile(_ name: String) -> Bool {
        name.count == 37 && name.hasSuffix(".json") && name.dropLast(5).allSatisfy { $0.isHexDigit && !$0.isUppercase }
    }

    /// The real name behind an iCloud placeholder (`.name.icloud`), so a file not downloaded yet still counts.
    static func realName(_ url: URL) -> URL {
        let name = url.lastPathComponent
        guard name.hasPrefix("."), name.hasSuffix(".icloud") else { return url }
        return url.deletingLastPathComponent().appendingPathComponent(String(name.dropFirst().dropLast(7)))
    }

    private static func read(_ url: URL) -> [String: Any] {
        guard let data = Coordinated.read(url),
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        else { return cached(url) ?? [:] }  // not downloaded yet: the last value stands
        remember(url, obj)
        return obj
    }

    /// Everything every device has written for this book, merged.
    static func load(shared dir: URL, edition: String) -> [String: Any] {
        var files = Coordinated.list(dir.appendingPathComponent("state", isDirectory: true))
            .map(realName)
            .filter { isDeviceFile($0.lastPathComponent) }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }  // the contract's order: equal times, earlier file wins
            .map(read)
        // a book the Mac has not opened since it went per device still has its one old file: read it too,
        // first, where the Mac's migration puts it before any device file
        let legacy = dir.appendingPathComponent("state.json")
        if FileManager.default.fileExists(atPath: legacy.path) { files.insert(read(legacy), at: 0) }
        return merge(files, edition: edition, editions: editions(dir))
    }

    /// This phone's own file, or nil when it exists but cannot be read now (not downloaded, half
    /// synced) and nothing is remembered: writing then would replace its history with an empty start.
    private static func readOwn(_ url: URL) -> [String: Any]? {
        if let data = Coordinated.read(url), let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            remember(url, obj)
            return obj
        }
        if let known = cached(url) { return known }
        // only its placeholder here (iCloud took the file back): it exists all the same
        let evicted = ((try? FileManager.default.contentsOfDirectory(
            at: url.deletingLastPathComponent(), includingPropertiesForKeys: nil)) ?? [])
            .contains { $0.lastPathComponent != url.lastPathComponent && realName($0).lastPathComponent == url.lastPathComponent }
        return FileManager.default.fileExists(atPath: url.path) || evicted ? nil : [:]
    }

    private static func ownURL(_ dir: URL) -> URL {
        dir.appendingPathComponent("state/\(Device.id).json")
    }

    /// A change from this phone. Only the merged keys are taken; statistics come from sessions alone. None
    /// when nothing was written: the caller tells the page, which may try again.
    @discardableResult
    static func put(shared dir: URL, edition: String, patch: [String: Any]) -> [String: Any]? {
        guard write(shared: dir, edition: edition, patch: patch) else { return nil }
        return load(shared: dir, edition: edition)
    }

    /// `put` without the merge read back: false when nothing was written, so the caller can try again.
    /// `edition`: the text the phone has now. A sentence comes with the edition the page loaded
    /// (`sentEdition`): one counted in another text is not taken, unless editions.json maps that text: then it
    /// is translated and saved as the current edition's, as on the Mac (state.py `put`).
    @discardableResult
    static func write(shared dir: URL, edition: String, patch: [String: Any]) -> Bool {
        queue.sync {
            guard var st = readOwn(ownURL(dir)) else { return false }
            // as state.py: a sentence naming no edition (none, null, "", or any falsy value) is the phone's own
            let named = patch["sentEdition"]
            let other = patch["sent"] != nil && !edition.isEmpty && !pyFalsy(named) && (named as? String) != edition
            let maps = other ? editions(dir).maps(for: edition) : [:]
            for key in lww where patch[key] != nil {
                var value = patch[key]
                // a list of days or nothing: anything else would be merged as one
                if key == "finished", !(value is [Any]) { continue }
                if key == "sent", other {
                    // a page still showing the text this book had before: taken only through the map; an edition
                    // that is not a string names no text at all
                    guard named is String, let moved = sentence(patch, edition: edition, maps: maps) else { continue }
                    value = moved
                }
                if num(patch[key + "At"]) >= num(st[key + "At"]) {
                    st[key] = value
                    st[key + "At"] = patch[key + "At"] ?? 0
                    if key == "sent" {
                        st["sentPct"] = patch["sentPct"] ?? 0
                        st["sentEdition"] = edition
                    }
                }
            }
            return save(st, dir)
        }
    }

    @discardableResult
    static func addSession(shared dir: URL, edition: String, day: String, sec: Double, words: Double) -> [String: Any] {
        session(shared: dir, day: day, sec: sec, words: words)
        return load(shared: dir, edition: edition)
    }

    /// A session added to this phone's statistics of the book: false when it could not be written.
    @discardableResult
    static func session(shared dir: URL, day: String, sec: Double, words: Double) -> Bool {
        guard sec >= 2 else { return true }  // nothing worth counting
        return queue.sync {
            guard var st = readOwn(ownURL(dir)) else { return false }
            var stats = st["stats"] as? [String: Any] ?? [:]
            var days = stats["days"] as? [String: Any] ?? [:]
            let cur = days[day] as? [String: Any] ?? [:]
            days[day] = ["sec": num(cur["sec"]) + max(0, sec), "words": num(cur["words"]) + max(0, words)]
            stats["days"] = days
            st["stats"] = stats
            return save(st, dir)
        }
    }

    /// Falsy as Python has it (state.py tests the edition so): nothing, null, "", 0, false, [] or {}.
    static func pyFalsy(_ v: Any?) -> Bool {
        switch v {
        case nil, is NSNull: return true
        case let s as String: return s.isEmpty
        case let n as NSNumber: return n.doubleValue == 0
        case let a as [Any]: return a.isEmpty
        case let d as [String: Any]: return d.isEmpty
        default: return false
        }
    }

    /// One device's state file joined into another of the same device (a book moved over its copy in another
    /// library): per merged key the newer value, as the merge takes it; the days read, per day the larger,
    /// so nothing counted in both is counted twice.
    static func joined(_ mine: [String: Any], into there: [String: Any]) -> [String: Any] {
        var out = there
        for key in lww where mine[key] != nil {
            guard out[key] == nil || num(mine[key + "At"]) > num(out[key + "At"]) else { continue }
            out[key] = mine[key]
            out[key + "At"] = mine[key + "At"] ?? 0
            if key == "sent" {
                out["sentPct"] = mine["sentPct"] ?? 0
                out["sentEdition"] = mine["sentEdition"]
            }
        }
        let a = ((mine["stats"] as? [String: Any])?["days"] as? [String: Any]) ?? [:]
        var days = ((there["stats"] as? [String: Any])?["days"] as? [String: Any]) ?? [:]
        for (day, v) in a {
            let m = v as? [String: Any] ?? [:], t = days[day] as? [String: Any] ?? [:]
            days[day] = ["sec": max(num(m["sec"]), num(t["sec"])), "words": max(num(m["words"]), num(t["words"]))]
        }
        if !days.isEmpty {
            var stats = there["stats"] as? [String: Any] ?? [:]
            stats["days"] = days
            out["stats"] = stats
        }
        return out
    }

    /// Written only into a book folder that is there: a book deleted (here or on another device) does not
    /// come back as a folder holding nothing but state.
    @discardableResult
    private static func save(_ st: [String: Any], _ dir: URL) -> Bool {
        guard FileManager.default.fileExists(atPath: dir.path) else { return false }
        guard let st = JSONSafe.clean(st) as? [String: Any], let data = JSONSafe.data(st) else { return false }
        let url = ownURL(dir)
        guard Coordinated.write(data, to: url) else { return false }
        remember(url, st)
        return true
    }

    /// The UTC day the reader counts statistics in (common.js `today`).
    static var today: String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.locale = Locale(identifier: "en_US_POSIX")  // Latin digits whatever the phone's language
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    static var nowMs: Double { (Date().timeIntervalSince1970 * 1000).rounded() }
}

/// What goes to JSONSerialization from the page: numbers that are not finite and values JSON has no
/// form for are dropped, so no write or answer can throw an exception that `try?` does not catch.
enum JSONSafe {
    static func clean(_ v: Any) -> Any? {
        switch v {
        case let s as String: return s
        case let n as NSNumber:
            return CFGetTypeID(n) == CFBooleanGetTypeID() || n.doubleValue.isFinite ? n : nil
        case is NSNull: return v
        case let a as [Any]: return a.compactMap(clean)
        case let d as [String: Any]: return d.compactMapValues(clean)
        default: return nil
        }
    }

    static func data(_ v: Any) -> Data? {
        guard let c = clean(v), JSONSerialization.isValidJSONObject(c) else { return nil }
        return try? JSONSerialization.data(withJSONObject: c)
    }
}

/// This phone's id: in the Keychain, so deleting and reinstalling the app keeps it.
enum Device {
    static let id: String = {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "readsync",
            kSecAttrAccount as String: "device-id",
            kSecReturnData as String: true,
        ]
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecSuccess, let data = out as? Data, let id = String(data: data, encoding: .utf8) {
            return id
        }
        // where the Keychain is out of reach (an unsigned build) the app's own defaults keep the id:
        // a new id on every launch would make one phone look like many devices
        let id = UserDefaults.standard.string(forKey: "deviceID")
            ?? UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        UserDefaults.standard.set(id, forKey: "deviceID")
        // a Keychain that failed (locked, say) rather than came up empty may hold an id: none is added over it
        guard status == errSecItemNotFound else { return id }
        let add: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "readsync",
            kSecAttrAccount as String: "device-id",
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
            kSecValueData as String: Data(id.utf8),
        ]
        SecItemAdd(add as CFDictionary, nil)
        return id
    }()
}
