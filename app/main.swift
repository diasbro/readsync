import AppKit
import ServiceManagement

// Two switches for the terminal, the same actions the menu offers: handy when the app is not running.
let args = CommandLine.arguments
/// Another copy of the app, running now: it owns the server, its port and the code it serves.
let otherCopy = Bundle.main.bundleIdentifier.map { id in
    NSRunningApplication.runningApplications(withBundleIdentifier: id).contains { $0.processIdentifier != getpid() }
} ?? false
if args.contains("--update") {
    // the code is not reset under a running server, nor under a book being loaded: the menu's update waits for both
    if otherCopy || holds(serverPidFile) != nil {
        print("не вышло: readsync запущен, обнови из его меню")
        exit(1)
    }
    let jobs = Menu.heldJobs()
    if !jobs.isEmpty {
        print("не вышло: книги ещё загружаются: \(jobs.joined(separator: ", ")). Обнови, когда закончится.")
        exit(1)
    }
    Payload.install()
    switch Payload.update() {
    case .updated(let what): print("обновлено: \(what)")
    case .upToDate: print("обновлений нет")
    case .failed(let why): print("не вышло: \(why)"); exit(1)
    }
    exit(0)
}
if let i = args.firstIndex(of: "--login"), i + 1 < args.count {
    do {
        if args[i + 1] == "off" {
            try SMAppService.mainApp.unregister()
        } else {
            try SMAppService.mainApp.register()
        }
        print("автозапуск: \(SMAppService.mainApp.status == .enabled ? "включён" : "выключен")")
        exit(0)
    } catch {
        print("не вышло: \(error.localizedDescription)")
        exit(1)
    }
}

if args.contains("--open") {
    Browser.openNow(libraryURL)
    exit(0)
}

// one copy at a time: a second would start a server of its own and write over the first one's pid and port
if otherCopy {
    log("readsync is running already: this copy quits")
    exit(0)
}

let app = NSApplication.shared
let delegate = Menu()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only, nothing in the Dock
app.run()
