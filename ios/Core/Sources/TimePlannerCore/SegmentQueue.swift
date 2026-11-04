import Foundation

/// Segments that couldn't be written yet (no calendar access, calendar missing, offline). Kept until a retry works,
/// so a failed write never loses time.
public final class SegmentQueue {
    private let defaults: UserDefaults
    private let key: String

    public init(defaults: UserDefaults = .standard, key: String = "timeplanner.pending") {
        self.defaults = defaults
        self.key = key
    }

    public var items: [Segment] {
        guard let data = defaults.data(forKey: key),
              let list = try? JSONDecoder().decode([Segment].self, from: data) else { return [] }
        return list
    }

    public func append(_ segments: [Segment]) {
        guard !segments.isEmpty else { return }
        write(items + segments)
    }

    /// Try to write every queued segment with `writer`; the ones that throw stay queued. Returns how many were written.
    @discardableResult
    public func flush(_ writer: (Segment) throws -> Void) -> Int {
        var left: [Segment] = []
        var written = 0
        for segment in items {
            do {
                try writer(segment)
                written += 1
            } catch {
                left.append(segment)
            }
        }
        write(left)
        return written
    }

    private func write(_ list: [Segment]) {
        if list.isEmpty {
            defaults.removeObject(forKey: key)
        } else if let data = try? JSONEncoder().encode(list) {
            defaults.set(data, forKey: key)
        }
    }
}
