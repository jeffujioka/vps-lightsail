import SwiftUI
import ShieldVPNCore

struct ContentView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(spacing: 24) {
            statusSection
            tokenSection
            controlSection
        }
        .padding()
    }

    private var statusSection: some View {
        VStack(spacing: 8) {
            Image(systemName: statusIcon)
                .font(.system(size: 48))
                .foregroundStyle(statusColor)
            Text(coordinator.tunnelManager.status.rawValue.capitalized)
                .font(.headline)
        }
    }

    private var tokenSection: some View {
        VStack(spacing: 4) {
            Text("Auth Token")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(coordinator.token)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .padding(8)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private var controlSection: some View {
        Group {
            switch coordinator.tunnelManager.status {
            case .connected:
                Button("Disconnect") {
                    Task { try? await coordinator.tunnelManager.disconnect() }
                }
                .tint(.red)
            case .disconnected:
                Text("Waiting for config push...")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            default:
                ProgressView()
            }
        }
    }

    private var statusIcon: String {
        switch coordinator.tunnelManager.status {
        case .connected: "lock.shield.fill"
        case .connecting: "arrow.triangle.2.circlepath"
        case .disconnecting: "arrow.triangle.2.circlepath"
        case .disconnected: "lock.shield"
        }
    }

    private var statusColor: Color {
        switch coordinator.tunnelManager.status {
        case .connected: .green
        case .connecting, .disconnecting: .orange
        case .disconnected: .secondary
        }
    }
}
