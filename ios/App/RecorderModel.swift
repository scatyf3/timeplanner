import Combine
import Foundation
import SwiftUI
import TimePlannerCore

/// Glue between the tested core (Recorder / SegmentQueue) and the calendar. Every closed segment goes into the
/// queue first and is flushed to the calendar, so a failed write is retried later instead of lost.
@MainActor
final class RecorderModel: ObservableObject {
    @Published private(set) var running: Running?
    @Published private(set) var pendingCount = 0
    @Published var message: String?

    @AppStorage("calendarName") var calendarName = "Actual"
    @AppStorage("restWeekday") var restWeekday = 7          // 1 = Sunday … 7 = Saturday, 0 = off

    let calendar = CalendarService()
    private let recorder = Recorder(store: DefaultsStore())
    private let queue = SegmentQueue()

    private var forward: AnyCancellable?

    init() {
        running = recorder.current
        pendingCount = queue.items.count
        // calendar access / calendar list changes should redraw views that only observe the model
        forward = calendar.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
    }

    func onAppear() async {
        await calendar.requestAccess()
        flush()
    }

    func wantsWindDown(to bucket: Bucket) -> Bool { recorder.wantsWindDown(to: bucket) }

    func offDayNote(for bucket: Bucket) -> String? {
        recorder.offDayNote(for: bucket, restWeekday: restWeekday == 0 ? nil : restWeekday)
    }

    func start(_ bucket: Bucket, summary: String = "") {
        let closed = recorder.start(bucket, summary: summary)
        running = recorder.current
        record(closed, then: "开始 \(bucket.label)")
    }

    func stop() {
        let closed = recorder.stop()
        running = nil
        record(closed, then: closed.isEmpty ? "当前没有在记录" : nil)
    }

    func flush() {
        let name = calendarName
        var lastError: Error?
        queue.flush { segment in
            do {
                try calendar.write(segment, to: name)
            } catch {
                lastError = error
                throw error
            }
        }
        pendingCount = queue.items.count
        if let lastError, pendingCount > 0 {
            message = "\(pendingCount) 段还没写进日历：\(lastError.localizedDescription)"
        }
    }

    private func record(_ closed: [Segment], then note: String?) {
        queue.append(closed)
        flush()
        if pendingCount == 0 {
            let done = closed.last.map { "已记 \($0.title) · " } ?? ""
            message = note.map { done + $0 } ?? (done.isEmpty ? nil : String(done.dropLast(3)))
        }
    }
}
