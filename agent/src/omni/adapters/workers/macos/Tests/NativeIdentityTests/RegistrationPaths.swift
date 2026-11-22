// Local disposable metadata only; no service, desktop, account or TCC mutation.
import Testing
import Foundation
import Darwin
import NativeIdentity

struct RegistrationPathTests {
    private func fixture(_ body: (URL, URL) throws -> Void) throws {
        #expect(getuid() > 0, "Run as the ordinary test account")
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appendingPathComponent("owned.plist")
        try Data("owned-fixture".utf8).write(to: file, options: .withoutOverwriting)
        #expect(chmod(file.path, 0o600) == 0)
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
        #expect(process.terminationStatus == 0)
    }

    @Test func testDefaultFileAndDirectoryHavePositiveAbsenceProof() throws {
        try fixture { directory, file in
            try mode(["-N", file.path])
            try mode(["-N", directory.path])
            #expect(safe(directory) == 1)
            #expect(safe(file) == 1)
        }
    }

    @Test func testDefaultDenyDeleteIsAccepted() throws {
        try fixture { _, file in
            defer { try? mode(["-N", file.path]) }
            try mode(["+a", "everyone deny delete", file.path])
            #expect(safe(file) == 1)
        }
    }

    @Test func testPositiveACLDoesNotBecomeSafeBecauseModeIsPrivate() throws {
        try fixture { _, file in
            defer { try? mode(["-N", file.path]) }
            try mode(["+a", "everyone allow read", file.path])
            #expect(safe(file) == 0)
        }
    }

    @Test func testSymbolicLinkIsRejected() throws {
        try fixture { directory, file in
            let link = directory.appendingPathComponent("link")
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
            #expect(safe(link) == 0)
        }
    }

    @Test func testHardLinkIsRejected() throws {
        try fixture { directory, file in
            try FileManager.default.linkItem(at: file, to: directory.appendingPathComponent("alias"))
            #expect(safe(file) == 0)
        }
    }

    @Test func testGroupWritableFileAndDirectoryAreRejected() throws {
        try fixture { directory, file in
            #expect(chmod(file.path, 0o620) == 0)
            #expect(safe(file) == 0)
            #expect(chmod(directory.path, 0o720) == 0)
            #expect(safe(directory) == 0)
        }
    }

    @Test func testSystemPathsCannotUseAnOwnerControlledObject() throws {
        try fixture { directory, file in
            #expect(safe(directory, system: true) == 0)
            #expect(safe(file, system: true) == 0)
        }
    }

    @Test func testMissingLeafDoesNotPreventNonOverwritingRegistration() throws {
        try fixture { directory, _ in
            #expect(safe(directory.appendingPathComponent("not-created")) == 1)
        }
    }
}
