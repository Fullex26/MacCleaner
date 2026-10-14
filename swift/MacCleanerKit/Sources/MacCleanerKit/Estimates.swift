import Foundation

/// Only estimate commands from the generated table enter here, never Target.cmd.
enum Estimates {
    static func parse(_ output: String, parser: String) -> Int64 {
        func matches(_ pattern: String, _ text: String) -> [[String]] {
            guard let re = try? NSRegularExpression(pattern: pattern) else { return [] }
            let ns = text as NSString
            return re.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { m in
                (1..<m.numberOfRanges).map { ns.substring(with: m.range(at: $0)) }
            }
        }
        func bytes(_ fields: [String]) -> Int64? {
            let units: [String: Double] = ["B": 1, "KB": 1024, "MB": 1048576,
                                           "GB": 1073741824, "TB": 1099511627776]
            guard fields.count == 2, let value = Double(fields[0]), let unit = units[fields[1]] else { return nil }
            let size = value * unit
            guard size.isFinite, size >= 0, size < Double(Int64.max) else { return nil }
            return Int64(size)
        }
        var amounts: [[String]] = []
        switch parser {
        case "brew_dry_run":
            amounts = Array(matches(#"free approximately ([0-9.]+)\s*(KB|MB|GB|TB)"#, output).prefix(1))
        case "conda_dry_run":
            amounts = matches(#"\(([0-9.]+)\s*(B|KB|MB|GB|TB)\)"#, output)
        case "docker_df":
            for line in output.trimmingCharacters(in: .whitespacesAndNewlines).components(separatedBy: .newlines).dropFirst() {
                let line = line.trimmingCharacters(in: .whitespaces)
                guard let type = ["Images", "Containers", "Build Cache"].first(where: { line.hasPrefix($0) }) else { continue }
                let fields = line.dropFirst(type.count).split(whereSeparator: { $0.isWhitespace })
                if fields.count >= 4 { amounts += matches(#"^([0-9.]+)(B|KB|MB|GB|TB)"#, String(fields[3])) }
            }
        case "du_path":
            guard let first = output.split(whereSeparator: { $0.isWhitespace }).first,
                  let kb = Int64(first) else { return 0 }
            let (size, overflow) = kb.multipliedReportingOverflow(by: 1024)
            return overflow ? 0 : size
        default: return 0
        }
        var total: Int64 = 0
        for amount in amounts {
            guard let size = bytes(amount) else { return 0 }
            let (sum, overflow) = total.addingReportingOverflow(size)
            guard !overflow else { return 0 }
            total = sum
        }
        return total
    }

    static func run(_ command: String, parser: String, timeout: TimeInterval = 15) -> Int64 {
        guard let output = ReadOnlyProcess.run(["-c", command], executable: "/bin/sh",
                                              timeout: timeout) else { return 0 }
        return parse(output, parser: parser)
    }
}
