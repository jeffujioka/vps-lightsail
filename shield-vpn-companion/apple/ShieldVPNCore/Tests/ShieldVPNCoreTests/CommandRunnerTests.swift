import XCTest
@testable import ShieldVPNCore

final class MockCommandRunner: CommandRunner {
    var privilegedCommands: [String] = []
    var runResults: [String: String] = [:]
    var shouldThrow: Error?

    func runPrivileged(_ command: String) throws {
        if let error = shouldThrow { throw error }
        privilegedCommands.append(command)
    }

    func run(_ executable: String, arguments: [String]) throws -> String {
        if let error = shouldThrow { throw error }
        let key = ([executable] + arguments).joined(separator: " ")
        return runResults[key] ?? ""
    }
}

final class CommandRunnerTests: XCTestCase {
    func testMockRecordsPrivilegedCommands() throws {
        let mock = MockCommandRunner()
        try mock.runPrivileged("echo hello")
        XCTAssertEqual(mock.privilegedCommands, ["echo hello"])
    }

    func testMockReturnsConfiguredRunOutput() throws {
        let mock = MockCommandRunner()
        mock.runResults["wg show"] = "interface: utun4"
        let output = try mock.run("wg", arguments: ["show"])
        XCTAssertEqual(output, "interface: utun4")
    }

    func testMockThrowsWhenConfigured() {
        let mock = MockCommandRunner()
        mock.shouldThrow = NSError(domain: "test", code: 1)
        XCTAssertThrowsError(try mock.runPrivileged("anything"))
    }
}
