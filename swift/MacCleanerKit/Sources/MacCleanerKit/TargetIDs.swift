import CryptoKit
import Foundation

enum TargetIDs {
    static func disambiguate(_ bases: [String], identities: [String]) -> [String] {
        let counts = Dictionary(bases.map { ($0, 1) }, uniquingKeysWith: +)
        return zip(bases, identities).map { base, identity in
            guard counts[base] != 1 else { return base }
            let digest = SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
            // slugify collapses repeated dashes, reserving this delimiter.
            return base + "--" + digest
        }
    }
}
