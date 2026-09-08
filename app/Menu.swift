// The menu bar item: the mark, what it offers, and what each item does. What a reader does daily is
// at the top and needs one click; everything that is set once and forgotten sits under «Настройки».

import AppKit
import ServiceManagement

final class Menu: NSObject, NSApplicationDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let server = Server()
    private let menu = NSMenu()
    private var python: String?
    private var toolsBusy = false
    private var behind = 0
    private var busy: String?  // what git is doing right now, while it is doing it
    private var reading: (title: String, slug: String)?
    private var checkTimer: Timer?
    private let gitQueue = DispatchQueue(label: "readsync.git")  // one at a time, never on the main thread

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.image = Menu.mark()
        item.menu = menu
        menu.delegate = self
        menu.autoenablesItems = false  // the update item says «занято» by being disabled, so AppKit must not decide
        log("menu bar item ready, visible=\(item.isVisible), icon=\(item.button?.image?.size ?? .zero)")
        Payload.install()
        python = findPython()
        guard let python else {
            alert(
                "Нужен Python 3.11 или новее",
                "Поставь его и запусти readsync снова:\n\nbrew install python@3.13")
            NSApp.terminate(nil)
            return
        }
        toolsBusy = true
        Payload.ensureTools(python: python) { [weak self] _ in self?.toolsBusy = false }
        server.start(python: python)
        build()
        // asked once the server is up, so the first opening of the menu already knows the book
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.lookForReading() }
        lookForUpdates()
        checkTimer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            self?.lookForUpdates()
        }
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

    // ---- the menu ----

    private func build() {
        item.button?.appearsDisabled = !server.isRunning  // the mark fades when nothing is serving
        item.button?.toolTip = server.isRunning ? "readsync · порт \(server.port)" : "readsync · сервер не запущен"
        menu.removeAllItems()
        add("Открыть библиотеку", "o", #selector(open))
        if let reading {
            add("Продолжить «\(reading.title)»", "", #selector(openReading))
        }
        menu.addItem(.separator())
        if !server.isRunning {
            add("Сервер не запущен, запустить", "", #selector(restart))
        }
        let updateItem = add(
            busy ?? (behind > 0 ? "Обновить: есть новое (\(behind))" : "Проверить обновления…"), "", #selector(update))
        updateItem.isEnabled = busy == nil
        let settings = NSMenuItem(title: "Настройки", action: nil, keyEquivalent: "")
        settings.submenu = settingsMenu()
        menu.addItem(settings)
        menu.addItem(.separator())
        add("Выйти", "q", #selector(quit))
    }

    private func settingsMenu() -> NSMenu {
        let sub = NSMenu()
        let version = Payload.installedVersion.isEmpty ? Payload.bundledVersion : Payload.installedVersion
        let state = server.isRunning ? "порт \(server.port)" : "сервер не запущен"
        sub.addItem(withTitle: "readsync \(version) · \(state)", action: nil, keyEquivalent: "").isEnabled = false
        if toolsBusy {
            sub.addItem(withTitle: "Ставлю инструменты импорта…", action: nil, keyEquivalent: "").isEnabled = false
        }
        sub.addItem(.separator())
        let login = add("Запускать при входе", "", #selector(toggleLogin), to: sub)
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        if Browser.canReuseTab {
            let reuse = add("Открывать в той же вкладке", "", #selector(toggleReuse), to: sub)
            reuse.state = Browser.reusesTab ? .on : .off
        }
        sub.addItem(.separator())
        add("Показать книги в Finder", "", #selector(showBooks), to: sub)
        add("Открыть журнал", "", #selector(showLog), to: sub)
        add("Перезапустить сервер", "", #selector(restart), to: sub)
        return sub
    }

    @discardableResult
    private func add(_ title: String, _ key: String, _ action: Selector, to target: NSMenu? = nil) -> NSMenuItem {
        let entry = NSMenuItem(title: title, action: action, keyEquivalent: key)
        entry.target = self
        (target ?? menu).addItem(entry)
        return entry
    }

    // ---- what the items do ----

    @objc private func open() { Browser.open(server.url) }

    @objc private func openReading() {
        guard let slug = reading?.slug else { return }
        guard let url = URL(string: "\(server.url.absoluteString)?book=\(slug)") else { return }
        Browser.open(url)
    }

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

    /// Off by default and never asked for on its own: the permission dialog appears only here,
    /// the moment the reader turns this on.
    @objc private func toggleReuse() {
        if Browser.reusesTab {
            Browser.reusesTab = false
        } else if Browser.mayAutomate(ask: true) {
            Browser.reusesTab = true
        } else {
            alert(
                "Нужен доступ к браузеру",
                "Разреши readsync управлять браузером в «Настройки → Конфиденциальность и безопасность → "
                    + "Автоматизация», тогда библиотека будет открываться в той же вкладке.")
        }
        build()
    }

    /// Git takes as long as the network takes, so it runs off the main thread: the menu keeps opening
    /// and says «Обновление…» meanwhile. A window appears only when something went wrong.
    @objc private func update() {
        guard busy == nil else { return }
        busy = "Обновление…"
        behind = 0
        build()
        gitQueue.async {
            let result = Payload.update()
            DispatchQueue.main.async {
                self.busy = nil
                switch result {
                case .updated(let what):
                    log("updated: \(what)")
                    if let python = self.python { self.server.start(python: python) }
                case .upToDate:
                    log("already up to date")
                case .failed(let why):
                    self.alert("Обновиться не вышло", why)
                }
                self.build()
            }
        }
    }

    @objc private func showBooks() { NSWorkspace.shared.open(booksDir) }
    @objc private func showLog() { NSWorkspace.shared.open(logFile) }
    @objc private func quit() { NSApp.terminate(nil) }

    private func alert(_ title: String, _ text: String) {
        let sheet = NSAlert()
        sheet.messageText = title
        sheet.informativeText = text
        NSApp.activate(ignoringOtherApps: true)
        sheet.runModal()
    }

    // ---- what the menu knows, asked in the background so opening it never waits ----

    private func lookForUpdates() {
        guard busy == nil else { return }
        busy = "Проверка обновлений…"
        build()
        gitQueue.async {
            let count = Payload.behindBy()
            DispatchQueue.main.async {
                self.busy = nil
                self.behind = count
                if count > 0 { log("\(count) new commits upstream") }
                self.build()
            }
        }
    }

    /// The book on the shelf «читаю сейчас», so it is one click away from the menu bar.
    private func lookForReading() {
        guard server.isRunning else { return }
        let url = server.url.appendingPathComponent("api/books")
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data,
                let books = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
            else { return }
            let now = books.filter { book in
                let state = book["state"] as? [String: Any]
                return (state?["shelf"] as? String) == "reading" && (book["ready"] as? Bool) == true
            }
            let newest = now.max { a, b in
                let opened = { (x: [String: Any]) in ((x["state"] as? [String: Any])?["opened"] as? Double) ?? 0 }
                return opened(a) < opened(b)
            }
            let title = newest?["title"] as? String
            let slug = newest?["slug"] as? String
            DispatchQueue.main.async {
                self.reading = (title != nil && slug != nil) ? (title!, slug!) : nil
            }
        }.resume()
    }
}

extension Menu: NSMenuDelegate {
    func menuWillOpen(_ menu: NSMenu) {
        lookForReading()  // answers by the next opening; the menu itself never waits on the network
        build()
    }
}
