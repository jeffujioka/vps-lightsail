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

    public init(port: UInt16 = ShieldVPNCore.httpPort, token: String) {
        self.port = port
        self.token = token
        let params = NWParameters.tcp
        params.includePeerToPeer = true
        self.listener = try! NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
        listener.service = NWListener.Service(
            name: "vps-lightsail",
            type: ShieldVPNCore.serviceType
        )
    }

    public func start() {
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
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, _, _ in
            guard let self, let data else {
                connection.cancel()
                return
            }
            let response = self.route(data)
            connection.send(content: response.serialize(), completion: .contentProcessed { _ in
                connection.cancel()
            })
        }
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
