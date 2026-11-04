import Foundation

/// The five buckets, same names and colors as the Python side (`timeplanner/core/gcal.py`).
public enum Bucket: String, CaseIterable, Codable, Sendable, Identifiable {
    case main, side, life, health, fun

    public var id: String { rawValue }

    /// Button label (matches the softbar).
    public var label: String {
        switch self {
        case .main: return "main"
        case .side: return "side"
        case .life: return "生活"
        case .health: return "健康"
        case .fun: return "玩"
        }
    }

    /// Summary used when you only tap a bucket (matches `running.DEFAULT_SUMMARY`).
    public var defaultSummary: String { label }

    /// Same palette as `BUCKET_HEX` (Google Calendar colors).
    public var colorHex: String {
        switch self {
        case .main, .side: return "#039be5"
        case .life: return "#f6bf26"
        case .health: return "#33b679"
        case .fun: return "#0b8043"
        }
    }

    /// main/side are "work" for the wind-down and rest-day prompts.
    public var isWork: Bool { self == .main || self == .side }

    private static let titlePattern = try! NSRegularExpression(
        pattern: #"^\s*[\[【(（]?\s*(main|side|life|health|fun)(?:\s*[\]】)）:：]|\s|$)"#,
        options: [.caseInsensitive])

    /// The bucket written at the start of an event title — `[fun] 打塔`, `fun 打塔`, `health：健身`.
    /// Mirrors `classify.explicit_bucket`, which is how the desktop side reads events this app writes.
    public static func fromTitle(_ title: String) -> Bucket? {
        let range = NSRange(title.startIndex..., in: title)
        guard let m = titlePattern.firstMatch(in: title, options: [], range: range),
              let r = Range(m.range(at: 1), in: title) else { return nil }
        return Bucket(rawValue: title[r].lowercased())
    }
}
