// The reader is the same page the Mac serves (reader/, bundled), shown in a web view. What the
// server does on the Mac is done here: readsync:// serves the page, the book's files from the app's
// copy and the reading state; writes come back over the `readsync` message bridge.

import AudioToolbox
import SwiftUI
import UniformTypeIdentifiers
import WebKit

struct ReaderView: UIViewRepresentable {
    let slug: String
    let close: () -> Void

    func makeCoordinator() -> Bridge { Bridge(slug: slug, close: close) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.setURLSchemeHandler(context.coordinator.files, forURLScheme: "readsync")
        config.mediaTypesRequiringUserActionForPlayback = []
        let ucc = config.userContentController
        if let url = Bundle.main.url(forResource: "native-audio", withExtension: "js"),
            let js = try? String(contentsOf: url, encoding: .utf8)
        {
            ucc.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        }
        let weak = WeakHandler(context.coordinator)
        ucc.addScriptMessageHandler(weak, contentWorld: .page, name: "readsync")
        ucc.add(weak, name: "audio")
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.isOpaque = false
        web.backgroundColor = .clear
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsBackForwardNavigationGestures = false
        #if DEBUG
            web.isInspectable = true
        #endif
        Player.shared.attach(web, slug: slug)
        context.coordinator.web = web
        #if DEBUG
            context.coordinator.watchDebugScript()
        #endif
        var page = URLComponents(string: "readsync://app/index.html")!
        page.queryItems = [URLQueryItem(name: "book", value: slug)]
        web.load(URLRequest(url: page.url!))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    static func dismantleUIView(_ web: WKWebView, coordinator: Bridge) {
        // the narrator plays on without the page: the library's mini player has it now
        Player.shared.detach(web)
        #if DEBUG
            coordinator.debugTimer?.invalidate()
        #endif
        web.configuration.userContentController.removeAllScriptMessageHandlers()
    }
}

/// The script message handlers hold their target strongly; this keeps the web view from owning the bridge.
final class WeakHandler: NSObject, WKScriptMessageHandler, WKScriptMessageHandlerWithReply {
    weak var target: Bridge?
    init(_ target: Bridge) { self.target = target }

    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(ucc, didReceive: message)
    }

    func userContentController(
        _ ucc: WKUserContentController, didReceive message: WKScriptMessage,
        replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void
    ) {
        target?.userContentController(ucc, didReceive: message, replyHandler: replyHandler)
    }
}

@MainActor
final class Bridge: NSObject, WKNavigationDelegate {
    let slug: String
    let close: () -> Void
    let files: Files
    weak var web: WKWebView?

    /// The reader's writes still on their way: closing a book waits for them (Player.flush).
    private static var writes: [UUID: Task<Void, Never>] = [:]

    static func flushWrites() async {
        for task in Array(writes.values) { await task.value }
    }

    init(slug: String, close: @escaping () -> Void) {
        self.slug = slug
        self.close = close
        files = Files(slug: slug)
    }

    // ---- messages ----

    func userContentController(_ ucc: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "audio", let body = message.body as? [String: Any] { Player.shared.handle(body, from: slug) }
    }

    func userContentController(
        _ ucc: WKUserContentController, didReceive message: WKScriptMessage,
        replyHandler: @escaping @MainActor @Sendable (Any?, String?) -> Void
    ) {
        guard let msg = message.body as? [String: Any], let method = msg["method"] as? String else {
            return replyHandler(nil, "bad message")
        }
        let path = msg["path"] as? String ?? ""
        let body = msg["body"] as? [String: Any] ?? [:]
        if method == "CHIME" {
            AudioServicesPlaySystemSound(1013)
            return replyHandler([:], nil)
        }
        if method == "PUT", path == "/api/settings" {
            AppSettings.save(body)
            return replyHandler(AppSettings.load(), nil)
        }
        // the path names the book: a write meant for another one is not this page's to make
        let named = path.hasPrefix("/api/state/")
            ? path.dropFirst("/api/state/".count).split(separator: "/").first.map { $0.removingPercentEncoding ?? String($0) }
            : nil
        guard named == slug else { return replyHandler(nil, "не та книга") }
        guard let book = Shelf.shared.localBook(slug) else { return replyHandler(nil, "нет книги") }
        guard let dir = Shelf.shared.sharedDir(slug) else { return replyHandler(nil, "папка библиотеки недоступна") }
        let id = UUID()
        Bridge.writes[id] = Task.detached {
            let out: [String: Any]
            if path.hasSuffix("/session") {
                out = ReadingState.addSession(
                    shared: dir, edition: book.edition, day: body["day"] as? String ?? ReadingState.today,
                    sec: ReadingState.num(body["sec"]), words: ReadingState.num(body["words"]))
            } else {
                var patch = body
                patch.removeValue(forKey: "pos")  // the player owns the position here
                patch.removeValue(forKey: "posAt")
                out = ReadingState.put(shared: dir, edition: book.edition, patch: patch)
            }
            let merged = JSONBox(out)
            await MainActor.run { Player.shared.stateChanged(book.slug, merged.value as? [String: Any] ?? [:]) }
            let sendable = JSONBox(out)
            await MainActor.run {
                Bridge.writes[id] = nil
                replyHandler(sendable.value, nil)
            }
        }
    }

    // ---- navigation ----

    func webView(
        _ web: WKWebView, decidePolicyFor action: WKNavigationAction,
        decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void
    ) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url.scheme == "readsync", url.query?.contains("book=") == true { return decisionHandler(.allow) }
        if url.scheme == "readsync" {
            decisionHandler(.cancel)  // the reader's link to the library: the library is native here
            close()
            return
        }
        decisionHandler(.cancel)
        if ["http", "https"].contains(url.scheme ?? "") { UIApplication.shared.open(url) }
    }

    #if DEBUG
        /// Debug builds only: a script dropped at Documents/debug.js runs in the page, and what it
        /// returns lands in Documents/debug-out.json. The simulator has no other way into the page.
        var debugTimer: Timer?
        func watchDebugScript() {
            let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            debugTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    let script = docs.appendingPathComponent("debug.js")
                    guard let js = try? String(contentsOf: script, encoding: .utf8), let web = self?.web else { return }
                    try? FileManager.default.removeItem(at: script)
                    web.callAsyncJavaScript(js, arguments: [:], in: nil, in: .page) { result in
                        let out: Any
                        switch result {
                        case .success(let v): out = ["ok": v]
                        case .failure(let e): out = ["error": "\(e)"]
                        }
                        let data = (try? JSONSerialization.data(withJSONObject: out, options: [.fragmentsAllowed]))
                            ?? Data("\(out)".utf8)
                        try? data.write(to: docs.appendingPathComponent("debug-out.json"))
                    }
                }
            }
        }
    #endif

    /// iOS takes the page away under memory pressure, often during a long listen in the background:
    /// it comes back where the player is.
    func webViewWebContentProcessDidTerminate(_ web: WKWebView) {
        web.reload()
    }
}

/// Holds a JSON-shaped value across the hop back to the main actor.
struct JSONBox: @unchecked Sendable {
    let value: Any
    init(_ value: Any) { self.value = value }
}

/// readsync://app/... — the page, the book's files and the reading state.
final class Files: NSObject, WKURLSchemeHandler {
    let slug: String
    private var stopped = Set<ObjectIdentifier>()
    private let lock = NSLock()

    init(slug: String) { self.slug = slug }

    func webView(_ web: WKWebView, start task: any WKURLSchemeTask) {
        let path = task.request.url?.path ?? "/"
        let slug = self.slug
        // off the main thread: the book's files and the coordinated state reads can take a moment
        Task.detached { [weak self] in
            let (status, type, data) = await Files.respond(path: path, slug: slug)
            await self?.finish(task, status: status, type: type, data: data)
        }
    }

    func webView(_ web: WKWebView, stop task: any WKURLSchemeTask) {
        _ = lock.withLock { stopped.insert(ObjectIdentifier(task)) }
    }

    /// On the main thread, where WebKit tells of a stop too: none can come between the check and the answer.
    @MainActor private func finish(_ task: any WKURLSchemeTask, status: Int, type: String, data: Data) {
        // a task the page gave up on must not be answered: WebKit throws if it is
        if lock.withLock({ stopped.remove(ObjectIdentifier(task)) != nil }) { return }
        let response = HTTPURLResponse(
            url: task.request.url!, statusCode: status, httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": type, "Content-Length": "\(data.count)", "Cache-Control": "no-store"])!
        task.didReceive(response)
        task.didReceive(data)
        task.didFinish()
    }

    nonisolated static func respond(path: String, slug: String) async -> (Int, String, Data) {
        let json = { (obj: Any) -> (Int, String, Data) in
            (200, "application/json", (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8))
        }
        if path == "/api/settings" { return json(AppSettings.load()) }
        if path == "/api/books" { return json(await MainActor.run { books() }) }
        if path.hasPrefix("/api/state/") {
            let s = String(path.dropFirst("/api/state/".count))
            guard let book = Shelf.localCopy(s) else { return json([:]) }
            guard let dir = await MainActor.run(body: { Shelf.shared.sharedDir(s) }) else { return json([:]) }
            return json(ReadingState.load(shared: dir, edition: book.edition))
        }
        if path.hasPrefix("/books/") {
            let rel = path.dropFirst("/books/".count)
            let file = Shelf.localRoot.appendingPathComponent(String(rel))
            guard file.standardizedFileURL.path.hasPrefix(Shelf.localRoot.standardizedFileURL.path + "/"),
                let data = try? Data(contentsOf: file)
            else { return (404, "text/plain", Data()) }
            return (200, mime(file), data)
        }
        // the reader's own files, bundled
        guard let base = Bundle.main.resourceURL?.appendingPathComponent("reader") else { return (404, "text/plain", Data()) }
        let name = path == "/" ? "index.html" : String(path.dropFirst())
        let file = base.appendingPathComponent(name)
        guard file.standardizedFileURL.path.hasPrefix(base.standardizedFileURL.path + "/"),
            let data = try? Data(contentsOf: file)
        else { return (404, "text/plain", Data()) }
        return (200, mime(file), data)
    }

    /// What the reader asks /api/books for: the book it opens, as the Mac would describe it.
    @MainActor static func books() -> [[String: Any]] {
        Shelf.shared.books.compactMap { b in
            guard let local = Shelf.localCopy(b.slug) else { return nil }
            var out: [String: Any] = ["slug": local.slug, "title": local.title, "author": local.author, "ready": true]
            if let a = local.audioName, local.hasAudio { out["audio"] = a }
            // where the main text ends: the reader marks the book read there
            if let end = local.textEnd { out["text_end"] = end }
            if let end = local.audioEnd { out["audio_end"] = end }
            return out
        }
    }

    nonisolated static func mime(_ url: URL) -> String {
        UTType(filenameExtension: url.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
    }
}

extension Shelf {
    /// The local copy of a book, from outside the main actor.
    nonisolated static func localCopy(_ slug: String) -> Book? {
        let toml = localDir(slug).appendingPathComponent("book.toml")
        return (try? String(contentsOf: toml, encoding: .utf8)).map { Book(slug: slug, toml: $0) }
    }
}

/// The reader's look on this phone: its own, not the Mac's (a phone wants another size and width).
enum AppSettings {
    private static var file: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("settings.json")
    }

    static func load() -> [String: Any] {
        (try? Data(contentsOf: file)).flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
    }

    /// The reader's save: the app's own keys, which the page does not know, are kept.
    static func save(_ value: [String: Any]) {
        var value = value
        if value["lockText"] == nil, let keep = load()["lockText"] { value["lockText"] = keep }
        write(value)
    }

    private static func write(_ value: [String: Any]) {
        if let data = try? JSONSerialization.data(withJSONObject: value) { try? data.write(to: file, options: .atomic) }
    }

    static var rewind: Bool { ((load()["settings"] as? [String: Any])?["rewind"] as? Bool) ?? true }

    /// «Отмечать прочитанной в конце»: the reader's setting, saved by the page; on until switched off.
    static let markReadKey = "autoDone"
    static var markRead: Bool { ((load()["settings"] as? [String: Any])?[markReadKey] as? Bool) ?? true }

    /// The sentence being spoken as the lock screen's title: the app's setting, set from the library.
    static var lockText: Bool {
        get { (load()["lockText"] as? Bool) ?? false }
        set {
            var all = load()
            all["lockText"] = newValue
            write(all)
        }
    }
}
