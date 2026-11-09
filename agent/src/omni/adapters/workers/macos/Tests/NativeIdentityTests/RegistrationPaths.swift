// Local disposable metadata only; no service, desktop, account or TCC mutation.
import XCTest
import Foundation
import Darwin
import NativeIdentity

final class RegistrationPathTests: XCTestCase {
    private func fixture(_ body: (URL, URL) throws -> Void) throws {
        XCTAssertGreaterThan(getuid(), 0, "Run as the ordinary test account")
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("owned.plist")
        try Data("owned-fixture".utf8).write(to: file, options: .withoutOverwriting)
        XCTAssertEqual(chmod(file.path, 0o600), 0)
        try body(directory, file)
    }

    private func safe(_ path: URL, system: Bool = false) -> Int32 {
        path.path.withCString { system ? omni_system_registration_path_safe($0) : omni_registration_path_safe($0) }
    }

    private func mode(_ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/chmod")
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
    }

    func testDefaultFileAndDirectoryHavePositiveAbsenceProof() throws {
        try fixture { directory, file in
            try mode(["-N", file.path])
            try mode(["-N", directory.path])
            XCTAssertEqual(safe(directory), 1)
            XCTAssertEqual(safe(file), 1)
        }
    }

    func testDefaultDenyDeleteIsAccepted() throws {
        try fixture { _, file in
            defer { try? mode(["-N", file.path]) }
            try mode(["+a", "everyone deny delete", file.path])
            XCTAssertEqual(safe(file), 1)
        }
    }

    func testPositiveACLDoesNotBecomeSafeBecauseModeIsPrivate() throws {
        try fixture { _, file in
            defer { try? mode(["-N", file.path]) }
            try mode(["+a", "everyone allow read", file.path])
            XCTAssertEqual(safe(file), 0)
        }
    }

    func testSymbolicLinkIsRejected() throws {
        try fixture { directory, file in
            let link = directory.appendingPathComponent("link")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
            XCTAssertEqual(safe(link), 0)
        }
    }

    func testHardLinkIsRejected() throws {
        try fixture { directory, file in
            try FileManager.default.linkItem(at: file, to: directory.appendingPathComponent("alias"))
            XCTAssertEqual(safe(file), 0)
        }
    }

    func testGroupWritableFileAndDirectoryAreRejected() throws {
        try fixture { directory, file in
            XCTAssertEqual(chmod(file.path, 0o620), 0)
            XCTAssertEqual(safe(file), 0)
            XCTAssertEqual(chmod(directory.path, 0o720), 0)
            XCTAssertEqual(safe(directory), 0)
        }
    }

    func testSystemPathsCannotUseAnOwnerControlledObject() throws {
        try fixture { directory, file in
            XCTAssertEqual(safe(directory, system: true), 0)
            XCTAssertEqual(safe(file, system: true), 0)
        }
    }

    func testMissingLeafDoesNotPreventNonOverwritingRegistration() throws {
        try fixture { directory, _ in
            XCTAssertEqual(safe(directory.appendingPathComponent("not-created")), 1)
        }
    }
}
