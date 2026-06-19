import AppKit
import ShieldVPNCore

final class SystemCommandRunner: CommandRunner {

    func runPrivileged(_ command: String) throws {
        let escaped = command.replacingOccurrences(of: "\\", with: "\\\\")
                            .replacingOccurrences(of: "\"", with: "\\\"")
        let source = """
            do shell script "\(escaped)" with administrator privileges
            """
        let script = NSAppleScript(source: source)
        var errorInfo: NSDictionary?
        script?.executeAndReturnError(&errorInfo)
        if let errorInfo {
            let message = errorInfo[NSAppleScript.errorMessage] as? String ?? "Unknown error"
            if message.contains("User canceled") {
                throw TunnelError.userCancelled
            }
            throw TunnelError.executionFailed(message)
        }
    }

    func run(_ executable: String, arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.arguments = [executable] + arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw TunnelError.executionFailed("exit code \(process.terminationStatus)")
        }
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }
}
