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
    private var updated: String?  // what the last update brought, said for a minute after it
    private var cloudBusy: String?  // the iCloud switch looking at the library before it asks
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
                "Нужен Python 3.12 или новее",
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
            let title = reading.title.count > 40 ? reading.title.prefix(39) + "…" : reading.title
            add("Продолжить «\(title)»", "", #selector(openReading))
        }
        menu.addItem(.separator())
        if !server.isRunning {
            add("Сервер не запущен, запустить", "", #selector(restart))
        }
        let updateItem = add(
            busy ?? (behind > 0 ? "Обновить: есть новое (\(behind))" : "Проверить обновления…"), "", #selector(update))
        updateItem.isEnabled = busy == nil
        if let updated, busy == nil {
            menu.addItem(withTitle: "Обновлено: \(updated). Открытую вкладку перезагрузи", action: nil, keyEquivalent: "")
                .isEnabled = false
        }
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
        if Cloud.isAvailable {
            let cloud = add(cloudBusy ?? "Библиотека в iCloud", "", #selector(toggleCloud), to: sub)
            cloud.state = Cloud.isOn ? .on : .off
            cloud.isEnabled = cloudBusy == nil
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
    /// and shows what is going on in grey. «Проверка обновлений…» while it is not yet known whether
    /// there is anything; «Обновление…» only once there is. A window appears only when something failed.
    @objc private func update() {
        guard busy == nil else { return }
        let known = behind > 0
        busy = known ? "Обновление…" : "Проверка обновлений…"
        updated = nil
        build()
        let url = server.isRunning ? server.url : nil
        gitQueue.async {
            // a check that could not reach upstream falls through to the update, which says why it failed;
            // a copy of the code that is not a checkout yet goes straight to the update that makes it one
            if !known, let count = Payload.behind() {
                DispatchQueue.main.async {
                    self.busy = count > 0 ? "Обновление…" : nil
                    self.behind = count
                    self.build()
                }
                if count == 0 { log("already up to date"); return }
            }
            // an update restarts the server: a book being loaded waits for nothing, so the update waits for it
            let jobs = Menu.runningJobs(at: url)
            if !jobs.isEmpty {
                DispatchQueue.main.async {
                    self.busy = nil
                    self.alert(
                        "Книги ещё загружаются",
                        "Обновить можно, когда закончится: \(jobs.joined(separator: ", ")).")
                    self.build()
                }
                return
            }
            let result = Payload.update()
            DispatchQueue.main.async {
                self.behind = 0
                self.busy = nil
                switch result {
                case .updated(let what):
                    log("updated: \(what)")
                    if let python = self.python { self.server.start(python: python) }
                    self.updated = what
                    DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
                        self.updated = nil
                        self.build()
                    }
                case .upToDate:
                    log("already up to date")
                case .failed(let why):
                    self.alert("Обновиться не вышло", why)
                }
                self.build()
            }
        }
    }

    /// Books move between this Mac and iCloud Drive/readsync with the server stopped, so nothing is
    /// half-written while the folder changes place. Every step says what it is about to do first. What
    /// takes time before the question (running jobs, the library's size, the room in iCloud) is asked off
    /// the main thread, the menu saying so in grey meanwhile.
    @objc private func toggleCloud() {
        guard python != nil, cloudBusy == nil else { return }
        cloudBusy = "Библиотека в iCloud: проверяю…"
        build()
        let url = server.isRunning ? server.url : nil
        let moving = !Cloud.isOn && !Cloud.hasLibrary  // only a move into iCloud needs the sizes
        DispatchQueue.global(qos: .userInitiated).async {
            let jobs = Menu.runningJobs(at: url)
            let need = moving ? Cloud.localBytes : 0
            let free = moving ? Cloud.freeBytes : nil
            DispatchQueue.main.async {
                self.cloudBusy = nil
                self.build()
                self.switchCloud(jobs: jobs, need: need, free: free)
            }
        }
    }

    private func switchCloud(jobs: [String], need: Int64, free: Int64?) {
        guard let python else { return }
        // a job writes into the library while it runs and outlives the server: the folder stays put until it ends
        if !jobs.isEmpty {
            alert("Книги ещё загружаются", "Библиотеку можно перенести, когда закончится: \(jobs.joined(separator: ", ")).")
            return
        }
        if Cloud.isOn {
            let sure =
                Cloud.isAdopted
                ? confirm(
                    "Отключить библиотеку iCloud на этом Mac?",
                    "Книги останутся в iCloud Drive и на iPhone, этот Mac снова откроет свои прежние книги.",
                    yes: "Отключить")
                : confirm(
                    "Вернуть книги на этот Mac?", "Библиотека переедет из iCloud Drive обратно. На iPhone книги пропадут.",
                    yes: "Вернуть книги")
            guard sure else { return }
            server.stop()
            if case .failed(let why) = Cloud.turnOff() { alert("Не вышло вернуть книги", why) }
        } else if Cloud.hasLibrary {
            guard confirm(
                "В iCloud уже есть библиотека readsync",
                "Открыть её на этом Mac? Здешние книги останутся на месте и вернутся, если выключить iCloud.",
                yes: "Открыть")
            else { return }
            server.stop()
            if case .failed(let why) = Cloud.turnOn(adopt: true) { alert("Не вышло открыть библиотеку", why) }
        } else {
            if let free, free < need {
                alert("В iCloud не хватает места", "Нужно \(Menu.size(need)), свободно \(Menu.size(free)).")
                return
            }
            guard confirm(
                "Перенести библиотеку в iCloud Drive?",
                "Книги (\(Menu.size(need))) переедут в iCloud Drive/readsync и будут видны на iPhone.",
                yes: "Перенести")
            else { return }
            server.stop()
            if case .failed(let why) = Cloud.turnOn(adopt: false) {
                alert("Не вышло перенести книги", why)
            } else if confirm(
                "Библиотека в iCloud",
                "Чтобы macOS не выгружала аудио с этого Mac, в Finder нажми на iCloud Drive/readsync "
                    + "правой кнопкой и выбери «Не выгружать».",
                yes: "Показать в Finder", no: "Готово")
            {
                NSWorkspace.shared.activateFileViewerSelecting([Cloud.library.deletingLastPathComponent()])
            }
        }
        server.start(python: python)
        build()
    }

    /// Books being loaded right now, by slug: the ones the server reports, and the ones whose job holds its
    /// work dir (a job outlives the server, so a stopped server does not mean nothing is loading). Asked
    /// with a short wait, off the main thread.
    private static func runningJobs(at url: URL?) -> [String] {
        final class Box: @unchecked Sendable { var slugs: [String] = [] }
        let box = Box()
        if let url {
            let answered = DispatchSemaphore(value: 0)
            let request = URLRequest(url: url.appendingPathComponent("api/jobs"), timeoutInterval: 3)
            URLSession.shared.dataTask(with: request) { data, _, _ in
                if let data, let jobs = (try? JSONSerialization.jsonObject(with: data)) as? [String: [String: Any]] {
                    box.slugs = jobs.filter { ($0.value["running"] as? Bool) == true }.map(\.key)
                }
                answered.signal()
            }.resume()
            _ = answered.wait(timeout: .now() + 4)
        }
        return Array(Set(box.slugs + heldJobs())).sorted()
    }

    /// Work dirs a live job holds: each job writes its pid there (pipeline/tidy.py `claim`).
    private static func heldJobs() -> [String] {
        let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/readsync/jobs")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
        return names.filter { name in
            let file = root.appendingPathComponent(name).appendingPathComponent("pid")
            guard let text = try? String(contentsOf: file, encoding: .utf8),
                let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
            else { return false }
            return kill(pid, 0) == 0 || errno == EPERM
        }
    }

    /// The buttons say what they do (macOS's own rule): «Перенести», not «Да».
    private func confirm(_ title: String, _ text: String, yes: String, no: String = "Отмена") -> Bool {
        let sheet = NSAlert()
        sheet.messageText = title
        sheet.informativeText = text
        sheet.addButton(withTitle: yes)
        sheet.addButton(withTitle: no)
        NSApp.activate(ignoringOtherApps: true)
        return sheet.runModal() == .alertFirstButtonReturn
    }

    static func size(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
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

    /// The book on the shelf «читаю сейчас», so it is one click away from the menu bar. The shelf as the
    /// library page counts it (reader/library.js `shelfOf`): one put there by hand, or one read for more
    /// than ten minutes and neither finished nor moved off it.
    private func lookForReading() {
        guard server.isRunning else { return }
        let url = server.url.appendingPathComponent("api/books")
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data,
                let books = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
            else { return }
            let now = books.filter { book in
                let state = book["state"] as? [String: Any]
                let shelf = state?["shelf"] as? String ?? ""
                let seconds = (state?["seconds"] as? Double) ?? 0
                let finished = (state?["finished"] as? Bool) ?? false
                let reading = shelf.isEmpty ? !finished && seconds > 600 : shelf == "reading"
                return reading && (book["ready"] as? Bool) == true
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
