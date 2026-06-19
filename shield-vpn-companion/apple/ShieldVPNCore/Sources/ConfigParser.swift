import Foundation

public struct TunnelConfig: Equatable, Sendable {
    public let privateKey: String
    public let address: String
    public let dns: String
    public let peerPublicKey: String
    public let endpoint: String
    public let allowedIPs: String
    public let persistentKeepalive: Int

    public init(
        privateKey: String,
        address: String,
        dns: String,
        peerPublicKey: String,
        endpoint: String,
        allowedIPs: String,
        persistentKeepalive: Int = 25
    ) {
        self.privateKey = privateKey
        self.address = address
        self.dns = dns
        self.peerPublicKey = peerPublicKey
        self.endpoint = endpoint
        self.allowedIPs = allowedIPs
        self.persistentKeepalive = persistentKeepalive
    }

    public func toConfString() -> String {
        """
        [Interface]
        PrivateKey = \(privateKey)
        Address = \(address)
        DNS = \(dns)

        [Peer]
        PublicKey = \(peerPublicKey)
        Endpoint = \(endpoint)
        AllowedIPs = \(allowedIPs)
        PersistentKeepalive = \(persistentKeepalive)
        """
    }
}

public enum ConfigParseError: Error, Equatable {
    case emptyConfig
    case missingSection(String)
    case missingField(String, String)
    case invalidLine(String)
}

public enum ConfigParser {
    public static func parse(_ conf: String) throws -> TunnelConfig {
        let trimmed = conf.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw ConfigParseError.emptyConfig }

        var sections: [String: [String: String]] = [:]
        var currentSection: String?

        for rawLine in trimmed.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }

            if line.hasPrefix("[") && line.hasSuffix("]") {
                currentSection = String(line.dropFirst().dropLast())
                sections[currentSection!, default: [:]] = sections[currentSection!, default: [:]]
                continue
            }

            guard let eqIndex = line.firstIndex(of: "=") else {
                throw ConfigParseError.invalidLine(line)
            }

            guard let section = currentSection else {
                throw ConfigParseError.invalidLine(line)
            }

            let key = line[line.startIndex..<eqIndex].trimmingCharacters(in: .whitespaces)
            let value = line[line.index(after: eqIndex)...].trimmingCharacters(in: .whitespaces)
            sections[section, default: [:]][key] = value
        }

        guard let iface = sections["Interface"] else {
            throw ConfigParseError.missingSection("Interface")
        }
        guard let peer = sections["Peer"] else {
            throw ConfigParseError.missingSection("Peer")
        }

        func require(_ section: [String: String], _ sectionName: String, _ key: String) throws -> String {
            guard let value = section[key] else {
                throw ConfigParseError.missingField(sectionName, key)
            }
            return value
        }

        let keepalive = peer["PersistentKeepalive"].flatMap(Int.init) ?? 25

        return TunnelConfig(
            privateKey: try require(iface, "Interface", "PrivateKey"),
            address: try require(iface, "Interface", "Address"),
            dns: try require(iface, "Interface", "DNS"),
            peerPublicKey: try require(peer, "Peer", "PublicKey"),
            endpoint: try require(peer, "Peer", "Endpoint"),
            allowedIPs: try require(peer, "Peer", "AllowedIPs"),
            persistentKeepalive: keepalive
        )
    }
}
