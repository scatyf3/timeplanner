import XCTest
@testable import TimePlannerCore

final class RecorderTests: XCTestCase {
    private var cal: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "America/New_York")!
        return c
    }()

    private func at(_ day: Int, _ hour: Int, _ minute: Int) -> Date {
        cal.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }

    private func recorder() -> Recorder { Recorder(store: MemoryStore(), calendar: cal) }

    func testStartWithoutRunningWritesNothing() {
        let r = recorder()
        XCTAssertEqual(r.start(.main, summary: "写 method", at: at(4, 14, 0)), [])
        XCTAssertEqual(r.current, Running(bucket: .main, summary: "写 method", start: at(4, 14, 0)))
    }

    func testSwitchClosesPreviousSegment() {
        let r = recorder()
        r.start(.main, summary: "写 method", at: at(4, 14, 0))
        let closed = r.start(.fun, at: at(4, 16, 30))
        XCTAssertEqual(closed, [Segment(bucket: .main, summary: "写 method", start: at(4, 14, 0), end: at(4, 16, 30))])
        XCTAssertEqual(r.current?.bucket, .fun)
        XCTAssertEqual(r.current?.summary, "玩", "bucket alone → default summary")
    }

    func testStopAndNothingToStop() {
        let r = recorder()
        r.start(.life, at: at(4, 12, 0))
        XCTAssertEqual(r.stop(at: at(4, 12, 40)).count, 1)
        XCTAssertNil(r.current)
        XCTAssertEqual(r.stop(at: at(4, 13, 0)), [])
    }

    func testAtLeastOneMinute() {
        let r = recorder()
        r.start(.side, at: at(4, 9, 0))
        let seg = r.stop(at: at(4, 9, 0))
        XCTAssertEqual(seg.first?.end, at(4, 9, 1))
    }

    func testSplitAtMidnight() {
        let r = recorder()
        r.start(.fun, at: at(4, 23, 0))
        let segs = r.stop(at: at(5, 1, 15))
        XCTAssertEqual(segs.map(\.start), [at(4, 23, 0), at(5, 0, 0)])
        XCTAssertEqual(segs.map(\.end), [at(5, 0, 0), at(5, 1, 15)])
    }

    func testSplitAcrossTwoMidnights() {
        let r = recorder()
        r.start(.health, at: at(4, 22, 0))
        XCTAssertEqual(r.stop(at: at(6, 1, 0)).count, 3)
    }

    func testWhitespaceSummaryFallsBackToDefault() {
        let r = recorder()
        r.start(.health, summary: "   ", at: at(4, 8, 0))
        XCTAssertEqual(r.current?.summary, "健康")
    }

    func testWindDownOnlyFromWorkToFun() {
        let r = recorder()
        XCTAssertFalse(r.wantsWindDown(to: .fun), "nothing running")
        r.start(.main, at: at(4, 14, 0))
        XCTAssertTrue(r.wantsWindDown(to: .fun))
        XCTAssertFalse(r.wantsWindDown(to: .life))
        r.start(.life, at: at(4, 15, 0))
        XCTAssertFalse(r.wantsWindDown(to: .fun))
    }

    func testOffDayNoteOnRestWeekday() {
        let r = recorder()
        let saturday = at(7, 10, 0)        // 2026-11-07 is a Saturday
        XCTAssertNotNil(r.offDayNote(for: .main, on: saturday, restWeekday: 7))
        XCTAssertNil(r.offDayNote(for: .fun, on: saturday, restWeekday: 7))
        XCTAssertNil(r.offDayNote(for: .main, on: at(9, 10, 0), restWeekday: 7))
        XCTAssertNil(r.offDayNote(for: .main, on: saturday, restWeekday: nil))
    }

    func testTitleRoundTripsThroughTheDesktopParser() {
        for bucket in Bucket.allCases {
            let title = Segment(bucket: bucket, summary: "x", start: at(4, 9, 0), end: at(4, 10, 0)).title
            XCTAssertEqual(Bucket.fromTitle(title), bucket, title)
        }
    }

    /// Same cases as the Python `classify.explicit_bucket` tests.
    func testFromTitleMatchesDesktopRules() {
        let cases: [(String, Bucket?)] = [
            ("[fun] 打塔", .fun), ("fun 打塔", .fun), ("health：健身", .health), ("【life】吃饭", .life),
            ("MAIN: paper", .main), ("funny cat", nil), ("写 method", nil), ("", nil),
        ]
        for (title, want) in cases {
            XCTAssertEqual(Bucket.fromTitle(title), want, title)
        }
    }

    func testDefaultsStorePersists() {
        let suite = "timeplanner.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let r1 = Recorder(store: DefaultsStore(defaults: defaults), calendar: cal)
        r1.start(.main, summary: "debug", at: at(4, 14, 0))
        let r2 = Recorder(store: DefaultsStore(defaults: defaults), calendar: cal)   // "relaunch"
        XCTAssertEqual(r2.current?.summary, "debug")
    }

    func testQueueKeepsFailedSegments() {
        let suite = "timeplanner.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        struct Offline: Error {}
        let q = SegmentQueue(defaults: defaults)
        let a = Segment(bucket: .main, summary: "a", start: at(4, 9, 0), end: at(4, 10, 0))
        let b = Segment(bucket: .fun, summary: "b", start: at(4, 10, 0), end: at(4, 11, 0))
        q.append([a, b])
        XCTAssertEqual(q.flush { s in if s.summary == "b" { throw Offline() } }, 1)
        XCTAssertEqual(q.items, [b])
        XCTAssertEqual(q.flush { _ in }, 1)
        XCTAssertEqual(q.items, [])
    }

    func testFormatElapsed() {
        XCTAssertEqual(formatElapsed(0), "0min")
        XCTAssertEqual(formatElapsed(23 * 60 + 59), "23min")
        XCTAssertEqual(formatElapsed(83 * 60), "1h23")
    }
}
