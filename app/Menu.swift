// The menu bar item: the mark, what it offers, and what each item does.

import AppKit
import ServiceManagement

final class Menu: NSObject, NSApplicationDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let server = Server()
    private let menu = NSMenu()
    private var python: String?
    private var toolsReady = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.image = Menu.mark()
        item.button?.toolTip = "readsync"
        item.menu = menu
        log("menu bar item ready, visible=\(item.isVisible), icon=\(item.button?.image?.size ?? .zero)")
        menu.delegate = self
        Payload.install()
        python = findPython()
        guard let python else {
            alert(
                "Нужен Python 3.11 или новее",
                "Поставь его и запусти readsync снова:\n\nbrew install python@3.13")
            NSApp.terminate(nil)
            return
        }
        Payload.ensureTools(python: python) { [weak self] ok in self?.toolsReady = ok }
        server.start(python: python)
        build()
    }

    func applicationWillTerminate(_ notification: Notification) { server.stop() }

    /// The mark from the reader: a filled dot inside a thin halo, drawn so it follows the menu bar.
    static func mark() -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let image = NSImage(size: size, flipped: false) { rect in
            let ring = NSBezierPath(ovalIn: rect.insetBy(dx: 2.5, dy: 2.5))
            ring.lineWidth = 1.3
            NSColor.black.withAlphaComponent(0.55).setStroke()
            ring.stroke()
            NSColor.black.setFill()
            NSBezierPath(ovalIn: rect.insetBy(dx: 6, dy: 6)).fill()
            return true
        }
        image.isTemplate = true  // the menu bar decides the colour, light or dark
        return image
    }

    private func build() {
        menu.removeAllItems()
        add("Открыть библиотеку", "o", #selector(open))
        menu.addItem(.separator())
        let state = server.isRunning ? "Работает на порту \(server.port)" : "Сервер не запущен"
        menu.addItem(withTitle: state, action: nil, keyEquivalent: "").isEnabled = false
        add(server.isRunning ? "Перезапустить сервер" : "Запустить сервер", "", #selector(restart))
        if !toolsReady {
            menu.addItem(withTitle: "Ставлю инструменты импорта…", action: nil, keyEquivalent: "")
                .isEnabled = false
        }
        menu.addItem(.separator())
        let login = add("Запускать при входе", "", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        add("Проверить обновления…", "", #selector(update))
        let version = Payload.installedVersion.isEmpty ? Payload.bundledVersion : Payload.installedVersion
        menu.addItem(withTitle: "Версия \(version)", action: nil, keyEquivalent: "").isEnabled = false
        menu.addItem(.separator())
        add("Показать книги в Finder", "", #selector(showBooks))
        add("Открыть журнал", "", #selector(showLog))
        menu.addItem(.separator())
        add("Выйти", "q", #selector(quit))
    }

    @discardableResult
    private func add(_ title: String, _ key: String, _ action: Selector) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
        entry.target = self
        menu.addItem(entry)
        return entry
    }

    @objc private func open() { NSWorkspace.shared.open(server.url) }

    @objc private func restart() {
        if let python { server.start(python: python) }
        build()
    }

    @objc private func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            alert("Не вышло изменить автозапуск", "\(error.localizedDescription)")
        }
        build()
    }

    @objc private func update() {
        let done = Payload.update()
        if done.hasPrefix("обновлено"), let python {
            server.start(python: python)
        }
        build()
        notify(done)
    }

    @objc private func showBooks() { NSWorkspace.shared.open(booksDir) }
    @objc private func showLog() { NSWorkspace.shared.open(logFile) }
    @objc private func quit() { NSApp.terminate(nil) }

    private func alert(_ title: String, _ text: String) {
        let sheet = NSAlert()
        sheet.messageText = title
        sheet.informativeText = text
        sheet.runModal()
    }

    private func notify(_ text: String) {
        let sheet = NSAlert()
        sheet.messageText = "readsync"
        sheet.informativeText = text
        sheet.runModal()
    }
}

extension Menu: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) { build() }
}
