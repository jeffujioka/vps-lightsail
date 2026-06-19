import Foundation

public protocol CommandRunner: Sendable {
    func runPrivileged(_ command: String) throws
    func run(_ executable: String, arguments: [String]) throws -> String
}
