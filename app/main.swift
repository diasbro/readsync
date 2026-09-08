import AppKit

let app = NSApplication.shared
let delegate = Menu()
app.delegate = delegate
app.setActivationPolicy(.accessory)  // menu bar only, nothing in the Dock
app.run()
