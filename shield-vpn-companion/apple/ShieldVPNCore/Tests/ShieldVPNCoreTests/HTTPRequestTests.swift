import XCTest
@testable import ShieldVPNCore

final class HTTPRequestTests: XCTestCase {

    func testParsePostConfig() throws {
        let raw = "POST /config HTTP/1.1\r\nHost: 192.168.1.10:8080\r\nAuthorization: Bearer abc123\r\nContent-Length: 11\r\n\r\nhello world"
        let request = try HTTPRequest.parse(Data(raw.utf8))
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(request.path, "/config")
        XCTAssertEqual(request.headers["authorization"], "Bearer abc123")
        XCTAssertEqual(request.body, "hello world")
    }

    func testParseGetHealth() throws {
        let raw = "GET /health HTTP/1.1\r\nHost: 192.168.1.10:8080\r\n\r\n"
        let request = try HTTPRequest.parse(Data(raw.utf8))
        XCTAssertEqual(request.method, "GET")
        XCTAssertEqual(request.path, "/health")
        XCTAssertEqual(request.body, "")
    }

    func testParseInvalidRequestThrows() {
        XCTAssertThrowsError(try HTTPRequest.parse(Data("garbage".utf8)))
    }

    func testResponseSerialization() {
        let response = HTTPResponse(status: 200, statusText: "OK", body: "hello")
        let data = response.serialize()
        let str = String(data: data, encoding: .utf8)!
        XCTAssertTrue(str.hasPrefix("HTTP/1.1 200 OK\r\n"))
        XCTAssertTrue(str.contains("Content-Length: 5\r\n"))
        XCTAssertTrue(str.hasSuffix("hello"))
    }
}
