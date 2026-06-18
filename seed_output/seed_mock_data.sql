-- Seed mock data for KMS demo reports
-- Generated 2026-06-16 10:00:00

PRAGMA foreign_keys = ON;

-- Clean existing demo data
DELETE FROM entry_comments;
DELETE FROM fact_corrections;
DELETE FROM reports;
DELETE FROM knowledge_entries;
DELETE FROM embeddings_vec;
DELETE FROM notes_fts;
DELETE FROM documents WHERE vault_path LIKE 'demo/%';

-- Documents (13 rows)
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-09-weekly-ops-sync.md', 'Weekly Ops Sync - Tuần 9/6', '## Tổng quan
Cuộc họp weekly sync của team Operations ngày 9/6.

## Điểm chính
* Kho Tân Bình đang chậm tiến độ nhập hàng 3 ngày — nguyên nhân: thiếu nhân sự ca tối.
* Đơn hàng Q2 tăng 18% so với cùng kỳ, nhưng logistics chưa scale kịp.
* Chị Minh đề xuất thuê thêm 2 nhân viên kho từ tuần sau.
* Hệ thống ERP bị lỗi đồng bộ tồn kho 2 lần trong tuần, anh Tùng đang làm việc với IT.

## Quyết định
* Duyệt chi ngân sách tuyển thêm 2 nhân viên kho (chị Minh phụ trách).
* Lịch họp với vendor ERP vào thứ 5 tuần này (anh Tùng).

## Action items
* Chị Minh: đăng tin tuyển dụng, phỏng vấn trong tuần.
* Anh Tùng: báo cáo lỗi ERP cho vendor, fix xong trước thứ 6.
* Cả team: đề xuất KPI logistics Q3 trước 20/6.

## Người tham gia
* Chị Minh (Ops Manager), Anh Tùng (IT Lead), Anh Hoàng (Logistics), Chị Hương (Warehouse)', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-09-weekly-ops-sync.md', 'Weekly Ops Sync - Tuần 9/6', '## Tổng quan
Cuộc họp weekly sync của team Operations ngày 9/6.

## Điểm chính
* Kho Tân Bình đang chậm tiến độ nhập hàng 3 ngày — nguyên nhân: thiếu nhân sự ca tối.
* Đơn hàng Q2 tăng 18% so với cùng kỳ, nhưng logistics chưa scale kịp.
* Chị Minh đề xuất thuê thêm 2 nhân viên kho từ tuần sau.
* Hệ thống ERP bị lỗi đồng bộ tồn kho 2 lần trong tuần, anh Tùng đang làm việc với IT.

## Quyết định
* Duyệt chi ngân sách tuyển thêm 2 nhân viên kho (chị Minh phụ trách).
* Lịch họp với vendor ERP vào thứ 5 tuần này (anh Tùng).

## Action items
* Chị Minh: đăng tin tuyển dụng, phỏng vấn trong tuần.
* Anh Tùng: báo cáo lỗi ERP cho vendor, fix xong trước thứ 6.
* Cả team: đề xuất KPI logistics Q3 trước 20/6.

## Người tham gia
* Chị Minh (Ops Manager), Anh Tùng (IT Lead), Anh Hoàng (Logistics), Chị Hương (Warehouse)', '## Tổng quan
Cuộc họp weekly sync của team Operations ngày 9/6.

## Điểm chính
* Kho Tân Bình đang chậm tiến độ nhập hàng 3 ngày — nguyên nhân: thiếu nhân sự ca tối.
* Đơn hàng Q2 tăng 18% so với cùng kỳ, nhưng logistics chưa scale kịp.
* Chị Minh đề xuất thuê thêm 2 nhân viên kho từ tuần sau.
* Hệ thống ERP bị lỗi đồng bộ tồn kho 2 lần trong tuần, anh Tùng đang làm việc với IT.

## Quyết định
* Duyệt chi ngân sách tuyển thêm 2 nhân viên kho (chị Minh phụ trách).
* Lịch họp với vendor ERP vào thứ 5 tuần này (anh Tùng).

## Action items
* Chị Minh: đăng tin tuyển dụng, phỏng vấn trong tuần.
* Anh Tùng: báo cáo lỗi ERP cho vendor, fix xong trước thứ 6.
* Cả team: đề xuất KPI logistics Q3 trước 20/6.

## Người tham gia
* Chị Minh (Ops Manager), Anh Tùng (IT Lead), Anh Hoàng (Logistics), Chị Hương (Warehouse)');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-10-sales-pipeline-review.md', 'Sales Pipeline Review - 10/6', '## Tổng quan
Meeting định kỳ review pipeline Sales tháng 6.

## Điểm chính
* Pipeline hiện có 45 deal đang active, tổng giá trị 28.5 tỷ — cao nhất từ đầu năm.
* Deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý, chị Lan cần hỗ trợ từ legal.
* Khách hàng XYZ đã chốt hợp đồng 3 năm — đóng góp 2.4 tỷ/năm.
* Team Sales đang thiếu 1 Account Executive khu vực miền Trung.
* Tỉ lệ chuyển đổi từ lead → qualified lead giảm nhẹ (22% → 19%), cần phân tích nguyên nhân.

## Quyết định
* Mở tuyển dụng AE miền Trung (chị Lan phụ trách).
* Chị Lan làm việc trực tiếp với legal team về deal ABC Corp.

## Action items
* Chị Lan: gửi email cho legal team, CC anh Hải.
* Anh Hải: gửi báo cáo phân tích tỉ lệ chuyển đổi trước 15/6.
* Chị Mai: chuẩn bị proposal upsell cho khách hàng XYZ.

## Người tham gia
* Chị Lan (Sales Director), Anh Hải (Sales Ops), Chị Mai (AE Senior), Anh Đức (SDR Lead)', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-10-sales-pipeline-review.md', 'Sales Pipeline Review - 10/6', '## Tổng quan
Meeting định kỳ review pipeline Sales tháng 6.

## Điểm chính
* Pipeline hiện có 45 deal đang active, tổng giá trị 28.5 tỷ — cao nhất từ đầu năm.
* Deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý, chị Lan cần hỗ trợ từ legal.
* Khách hàng XYZ đã chốt hợp đồng 3 năm — đóng góp 2.4 tỷ/năm.
* Team Sales đang thiếu 1 Account Executive khu vực miền Trung.
* Tỉ lệ chuyển đổi từ lead → qualified lead giảm nhẹ (22% → 19%), cần phân tích nguyên nhân.

## Quyết định
* Mở tuyển dụng AE miền Trung (chị Lan phụ trách).
* Chị Lan làm việc trực tiếp với legal team về deal ABC Corp.

## Action items
* Chị Lan: gửi email cho legal team, CC anh Hải.
* Anh Hải: gửi báo cáo phân tích tỉ lệ chuyển đổi trước 15/6.
* Chị Mai: chuẩn bị proposal upsell cho khách hàng XYZ.

## Người tham gia
* Chị Lan (Sales Director), Anh Hải (Sales Ops), Chị Mai (AE Senior), Anh Đức (SDR Lead)', '## Tổng quan
Meeting định kỳ review pipeline Sales tháng 6.

## Điểm chính
* Pipeline hiện có 45 deal đang active, tổng giá trị 28.5 tỷ — cao nhất từ đầu năm.
* Deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý, chị Lan cần hỗ trợ từ legal.
* Khách hàng XYZ đã chốt hợp đồng 3 năm — đóng góp 2.4 tỷ/năm.
* Team Sales đang thiếu 1 Account Executive khu vực miền Trung.
* Tỉ lệ chuyển đổi từ lead → qualified lead giảm nhẹ (22% → 19%), cần phân tích nguyên nhân.

## Quyết định
* Mở tuyển dụng AE miền Trung (chị Lan phụ trách).
* Chị Lan làm việc trực tiếp với legal team về deal ABC Corp.

## Action items
* Chị Lan: gửi email cho legal team, CC anh Hải.
* Anh Hải: gửi báo cáo phân tích tỉ lệ chuyển đổi trước 15/6.
* Chị Mai: chuẩn bị proposal upsell cho khách hàng XYZ.

## Người tham gia
* Chị Lan (Sales Director), Anh Hải (Sales Ops), Chị Mai (AE Senior), Anh Đức (SDR Lead)');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-11-executive-briefing.md', 'Executive Briefing - 11/6', '## Tổng quan
Báo cáo nhanh cho CEO về tình hình kinh doanh và vận hành.

## Điểm chính
* Doanh thu tháng 5 đạt 8.2 tỷ, tăng 12% MoM, đạt 95% target.
* Chi phí logistics tăng 8% do giá xăng và phí kho bãi — cần cân nhắc tối ưu.
* Dự án ERP upgrade đang đi đúng tiến độ, dự kiến go-live 1/7/2026.
* Phản hồi từ khách hàng về chất lượng giao hàng: 4.2/5 — giảm nhẹ so với quý trước (4.4).
* Cần quyết định chiến lược mở rộng miền Trung: tự mở kho hay hợp tác đối tác?

## Quyết định
* CEO duyệt mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026.
* Giữ nguyên ngân sách logistics Q2, review lại Q3.

## Action items
* Chị Lan: lên kế hoạch chi tiết mở rộng miền Trung, deadline 25/6.
* Chị Minh: đề xuất phương án tối ưu logistics, deadline 20/6.

## Người tham gia
* Anh Nam (CEO), Chị Lan (Sales), Chị Minh (Ops), Chị Hà (Finance)', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-11-executive-briefing.md', 'Executive Briefing - 11/6', '## Tổng quan
Báo cáo nhanh cho CEO về tình hình kinh doanh và vận hành.

## Điểm chính
* Doanh thu tháng 5 đạt 8.2 tỷ, tăng 12% MoM, đạt 95% target.
* Chi phí logistics tăng 8% do giá xăng và phí kho bãi — cần cân nhắc tối ưu.
* Dự án ERP upgrade đang đi đúng tiến độ, dự kiến go-live 1/7/2026.
* Phản hồi từ khách hàng về chất lượng giao hàng: 4.2/5 — giảm nhẹ so với quý trước (4.4).
* Cần quyết định chiến lược mở rộng miền Trung: tự mở kho hay hợp tác đối tác?

## Quyết định
* CEO duyệt mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026.
* Giữ nguyên ngân sách logistics Q2, review lại Q3.

## Action items
* Chị Lan: lên kế hoạch chi tiết mở rộng miền Trung, deadline 25/6.
* Chị Minh: đề xuất phương án tối ưu logistics, deadline 20/6.

## Người tham gia
* Anh Nam (CEO), Chị Lan (Sales), Chị Minh (Ops), Chị Hà (Finance)', '## Tổng quan
Báo cáo nhanh cho CEO về tình hình kinh doanh và vận hành.

## Điểm chính
* Doanh thu tháng 5 đạt 8.2 tỷ, tăng 12% MoM, đạt 95% target.
* Chi phí logistics tăng 8% do giá xăng và phí kho bãi — cần cân nhắc tối ưu.
* Dự án ERP upgrade đang đi đúng tiến độ, dự kiến go-live 1/7/2026.
* Phản hồi từ khách hàng về chất lượng giao hàng: 4.2/5 — giảm nhẹ so với quý trước (4.4).
* Cần quyết định chiến lược mở rộng miền Trung: tự mở kho hay hợp tác đối tác?

## Quyết định
* CEO duyệt mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026.
* Giữ nguyên ngân sách logistics Q2, review lại Q3.

## Action items
* Chị Lan: lên kế hoạch chi tiết mở rộng miền Trung, deadline 25/6.
* Chị Minh: đề xuất phương án tối ưu logistics, deadline 20/6.

## Người tham gia
* Anh Nam (CEO), Chị Lan (Sales), Chị Minh (Ops), Chị Hà (Finance)');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-12-customer-feedback-review.md', 'Customer Feedback Review - 12/6', '## Tổng quan
Tổng hợp và phân tích phản hồi khách hàng tháng 5-6.

## Điểm chính
* 127 phản hồi trong tháng: 78% tích cực, 15% trung tính, 7% tiêu cực.
* Phàn nàn nhiều nhất: thời gian giao hàng (12 cases), đóng gói sản phẩm (5 cases).
* Khách hàng lớn nhất (XYZ) đánh giá 5/5 sau khi triển khai dedicated support.
* Đề xuất triển khai chương trình khách hàng VIP cho top 10 accounts.

## Quyết định
* Triển khai chương trình VIP cho top 10 khách hàng từ tháng 7.
* Cải tiến quy trình đóng gói trong tháng 6.

## Action items
* Chị Mai: thiết kế chương trình VIP, trình duyệt trước 20/6.
* Anh Hoàng: audit quy trình đóng gói, đề xuất cải tiến trước 18/6.

## Người tham gia
* Chị Mai (CS Manager), Anh Hoàng (Logistics), Chị Hương (Warehouse)', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-12-customer-feedback-review.md', 'Customer Feedback Review - 12/6', '## Tổng quan
Tổng hợp và phân tích phản hồi khách hàng tháng 5-6.

## Điểm chính
* 127 phản hồi trong tháng: 78% tích cực, 15% trung tính, 7% tiêu cực.
* Phàn nàn nhiều nhất: thời gian giao hàng (12 cases), đóng gói sản phẩm (5 cases).
* Khách hàng lớn nhất (XYZ) đánh giá 5/5 sau khi triển khai dedicated support.
* Đề xuất triển khai chương trình khách hàng VIP cho top 10 accounts.

## Quyết định
* Triển khai chương trình VIP cho top 10 khách hàng từ tháng 7.
* Cải tiến quy trình đóng gói trong tháng 6.

## Action items
* Chị Mai: thiết kế chương trình VIP, trình duyệt trước 20/6.
* Anh Hoàng: audit quy trình đóng gói, đề xuất cải tiến trước 18/6.

## Người tham gia
* Chị Mai (CS Manager), Anh Hoàng (Logistics), Chị Hương (Warehouse)', '## Tổng quan
Tổng hợp và phân tích phản hồi khách hàng tháng 5-6.

## Điểm chính
* 127 phản hồi trong tháng: 78% tích cực, 15% trung tính, 7% tiêu cực.
* Phàn nàn nhiều nhất: thời gian giao hàng (12 cases), đóng gói sản phẩm (5 cases).
* Khách hàng lớn nhất (XYZ) đánh giá 5/5 sau khi triển khai dedicated support.
* Đề xuất triển khai chương trình khách hàng VIP cho top 10 accounts.

## Quyết định
* Triển khai chương trình VIP cho top 10 khách hàng từ tháng 7.
* Cải tiến quy trình đóng gói trong tháng 6.

## Action items
* Chị Mai: thiết kế chương trình VIP, trình duyệt trước 20/6.
* Anh Hoàng: audit quy trình đóng gói, đề xuất cải tiến trước 18/6.

## Người tham gia
* Chị Mai (CS Manager), Anh Hoàng (Logistics), Chị Hương (Warehouse)');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-13-q2-review-retro.md', 'Q2 Review & Retrospective - 13/6', '## Tổng quan
Retrospective Q2/2026 toàn công ty.

## Điểm chính
* Q2 đạt 103% target doanh thu, vượt kế hoạch nhờ 3 deal lớn cuối quý.
* Team Sales vượt KPI 110%, team Ops đạt 95% (thiếu hụt do vấn đề kho Tân Bình).
* 3 bài học chính: (1) Cần dự báo nhân sự sớm hơn cho mùa cao điểm, (2) Quy trình legal cần rút ngắn, (3) Đối tác logistics cần SLA chặt hơn.
* Văn hóa công ty cải thiện rõ: khảo sát nội bộ đạt 4.5/5 (tăng từ 4.1 quý trước).

## Quyết định
* Q3 target: 9.5 tỷ/tháng (tăng 15% so với Q2).
* Tuyển thêm 1 legal specialist để rút ngắn thời gian review hợp đồng.

## Action items
* Chị Hà: lập budget Q3 chi tiết, trình duyệt 25/6.
* Chị Lan: onboard legal specialist mới trong tháng 7.
* Chị Minh: lên kế hoạch nhân sự Q3 cho team Ops, deadline 22/6.

## Người tham gia
* Toàn bộ management team', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-13-q2-review-retro.md', 'Q2 Review & Retrospective - 13/6', '## Tổng quan
Retrospective Q2/2026 toàn công ty.

## Điểm chính
* Q2 đạt 103% target doanh thu, vượt kế hoạch nhờ 3 deal lớn cuối quý.
* Team Sales vượt KPI 110%, team Ops đạt 95% (thiếu hụt do vấn đề kho Tân Bình).
* 3 bài học chính: (1) Cần dự báo nhân sự sớm hơn cho mùa cao điểm, (2) Quy trình legal cần rút ngắn, (3) Đối tác logistics cần SLA chặt hơn.
* Văn hóa công ty cải thiện rõ: khảo sát nội bộ đạt 4.5/5 (tăng từ 4.1 quý trước).

## Quyết định
* Q3 target: 9.5 tỷ/tháng (tăng 15% so với Q2).
* Tuyển thêm 1 legal specialist để rút ngắn thời gian review hợp đồng.

## Action items
* Chị Hà: lập budget Q3 chi tiết, trình duyệt 25/6.
* Chị Lan: onboard legal specialist mới trong tháng 7.
* Chị Minh: lên kế hoạch nhân sự Q3 cho team Ops, deadline 22/6.

## Người tham gia
* Toàn bộ management team', '## Tổng quan
Retrospective Q2/2026 toàn công ty.

## Điểm chính
* Q2 đạt 103% target doanh thu, vượt kế hoạch nhờ 3 deal lớn cuối quý.
* Team Sales vượt KPI 110%, team Ops đạt 95% (thiếu hụt do vấn đề kho Tân Bình).
* 3 bài học chính: (1) Cần dự báo nhân sự sớm hơn cho mùa cao điểm, (2) Quy trình legal cần rút ngắn, (3) Đối tác logistics cần SLA chặt hơn.
* Văn hóa công ty cải thiện rõ: khảo sát nội bộ đạt 4.5/5 (tăng từ 4.1 quý trước).

## Quyết định
* Q3 target: 9.5 tỷ/tháng (tăng 15% so với Q2).
* Tuyển thêm 1 legal specialist để rút ngắn thời gian review hợp đồng.

## Action items
* Chị Hà: lập budget Q3 chi tiết, trình duyệt 25/6.
* Chị Lan: onboard legal specialist mới trong tháng 7.
* Chị Minh: lên kế hoạch nhân sự Q3 cho team Ops, deadline 22/6.

## Người tham gia
* Toàn bộ management team');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/2026-06-15-monday-standup.md', 'Monday Standup - 15/6', '## Tổng quan
Standup đầu tuần, cập nhật tiến độ các task đang chạy.

## Điểm chính
* Deal ABC Corp: legal team đã phản hồi, chị Lan sẽ review trong hôm nay.
* Kho Tân Bình: đã tuyển được 1 nhân viên, đang training, dự kiến vào ca từ 18/6.
* Lỗi ERP: vendor đã xác nhận bug, hotfix dự kiến 17/6.
* Chương trình VIP: chị Mai đã có draft, cần input từ Sales team trước khi finalize.
* Pipeline tuần này: 5 deal mới, 3 deal đang chốt.

## Action items
* Chị Lan: review legal response cho ABC Corp, phản hồi trước 16/6.
* Chị Mai: gửi draft VIP cho Sales team review.
* Chị Minh: follow-up với vendor ERP về timeline hotfix.

## Người tham gia
* Chị Lan, Chị Minh, Chị Mai, Anh Hải, Anh Hoàng, Anh Tùng', 'meeting-notes', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/2026-06-15-monday-standup.md', 'Monday Standup - 15/6', '## Tổng quan
Standup đầu tuần, cập nhật tiến độ các task đang chạy.

## Điểm chính
* Deal ABC Corp: legal team đã phản hồi, chị Lan sẽ review trong hôm nay.
* Kho Tân Bình: đã tuyển được 1 nhân viên, đang training, dự kiến vào ca từ 18/6.
* Lỗi ERP: vendor đã xác nhận bug, hotfix dự kiến 17/6.
* Chương trình VIP: chị Mai đã có draft, cần input từ Sales team trước khi finalize.
* Pipeline tuần này: 5 deal mới, 3 deal đang chốt.

## Action items
* Chị Lan: review legal response cho ABC Corp, phản hồi trước 16/6.
* Chị Mai: gửi draft VIP cho Sales team review.
* Chị Minh: follow-up với vendor ERP về timeline hotfix.

## Người tham gia
* Chị Lan, Chị Minh, Chị Mai, Anh Hải, Anh Hoàng, Anh Tùng', '## Tổng quan
Standup đầu tuần, cập nhật tiến độ các task đang chạy.

## Điểm chính
* Deal ABC Corp: legal team đã phản hồi, chị Lan sẽ review trong hôm nay.
* Kho Tân Bình: đã tuyển được 1 nhân viên, đang training, dự kiến vào ca từ 18/6.
* Lỗi ERP: vendor đã xác nhận bug, hotfix dự kiến 17/6.
* Chương trình VIP: chị Mai đã có draft, cần input từ Sales team trước khi finalize.
* Pipeline tuần này: 5 deal mới, 3 deal đang chốt.

## Action items
* Chị Lan: review legal response cho ABC Corp, phản hồi trước 16/6.
* Chị Mai: gửi draft VIP cho Sales team review.
* Chị Minh: follow-up với vendor ERP về timeline hotfix.

## Người tham gia
* Chị Lan, Chị Minh, Chị Mai, Anh Hải, Anh Hoàng, Anh Tùng');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Báo cáo doanh thu tháng 5-2026.pdf', 'Báo Cáo Doanh Thu Tháng 5/2026', '## Tổng quan
Báo cáo doanh thu chi tiết tháng 5/2026 do Finance team tổng hợp.

## Điểm chính
* Tổng doanh thu: 8.2 tỷ (tăng 12% MoM, đạt 95% target).
* Top 3 khách hàng: XYZ (2.4 tỷ), ABC Corp (1.8 tỷ), DEF Ltd (1.2 tỷ).
* Doanh thu từ khách hàng mới: 920 triệu (11.2% tổng doanh thu).
* Chi phí: 5.8 tỷ (COGS 4.2 tỷ, vận hành 1.6 tỷ).
* Biên lợi nhuận gộp: 29.3% (giảm nhẹ từ 30.1% tháng trước do chi phí logistics tăng).

## Phân tích theo khu vực
* Miền Bắc: 3.4 tỷ (41.5%) — tăng 15% MoM.
* Miền Trung: 1.8 tỷ (22%) — tăng 8% MoM.
* Miền Nam: 3.0 tỷ (36.5%) — tăng 10% MoM.

## Dự báo tháng 6
* Target: 8.8 tỷ, dựa trên pipeline hiện tại và xu hướng tăng trưởng.', 'report', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Báo cáo doanh thu tháng 5-2026.pdf', 'Báo Cáo Doanh Thu Tháng 5/2026', '## Tổng quan
Báo cáo doanh thu chi tiết tháng 5/2026 do Finance team tổng hợp.

## Điểm chính
* Tổng doanh thu: 8.2 tỷ (tăng 12% MoM, đạt 95% target).
* Top 3 khách hàng: XYZ (2.4 tỷ), ABC Corp (1.8 tỷ), DEF Ltd (1.2 tỷ).
* Doanh thu từ khách hàng mới: 920 triệu (11.2% tổng doanh thu).
* Chi phí: 5.8 tỷ (COGS 4.2 tỷ, vận hành 1.6 tỷ).
* Biên lợi nhuận gộp: 29.3% (giảm nhẹ từ 30.1% tháng trước do chi phí logistics tăng).

## Phân tích theo khu vực
* Miền Bắc: 3.4 tỷ (41.5%) — tăng 15% MoM.
* Miền Trung: 1.8 tỷ (22%) — tăng 8% MoM.
* Miền Nam: 3.0 tỷ (36.5%) — tăng 10% MoM.

## Dự báo tháng 6
* Target: 8.8 tỷ, dựa trên pipeline hiện tại và xu hướng tăng trưởng.', '## Tổng quan
Báo cáo doanh thu chi tiết tháng 5/2026 do Finance team tổng hợp.

## Điểm chính
* Tổng doanh thu: 8.2 tỷ (tăng 12% MoM, đạt 95% target).
* Top 3 khách hàng: XYZ (2.4 tỷ), ABC Corp (1.8 tỷ), DEF Ltd (1.2 tỷ).
* Doanh thu từ khách hàng mới: 920 triệu (11.2% tổng doanh thu).
* Chi phí: 5.8 tỷ (COGS 4.2 tỷ, vận hành 1.6 tỷ).
* Biên lợi nhuận gộp: 29.3% (giảm nhẹ từ 30.1% tháng trước do chi phí logistics tăng).

## Phân tích theo khu vực
* Miền Bắc: 3.4 tỷ (41.5%) — tăng 15% MoM.
* Miền Trung: 1.8 tỷ (22%) — tăng 8% MoM.
* Miền Nam: 3.0 tỷ (36.5%) — tăng 10% MoM.

## Dự báo tháng 6
* Target: 8.8 tỷ, dựa trên pipeline hiện tại và xu hướng tăng trưởng.');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Kế hoạch mở rộng miền Trung - proposal.pdf', 'Kế Hoạch Mở Rộng Miền Trung — Proposal', '## Tổng quan
Đề xuất chiến lược mở rộng thị trường miền Trung theo mô hình tự mở kho, target Q4/2026.

## Phân tích thị trường
* Dân số: 12 triệu, GDP đầu người đang tăng trưởng 7%/năm.
* Đối thủ chính: 3 công ty đang hoạt động, chưa có đơn vị nào chiếm >30% thị phần.
* Nhu cầu: ước tính 1,200 doanh nghiệp vừa và nhỏ đang tìm giải pháp logistics.

## Kế hoạch triển khai
* Giai đoạn 1 (Q4/2026): Mở kho Đà Nẵng, tuyển 5 nhân viên.
* Giai đoạn 2 (Q1/2027): Mở rộng ra Huế và Quảng Nam.
* Giai đoạn 3 (Q2/2027): Đạt điểm hòa vốn, target 15 khách hàng.

## Ngân sách dự kiến
* Đầu tư ban đầu: 3.2 tỷ (kho bãi, thiết bị, nhân sự).
* Chi phí vận hành/tháng: 380 triệu.
* Doanh thu dự kiến: 600 triệu/tháng từ tháng 6.

## Rủi ro
* Thị trường chưa quen thương hiệu — cần chiến dịch marketing trước mở kho 2 tháng.
* Thiếu nhân sự quản lý có kinh nghiệm tại địa phương.', 'proposal', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Kế hoạch mở rộng miền Trung - proposal.pdf', 'Kế Hoạch Mở Rộng Miền Trung — Proposal', '## Tổng quan
Đề xuất chiến lược mở rộng thị trường miền Trung theo mô hình tự mở kho, target Q4/2026.

## Phân tích thị trường
* Dân số: 12 triệu, GDP đầu người đang tăng trưởng 7%/năm.
* Đối thủ chính: 3 công ty đang hoạt động, chưa có đơn vị nào chiếm >30% thị phần.
* Nhu cầu: ước tính 1,200 doanh nghiệp vừa và nhỏ đang tìm giải pháp logistics.

## Kế hoạch triển khai
* Giai đoạn 1 (Q4/2026): Mở kho Đà Nẵng, tuyển 5 nhân viên.
* Giai đoạn 2 (Q1/2027): Mở rộng ra Huế và Quảng Nam.
* Giai đoạn 3 (Q2/2027): Đạt điểm hòa vốn, target 15 khách hàng.

## Ngân sách dự kiến
* Đầu tư ban đầu: 3.2 tỷ (kho bãi, thiết bị, nhân sự).
* Chi phí vận hành/tháng: 380 triệu.
* Doanh thu dự kiến: 600 triệu/tháng từ tháng 6.

## Rủi ro
* Thị trường chưa quen thương hiệu — cần chiến dịch marketing trước mở kho 2 tháng.
* Thiếu nhân sự quản lý có kinh nghiệm tại địa phương.', '## Tổng quan
Đề xuất chiến lược mở rộng thị trường miền Trung theo mô hình tự mở kho, target Q4/2026.

## Phân tích thị trường
* Dân số: 12 triệu, GDP đầu người đang tăng trưởng 7%/năm.
* Đối thủ chính: 3 công ty đang hoạt động, chưa có đơn vị nào chiếm >30% thị phần.
* Nhu cầu: ước tính 1,200 doanh nghiệp vừa và nhỏ đang tìm giải pháp logistics.

## Kế hoạch triển khai
* Giai đoạn 1 (Q4/2026): Mở kho Đà Nẵng, tuyển 5 nhân viên.
* Giai đoạn 2 (Q1/2027): Mở rộng ra Huế và Quảng Nam.
* Giai đoạn 3 (Q2/2027): Đạt điểm hòa vốn, target 15 khách hàng.

## Ngân sách dự kiến
* Đầu tư ban đầu: 3.2 tỷ (kho bãi, thiết bị, nhân sự).
* Chi phí vận hành/tháng: 380 triệu.
* Doanh thu dự kiến: 600 triệu/tháng từ tháng 6.

## Rủi ro
* Thị trường chưa quen thương hiệu — cần chiến dịch marketing trước mở kho 2 tháng.
* Thiếu nhân sự quản lý có kinh nghiệm tại địa phương.');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Đề xuất chương trình khách hàng VIP.docx', 'Đề Xuất Chương Trình Khách Hàng VIP', '## Tổng quan
Đề xuất triển khai chương trình VIP cho top 10 khách hàng, bắt đầu từ tháng 7/2026.

## Tiêu chí chọn khách hàng VIP
* Doanh thu năm: >1 tỷ.
* Thời gian hợp tác: >2 năm.
* Tiềm năng tăng trưởng: có pipeline mở rộng.

## Quyền lợi VIP
* Dedicated account manager 24/7.
* Ưu tiên xử lý đơn hàng trong 2 giờ.
* Chiết khấu 5% cho đơn hàng >500 triệu.
* Báo cáo định kỳ riêng hàng tháng.
* Quà tặng sinh nhật và sự kiện đặc biệt.

## Chi phí dự kiến
* Nhân sự: 2 account manager dedicated (120 triệu/tháng).
* Chiết khấu: ước tính 200 triệu/năm.
* Quà tặng & sự kiện: 80 triệu/năm.

## Kỳ vọng
* Tăng retention rate từ 85% lên 95%.
* Tăng upsell 20% từ nhóm VIP.
* Củng cố vị thế với khách hàng chiến lược.', 'proposal', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Đề xuất chương trình khách hàng VIP.docx', 'Đề Xuất Chương Trình Khách Hàng VIP', '## Tổng quan
Đề xuất triển khai chương trình VIP cho top 10 khách hàng, bắt đầu từ tháng 7/2026.

## Tiêu chí chọn khách hàng VIP
* Doanh thu năm: >1 tỷ.
* Thời gian hợp tác: >2 năm.
* Tiềm năng tăng trưởng: có pipeline mở rộng.

## Quyền lợi VIP
* Dedicated account manager 24/7.
* Ưu tiên xử lý đơn hàng trong 2 giờ.
* Chiết khấu 5% cho đơn hàng >500 triệu.
* Báo cáo định kỳ riêng hàng tháng.
* Quà tặng sinh nhật và sự kiện đặc biệt.

## Chi phí dự kiến
* Nhân sự: 2 account manager dedicated (120 triệu/tháng).
* Chiết khấu: ước tính 200 triệu/năm.
* Quà tặng & sự kiện: 80 triệu/năm.

## Kỳ vọng
* Tăng retention rate từ 85% lên 95%.
* Tăng upsell 20% từ nhóm VIP.
* Củng cố vị thế với khách hàng chiến lược.', '## Tổng quan
Đề xuất triển khai chương trình VIP cho top 10 khách hàng, bắt đầu từ tháng 7/2026.

## Tiêu chí chọn khách hàng VIP
* Doanh thu năm: >1 tỷ.
* Thời gian hợp tác: >2 năm.
* Tiềm năng tăng trưởng: có pipeline mở rộng.

## Quyền lợi VIP
* Dedicated account manager 24/7.
* Ưu tiên xử lý đơn hàng trong 2 giờ.
* Chiết khấu 5% cho đơn hàng >500 triệu.
* Báo cáo định kỳ riêng hàng tháng.
* Quà tặng sinh nhật và sự kiện đặc biệt.

## Chi phí dự kiến
* Nhân sự: 2 account manager dedicated (120 triệu/tháng).
* Chiết khấu: ước tính 200 triệu/năm.
* Quà tặng & sự kiện: 80 triệu/năm.

## Kỳ vọng
* Tăng retention rate từ 85% lên 95%.
* Tăng upsell 20% từ nhóm VIP.
* Củng cố vị thế với khách hàng chiến lược.');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Phương án tối ưu logistics Q3.docx', 'Phương Án Tối Ưu Logistics Q3/2026', '## Tổng quan
Đề xuất các phương án tối ưu chi phí logistics trước tình hình chi phí tăng 8% trong Q2.

## Hiện trạng
* Chi phí logistics tháng 5: 1.6 tỷ (tăng 8% so với tháng 4).
* Nguyên nhân chính: giá xăng tăng 5%, phí kho bãi tăng 12%, phụ phí mùa cao điểm.
* Số đơn giao trễ: 7.3% (tăng từ 5.1% Q1).

## Phương án đề xuất
* PA1: Đàm phán lại hợp đồng với 3 hãng vận chuyển — dự kiến giảm 5-7%.
* PA2: Tối ưu tuyến đường giao hàng bằng phần mềm routing — giảm 10% km vận chuyển.
* PA3: Gộp đơn hàng nhỏ thành lô lớn — giảm 15% chi phí giao lẻ.

## Timeline
* Tuần 3/6: Đàm phán với hãng vận chuyển.
* Tuần 4/6: Pilot phần mềm routing tại khu vực miền Bắc.
* Tháng 7: Triển khai toàn quốc.

## Dự kiến tiết kiệm
* 120-180 triệu/tháng từ tháng 8 trở đi.', 'proposal', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Phương án tối ưu logistics Q3.docx', 'Phương Án Tối Ưu Logistics Q3/2026', '## Tổng quan
Đề xuất các phương án tối ưu chi phí logistics trước tình hình chi phí tăng 8% trong Q2.

## Hiện trạng
* Chi phí logistics tháng 5: 1.6 tỷ (tăng 8% so với tháng 4).
* Nguyên nhân chính: giá xăng tăng 5%, phí kho bãi tăng 12%, phụ phí mùa cao điểm.
* Số đơn giao trễ: 7.3% (tăng từ 5.1% Q1).

## Phương án đề xuất
* PA1: Đàm phán lại hợp đồng với 3 hãng vận chuyển — dự kiến giảm 5-7%.
* PA2: Tối ưu tuyến đường giao hàng bằng phần mềm routing — giảm 10% km vận chuyển.
* PA3: Gộp đơn hàng nhỏ thành lô lớn — giảm 15% chi phí giao lẻ.

## Timeline
* Tuần 3/6: Đàm phán với hãng vận chuyển.
* Tuần 4/6: Pilot phần mềm routing tại khu vực miền Bắc.
* Tháng 7: Triển khai toàn quốc.

## Dự kiến tiết kiệm
* 120-180 triệu/tháng từ tháng 8 trở đi.', '## Tổng quan
Đề xuất các phương án tối ưu chi phí logistics trước tình hình chi phí tăng 8% trong Q2.

## Hiện trạng
* Chi phí logistics tháng 5: 1.6 tỷ (tăng 8% so với tháng 4).
* Nguyên nhân chính: giá xăng tăng 5%, phí kho bãi tăng 12%, phụ phí mùa cao điểm.
* Số đơn giao trễ: 7.3% (tăng từ 5.1% Q1).

## Phương án đề xuất
* PA1: Đàm phán lại hợp đồng với 3 hãng vận chuyển — dự kiến giảm 5-7%.
* PA2: Tối ưu tuyến đường giao hàng bằng phần mềm routing — giảm 10% km vận chuyển.
* PA3: Gộp đơn hàng nhỏ thành lô lớn — giảm 15% chi phí giao lẻ.

## Timeline
* Tuần 3/6: Đàm phán với hãng vận chuyển.
* Tuần 4/6: Pilot phần mềm routing tại khu vực miền Bắc.
* Tháng 7: Triển khai toàn quốc.

## Dự kiến tiết kiệm
* 120-180 triệu/tháng từ tháng 8 trở đi.');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Số liệu pipeline tháng 6-2026.xlsx', 'Số Liệu Pipeline Tháng 6/2026', '## Tổng quan
Bảng tổng hợp số liệu pipeline Sales tháng 6/2026.

## Tổng hợp
* Tổng số deal: 45 deal active.
* Tổng giá trị: 28.5 tỷ.
* Deal trung bình: 633 triệu.
* Win rate dự kiến: 35% (tương đương 10 tỷ doanh thu dự kiến).

## Phân bố theo giai đoạn
* Prospecting: 12 deal (6.8 tỷ).
* Qualification: 10 deal (5.2 tỷ).
* Proposal: 8 deal (7.1 tỷ).
* Negotiation: 10 deal (6.4 tỷ).
* Closed Won (tháng này): 5 deal (3.0 tỷ).

## Top 5 deal lớn nhất
* ABC Corp — 6.2 tỷ (Negotiation) — Chị Lan.
* XYZ Upsell — 3.6 tỷ (Proposal) — Chị Mai.
* DEF Logistics — 2.8 tỷ (Negotiation) — Anh Đức.
* GHI Manufacturing — 2.1 tỷ (Proposal) — Chị Mai.
* JKL Retail — 1.9 tỷ (Qualification) — Anh Đức.

## Xu hướng
* Số deal mới/tuần: trung bình 5.2 — tăng 15% so với tháng trước.
* Thời gian trung bình từ lead → close: 47 ngày (giảm từ 52 ngày Q1).', 'spreadsheet', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Số liệu pipeline tháng 6-2026.xlsx', 'Số Liệu Pipeline Tháng 6/2026', '## Tổng quan
Bảng tổng hợp số liệu pipeline Sales tháng 6/2026.

## Tổng hợp
* Tổng số deal: 45 deal active.
* Tổng giá trị: 28.5 tỷ.
* Deal trung bình: 633 triệu.
* Win rate dự kiến: 35% (tương đương 10 tỷ doanh thu dự kiến).

## Phân bố theo giai đoạn
* Prospecting: 12 deal (6.8 tỷ).
* Qualification: 10 deal (5.2 tỷ).
* Proposal: 8 deal (7.1 tỷ).
* Negotiation: 10 deal (6.4 tỷ).
* Closed Won (tháng này): 5 deal (3.0 tỷ).

## Top 5 deal lớn nhất
* ABC Corp — 6.2 tỷ (Negotiation) — Chị Lan.
* XYZ Upsell — 3.6 tỷ (Proposal) — Chị Mai.
* DEF Logistics — 2.8 tỷ (Negotiation) — Anh Đức.
* GHI Manufacturing — 2.1 tỷ (Proposal) — Chị Mai.
* JKL Retail — 1.9 tỷ (Qualification) — Anh Đức.

## Xu hướng
* Số deal mới/tuần: trung bình 5.2 — tăng 15% so với tháng trước.
* Thời gian trung bình từ lead → close: 47 ngày (giảm từ 52 ngày Q1).', '## Tổng quan
Bảng tổng hợp số liệu pipeline Sales tháng 6/2026.

## Tổng hợp
* Tổng số deal: 45 deal active.
* Tổng giá trị: 28.5 tỷ.
* Deal trung bình: 633 triệu.
* Win rate dự kiến: 35% (tương đương 10 tỷ doanh thu dự kiến).

## Phân bố theo giai đoạn
* Prospecting: 12 deal (6.8 tỷ).
* Qualification: 10 deal (5.2 tỷ).
* Proposal: 8 deal (7.1 tỷ).
* Negotiation: 10 deal (6.4 tỷ).
* Closed Won (tháng này): 5 deal (3.0 tỷ).

## Top 5 deal lớn nhất
* ABC Corp — 6.2 tỷ (Negotiation) — Chị Lan.
* XYZ Upsell — 3.6 tỷ (Proposal) — Chị Mai.
* DEF Logistics — 2.8 tỷ (Negotiation) — Anh Đức.
* GHI Manufacturing — 2.1 tỷ (Proposal) — Chị Mai.
* JKL Retail — 1.9 tỷ (Qualification) — Anh Đức.

## Xu hướng
* Số deal mới/tuần: trung bình 5.2 — tăng 15% so với tháng trước.
* Thời gian trung bình từ lead → close: 47 ngày (giảm từ 52 ngày Q1).');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Chi phí vận hành Q2-2026.xlsx', 'Chi Phí Vận Hành Q2/2026', '## Tổng quan
Bảng tổng hợp chi phí vận hành Q2/2026, cập nhật đến 15/6.

## Tổng hợp chi phí
* Tổng chi phí Q2 đến hiện tại: 11.8 tỷ.
* Chi phí cố định: 4.2 tỷ (kho bãi, lương, khấu hao).
* Chi phí biến đổi: 7.6 tỷ (vận chuyển, đóng gói, hoa hồng).

## Chi tiết theo hạng mục
* Vận chuyển: 3.8 tỷ (32.2%) — tăng 8% so với Q1.
* Nhân sự vận hành: 2.4 tỷ (20.3%).
* Thuê kho: 1.8 tỷ (15.3%).
* Đóng gói: 920 triệu (7.8%).
* Hoa hồng Sales: 1.6 tỷ (13.6%).
* IT & ERP: 680 triệu (5.8%).
* Khác: 600 triệu (5.1%).

## So sánh Q1 vs Q2
* Q1 tổng: 10.9 tỷ → Q2 dự kiến: 12.4 tỷ (+13.8%).
* Chi phí vận chuyển tăng mạnh nhất: +8% — cần ưu tiên tối ưu.', 'spreadsheet', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Chi phí vận hành Q2-2026.xlsx', 'Chi Phí Vận Hành Q2/2026', '## Tổng quan
Bảng tổng hợp chi phí vận hành Q2/2026, cập nhật đến 15/6.

## Tổng hợp chi phí
* Tổng chi phí Q2 đến hiện tại: 11.8 tỷ.
* Chi phí cố định: 4.2 tỷ (kho bãi, lương, khấu hao).
* Chi phí biến đổi: 7.6 tỷ (vận chuyển, đóng gói, hoa hồng).

## Chi tiết theo hạng mục
* Vận chuyển: 3.8 tỷ (32.2%) — tăng 8% so với Q1.
* Nhân sự vận hành: 2.4 tỷ (20.3%).
* Thuê kho: 1.8 tỷ (15.3%).
* Đóng gói: 920 triệu (7.8%).
* Hoa hồng Sales: 1.6 tỷ (13.6%).
* IT & ERP: 680 triệu (5.8%).
* Khác: 600 triệu (5.1%).

## So sánh Q1 vs Q2
* Q1 tổng: 10.9 tỷ → Q2 dự kiến: 12.4 tỷ (+13.8%).
* Chi phí vận chuyển tăng mạnh nhất: +8% — cần ưu tiên tối ưu.', '## Tổng quan
Bảng tổng hợp chi phí vận hành Q2/2026, cập nhật đến 15/6.

## Tổng hợp chi phí
* Tổng chi phí Q2 đến hiện tại: 11.8 tỷ.
* Chi phí cố định: 4.2 tỷ (kho bãi, lương, khấu hao).
* Chi phí biến đổi: 7.6 tỷ (vận chuyển, đóng gói, hoa hồng).

## Chi tiết theo hạng mục
* Vận chuyển: 3.8 tỷ (32.2%) — tăng 8% so với Q1.
* Nhân sự vận hành: 2.4 tỷ (20.3%).
* Thuê kho: 1.8 tỷ (15.3%).
* Đóng gói: 920 triệu (7.8%).
* Hoa hồng Sales: 1.6 tỷ (13.6%).
* IT & ERP: 680 triệu (5.8%).
* Khác: 600 triệu (5.1%).

## So sánh Q1 vs Q2
* Q1 tổng: 10.9 tỷ → Q2 dự kiến: 12.4 tỷ (+13.8%).
* Chi phí vận chuyển tăng mạnh nhất: +8% — cần ưu tiên tối ưu.');
INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) VALUES ('demo/Email - Chị Lan gửi legal team re ABC Corp.eml', 'Email: Gửi Legal Team về Deal ABC Corp', 'From: Chị Lan (Sales Director)
To: Legal Team
CC: Anh Hải (Sales Ops), Anh Nam (CEO)
Subject: Gấp — Cần review hợp đồng ABC Corp trước 16/6

Chào anh chị,

Như đã trao đổi trong cuộc họp sáng nay, deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý.
Hợp đồng đã được gửi từ tuần trước nhưng chưa có phản hồi.

Đây là deal lớn nhất pipeline hiện tại, và khách hàng đang mong phản hồi trước 16/6.
Nếu chậm trễ, rủi ro mất deal là rất cao — đối thủ đang chào giá cạnh tranh.

Nhờ anh chị ưu tiên xử lý gấp và phản hồi trước cuối ngày mai.

Cảm ơn anh chị,
Chị Lan
Sales Director', 'email', 0.85, '2026-06-16 10:00:00', '2026-06-16 10:00:00');
INSERT INTO notes_fts(vault_path, title, summary, body) VALUES ('demo/Email - Chị Lan gửi legal team re ABC Corp.eml', 'Email: Gửi Legal Team về Deal ABC Corp', 'From: Chị Lan (Sales Director)
To: Legal Team
CC: Anh Hải (Sales Ops), Anh Nam (CEO)
Subject: Gấp — Cần review hợp đồng ABC Corp trước 16/6

Chào anh chị,

Như đã trao đổi trong cuộc họp sáng nay, deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý.
Hợp đồng đã được gửi từ tuần trước nhưng chưa có phản hồi.

Đây là deal lớn nhất pipeline hiện tại, và khách hàng đang mong phản hồi trước 16/6.
Nếu chậm trễ, rủi ro mất deal là rất cao — đối thủ đang chào giá cạnh tranh.

Nhờ anh chị ưu tiên xử lý gấp và phản hồi trước cuối ngày mai.

Cảm ơn anh chị,
Chị Lan
Sales Director', 'From: Chị Lan (Sales Director)
To: Legal Team
CC: Anh Hải (Sales Ops), Anh Nam (CEO)
Subject: Gấp — Cần review hợp đồng ABC Corp trước 16/6

Chào anh chị,

Như đã trao đổi trong cuộc họp sáng nay, deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý.
Hợp đồng đã được gửi từ tuần trước nhưng chưa có phản hồi.

Đây là deal lớn nhất pipeline hiện tại, và khách hàng đang mong phản hồi trước 16/6.
Nếu chậm trễ, rủi ro mất deal là rất cao — đối thủ đang chào giá cạnh tranh.

Nhờ anh chị ưu tiên xử lý gấp và phản hồi trước cuối ngày mai.

Cảm ơn anh chị,
Chị Lan
Sales Director');

-- Knowledge entries (42 rows)
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'ERP Upgrade', 'status', 'Go-live dự kiến 1/7/2026', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'ERP Upgrade', 'blocker', 'Lỗi đồng bộ tồn kho xảy ra 2 lần trong tuần 9/6', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'Kho Tân Bình', 'status', 'Đang chậm tiến độ nhập hàng 3 ngày', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'Kho Tân Bình', 'blocker', 'Thiếu nhân sự ca tối — đã duyệt tuyển 2 người', 'pending', 0.72, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'Mở Rộng Miền Trung', 'status', 'CEO duyệt mở rộng theo mô hình tự mở kho, target Q4/2026', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'Mở Rộng Miền Trung', 'deadline', 'Kế hoạch chi tiết deadline 25/6/2026', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'Chương Trình VIP', 'status', 'Draft đã có, đang chờ input từ Sales team', 'pending', 0.68, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'ABC Corp', 'status', 'Đang kẹt ở vòng pháp lý — legal team đã phản hồi', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('project', 'ABC Corp', 'value', '6.2 tỷ — deal lớn nhất pipeline hiện tại', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Lan', 'role', 'Sales Director', 'confident', 0.95, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Lan', 'responsibility', 'Phụ trách deal ABC Corp và tuyển dụng AE miền Trung', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Minh', 'role', 'Operations Manager', 'confident', 0.95, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Minh', 'responsibility', 'Quản lý kho Tân Bình và tối ưu logistics', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Tùng', 'role', 'IT Lead', 'confident', 0.95, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Tùng', 'responsibility', 'Xử lý lỗi ERP và làm việc với vendor', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Mai', 'role', 'Account Executive Senior', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Mai', 'responsibility', 'Thiết kế chương trình VIP và upsell XYZ', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Hải', 'role', 'Sales Operations', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Hải', 'responsibility', 'Phân tích tỉ lệ chuyển đổi và báo cáo pipeline', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Hoàng', 'role', 'Logistics Lead', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Hoàng', 'responsibility', 'Audit quy trình đóng gói và giao hàng', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Hương', 'role', 'Warehouse Manager', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Chị Hà', 'role', 'Finance Manager', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('person', 'Anh Nam', 'role', 'CEO', 'confident', 0.95, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Doanh Thu Tháng 5', 'value', '8.2 tỷ — tăng 12% MoM', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Doanh Thu Tháng 5', 'target', 'Đạt 95% target tháng', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Pipeline Tháng 6', 'value', '45 deal active, 28.5 tỷ', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Tỉ Lệ Chuyển Đổi', 'value', '19% (giảm từ 22%)', 'pending', 0.65, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'CSAT Giao Hàng', 'value', '4.2/5 (giảm từ 4.4)', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Feedback Khách Hàng', 'value', '78% tích cực, 7% tiêu cực', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Q2 Target', 'value', 'Đạt 103% target doanh thu', 'confident', 0.92, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('metric', 'Q3 Target', 'value', '9.5 tỷ/tháng — tăng 15% so với Q2', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('risk', 'Logistics', 'issue', 'Chi phí logistics tăng 8% do giá xăng và phí kho bãi', 'pending', 0.72, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('risk', 'Logistics', 'issue', 'Đơn hàng tăng 18% nhưng logistics chưa scale kịp', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('risk', 'HR', 'issue', 'Thiếu Account Executive khu vực miền Trung', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('risk', 'HR', 'issue', 'Cần tuyển thêm 1 legal specialist', 'pending', 0.7, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('risk', 'Chất Lượng', 'issue', 'Đóng gói sản phẩm — 5 phàn nàn trong tháng', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('decision', 'Tuyển Dụng', 'action', 'Duyệt tuyển 2 nhân viên kho Tân Bình', 'confident', 0.88, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('decision', 'Tuyển Dụng', 'action', 'Mở tuyển AE miền Trung và legal specialist', 'confident', 0.85, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('decision', 'Chiến Lược', 'action', 'Mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026', 'confident', 0.9, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('decision', 'Khách Hàng', 'action', 'Triển khai chương trình VIP cho top 10 accounts từ tháng 7', 'confident', 0.82, '[1,2]', 'Extracted from meeting notes', 0.60, 0);
INSERT INTO knowledge_entries (dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) VALUES ('decision', 'ERP', 'action', 'Vendor ERP sẽ hotfix lỗi đồng bộ trước 17/6', 'pending', 0.72, '[1,2]', 'Extracted from meeting notes', 0.60, 0);

-- Fact corrections (3 rows)
INSERT INTO fact_corrections (entry_id, operation, reason_category, feedback, old_fact, new_fact, old_trust_score, new_trust_score, created_at) VALUES (10, 'edit_fact', 'wrong_fact', 'Chị Lan là Sales Director không phải PM', 'Chị Lan — Project Manager', 'Chị Lan — Sales Director', 0.5, 0.65, '2026-06-13 00:00:00');
UPDATE knowledge_entries SET trust_score = 0.65 WHERE id = 10;
INSERT INTO fact_corrections (entry_id, operation, reason_category, feedback, old_fact, new_fact, old_trust_score, new_trust_score, created_at) VALUES (32, 'edit_fact', 'wrong_fact', 'CEO đã điều chỉnh target lên 9.5', 'Q3 target: 9 tỷ/tháng', 'Q3 target: 9.5 tỷ/tháng — tăng 15% so với Q2', 0.55, 0.7, '2026-06-15 00:00:00');
UPDATE knowledge_entries SET trust_score = 0.7 WHERE id = 32;
INSERT INTO fact_corrections (entry_id, operation, reason_category, feedback, old_fact, new_fact, old_trust_score, new_trust_score, created_at) VALUES (4, 'edit_fact', 'outdated', 'Đã tuyển được 1 người, không còn thiếu 3 người', 'Thiếu 3 nhân viên ca tối', 'Thiếu nhân sự ca tối — đã duyệt tuyển 2 người', 0.55, 0.65, '2026-06-14 00:00:00');
UPDATE knowledge_entries SET trust_score = 0.65 WHERE id = 4;
