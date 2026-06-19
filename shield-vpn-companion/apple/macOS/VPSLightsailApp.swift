import SwiftUI
import ServiceManagement

@main
struct VPSLightsailApp: App {
    @StateObject private var coordinator = AppCoordinator()

    init() {
        registerLoginItem()
    }

    var body: some Scene {
        MenuBarExtra("VPN", systemImage: menuBarIcon) {
            MenuBarView()
                .environmentObject(coordinator)
        }
        .menuBarExtraStyle(.window)

        Window("vps-lightsail", id: "detail") {
            DetailView()
                .environmentObject(coordinator)
        }
        .windowResizability(.contentSize)
    }

    private var menuBarIcon: String {
        switch coordinator.tunnelManager.status {
        case .connected: "lock.shield.fill"
        case .disconnected: "lock.shield"
        default: "arrow.triangle.2.circlepath"
        }
    }

    private func registerLoginItem() {
        if #available(macOS 13.0, *) {
            try? SMAppService.mainApp.register()
        }
    }
}
