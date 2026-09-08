// readsync for macOS: a menu bar item that runs the local server and keeps the code up to date.
// The app is a launcher. The code it serves lives in Application Support, so an update is a git
// pull, not a new disk image; the books live beside it and no update ever touches them.

import AppKit
import ServiceManagement

let support = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/readsync")
let srcDir = support.appendingPathComponent("src")
let booksDir = support.appendingPathComponent("books")
let libsDir = support.appendingPathComponent("pylibs")  // only for a package the app does not carry
let portFile = support.appendingPathComponent("port")

/// Where the library is right now: the running server writes its port down.
var libraryURL: URL {
    let port = (try? String(contentsOf: portFile, encoding: .utf8))
        .flatMap { Int($0.trimmingCharacters(in: .whitespacesAndNewlines)) } ?? 8765
    return URL(string: "http://127.0.0.1:\(port)/")!
}
let logFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/readsync.log")

/// The log is opened for appending every time: the server writes to the same file from its own
/// process, and two writers at their own offsets would overwrite each other's lines.
func logHandle() -> FileHandle? {
    try? FileManager.default.createDirectory(
        at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
    let fd = open(logFile.path, O_WRONLY | O_CREAT | O_APPEND, 0o644)
    return fd < 0 ? nil : FileHandle(fileDescriptor: fd, closeOnDealloc: true)
}

func log(_ line: String) {
    let stamp = ISO8601DateFormatter().string(from: Date())
    guard let data = "\(stamp) \(line)\n".data(using: .utf8), let handle = logHandle() else { return }
    handle.write(data)
    try? handle.close()
}

/// Run a command and wait for it. Returns (exit code, output).
@discardableResult
func run(_ tool: String, _ args: [String], cwd: URL? = nil, timeout: TimeInterval = 120) -> (Int32, String) {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: tool)
    task.arguments = args
    if let cwd { task.currentDirectoryURL = cwd }
    var env = ProcessInfo.processInfo.environment
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    task.environment = env
    let pipe = Pipe()
    task.standardOutput = pipe
    task.standardError = pipe
    do { try task.run() } catch { return (-1, "\(error)") }
    let deadline = Date().addingTimeInterval(timeout)
    var data = Data()
    while task.isRunning && Date() < deadline {
        data += pipe.fileHandleForReading.availableData
        usleep(50_000)
    }
    if task.isRunning { task.terminate() }
    task.waitUntilExit()
    data += pipe.fileHandleForReading.readDataToEndOfFile()
    return (task.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

/// The Python inside the app, for this processor. Nothing is installed on the Mac it runs on.
var bundledPython: String? {
    #if arch(arm64)
        let arch = "arm64"
    #else
        let arch = "x86_64"
    #endif
    let path = Bundle.main.resourceURL?.appendingPathComponent("python/\(arch)/bin/python3").path
    return path.flatMap { FileManager.default.isExecutableFile(atPath: $0) ? $0 : nil }
}

/// The Python that runs the server: the app's own, or the machine's if the bundle lost it.
func findPython() -> String? {
    let candidates = [
        bundledPython,
        "/opt/homebrew/bin/python3.13", "/opt/homebrew/bin/python3.12", "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3", "/usr/bin/python3",
    ].compactMap { $0 }
    for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
        let (code, out) = run(path, ["-c", "import sys; print(sys.version_info >= (3, 11))"], timeout: 10)
        if code == 0 && out.contains("True") { return path }
    }
    return nil
}
