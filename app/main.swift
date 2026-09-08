import AppKit
import ServiceManagement

// Two switches for the terminal, the same actions the menu offers: handy when the app is not running.
let args = CommandLine.arguments
if args.contains("--update") {
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
    Browser.open(libraryURL)
    exit(0)
}

let app = NSApplication.shared
let delegate = Menu()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only, nothing in the Dock
app.run()
