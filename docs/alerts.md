# Alert và runbook CP2

Ba rule trong `config/alert_rules.yaml` là đặc tả, condition là pseudocode.
Repo chưa có alert evaluator hoặc tích hợp gửi Slack. Kênh dự kiến:
`#day13-k4-l3a-monitoring`; cần chọn kênh thực tế khi triển khai thông báo.
Owner cả ba alert: Nguyễn Quang Huy. Đánh giá mỗi phút; duration 5m nghĩa là
điều kiện đúng liên tục 5 phút. Không có traffic không được coi là error rate 0%.

## Alert 1

- Tên: `high_request_latency`; severity: warning; duration: 5m.
- Kênh: Slack `#day13-k4-l3a-monitoring`; owner: Nguyễn Quang Huy.
- SLI/SLO: request thành công trong 3000 ms, mục tiêu 99.5% trong 28 ngày.
- Điều kiện: ít nhất 10 response trong 5 phút và P95 > 3000 ms, duy trì 5 phút.
- Ảnh hưởng: người dùng phải chờ lâu. Alert P95 không tương đương trực tiếp SLO 99.5%.
- Ba bước kiểm tra:
  1. Mở `/dashboard`, xác định thời gian P95/TTFT tăng và traffic cùng lúc.
  2. Lọc `response_sent` chậm, lấy correlation ID.
  3. Mở trace cùng ID, so sánh retrieval/generation và thời gian fetch prompt ngoài child spans.
- Mitigation: nếu practice `rag_slow` gây chậm, chạy `python scripts/inject_incident.py --scenario rag_slow --disable`.
  Nếu thay đổi prompt liên quan, rollback production, restart API và đo lại cùng workload.
- Xác nhận phục hồi: so sánh P95 và waterfall trước/sau; lưu evidence.

## Alert 2

- Tên: `high_request_error_rate`; severity: critical; duration: 5m.
- Kênh: Slack `#day13-k4-l3a-monitoring`; owner: Nguyễn Quang Huy.
- SLI/SLO: request thành công; guardrail error rate <= 2%.
- Điều kiện: ít nhất 10 request trong 5 phút, failed/received * 100 > 2%, duy trì 5 phút.
- Ảnh hưởng: người dùng không nhận được câu trả lời.
- Ba bước kiểm tra:
  1. Xem error rate, breakdown và retrieval success trên dashboard.
  2. Lọc `request_failed`, lấy error_type, thời gian và correlation ID.
  3. Mở trace cùng ID, xác định child observation lỗi và đối chiếu log.
- Mitigation: nếu practice `tool_fail` gây lỗi, chạy `python scripts/inject_incident.py --scenario tool_fail --disable`.
  Trong hệ thống thật, kiểm tra dependency và dùng fallback có kiểm soát; tránh retry vô hạn.
- Xác nhận phục hồi: gửi lại workload; response thành công và retrieval success hồi phục.

## Alert 3

- Tên: `daily_cost_budget_exceeded`; severity: warning; duration: 5m.
- Kênh: Slack `#day13-k4-l3a-monitoring`; owner: Nguyễn Quang Huy.
- SLI: tổng cost; guardrail 2.5 USD/ngày (cost trong lab là mô phỏng).
- Điều kiện: tổng cost từ 00:00 ngày hiện tại theo Asia/Ho_Chi_Minh > 2.5 USD, duy trì 5 phút.
- Ảnh hưởng: vượt ngân sách vận hành, có thể phải hạn chế dịch vụ.
- Ba bước kiểm tra:
  1. So sánh cost với traffic/token trên dashboard; tính riêng tổng ngày từ log cho rule này.
  2. Tìm request cost cao, đối chiếu model, token và correlation ID.
  3. Mở generation tương ứng, kiểm tra usage/cost và prompt version.
- Mitigation: nếu practice `cost_spike` gây tăng token, chạy `python scripts/inject_incident.py --scenario cost_spike --disable`.
  Trong hệ thống thật, giới hạn output token hoặc rollback prompt gây tăng cost sau khi có bằng chứng.
- Xác nhận phục hồi: cost/request và output tokens giảm trên cùng workload.
  Tổng cost ngày không giảm sau mitigation; alert chỉ hết khi sang ngày mới hoặc được xử lý theo quy trình ngân sách.

Dashboard cost dùng tổng 60 phút; rule cost dùng tổng ngày. Không dùng tổng 60 phút
để kết luận vượt ngân sách ngày. Workload ngắn có thể chưa đủ duration để alert fire.
