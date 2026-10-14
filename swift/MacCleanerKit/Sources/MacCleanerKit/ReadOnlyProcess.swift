import Foundation
import Darwin

/// Internal subprocess plumbing for read-only probes, never deletion commands.
enum ReadOnlyProcess {
    /// Own process group + file-backed stdout: a shell pipeline cannot keep a
    /// read-to-EOF blocked after the timeout, and its children are killed too.
    static func run(_ arguments: [String], executable: String = "/usr/bin/env",
                    timeout: TimeInterval, allowFailure: Bool = false) -> String? {
        var name = Array((NSTemporaryDirectory() + "mc-read-only-XXXXXX").utf8CString)
        let fd = mkstemp(&name)
        guard fd >= 0 else { return nil }
        let path = String(cString: name)
        defer { close(fd); unlink(path) }
        var actions: posix_spawn_file_actions_t?
        var attrs: posix_spawnattr_t?
        guard posix_spawn_file_actions_init(&actions) == 0 else { return nil }
        defer { posix_spawn_file_actions_destroy(&actions) }
        guard posix_spawnattr_init(&attrs) == 0 else { return nil }
        defer { posix_spawnattr_destroy(&attrs) }
        guard posix_spawn_file_actions_adddup2(&actions, fd, STDOUT_FILENO) == 0,
              posix_spawn_file_actions_addclose(&actions, fd) == 0,
              posix_spawn_file_actions_addopen(&actions, STDERR_FILENO, "/dev/null", O_WRONLY, 0) == 0,
              posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0) == 0,
              posix_spawnattr_setflags(&attrs, Int16(POSIX_SPAWN_SETPGROUP)) == 0,
              posix_spawnattr_setpgroup(&attrs, 0) == 0 else { return nil }
        let fullArguments = [executable] + arguments
        let strings = fullArguments.map { $0.withCString { strdup($0) } }
        defer { strings.forEach { free($0) } }
        let environment = ProcessInfo.processInfo.environment.map {
            ($0.key + "=" + $0.value).withCString { strdup($0) }
        }
        defer { environment.forEach { free($0) } }
        var envp = environment + [nil]
        var argv = strings + [nil]
        var pid: pid_t = 0
        guard posix_spawn(&pid, executable, &actions, &attrs, &argv, &envp) == 0 else { return nil }
        let deadline = ProcessInfo.processInfo.systemUptime + max(0, timeout)
        var status: Int32 = 0
        while true {
            let result = waitpid(pid, &status, WNOHANG)
            if result == pid { break }
            if result == -1 && errno != EINTR { return nil }
            if ProcessInfo.processInfo.systemUptime >= deadline {
                kill(-pid, SIGKILL)
                while waitpid(pid, &status, 0) == -1 && errno == EINTR {}
                return nil
            }
            Thread.sleep(forTimeInterval: 0.01)
        }
        guard (status == 0 || allowFailure), let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let output = String(data: data, encoding: .utf8) else { return nil }
        return output
    }
}
