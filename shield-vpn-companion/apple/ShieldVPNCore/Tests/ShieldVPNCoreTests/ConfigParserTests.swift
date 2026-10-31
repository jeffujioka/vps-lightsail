import XCTest
@testable import ShieldVPNCore

final class ConfigParserTests: XCTestCase {

    let validConfig = """
    [Interface]
    PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
    Address = 10.8.0.2/32
    DNS = 1.1.1.1

    [Peer]
    PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
    Endpoint = 203.0.113.1:51820
    AllowedIPs = 0.0.0.0/0
    PersistentKeepalive = 25
    """

    func testParseValidConfig() throws {
        let config = try ConfigParser.parse(validConfig)
        XCTAssertEqual(config.privateKey, "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=")
        XCTAssertEqual(config.address, "10.8.0.2/32")
        XCTAssertEqual(config.dns, "1.1.1.1")
        XCTAssertEqual(config.peerPublicKey, "xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=")
        XCTAssertEqual(config.endpoint, "203.0.113.1:51820")
        XCTAssertEqual(config.allowedIPs, "0.0.0.0/0")
        XCTAssertEqual(config.persistentKeepalive, 25)
    }

    func testParseDefaultKeepalive() throws {
        let noKeepalive = """
        [Interface]
        PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
        Address = 10.8.0.2/32
        DNS = 1.1.1.1

        [Peer]
        PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
        Endpoint = 203.0.113.1:51820
        AllowedIPs = 0.0.0.0/0
        """
        let config = try ConfigParser.parse(noKeepalive)
        XCTAssertEqual(config.persistentKeepalive, 25)
    }

    func testParseEmptyStringThrows() {
        XCTAssertThrowsError(try ConfigParser.parse("")) { error in
            XCTAssertEqual(error as? ConfigParseError, .emptyConfig)
        }
    }

    func testParseMissingInterfaceThrows() {
        let noPeer = """
        [Peer]
        PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
        Endpoint = 203.0.113.1:51820
        AllowedIPs = 0.0.0.0/0
        """
        XCTAssertThrowsError(try ConfigParser.parse(noPeer)) { error in
            XCTAssertEqual(error as? ConfigParseError, .missingSection("Interface"))
        }
    }

    func testParseMissingFieldThrows() {
        let noPrivateKey = """
        [Interface]
        Address = 10.8.0.2/32
        DNS = 1.1.1.1

        [Peer]
        PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
        Endpoint = 203.0.113.1:51820
        AllowedIPs = 0.0.0.0/0
        """
        XCTAssertThrowsError(try ConfigParser.parse(noPrivateKey)) { error in
            XCTAssertEqual(error as? ConfigParseError, .missingField("Interface", "PrivateKey"))
        }
    }

    func testToConfStringRoundTrips() throws {
        let config = try ConfigParser.parse(validConfig)
        let output = config.toConfString()
        let reparsed = try ConfigParser.parse(output)
        XCTAssertEqual(config, reparsed)
    }
}
