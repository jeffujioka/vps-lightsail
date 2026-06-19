import Foundation

public enum TunnelStatus: String, Sendable {
    case disconnected
    case connecting
    case connected
    case disconnecting
}
