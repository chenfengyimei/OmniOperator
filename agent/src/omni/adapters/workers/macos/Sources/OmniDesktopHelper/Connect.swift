// Owner-hosted Unix socket transport for a LaunchServices-launched helper.
// The helper is a child of launchd and has no inherited pipes, so it connects
// back to the host's private socket; validation failures never fall back to stdio.
import Darwin
import Foundation

private let maximumSocketPath = 100

private func reject() -> Never { exit(2) }

/// Validate and connect to the host-owned socket, returning the connected fd.
func connectHostSocket(_ path: String) -> Int32 {
    guard path.hasPrefix("/"), path.utf8.count < maximumSocketPath, !path.contains("\0") else { reject() }
    let url = URL(fileURLWithPath: path)
    let directory = url.deletingLastPathComponent().path
    var info = stat()
    guard lstat(directory, &info) == 0,
          (info.st_mode & S_IFMT) == S_IFDIR,
          info.st_uid == getuid(),
          info.st_mode & 0o077 == 0 else { reject() }
    guard lstat(path, &info) == 0,
          (info.st_mode & S_IFMT) == S_IFSOCK,
          info.st_uid == getuid() else { reject() }
    let fd = socket(AF_UNIX, SOCK_STREAM, 0)
    guard fd >= 0 else { reject() }
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    let capacity = MemoryLayout.size(ofValue: address.sun_path)
    path.utf8CString.withUnsafeBufferPointer { source in
        withUnsafeMutablePointer(to: &address.sun_path) { target in
            target.withMemoryRebound(to: CChar.self, capacity: capacity) { characters in
                let length = min(source.count - 1, capacity - 1)
                for index in 0..<length { characters[index] = source[index] }
                characters[length] = 0
            }
        }
    }
    let length = socklen_t(MemoryLayout<sockaddr_un>.size)
    let result = withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, length) }
    }
    guard result == 0 else { close(fd); reject() }
    var uid: uid_t = 0, gid: gid_t = 0
    guard getpeereid(fd, &uid, &gid) == 0, uid == getuid() else { close(fd); reject() }
    return fd
}
