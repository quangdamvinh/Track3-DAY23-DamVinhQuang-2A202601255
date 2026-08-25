# Day 08 Lab Report

## 1. Team / student

- Name: Đàm Vinh Quang

- Repo/commit: [Track 3 Day 23](https://github.com/quangdamvinh/Track3-DAY23-DamVinhQuang-2A202601255)

- Date: 25/8/2026

## 2. Architecture

Workflow được xây dựng bằng LangGraph StateGraph, kết hợp quản lý state, conditional routing, retry loop, human-in-the-loop approval, persistence và audit logging.

Luồng chính của graph:

START

↓

intake

↓

classify

├── simple ─────────────→ answer

├── tool ───────────────→ tool → evaluate

├── missing_info ───────→ clarify

├── risky ──────────────→ risky_action → approval

└── error ──────────────→ retry

　　　　　　　　　　　　　　↓

　　　　　　　　　　　bounded retry

　　　　　　　　　　　　　　↓

　　　　　　　　　　　tool / dead_letter

Các route tạo ra câu trả lời sẽ đi qua answer → finalize → END.

Các route clarification và dead-letter đi trực tiếp tới finalize → END.

Do đó, tất cả các đường đi trong graph đều kết thúc tại finalize trước khi tới END.

Node classify sử dụng LLM với structured output để phân loại support-ticket thành một trong năm route: simple, tool, missing_info, risky hoặc error. Việc sử dụng structured output giúp kết quả classification có format ổn định hơn so với việc tự parse raw text.

Node evaluate đóng vai trò là retry-loop gate. Khi tool gặp lỗi tạm thời, workflow chuyển sang retry. Node retry kiểm tra giới hạn max_attempts trước khi quyết định thử lại tool hoặc chuyển sang dead_letter.

Các thao tác có side effect như refund hoặc delete được đưa qua risky_action → approval trước khi tiếp tục. Approval mặc định sử dụng mock approval để có thể chạy trong môi trường test.

## 3. State schema

| Field | Reducer | Why |
|---|---|---|
| thread_id | overwrite | Xác định execution thread của mỗi lần chạy |
| scenario_id | overwrite | Xác định scenario hiện tại |
| query | overwrite | Lưu user query sau khi được normalize |
| route | overwrite | Lưu route hiện tại được LLM classification |
| risk_level | overwrite | Lưu mức độ rủi ro của request |
| attempt | overwrite | Theo dõi số lần retry hiện tại |
| max_attempts | overwrite | Giới hạn số lần retry |
| final_answer | overwrite | Lưu câu trả lời cuối cùng |
| evaluation_result | overwrite | Quyết định tiếp tục retry hay chuyển sang answer |
| pending_question | overwrite | Lưu câu hỏi clarification |
| proposed_action | overwrite | Lưu action đang chờ approval |
| approval | overwrite | Lưu quyết định approval mới nhất |
| messages | append | Lưu các workflow messages |
| tool_results | append | Lưu kết quả của các lần tool execution |
| errors | append | Lưu các lỗi trong quá trình thực thi |
| events | append | Duy trì audit trail của quá trình chạy graph |

State được thiết kế theo hướng lean và serializable. Các trường biểu diễn trạng thái hiện tại sử dụng overwrite reducer, trong khi các trường cần duy trì lịch sử như messages, tool_results, errors và events sử dụng append reducer.

## 4. Scenario results

Các metrics chính từ outputs/metrics.json:

- Total scenarios: 7
- Success rate: 100.00%
- Average nodes visited: 19.29
- Total retries: 0
- Total interrupts: 6
- Resume success: No

| Scenario | Expected route | Actual route | Success | Retries | Interrupts |
|---|---|---|---:|---:|---:|
| S01_simple | simple | simple | Yes | 0 | 0 |
| S02_tool | tool | tool | Yes | 0 | 0 |
| S03_missing | missing_info | missing_info | Yes | 0 | 0 |
| S04_risky | risky | risky | Yes | 0 | 3 |
| S05_error | error | error | Yes | 0 | 0 |
| S06_delete | risky | risky | Yes | 0 | 3 |
| S07_dead_letter | error | error | Yes | 0 | 0 |

Tất cả 7 scenario đều được route tới đúng expected route và được đánh dấu success. Hai scenario risky là S04_risky và S06_delete đều đi qua approval path.

## 5. Failure analysis

Hai failure mode chính được xem xét:

1. Retry or tool failure:

Tool có thể gặp transient failure trong quá trình thực thi. Workflow ghi nhận lỗi, tăng attempt counter và chỉ retry khi attempt vẫn nhỏ hơn max_attempts. Khi đạt giới hạn, workflow chuyển sang dead_letter thay vì tiếp tục retry vô hạn.

Scenario S05_error được sử dụng để kiểm tra retry loop, trong khi S07_dead_letter kiểm tra trường hợp giới hạn retry được đặt ở mức thấp và workflow phải kết thúc bằng dead_letter.

2. Risky action without approval:

Các thao tác có side effect như refund, delete hoặc cancellation không được phép thực hiện ngay sau khi classification. Workflow trước tiên tạo proposed_action và chuyển request tới approval node.

Nếu action được approve, workflow tiếp tục thực hiện tool path. Nếu bị reject, request được chuyển sang clarification thay vì thực hiện action.

Ngoài ra, workflow còn cần xem xét các failure mode như user query quá mơ hồ, LLM classification không chính xác và tool result không chứa đủ thông tin để tạo câu trả lời.

## 6. Persistence / recovery evidence

Graph hỗ trợ truyền checkpointer thông qua build_graph() và mỗi scenario được gán một thread_id riêng. thread_id được truyền vào execution config của LangGraph để xác định execution thread tương ứng.

Persistence adapter hỗ trợ MemorySaver cho quá trình development và SQLite checkpointing cho persistence.

SQLite implementation sử dụng SqliteSaver với SQLite connection và WAL mode, cho phép checkpoint được lưu persistent thay vì chỉ tồn tại trong memory.

Trong lần chạy hiện tại, metrics ghi nhận resume_success là false. Vì vậy, kết quả hiện tại chứng minh graph đã được chạy với cơ chế checkpointer/thread_id, nhưng chưa có bằng chứng về một lần crash-resume thành công.

## 7. Extension work

Không thực hiện bonus extension riêng ngoài các yêu cầu chính của lab.

Các extension có thể triển khai thêm trong tương lai gồm:

- Real HITL interrupts với interrupt()
- State history và time-travel replay
- Parallel fan-out/fan-in với Send()
- Graph visualization sử dụng Mermaid
- Crash-recovery testing

## 8. Improvement plan

Nếu có thêm một ngày, ưu tiên đầu tiên sẽ là productionize LLM và tool execution layer.

Cụ thể, hệ thống nên bổ sung observability cho LLM latency, token usage, classification confidence, tool failures, retry causes và approval decisions.

Retry strategy cũng có thể được cải thiện bằng exponential backoff và phân biệt rõ giữa retryable và non-retryable failures.

Cuối cùng, nên bổ sung nhiều hidden-style scenarios hơn để kiểm tra khả năng generalization của LLM-based routing thay vì chỉ đánh giá trên các scenario được cung cấp trong lab.