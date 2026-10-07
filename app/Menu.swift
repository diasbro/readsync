// The menu bar item: the mark, what it offers, and what each item does. What a reader does daily is
// at the top and needs one click; everything that is set once and forgotten sits under «Настройки».

import AppKit
import ServiceManagement

final class Menu: NSObject, NSApplicationDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let server = Server()
    private let menu = NSMenu()
    private var python: String?
    // Three long jobs, one at a time: the tools being installed, git (`busy`), the library moving (`cloudBusy`).
    // While one runs the server is not started from the menu, and a job that ends under another leaves the
    // start to the last one to end (`startWhenFree`).
    private var toolsBusy = false { didSet { startWhenFree() } }
    private var behind = 0
    private var busy: String? { didSet { startWhenFree() } }  // what git is doing right now, while it is doing it
    private var updated = false  // an update just landed, said for a minute after it
    private var cloudBusy: String? { didSet { startWhenFree() } }  // «Синхронизация» looking at the library or moving it
    private var occupied: Bool { toolsBusy || busy != nil || cloudBusy != nil }
    private var startPending = false  // a job stopped the server, or needs it started again, once nothing else runs
    private var reading: (title: String, slug: String)?
    private let gitQueue = DispatchQueue(label: "readsync.git")  // one at a time, never on the main thread

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.image = Menu.mark()
        item.menu = menu
        menu.delegate = self
        menu.autoenablesItems = false  // the update item says «занято» by being disabled, so AppKit must not decide
        log("menu bar item ready, visible=\(item.isVisible), icon=\(item.button?.image?.size ?? .zero)")
        build()
        // copying the code out and trying each Python take seconds: off the main thread, the menu opens meanwhile
        DispatchQueue.global(qos: .userInitiated).async {
            Payload.install()
            let python = findPython()
            DispatchQueue.main.async { self.ready(python) }
        }
    }

    /// The rest of the launch, once a Python is known. Updates are looked for only when the reader asks
    /// (the menu item): nothing goes to the network on its own.
    private func ready(_ python: String?) {
        self.python = python
        guard let python else {
            alert(
                "Нужен Python 3.12 или новее",
                "Поставь его и запусти readsync снова:\n\nbrew install python@3.13")
            NSApp.terminate(nil)
            return
        }
        toolsBusy = true
        Payload.ensureTools(python: python) { [weak self] installed in
            guard let self else { return }
            if installed { self.startPending = true }  // the server finds the new packages only when it starts
            self.toolsBusy = false
            self.build()
        }
        server.start(python: python)
        build()
        openFirstTime()
        // asked once the server is up, so the first opening of the menu already knows the book
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.lookForReading() }
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
            add("Сервер не запущен, запустить", "", #selector(restart)).isEnabled = !occupied
        }
        let updateItem = add(
            busy ?? (behind > 0 ? "Обновить: есть новое (\(behind))" : "Проверить обновления…"), "", #selector(update))
        updateItem.isEnabled = !occupied
        if updated, busy == nil {
            menu.addItem(withTitle: "Обновлено. Открытую вкладку перезагрузи", action: nil, keyEquivalent: "")
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
        // turned on but not yet allowed: the switch says where to allow it, and a click opens that place
        let status = SMAppService.mainApp.status
        let login = add(
            status == .requiresApproval ? "Запускать при входе: разреши в настройках «Объекты входа»…" : "Запускать при входе",
            "", #selector(toggleLogin), to: sub)
        login.state = status == .enabled ? .on : status == .requiresApproval ? .mixed : .off
        if Browser.canReuseTab {
            let reuse = add("Открывать в той же вкладке", "", #selector(toggleReuse), to: sub)
            reuse.state = Browser.reusesTab ? .on : .off
        }
        if Cloud.isAvailable {
            let cloud = add(cloudBusy ?? "Синхронизация", "", #selector(toggleCloud), to: sub)
            cloud.state = Cloud.isOn ? .on : .off
            cloud.isEnabled = !occupied
        }
        sub.addItem(.separator())
        add("Показать книги в Finder", "", #selector(showBooks), to: sub)
        add("Открыть журнал", "", #selector(showLog), to: sub)
        add("Перезапустить сервер", "", #selector(restart), to: sub).isEnabled = !occupied
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

    /// The very first launch opens the library by itself once the server answers (up to ten seconds),
    /// so a new reader sees where to start rather than a dot in the menu bar. Never again after that,
    /// and never for a library that already has books: its reader knows the way.
    private func openFirstTime() {
        let key = "opened"
        guard !UserDefaults.standard.bool(forKey: key), server.isRunning else { return }
        let books = (try? FileManager.default.contentsOfDirectory(atPath: booksDir.resolvingSymlinksInPath().path)) ?? []
        if books.contains(where: { !$0.hasPrefix(".") }) {
            UserDefaults.standard.set(true, forKey: key)
            return
        }
        let url = server.url
        DispatchQueue.global(qos: .userInitiated).async {
            guard answers(url, within: 10) else { log("the server did not answer: the library opens next launch"); return }
            DispatchQueue.main.async {
                UserDefaults.standard.set(true, forKey: key)
                Browser.open(url)
            }
        }
    }

    @objc private func openReading() {
        guard let slug = reading?.slug else { return }
        guard let url = URL(string: "\(server.url.absoluteString)?book=\(slug)") else { return }
        Browser.open(url)
    }

    @objc private func restart() {
        guard !occupied else { return }  // a long job holds the server: it starts it again when it ends
        reading = nil  // asked again at the next opening, from the server that runs now
        if let python { server.start(python: python) }
        build()
    }

    /// The server a job asked for, started once no long job runs any more.
    private func startWhenFree() {
        guard startPending, !occupied, let python else { return }
        startPending = false
        reading = nil
        server.start(python: python)
        build()
    }

    @objc private func toggleLogin() {
        do {
            switch SMAppService.mainApp.status {
            case .enabled:
                try SMAppService.mainApp.unregister()
            case .requiresApproval:
                SMAppService.openSystemSettingsLoginItems()
            default:
                try SMAppService.mainApp.register()
                // registered, but the system wants the reader's yes first: show where to give it
                if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
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
        guard !occupied else { return }
        let known = behind > 0
        busy = known ? "Обновление…" : "Проверка обновлений…"
        updated = false
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
            var stopped = false
            let result = Payload.update {
                // the server stops before the code changes, so no book starts loading between this look and
                // the reset; a job outlives the server and holds its work dir
                if let old = DispatchQueue.main.sync(execute: { self.server.halt() }) { Server.reap(old) }
                stopped = true
                let held = Menu.heldJobs()
                return held.isEmpty ? nil : "Книги ещё загружаются: \(held.joined(separator: ", ")). Обнови, когда закончится."
            }
            DispatchQueue.main.async {
                if stopped { self.startPending = true }
                self.busy = nil
                switch result {
                case .updated(let what):
                    log("updated: \(what)")
                    self.behind = 0
                    self.updated = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 60) {
                        self.updated = false
                        self.build()
                    }
                case .upToDate:
                    log("already up to date")
                    self.behind = 0
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
        guard python != nil, !occupied else { return }
        cloudBusy = "Синхронизация: проверяю…"
        build()
        let url = server.isRunning ? server.url : nil
        // a move into iCloud needs the sizes, measured for any turning on, since what iCloud holds may change
        // before the question; so does a turning off that copies the library here
        let moving = !Cloud.isOn
        let copying = Cloud.isOn && !Cloud.isAdopted
        DispatchQueue.global(qos: .userInitiated).async {
            let jobs = Menu.runningJobs(at: url)
            let need = moving || copying ? Cloud.localBytes : 0
            let free = moving ? Cloud.freeBytes : copying ? Cloud.localFreeBytes : nil
            DispatchQueue.main.async {
                self.cloudBusy = nil
                self.build()
                self.switchCloud(jobs: jobs, need: need, free: free)
            }
        }
    }

    private func switchCloud(jobs: [String], need: Int64, free: Int64?) {
        guard python != nil else { return }
        // a job writes into the library while it runs and outlives the server: the folder stays put until it ends
        if !jobs.isEmpty {
            alert("Книги ещё загружаются", "Синхронизацию можно переключить, когда закончится: \(jobs.joined(separator: ", ")).")
            return
        }
        let work: () -> Cloud.Move
        let failure: String
        var after: (() -> Void)?
        if Cloud.isOn {
            if !Cloud.isAdopted, let free, free < need {
                alert("Недостаточно места", "Чтобы скопировать книги сюда, нужно \(Menu.size(need)), свободно \(Menu.size(free)).")
                return
            }
            let sure =
                Cloud.isAdopted
                ? confirm(
                    "Выключить синхронизацию?",
                    "Книги останутся в iCloud Drive, здесь снова откроются прежние.",
                    yes: "Выключить")
                : confirm(
                    "Выключить синхронизацию?", "Книги останутся в iCloud Drive, а сюда скопируются и больше не будут синхронизироваться.",
                    yes: "Скопировать книги")
            guard sure else { return }
            work = Cloud.turnOff
            failure = "Не вышло скопировать книги"
        } else if Cloud.hasLibrary {
            guard confirm(
                "В iCloud Drive уже есть библиотека readsync",
                "Открыть её? Здешние книги останутся на месте и вернутся, если выключить синхронизацию.",
                yes: "Открыть")
            else { return }
            work = { Cloud.turnOn(adopt: true) }
            failure = "Не вышло открыть библиотеку"
        } else {
            if let free, free < need {
                alert("В iCloud не хватает места", "Нужно \(Menu.size(need)), свободно \(Menu.size(free)).")
                return
            }
            guard confirm(
                "Включить синхронизацию?",
                "Книги (\(Menu.size(need))) переедут в iCloud Drive/readsync.",
                yes: "Перенести")
            else { return }
            work = { Cloud.turnOn(adopt: false) }
            failure = "Не вышло перенести книги"
            after = {
                if self.confirm(
                    "Синхронизация включена",
                    "Чтобы аудио не выгружалось с диска, в Finder нажми на iCloud Drive/readsync "
                        + "правой кнопкой и выбери «Не выгружать».",
                    yes: "Показать в Finder", no: "Готово")
                {
                    NSWorkspace.shared.activateFileViewerSelecting([Cloud.library.deletingLastPathComponent()])
                }
            }
        }
        // the server stops and the books move off the main thread, the menu saying so in grey meanwhile
        let old = server.halt()
        reading = nil  // the library changes: asked again from the one served next
        startPending = true  // started again once the books have moved, or once whatever else runs then ends
        cloudBusy = "Синхронизация: переношу книги…"
        build()
        DispatchQueue.global(qos: .userInitiated).async {
            if let old { Server.reap(old) }
            // a book started loading while the question was open: it holds its work dir, the folder waits for it
            let held = Menu.heldJobs()
            let result = held.isEmpty ? work() : nil
            DispatchQueue.main.async {
                self.cloudBusy = nil
                self.build()
                switch result {
                case nil:
                    self.alert(
                        "Книги ещё загружаются",
                        "Синхронизацию можно переключить, когда закончится: \(held.joined(separator: ", ")).")
                case .failed(let why):
                    self.alert(failure, why)
                case .done:
                    after?()
                    self.build()
                }
            }
        }
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
    static func heldJobs() -> [String] {
        let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/readsync/jobs")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
        return names.filter { name in
            let file = root.appendingPathComponent(name).appendingPathComponent("pid")
            return holds(file) != nil  // a pid the system has given to another process since does not count
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

    /// The book on the shelf «Читаю сейчас», so it is one click away from the menu bar: the one the library
    /// page puts first there (reader/library.js `paint`), by the status the server gives each book, the most
    /// recently opened (or added) first.
    private func lookForReading() {
        guard server.isRunning else {
            reading = nil
            return
        }
        let url = server.url.appendingPathComponent("api/books")
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data,
                let books = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]]
            else {
                DispatchQueue.main.async { self.reading = nil }  // no answer: no book to offer, not an old one
                return
            }
            let now = books.filter { book in
                (book["state"] as? [String: Any])?["status"] as? String == "reading" && (book["ready"] as? Bool) == true
            }
            let at = { (x: [String: Any]) -> Double in
                let opened = ((x["state"] as? [String: Any])?["opened"] as? Double) ?? 0
                return opened != 0 ? opened : (x["added"] as? Double) ?? 0
            }
            let newest = now.min { a, b in
                at(a) != at(b) ? at(a) > at(b) : ((a["title"] as? String) ?? "").localizedCompare((b["title"] as? String) ?? "") == .orderedAscending
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
