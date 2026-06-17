"""Seed mock data for demo reports (meeting_insights, action_items, weekly_digest).

Vietnamese business context: a tech company running Sales & Operations.

Usage:
    # Write to local SQLite DB
    python scripts/seed_mock_data.py --db-path data/kb.db

    # Write SQL + JSON files for cloud deployment
    python scripts/seed_mock_data.py --output-dir ./seed_output
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def _weekday(n: int) -> str:
    today = datetime(2026, 6, 16)
    d = today - timedelta(days=n)
    return d.strftime("%Y-%m-%d %H:%M:%S")


NOW = datetime(2026, 6, 16, 10, 0, 0)
NOW_TS = NOW.strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════
#  DATA — pure lists of tuples, no DB calls
# ═══════════════════════════════════════════════════════════════════════════

DOCS = [
    # (vault_path, title, summary, note_type)
    (
        "demo/2026-06-09-weekly-ops-sync.md",
        "Weekly Ops Sync - Tuần 9/6",
        "## Tổng quan\nCuộc họp weekly sync của team Operations ngày 9/6.\n\n"
        "## Điểm chính\n* Kho Tân Bình đang chậm tiến độ nhập hàng 3 ngày — nguyên nhân: thiếu nhân sự ca tối.\n"
        "* Đơn hàng Q2 tăng 18% so với cùng kỳ, nhưng logistics chưa scale kịp.\n"
        "* Chị Minh đề xuất thuê thêm 2 nhân viên kho từ tuần sau.\n"
        "* Hệ thống ERP bị lỗi đồng bộ tồn kho 2 lần trong tuần, anh Tùng đang làm việc với IT.\n\n"
        "## Quyết định\n* Duyệt chi ngân sách tuyển thêm 2 nhân viên kho (chị Minh phụ trách).\n"
        "* Lịch họp với vendor ERP vào thứ 5 tuần này (anh Tùng).\n\n"
        "## Action items\n* Chị Minh: đăng tin tuyển dụng, phỏng vấn trong tuần.\n"
        "* Anh Tùng: báo cáo lỗi ERP cho vendor, fix xong trước thứ 6.\n"
        "* Cả team: đề xuất KPI logistics Q3 trước 20/6.\n\n"
        "## Người tham gia\n* Chị Minh (Ops Manager), Anh Tùng (IT Lead), Anh Hoàng (Logistics), Chị Hương (Warehouse)",
        "meeting-notes",
    ),
    (
        "demo/2026-06-10-sales-pipeline-review.md",
        "Sales Pipeline Review - 10/6",
        "## Tổng quan\nMeeting định kỳ review pipeline Sales tháng 6.\n\n"
        "## Điểm chính\n* Pipeline hiện có 45 deal đang active, tổng giá trị 28.5 tỷ — cao nhất từ đầu năm.\n"
        "* Deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý, chị Lan cần hỗ trợ từ legal.\n"
        "* Khách hàng XYZ đã chốt hợp đồng 3 năm — đóng góp 2.4 tỷ/năm.\n"
        "* Team Sales đang thiếu 1 Account Executive khu vực miền Trung.\n"
        "* Tỉ lệ chuyển đổi từ lead → qualified lead giảm nhẹ (22% → 19%), cần phân tích nguyên nhân.\n\n"
        "## Quyết định\n* Mở tuyển dụng AE miền Trung (chị Lan phụ trách).\n"
        "* Chị Lan làm việc trực tiếp với legal team về deal ABC Corp.\n\n"
        "## Action items\n* Chị Lan: gửi email cho legal team, CC anh Hải.\n"
        "* Anh Hải: gửi báo cáo phân tích tỉ lệ chuyển đổi trước 15/6.\n"
        "* Chị Mai: chuẩn bị proposal upsell cho khách hàng XYZ.\n\n"
        "## Người tham gia\n* Chị Lan (Sales Director), Anh Hải (Sales Ops), Chị Mai (AE Senior), Anh Đức (SDR Lead)",
        "meeting-notes",
    ),
    (
        "demo/2026-06-11-executive-briefing.md",
        "Executive Briefing - 11/6",
        "## Tổng quan\nBáo cáo nhanh cho CEO về tình hình kinh doanh và vận hành.\n\n"
        "## Điểm chính\n* Doanh thu tháng 5 đạt 8.2 tỷ, tăng 12% MoM, đạt 95% target.\n"
        "* Chi phí logistics tăng 8% do giá xăng và phí kho bãi — cần cân nhắc tối ưu.\n"
        "* Dự án ERP upgrade đang đi đúng tiến độ, dự kiến go-live 1/7/2026.\n"
        "* Phản hồi từ khách hàng về chất lượng giao hàng: 4.2/5 — giảm nhẹ so với quý trước (4.4).\n"
        "* Cần quyết định chiến lược mở rộng miền Trung: tự mở kho hay hợp tác đối tác?\n\n"
        "## Quyết định\n* CEO duyệt mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026.\n"
        "* Giữ nguyên ngân sách logistics Q2, review lại Q3.\n\n"
        "## Action items\n* Chị Lan: lên kế hoạch chi tiết mở rộng miền Trung, deadline 25/6.\n"
        "* Chị Minh: đề xuất phương án tối ưu logistics, deadline 20/6.\n\n"
        "## Người tham gia\n* Anh Nam (CEO), Chị Lan (Sales), Chị Minh (Ops), Chị Hà (Finance)",
        "meeting-notes",
    ),
    (
        "demo/2026-06-12-customer-feedback-review.md",
        "Customer Feedback Review - 12/6",
        "## Tổng quan\nTổng hợp và phân tích phản hồi khách hàng tháng 5-6.\n\n"
        "## Điểm chính\n* 127 phản hồi trong tháng: 78% tích cực, 15% trung tính, 7% tiêu cực.\n"
        "* Phàn nàn nhiều nhất: thời gian giao hàng (12 cases), đóng gói sản phẩm (5 cases).\n"
        "* Khách hàng lớn nhất (XYZ) đánh giá 5/5 sau khi triển khai dedicated support.\n"
        "* Đề xuất triển khai chương trình khách hàng VIP cho top 10 accounts.\n\n"
        "## Quyết định\n* Triển khai chương trình VIP cho top 10 khách hàng từ tháng 7.\n"
        "* Cải tiến quy trình đóng gói trong tháng 6.\n\n"
        "## Action items\n* Chị Mai: thiết kế chương trình VIP, trình duyệt trước 20/6.\n"
        "* Anh Hoàng: audit quy trình đóng gói, đề xuất cải tiến trước 18/6.\n\n"
        "## Người tham gia\n* Chị Mai (CS Manager), Anh Hoàng (Logistics), Chị Hương (Warehouse)",
        "meeting-notes",
    ),
    (
        "demo/2026-06-13-q2-review-retro.md",
        "Q2 Review & Retrospective - 13/6",
        "## Tổng quan\nRetrospective Q2/2026 toàn công ty.\n\n"
        "## Điểm chính\n* Q2 đạt 103% target doanh thu, vượt kế hoạch nhờ 3 deal lớn cuối quý.\n"
        "* Team Sales vượt KPI 110%, team Ops đạt 95% (thiếu hụt do vấn đề kho Tân Bình).\n"
        "* 3 bài học chính: (1) Cần dự báo nhân sự sớm hơn cho mùa cao điểm, (2) Quy trình legal cần rút ngắn, (3) Đối tác logistics cần SLA chặt hơn.\n"
        "* Văn hóa công ty cải thiện rõ: khảo sát nội bộ đạt 4.5/5 (tăng từ 4.1 quý trước).\n\n"
        "## Quyết định\n* Q3 target: 9.5 tỷ/tháng (tăng 15% so với Q2).\n"
        "* Tuyển thêm 1 legal specialist để rút ngắn thời gian review hợp đồng.\n\n"
        "## Action items\n* Chị Hà: lập budget Q3 chi tiết, trình duyệt 25/6.\n"
        "* Chị Lan: onboard legal specialist mới trong tháng 7.\n"
        "* Chị Minh: lên kế hoạch nhân sự Q3 cho team Ops, deadline 22/6.\n\n"
        "## Người tham gia\n* Toàn bộ management team",
        "meeting-notes",
    ),
    (
        "demo/2026-06-15-monday-standup.md",
        "Monday Standup - 15/6",
        "## Tổng quan\nStandup đầu tuần, cập nhật tiến độ các task đang chạy.\n\n"
        "## Điểm chính\n* Deal ABC Corp: legal team đã phản hồi, chị Lan sẽ review trong hôm nay.\n"
        "* Kho Tân Bình: đã tuyển được 1 nhân viên, đang training, dự kiến vào ca từ 18/6.\n"
        "* Lỗi ERP: vendor đã xác nhận bug, hotfix dự kiến 17/6.\n"
        "* Chương trình VIP: chị Mai đã có draft, cần input từ Sales team trước khi finalize.\n"
        "* Pipeline tuần này: 5 deal mới, 3 deal đang chốt.\n\n"
        "## Action items\n* Chị Lan: review legal response cho ABC Corp, phản hồi trước 16/6.\n"
        "* Chị Mai: gửi draft VIP cho Sales team review.\n"
        "* Chị Minh: follow-up với vendor ERP về timeline hotfix.\n\n"
        "## Người tham gia\n* Chị Lan, Chị Minh, Chị Mai, Anh Hải, Anh Hoàng, Anh Tùng",
        "meeting-notes",
    ),
    # ── PDF reports ──────────────────────────────────────────────────
    (
        "demo/Báo cáo doanh thu tháng 5-2026.pdf",
        "Báo Cáo Doanh Thu Tháng 5/2026",
        "## Tổng quan\nBáo cáo doanh thu chi tiết tháng 5/2026 do Finance team tổng hợp.\n\n"
        "## Điểm chính\n* Tổng doanh thu: 8.2 tỷ (tăng 12% MoM, đạt 95% target).\n"
        "* Top 3 khách hàng: XYZ (2.4 tỷ), ABC Corp (1.8 tỷ), DEF Ltd (1.2 tỷ).\n"
        "* Doanh thu từ khách hàng mới: 920 triệu (11.2% tổng doanh thu).\n"
        "* Chi phí: 5.8 tỷ (COGS 4.2 tỷ, vận hành 1.6 tỷ).\n"
        "* Biên lợi nhuận gộp: 29.3% (giảm nhẹ từ 30.1% tháng trước do chi phí logistics tăng).\n\n"
        "## Phân tích theo khu vực\n* Miền Bắc: 3.4 tỷ (41.5%) — tăng 15% MoM.\n"
        "* Miền Trung: 1.8 tỷ (22%) — tăng 8% MoM.\n"
        "* Miền Nam: 3.0 tỷ (36.5%) — tăng 10% MoM.\n\n"
        "## Dự báo tháng 6\n* Target: 8.8 tỷ, dựa trên pipeline hiện tại và xu hướng tăng trưởng.",
        "report",
    ),
    (
        "demo/Kế hoạch mở rộng miền Trung - proposal.pdf",
        "Kế Hoạch Mở Rộng Miền Trung — Proposal",
        "## Tổng quan\nĐề xuất chiến lược mở rộng thị trường miền Trung theo mô hình tự mở kho, target Q4/2026.\n\n"
        "## Phân tích thị trường\n* Dân số: 12 triệu, GDP đầu người đang tăng trưởng 7%/năm.\n"
        "* Đối thủ chính: 3 công ty đang hoạt động, chưa có đơn vị nào chiếm >30% thị phần.\n"
        "* Nhu cầu: ước tính 1,200 doanh nghiệp vừa và nhỏ đang tìm giải pháp logistics.\n\n"
        "## Kế hoạch triển khai\n* Giai đoạn 1 (Q4/2026): Mở kho Đà Nẵng, tuyển 5 nhân viên.\n"
        "* Giai đoạn 2 (Q1/2027): Mở rộng ra Huế và Quảng Nam.\n"
        "* Giai đoạn 3 (Q2/2027): Đạt điểm hòa vốn, target 15 khách hàng.\n\n"
        "## Ngân sách dự kiến\n* Đầu tư ban đầu: 3.2 tỷ (kho bãi, thiết bị, nhân sự).\n"
        "* Chi phí vận hành/tháng: 380 triệu.\n"
        "* Doanh thu dự kiến: 600 triệu/tháng từ tháng 6.\n\n"
        "## Rủi ro\n* Thị trường chưa quen thương hiệu — cần chiến dịch marketing trước mở kho 2 tháng.\n"
        "* Thiếu nhân sự quản lý có kinh nghiệm tại địa phương.",
        "proposal",
    ),
    # ── DOCX proposals ────────────────────────────────────────────────
    (
        "demo/Đề xuất chương trình khách hàng VIP.docx",
        "Đề Xuất Chương Trình Khách Hàng VIP",
        "## Tổng quan\nĐề xuất triển khai chương trình VIP cho top 10 khách hàng, bắt đầu từ tháng 7/2026.\n\n"
        "## Tiêu chí chọn khách hàng VIP\n* Doanh thu năm: >1 tỷ.\n"
        "* Thời gian hợp tác: >2 năm.\n"
        "* Tiềm năng tăng trưởng: có pipeline mở rộng.\n\n"
        "## Quyền lợi VIP\n* Dedicated account manager 24/7.\n"
        "* Ưu tiên xử lý đơn hàng trong 2 giờ.\n"
        "* Chiết khấu 5% cho đơn hàng >500 triệu.\n"
        "* Báo cáo định kỳ riêng hàng tháng.\n"
        "* Quà tặng sinh nhật và sự kiện đặc biệt.\n\n"
        "## Chi phí dự kiến\n* Nhân sự: 2 account manager dedicated (120 triệu/tháng).\n"
        "* Chiết khấu: ước tính 200 triệu/năm.\n"
        "* Quà tặng & sự kiện: 80 triệu/năm.\n\n"
        "## Kỳ vọng\n* Tăng retention rate từ 85% lên 95%.\n"
        "* Tăng upsell 20% từ nhóm VIP.\n"
        "* Củng cố vị thế với khách hàng chiến lược.",
        "proposal",
    ),
    (
        "demo/Phương án tối ưu logistics Q3.docx",
        "Phương Án Tối Ưu Logistics Q3/2026",
        "## Tổng quan\nĐề xuất các phương án tối ưu chi phí logistics trước tình hình chi phí tăng 8% trong Q2.\n\n"
        "## Hiện trạng\n* Chi phí logistics tháng 5: 1.6 tỷ (tăng 8% so với tháng 4).\n"
        "* Nguyên nhân chính: giá xăng tăng 5%, phí kho bãi tăng 12%, phụ phí mùa cao điểm.\n"
        "* Số đơn giao trễ: 7.3% (tăng từ 5.1% Q1).\n\n"
        "## Phương án đề xuất\n* PA1: Đàm phán lại hợp đồng với 3 hãng vận chuyển — dự kiến giảm 5-7%.\n"
        "* PA2: Tối ưu tuyến đường giao hàng bằng phần mềm routing — giảm 10% km vận chuyển.\n"
        "* PA3: Gộp đơn hàng nhỏ thành lô lớn — giảm 15% chi phí giao lẻ.\n\n"
        "## Timeline\n* Tuần 3/6: Đàm phán với hãng vận chuyển.\n"
        "* Tuần 4/6: Pilot phần mềm routing tại khu vực miền Bắc.\n"
        "* Tháng 7: Triển khai toàn quốc.\n\n"
        "## Dự kiến tiết kiệm\n* 120-180 triệu/tháng từ tháng 8 trở đi.",
        "proposal",
    ),
    # ── XLSX spreadsheets ────────────────────────────────────────────
    (
        "demo/Số liệu pipeline tháng 6-2026.xlsx",
        "Số Liệu Pipeline Tháng 6/2026",
        "## Tổng quan\nBảng tổng hợp số liệu pipeline Sales tháng 6/2026.\n\n"
        "## Tổng hợp\n* Tổng số deal: 45 deal active.\n"
        "* Tổng giá trị: 28.5 tỷ.\n"
        "* Deal trung bình: 633 triệu.\n"
        "* Win rate dự kiến: 35% (tương đương 10 tỷ doanh thu dự kiến).\n\n"
        "## Phân bố theo giai đoạn\n* Prospecting: 12 deal (6.8 tỷ).\n"
        "* Qualification: 10 deal (5.2 tỷ).\n"
        "* Proposal: 8 deal (7.1 tỷ).\n"
        "* Negotiation: 10 deal (6.4 tỷ).\n"
        "* Closed Won (tháng này): 5 deal (3.0 tỷ).\n\n"
        "## Top 5 deal lớn nhất\n* ABC Corp — 6.2 tỷ (Negotiation) — Chị Lan.\n"
        "* XYZ Upsell — 3.6 tỷ (Proposal) — Chị Mai.\n"
        "* DEF Logistics — 2.8 tỷ (Negotiation) — Anh Đức.\n"
        "* GHI Manufacturing — 2.1 tỷ (Proposal) — Chị Mai.\n"
        "* JKL Retail — 1.9 tỷ (Qualification) — Anh Đức.\n\n"
        "## Xu hướng\n* Số deal mới/tuần: trung bình 5.2 — tăng 15% so với tháng trước.\n"
        "* Thời gian trung bình từ lead → close: 47 ngày (giảm từ 52 ngày Q1).",
        "spreadsheet",
    ),
    (
        "demo/Chi phí vận hành Q2-2026.xlsx",
        "Chi Phí Vận Hành Q2/2026",
        "## Tổng quan\nBảng tổng hợp chi phí vận hành Q2/2026, cập nhật đến 15/6.\n\n"
        "## Tổng hợp chi phí\n* Tổng chi phí Q2 đến hiện tại: 11.8 tỷ.\n"
        "* Chi phí cố định: 4.2 tỷ (kho bãi, lương, khấu hao).\n"
        "* Chi phí biến đổi: 7.6 tỷ (vận chuyển, đóng gói, hoa hồng).\n\n"
        "## Chi tiết theo hạng mục\n* Vận chuyển: 3.8 tỷ (32.2%) — tăng 8% so với Q1.\n"
        "* Nhân sự vận hành: 2.4 tỷ (20.3%).\n"
        "* Thuê kho: 1.8 tỷ (15.3%).\n"
        "* Đóng gói: 920 triệu (7.8%).\n"
        "* Hoa hồng Sales: 1.6 tỷ (13.6%).\n"
        "* IT & ERP: 680 triệu (5.8%).\n"
        "* Khác: 600 triệu (5.1%).\n\n"
        "## So sánh Q1 vs Q2\n* Q1 tổng: 10.9 tỷ → Q2 dự kiến: 12.4 tỷ (+13.8%).\n"
        "* Chi phí vận chuyển tăng mạnh nhất: +8% — cần ưu tiên tối ưu.",
        "spreadsheet",
    ),
    # ── Email (.eml) ──────────────────────────────────────────────────
    (
        "demo/Email - Chị Lan gửi legal team re ABC Corp.eml",
        "Email: Gửi Legal Team về Deal ABC Corp",
        "From: Chị Lan (Sales Director)\n"
        "To: Legal Team\n"
        "CC: Anh Hải (Sales Ops), Anh Nam (CEO)\n"
        "Subject: Gấp — Cần review hợp đồng ABC Corp trước 16/6\n\n"
        "Chào anh chị,\n\n"
        "Như đã trao đổi trong cuộc họp sáng nay, deal ABC Corp (6.2 tỷ) đang bị kẹt ở vòng pháp lý.\n"
        "Hợp đồng đã được gửi từ tuần trước nhưng chưa có phản hồi.\n\n"
        "Đây là deal lớn nhất pipeline hiện tại, và khách hàng đang mong phản hồi trước 16/6.\n"
        "Nếu chậm trễ, rủi ro mất deal là rất cao — đối thủ đang chào giá cạnh tranh.\n\n"
        "Nhờ anh chị ưu tiên xử lý gấp và phản hồi trước cuối ngày mai.\n\n"
        "Cảm ơn anh chị,\n"
        "Chị Lan\n"
        "Sales Director",
        "email",
    ),
]

FACTS = [
    # (dimension, entity, tag, fact, status, confidence)
    ("project", "ERP Upgrade", "status", "Go-live dự kiến 1/7/2026", "confident", 0.92),
    ("project", "ERP Upgrade", "blocker", "Lỗi đồng bộ tồn kho xảy ra 2 lần trong tuần 9/6", "confident", 0.88),
    ("project", "Kho Tân Bình", "status", "Đang chậm tiến độ nhập hàng 3 ngày", "confident", 0.85),
    ("project", "Kho Tân Bình", "blocker", "Thiếu nhân sự ca tối — đã duyệt tuyển 2 người", "pending", 0.72),
    ("project", "Mở Rộng Miền Trung", "status", "CEO duyệt mở rộng theo mô hình tự mở kho, target Q4/2026", "confident", 0.90),
    ("project", "Mở Rộng Miền Trung", "deadline", "Kế hoạch chi tiết deadline 25/6/2026", "confident", 0.88),
    ("project", "Chương Trình VIP", "status", "Draft đã có, đang chờ input từ Sales team", "pending", 0.68),
    ("project", "ABC Corp", "status", "Đang kẹt ở vòng pháp lý — legal team đã phản hồi", "confident", 0.82),
    ("project", "ABC Corp", "value", "6.2 tỷ — deal lớn nhất pipeline hiện tại", "confident", 0.90),
    ("person", "Chị Lan", "role", "Sales Director", "confident", 0.95),
    ("person", "Chị Lan", "responsibility", "Phụ trách deal ABC Corp và tuyển dụng AE miền Trung", "confident", 0.88),
    ("person", "Chị Minh", "role", "Operations Manager", "confident", 0.95),
    ("person", "Chị Minh", "responsibility", "Quản lý kho Tân Bình và tối ưu logistics", "confident", 0.85),
    ("person", "Anh Tùng", "role", "IT Lead", "confident", 0.95),
    ("person", "Anh Tùng", "responsibility", "Xử lý lỗi ERP và làm việc với vendor", "confident", 0.85),
    ("person", "Chị Mai", "role", "Account Executive Senior", "confident", 0.92),
    ("person", "Chị Mai", "responsibility", "Thiết kế chương trình VIP và upsell XYZ", "confident", 0.82),
    ("person", "Anh Hải", "role", "Sales Operations", "confident", 0.92),
    ("person", "Anh Hải", "responsibility", "Phân tích tỉ lệ chuyển đổi và báo cáo pipeline", "confident", 0.82),
    ("person", "Anh Hoàng", "role", "Logistics Lead", "confident", 0.92),
    ("person", "Anh Hoàng", "responsibility", "Audit quy trình đóng gói và giao hàng", "confident", 0.82),
    ("person", "Chị Hương", "role", "Warehouse Manager", "confident", 0.90),
    ("person", "Chị Hà", "role", "Finance Manager", "confident", 0.92),
    ("person", "Anh Nam", "role", "CEO", "confident", 0.95),
    ("metric", "Doanh Thu Tháng 5", "value", "8.2 tỷ — tăng 12% MoM", "confident", 0.90),
    ("metric", "Doanh Thu Tháng 5", "target", "Đạt 95% target tháng", "confident", 0.88),
    ("metric", "Pipeline Tháng 6", "value", "45 deal active, 28.5 tỷ", "confident", 0.90),
    ("metric", "Tỉ Lệ Chuyển Đổi", "value", "19% (giảm từ 22%)", "pending", 0.65),
    ("metric", "CSAT Giao Hàng", "value", "4.2/5 (giảm từ 4.4)", "confident", 0.85),
    ("metric", "Feedback Khách Hàng", "value", "78% tích cực, 7% tiêu cực", "confident", 0.88),
    ("metric", "Q2 Target", "value", "Đạt 103% target doanh thu", "confident", 0.92),
    ("metric", "Q3 Target", "value", "9.5 tỷ/tháng — tăng 15% so với Q2", "confident", 0.85),
    ("risk", "Logistics", "issue", "Chi phí logistics tăng 8% do giá xăng và phí kho bãi", "pending", 0.72),
    ("risk", "Logistics", "issue", "Đơn hàng tăng 18% nhưng logistics chưa scale kịp", "confident", 0.85),
    ("risk", "HR", "issue", "Thiếu Account Executive khu vực miền Trung", "confident", 0.88),
    ("risk", "HR", "issue", "Cần tuyển thêm 1 legal specialist", "pending", 0.70),
    ("risk", "Chất Lượng", "issue", "Đóng gói sản phẩm — 5 phàn nàn trong tháng", "confident", 0.82),
    ("decision", "Tuyển Dụng", "action", "Duyệt tuyển 2 nhân viên kho Tân Bình", "confident", 0.88),
    ("decision", "Tuyển Dụng", "action", "Mở tuyển AE miền Trung và legal specialist", "confident", 0.85),
    ("decision", "Chiến Lược", "action", "Mở rộng miền Trung theo mô hình tự mở kho, target Q4/2026", "confident", 0.90),
    ("decision", "Khách Hàng", "action", "Triển khai chương trình VIP cho top 10 accounts từ tháng 7", "confident", 0.82),
    ("decision", "ERP", "action", "Vendor ERP sẽ hotfix lỗi đồng bộ trước 17/6", "pending", 0.72),
]

CORRECTIONS = [
    # (entry_idx, operation, reason, feedback, old_fact, new_fact, old_trust, new_trust, days_ago)
    (9, "edit_fact", "wrong_fact", "Chị Lan là Sales Director không phải PM",
     "Chị Lan — Project Manager", "Chị Lan — Sales Director", 0.50, 0.65, 3),
    (31, "edit_fact", "wrong_fact", "CEO đã điều chỉnh target lên 9.5",
     "Q3 target: 9 tỷ/tháng", "Q3 target: 9.5 tỷ/tháng — tăng 15% so với Q2", 0.55, 0.70, 1),
    (3, "edit_fact", "outdated", "Đã tuyển được 1 người, không còn thiếu 3 người",
     "Thiếu 3 nhân viên ca tối", "Thiếu nhân sự ca tối — đã duyệt tuyển 2 người", 0.55, 0.65, 2),
]


# ═══════════════════════════════════════════════════════════════════════════
#  SQL GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def _escape_sql(s: str) -> str:
    return s.replace("'", "''")


def _sql_quote(s: str) -> str:
    return f"'{_escape_sql(s)}'"


def generate_sql() -> str:
    lines = [
        "-- Seed mock data for KMS demo reports",
        f"-- Generated {NOW_TS}",
        "",
        "PRAGMA foreign_keys = ON;",
        "",
        "-- Clean existing demo data",
        "DELETE FROM entry_comments;",
        "DELETE FROM fact_corrections;",
        "DELETE FROM reports;",
        "DELETE FROM knowledge_entries;",
        "DELETE FROM embeddings_vec;",
        "DELETE FROM notes_fts;",
        "DELETE FROM documents WHERE vault_path LIKE 'demo/%';",
        "",
        f"-- Documents ({len(DOCS)} rows)",
    ]

    for vp, title, summary, note_type in DOCS:
        lines.append(
            "INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) "
            f"VALUES ({_sql_quote(vp)}, {_sql_quote(title)}, {_sql_quote(summary)}, "
            f"{_sql_quote(note_type)}, 0.85, {_sql_quote(NOW_TS)}, {_sql_quote(NOW_TS)});"
        )
        lines.append(
            "INSERT INTO notes_fts(vault_path, title, summary, body) "
            f"VALUES ({_sql_quote(vp)}, {_sql_quote(title)}, {_sql_quote(summary)}, {_sql_quote(summary)});"
        )

    lines.append("")
    lines.append(f"-- Knowledge entries ({len(FACTS)} rows)")

    for i, (dim, entity, tag, fact, status, conf) in enumerate(FACTS):
        lines.append(
            "INSERT INTO knowledge_entries "
            "(dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) "
            f"VALUES ({_sql_quote(dim)}, {_sql_quote(entity)}, {_sql_quote(tag)}, "
            f"{_sql_quote(fact)}, {_sql_quote(status)}, {conf}, '[1,2]', "
            f"'Extracted from meeting notes', 0.60, 0);"
        )

    lines.append("")
    lines.append(f"-- Fact corrections ({len(CORRECTIONS)} rows)")

    for entry_idx, op, reason, feedback, old_f, new_f, old_t, new_t, days_ago in CORRECTIONS:
        entry_id = entry_idx + 1  # 1-based SQL IDs
        ts = _weekday(days_ago)
        lines.append(
            "INSERT INTO fact_corrections "
            "(entry_id, operation, reason_category, feedback, old_fact, new_fact, old_trust_score, new_trust_score, created_at) "
            f"VALUES ({entry_id}, {_sql_quote(op)}, {_sql_quote(reason)}, {_sql_quote(feedback)}, "
            f"{_sql_quote(old_f)}, {_sql_quote(new_f)}, {old_t}, {new_t}, {_sql_quote(ts)});"
        )
        lines.append(
            f"UPDATE knowledge_entries SET trust_score = {new_t} WHERE id = {entry_id};"
        )

    lines.append("")
    return "\n".join(lines)


def generate_json() -> dict:
    return {
        "generated": NOW_TS,
        "documents": [{"vault_path": vp, "title": t, "summary": s, "note_type": nt} for vp, t, s, nt in DOCS],
        "knowledge_entries": [
            {"dimension": d, "entity": e, "tag": tg, "fact": f, "status": st, "confidence": c}
            for d, e, tg, f, st, c in FACTS
        ],
        "fact_corrections": [
            {"entry_index": ei, "operation": op, "reason_category": rc, "feedback": fb,
             "old_fact": of, "new_fact": nf, "old_trust": ot, "new_trust": nt, "days_ago": da}
            for ei, op, rc, fb, of, nf, ot, nt, da in CORRECTIONS
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  DB WRITER (original behavior)
# ═══════════════════════════════════════════════════════════════════════════

def seed_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("DELETE FROM entry_comments")
    conn.execute("DELETE FROM fact_corrections")
    conn.execute("DELETE FROM reports")
    conn.execute("DELETE FROM knowledge_entries")
    conn.execute("DELETE FROM embeddings_vec")
    conn.execute("DELETE FROM notes_fts")
    conn.execute("DELETE FROM documents WHERE vault_path LIKE 'demo/%'")

    doc_ids: list[int] = []
    for vp, title, summary, note_type in DOCS:
        conn.execute(
            "INSERT INTO documents (vault_path, title, summary, note_type, confidence, updated_at, created_at) "
            "VALUES (?, ?, ?, ?, 0.85, ?, ?)",
            (vp, title, summary, note_type, NOW_TS, NOW_TS),
        )
        doc_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO notes_fts(vault_path, title, summary, body) VALUES (?, ?, ?, ?)",
            (vp, title, summary, summary),
        )

    entry_ids: list[int] = []
    for dim, entity, tag, fact, status, conf in FACTS:
        conn.execute(
            "INSERT INTO knowledge_entries "
            "(dimension, entity, tag, fact, status, confidence, sources, reasoning, trust_score, retrieval_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dim, entity, tag, fact, status, conf, "[1,2]", "Extracted from meeting notes", 0.60, 0),
        )
        entry_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    for entry_idx, op, reason, feedback, old_f, new_f, old_t, new_t, days_ago in CORRECTIONS:
        entry_id = entry_ids[entry_idx]
        ts = _weekday(days_ago)
        conn.execute(
            "INSERT INTO fact_corrections "
            "(entry_id, operation, reason_category, feedback, old_fact, new_fact, old_trust_score, new_trust_score, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry_id, op, reason, feedback, old_f, new_f, old_t, new_t, ts),
        )
        conn.execute("UPDATE knowledge_entries SET trust_score = ? WHERE id = ?", (new_t, entry_id))

    conn.commit()
    conn.close()

    from collections import Counter
    type_counts = Counter(nt for _, _, _, nt in DOCS)
    print(f"✅ Seeded {len(DOCS)} documents ({', '.join(f'{c}x {t}' for t, c in sorted(type_counts.items()))}), "
          f"{len(FACTS)} facts, {len(CORRECTIONS)} corrections.")
    print("   Ready for: meeting_insights, action_items, weekly_digest")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Seed mock data for KMS reports demo")
    p.add_argument("--db-path", default=None, help="Write directly to SQLite database")
    p.add_argument("--output-dir", default=None, help="Write .sql and .json files to this directory")
    args = p.parse_args()

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        sql_path = out / "seed_mock_data.sql"
        sql_path.write_text(generate_sql(), encoding="utf-8")
        print(f"✅ SQL written to {sql_path}")

        json_path = out / "seed_mock_data.json"
        json_path.write_text(json.dumps(generate_json(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ JSON written to {json_path}")
        print(f"   Push these files, then run: sqlite3 /data/kb.db < seed_mock_data.sql")

    elif args.db_path:
        seed_db(args.db_path)
    else:
        p.print_help()
