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
        port = Server.freePort(from: 8765)
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
        task.environment = env
        if let handle = logHandle() {
            task.standardOutput = handle
            task.standardError = handle
            self.handle = handle
        }
        do {
            try task.run()
            self.task = task
            try? "\(port)".write(to: portFile, atomically: true, encoding: .utf8)  // so --open knows where to look
            log("server started on \(port)")
        } catch {
            log("server did not start: \(error)")
        }
    }

    func stop() {
        if let task, task.isRunning {
            task.terminate()
            // this runs on the main thread: a server that will not stop gets three seconds, not forever
            let deadline = Date().addingTimeInterval(3)
            while task.isRunning && Date() < deadline { usleep(20_000) }
            if task.isRunning { kill(task.processIdentifier, SIGKILL) }
            log("server stopped")
        }
        task = nil
        try? handle?.close()
        handle = nil
    }

    /// The first port nothing is listening on, so a second copy of the app does not fight the first.
    static func freePort(from first: Int) -> Int {
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
        return first
    }
}
