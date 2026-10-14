import Foundation
import Darwin

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

    /// Own process group + file-backed stdout: a shell pipeline cannot keep a
    /// read-to-EOF blocked after the timeout, and its children are killed too.
    static func run(_ command: String, parser: String, timeout: TimeInterval = 15) -> Int64 {
        var name = Array((NSTemporaryDirectory() + "mc-estimate-XXXXXX").utf8CString)
        let fd = mkstemp(&name)
        guard fd >= 0 else { return 0 }
        let path = String(cString: name)
        defer { close(fd); unlink(path) }
        var actions: posix_spawn_file_actions_t?
        var attrs: posix_spawnattr_t?
        guard posix_spawn_file_actions_init(&actions) == 0 else { return 0 }
        defer { posix_spawn_file_actions_destroy(&actions) }
        guard posix_spawnattr_init(&attrs) == 0 else { return 0 }
        defer { posix_spawnattr_destroy(&attrs) }
        guard posix_spawn_file_actions_adddup2(&actions, fd, STDOUT_FILENO) == 0,
              posix_spawn_file_actions_addclose(&actions, fd) == 0,
              posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0) == 0,
              posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0) == 0,
              posix_spawnattr_setflags(&attrs, Int16(POSIX_SPAWN_SETPGROUP)) == 0,
              posix_spawnattr_setpgroup(&attrs, 0) == 0 else { return 0 }
        let arguments: [String] = ["/bin/sh", "-c", command]
        let strings = arguments.map { $0.withCString { strdup($0) } }
        defer { strings.forEach { free($0) } }
        let environment = ProcessInfo.processInfo.environment.map {
            ($0.key + "=" + $0.value).withCString { strdup($0) }
        }
        defer { environment.forEach { free($0) } }
        var envp = environment + [nil]
        var argv = strings + [nil]
        var pid: pid_t = 0
        guard posix_spawn(&pid, "/bin/sh", &actions, &attrs, &argv, &envp) == 0 else { return 0 }
        let deadline = ProcessInfo.processInfo.systemUptime + max(0, timeout)
        var status: Int32 = 0
        while true {
            let result = waitpid(pid, &status, WNOHANG)
            if result == pid { break }
            if result == -1 && errno != EINTR { return 0 }
            if ProcessInfo.processInfo.systemUptime >= deadline {
                kill(-pid, SIGKILL)
                while waitpid(pid, &status, 0) == -1 && errno == EINTR {}
                return 0
            }
            Thread.sleep(forTimeInterval: 0.01)
        }
        guard status == 0, let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let output = String(data: data, encoding: .utf8) else { return 0 }
        return parse(output, parser: parser)
    }
}
