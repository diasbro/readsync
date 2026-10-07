// The local server as a child process: started on launch, stopped on quit, restarted on demand.

import Foundation

final class Server {
    private(set) var port = 8765
    private var task: Process?
    private var handle: FileHandle?

    var isRunning: Bool { task?.isRunning ?? false }
    var url: URL { URL(string: "http://127.0.0.1:\(port)/")! }

    func start(python: String) {
        stop()
        Server.stopOrphan()
        guard let port = Server.freePort(from: 8765) else {
            log("server did not start: ports 8765-8776 are all taken")
            return
        }
        self.port = port
        let task = Process()
        task.executableURL = URL(fileURLWithPath: python)
        task.arguments = [srcDir.appendingPathComponent("serve.py").path, "--port", "\(port)"]
        task.currentDirectoryURL = srcDir
        var env = ProcessInfo.processInfo.environment
        env["READSYNC_BOOKS"] = booksDir.path  // the books stay outside the code, updates never touch them
        env["READSYNC_PYTHON"] = python  // the pipeline runs on the same interpreter as the server
        if FileManager.default.fileExists(atPath: libsDir.path) { env["PYTHONPATH"] = libsDir.path }
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPYCACHEPREFIX"] = pycacheDir.path  // never into the signed bundle; the jobs inherit it
        task.environment = env
        if let handle = logHandle() {
            task.standardOutput = handle
            task.standardError = handle
            self.handle = handle
        }
        do {
            try task.run()
            self.task = task
            try? "\(task.processIdentifier)".write(to: serverPidFile, atomically: true, encoding: .utf8)
            log("server launched on \(port)")
            // the port is written down only once the server answers, so --open never points at a dead one
            DispatchQueue.global(qos: .utility).async {
                if answers(URL(string: "http://127.0.0.1:\(port)/")!, within: 10), task.isRunning {
                    try? "\(port)".write(to: portFile, atomically: true, encoding: .utf8)
                    log("server answers on \(port)")
                } else {
                    log("server on \(port) did not answer")
                }
            }
        } catch {
            log("server did not start: \(error)")
        }
    }

    func stop() {
        if let task = halt() { Server.reap(task) }
    }

    /// Asks the server to stop and forgets it; `reap` waits for it to go, so a caller can wait off the
    /// main thread. The pid and port files go only with a server this app started: with none, they are
    /// an orphan's (found by `stopOrphan`) or another copy's.
    func halt() -> Process? {
        let task = self.task
        self.task = nil
        try? handle?.close()
        handle = nil
        guard let task else { return nil }
        try? FileManager.default.removeItem(at: serverPidFile)
        try? FileManager.default.removeItem(at: portFile)
        guard task.isRunning else { return nil }
        task.terminate()
        return task
    }

    /// A server that will not stop gets three seconds, not forever.
    static func reap(_ task: Process) {
        let deadline = Date().addingTimeInterval(3)
        while task.isRunning && Date() < deadline { usleep(20_000) }
        if task.isRunning { kill(task.processIdentifier, SIGKILL) }
        log("server stopped")
    }

    /// A server whose app crashed or was force-quit keeps the port and keeps writing into the library:
    /// it goes before a new one starts. Only an orphan (its parent gone) that is this app's serve.py, by
    /// its pid: a second copy of the app never stops the first one's server, nor a process that got the number.
    private static func stopOrphan() {
        guard let pid = holds(serverPidFile), processDetails(pid)?.parent == 1,
            arguments(of: pid)?.contains(srcDir.appendingPathComponent("serve.py").path) == true
        else { return }
        kill(pid, SIGTERM)
        let deadline = Date().addingTimeInterval(3)
        while kill(pid, 0) == 0 && Date() < deadline { usleep(20_000) }
        if kill(pid, 0) == 0 { kill(pid, SIGKILL) }
        try? FileManager.default.removeItem(at: serverPidFile)
        try? FileManager.default.removeItem(at: portFile)
        log("stopped the server a previous run left behind (pid \(pid))")
    }

    /// A process's command line, or nil when the system will not say (KERN_PROCARGS2: argc, the executable's
    /// path, padding, then the arguments, each ending in a zero byte).
    static func arguments(of pid: pid_t) -> [String]? {
        var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
        var size = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > MemoryLayout<Int32>.size else { return nil }
        var buf = [UInt8](repeating: 0, count: size)
        guard sysctl(&mib, 3, &buf, &size, nil, 0) == 0, size > MemoryLayout<Int32>.size else { return nil }
        let argc = buf.withUnsafeBytes { $0.loadUnaligned(as: Int32.self) }
        var i = MemoryLayout<Int32>.size
        while i < size && buf[i] != 0 { i += 1 }  // the executable's path
        while i < size && buf[i] == 0 { i += 1 }  // its padding
        var args: [String] = []
        while args.count < argc && i < size {
            let start = i
            while i < size && buf[i] != 0 { i += 1 }
            args.append(String(decoding: buf[start..<i], as: UTF8.self))
            i += 1
        }
        return args
    }

    /// The first port nothing is listening on, so a second copy of the app does not fight the first.
    static func freePort(from first: Int) -> Int? {
        for port in first..<(first + 12) {
            let sock = socket(AF_INET, SOCK_STREAM, 0)
            if sock < 0 { continue }
            var yes: Int32 = 1
            setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
            var addr = sockaddr_in()
            addr.sin_family = sa_family_t(AF_INET)
            addr.sin_port = UInt16(port).bigEndian
            addr.sin_addr.s_addr = inet_addr("127.0.0.1")
            let bound = withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    bind(sock, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
            close(sock)
            if bound == 0 { return port }
        }
        return nil
    }
}
