import SwiftUI

struct WorkView: View {
    @EnvironmentObject var model: PortalModel
    @State private var request = ""
    @State private var kind = "research"
    @State private var selected: WorkOperation?
    @State private var artifactText: String?

    var body: some View {
        NavigationStack {
            VStack {
                VStack {
                    Picker("Kind", selection: $kind) { Text("Research").tag("research"); Text("Testimony").tag("testimony") }.pickerStyle(.segmented)
                    TextField("Describe the work (stable operation id is assigned locally)", text: $request, axis: .vertical).textFieldStyle(.roundedBorder)
                    Button("Record and dispatch") { let text = request; request = ""; Task { await model.submitWork(kind: kind, text: text) } }
                        .buttonStyle(.borderedProminent).disabled(request.isEmpty)
                    if !model.connection.isConnected { Text("Offline: the request is kept on the phone and sent on reconnect.").font(.caption2).foregroundStyle(.orange) }
                }.padding(.horizontal)
                if let a = model.backendStatus?.allocator {
                    Text(allocatorLine(a)).font(.caption2).foregroundStyle(.secondary).padding(.horizontal)
                }
                List {
                    ForEach(model.localWork.filter { $0.state == "pending" }) { item in
                        VStack(alignment: .leading) {
                            Text(item.requestText).font(.caption).lineLimit(2)
                            HStack {
                                Text("Queued locally on phone").font(.caption.bold()).foregroundStyle(.orange)
                                Text(item.operationId.prefix(8)).font(.caption2).foregroundStyle(.secondary)
                            }
                            if let e = item.lastError { Text(e).font(.caption2).foregroundStyle(.secondary).lineLimit(2) }
                        }
                    }
                    ForEach(model.work) { op in
                    Button { Task { selected = await model.workDetail(op.operation_id) } } label: {
                        VStack(alignment: .leading) {
                            Text(op.request_text).font(.caption).lineLimit(2)
                            HStack {
                                Text(op.statusLabel).font(.caption.bold()).foregroundStyle(color(op.status))
                                Text(op.operation_id.prefix(8)).font(.caption2).foregroundStyle(.secondary)
                            }
                            if let e = op.last_error { Text(e).font(.caption2).foregroundStyle(.secondary).lineLimit(2) }
                        }
                    }
                    }
                }
                .refreshable { await model.syncLocalWork(); await model.refreshWork() }
            }
            .navigationTitle("Work")
            .task { await model.refreshWork() }
            .sheet(item: $selected) { op in
                NavigationStack {
                    List {
                        Section("Operation") {
                            Text(op.request_text)
                            Text("Status: \(op.statusLabel)").bold()
                            if let n = op.note { Text(n).font(.caption) }
                            HStack {
                                Button("Dispatch") { Task { await model.dispatchWork(op.operation_id); selected = await model.workDetail(op.operation_id) } }.disabled(!(op.status == "queued_locally" || op.status == "backend_unavailable"))
                                Button("Reconcile") { Task { await model.reconcileWork(op.operation_id); selected = await model.workDetail(op.operation_id) } }
                                Button("Source packet") { Task { await model.buildSourcePacket(op.operation_id); selected = await model.workDetail(op.operation_id) } }
                            }.font(.caption).buttonStyle(.bordered)
                        }
                        Section("Attempts (exact CLI evidence)") {
                            ForEach(op.attempts ?? []) { a in
                                VStack(alignment: .leading) {
                                    Text("\(a.attempt_id) · \(a.outcome)").font(.caption.bold())
                                    if let m = a.message_id { Text("message_id \(m) run \(a.run_id ?? "-")").font(.caption2) }
                                    if let s = a.stdout, !s.isEmpty { Text(s).font(.caption2).lineLimit(4) }
                                    if let s = a.stderr, !s.isEmpty { Text(s).font(.caption2).foregroundStyle(.red).lineLimit(4) }
                                }
                            }
                        }
                        Section("Artifacts") {
                            if (op.artifacts ?? []).isEmpty { Text("None. A source packet is a local bundle, never an RLM result.").font(.caption2) }
                            ForEach(op.artifacts ?? []) { art in
                                Button {
                                    Task { artifactText = await model.artifactText(op.operation_id, art.artifact_id) }
                                } label: {
                                    HStack { Text(art.kind).font(.caption.bold()); Text(art.available ? "available" : "unavailable (file missing)").font(.caption2).foregroundStyle(art.available ? .green : .orange) }
                                }
                            }
                            if let t = artifactText { Text(t).font(.caption2) }
                        }
                    }
                    .navigationTitle("Operation \(op.operation_id.prefix(8))")
                }
            }
        }
    }

    private func allocatorLine(_ a: BackendStatus.Allocator) -> String {
        guard a.available else { return "Allocator: status unavailable" }
        let runs = a.service_runs?.count ?? 0
        return "Allocator: \(a.active ?? "?"), pid \(a.main_pid ?? 0), launch \(a.launch_instance ?? "?"), live runs \(runs)"
    }

    private func color(_ status: String) -> Color {
        switch status {
        case "directive_posted", "artifact_available": return .green
        case "delivery_unknown", "backend_unavailable": return .orange
        case "failed": return .red
        default: return .primary
        }
    }
}
