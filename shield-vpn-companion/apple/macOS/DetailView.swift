import SwiftUI
import ShieldVPNCore

struct DetailView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: statusIcon)
                .font(.system(size: 48))
                .foregroundStyle(statusColor)

            Text(coordinator.tunnelManager.status.rawValue.capitalized)
                .font(.title2)

            if !coordinator.tunnelManager.wgQuickAvailable {
                GroupBox {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("wireguard-tools not found", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Text("brew install wireguard-tools")
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(4)
                }
            }

            GroupBox("Auth Token") {
                Text(coordinator.token)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity)
                    .padding(4)
            }

            if coordinator.tunnelManager.status == .connected {
                Button("Disconnect") {
                    Task { try? await coordinator.tunnelManager.disconnect() }
                }
                .controlSize(.large)
            }

            Spacer()
        }
        .padding()
        .frame(width: 360, height: 280)
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
