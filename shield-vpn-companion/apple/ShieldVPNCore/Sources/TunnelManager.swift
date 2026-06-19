import Foundation
import NetworkExtension

public enum TunnelStatus: String, Sendable {
    case disconnected
    case connecting
    case connected
    case disconnecting
}

@MainActor
public final class TunnelManager: ObservableObject {

    @Published public private(set) var status: TunnelStatus = .disconnected

    private var manager: NETunnelProviderManager?
    private let appGroup = "group.com.jeffujioka.vpslightsail"

    public init() {
        Task { await loadManager() }
        NotificationCenter.default.addObserver(
            self, selector: #selector(vpnStatusChanged),
            name: .NEVPNStatusDidChange, object: nil
        )
    }

    public func connect(config: TunnelConfig) async throws {
        let manager = try await getOrCreateManager()

        let defaults = UserDefaults(suiteName: appGroup)
        defaults?.set(config.toConfString(), forKey: "tunnelConfig")

        let proto = NETunnelProviderProtocol()
        proto.providerBundleIdentifier = tunnelBundleId()
        proto.serverAddress = config.endpoint
        proto.providerConfiguration = ["config": config.toConfString()]

        manager.protocolConfiguration = proto
        manager.localizedDescription = "vps-lightsail"
        manager.isEnabled = true

        let onDemandRule = NEOnDemandRuleConnect()
        onDemandRule.interfaceTypeMatch = .any
        manager.onDemandRules = [onDemandRule]
        manager.isOnDemandEnabled = true

        try await manager.saveToPreferences()
        try await manager.loadFromPreferences()
        try manager.connection.startVPNTunnel()
    }

    public func disconnect() async throws {
        guard let manager else { return }
        manager.isOnDemandEnabled = false
        try await manager.saveToPreferences()
        manager.connection.stopVPNTunnel()
    }

    private func loadManager() async {
        let managers = try? await NETunnelProviderManager.loadAllFromPreferences()
        manager = managers?.first
        updateStatus()
    }

    private func getOrCreateManager() async throws -> NETunnelProviderManager {
        if let manager { return manager }
        let m = NETunnelProviderManager()
        self.manager = m
        return m
    }

    private func tunnelBundleId() -> String {
        #if os(iOS)
        return "com.jeffujioka.vpslightsail.ios.tunnel"
        #else
        return "com.jeffujioka.vpslightsail.macos.tunnel"
        #endif
    }

    @objc private func vpnStatusChanged() {
        updateStatus()
    }

    private func updateStatus() {
        guard let connection = manager?.connection else {
            status = .disconnected
            return
        }
        switch connection.status {
        case .connected: status = .connected
        case .connecting, .reasserting: status = .connecting
        case .disconnecting: status = .disconnecting
        case .disconnected, .invalid: status = .disconnected
        @unknown default: status = .disconnected
        }
    }
}
