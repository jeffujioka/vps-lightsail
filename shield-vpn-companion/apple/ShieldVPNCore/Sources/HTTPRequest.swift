import Foundation

public struct HTTPRequest: Sendable {
    public let method: String
    public let path: String
    public let headers: [String: String]
    public let body: String

    public static func parse(_ data: Data) throws -> HTTPRequest {
        guard let str = String(data: data, encoding: .utf8) else {
            throw HTTPParseError.invalidEncoding
        }

        let headerBodySplit = str.components(separatedBy: "\r\n\r\n")
        guard headerBodySplit.count >= 1 else { throw HTTPParseError.malformed }

        let headerSection = headerBodySplit[0]
        let body = headerBodySplit.count > 1 ? headerBodySplit[1...].joined(separator: "\r\n\r\n") : ""

        let lines = headerSection.components(separatedBy: "\r\n")
        guard let requestLine = lines.first else { throw HTTPParseError.malformed }

        let parts = requestLine.split(separator: " ", maxSplits: 2)
        guard parts.count >= 2 else { throw HTTPParseError.malformed }

        let method = String(parts[0])
        let path = String(parts[1])

        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }
            guard let colonIndex = trimmed.firstIndex(of: ":") else { continue }
            let key = trimmed[trimmed.startIndex..<colonIndex].trimmingCharacters(in: .whitespaces).lowercased()
            let value = trimmed[trimmed.index(after: colonIndex)...].trimmingCharacters(in: .whitespaces)
            headers[key] = value
        }

        return HTTPRequest(method: method, path: path, headers: headers, body: body)
    }
}

public struct HTTPResponse: Sendable {
    public let status: Int
    public let statusText: String
    public let body: String

    public init(status: Int, statusText: String, body: String) {
        self.status = status
        self.statusText = statusText
        self.body = body
    }

    public func serialize() -> Data {
        let bodyData = Data(body.utf8)
        let header = "HTTP/1.1 \(status) \(statusText)\r\nContent-Length: \(bodyData.count)\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
        return Data(header.utf8) + bodyData
    }

    public static let ok = HTTPResponse(status: 200, statusText: "OK", body: "ok")
    public static let notFound = HTTPResponse(status: 404, statusText: "Not Found", body: "Not found")
    public static let forbidden = HTTPResponse(status: 403, statusText: "Forbidden", body: "Unauthorized")
    public static let badRequest = HTTPResponse(status: 400, statusText: "Bad Request", body: "Bad request")

    public static func internalError(_ message: String) -> HTTPResponse {
        HTTPResponse(status: 500, statusText: "Internal Server Error", body: "Failed: \(message)")
    }
}

public enum HTTPParseError: Error {
    case invalidEncoding
    case malformed
}
