// Reading state, the same way the Mac keeps it (state.py): this phone writes only
// books/<slug>/state/<its id>.json in the shared library, reads every device's file and merges them.
// tests/state_vectors.json is the contract both sides follow.

import Foundation
import Security

enum ReadingState {
    static let lww = ["pos", "sent", "mode", "opened", "shelf"]
    private static let queue = DispatchQueue(label: "readsync.state")  // one writer for this phone's file
    nonisolated(unsafe) private static var lastGood: [URL: [String: Any]] = [:]
    private static let cacheLock = NSLock()  // apart from `queue`: reads happen inside a write

    private static func cached(_ url: URL) -> [String: Any]? { cacheLock.withLock { lastGood[url] } }
    private static func remember(_ url: URL, _ st: [String: Any]) { cacheLock.withLock { lastGood[url] = st } }

    /// The merged view of several devices' state. Pure, like state.merge on the Mac.
    static func merge(_ files: [[String: Any]], edition: String) -> [String: Any] {
        var out: [String: Any] = [:]
        for st in files {
            for key in lww {
                guard let value = st[key] else { continue }
                if key == "sent", !edition.isEmpty, let ed = st["sentEdition"].map({ "\($0)" }), !ed.isEmpty,
                    !(st["sentEdition"] is NSNull), ed != edition
                {
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
        return out
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
    private static func realName(_ url: URL) -> URL {
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
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
            .map(read)
        // a book the Mac has not opened since it went per device still has its one old file: read it too
        let legacy = dir.appendingPathComponent("state.json")
        if FileManager.default.fileExists(atPath: legacy.path) { files.append(read(legacy)) }
        return merge(files, edition: edition)
    }

    /// This phone's own file, or nil when it exists but cannot be read now (not downloaded, half
    /// synced) and nothing is remembered: writing then would replace its history with an empty start.
    private static func readOwn(_ url: URL) -> [String: Any]? {
        if let data = Coordinated.read(url), let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
            remember(url, obj)
            return obj
        }
        if let known = cached(url) { return known }
        return FileManager.default.fileExists(atPath: url.path) ? nil : [:]
    }

    private static func ownURL(_ dir: URL) -> URL {
        dir.appendingPathComponent("state/\(Device.id).json")
    }

    /// A change from this phone. Only the merged keys are taken; statistics come from sessions alone.
    @discardableResult
    static func put(shared dir: URL, edition: String, patch: [String: Any]) -> [String: Any] {
        queue.sync {
            guard var st = readOwn(ownURL(dir)) else { return }
            for key in lww where patch[key] != nil {
                if num(patch[key + "At"]) >= num(st[key + "At"]) {
                    st[key] = patch[key]
                    st[key + "At"] = patch[key + "At"] ?? 0
                    if key == "sent" {
                        st["sentPct"] = patch["sentPct"] ?? 0
                        st["sentEdition"] = edition
                    }
                }
            }
            save(st, dir)
        }
        return load(shared: dir, edition: edition)
    }

    @discardableResult
    static func addSession(shared dir: URL, edition: String, day: String, sec: Double, words: Double) -> [String: Any] {
        guard sec >= 2 else { return load(shared: dir, edition: edition) }
        queue.sync {
            guard var st = readOwn(ownURL(dir)) else { return }
            var stats = st["stats"] as? [String: Any] ?? [:]
            var days = stats["days"] as? [String: Any] ?? [:]
            let cur = days[day] as? [String: Any] ?? [:]
            days[day] = ["sec": num(cur["sec"]) + max(0, sec), "words": num(cur["words"]) + max(0, words)]
            stats["days"] = days
            st["stats"] = stats
            save(st, dir)
        }
        return load(shared: dir, edition: edition)
    }

    private static func save(_ st: [String: Any], _ dir: URL) {
        guard let data = try? JSONSerialization.data(withJSONObject: st) else { return }
        let url = ownURL(dir)
        if Coordinated.write(data, to: url) { remember(url, st) }
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
        if SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
            let data = out as? Data, let id = String(data: data, encoding: .utf8)
        {
            return id
        }
        // where the Keychain is out of reach (an unsigned build) the app's own defaults keep the id:
        // a new id on every launch would make one phone look like many devices
        let id = UserDefaults.standard.string(forKey: "deviceID")
            ?? UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        UserDefaults.standard.set(id, forKey: "deviceID")
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
