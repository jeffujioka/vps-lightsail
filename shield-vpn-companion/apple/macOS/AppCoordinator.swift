import Foundation
import ShieldVPNCore

@MainActor
final class AppCoordinator: ObservableObject, HTTPServerDelegate {

    let tunnelManager = TunnelManager(
        commandRunner: SystemCommandRunner(),
        wgQuickPath: TunnelManager.findWgQuick() ?? "/opt/homebrew/bin/wg-quick",
        configDir: FileManager.default.temporaryDirectory
    )
    private var httpServer: HTTPServer?
    private var mdnsAnnouncer: MdnsAnnouncer?

    let token = TokenManager.token

    func start() {
        let server = HTTPServer(token: token)
        server.delegate = self
        server.start()
        httpServer = server

        let announcer = MdnsAnnouncer()
        announcer.start()
        mdnsAnnouncer = announcer
    }

    func stop() {
        httpServer?.stop()
        mdnsAnnouncer?.stop()
    }

    nonisolated func httpServer(_ server: HTTPServer, didReceiveConfig body: String) throws {
        let config = try ConfigParser.parse(body)
        Task { @MainActor in
            try await tunnelManager.connect(config: config)
        }
    }

    nonisolated func httpServerDidReceiveDisconnect(_ server: HTTPServer) throws {
        Task { @MainActor in
            try await tunnelManager.disconnect()
        }
    }
}
