// The narrator, played natively: AVPlayer keeps going on a locked screen, answers the lock screen and
// headphones, pauses for a call. The reader in the web view drives it through the `audio` messages and
// hears back through window.nativeAudio._update. While the screen is locked no JavaScript runs, so the
// position and the listening sessions are kept here, not in the reader. Closing the reader leaves the
// narrator as it was: the library shows it in its mini player.

import AVFoundation
import Combine
import MediaPlayer
import UIKit
import WebKit

@MainActor
final class Player: NSObject, ObservableObject {
    static let shared = Player()

    /// The book the narrator is loaded with, playing or not.
    @Published private(set) var book: Book?
    /// `isPlaying` as the views see it: told by the player, not asked of it on every draw.
    @Published private(set) var playing = false
    @Published private(set) var progress = 0.0
    /// The lock screen shows the sentence being spoken instead of the book's title.
    @Published var lockText = AppSettings.lockText {
        didSet {
            guard lockText != oldValue else { return }
            AppSettings.lockText = lockText
            if lockText { loadText() } else { text = nil }  // the book's text is held only while it is shown
            updateNowPlaying()
        }
    }

    private var player: AVPlayer?
    private var observations: [NSKeyValueObservation] = []
    private var endObserver: NSObjectProtocol?
    private var timeObserver: Any?
    private var src = ""
    private var sharedDir: URL?
    private var words = Words()  // when each word starts (sessions count the words heard) and where it sits
    private var text: BookText?  // only while the lock screen shows the sentence
    private var loadingText = false
    private var shownLine: String?
    private weak var web: WKWebView?
    private var webSlug: String?  // the book of the page in the web view, which may be one without audio
    private var reattached = false  // the reader came back to the narrator it left: its position stands

    private var posDirty = false {  // only a position that was played or chosen is worth saving
        didSet { if posDirty { dirtyMark += 1 } }
    }
    private var dirtyMark = 0  // counts the times the position became worth saving: a write clears only its own
    private var playedSinceLoad = false
    private var storedPos: (t: Double, at: Double) = (0, 0)  // the newest saved position this player knows of
    private var pendingSeek: Double?  // a seek asked for before the item could take it
    private var saveTimer: Timer?
    private var sessionStart: (at: Date, word: Int)?
    private var pausedAt: Date?
    private var wasPlayingBeforeInterruption = false
    private var playIntent = false  // playing because we asked to: a pause the system made leaves it set
    private var stateLoaded = false  // the book's saved state is read: until then storedPos is no guide
    private var itemGen = 0  // one per item: what an item already replaced still reports is dropped
    private var pagesOn = false  // the reader shows pages: the lock screen must not start the narrator under them
    private var rate: Float = 1
    private var lastSave: Task<Void, Never>?
    private var autoplay: String?  // «Слушать» in the library: this book plays once it is loaded and in place

    /// Wait for the last position write and the reader's own writes: the library reads them right after a
    /// book is closed.
    func flush() async {
        await lastSave?.value
        await Bridge.flushWrites()
    }

    /// The book the narrator is loaded with.
    var slug: String? { book?.slug }

    /// The book open in the reader now, audio or not.
    var pageSlug: String? { web == nil ? nil : webSlug }

    /// «Слушать»: the narrator starts as soon as the reader has the book loaded, from its saved place.
    func playWhenOpened(_ slug: String) {
        if book?.slug == slug, !pagesOn { return play() }
        autoplay = slug
    }

    /// The asked-for start, once the item can play and the saved place is known.
    private func startIfAsked() {
        guard let book, autoplay == book.slug, stateLoaded, !pagesOn, let player,
            player.currentItem?.status == .readyToPlay
        else { return }
        autoplay = nil
        let d = player.currentItem?.duration.seconds.finite ?? 0
        if storedPos.t > 0, d == 0 || storedPos.t < d - 5 { seek(storedPos.t, chosen: false) }
        reattached = true  // the place is put: the page's own restore of it would only jump back
        play(rewind: false)
    }

    /// The reader's page for `slug` is on screen and hears the narrator from now on.
    func attach(_ web: WKWebView, slug: String) {
        self.web = web
        webSlug = slug
    }

    func detach(_ web: WKWebView) {
        if self.web === web { self.web = nil }
    }

    /// The reader is closed: the narrator stays as it was, and its place is saved for the library to read.
    /// One the reader left under pages was never listened to and goes.
    func readerClosed(_ slug: String) {
        autoplay = nil
        if slug == book?.slug, pagesOn { return stop() }
        savePosition()
    }

    var time: Double { player?.currentTime().seconds.finite ?? 0 }
    var isPlaying: Bool { player?.timeControlStatus == .playing || player?.timeControlStatus == .waitingToPlayAtSpecifiedRate }

    // ---- commands from the reader ----

    /// `page`: the book the reader shows. A book without audio, read while this one plays, does not drive it.
    func handle(_ msg: [String: Any], from page: String) {
        let cmd = msg["cmd"] as? String
        if cmd == "load" {
            if let src = msg["src"] as? String { load(src) }
            return
        }
        guard page == book?.slug else { return }
        switch cmd {
        case "play": play(rewind: false)  // the reader has already gone back to the sentence start
        case "pause": pause()
        case "seek":
            let chosen = (msg["chosen"] as? Bool) ?? true
            // the place a reopened reader puts back was read from a file a save may still be on its way to
            if !chosen, reattached { return emit("seeked") }
            seek(ReadingState.num(msg["t"]), chosen: chosen)
        case "rate": setRate(Float(ReadingState.num(msg["rate"])))
        case "pages":
            pagesOn = (msg["on"] as? Bool) ?? false
            if pagesOn { autoplay = nil }
        default: break
        }
    }

    /// `src` is the reader's path, /books/<slug>/<file>; the file is the app's own copy.
    func load(_ src: String) {
        if src == self.src, player != nil {
            // the same book again: reopened from the library, or the page was reloaded (iOS dropped it in the
            // background); the narrator plays on
            reattached = true
            emit("loadedmetadata")
            emit(isPlaying ? "play" : "pause")
            return
        }
        let parts = src.split(separator: "/").map(String.init)
        guard parts.count == 3, parts[0] == "books", let book = Shelf.localCopy(parts[1]) else {
            return emit("error", error: "нет аудио", toAnyPage: true)
        }
        stop()  // the last book's place, words and session go with it
        if autoplay != book.slug { autoplay = nil }
        let gen = itemGen
        self.src = src
        self.book = book
        let shared = Shelf.shared.sharedDir(book.slug)
        sharedDir = shared
        let local = Shelf.localDir(book.slug)
        Task.detached {
            let words = Words(local.appendingPathComponent("timing.json"))
            let merged = shared.map { ReadingState.load(shared: $0, edition: book.edition) } ?? [:]
            await MainActor.run {
                let p = Player.shared
                guard p.itemGen == gen else { return }
                p.words = words
                p.storedPos = (ReadingState.num(merged["pos"]), ReadingState.num(merged["posAt"]))
                p.stateLoaded = true
                p.startIfAsked()
                // it began to play before the words were known: the session starts now, from where it is
                if p.sessionStart == nil, p.isPlaying, !words.starts.isEmpty { p.sessionStart = (Date(), p.wordIndex(p.time)) }
                p.tick()
            }
        }
        let item = AVPlayerItem(url: local.appendingPathComponent(parts[2]))
        item.audioTimePitchAlgorithm = .timeDomain  // speech stays natural at 1.5-2x
        let player = AVPlayer(playerItem: item)
        player.automaticallyWaitsToMinimizeStalling = false  // a local file never stalls
        self.player = player
        observations = [
            // .initial: an item ready before it is watched still tells the reader
            item.observe(\.status, options: [.initial, .new]) { item, _ in
                let status = item.status
                let error = item.error?.localizedDescription
                Task { @MainActor in Player.shared.itemStatus(status, error: error, gen: gen) }
            },
            player.observe(\.timeControlStatus, options: [.new]) { p, _ in
                let status = p.timeControlStatus  // read now, not when the main actor gets to it
                Task { @MainActor in
                    guard Player.shared.itemGen == gen else { return }
                    Player.shared.statusChanged(status)
                }
            },
        ]
        // once a second while it plays (and on every jump): the mini player's line and the lock screen's sentence
        timeObserver = player.addPeriodicTimeObserver(forInterval: CMTime(seconds: 1, preferredTimescale: 10), queue: .main) {
            _ in
            MainActor.assumeIsolated {
                guard Player.shared.itemGen == gen else { return }
                Player.shared.tick()
            }
        }
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { _ in
            Task { @MainActor in
                let p = Player.shared
                guard p.itemGen == gen else { return }
                p.playIntent = false  // the end is not a pause a call made: nothing to resume after one
                p.emit("ended")
            }
        }
        setupRemote()
        loadText()
        updateNowPlaying()
    }

    private func itemStatus(_ status: AVPlayerItem.Status, error: String?, gen: Int) {
        guard gen == itemGen else { return }
        if status == .readyToPlay {
            if let t = pendingSeek {
                pendingSeek = nil
                seek(t, chosen: false)
            }
            startIfAsked()
            emit("loadedmetadata")
            tick()
        } else if status == .failed {
            emit("error", error: error ?? "аудио не открылось")
        }
    }

    /// `rewind`: started from the lock screen or headphones after a pause, where no reader took a step back.
    func play(rewind: Bool = true) {
        guard let player else { return emit("pause") }
        let session = AVAudioSession.sharedInstance()
        // the session goes active only to play: opening a book to read must not stop the reader's music
        try? session.setCategory(.playback, mode: .spokenAudio)
        try? session.setActive(true)
        if rewind, let pausedAt, Date().timeIntervalSince(pausedAt) > 8, AppSettings.rewind {
            seek(max(0, time - 3), remote: true)
        }
        playIntent = true
        player.defaultRate = rate
        player.playImmediately(atRate: rate)
    }

    func pause() {
        playIntent = false
        player?.pause()
    }

    /// `chosen`: the reader or the lock screen picked this place. The reader's own restore of the saved
    /// position on opening is not a choice, and saving it would only stamp an old place with a new time.
    /// `remote`: the lock screen or headphones, the only choice taken before the saved place is known.
    func seek(_ t: Double, chosen: Bool = true, remote: Bool = false) {
        guard let player else { return }
        guard player.currentItem?.status == .readyToPlay else {
            pendingSeek = t
            return
        }
        if chosen, stateLoaded || remote, playedSinceLoad || abs(t - storedPos.t) > 0.5 { posDirty = true }
        let playing = isPlaying
        if playing { endSession() }  // words skipped over are not words heard
        let gen = itemGen
        let target = CMTime(seconds: max(0, t), preferredTimescale: 1000)
        player.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            Task { @MainActor in
                let p = Player.shared
                guard p.itemGen == gen else { return }
                if playing, p.isPlaying, !p.words.starts.isEmpty { p.sessionStart = (Date(), p.wordIndex(p.time)) }
                p.progress = p.fraction
                p.emit("seeked")
                p.updateNowPlaying()
            }
        }
    }

    private func setRate(_ r: Float) {
        guard r > 0 else { return }
        rate = r
        player?.defaultRate = r  // a paused player stays paused: setting `rate` would start it
        if isPlaying { player?.rate = r }
        emit("ratechange")
        updateNowPlaying()
    }

    func stop() {
        if isPlaying { player?.pause() }
        endSession()
        savePosition()
        saveTimer?.invalidate()
        itemGen += 1
        if let timeObserver { player?.removeTimeObserver(timeObserver) }
        timeObserver = nil
        observations = []
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        endObserver = nil
        player = nil
        book = nil
        src = ""
        posDirty = false
        playedSinceLoad = false
        pausedAt = nil
        pendingSeek = nil
        pagesOn = false
        playIntent = false
        wasPlayingBeforeInterruption = false
        stateLoaded = false
        storedPos = (0, 0)
        words = Words()
        text = nil
        loadingText = false
        shownLine = nil
        reattached = false
        sessionStart = nil
        playing = false
        progress = 0
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    // ---- what the player did ----

    private func statusChanged(_ status: AVPlayer.TimeControlStatus) {
        switch status {
        case .playing:
            posDirty = true
            playedSinceLoad = true
            pausedAt = nil
            if sessionStart == nil, !words.starts.isEmpty { sessionStart = (Date(), wordIndex(time)) }  // else when they come
            saveTimer?.invalidate()
            saveTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { _ in
                Task { @MainActor in Player.shared.savePosition() }
            }
            emit("play")
            emit("playing")
        case .paused:
            saveTimer?.invalidate()
            pausedAt = Date()
            endSession()
            savePosition()
            emit("pause")
        default:
            break
        }
        playing = isPlaying
        updateNowPlaying()
    }

    /// The app is leaving the screen (or the phone is being locked): what the reader would have saved.
    func appWillResignActive() {
        savePosition()
    }

    /// Back on screen. While paused, a place another device saved in the meantime is taken; then the
    /// reader catches up with whatever happened while it was frozen.
    func appDidBecomeActive() {
        guard let book, let sharedDir else { return }
        if isPlaying { return emit("snapshot") }
        let known = storedPos.at
        Task.detached {
            let merged = ReadingState.load(shared: sharedDir, edition: book.edition)
            await MainActor.run {
                let p = Player.shared
                let at = ReadingState.num(merged["posAt"])
                if p.book?.slug == book.slug, !p.isPlaying, at > known, !p.posDirty {
                    p.storedPos = (ReadingState.num(merged["pos"]), at)
                    p.seek(p.storedPos.t, chosen: false)
                }
                p.emit("snapshot")
            }
        }
    }

    // ---- saving, off the main thread: iCloud's file coordination can take a moment ----

    func savePosition() {
        guard posDirty, let book, let sharedDir else { return }
        let t = time, at = ReadingState.nowMs
        storedPos = (t, at)
        // clean again only once the write is in, so a failed one is tried again; while playing it stays dirty
        let clean = !isPlaying, mark = dirtyMark, gen = itemGen
        lastSave = background {
            let ok = ReadingState.write(shared: sharedDir, edition: book.edition, patch: ["pos": t, "posAt": at])
            await MainActor.run {
                let p = Player.shared
                if ok, clean, p.itemGen == gen, p.dirtyMark == mark { p.posDirty = false }
            }
        }
    }

    /// A write iOS lets finish after the app leaves the screen: suspension must not cut an iCloud write short.
    private func background(_ work: @escaping @Sendable () async -> Void) -> Task<Void, Never> {
        let time = BackgroundTime()
        return Task.detached {
            await work()
            await time.end()
        }
    }

    private func endSession() {
        guard let start = sessionStart, let book, let sharedDir else { return }
        sessionStart = nil
        let sec = Date().timeIntervalSince(start.at)
        let words = Double(max(0, wordIndex(time) - start.word))
        _ = background {
            _ = ReadingState.addSession(shared: sharedDir, edition: book.edition, day: ReadingState.today, sec: sec, words: words)
        }
    }

    private func wordIndex(_ t: Double) -> Int {
        var lo = 0, hi = words.starts.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if words.starts[mid] <= t { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    private var fraction: Double {
        guard let d = player?.currentItem?.duration.seconds.finite, d > 0 else { return 0 }
        return min(1, time / d)
    }

    /// Once a second: the mini player moves on, and the lock screen follows a new sentence.
    private func tick() {
        let f = fraction
        if abs(f - progress) > 0.0005 { progress = f }  // a hair on the bar: no redraw for less
        if lockText, line()?.text != shownLine { updateNowPlaying() }
    }

    // ---- the sentence on the lock screen ----

    private func loadText() {
        guard lockText, text == nil, !loadingText, let book else { return }
        loadingText = true
        let gen = itemGen, file = Shelf.localDir(book.slug).appendingPathComponent("book.json")
        Task.detached {
            let text = BookText(file)
            await MainActor.run {
                let p = Player.shared
                guard p.itemGen == gen, p.lockText else { return }
                p.loadingText = false
                p.text = text
                p.updateNowPlaying()
            }
        }
    }

    /// The sentence being spoken now, and its chapter.
    private func line() -> (text: String, chapter: String)? {
        guard let text else { return nil }
        let i = wordIndex(time) - 1
        guard i >= 0, i < words.block.count else { return nil }
        let b = Int(words.block[i])
        guard let s = text.sentence(block: b, at: Int(words.from[i]), to: Int(words.to[i])) else { return nil }
        return (s, text.chapter(b))
    }

    // ---- telling the reader ----

    /// `toAnyPage`: an answer to the page that asked, whichever book the narrator holds.
    private func emit(_ event: String, error: String? = nil, toAnyPage: Bool = false) {
        guard let web, toAnyPage || webSlug == book?.slug else { return }
        var s: [String: Any] = ["event": event, "t": time, "paused": !isPlaying, "rate": rate]
        if let d = player?.currentItem?.duration.seconds.finite, d > 0 { s["duration"] = d }
        if let error { s["error"] = error }
        guard let data = try? JSONSerialization.data(withJSONObject: s), let json = String(data: data, encoding: .utf8)
        else { return }
        web.evaluateJavaScript("window.nativeAudio && window.nativeAudio._update(\(json))")
    }

    // ---- lock screen and headphones ----

    private var remoteReady = false

    private func setupRemote() {
        guard !remoteReady else { return }
        remoteReady = true
        let c = MPRemoteCommandCenter.shared()
        let start: () -> MPRemoteCommandHandlerStatus = {
            let p = Player.shared
            if p.pagesOn || p.player == nil { return .commandFailed }
            p.play()
            return .success
        }
        c.playCommand.addTarget { _ in start() }
        c.pauseCommand.addTarget { _ in Player.shared.pause(); return .success }
        c.togglePlayPauseCommand.addTarget { _ in
            if Player.shared.isPlaying { Player.shared.pause(); return .success }
            return start()
        }
        c.skipBackwardCommand.preferredIntervals = [10]
        c.skipForwardCommand.preferredIntervals = [10]
        // under pages the narrator is not the lock screen's to move, as it is not to start
        let move: (Double) -> MPRemoteCommandHandlerStatus = { t in
            let p = Player.shared
            if p.pagesOn || p.player == nil { return .commandFailed }
            p.seek(t, remote: true)
            return .success
        }
        c.skipBackwardCommand.addTarget { _ in move(Player.shared.time - 10) }
        c.skipForwardCommand.addTarget { _ in move(Player.shared.time + 10) }
        c.changePlaybackPositionCommand.addTarget { e in
            guard let e = e as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            return move(e.positionTime)
        }
        // one pair on the lock screen, and AirPods' double press stays ±10 s: no track commands
        c.nextTrackCommand.isEnabled = false
        c.previousTrackCommand.isEnabled = false

        let nc = NotificationCenter.default
        nc.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { note in
            let type = (note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
                .flatMap(AVAudioSession.InterruptionType.init(rawValue:))
            let resume = (note.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt)
                .map { AVAudioSession.InterruptionOptions(rawValue: $0).contains(.shouldResume) } ?? false
            // on the main queue already, and read now: by the time a task ran, the system has paused it
            MainActor.assumeIsolated {
                let p = Player.shared
                if type == .began {
                    // what we meant, not what the player does: the system pauses it before this arrives
                    p.wasPlayingBeforeInterruption = p.playIntent || p.wasPlayingBeforeInterruption
                } else if type == .ended {
                    // a call ended: carry on, but only if it was the call that stopped the narrator
                    let was = p.wasPlayingBeforeInterruption
                    p.wasPlayingBeforeInterruption = false
                    p.playIntent = false  // the system's pause stands unless we play again
                    if resume, was { p.play() }
                }
            }
        }
        nc.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { note in
            let reason = (note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt)
                .flatMap(AVAudioSession.RouteChangeReason.init(rawValue:))
            guard reason == .oldDeviceUnavailable else { return }
            Task { @MainActor in Player.shared.pause() }  // headphones out: never on to the speaker
        }
        nc.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in
                let p = Player.shared
                let src = p.src, t = p.time, pages = p.pagesOn
                guard !src.isEmpty else { return }
                p.stop()  // saves a dirty position first
                p.load(src)
                p.pagesOn = pages
                p.seek(t, chosen: false)  // taken once the new item is ready
            }
        }
    }

    private func updateNowPlaying() {
        guard let book else { return }
        let line = lockText ? line() : nil
        shownLine = line?.text
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: line?.text ?? book.title,
            MPMediaItemPropertyArtist: line == nil ? book.author : book.title,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: time,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? Double(rate) : 0,
            MPNowPlayingInfoPropertyDefaultPlaybackRate: Double(rate),
        ]
        if let line, !line.chapter.isEmpty { info[MPMediaItemPropertyAlbumTitle] = line.chapter }
        if let d = player?.currentItem?.duration.seconds.finite { info[MPMediaItemPropertyPlaybackDuration] = d }
        if let cover = Player.cover(book.slug) {
            info[MPMediaItemPropertyArtwork] = MPMediaItemArtwork(boundsSize: cover.size) { _ in cover }
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private static let covers = NSCache<NSString, UIImage>()
    private static var noCover = Set<String>()  // looked for and not found: the list asks on every draw

    static func cover(_ slug: String) -> UIImage? {
        if let hit = covers.object(forKey: slug as NSString) { return hit }
        if noCover.contains(slug) { return nil }
        let images = Shelf.localDir(slug).appendingPathComponent("images")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: images.path)) ?? []
        let file = names.first(where: { $0.hasPrefix("cover.") }).map { images.appendingPathComponent($0) }
            ?? Shelf.cachedCover(slug)
        guard let file, let img = UIImage(contentsOfFile: file.path) else {
            noCover.insert(slug)
            return nil
        }
        covers.setObject(img, forKey: slug as NSString)
        return img
    }

    static func forgetCover(_ slug: String) {
        covers.removeObject(forKey: slug as NSString)
        noCover.remove(slug)
    }

    /// Covers may have come in with a refresh: the books without one are looked at again.
    static func forgetMissingCovers() { noCover.removeAll() }
}

/// timing.json's words, as small numbers: `[block, charStart, charEnd, t0, t1]` each.
struct Words: Sendable {
    var starts: [Double] = []
    var block: [Int32] = []
    var from: [Int32] = []
    var to: [Int32] = []

    init() {}

    init(_ timing: URL) {
        guard let data = try? Data(contentsOf: timing),
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            let words = obj["words"] as? [[Any]]
        else { return }
        let int = { (w: [Any], i: Int) in Int32(clamping: Int(w.count > i ? ReadingState.num(w[i]) : 0)) }
        starts = words.map { $0.count > 3 ? ReadingState.num($0[3]) : 0 }
        block = words.map { int($0, 0) }
        from = words.map { int($0, 1) }
        to = words.map { int($0, 2) }
    }
}

/// What the lock screen needs of book.json: each block's text and sentences, and where chapters begin.
struct BookText: Sendable {
    private var blocks: [String] = []
    private var sentences: [[Int32]] = []  // per block: start, end, start, end… in UTF-16 units, as the reader counts
    private var chapters: [(block: Int, title: String)] = []

    init?(_ file: URL) {
        guard let data = try? Data(contentsOf: file),
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            let blocks = obj["blocks"] as? [[String: Any]]
        else { return nil }
        for b in blocks {
            self.blocks.append(b["text"] as? String ?? "")
            let spans = b["sentences"] as? [[Any]] ?? []
            sentences.append(spans.flatMap { $0.prefix(2).map { Int32(clamping: Int(ReadingState.num($0))) } })
        }
        for c in obj["chapters"] as? [[String: Any]] ?? [] where (c["hidden"] as? Bool) != true {
            chapters.append((Int(ReadingState.num(c["first_block"])), c["title"] as? String ?? ""))
        }
    }

    func chapter(_ block: Int) -> String { chapters.last { $0.block <= block }?.title ?? "" }

    /// The sentence the word at `from..<to` of `block` is in: the block's own span, or, without spans, as far
    /// as the sentence-ending marks either side. A long one is shown a piece at a time, the piece the word is in.
    func sentence(block b: Int, at from: Int, to: Int, limit: Int = 160) -> String? {
        guard b >= 0, b < blocks.count else { return nil }
        let s = blocks[b] as NSString
        let n = s.length
        guard from >= 0, from <= n else { return nil }
        var start = 0, end = n
        let spans = sentences[b]
        if let k = stride(from: 0, to: spans.count - 1, by: 2).first(where: { from >= Int(spans[$0]) && from < Int(spans[$0 + 1]) }) {
            (start, end) = (Int(spans[k]), min(n, Int(spans[k + 1])))
        } else {
            let enders = Set(".!?…".utf16), closers = Set("\"»”’)]".utf16)
            start = from
            while start > 0, !enders.contains(s.character(at: start - 1)) { start -= 1 }
            end = max(from, min(to, n))
            while end < n, !enders.contains(s.character(at: end)) { end += 1 }
            while end < n, enders.contains(s.character(at: end)) || closers.contains(s.character(at: end)) { end += 1 }
        }
        // a long one in pieces of up to `limit`, cut at a space: the line changes once per piece, not per word
        var a = start, z = end
        while z - a > limit {
            let r = s.range(of: " ", options: .backwards, range: NSRange(location: a + 1, length: limit - 1))
            let cut = r.location == NSNotFound ? a + limit : r.location
            if from < cut {
                z = cut
                break
            }
            a = cut
        }
        let r = s.rangeOfComposedCharacterSequences(for: NSRange(location: a, length: z - a))
        let body = s.substring(with: r).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return nil }
        return (a > start ? "…" : "") + body + (z < end ? "…" : "")
    }
}

/// The time iOS grants a write to finish once the app is off the screen.
@MainActor private final class BackgroundTime {
    private var id = UIBackgroundTaskIdentifier.invalid

    init() {
        id = UIApplication.shared.beginBackgroundTask(withName: "readsync.save") { [weak self] in self?.end() }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}

extension Double {
    var finite: Double? { isFinite ? self : nil }
}
