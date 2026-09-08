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

    static func open(_ url: URL) {
        if reusesTab, mayAutomate(ask: false), focus(url) { return }
        NSWorkspace.shared.open(url)
    }

    /// True when a tab showing this address was brought to the front.
    private static func focus(_ url: URL) -> Bool {
        guard let browser = defaultBrowser, let script = script(for: browser, url: url) else { return false }
        var error: NSDictionary?
        let result = NSAppleScript(source: script)?.executeAndReturnError(&error)
        if let error {  // the browser said no: the caller opens the address the usual way
            log("could not look through the tabs: \(error[NSAppleScript.errorMessage] ?? error)")
            return false
        }
        return result?.booleanValue ?? false
    }

    /// The script for this browser, or nil when it has no way to tell us about its tabs.
    /// With no url this only answers whether such a script exists.
    private static func script(for browser: String, url: URL?) -> String? {
        let prefix = url?.absoluteString ?? ""
        if browser == "com.apple.safari" {
            return """
                tell application "Safari"
                  repeat with w in windows
                    repeat with t in tabs of w
                      try
                        if (URL of t) starts with "\(prefix)" then
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
                    if (URL of t) starts with "\(prefix)" then
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
