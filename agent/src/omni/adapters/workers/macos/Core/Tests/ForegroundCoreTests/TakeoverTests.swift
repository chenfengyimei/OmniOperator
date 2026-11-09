// No system input, UI, sleeping or permission prompts. The real controller uses this port contract.
import XCTest
@testable import ForegroundCore

final class FixturePort: TakeoverPort {
    var now = 0.0, quietAt = 0.0
    var inputRevision: UInt64 = 0
    var ready = true, keysUp = true, allowed = true, cleanup = true
    var prepares = 0, authorizations = 0, sends = 0, confirmations = 0, closes = 0, checks = 0
    var prepared = false, armed = false
    var breakAtCheck: Int?
    var sendError: TakeoverCode?, confirmError: TakeoverCode?
    var expectedVerification: String?
    var onSleep: ((FixturePort) -> Void)?
    var changeAtArm = false
    func prepare(_ command: InputCommand) { prepares += 1; prepared = true }
    func focusReady() -> Bool { ready }
    func released() -> Bool { keysUp }
    func sleep(_ seconds: Double) { now += seconds; onSleep?(self) }
    func arm() { armed = true; quietAt = now; if changeAtArm { inputRevision += 1 } }
    func authorize() -> Bool { authorizations += 1; return allowed }
    func check() throws {
        checks += 1
        if breakAtCheck == checks { throw TakeoverCode.userIntervened }
    }
    func captureBefore() { }
    func send() throws { sends += 1; if let error = sendError { throw error } }
    func confirm() throws { confirmations += 1; if let error = confirmError { throw error } }
    func verification() -> String? { expectedVerification }
    func close() -> Bool { closes += 1; return cleanup }
}
final class TakeoverTests: XCTestCase {
    func text() throws -> InputCommand { try InputCommand(kind: "type_text", text: "owned-🙂") }
    func testManualQuietFocusAndReleasedKeysPrecedeAuthorization() throws {
        let port = FixturePort(); port.ready = false; port.keysUp = false
        port.onSleep = { p in
            if p.now < 0.5 { p.inputRevision += 1 }
            else { p.ready = true; p.keysUp = true }
        }
        let result = TakeoverController.execute(try text(), port: port)
        XCTAssertGreaterThanOrEqual(port.quietAt, 0.74)
        XCTAssertEqual(port.authorizations, 1); XCTAssertEqual(port.sends, 1); XCTAssertEqual(port.closes, 1)
        XCTAssertEqual(result.status, "unknown")
    }
    func testTenSecondFocusDeadlineDoesNotAuthorizeOrSend() throws {
        let port = FixturePort(); port.ready = false
        let result = TakeoverController.execute(try text(), port: port)
        XCTAssertGreaterThanOrEqual(port.now, 10); XCTAssertLessThan(port.now, 10.02)
        XCTAssertEqual(result.errorCode, TakeoverCode.focusTimeout.rawValue)
        XCTAssertEqual(result.effect, "none"); XCTAssertEqual(port.authorizations, 0); XCTAssertEqual(port.sends, 0)
        XCTAssertEqual(port.closes, 1)
    }
    func testPermitRefusalNeverSends() throws {
        let port = FixturePort(); port.allowed = false
        let result = TakeoverController.execute(try text(), port: port)
        XCTAssertEqual(result.status, "denied"); XCTAssertEqual(result.errorCode, TakeoverCode.revoked.rawValue)
        XCTAssertEqual(port.sends, 0); XCTAssertEqual(port.closes, 1)
    }
    func testInputBetweenQuietProofAndArmingInvalidatesReadiness() throws {
        let port = FixturePort(); port.changeAtArm = true
        let result = TakeoverController.execute(try text(), port: port)
        XCTAssertEqual(result.effect, "none"); XCTAssertEqual(result.errorCode, TakeoverCode.userIntervened.rawValue)
        XCTAssertEqual(port.authorizations, 0); XCTAssertEqual(port.sends, 0); XCTAssertEqual(port.closes, 1)
    }
    func testInterventionBeforeSendAndAfterSendHaveDifferentCertainty() throws {
        for (check, effect, sends) in [(2, "none", 0), (3, "none", 0), (4, "unknown", 1), (5, "unknown", 1)] {
            let port = FixturePort(); port.breakAtCheck = check; port.expectedVerification = "text_appended"
            let result = TakeoverController.execute(try text(), port: port)
            XCTAssertEqual(result.effect, effect); XCTAssertEqual(port.sends, sends); XCTAssertEqual(port.closes, 1)
        }
    }
    func testPartialOrUnconfirmedDispatchNeverRetries() throws {
        for error in [TakeoverCode.inputUnverified, .targetChanged, .permissionDenied, .monitorUnavailable] {
            let port = FixturePort(); port.sendError = error
            let result = TakeoverController.execute(try text(), port: port)
            XCTAssertEqual(result.status, "unknown"); XCTAssertEqual(port.sends, 1); XCTAssertEqual(port.confirmations, 0)
            XCTAssertEqual(port.closes, 1)
            let confirmation = FixturePort(); confirmation.confirmError = error
            XCTAssertEqual(TakeoverController.execute(try text(), port: confirmation).effect, "unknown")
            XCTAssertEqual(confirmation.sends, 1); XCTAssertEqual(confirmation.closes, 1)
        }
    }
    func testOnlyExactAppendCanClaimVerifiedSuccess() throws {
        let port = FixturePort(); port.expectedVerification = "text_appended"
        let result = TakeoverController.execute(try text(), port: port)
        XCTAssertEqual(result.status, "ok"); XCTAssertEqual(result.verification, "text_appended")
        for command in [try InputCommand(kind: "press_key", text: "Enter"), try InputCommand(kind: "click", text: "")] {
            let fake = FixturePort(); fake.expectedVerification = "text_appended"
            XCTAssertEqual(TakeoverController.execute(command, port: fake).status, "unknown")
        }
    }
    func testCleanupFailureOverridesBothSuccessAndNoDispatch() throws {
        let port = FixturePort(); port.expectedVerification = "text_appended"; port.cleanup = false
        XCTAssertEqual(TakeoverController.execute(try text(), port: port).status, "unknown")
        let denied = FixturePort(); denied.allowed = false; denied.cleanup = false
        let result = TakeoverController.execute(try text(), port: denied)
        XCTAssertEqual(result.status, "denied"); XCTAssertEqual(result.errorCode, TakeoverCode.cleanupFailed.rawValue)
    }
    func testInvalidTextKeysAndKindsAreRejectedWithoutAPort() throws {
        for (kind, text) in [("type_text", ""), ("type_text", String(repeating: "x", count: 129)),
            ("type_text", "line\n"), ("press_key", "Ctrl+A"), ("click", "123,456"), ("shell", "x")] {
            XCTAssertThrowsError(try InputCommand(kind: kind, text: text))
        }
        XCTAssertNoThrow(try InputCommand(kind: "type_text", text: String(repeating: "🙂", count: 128)))
    }
}

