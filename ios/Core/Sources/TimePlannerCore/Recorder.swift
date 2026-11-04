import Foundation

/// What is being recorded right now.
public struct Running: Codable, Equatable, Sendable {
    public var bucket: Bucket
    public var summary: String
    public var start: Date

    public init(bucket: Bucket, summary: String, start: Date) {
        self.bucket = bucket
        self.summary = summary
        self.start = start
    }

    public func elapsed(at now: Date = Date()) -> TimeInterval { max(0, now.timeIntervalSince(start)) }
}

/// A finished stretch of time, ready to be written to the calendar.
public struct Segment: Codable, Equatable, Sendable {
    public var bucket: Bucket
    public var summary: String
    public var start: Date
    public var end: Date

    public init(bucket: Bucket, summary: String, start: Date, end: Date) {
        self.bucket = bucket
        self.summary = summary
        self.start = start
        self.end = end
    }

    /// `[bucket] summary` — the desktop side reads the bucket back from this prefix.
    public var title: String { "[\(bucket.rawValue)] \(summary)" }
}

/// Where the running activity is kept between launches.
public protocol RunningStore: AnyObject {
    func load() -> Running?
    func save(_ running: Running?)
}

public final class MemoryStore: RunningStore {
    private var value: Running?
    public init(_ value: Running? = nil) { self.value = value }
    public func load() -> Running? { value }
    public func save(_ running: Running?) { value = running }
}

/// UserDefaults-backed store (pass an App Group suite later to share with a widget / watch app).
public final class DefaultsStore: RunningStore {
    private let defaults: UserDefaults
    private let key: String

    public init(defaults: UserDefaults = .standard, key: String = "timeplanner.running") {
        self.defaults = defaults
        self.key = key
    }

    public func load() -> Running? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(Running.self, from: data)
    }

    public func save(_ running: Running?) {
        if let running, let data = try? JSONEncoder().encode(running) {
            defaults.set(data, forKey: key)
        } else {
            defaults.removeObject(forKey: key)
        }
    }
}

/// One-tap recording: starting a bucket closes whatever was running into segments. Same rules as the
/// desktop `core/running.py`: at least 1 minute, split at midnight, the summary defaults to the bucket.
public struct Recorder {
    public static let minSegment: TimeInterval = 60
    public static let windDownSummary = "收尾：写 next action + 收拾桌面"
    public static let windDownMinutes = 12

    public let store: RunningStore
    public var calendar: Calendar

    public init(store: RunningStore, calendar: Calendar = .current) {
        self.store = store
        self.calendar = calendar
    }

    public var current: Running? { store.load() }

    /// Start `bucket` at `now`, closing the running one. Returns the segments to write.
    @discardableResult
    public func start(_ bucket: Bucket, summary: String = "", at now: Date = Date()) -> [Segment] {
        let closed = stop(at: now)
        let text = summary.trimmingCharacters(in: .whitespacesAndNewlines)
        store.save(Running(bucket: bucket, summary: text.isEmpty ? bucket.defaultSummary : text, start: now))
        return closed
    }

    /// Stop recording at `now`. Returns the segments to write (empty when nothing was running).
    @discardableResult
    public func stop(at now: Date = Date()) -> [Segment] {
        guard let running = store.load() else { return [] }
        store.save(nil)
        return segments(for: running, end: now)
    }

    /// Split `[running.start, end]` at each midnight; never shorter than a minute.
    public func segments(for running: Running, end rawEnd: Date) -> [Segment] {
        let end = max(rawEnd, running.start.addingTimeInterval(Self.minSegment))
        var out: [Segment] = []
        var cursor = running.start
        while let midnight = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: cursor)),
              midnight < end {
            out.append(Segment(bucket: running.bucket, summary: running.summary, start: cursor, end: midnight))
            cursor = midnight
        }
        out.append(Segment(bucket: running.bucket, summary: running.summary, start: cursor, end: end))
        return out
    }

    /// Work → fun: offer the 10–15 minute wind-down first.
    public func wantsWindDown(to bucket: Bucket) -> Bool {
        guard let running = current else { return false }
        return running.bucket.isWork && bucket == .fun
    }

    /// A gentle note when starting work on the weekly rest day (`restWeekday` uses Calendar's 1 = Sunday … 7 = Saturday).
    public func offDayNote(for bucket: Bucket, on date: Date = Date(), restWeekday: Int?) -> String? {
        guard bucket.isWork, let restWeekday, calendar.component(.weekday, from: date) == restWeekday else { return nil }
        return "今天是休息日，确定要干活？"
    }
}

/// "0h23" past an hour, else "23min" — same as the softbar.
public func formatElapsed(_ seconds: TimeInterval) -> String {
    let minutes = max(0, Int(seconds) / 60)
    return minutes >= 60 ? String(format: "%dh%02d", minutes / 60, minutes % 60) : "\(minutes)min"
}
