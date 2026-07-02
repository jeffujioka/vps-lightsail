import Foundation
import Network

public protocol HTTPServerDelegate: AnyObject, Sendable {
    func httpServer(_ server: HTTPServer, didReceiveConfig body: String) throws
    func httpServerDidReceiveDisconnect(_ server: HTTPServer) throws
}

public final class HTTPServer: @unchecked Sendable {

    private let listener: NWListener
    private let port: UInt16
    private let token: String
    public weak var delegate: HTTPServerDelegate?
    public private(set) var isReady = false

    public init(port: UInt16 = ShieldVPNCore.httpPort, token: String) {
        self.port = port
        self.token = token
        let params = NWParameters.tcp
        params.includePeerToPeer = true
        self.listener = try! NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
        listener.service = NWListener.Service(
            name: "vps-lightsail",
            type: ShieldVPNCore.serviceType,
            domain: nil
        )
    }

    public func start() {
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.isReady = true
                print("[HTTP] Listening on port \(self.port)")
            case .failed(let error):
                // NWListener cannot be restarted after cancel().
                // User must relaunch the app to recover.
                self.isReady = false
                print("[HTTP] Listener failed: \(error). Relaunch app to retry.")
            case .cancelled:
                self.isReady = false
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.handleConnection(connection)
        }
        listener.start(queue: .global(qos: .userInitiated))
    }

    public func stop() {
        listener.cancel()
    }

    private func handleConnection(_ connection: NWConnection) {
        connection.start(queue: .global(qos: .userInitiated))
        receiveAll(connection: connection, buffer: Data())
    }

    private func receiveAll(connection: NWConnection, buffer: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, _ in
            guard let self else { connection.cancel(); return }
            guard let data else { connection.cancel(); return }

            var accumulated = buffer
            accumulated.append(data)

            if self.hasCompleteRequest(accumulated) || isComplete {
                let response = self.route(accumulated)
                connection.send(content: response.serialize(), completion: .contentProcessed { _ in
                    connection.cancel()
                })
            } else {
                self.receiveAll(connection: connection, buffer: accumulated)
            }
        }
    }

    private func hasCompleteRequest(_ data: Data) -> Bool {
        guard let str = String(data: data, encoding: .utf8) else { return true }
        guard let headerEnd = str.range(of: "\r\n\r\n") else { return false }
        let headerSection = str[str.startIndex..<headerEnd.lowerBound]
        let bodyStart = headerEnd.upperBound
        let currentBodyLength = str[bodyStart...].utf8.count

        for line in headerSection.split(separator: "\r\n") {
            let lower = line.lowercased()
            if lower.hasPrefix("content-length:") {
                let value = lower.dropFirst("content-length:".count).trimmingCharacters(in: .whitespaces)
                if let expected = Int(value) {
                    return currentBodyLength >= expected
                }
            }
        }
        return true
    }

    private func route(_ data: Data) -> HTTPResponse {
        guard let request = try? HTTPRequest.parse(data) else {
            return .badRequest
        }

        switch (request.method, request.path) {
        case ("GET", "/health"):
            return .ok
        case ("POST", "/config"):
            return handleConfig(request)
        case ("POST", "/disconnect"):
            return handleDisconnect(request)
        default:
            return .notFound
        }
    }

    private func handleConfig(_ request: HTTPRequest) -> HTTPResponse {
        guard TokenManager.isAuthorized(header: request.headers["authorization"], expectedToken: token) else {
            return .forbidden
        }
        guard !request.body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return .badRequest
        }
        do {
            try delegate?.httpServer(self, didReceiveConfig: request.body)
            return HTTPResponse(status: 200, statusText: "OK", body: "OK \u{2014} tunnel activated")
        } catch {
            return .internalError(error.localizedDescription)
        }
    }

    private func handleDisconnect(_ request: HTTPRequest) -> HTTPResponse {
        guard TokenManager.isAuthorized(header: request.headers["authorization"], expectedToken: token) else {
            return .forbidden
        }
        do {
            try delegate?.httpServerDidReceiveDisconnect(self)
            return HTTPResponse(status: 200, statusText: "OK", body: "Disconnected")
        } catch {
            return .internalError(error.localizedDescription)
        }
    }
}
