// readsync on the phone: the library, and the reader for any book copied to the phone. Text books are
// added here from files (fb2, epub, pdf, txt); audiobooks come only from the shared library.

import SwiftUI
import UniformTypeIdentifiers

@main
struct ReadsyncApp: App {
    @StateObject private var shelf = Shelf.shared
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            LibraryView()
                .environmentObject(shelf)
                .onOpenURL { shelf.add([$0]) }  // «Открыть в readsync» from another app
        }
        .onChange(of: phase) { _, now in
            switch now {
            case .active:
                Player.shared.appDidBecomeActive()
                Task { await shelf.refresh() }
            case .inactive, .background:
                Player.shared.appWillResignActive()
            @unknown default:
                break
            }
        }
    }
}

// ---- the library ----

struct LibraryView: View {
    @EnvironmentObject var shelf: Shelf
    @State private var reading: String?
    @State private var openWhenReady: String?  // tapped while not on the phone yet: opens as soon as it is
    @State private var picking = false
    @State private var pick = Pick.books  // what the system's picker is open for
    @State private var deleting: Book?
    @State private var askICloud = false  // «Синхронизация» switched on with the library not in iCloud Drive
    @State private var askBring: Bring?  // a library opened in iCloud Drive: the books added here go into it?
    @State private var notice: String?  // how the move ended: one short line
    @AppStorage(Shelf.dropReadKey) private var dropRead = false
    @AppStorage(Shelf.syncKey) private var sync = true
    @AppStorage("libView") private var libView = "list"  // this phone's own: «list» or «covers»
    @AppStorage("showRead") private var showRead = false
    @Environment(\.dynamicTypeSize) private var textSize
    @Environment(\.scenePhase) private var phase

    var body: some View {
        NavigationStack {
            Group {
                if shelf.books.isEmpty && shelf.adding.isEmpty {
                    empty
                } else if libView == "covers" {
                    grid
                } else {
                    list
                }
            }
            .navigationTitle("Библиотека")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Добавить книгу", systemImage: "plus") { choose(.books) }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Вид", selection: $libView) {
                            Label("Списком", systemImage: "list.bullet").tag("list")
                            Label("Обложками", systemImage: "square.grid.2x2").tag("covers")
                        }
                        .pickerStyle(.palette)
                        // the source under «Открыть», as the button's subtitle: one line, next to what changes it
                        Button { choose(.folder) } label: {
                            Text("Открыть")
                            Text(shelf.sourceLabel)
                            Image(systemName: "folder")
                        }
                        Button("Обновить", systemImage: "arrow.clockwise") { Task { await shelf.refresh() } }
                        Toggle("Синхронизация", systemImage: "arrow.triangle.2.circlepath", isOn: syncSwitch)
                        Toggle("Скрывать прочитанные", systemImage: "archivebox", isOn: $dropRead)
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                    .tint(Color.accentColor)  // the menu's icons in the app's colour, not the system blue
                }
            }
            .fileImporter(isPresented: $picking, allowedContentTypes: pick == .books ? Self.bookTypes : [.folder],
                          allowsMultipleSelection: pick == .books) { result in
                guard case .success(let urls) = result else { return }
                switch pick {
                case .books: shelf.add(urls)
                case .folder: if let url = urls.first { opened(url, syncing: false) }
                case .iCloud: if let url = urls.first { opened(url, syncing: true) }
                case .moveTo:
                    guard let url = urls.first else { return }
                    Task {
                        notice = await shelf.moveLibrary(into: url)
                        if shelf.inICloud { sync = true }
                    }
                }
            }
            .onChange(of: dropRead) { _, on in if on { Task { await shelf.refresh() } } }
            .onChange(of: sync) { _, on in
                if on { Task { await shelf.sync() } } else { shelf.stopSync() }
            }
            .alert("Синхронизация через iCloud", isPresented: $askICloud) {
                // the books go into the folder picked, book by book: a library there already (the Mac's) keeps
                // all it has, and nothing in it is replaced
                Button("Перенести библиотеку") { choose(.moveTo) }
                Button("Открыть папку в iCloud Drive") { choose(.iCloud) }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("Выбери папку в iCloud Drive: книги перенесутся в неё и будут на всех устройствах.")
            }
            .alert("Перенести книги в эту библиотеку?", isPresented: bringShown, presenting: askBring) { bring in
                Button("Перенести") { Task { notice = await shelf.bring(bring.slugs, already: bring.already) } }
                Button("Оставить", role: .cancel) {}
            } message: { bring in
                Text("В прежней библиотеке \(bring.slugs.count) \(booksWord(bring.slugs.count)).")
            }
            .alert(notice ?? "", isPresented: noticeShown) {
                Button("OK", role: .cancel) {}
            }
            .modifier(ImportDialogs())
            .onChange(of: shelf.toOpen) { _, _ in openAdded() }
            .onChange(of: reading) { _, now in if now == nil { openAdded() } }
            .onChange(of: shelf.copies) { _, copies in
                guard let slug = openWhenReady else { return }
                switch copies[slug] ?? .absent {
                case .here, .textOnly:
                    openWhenReady = nil
                    reading = slug
                case .absent, .failed:
                    openWhenReady = nil  // it did not come: a later download must not open it out of the blue
                    Player.shared.dropAutoplay(slug)  // nor play it
                default:
                    break
                }
            }

            .fullScreenCover(item: Binding(get: { reading.map(Slug.init) }, set: { reading = $0?.id })) { item in
                ReaderView(slug: item.id) {
                    Player.shared.readerClosed(item.id)  // saved here, before the library reads the place back
                    reading = nil
                    Task {
                        await Player.shared.flush()
                        await shelf.refresh()
                    }
                }
                .ignoresSafeArea()
                .statusBarHidden()
                .background(Color(.systemBackground))
            }
        }
        .tint(.accentColor)
        // here, on the screen and not on the list or the grid inside it: a modifier on that Group goes to each
        // of them, and switching the view ran a refresh (and a sync round) every time
        .task { await shelf.refresh() }
        // while the library is open and «Синхронизация» is on, a book added elsewhere, or one whose files had
        // not come yet, is picked up within a minute, without the reader doing anything. Started again with
        // each change of the phase: a task reads the phase it was started with, never a later one
        .task(id: phase) {
            guard phase == .active else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(60))
                if !Task.isCancelled, sync, shelf.inICloud, reading == nil { await shelf.refresh() }
            }
        }
    }

    /// A book just added, opened when the reader is not open on another one; else once it is closed.
    private func openAdded() {
        guard reading == nil, let slug = shelf.toOpen else { return }
        shelf.toOpen = nil
        if let book = shelf.books.first(where: { $0.slug == slug }) { open(book) }
    }

    private var list: some View {
        let reading = shelf.reading
        let (all, read) = Shelf.sections(shelf.books, progress: shelf.progress, reading: Set(reading.map(\.slug)))
        return List {
            if let round = shelf.round {
                SyncLine(round: round, copies: shelf.copies)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 0, leading: 20, bottom: 8, trailing: 20))
            }
            if !shelf.message.isEmpty {
                message.listRowSeparator(.hidden)
            }
            ForEach(shelf.adding) { item in
                AddingRow(item: item) { shelf.cancelAdd(item.id) }
            }
            // one section with its titles as rows: plain sections would leave a wide gap between them
            if let current = reading.first {
                SectionTitle("Читаю сейчас")
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 4, leading: 20, bottom: 0, trailing: 20))
                nowReading(current)
                    .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 12, trailing: 16))
                    .listRowSeparator(.hidden)
                ForEach(reading.dropFirst()) { book in
                    row(book)
                }
                SectionTitle("Все книги")
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 8, leading: 20, bottom: 0, trailing: 20))
            }
            ForEach(all) { book in
                row(book)
            }
            if !read.isEmpty, !dropRead {  // «Скрывать прочитанные»: no shelf of them at all
                readTitle(read)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 18, leading: 20, bottom: 6, trailing: 20))
                if showRead {
                    ForEach(read) { book in
                        row(book)
                    }
                }
            }
            if !shelf.localBytes.isEmpty {
                footer
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 14, leading: 20, bottom: 14, trailing: 20))
            }
        }
        .listStyle(.plain)
        .environment(\.defaultMinListRowHeight, 0)  // the section titles are rows: no 44-point floor under them
        .modifier(chrome)
    }

    /// «Обложками»: the same library, its books as a grid of covers under «Читаю сейчас».
    private var grid: some View {
        let reading = shelf.reading
        let (all, read) = Shelf.sections(shelf.books, progress: shelf.progress, reading: Set(reading.map(\.slug)))
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if let round = shelf.round {
                    SyncLine(round: round, copies: shelf.copies).padding(.horizontal, 20).padding(.bottom, 12)
                }
                if !shelf.message.isEmpty {
                    message.padding(.horizontal, 20).padding(.bottom, 12)
                }
                ForEach(shelf.adding) { item in
                    AddingRow(item: item) { shelf.cancelAdd(item.id) }.padding(.horizontal, 20).padding(.bottom, 12)
                }
                if let current = reading.first {
                    SectionTitle("Читаю сейчас").padding(.horizontal, 20).padding(.top, 4)
                    nowReading(current).padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 12)
                    if reading.count > 1 { tiles(Array(reading.dropFirst())).padding(.top, 4).padding(.bottom, 12) }
                    SectionTitle("Все книги").padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 12)
                }
                tiles(all)
                if !read.isEmpty, !dropRead {
                    readTitle(read).padding(.horizontal, 20).padding(.top, 28).padding(.bottom, showRead ? 12 : 0)
                    if showRead { tiles(read) }
                }
                if !shelf.localBytes.isEmpty {
                    footer.padding(.horizontal, 20).padding(.vertical, 14)
                }
            }
            .padding(.bottom, 8)
        }
        .modifier(chrome)
    }

    /// What both views have around the books: pull to refresh, and the narrator under them.
    private var chrome: some ViewModifier {
        Chrome(refresh: { await shelf.refresh() }) {
            openWhenReady = nil
            reading = $0
        }
    }

    /// «Синхронизация» switched on: straight on, with the library in iCloud Drive; else the question first.
    private var syncSwitch: Binding<Bool> {
        // sync is through iCloud: with the source elsewhere the switch reads off whatever was saved
        Binding(get: { sync && shelf.inICloud }, set: { on in
            if on, !shelf.inICloud { askICloud = true } else { sync = on }
        })
    }

    private var bringShown: Binding<Bool> { Binding(get: { askBring != nil }, set: { if !$0 { askBring = nil } }) }
    private var noticeShown: Binding<Bool> { Binding(get: { notice != nil }, set: { if !$0 { notice = nil } }) }

    /// The picker for books (fb2, fb2.zip, epub, pdf, txt), or for a library folder: any, or one in iCloud
    /// Drive from «Синхронизация», to open or to move the library into.
    enum Pick { case books, folder, iCloud, moveTo }

    static let bookTypes: [UTType] = {
        let fb2 = [UTType(filenameExtension: "fb2"), UTType("org.gribuser.fictionbook")].compactMap { $0 }
        return fb2 + [.epub, .zip, .pdf, .plainText]
    }()

    private func choose(_ kind: Pick) {
        pick = kind
        picking = true
    }

    /// A folder opened: the library from now on, and the question whether the books added here go into it.
    /// `syncing`: opened from «Синхронизация», which goes on when the folder is in iCloud Drive.
    private func opened(_ url: URL, syncing: Bool) {
        Task {
            // from «Синхронизация»: a folder with no library in it gets readsync/books, as a move would make
            let chosen = syncing ? await shelf.chooseLibrary(url) : shelf.choose(folder: url)
            guard chosen, let library = shelf.booksRoot else { return }
            if syncing {
                if shelf.inICloud { sync = true } else { notice = "Папка не в iCloud Drive — синхронизация выключена" }
            }
            let own = shelf.ownLibrary
            let slugs = await Task.detached { Shelf.booksToBring(from: own, to: library) }.value
            if !slugs.isEmpty { askBring = Bring(slugs: slugs, already: Shelf.ownBookCount(own) - slugs.count) }
        }
    }

    private var message: some View {
        Label(shelf.message, systemImage: shelf.folderLost ? "folder.badge.questionmark" : "exclamationmark.circle")
            .font(.footnote).foregroundStyle(.secondary)
    }

    private func nowReading(_ current: Book) -> some View {
        // the audio of the copy on the phone, or, none there yet, of the library's book
        let audio = shelf.localBook(current.slug).map(\.hasAudio) ?? current.hasAudio
        let listen = audio && shelf.progress[current.slug]?.pages != true
        return NowReading(book: current, progress: shelf.progress[current.slug]?.fraction ?? 0, listen: listen) {
            if listen { Player.shared.playWhenOpened(current.slug) }
            open(current)
        }
        .contextMenu {  // a text-only one gets its audio here
            statusMenu(current)
            actions(current, shelf.copy(of: current.slug))
        }
        .modifier(askDelete(current))
    }

    private var footer: some View {
        let n = shelf.localBytes.count
        return Text("Загружено: \(n) \(booksWord(n)) · \(size(shelf.localBytes.values.reduce(0, +)))")
            .font(.caption).foregroundStyle(.tertiary).monospacedDigit()
            .frame(maxWidth: .infinity)
    }

    /// «Прочитанные · 12», with how many of them this year: a tap folds them away or out.
    private func readTitle(_ read: [Book]) -> some View {
        let year = Shelf.utcYear(Date())  // the days a book was finished on are UTC days
        let thisYear = Shelf.readIn(year, read, progress: shelf.progress)
        return Button {
            withAnimation(.easeOut(duration: 0.2)) { showRead.toggle() }
        } label: {
            HStack(spacing: 8) {
                SectionTitle("Прочитанные · \(read.count)")
                Spacer(minLength: 8)
                if thisYear > 0 {
                    Text("в \(year) — \(thisYear)").font(.caption).foregroundStyle(.tertiary).monospacedDigit()
                }
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.semibold)).foregroundStyle(.tertiary)
                    .rotationEffect(.degrees(showRead ? 90 : 0))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(showRead ? "развёрнуто" : "свёрнуто")
    }

    private func tiles(_ books: [Book]) -> some View {
        let column = GridItem(.adaptive(minimum: textSize.isAccessibilitySize ? 150 : 100, maximum: 170), spacing: 14, alignment: .top)
        return LazyVGrid(columns: [column], spacing: 20) {
            ForEach(books) { book in
                tile(book)
            }
        }
        .padding(.horizontal, 20)
    }

    private func tile(_ book: Book) -> some View {
        let copy = shelf.copy(of: book.slug)
        return BookTile(book: book, copy: copy, progress: shelf.progress[book.slug] ?? Progress(), detached: detached(book)) {
            open(book)
        } cancel: {
            shelf.cancel(book.slug)
        } actions: {
            statusMenu(book, title: book.title)  // no title under the cover: the menu names the book
            actions(book, copy)
        }
        .contextMenu {
            statusMenu(book, title: book.title)
            actions(book, copy)
        }
        .modifier(askDelete(book))
    }

    private func row(_ book: Book) -> some View {
        let copy = shelf.copy(of: book.slug)
        // a tap, not a Button: the ring of a download is a button of its own inside the row
        return BookRow(book: book, copy: copy, progress: shelf.progress[book.slug] ?? Progress(), detached: detached(book)) {
            shelf.cancel(book.slug)
        } update: {
            shelf.fetch(book)
        }
        .onTapGesture { open(book) }
        .accessibilityAddTraits(.isButton)
        // the first is the full swipe: a new version comes in, nothing goes without the dialog
        .swipeActions(edge: .trailing) { actions(book, copy) }
        .contextMenu {
            statusMenu(book)
            actions(book, copy)
        }
        .modifier(askDelete(book))
    }

    /// A copy whose book the library does not have (any more): read, but nothing of it is saved. With the
    /// folder out of reach the message over the list says so for all of them.
    private func detached(_ book: Book) -> Bool {
        !shelf.folderLost && !shelf.inLibrary.contains(book.slug) && shelf.copy(of: book.slug).isReadable
    }

    /// «Удалить»: from this phone only, or from the library on every device. Shown at the book's row.
    private func askDelete(_ book: Book) -> some ViewModifier {
        DeleteDialog(book: book, shown: Binding(get: { deleting?.slug == book.slug }, set: { if !$0 { deleting = nil } }))
    }

    /// «Читаю · Отложена · Прочитана», the current one ticked, and «Перечитать» for a read book. Written to
    /// the shared library: with its folder out of reach there is nowhere to write, and the choice is off.
    @ViewBuilder private func statusMenu(_ book: Book, title: String? = nil) -> some View {
        let status = shelf.progress[book.slug]?.status ?? .none
        let mark = { (s: BookStatus) in
            Binding(get: { status == s }, set: { if $0 { shelf.setStatus(book, s) } })
        }
        let items = Group {
            Toggle("Читаю", systemImage: "book", isOn: mark(.reading))
            Toggle("Отложена", systemImage: "pause.circle", isOn: mark(.paused))
            Toggle("Прочитана", systemImage: "checkmark.circle", isOn: mark(.done))
            if status == .done {
                Button("Перечитать", systemImage: "arrow.counterclockwise") { shelf.reread(book) }
            }
        }
        .disabled(shelf.sharedDir(book.slug) == nil)
        if let title {
            Section(title) { items }
        } else {
            Section { items }
        }
    }

    @ViewBuilder private func actions(_ book: Book, _ copy: Copy) -> some View {
        if copy == .outdated {
            Button("Обновить", systemImage: "arrow.down.circle") { shelf.fetch(book) }.tint(.accentColor)
        }
        if case .fetching = copy {
            Button("Отменить", systemImage: "xmark") { shelf.cancel(book.slug) }.tint(.gray)
        }
        Button("Удалить", systemImage: "trash") { deleting = book }.tint(.red)
        // an audiobook to read as pages: its text without the hundreds of megabytes of audio
        if book.hasAudio, copy == .absent || copy.isFailed {
            Button("Скачать без звука", systemImage: "text.book.closed") { shelf.fetch(book, textOnly: true) }.tint(.indigo)
        }
        if copy == .textOnly {
            Button("Скачать со звуком", systemImage: "headphones") { shelf.fetch(book, textOnly: false) }.tint(.accentColor)
        }
    }

    @ViewBuilder private var empty: some View {
        if shelf.folderLost {
            ContentUnavailableView {
                Label("Папка библиотеки недоступна", systemImage: "folder.badge.questionmark")
            } description: {
                Text("Открой её снова или вернись к своей библиотеке.")
            } actions: {
                Button("Открыть снова") { choose(.folder) }
                    .buttonStyle(.borderedProminent)
                Button("Вернуться к своей библиотеке") { shelf.forgetFolder() }
                    .buttonStyle(.borderless)
            }
        } else {
            ContentUnavailableView {
                Label("Пока нет книг", systemImage: "books.vertical")
            } description: {
                Text("Добавь книгу: fb2, epub, pdf или txt.")
            } actions: {
                Button("Добавить книгу") { choose(.books) }
                    .buttonStyle(.borderedProminent)
            }
        }
    }

    private func open(_ book: Book) {
        switch shelf.copy(of: book.slug) {
        case .here, .textOnly, .outdated:
            openWhenReady = nil  // a fetch that ends later must not swap the book being read
            reading = book.slug  // a newer version waits for its own swipe
        case .fetching:
            openWhenReady = book.slug
        case .absent, .failed:
            openWhenReady = book.slug
            shelf.fetch(book)
        }
    }
}

/// «Загрузка · 4 из 15» under the title while a round runs, its bytes as a thin bar.
struct SyncLine: View {
    let round: SyncRound
    let copies: [String: Copy]

    var body: some View {
        let (done, total, fraction) = round.count(copies: copies)
        if total > 0 {
            VStack(alignment: .leading, spacing: 6) {
                Text("Загрузка · \(done) из \(total)")
                    .font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                ProgressView(value: fraction).tint(Color.accentColor)
            }
            .accessibilityElement(children: .combine)
        }
    }
}

/// The books added here to bring into a library opened in iCloud Drive; `already`: those it has.
struct Bring {
    let slugs: [String]
    let already: Int
}

/// A file being made into a book: its name, «Добавляю…», and a square to call it off.
struct AddingRow: View {
    let item: Adding
    let cancel: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            RoundedRectangle(cornerRadius: 5, style: .continuous)
                .fill(Color.accentColor.opacity(0.15))
                .frame(width: 52, height: 52 * 1.45)
                .overlay { ProgressView() }
            VStack(alignment: .leading, spacing: 4) {
                Text(item.name)
                    .font(.system(.body, design: .serif, weight: .medium))
                    .lineLimit(2)
                Text("Добавляю…").font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Button(action: cancel) {
                Image(systemName: "stop.circle").font(.title3).foregroundStyle(Color.accentColor)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("Отменить")
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }
}

/// What adding books has to say: the files that did not become books, and a book the library has already.
struct ImportDialogs: ViewModifier {
    @EnvironmentObject var shelf: Shelf

    func body(content: Content) -> some View {
        let failed = Binding(get: { shelf.importNotice != nil }, set: { if !$0 { shelf.importNotice = nil } })
        let twin = shelf.duplicates.first
        content
            .alert(shelf.importNotice?.title ?? "", isPresented: failed) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(shelf.importNotice?.message ?? "")
            }
            .alert(twin.map { "«\($0.title)» уже есть" } ?? "", isPresented: .constant(twin != nil), presenting: twin) { d in
                Button("Открыть") { shelf.settle(d, keep: false, openTwin: true) }
                Button("Добавить ещё одну") { shelf.settle(d, keep: true) }
                Button("Отмена", role: .cancel) { shelf.settle(d, keep: false) }
            }
    }
}

struct DeleteDialog: ViewModifier {
    let book: Book
    @Binding var shown: Bool
    @EnvironmentObject var shelf: Shelf

    func body(content: Content) -> some View {
        content.confirmationDialog(book.title, isPresented: $shown, titleVisibility: .visible) {
            if shelf.copy(of: book.slug) != .absent || !shelf.skip.contains(book.slug) {
                Button("Только здесь") { shelf.removeHere(book.slug) }
            }
            if shelf.inLibrary.contains(book.slug) {
                Button("Отовсюду", role: .destructive) { shelf.removeEverywhere(book) }
            }
            Button("Отмена", role: .cancel) {}
        }
    }
}

/// Pull to refresh, and the mini player under the books: the list and the grid alike.
struct Chrome: ViewModifier {
    let refresh: () async -> Void
    let open: (String) -> Void

    func body(content: Content) -> some View {
        content
            .refreshable { await refresh() }
            .safeAreaInset(edge: .bottom) {
                MiniPlayer(open: open) {
                    Player.shared.stop()  // saves the place
                    Task {
                        await Player.shared.flush()
                        await refresh()
                    }
                }
            }
    }
}

/// «книга», «книги», «книг» for `n`.
func booksWord(_ n: Int) -> String {
    let (ten, hundred) = (n % 10, n % 100)
    if ten == 1, hundred != 11 { return "книга" }
    if (2...4).contains(ten), !(12...14).contains(hundred) { return "книги" }
    return "книг"
}

struct Slug: Identifiable {
    let id: String
}

struct SectionTitle: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(.caption.weight(.semibold)).tracking(0.8)
            .foregroundStyle(.secondary)
    }
}

// ---- the book on top: one tap back into it ----

struct NowReading: View {
    let book: Book
    let progress: Double
    let listen: Bool  // an audiobook with its audio on the phone, last listened to
    let open: () -> Void
    @Environment(\.colorScheme) private var scheme
    @Environment(\.dynamicTypeSize) private var textSize
    @ObservedObject private var player = Player.shared  // the mini player under the list has this book: one button is enough

    var body: some View {
        Button(action: open) {
            HStack(alignment: .top, spacing: 16) {
                Cover(book: book, width: 96)
                VStack(alignment: .leading, spacing: 6) {
                    Text(book.title)
                        .font(.system(.title3, design: .serif, weight: .semibold))
                        .lineLimit(3)
                        .foregroundStyle(.primary)
                    if !book.author.isEmpty {
                        Text(book.author).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer(minLength: 6)
                    ProgressBar(value: progress)
                    HStack {
                        Text(percent(progress)).font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        Spacer()
                        if player.slug != book.slug {
                            // a large text size keeps the word whole: the icon alone, then
                            let label = Label(listen ? "Слушать" : "Читать", systemImage: listen ? "headphones" : "book")
                            Group {
                                if textSize.isAccessibilitySize { label.labelStyle(.iconOnly) } else { label }
                            }
                            .lineLimit(1)
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 14).padding(.vertical, 7)
                            .background(Color.accentColor, in: Capsule())
                            .foregroundStyle(scheme == .dark ? Color.black : Color.white)  // the dark accent is light
                        }
                    }
                }
            }
            .padding(16)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

// ---- the narrator the reader left playing: a bar over the list ----

/// Its own view, observing the player: the list is not redrawn every second the narrator plays.
struct MiniPlayer: View {
    let open: (String) -> Void
    let close: () -> Void
    @ObservedObject private var player = Player.shared

    var body: some View {
        if let book = player.book {
            HStack(spacing: 12) {
                Cover(book: book, width: 30)
                VStack(alignment: .leading, spacing: 6) {
                    Text(book.title)
                        .font(.system(.subheadline, design: .serif, weight: .semibold))
                        .lineLimit(1)
                    ProgressBar(value: player.progress, height: 2)
                }
                Button {
                    if player.playing { player.pause() } else { player.resume() }
                } label: {
                    Image(systemName: player.playing ? "pause.fill" : "play.fill")
                        .font(.title3)
                        .foregroundStyle(Color.accentColor)
                        .frame(width: 40, height: 40)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel(player.playing ? "Пауза" : "Слушать")
                Button(action: close) {
                    Image(systemName: "xmark")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .frame(width: 32, height: 40)
                        .contentShape(Rectangle())
                }
                .accessibilityLabel("Остановить")
            }
            .buttonStyle(.plain)
            .padding(.leading, 10).padding(.trailing, 6).padding(.vertical, 8)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(.primary.opacity(0.06)))
            .shadow(color: .black.opacity(0.12), radius: 10, y: 3)
            .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .onTapGesture { open(book.slug) }
            .padding(.horizontal, 12).padding(.bottom, 6)
        }
    }
}

// ---- one book in the list ----

struct BookRow: View {
    let book: Book
    let copy: Copy
    let progress: Progress
    var detached = false  // not in the library: nothing read here is saved
    let cancel: () -> Void
    let update: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            Cover(book: book, width: 52)
            VStack(alignment: .leading, spacing: 4) {
                Text(book.title)
                    .font(.system(.body, design: .serif, weight: .medium))
                    .lineLimit(2)
                if !detail.isEmpty {
                    Text(detail).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                }
                status
            }
            .alignmentGuide(.listRowSeparatorLeading) { $0[.leading] }  // under the title, cover or letter alike
            Spacer(minLength: 8)
            trailing
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
    }

    private var detail: String {
        [book.author, book.hasAudio ? (book.narrator.isEmpty ? "аудио" : "читает \(book.narrator)") : ""]
            .filter { !$0.isEmpty }.joined(separator: " · ")
    }

    @ViewBuilder private var status: some View {
        if detached {
            Text("Не в библиотеке — прогресс не сохраняется").font(.caption).foregroundStyle(.secondary).lineLimit(2)
        } else {
            copyStatus
        }
    }

    @ViewBuilder private var copyStatus: some View {
        switch copy {
        case .here where progress.status == .done:
            Text(readOn(progress)).font(.caption).foregroundStyle(.secondary)
        case .here where progress.fraction > 0 || progress.rereading:
            HStack(spacing: 8) {
                ProgressBar(value: progress.fraction).frame(maxWidth: 120)
                Text(progressText(progress)).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            }
        case .textOnly:
            HStack(spacing: 8) {
                if progress.status == .done {
                    Text(readOn(progress)).font(.caption).foregroundStyle(.secondary)
                } else if progress.fraction > 0 {
                    ProgressBar(value: progress.fraction).frame(maxWidth: 120)
                    Text(progressText(progress)).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                }
                Label("без звука", systemImage: "text.book.closed").font(.caption2).foregroundStyle(.secondary)
            }
        case .absent:
            Text((progress.status == .done ? readOn(progress) + " · не скачана · " : "Не скачана · ") + size(book.bytes))
                .font(.caption).foregroundStyle(.secondary)
        case .outdated:
            Text("Есть новая версия").font(.caption).foregroundStyle(Color.accentColor)
        case .failed(let why):
            Text("Не скачалась · \(why)").font(.caption).foregroundStyle(.orange).lineLimit(2)
        default:
            EmptyView()
        }
    }

    @ViewBuilder private var trailing: some View {
        switch copy {
        case .absent, .failed:
            Image(systemName: "arrow.down.circle").font(.title3).foregroundStyle(Color.accentColor)
        case .outdated:  // the copy here is older: the sign stays until the new version is in
            Button(action: update) {
                Image(systemName: "arrow.down.circle").font(.title3).foregroundStyle(Color.accentColor)
                    .padding(8).contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .padding(-8)
            .accessibilityLabel("Обновить")
        case .fetching(let p):
            Button(action: cancel) {
                Ring(value: p, stop: true).frame(width: 26, height: 26).padding(8).contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .padding(-8)
            .accessibilityLabel("Отменить загрузку")
        default:
            EmptyView()
        }
    }
}

// ---- one book in the grid: its cover, one line under it ----

struct BookTile<Actions: View>: View {
    let book: Book
    let copy: Copy
    let progress: Progress
    var detached = false  // not in the library: nothing read here is saved
    let open: () -> Void
    let cancel: () -> Void
    @ViewBuilder let actions: () -> Actions
    @Environment(\.dynamicTypeSize) private var textSize

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Cover(book: book)
                .opacity(fetching != nil ? 0.5 : 1)
                .contentShape(Rectangle())
                .onTapGesture(perform: open)
            HStack(spacing: 4) {
                status
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                    .lineLimit(textSize.isAccessibilitySize ? 2 : 1)
                    .contentShape(Rectangle())
                    .onTapGesture(perform: open)
                Spacer(minLength: 0)
                trailing
            }
            .frame(minHeight: 28)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction(.default, open)
        .accessibilityActions { actions() }
    }

    private var fetching: Double? {
        if case .fetching(let p) = copy { return p }
        return nil
    }

    @ViewBuilder private var status: some View {
        if detached {
            Text("Не в библиотеке")
        } else {
            copyStatus
        }
    }

    @ViewBuilder private var copyStatus: some View {
        switch copy {
        case .fetching:
            Text("Загрузка")
        case .absent:
            HStack(spacing: 4) {
                Image(systemName: "arrow.down.circle").foregroundStyle(Color.accentColor)
                Text(size(book.bytes))
            }
        case .outdated:
            HStack(spacing: 4) {
                Image(systemName: "arrow.down.circle")
                Text("Новая версия")
            }
            .foregroundStyle(Color.accentColor)
        case .failed:
            Text("Не скачалась").foregroundStyle(.orange)
        case .here, .textOnly:
            if progress.status == .done {
                HStack(spacing: 4) {  // «✓ 14 сент.»: the word does not fit under a cover
                    Image(systemName: "checkmark.circle")
                    Text(readDate(progress) ?? "Прочитана")
                }
            } else {
                HStack(spacing: 4) {
                    // what the reader made of it, as a sign: the line under a cover is short
                    if progress.status == .paused {
                        Image(systemName: "pause.circle")
                    } else if progress.rereading {
                        Image(systemName: "arrow.counterclockwise")
                    } else if copy == .textOnly {
                        Image(systemName: "text.book.closed")
                    } else if book.hasAudio {
                        Image(systemName: "headphones")
                    }
                    if progress.fraction > 0 { Text(percent(progress.fraction)) }
                }
            }
        }
    }

    @ViewBuilder private var trailing: some View {
        if let p = fetching {
            Button(action: cancel) {
                Ring(value: p, stop: true).frame(width: 22, height: 22).padding(11).contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .padding(-11)
            .accessibilityLabel("Отменить загрузку")
        } else {
            Menu {
                actions()
            } label: {
                Image(systemName: "ellipsis")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .frame(width: 44, height: 28)
                    .contentShape(Rectangle())
            }
            .padding(.trailing, -12)
            .accessibilityLabel("Действия")
        }
    }

    private var label: String {
        var parts = [book.title, book.author]
        switch copy {
        case .absent: parts.append("не скачана")
        case .fetching: parts.append("скачивается")
        case .outdated: parts.append("есть новая версия")
        case .failed: parts.append("не скачалась")
        case .here, .textOnly:
            parts.append(progress.status == .done ? readOn(progress) : progress.fraction > 0 ? progressText(progress) : "")
        }
        if detached { parts.append("не в библиотеке, прогресс не сохраняется") }
        return parts.filter { !$0.isEmpty }.joined(separator: ", ")
    }
}

// ---- pieces ----

struct Cover: View {
    let book: Book
    var width: CGFloat?  // none: as wide as its column, in the grid

    var body: some View {
        let big = width.map { $0 > 60 } ?? true
        let shape = RoundedRectangle(cornerRadius: big ? 8 : 5, style: .continuous)
        sized(Color.clear)
            .overlay {
                if let img = Player.cover(book.slug) {
                    Image(uiImage: img).resizable().scaledToFill()
                } else {
                    ZStack(alignment: .topLeading) {
                        LinearGradient(
                            colors: [Color.accentColor.opacity(0.28), Color.accentColor.opacity(0.12)],
                            startPoint: .topLeading, endPoint: .bottomTrailing)
                        if let width {
                            Text(String(book.title.prefix(1)))
                                .font(.system(size: width * 0.42, weight: .medium, design: .serif))
                                .foregroundStyle(Color.accentColor)
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        } else {
                            placard  // a cover in the grid carries the title the tile does not
                        }
                    }
                }
            }
            .clipShape(shape)
            .overlay(shape.strokeBorder(.primary.opacity(0.08)))
            .shadow(color: .black.opacity(big ? 0.18 : 0.08), radius: big ? 8 : 3, y: 2)
    }

    @ViewBuilder private func sized(_ base: some View) -> some View {
        if let width {
            base.frame(width: width, height: width * 1.45)
        } else {
            base.aspectRatio(1 / 1.45, contentMode: .fit)
        }
    }

    private var placard: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(book.title)
                .font(.system(.footnote, design: .serif, weight: .semibold))
                .foregroundStyle(.primary)
                .lineLimit(5)
            Spacer(minLength: 0)
            if !book.author.isEmpty {
                Text(book.author).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
        }
        .padding(8)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

struct ProgressBar: View {
    let value: Double
    var height: CGFloat = 4
    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(.quaternary)
                Capsule().fill(Color.accentColor).frame(width: max(height, g.size.width * value))
            }
        }
        .frame(height: height)
    }
}

struct Ring: View {
    let value: Double
    var stop = false  // a square inside: a tap stops it
    var body: some View {
        ZStack {
            if stop { RoundedRectangle(cornerRadius: 2).fill(Color.accentColor).frame(width: 8, height: 8) }
            Circle().stroke(.quaternary, lineWidth: 3)
            Circle().trim(from: 0, to: max(0.03, value))
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.2), value: value)
        }
    }
}

/// «524,5 МБ»: Russian whatever the phone's language, as the rest of the app.
func size(_ bytes: Int64) -> String {
    bytes.formatted(.byteCount(style: .file).locale(Locale(identifier: "ru_RU")))
}

/// «прочитана 7 окт.»; «прочитана» when the day is not known.
func readOn(_ p: Progress) -> String { readDate(p).map { "прочитана \($0)" } ?? "прочитана" }

/// «7 окт.», the day a book was last finished, the year only when it is not this one.
func readDate(_ p: Progress) -> String? {
    guard let on = p.finishedOn, let ms = Shelf.day(on) else { return nil }
    let f = DateFormatter()
    f.locale = Locale(identifier: "ru_RU")
    f.timeZone = TimeZone(identifier: "UTC")  // the reader's days are UTC days
    f.dateFormat = on.hasPrefix(String(Calendar.current.component(.year, from: Date()))) ? "d MMM" : "d MMM yyyy"
    return f.string(from: Date(timeIntervalSince1970: ms / 1000))
}

/// «34%», and what the reader made of the book: «отложена · 34%», «перечитываю · 12%».
func progressText(_ p: Progress) -> String {
    let pct = percent(p.fraction)
    if p.status == .paused { return "отложена · \(pct)" }
    if p.rereading { return p.fraction > 0 ? "перечитываю · \(pct)" : "перечитываю" }
    return pct
}

/// A started book never reads «0%»: the first few minutes of a long one are less than a percent.
func percent(_ v: Double) -> String { v > 0 && v < 0.01 ? "<1%" : "\(Int((v * 100).rounded()))%" }
