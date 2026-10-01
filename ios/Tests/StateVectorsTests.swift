// The merge rule the Mac and the phone share: both run tests/state_vectors.json.

import XCTest

@testable import Readsync

final class StateVectorsTests: XCTestCase {
    func testSharedMergeVectors() throws {
        let url = Bundle(for: Self.self).url(forResource: "state_vectors", withExtension: "json")!
        let vectors = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [[String: Any]]
        XCTAssertFalse(vectors.isEmpty)
        for v in vectors {
            let files = v["files"] as! [[String: Any]]
            let got = ReadingState.merge(files, edition: v["edition"] as! String)
            let want = v["merged"] as! [String: Any]
            // numbers compare as numbers: Python writes 10, Swift reads 10.0
            XCTAssertEqual(normalize(got), normalize(want), v["name"] as? String ?? "")
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
