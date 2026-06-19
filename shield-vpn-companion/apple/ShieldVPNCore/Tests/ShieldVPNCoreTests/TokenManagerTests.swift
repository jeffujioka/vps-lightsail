import XCTest
@testable import ShieldVPNCore

final class TokenManagerTests: XCTestCase {

    func testGenerateTokenIsCorrectLength() {
        let token = TokenManager.generateToken()
        XCTAssertEqual(token.count, 64)
    }

    func testGenerateTokenIsHex() {
        let token = TokenManager.generateToken()
        let hexCharSet = CharacterSet(charactersIn: "0123456789abcdef")
        for scalar in token.unicodeScalars {
            XCTAssertTrue(hexCharSet.contains(scalar), "Non-hex character: \(scalar)")
        }
    }

    func testGenerateTokenIsUnique() {
        let token1 = TokenManager.generateToken()
        let token2 = TokenManager.generateToken()
        XCTAssertNotEqual(token1, token2)
    }

    func testBearerAuthValidation() {
        let token = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        XCTAssertTrue(TokenManager.isAuthorized(header: "Bearer \(token)", expectedToken: token))
        XCTAssertFalse(TokenManager.isAuthorized(header: "Bearer wrong", expectedToken: token))
        XCTAssertFalse(TokenManager.isAuthorized(header: nil, expectedToken: token))
        XCTAssertFalse(TokenManager.isAuthorized(header: "", expectedToken: token))
        XCTAssertFalse(TokenManager.isAuthorized(header: "Basic \(token)", expectedToken: token))
    }
}
