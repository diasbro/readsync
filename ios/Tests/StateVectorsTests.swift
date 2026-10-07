// The merge rule the Mac and the phone share: both run tests/state_vectors.json; and the status derived from
// the merge, tests/status_vectors.json.

import XCTest

@testable import Readsync

final class StateVectorsTests: XCTestCase {
    func testSharedMergeVectors() throws {
        let url = Bundle(for: Self.self).url(forResource: "state_vectors", withExtension: "json")!
        let vectors = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [[String: Any]]
        XCTAssertFalse(vectors.isEmpty)
        for v in vectors {
            let files = v["files"] as! [[String: Any]]
            let got = ReadingState.merge(
                files, edition: v["edition"] as! String, editions: Editions(json: v["editions"]))
            let want = v["merged"] as! [String: Any]
            // numbers compare as numbers: Python writes 10, Swift reads 10.0
            XCTAssertEqual(normalize(got), normalize(want), v["name"] as? String ?? "")
        }
    }

    func testSharedStatusVectors() throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "status_vectors", withExtension: "json"))
        let vectors = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [[String: Any]])
        XCTAssertFalse(vectors.isEmpty)
        for v in vectors {
            let name = v["name"] as? String ?? ""
            let state = try XCTUnwrap(v["state"] as? [String: Any], name)
            let want = try XCTUnwrap(v["expect"] as? [String: Any], name)
            let got = ReadingState.status(state, audio: v["audio"] as? Bool ?? false, atEnd: v["atEnd"] as? Bool ?? false)
            XCTAssertEqual(got.status.rawValue, want["status"] as? String, name)
            XCTAssertEqual(got.rereading, want["rereading"] as? Bool, name)
            XCTAssertEqual(got.finishedOn, want["finishedOn"] as? String, name)  // JSON null: nil
        }
    }

    private func normalize(_ value: Any) -> NSObject {
        switch value {
        case let d as [String: Any]: return d.mapValues { normalize($0) } as NSDictionary
        case let n as NSNumber: return NSNumber(value: n.doubleValue)
        default: return value as! NSObject
        }
    }
}
