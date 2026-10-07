// Opening the library. By default the address is handed to the browser and it decides, which means
// a new tab every time. Turn "в той же вкладке" on and the tab that already shows the library comes
// forward instead; that needs permission to talk to the browser, asked once, only when asked for.

import AppKit
import CoreServices

enum Browser {
    private static let reuseKey = "reuseTab"

    static var reusesTab: Bool {
        get { UserDefaults.standard.bool(forKey: reuseKey) }
        set { UserDefaults.standard.set(newValue, forKey: reuseKey) }
    }

    /// The bundle id of whatever opens http links on this Mac.
    static var defaultBrowser: String? {
        guard let http = URL(string: "http://127.0.0.1/"),
            let app = NSWorkspace.shared.urlForApplication(toOpen: http)
        else { return nil }
        return Bundle(url: app)?.bundleIdentifier?.lowercased()
    }

    /// Whether this browser can be asked at all: Safari and the Chrome family can, others cannot.
    static var canReuseTab: Bool { defaultBrowser.flatMap { script(for: $0, url: nil) } != nil }

    /// Ask the system for permission to talk to the browser. `ask` false never shows a dialog, which
    /// is how the everyday path checks; true is the one moment the reader asked for this themselves.
    @discardableResult
    static func mayAutomate(ask: Bool) -> Bool {
        guard let bundleID = defaultBrowser else { return false }
        var target = AEAddressDesc()
        let made = bundleID.withCString { ptr in
            AECreateDesc(typeApplicationBundleID, ptr, strlen(ptr), &target)
        }
        guard made == noErr else { return false }
        defer { AEDisposeDesc(&target) }
        return AEDeterminePermissionToAutomateTarget(&target, typeWildCard, typeWildCard, ask) == noErr
    }

    /// The browser is asked off the main thread: a busy or hung one never freezes the menu.
    static func open(_ url: URL) {
        DispatchQueue.global(qos: .userInitiated).async {
            if reusesTab, mayAutomate(ask: false), focus(url) { return }
            DispatchQueue.main.async { NSWorkspace.shared.open(url) }
        }
    }

    /// The same, waiting for the browser: for `--open`, which has no menu to keep responsive.
    static func openNow(_ url: URL) {
        if reusesTab, mayAutomate(ask: false), focus(url) { return }
        NSWorkspace.shared.open(url)
    }

    /// True when a tab showing this address was brought to the front. The browser gets five seconds.
    private static func focus(_ url: URL) -> Bool {
        guard let browser = defaultBrowser, let script = script(for: browser, url: url) else { return false }
        let (code, out) = run("/usr/bin/osascript", ["-e", script], timeout: 5)
        let answer = out.trimmingCharacters(in: .whitespacesAndNewlines)
        if code != 0 {  // the browser said no or did not answer: the caller opens the address the usual way
            log("could not look through the tabs: \(answer)")
            return false
        }
        return answer == "true"
    }

    /// The script for this browser, or nil when it has no way to tell us about its tabs.
    /// With no url this only answers whether such a script exists.
    private static func script(for browser: String, url: URL?) -> String? {
        // a string for the script: a quote or a backslash in the address cannot end it early
        let prefix = (url?.absoluteString ?? "").replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        // the library is the bare address, and a reader tab (`?book=`) starts with it too. A book's address is
        // the whole of it, or it followed by more of the query or a fragment: `?book=a` is not `?book=a-2`
        let match =
            url?.query == nil
            ? "(URL of t) starts with \"\(prefix)\" and (URL of t) does not contain \"?book=\""
            : "((URL of t) is \"\(prefix)\" or (URL of t) starts with \"\(prefix)&\" or (URL of t) starts with \"\(prefix)#\")"
        if browser == "com.apple.safari" {
            return """
                tell application "Safari"
                  repeat with w in windows
                    repeat with t in tabs of w
                      try
                        if \(match) then
                          set current tab of w to t
                          set index of w to 1
                          activate
                          return true
                        end if
                      end try
                    end repeat
                  end repeat
                  return false
                end tell
                """
        }
        let chromium = [
            "com.google.chrome", "com.google.chrome.canary", "com.brave.browser", "com.microsoft.edgemac",
            "com.vivaldi.vivaldi", "org.chromium.chromium", "company.thebrowser.browser",
            "ru.yandex.desktop.yandex-browser",
        ]
        guard chromium.contains(browser) else { return nil }
        return """
            tell application id "\(browser)"
              set found to false
              repeat with w in windows
                set i to 0
                repeat with t in tabs of w
                  set i to i + 1
                  try
                    if \(match) then
                      set active tab index of w to i
                      set index of w to 1
                      set found to true
                      exit repeat
                    end if
                  end try
                end repeat
                if found then exit repeat
              end repeat
              if found then activate
              return found
            end tell
            """
    }
}
