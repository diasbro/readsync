// Where the served code comes from, and how it is updated without a new disk image.

import Foundation

enum Payload {
    static var repoURL: String {
        (Bundle.main.object(forInfoDictionaryKey: "RSRepository") as? String) ?? ""
    }
    static var bundledVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "RSPayloadVersion") as? String) ?? ""
    }
    static var installedVersion: String {
        (try? String(contentsOf: srcDir.appendingPathComponent(".payload-version"), encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
    static var isCheckout: Bool {
        FileManager.default.fileExists(atPath: srcDir.appendingPathComponent(".git").path)
    }

    /// First run copies the code out of the bundle; a checkout is never overwritten, git owns it.
    static func install() {
        let fm = FileManager.default
        adoptExistingBooks()
        if !fm.fileExists(atPath: booksDir.path) {
            try? fm.createDirectory(at: booksDir, withIntermediateDirectories: true)
        }
        guard let bundled = Bundle.main.url(forResource: "payload", withExtension: nil) else {
            log("payload missing from the bundle")
            return
        }
        let fresh = !fm.fileExists(atPath: srcDir.path)
        if !fresh && (isCheckout || installedVersion == bundledVersion) { return }
        log(fresh ? "installing the code" : "replacing the code with the bundled \(bundledVersion)")
        let staging = support.appendingPathComponent("src.new")
        try? fm.removeItem(at: staging)
        do {
            try fm.copyItem(at: bundled, to: staging)
            let old = support.appendingPathComponent("src.old")
            try? fm.removeItem(at: old)
            if fm.fileExists(atPath: srcDir.path) { try fm.moveItem(at: srcDir, to: old) }
            try fm.moveItem(at: staging, to: srcDir)
            try? fm.removeItem(at: old)
            try? bundledVersion.write(
                to: srcDir.appendingPathComponent(".payload-version"), atomically: true, encoding: .utf8)
        } catch {
            log("could not install the code: \(error)")
        }
    }

    /// A checkout of the project on this Mac already holds a library; the app reads the same one
    /// instead of starting empty beside it. Only the books are shared, the code stays the app's own.
    static func adoptExistingBooks() {
        let fm = FileManager.default
        if fm.fileExists(atPath: booksDir.path) { return }
        let mine = fm.homeDirectoryForCurrentUser.appendingPathComponent("readsync/books")
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: mine.path, isDirectory: &isDir), isDir.boolValue else { return }
        try? fm.createDirectory(at: support, withIntermediateDirectories: true)
        do {
            try fm.createSymbolicLink(at: booksDir, withDestinationURL: mine)
            log("reading the library at \(mine.path)")
        } catch {
            log("could not use the library at \(mine.path): \(error)")
        }
    }

    /// An update is a pull, so only what changed comes down the wire. A copied payload is turned
    /// into a checkout on the first update, which is why no disk image is needed for the next one.
    static var gitPath: String? {
        for path in ["/opt/homebrew/bin/git", "/usr/bin/git"]
        where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return nil
    }

    enum Update {
        case updated(String)  // what the new commit is called
        case upToDate
        case failed(String)  // only this one is worth a window
    }

    /// An update is a pull, so only what changed comes down the wire. A copied payload is turned
    /// into a checkout on the first update, which is why no disk image is needed for the next one.
    /// `beforeReset` runs once the new code is fetched, right before it replaces the old; a reason it
    /// returns stops the update there.
    static func update(beforeReset: () -> String? = { nil }) -> Update {
        guard !repoURL.isEmpty else {
            return .failed("в приложении не записано, откуда обновляться")
        }
        guard let git = gitPath else {
            return .failed("для обновления нужен git, поставь его командой xcode-select --install")
        }
        // a checkout made here and not yet reset to upstream is taken back on any failure: a `.git` without a
        // commit would count as a checkout, and the copy of the code would never again be replaced by a newer
        // one the app brings (`install`)
        let made = !isCheckout
        let failed = { (why: String) -> Update in
            if made { try? FileManager.default.removeItem(at: srcDir.appendingPathComponent(".git")) }
            return .failed(why)
        }
        if made {
            log("turning the copied code into a checkout")
            let (icode, iout) = run(git, ["init", "-q"], cwd: srcDir)
            if icode != 0 { return failed(reason(iout)) }
        }
        // the address this app was built with wins: an older one (ssh, say) may need a key this Mac lacks
        let (ocode, origin) = run(git, ["remote", "get-url", "origin"], cwd: srcDir)
        if ocode != 0 || origin.trimmingCharacters(in: .whitespacesAndNewlines) != repoURL {
            let (rcode, rout) = run(git, ["remote", ocode != 0 ? "add" : "set-url", "origin", repoURL], cwd: srcDir)
            if rcode != 0 { return failed(reason(rout)) }
        }
        let before = run(git, ["rev-parse", "--short", "HEAD"], cwd: srcDir).1
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let (code, out) = run(git, ["fetch", "--quiet", "origin", "main"], cwd: srcDir, timeout: 180)
        if code != 0 { return failed(reason(out)) }
        if let why = beforeReset() { return failed(why) }
        let (rcode, rout) = run(git, ["reset", "--hard", "--quiet", "origin/main"], cwd: srcDir)
        if rcode != 0 { return failed(reason(rout)) }
        // files the copied payload had and upstream has since deleted would stay forever, untracked
        _ = run(git, ["clean", "-fdq", "-e", ".payload-version"], cwd: srcDir)
        let after = run(git, ["rev-parse", "--short", "HEAD"], cwd: srcDir).1
            .trimmingCharacters(in: .whitespacesAndNewlines)
        try? after.write(to: srcDir.appendingPathComponent(".payload-version"), atomically: true, encoding: .utf8)
        if before == after { return .upToDate }
        let subject = run(git, ["log", "-1", "--pretty=%s"], cwd: srcDir).1
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return .updated(subject)
    }

    /// git says a lot; the reader needs the one line that says what to do about it. What git said goes to the log.
    private static func reason(_ out: String) -> String {
        let text = out.trimmingCharacters(in: .whitespacesAndNewlines)
        log("git: \(text)")
        // the precise complaints first: git repeats "Could not read from remote" after every one of them
        if text.contains("Repository not found") || text.contains("does not exist") {
            return "репозиторий не найден: \(repoURL)"
        }
        if text.contains("Permission denied") || text.contains("publickey") {
            return "репозиторий не пускает: нужен доступ по ключу к \(repoURL)"
        }
        if text.contains("Could not resolve host") || text.contains("Could not read from remote")
            || text.contains("Connection refused") || text.contains("timed out") {
            return "нет связи с репозиторием, проверь интернет и попробуй позже"
        }
        return "обновить код не вышло, подробности в журнале"
    }

    /// How many commits upstream is ahead, or nil when that could not be learned (no checkout yet,
    /// no git, no network).
    static func behind() -> Int? {
        guard isCheckout, let git = gitPath else { return nil }
        if run(git, ["fetch", "--quiet", "origin", "main"], cwd: srcDir, timeout: 120).0 != 0 { return nil }
        let out = run(git, ["rev-list", "--count", "HEAD..origin/main"], cwd: srcDir).1
        return Int(out.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// The importers need beautifulsoup4, lxml and pypdf. The app carries them; this only matters
    /// on a Mac whose own Python is standing in, or after an update that asked for something new.
    /// `done` is told whether tools were installed just now: the server sees them only after a restart.
    static func ensureTools(python: String, done: @escaping (_ installed: Bool) -> Void) {
        // with libsDir on the path, as the server has it: what an earlier launch installed there counts
        let check = ["-c", "import sys; sys.path.insert(0, sys.argv[1]); import bs4, lxml.etree, pypdf", libsDir.path]
        DispatchQueue.global(qos: .utility).async {
            if run(python, check, timeout: 30).0 == 0 { DispatchQueue.main.async { done(false) }; return }
            log("installing the import tools next to the books")
            let (code, out) = run(
                python,
                ["-m", "pip", "install", "--quiet", "--target", libsDir.path,
                 "beautifulsoup4", "lxml", "pypdf"], timeout: 900)
            log(code == 0 ? "import tools ready" : "import tools failed: \(out)")
            DispatchQueue.main.async { done(code == 0) }
        }
    }
}
