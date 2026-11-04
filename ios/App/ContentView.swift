import SwiftUI
import TimePlannerCore

extension Color {
    init(hex: String) {
        let v = UInt64(hex.dropFirst(), radix: 16) ?? 0
        self.init(red: Double((v >> 16) & 0xff) / 255, green: Double((v >> 8) & 0xff) / 255,
                  blue: Double(v & 0xff) / 255)
    }
}

struct ContentView: View {
    @EnvironmentObject var model: RecorderModel
    @State private var summary = ""
    @State private var askWindDown = false
    @State private var offDay: (bucket: Bucket, note: String)?
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                status
                TextField("在干嘛（可选）", text: $summary)
                    .textFieldStyle(.roundedBorder)
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                    ForEach(Bucket.allCases) { bucket in bucketButton(bucket) }
                    Button(role: .destructive) { model.stop() } label: {
                        Label("停止", systemImage: "stop.fill").frame(maxWidth: .infinity, minHeight: 56)
                    }
                    .buttonStyle(.bordered)
                    .disabled(model.running == nil)
                }
                if let message = model.message {
                    Text(message).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                if model.pendingCount > 0 {
                    Button("重试写入（\(model.pendingCount) 段）") { model.flush() }
                }
                Spacer()
            }
            .padding()
            .navigationTitle("TimePlanner")
            .toolbar {
                Button { showSettings = true } label: { Image(systemName: "gearshape") }
            }
            .sheet(isPresented: $showSettings) { SettingsView().environmentObject(model) }
            .confirmationDialog("先收尾 \(Recorder.windDownMinutes) 分钟？写 next action + 收拾桌面",
                                isPresented: $askWindDown, titleVisibility: .visible) {
                Button("好，先收尾") { model.start(.life, summary: Recorder.windDownSummary) }
                Button("直接玩") { begin(.fun) }
                Button("取消", role: .cancel) {}
            }
            .alert(offDay?.note ?? "", isPresented: Binding(get: { offDay != nil }, set: { if !$0 { offDay = nil } })) {
                Button("照样开始") { if let b = offDay?.bucket { begin(b) } }
                Button("算了", role: .cancel) {}
            }
            .task { await model.onAppear() }
        }
    }

    private var status: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(spacing: 6) {
                if let r = model.running {
                    Text("▶ \(r.bucket.label)").font(.title2.bold()).foregroundStyle(Color(hex: r.bucket.colorHex))
                    if r.summary != r.bucket.defaultSummary { Text(r.summary).font(.headline) }
                    Text(formatElapsed(r.elapsed(at: context.date))).font(.system(.largeTitle, design: .rounded).monospacedDigit())
                } else {
                    Text("⏸ 未在记录").font(.title2).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, minHeight: 120)
        }
    }

    private func bucketButton(_ bucket: Bucket) -> some View {
        let on = model.running?.bucket == bucket
        let color = Color(hex: bucket.colorHex)
        return Button { choose(bucket) } label: {
            Text(bucket.label).font(.headline).frame(maxWidth: .infinity, minHeight: 56)
                .foregroundStyle(on ? .white : color)
                .background(on ? color : color.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        }
        .accessibilityIdentifier("bucket-\(bucket.rawValue)")
    }

    private func choose(_ bucket: Bucket) {
        if let note = model.offDayNote(for: bucket) {
            offDay = (bucket, note)
        } else if model.wantsWindDown(to: bucket) {
            askWindDown = true
        } else {
            begin(bucket)
        }
    }

    private func begin(_ bucket: Bucket) {
        model.start(bucket, summary: summary)
        summary = ""
    }
}

struct SettingsView: View {
    @EnvironmentObject var model: RecorderModel
    @Environment(\.dismiss) private var dismiss
    private let weekdays = ["关闭", "周日", "周一", "周二", "周三", "周四", "周五", "周六"]

    var body: some View {
        NavigationStack {
            Form {
                Section("写进哪个日历") {
                    if model.calendar.calendarNames.isEmpty {
                        Text("没有可写的日历：先给 TimePlanner 日历权限，并在 iPhone 设置里添加 Google 账户。")
                            .foregroundStyle(.secondary)
                    }
                    Picker("日历", selection: $model.calendarName) {
                        ForEach(model.calendar.calendarNames, id: \.self) { Text($0).tag($0) }
                        if !model.calendar.calendarNames.contains(model.calendarName) {
                            Text("\(model.calendarName)（未找到）").tag(model.calendarName)
                        }
                    }
                }
                Section("每周休息日（点 main/side 会先确认）") {
                    Picker("休息日", selection: $model.restWeekday) {
                        ForEach(0..<weekdays.count, id: \.self) { Text(weekdays[$0]).tag($0) }
                    }
                }
            }
            .navigationTitle("设置")
            .toolbar { Button("完成") { dismiss() } }
            .onAppear { model.calendar.reload() }
        }
    }
}
