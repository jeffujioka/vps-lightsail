import Foundation
import Network

public final class MdnsAnnouncer: @unchecked Sendable {

    private let listener: NWListener
    private let serviceName: String

    public init(port: UInt16 = ShieldVPNCore.httpPort, serviceName: String? = nil) {
        let params = NWParameters.tcp
        params.includePeerToPeer = true

        self.listener = try! NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
        self.serviceName = serviceName ?? "vps-lightsail"

        listener.service = NWListener.Service(
            name: self.serviceName,
            type: ShieldVPNCore.serviceType
        )
    }

    public func start() {
        listener.stateUpdateHandler = { state in
            switch state {
            case .ready:
                print("[mDNS] Announcing \(self.serviceName) on \(ShieldVPNCore.serviceType)")
            case .failed(let error):
                print("[mDNS] Failed: \(error)")
            default:
                break
            }
        }
        listener.newConnectionHandler = { connection in
            connection.cancel()
        }
        listener.start(queue: .global(qos: .utility))
    }

    public func stop() {
        listener.cancel()
    }
}
