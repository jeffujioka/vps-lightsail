import Foundation

public enum TunnelError: LocalizedError, Equatable {
    case wgQuickNotFound
    case executionFailed(String)
    case userCancelled

    public var errorDescription: String? {
        switch self {
        case .wgQuickNotFound:
            return "wg-quick not found. Install with: brew install wireguard-tools"
        case .executionFailed(let msg):
            return "Tunnel command failed: \(msg)"
        case .userCancelled:
            return "Authentication cancelled"
        }
    }

    public static func == (lhs: TunnelError, rhs: TunnelError) -> Bool {
        switch (lhs, rhs) {
        case (.wgQuickNotFound, .wgQuickNotFound): return true
        case (.userCancelled, .userCancelled): return true
        case (.executionFailed(let a), .executionFailed(let b)): return a == b
        default: return false
        }
    }
}
