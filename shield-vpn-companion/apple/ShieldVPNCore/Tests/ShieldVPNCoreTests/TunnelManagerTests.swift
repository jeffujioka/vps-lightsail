import XCTest
@testable import ShieldVPNCore

final class TunnelManagerTests: XCTestCase {

    var mock: MockCommandRunner!
    var fakeWgQuickPath: URL!

    override func setUp() {
        mock = MockCommandRunner()
        fakeWgQuickPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("wg-quick-test-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: fakeWgQuickPath.path, contents: nil)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: fakeWgQuickPath)
    }

    @MainActor
    func testConnectWritesConfAndRunsWgQuickUp() async throws {
        let config = TunnelConfig(
            privateKey: "testkey=",
            address: "10.8.0.2/32",
            dns: "1.1.1.1",
            peerPublicKey: "peerpub=",
            endpoint: "1.2.3.4:51820",
            allowedIPs: "0.0.0.0/0"
        )
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )

        try await manager.connect(config: config)

        XCTAssertEqual(manager.status, .connected)
        XCTAssertEqual(mock.privilegedCommands.count, 1)
        XCTAssert(mock.privilegedCommands[0].contains(fakeWgQuickPath.path))
        XCTAssert(mock.privilegedCommands[0].contains("up"))
        XCTAssert(mock.privilegedCommands[0].contains("wg-vpslightsail.conf"))

        let confPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("wg-vpslightsail.conf")
        let written = try String(contentsOf: confPath, encoding: .utf8)
        XCTAssert(written.contains("PrivateKey = testkey="))
        XCTAssert(written.contains("Endpoint = 1.2.3.4:51820"))
    }

    @MainActor
    func testDisconnectRunsWgQuickDown() async throws {
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )
        try await manager.connect(config: config)
        mock.privilegedCommands.removeAll()

        try await manager.disconnect()

        XCTAssertEqual(manager.status, .disconnected)
        XCTAssertEqual(mock.privilegedCommands.count, 1)
        XCTAssert(mock.privilegedCommands[0].contains(fakeWgQuickPath.path))
        XCTAssert(mock.privilegedCommands[0].contains("down"))
    }

    @MainActor
    func testConnectFailureReportsDisconnected() async {
        mock.shouldThrow = TunnelError.executionFailed("exit code 1")
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )

        do {
            try await manager.connect(config: config)
            XCTFail("Expected error")
        } catch {}

        XCTAssertEqual(manager.status, .disconnected)
    }

    @MainActor
    func testUserCancelledLeavesDisconnected() async {
        mock.shouldThrow = TunnelError.userCancelled
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )

        do {
            try await manager.connect(config: config)
            XCTFail("Expected error")
        } catch let error as TunnelError {
            XCTAssertEqual(error, TunnelError.userCancelled)
        } catch {
            XCTFail("Wrong error type")
        }

        XCTAssertEqual(manager.status, .disconnected)
    }

    @MainActor
    func testDisconnectFailureRevertsToConnected() async throws {
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )
        try await manager.connect(config: config)

        mock.shouldThrow = TunnelError.executionFailed("exit code 1")

        do {
            try await manager.disconnect()
            XCTFail("Expected error")
        } catch {}

        XCTAssertEqual(manager.status, .connected)
    }

    @MainActor
    func testDisconnectUserCancelledRevertsToConnected() async throws {
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )
        try await manager.connect(config: config)

        mock.shouldThrow = TunnelError.userCancelled

        do {
            try await manager.disconnect()
            XCTFail("Expected error")
        } catch let error as TunnelError {
            XCTAssertEqual(error, .userCancelled)
        }

        XCTAssertEqual(manager.status, .connected)
    }

    @MainActor
    func testConnectWhileConnectedDisconnectsFirst() async throws {
        let config = TunnelConfig(
            privateKey: "k=", address: "10.8.0.2/32", dns: "1.1.1.1",
            peerPublicKey: "p=", endpoint: "1.2.3.4:51820", allowedIPs: "0.0.0.0/0"
        )
        let manager = TunnelManager(
            commandRunner: mock,
            wgQuickPath: fakeWgQuickPath.path,
            configDir: FileManager.default.temporaryDirectory
        )

        try await manager.connect(config: config)
        XCTAssertEqual(manager.status, .connected)
        XCTAssertEqual(mock.privilegedCommands.count, 1)

        try await manager.connect(config: config)
        XCTAssertEqual(manager.status, .connected)
        // Should have: down (disconnect) + up (reconnect) = 2 more commands
        XCTAssertEqual(mock.privilegedCommands.count, 3)
        XCTAssert(mock.privilegedCommands[1].contains("down"))
        XCTAssert(mock.privilegedCommands[2].contains("up"))
    }
}
