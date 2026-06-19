import SwiftUI
import ShieldVPNCore

struct MenuBarView: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                Text(coordinator.tunnelManager.status.rawValue.capitalized)
                    .font(.headline)
            }

            Divider()

            if coordinator.tunnelManager.status == .connected {
                Button("Disconnect") {
                    Task { try? await coordinator.tunnelManager.disconnect() }
                }
            }

            Button("Show Details...") {
                openWindow(id: "detail")
            }

            Divider()

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
            .keyboardShortcut("q")
        }
        .padding(8)
        .frame(width: 200)
    }

    private var statusColor: Color {
        switch coordinator.tunnelManager.status {
        case .connected: .green
        case .connecting, .disconnecting: .orange
        case .disconnected: .red
        }
    }
}
