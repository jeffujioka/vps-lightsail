import NetworkExtension
import WireGuardKit
import ShieldVPNCore

class PacketTunnelProvider: NEPacketTunnelProvider {

    private lazy var adapter: WireGuardAdapter = {
        WireGuardAdapter(with: self) { _, message in
            NSLog("[WG] %@", message)
        }
    }()

    override func startTunnel(options: [String: NSObject]?) async throws {
        let configString: String
        if let proto = protocolConfiguration as? NETunnelProviderProtocol,
           let conf = proto.providerConfiguration?["config"] as? String {
            configString = conf
        } else {
            let defaults = UserDefaults(suiteName: "group.com.jeffujioka.vpslightsail")
            guard let saved = defaults?.string(forKey: "tunnelConfig") else {
                throw NEVPNError(.configurationInvalid)
            }
            configString = saved
        }

        let tunnelConfig = try ConfigParser.parse(configString)
        let wgQuickConfig = tunnelConfig.toConfString()

        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            adapter.start(tunnelConfiguration: try! TunnelConfiguration(fromWgQuickConfig: wgQuickConfig)) { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason) async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            adapter.stop { _ in
                continuation.resume()
            }
        }
    }
}
