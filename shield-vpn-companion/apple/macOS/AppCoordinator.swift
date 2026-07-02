import AppKit
import ShieldVPNCore

@MainActor
final class AppCoordinator: ObservableObject, HTTPServerDelegate {

    let tunnelManager = TunnelManager(
        commandRunner: SystemCommandRunner(),
        wgQuickPath: TunnelManager.findWgQuick() ?? "/opt/homebrew/bin/wg-quick",
        configDir: FileManager.default.temporaryDirectory
    )
    private var httpServer: HTTPServer?

    let token = TokenManager.token

    init() {
        start()
    }

    func start() {
        guard httpServer == nil else { return }
        let server = HTTPServer(token: token)
        server.delegate = self
        server.start()
        httpServer = server
    }

    func stop() {
        httpServer?.stop()
    }

    nonisolated func httpServer(_ server: HTTPServer, didReceiveConfig body: String) -> Result<Void, Error> {
        dispatchPrecondition(condition: .notOnQueue(.main))
        do {
            let config = try ConfigParser.parse(body)
            let semaphore = DispatchSemaphore(value: 0)
            var connectError: Error?
            Task { @MainActor in
                do {
                    try await self.tunnelManager.connect(config: config)
                } catch {
                    connectError = error
                }
                semaphore.signal()
            }
            semaphore.wait()
            if let error = connectError {
                return .failure(error)
            }
            return .success(())
        } catch {
            return .failure(error)
        }
    }

    nonisolated func httpServerDidReceiveDisconnect(_ server: HTTPServer) -> Result<Void, Error> {
        dispatchPrecondition(condition: .notOnQueue(.main))
        let semaphore = DispatchSemaphore(value: 0)
        var disconnectError: Error?
        Task { @MainActor in
            do {
                try await self.tunnelManager.disconnect()
            } catch {
                disconnectError = error
            }
            semaphore.signal()
        }
        semaphore.wait()
        if let error = disconnectError {
            return .failure(error)
        }
        return .success(())
    }
}
