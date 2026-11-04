import EventKit
import Foundation
import TimePlannerCore

enum CalendarError: LocalizedError {
    case noAccess
    case notFound(String)

    var errorDescription: String? {
        switch self {
        case .noAccess:
            return "没有日历权限：设置 → 隐私与安全性 → 日历 → TimePlanner 选「完全访问」。"
        case .notFound(let name):
            return "找不到日历「\(name)」：确认 Google 账户已加到 iPhone 日历，或在设置里换一个日历。"
        }
    }
}

/// Writes segments into a system calendar (the Google "Actual" calendar once the Google account is added to iOS).
/// Same sync route as the Shortcuts guide: no Google OAuth in the app.
@MainActor
final class CalendarService: ObservableObject {
    private let store = EKEventStore()
    @Published private(set) var authorized = false
    @Published private(set) var calendarNames: [String] = []

    func requestAccess() async {
        do {
            authorized = try await store.requestFullAccessToEvents()
        } catch {
            authorized = false
        }
        reload()
    }

    func reload() {
        guard authorized else {
            calendarNames = []
            return
        }
        store.refreshSourcesIfNecessary()
        calendarNames = store.calendars(for: .event)
            .filter(\.allowsContentModifications)
            .map(\.title)
            .sorted()
    }

    func write(_ segment: Segment, to calendarName: String) throws {
        guard authorized else { throw CalendarError.noAccess }
        guard let calendar = store.calendars(for: .event).first(where: { $0.title == calendarName }) else {
            throw CalendarError.notFound(calendarName)
        }
        let event = EKEvent(eventStore: store)
        event.calendar = calendar
        event.title = segment.title
        event.startDate = segment.start
        event.endDate = segment.end
        event.notes = "timeplanner-ios"
        try store.save(event, span: .thisEvent, commit: true)
    }
}
