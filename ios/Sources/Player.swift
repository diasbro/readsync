// The narrator, played natively: AVPlayer keeps going on a locked screen, answers the lock screen and
// headphones, pauses for a call. The reader in the web view drives it through the `audio` messages and
// hears back through window.nativeAudio._update. While the screen is locked no JavaScript runs, so the
// position and the listening sessions are kept here, not in the reader.

import AVFoundation
import MediaPlayer
import UIKit
import WebKit

@MainActor
final class Player: NSObject {
    static let shared = Player()

    private var player: AVPlayer?
    private var observations: [NSKeyValueObservation] = []
    private var endObserver: NSObjectProtocol?
    private var src = ""
    private var book: Book?
    private var sharedDir: URL?
    private var wordStarts: [Double] = []  // word start times, to count the words a session heard
    weak var web: WKWebView?

    private var posDirty = false  // only a position that was played or chosen is worth saving
    private var playedSinceLoad = false
    private var storedPos: (t: Double, at: Double) = (0, 0)  // the newest saved position this player knows of
    private var pendingSeek: Double?  // a seek asked for before the item could take it
    private var saveTimer: Timer?
    private var sessionStart: (at: Date, word: Int)?
    private var pausedAt: Date?
    private var wasPlayingBeforeInterruption = false
    private var pagesOn = false  // the reader shows pages: the lock screen must not start the narrator under them
    private var rate: Float = 1
    private var lastSave: Task<Void, Never>?

    /// Wait for the last position write: the library reads it right after a book is closed.
    func flush() async { await lastSave?.value }

    var time: Double { player?.currentTime().seconds.finite ?? 0 }
    var isPlaying: Bool { player?.timeControlStatus == .playing || player?.timeControlStatus == .waitingToPlayAtSpecifiedRate }

    // ---- commands from the reader ----

    func handle(_ msg: [String: Any]) {
        switch msg["cmd"] as? String {
        case "load": if let src = msg["src"] as? String { load(src) }
        case "play": play(rewind: false)  // the reader has already gone back to the sentence start
        case "pause": pause()
        case "seek": seek(ReadingState.num(msg["t"]))
        case "rate": setRate(Float(ReadingState.num(msg["rate"])))
        case "pages": pagesOn = (msg["on"] as? Bool) ?? false
        default: break
        }
    }

    /// `src` is the reader's path, /books/<slug>/<file>; the file is the app's own copy.
    func load(_ src: String) {
        if src == self.src, player != nil {
            // the same book again: the page was reloaded (iOS dropped it in the background), the narrator plays on
            emit("loadedmetadata")
            emit(isPlaying ? "play" : "pause")
            return
        }
        let parts = src.split(separator: "/").map(String.init)
        guard parts.count == 3, parts[0] == "books", let book = Shelf.localCopy(parts[1]) else {
            return emit("error", error: "нет аудио")
        }
        stop()
        self.src = src
        self.book = book
        let shared = Shelf.shared.sharedDir(book.slug)
        sharedDir = shared
        let local = Shelf.localDir(book.slug)
        Task.detached {
            let starts = Player.wordStarts(local.appendingPathComponent("timing.json"))
            let merged = ReadingState.load(shared: shared, edition: book.edition)
            await MainActor.run {
                guard Player.shared.book?.slug == book.slug else { return }
                Player.shared.wordStarts = starts
                Player.shared.storedPos = (ReadingState.num(merged["pos"]), ReadingState.num(merged["posAt"]))
            }
        }
        let item = AVPlayerItem(url: local.appendingPathComponent(parts[2]))
        item.audioTimePitchAlgorithm = .timeDomain  // speech stays natural at 1.5-2x
        let player = AVPlayer(playerItem: item)
        player.automaticallyWaitsToMinimizeStalling = false  // a local file never stalls
        self.player = player
        observations = [
            item.observe(\.status) { item, _ in
                let status = item.status
                let error = item.error?.localizedDescription
                Task { @MainActor in Player.shared.itemStatus(status, error: error) }
            },
            player.observe(\.timeControlStatus, options: [.new]) { p, _ in
                let status = p.timeControlStatus  // read now, not when the main actor gets to it
                Task { @MainActor in Player.shared.statusChanged(status) }
            },
        ]
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { _ in Task { @MainActor in Player.shared.emit("ended") } }
        setupRemote()
        updateNowPlaying()
    }

    private func itemStatus(_ status: AVPlayerItem.Status, error: String?) {
        if status == .readyToPlay {
            if let t = pendingSeek {
                pendingSeek = nil
                seek(t, chosen: false)
            }
            emit("loadedmetadata")
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
            seek(max(0, time - 3))
        }
        player.defaultRate = rate
        player.playImmediately(atRate: rate)
    }

    func pause() { player?.pause() }

    /// `chosen`: the reader or the lock screen picked this place. The reader's own restore of the saved
    /// position on opening is not a choice, and saving it would only stamp an old place with a new time.
    func seek(_ t: Double, chosen: Bool = true) {
        guard let player else { return }
        guard player.currentItem?.status == .readyToPlay else {
            pendingSeek = t
            return
        }
        if chosen, playedSinceLoad || abs(t - storedPos.t) > 0.5 { posDirty = true }
        let playing = isPlaying
        if playing { endSession() }  // words skipped over are not words heard
        let target = CMTime(seconds: max(0, t), preferredTimescale: 1000)
        player.seek(to: target, toleranceBefore: .zero, toleranceAfter: .zero) { _ in
            Task { @MainActor in
                let p = Player.shared
                if playing, p.isPlaying { p.sessionStart = (Date(), p.wordIndex(p.time)) }
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
            if sessionStart == nil { sessionStart = (Date(), wordIndex(time)) }
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
        if !isPlaying { posDirty = false }
        lastSave = Task.detached { ReadingState.put(shared: sharedDir, edition: book.edition, patch: ["pos": t, "posAt": at]) }
    }

    private func endSession() {
        guard let start = sessionStart, let book, let sharedDir else { return }
        sessionStart = nil
        let sec = Date().timeIntervalSince(start.at)
        let words = Double(max(0, wordIndex(time) - start.word))
        Task.detached {
            ReadingState.addSession(shared: sharedDir, edition: book.edition, day: ReadingState.today, sec: sec, words: words)
        }
    }

    private func wordIndex(_ t: Double) -> Int {
        var lo = 0, hi = wordStarts.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if wordStarts[mid] <= t { lo = mid + 1 } else { hi = mid }
        }
        return lo
    }

    nonisolated static func wordStarts(_ timing: URL) -> [Double] {
        guard let data = try? Data(contentsOf: timing),
            let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
            let words = obj["words"] as? [[Any]]
        else { return [] }
        return words.map { $0.count > 3 ? ReadingState.num($0[3]) : 0 }
    }

    // ---- telling the reader ----

    private func emit(_ event: String, error: String? = nil) {
        guard let web else { return }
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
        c.skipBackwardCommand.addTarget { _ in Player.shared.seek(Player.shared.time - 10); return .success }
        c.skipForwardCommand.addTarget { _ in Player.shared.seek(Player.shared.time + 10); return .success }
        c.changePlaybackPositionCommand.addTarget { e in
            guard let e = e as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            Player.shared.seek(e.positionTime)
            return .success
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
            Task { @MainActor in
                let p = Player.shared
                if type == .began {
                    p.wasPlayingBeforeInterruption = p.isPlaying || p.wasPlayingBeforeInterruption
                } else if type == .ended {
                    // a call ended: carry on, but only if it was the call that stopped the narrator
                    if resume, p.wasPlayingBeforeInterruption { p.play() }
                    p.wasPlayingBeforeInterruption = false
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
                let src = p.src, t = p.time
                guard !src.isEmpty else { return }
                p.stop()
                p.load(src)
                p.seek(t, chosen: false)  // taken once the new item is ready
            }
        }
    }

    private func updateNowPlaying() {
        guard let book else { return }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: book.title,
            MPMediaItemPropertyArtist: book.author,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: time,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? Double(rate) : 0,
            MPNowPlayingInfoPropertyDefaultPlaybackRate: Double(rate),
        ]
        if let d = player?.currentItem?.duration.seconds.finite { info[MPMediaItemPropertyPlaybackDuration] = d }
        if let cover = Player.cover(book.slug) {
            info[MPMediaItemPropertyArtwork] = MPMediaItemArtwork(boundsSize: cover.size) { _ in cover }
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private static let covers = NSCache<NSString, UIImage>()

    static func cover(_ slug: String) -> UIImage? {
        if let hit = covers.object(forKey: slug as NSString) { return hit }
        let images = Shelf.localDir(slug).appendingPathComponent("images")
        let names = (try? FileManager.default.contentsOfDirectory(atPath: images.path)) ?? []
        let file = names.first(where: { $0.hasPrefix("cover.") }).map { images.appendingPathComponent($0) }
            ?? Shelf.cachedCover(slug)
        guard let file, let img = UIImage(contentsOfFile: file.path) else { return nil }
        covers.setObject(img, forKey: slug as NSString)
        return img
    }

    static func forgetCover(_ slug: String) { covers.removeObject(forKey: slug as NSString) }
}

extension Double {
    var finite: Double? { isFinite ? self : nil }
}
