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
let serverPidFile = support.appendingPathComponent("server.pid")
/// Python's compiled files go here, never next to the code: the bundled Python sits inside the signed app.
let pycacheDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/readsync/pycache")

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
    // nothing here may wait for a person: git and ssh fail instead of asking for a password or a host key
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    env["PYTHONPYCACHEPREFIX"] = pycacheDir.path
    task.environment = env
    let pipe = Pipe()
    task.standardOutput = pipe
    task.standardError = pipe
    do { try task.run() } catch { return (-1, "\(error)") }
    // read on the side: reading blocks while the tool is silent, and the deadline must still hold then
    var data = Data()
    let read = DispatchGroup()
    read.enter()
    DispatchQueue.global(qos: .utility).async {
        data = pipe.fileHandleForReading.readDataToEndOfFile()
        read.leave()
    }
    let exited = DispatchSemaphore(value: 0)
    task.terminationHandler = { _ in exited.signal() }
    if exited.wait(timeout: .now() + timeout) == .timedOut {
        task.terminate()
        if exited.wait(timeout: .now() + 3) == .timedOut { kill(task.processIdentifier, SIGKILL) }
    }
    _ = read.wait(timeout: .now() + 5)
    return (task.isRunning ? -1 : task.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

/// When a process started and who its parent is, or nil when no process has that number.
func processDetails(_ pid: pid_t) -> (start: Date, parent: pid_t)? {
    var info = kinfo_proc()
    var size = MemoryLayout<kinfo_proc>.stride
    var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, pid]
    guard sysctl(&mib, 4, &info, &size, nil, 0) == 0, size > 0 else { return nil }
    let t = info.kp_proc.p_un.__p_starttime
    return (Date(timeIntervalSince1970: Double(t.tv_sec) + Double(t.tv_usec) / 1e6), info.kp_eproc.e_ppid)
}

/// Whether the process that wrote its pid into this file still runs: a number the system has since given
/// to another process (after a reboot, say) started after the file was written.
func holds(_ file: URL) -> pid_t? {
    guard let text = try? String(contentsOf: file, encoding: .utf8),
        let pid = pid_t(text.trimmingCharacters(in: .whitespacesAndNewlines)),
        let written = (try? FileManager.default.attributesOfItem(atPath: file.path))?[.modificationDate] as? Date,
        let started = processDetails(pid)?.start, started <= written.addingTimeInterval(1)
    else { return nil }
    return pid
}

/// Whether the server answers within so many seconds, asked again and again. Off the main thread.
func answers(_ url: URL, within seconds: TimeInterval) -> Bool {
    final class Box: @unchecked Sendable { var up = false }
    let box = Box()
    let deadline = Date().addingTimeInterval(seconds)
    while !box.up && Date() < deadline {
        let answered = DispatchSemaphore(value: 0)
        let request = URLRequest(url: url.appendingPathComponent("api/books"), timeoutInterval: 1)
        URLSession.shared.dataTask(with: request) { _, response, _ in
            box.up = (response as? HTTPURLResponse)?.statusCode == 200
            answered.signal()
        }.resume()
        _ = answered.wait(timeout: .now() + 2)
        if !box.up { usleep(250_000) }
    }
    return box.up
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
        let (code, out) = run(path, ["-c", "import sys; print(sys.version_info >= (3, 12))"], timeout: 10)
        if code == 0 && out.contains("True") { return path }
    }
    return nil
}
