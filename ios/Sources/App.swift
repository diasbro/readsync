// readsync on the phone: the library the Mac shares, and the reader for any book copied to the phone.
// Finding and adding books stays on the Mac.

import SwiftUI

@main
struct ReadsyncApp: App {
    @StateObject private var shelf = Shelf.shared
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            LibraryView()
                .environmentObject(shelf)
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
    @State private var lockText = Player.shared.lockText
    @AppStorage(Shelf.dropReadKey) private var dropRead = false

    var body: some View {
        NavigationStack {
            Group {
                if shelf.books.isEmpty {
                    empty
                } else {
                    list
                }
            }
            .navigationTitle("Библиотека")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button("Выбрать папку библиотеки", systemImage: "folder") { picking = true }
                        Button("Обновить", systemImage: "arrow.clockwise") { Task { await shelf.refresh() } }
                        Button("Скачать все книги", systemImage: "icloud.and.arrow.down") { Task { await shelf.fetchAll() } }
                        Toggle("Текст на экране блокировки", systemImage: "lock.iphone", isOn: $lockText)
                        Toggle("Убирать прочитанные", systemImage: "checkmark.circle", isOn: $dropRead)
                        Text("Папка: \(shelf.folderName)")
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.folder]) { result in
                if case .success(let url) = result { shelf.choose(folder: url) }
            }
            .task { await shelf.refresh() }
            .onChange(of: lockText) { _, on in Player.shared.lockText = on }
            .onChange(of: dropRead) { _, on in if on { Task { await shelf.refresh() } } }
            .onChange(of: shelf.copies) { _, copies in
                if let slug = openWhenReady, copies[slug] == .here || copies[slug] == .textOnly {
                    openWhenReady = nil
                    reading = slug
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
    }

    private var list: some View {
        List {
            if !shelf.message.isEmpty {
                Label(shelf.message, systemImage: "exclamationmark.icloud")
                    .font(.footnote).foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }
            // one section with its titles as rows: plain sections would leave a wide gap between them
            if let current = shelf.current {
                SectionTitle("Читаю сейчас")
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 4, leading: 20, bottom: 0, trailing: 20))
                NowReading(
                    book: current, progress: shelf.progress[current.slug]?.fraction ?? 0,
                    listen: current.hasAudio && shelf.copy(of: current.slug) != .textOnly
                ) { open(current) }
                .contextMenu { actions(current, shelf.copy(of: current.slug)) }  // a text-only one gets its audio here
                    .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 12, trailing: 16))
                    .listRowSeparator(.hidden)
                SectionTitle("Все книги")
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 8, leading: 20, bottom: 0, trailing: 20))
            }
            ForEach(shelf.books.filter { $0.slug != shelf.current?.slug }) { book in
                row(book)
            }
            if !shelf.localBytes.isEmpty {
                let n = shelf.localBytes.count
                let size = ByteCountFormatter.string(fromByteCount: shelf.localBytes.values.reduce(0, +), countStyle: .file)
                Text("На iPhone: \(n) \(booksWord(n)) · \(size)")
                    .font(.caption).foregroundStyle(.tertiary).monospacedDigit()
                    .frame(maxWidth: .infinity)
                    .listRowSeparator(.hidden)
                    .listRowInsets(EdgeInsets(top: 14, leading: 20, bottom: 14, trailing: 20))
            }
        }
        .listStyle(.plain)
        .environment(\.defaultMinListRowHeight, 0)  // the section titles are rows: no 44-point floor under them
        .refreshable { await shelf.refresh() }
        .safeAreaInset(edge: .bottom) {
            MiniPlayer {
                openWhenReady = nil
                reading = $0
            } close: {
                Player.shared.stop()  // saves the place
                Task {
                    await Player.shared.flush()
                    await shelf.refresh()
                }
            }
        }
    }

    private func row(_ book: Book) -> some View {
        let copy = shelf.copy(of: book.slug)
        return Button { open(book) } label: {
            BookRow(book: book, copy: copy, progress: shelf.progress[book.slug]?.fraction ?? 0)
        }
        .buttonStyle(.plain)
        .swipeActions(edge: .trailing) { actions(book, copy) }
        .contextMenu { actions(book, copy) }
    }

    @ViewBuilder private func actions(_ book: Book, _ copy: Copy) -> some View {
        if copy.isReadable {
            Button("Убрать с iPhone", systemImage: "iphone.slash", role: .destructive) { shelf.remove(book.slug) }
        }
        if copy == .outdated {
            Button("Обновить", systemImage: "arrow.down.circle") { shelf.fetch(book) }.tint(.accentColor)
        }
        // an audiobook to read as pages: its text without the hundreds of megabytes of audio
        if book.hasAudio, copy == .absent || copy.isFailed {
            Button("Скачать без звука", systemImage: "text.book.closed") { shelf.fetch(book, textOnly: true) }.tint(.indigo)
        }
        if copy == .textOnly {
            Button("Скачать со звуком", systemImage: "headphones") { shelf.fetch(book, textOnly: false) }.tint(.accentColor)
        }
    }

    private var empty: some View {
        ContentUnavailableView {
            Label("Пока пусто", systemImage: "books.vertical")
        } description: {
            Text("Книги приходят с Mac. В меню readsync на Mac включи «Библиотека в iCloud», а здесь выбери папку iCloud Drive → readsync.")
        } actions: {
            Button("Выбрать папку") { picking = true }
                .buttonStyle(.borderedProminent)
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
    let listen: Bool  // an audiobook with its audio on the phone
    let open: () -> Void
    @Environment(\.colorScheme) private var scheme

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
                        Label(listen ? "Слушать" : "Читать", systemImage: listen ? "headphones" : "book")
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 14).padding(.vertical, 7)
                            .background(Color.accentColor, in: Capsule())
                            .foregroundStyle(scheme == .dark ? Color.black : Color.white)  // the dark accent is light
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
                    if player.playing { player.pause() } else { player.play() }
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
    let progress: Double

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
        switch copy {
        case .here where progress > 0:
            HStack(spacing: 8) {
                ProgressBar(value: progress).frame(maxWidth: 120)
                Text(percent(progress)).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            }
        case .textOnly:
            HStack(spacing: 8) {
                if progress > 0 {
                    ProgressBar(value: progress).frame(maxWidth: 120)
                    Text(percent(progress)).font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                }
                Label("без звука", systemImage: "text.book.closed").font(.caption2).foregroundStyle(.secondary)
            }
        case .absent:
            Text("Не скачана · \(ByteCountFormatter.string(fromByteCount: book.bytes, countStyle: .file))")
                .font(.caption).foregroundStyle(.secondary)
        case .outdated:
            Text("На Mac новая версия — смахни, чтобы обновить").font(.caption).foregroundStyle(Color.accentColor)
        case .failed(let why):
            Text(why).font(.caption).foregroundStyle(.orange).lineLimit(2)
        default:
            EmptyView()
        }
    }

    @ViewBuilder private var trailing: some View {
        switch copy {
        case .absent, .failed:
            Image(systemName: "icloud.and.arrow.down").font(.title3).foregroundStyle(Color.accentColor)
        case .fetching(let p):
            Ring(value: p).frame(width: 24, height: 24)
        default:
            EmptyView()
        }
    }
}

// ---- pieces ----

struct Cover: View {
    let book: Book
    let width: CGFloat

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: width > 60 ? 8 : 5, style: .continuous)
        Group {
            if let img = Player.cover(book.slug) {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                ZStack {
                    LinearGradient(
                        colors: [Color.accentColor.opacity(0.28), Color.accentColor.opacity(0.12)],
                        startPoint: .topLeading, endPoint: .bottomTrailing)
                    Text(String(book.title.prefix(1)))
                        .font(.system(size: width * 0.42, weight: .medium, design: .serif))
                        .foregroundStyle(Color.accentColor)
                }
            }
        }
        .frame(width: width, height: width * 1.45)
        .clipShape(shape)
        .overlay(shape.strokeBorder(.primary.opacity(0.08)))
        .shadow(color: .black.opacity(width > 60 ? 0.18 : 0.08), radius: width > 60 ? 8 : 3, y: 2)
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
    var body: some View {
        ZStack {
            Circle().stroke(.quaternary, lineWidth: 3)
            Circle().trim(from: 0, to: max(0.03, value))
                .stroke(Color.accentColor, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(-90))
                .animation(.easeOut(duration: 0.2), value: value)
        }
    }
}

/// A started book never reads «0%»: the first few minutes of a long one are less than a percent.
func percent(_ v: Double) -> String { v > 0 && v < 0.01 ? "<1%" : "\(Int((v * 100).rounded()))%" }
