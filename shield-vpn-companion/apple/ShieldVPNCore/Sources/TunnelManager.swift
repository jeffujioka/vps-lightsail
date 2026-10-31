import Foundation

@MainActor
public final class TunnelManager: ObservableObject {

    @Published public private(set) var status: TunnelStatus = .disconnected

    private let commandRunner: CommandRunner
    private let wgQuickPath: String
    private let configDir: URL
    private let wireguardRunDir: String
    private let confName = "wg-vpslightsail.conf"
    private var statusTimer: Timer?

    public init(
        commandRunner: CommandRunner,
        wgQuickPath: String,
        configDir: URL,
        wireguardRunDir: String = "/var/run/wireguard"
    ) {
        self.commandRunner = commandRunner
        self.wgQuickPath = wgQuickPath
        self.configDir = configDir
        self.wireguardRunDir = wireguardRunDir
    }

    public var wgQuickAvailable: Bool {
        FileManager.default.fileExists(atPath: wgQuickPath)
    }

    public func connect(config: TunnelConfig) async throws {
        guard FileManager.default.fileExists(atPath: wgQuickPath) else {
            throw TunnelError.wgQuickNotFound
        }

        if status == .connected {
            try await disconnect()
        }

        status = .connecting
        let confPath = configDir.appendingPathComponent(confName)

        do {
            try config.toConfString().write(to: confPath, atomically: true, encoding: .utf8)
            try commandRunner.runPrivileged("\(wgQuickPath) up \(confPath.path)")
            status = .connected
            startStatusMonitor()
        } catch {
            status = .disconnected
            throw error
        }
    }

    public func disconnect() async throws {
        let confPath = configDir.appendingPathComponent(confName)
        guard FileManager.default.fileExists(atPath: confPath.path) else { return }

        status = .disconnecting
        do {
            try commandRunner.runPrivileged("\(wgQuickPath) down \(confPath.path)")
            status = .disconnected
            stopStatusMonitor()
        } catch {
            status = .connected
            throw error
        }
    }

    private func startStatusMonitor() {
        statusTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.checkStatus()
            }
        }
    }

    private func stopStatusMonitor() {
        statusTimer?.invalidate()
        statusTimer = nil
    }

    public func forceCheckStatus() {
        checkStatus()
    }

    private func checkStatus() {
        let name = confName.replacingOccurrences(of: ".conf", with: "")
        let socketPath = "\(wireguardRunDir)/\(name).name"
        if FileManager.default.fileExists(atPath: socketPath) {
            status = .connected
        } else {
            status = .disconnected
            stopStatusMonitor()
        }
    }

    public static func findWgQuick() -> String? {
        let paths = ["/opt/homebrew/bin/wg-quick", "/usr/local/bin/wg-quick"]
        return paths.first { FileManager.default.fileExists(atPath: $0) }
    }
}
