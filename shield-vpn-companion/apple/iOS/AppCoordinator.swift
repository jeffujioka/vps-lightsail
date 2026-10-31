import Foundation
import ShieldVPNCore

@MainActor
final class AppCoordinator: ObservableObject, HTTPServerDelegate {

    let tunnelManager = TunnelManager()
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

    nonisolated func httpServer(_ server: HTTPServer, didReceiveConfig body: String) -> Result<Void, Error> {
        return .success(())
    }

    nonisolated func httpServerDidReceiveDisconnect(_ server: HTTPServer) -> Result<Void, Error> {
        return .success(())
    }
}
