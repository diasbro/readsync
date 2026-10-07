// The library's minute refresh reads the scene phase it runs in: a `.task` reads the values of the view it was
// started from, so LibraryView starts its loop again with each phase (`.task(id: phase)`). This holds that
// pattern to what SwiftUI does.

import SwiftUI
import UIKit
import XCTest

private struct ValueKey: EnvironmentKey { static let defaultValue = 0 }

extension EnvironmentValues {
    fileprivate var taskTestValue: Int {
        get { self[ValueKey.self] }
        set { self[ValueKey.self] = newValue }
    }
}

private final class Seen: ObservableObject {
    @Published var value = 0
    var read: [Int] = []
}

private struct Parent: View {
    @ObservedObject var seen: Seen
    let restarts: Bool
    var body: some View { Child(seen: seen, restarts: restarts).environment(\.taskTestValue, seen.value) }
}

private struct Child: View {
    let seen: Seen
    let restarts: Bool
    @Environment(\.taskTestValue) private var value

    var body: some View {
        if restarts {
            Text("\(value)").task(id: value) { await loop() }
        } else {
            Text("\(value)").task { await loop() }
        }
    }

    private func loop() async {
        while !Task.isCancelled {
            seen.read.append(value)
            try? await Task.sleep(for: .milliseconds(100))
        }
    }
}

@MainActor
final class ViewTaskTests: XCTestCase {
    private func run(restarts: Bool) async throws -> [Int] {
        let scene = try XCTUnwrap(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let window = UIWindow(windowScene: scene)
        let seen = Seen()
        window.rootViewController = UIHostingController(rootView: Parent(seen: seen, restarts: restarts))
        window.makeKeyAndVisible()
        defer { window.isHidden = true }
        try await Task.sleep(for: .milliseconds(600))
        seen.value = 1
        try await Task.sleep(for: .milliseconds(600))
        seen.value = 2
        try await Task.sleep(for: .milliseconds(600))
        return seen.read
    }

    func testTaskWithIDReadsTheCurrentValue() async throws {
        let read = try await run(restarts: true)
        XCTAssertEqual(read.last, 2, "\(read)")
        XCTAssertTrue(read.contains(1), "\(read)")
    }

    // what LibraryView did before: the loop kept the value it started with
    func testPlainTaskKeepsItsFirstValue() async throws {
        let read = try await run(restarts: false)
        XCTAssertFalse(read.isEmpty)
        XCTAssertEqual(Set(read), [0], "\(read)")
    }
}
