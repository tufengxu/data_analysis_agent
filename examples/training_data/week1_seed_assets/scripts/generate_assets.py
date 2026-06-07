"""Generate week-1 seed assets for DataAnalysisAgent model training.

Outputs:
- data/*.csv: executable synthetic business datasets
- dataset_manifest.json: dataset schemas and quality notes
- seed_tasks.jsonl: 100 business analysis seed tasks, 5 per dataset
- README.md: asset overview
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
AGENT_RELATIVE_DATA_DIR = "examples/training_data/week1_seed_assets/data"
SEED = 20260604


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    file_name: str
    domain: str
    description: str
    row_count: int
    date_column: str
    dimensions: list[str]
    metrics: list[str]
    quality_issues: list[str]
    business_questions: list[str]


SPECS = [
    DatasetSpec(
        "retail_sales_orders",
        "retail_sales_orders.csv",
        "retail",
        "multi-channel retail order performance",
        720,
        "order_date",
        ["region", "channel", "category", "product"],
        ["revenue", "gross_margin", "units", "discount_pct"],
        ["returns", "discount outliers", "seasonality"],
        [
            "各渠道收入和毛利贡献是否匹配",
            "哪些品类的折扣侵蚀了毛利",
            "退货是否集中在特定区域或产品",
        ],
    ),
    DatasetSpec(
        "ecommerce_marketing_funnel",
        "ecommerce_marketing_funnel.csv",
        "marketing",
        "paid marketing funnel efficiency",
        540,
        "date",
        ["channel", "campaign", "market"],
        ["spend", "clicks", "purchases", "revenue"],
        ["zero conversions", "high CAC outliers", "weekly seasonality"],
        [
            "哪些渠道 ROI 最高",
            "哪些活动花费高但转化低",
            "漏斗转化率是否在近期恶化",
        ],
    ),
    DatasetSpec(
        "subscription_saas_metrics",
        "subscription_saas_metrics.csv",
        "saas",
        "subscription account health and MRR movement",
        640,
        "month",
        ["segment", "plan", "region"],
        ["mrr", "active_users", "expansion_mrr", "support_tickets"],
        ["churn flags", "expansion spikes", "support burden"],
        [
            "哪个客户分层贡献了主要 MRR",
            "活跃用户数和续费风险是否相关",
            "扩容收入是否集中在少数计划",
        ],
    ),
    DatasetSpec(
        "customer_support_tickets",
        "customer_support_tickets.csv",
        "support",
        "support ticket resolution and customer satisfaction",
        680,
        "created_date",
        ["channel", "product_area", "priority"],
        ["first_response_minutes", "resolution_hours", "csat_score", "reopened"],
        ["SLA breach outliers", "missing CSAT", "escalations"],
        [
            "哪些产品模块拖慢了解决时长",
            "首次响应是否影响 CSAT",
            "升级工单是否集中在高优先级问题",
        ],
    ),
    DatasetSpec(
        "manufacturing_quality_inspection",
        "manufacturing_quality_inspection.csv",
        "manufacturing",
        "factory quality inspection and downtime",
        520,
        "inspection_date",
        ["plant", "line", "product_family", "shift"],
        ["units_checked", "defect_count", "downtime_minutes"],
        ["defect clusters", "shift effects", "downtime spikes"],
        [
            "缺陷率最高的产线在哪里",
            "停机时间是否解释缺陷率上升",
            "夜班质量是否明显更差",
        ],
    ),
    DatasetSpec(
        "supply_chain_shipments",
        "supply_chain_shipments.csv",
        "supply_chain",
        "shipment timeliness, cost, and damage claims",
        600,
        "ship_date",
        ["origin", "destination_region", "carrier"],
        ["actual_days", "shipping_cost", "weight_kg", "damage_claim"],
        ["late deliveries", "carrier cost variance", "damage claims"],
        [
            "哪个承运商的延误率最高",
            "运输成本是否被重量或区域驱动",
            "破损索赔是否集中在特定线路",
        ],
    ),
    DatasetSpec(
        "finance_expense_claims",
        "finance_expense_claims.csv",
        "finance",
        "employee expense claims and policy compliance",
        560,
        "submitted_date",
        ["department", "employee_level", "category"],
        ["amount", "reimbursed_days", "policy_violation"],
        ["policy violations", "amount outliers", "approval delays"],
        [
            "哪些费用类别最容易违规",
            "报销周期是否因部门不同而变慢",
            "异常大额费用来自哪些层级",
        ],
    ),
    DatasetSpec(
        "accounts_receivable_aging",
        "accounts_receivable_aging.csv",
        "finance",
        "accounts receivable aging and dispute risk",
        520,
        "invoice_date",
        ["customer_segment", "region", "collector"],
        ["invoice_amount", "days_past_due", "dispute_flag"],
        ["overdue invoices", "disputes", "collector variance"],
        [
            "逾期金额主要集中在哪些客户分层",
            "争议发票是否导致回款周期拉长",
            "哪个催收负责人风险敞口最高",
        ],
    ),
    DatasetSpec(
        "hr_recruiting_pipeline",
        "hr_recruiting_pipeline.csv",
        "hr",
        "recruiting funnel throughput and offer acceptance",
        500,
        "applied_date",
        ["role_family", "source", "stage"],
        ["days_in_stage", "interview_score", "salary_expectation", "offer_accepted"],
        ["stage bottlenecks", "source quality", "offer dropoff"],
        [
            "招聘漏斗瓶颈在哪个阶段",
            "哪些渠道候选人质量最高",
            "薪资期望是否影响 offer 接受率",
        ],
    ),
    DatasetSpec(
        "employee_attrition_pulse",
        "employee_attrition_pulse.csv",
        "hr",
        "employee engagement pulse and attrition risk",
        620,
        "survey_month",
        ["department", "manager_rating", "remote_days"],
        ["engagement_score", "overtime_hours", "attrition_risk", "left_company"],
        ["attrition flags", "engagement missingness", "overtime outliers"],
        [
            "离职风险是否由加班和敬业度解释",
            "哪个部门的敬业度下滑最快",
            "远程天数和留存是否存在关系",
        ],
    ),
    DatasetSpec(
        "product_usage_events",
        "product_usage_events.csv",
        "product",
        "B2B product feature usage and conversion",
        750,
        "event_date",
        ["plan", "feature", "cohort_month"],
        ["events", "active_minutes", "errors", "converted"],
        ["feature adoption skew", "error spikes", "cohort effects"],
        [
            "哪些功能使用后更可能转化",
            "错误数是否压低活跃时长",
            "新老 cohort 的功能采用是否不同",
        ],
    ),
    DatasetSpec(
        "mobile_app_ab_test",
        "mobile_app_ab_test.csv",
        "product",
        "mobile app A/B experiment outcomes",
        700,
        "assign_date",
        ["variant", "country", "device"],
        ["sessions", "purchase_count", "revenue", "retention_d7"],
        ["variant imbalance", "crash outliers", "country mix effects"],
        [
            "实验组是否提高 D7 留存",
            "收入提升是否只来自少数国家",
            "崩溃次数是否影响购买",
        ],
    ),
    DatasetSpec(
        "warehouse_inventory_movements",
        "warehouse_inventory_movements.csv",
        "operations",
        "warehouse inventory movement and stockout risk",
        650,
        "date",
        ["warehouse", "sku_category", "movement_type"],
        ["quantity", "unit_cost", "stockout_flag", "shrinkage_flag"],
        ["stockouts", "shrinkage", "movement imbalance"],
        [
            "哪些仓库缺货风险最高",
            "库存损耗是否集中在某类 SKU",
            "出入库结构是否造成库存压力",
        ],
    ),
    DatasetSpec(
        "energy_consumption_sites",
        "energy_consumption_sites.csv",
        "operations",
        "building energy consumption and peak demand",
        600,
        "reading_date",
        ["region", "building_type", "site_id"],
        ["kwh", "occupancy", "temperature_c", "peak_demand_kw"],
        ["temperature effects", "maintenance days", "peak anomalies"],
        [
            "能耗异常是否由温度或入住率解释",
            "哪些建筑类型单位能耗最高",
            "维护日是否降低峰值负载",
        ],
    ),
    DatasetSpec(
        "banking_transactions_risk",
        "banking_transactions_risk.csv",
        "risk",
        "banking transaction risk and fraud signals",
        760,
        "transaction_date",
        ["customer_segment", "channel", "merchant_category"],
        ["transaction_amount", "risk_score", "fraud_flag", "chargeback_flag"],
        ["fraud labels", "amount outliers", "channel risk"],
        [
            "高风险交易集中在哪些渠道",
            "交易金额和欺诈概率是否相关",
            "哪些商户类别的拒付率最高",
        ],
    ),
    DatasetSpec(
        "insurance_claims",
        "insurance_claims.csv",
        "insurance",
        "insurance claims severity and closure performance",
        560,
        "claim_date",
        ["policy_type", "region", "adjuster"],
        ["claim_amount", "approved_amount", "days_to_close", "suspected_fraud"],
        ["severity outliers", "fraud suspicion", "adjuster variance"],
        [
            "理赔金额异常集中在哪些险种",
            "疑似欺诈是否延长结案时间",
            "不同理赔员的批准金额比例是否异常",
        ],
    ),
    DatasetSpec(
        "healthcare_appointments",
        "healthcare_appointments.csv",
        "healthcare",
        "clinic appointment access and no-show risk",
        620,
        "appointment_date",
        ["clinic", "specialty", "payer_type"],
        ["wait_days", "no_show", "appointment_duration_min", "followup_required"],
        ["no-shows", "wait time skew", "clinic mix"],
        [
            "等待天数是否提高爽约率",
            "哪些专科的随访需求最高",
            "不同诊所的就诊效率是否不同",
        ],
    ),
    DatasetSpec(
        "education_course_engagement",
        "education_course_engagement.csv",
        "education",
        "online course engagement and completion",
        620,
        "week_start",
        ["course", "cohort"],
        ["lessons_completed", "quiz_score", "watch_minutes", "certificate_earned"],
        ["dropoff risk", "quiz missingness", "engagement outliers"],
        [
            "学习投入是否预测证书完成",
            "哪些课程的流失风险最高",
            "测验成绩和观看时长是否一致",
        ],
    ),
    DatasetSpec(
        "real_estate_leads",
        "real_estate_leads.csv",
        "sales",
        "real estate lead conversion and sales cycle",
        540,
        "created_date",
        ["city", "channel", "property_type"],
        ["budget", "visits_scheduled", "offer_made", "closed_won"],
        ["channel quality", "budget outliers", "long sales cycles"],
        [
            "哪些渠道带来的成交率最高",
            "预算是否影响看房和出价",
            "城市间销售周期差异在哪里",
        ],
    ),
    DatasetSpec(
        "restaurant_operations",
        "restaurant_operations.csv",
        "hospitality",
        "restaurant order operations and guest experience",
        700,
        "order_date",
        ["store", "daypart", "channel"],
        ["ticket_size", "prep_minutes", "rating", "labor_hours"],
        ["refunds", "prep time spikes", "rating missingness"],
        [
            "备餐时间是否影响评分",
            "哪些门店的退款率最高",
            "不同时段的客单价和人效差异如何",
        ],
    ),
]


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def maybe_blank(value: Any, probability: float = 0.02) -> Any:
    return "" if random.random() < probability else value


def rand_date(start: date, days: int) -> str:
    return (start + timedelta(days=random.randrange(days))).isoformat()


def rand_month(start: date, months: int) -> str:
    offset = random.randrange(months)
    year = start.year + (start.month - 1 + offset) // 12
    month = (start.month - 1 + offset) % 12 + 1
    return f"{year:04d}-{month:02d}-01"


def seasonal_multiplier(date_text: str) -> float:
    month = int(date_text[5:7])
    return 1.0 + 0.16 * math.sin((month - 1) / 12 * math.tau)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"No rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def generate_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    random.seed(f"{SEED}:{spec.dataset_id}")
    rows: list[dict[str, Any]] = []
    start = date(2025, 1, 1)

    for i in range(spec.row_count):
        if spec.dataset_id == "retail_sales_orders":
            order_date = rand_date(start, 455)
            channel = random.choice(["store", "web", "marketplace", "partner"])
            category = random.choice(["electronics", "home", "beauty", "sports", "grocery"])
            units = max(1, int(random.gauss(3, 1.6)))
            unit_price = round(random.uniform(12, 420) * seasonal_multiplier(order_date), 2)
            discount_pct = round(clamp(random.gauss(0.11, 0.08), 0, 0.55), 2)
            if random.random() < 0.025:
                discount_pct = round(random.uniform(0.45, 0.75), 2)
            revenue = round(units * unit_price * (1 - discount_pct), 2)
            margin_rate = clamp(random.gauss(0.34, 0.09) - discount_pct * 0.32, 0.04, 0.62)
            rows.append({
                "order_id": f"RSO-{i + 1:06d}",
                "order_date": order_date,
                "region": random.choice(["north", "south", "east", "west"]),
                "channel": channel,
                "category": category,
                "product": f"{category[:3].upper()}-{random.randrange(100, 999)}",
                "units": units,
                "unit_price": unit_price,
                "discount_pct": discount_pct,
                "revenue": revenue,
                "gross_margin": round(revenue * margin_rate, 2),
                "returned": int(random.random() < (0.04 + discount_pct * 0.08)),
            })

        elif spec.dataset_id == "ecommerce_marketing_funnel":
            day = rand_date(start, 420)
            channel = random.choice(["search", "social", "affiliate", "email", "display"])
            impressions = random.randrange(4_000, 85_000)
            ctr = {"search": 0.035, "social": 0.022, "affiliate": 0.018, "email": 0.045, "display": 0.01}[channel]
            clicks = max(1, int(impressions * clamp(random.gauss(ctr, ctr * 0.3), 0.002, 0.08)))
            spend = round(clicks * random.uniform(0.35, 2.8), 2)
            purchases = max(0, int(clicks * clamp(random.gauss(0.035, 0.018), 0, 0.12)))
            revenue = round(purchases * random.uniform(38, 180), 2)
            rows.append({
                "date": day,
                "campaign": random.choice(["brand", "launch", "retargeting", "seasonal", "competitor"]),
                "channel": channel,
                "market": random.choice(["US", "CA", "UK", "AU", "SG"]),
                "impressions": impressions,
                "clicks": clicks,
                "spend": spend,
                "signups": max(purchases, int(clicks * random.uniform(0.05, 0.22))),
                "purchases": purchases,
                "revenue": revenue,
            })

        elif spec.dataset_id == "subscription_saas_metrics":
            segment = random.choice(["startup", "mid_market", "enterprise"])
            plan = random.choice(["basic", "pro", "business", "enterprise"])
            seats = random.randrange(5, 320) if segment != "startup" else random.randrange(1, 45)
            mrr = round(seats * random.uniform(18, 95) * (1.4 if plan == "enterprise" else 1), 2)
            active_users = max(1, int(seats * random.uniform(0.35, 0.96)))
            churned = int(random.random() < (0.025 if segment == "enterprise" else 0.065))
            rows.append({
                "month": rand_month(date(2024, 1, 1), 24),
                "account_id": f"ACCT-{random.randrange(10000, 99999)}",
                "segment": segment,
                "plan": plan,
                "region": random.choice(["NA", "EMEA", "APAC", "LATAM"]),
                "seats": seats,
                "mrr": mrr,
                "active_users": active_users,
                "churned": churned,
                "expansion_mrr": 0 if churned else round(max(0, random.gauss(180, 260)), 2),
                "support_tickets": random.randrange(0, 16),
            })

        elif spec.dataset_id == "customer_support_tickets":
            priority = random.choice(["low", "medium", "high", "urgent"])
            response = max(3, random.gauss(80, 45) * (1.8 if priority == "urgent" else 1))
            resolution = max(0.3, random.gauss(20, 14) * (1.7 if priority in ["high", "urgent"] else 1))
            csat = round(clamp(random.gauss(4.2, 0.7) - resolution / 80, 1, 5), 1)
            rows.append({
                "ticket_id": f"TCK-{i + 1:06d}",
                "created_date": rand_date(start, 430),
                "channel": random.choice(["email", "chat", "phone", "portal"]),
                "product_area": random.choice(["billing", "login", "reporting", "integration", "mobile"]),
                "priority": priority,
                "first_response_minutes": round(response, 1),
                "resolution_hours": round(resolution, 1),
                "csat_score": maybe_blank(csat, 0.07),
                "reopened": int(random.random() < (0.08 + resolution / 260)),
                "escalated": int(random.random() < (0.05 if priority == "low" else 0.18)),
            })

        elif spec.dataset_id == "manufacturing_quality_inspection":
            checked = random.randrange(80, 520)
            plant = random.choice(["shenzhen", "suzhou", "chengdu", "hanoi"])
            line = random.choice(["L1", "L2", "L3", "L4"])
            shift = random.choice(["day", "swing", "night"])
            base_defect = 0.018 + (0.012 if shift == "night" else 0) + (0.01 if line == "L3" else 0)
            defects = sum(1 for _ in range(checked) if random.random() < base_defect)
            rows.append({
                "batch_id": f"BATCH-{i + 1:05d}",
                "inspection_date": rand_date(start, 395),
                "plant": plant,
                "line": line,
                "product_family": random.choice(["sensor", "controller", "battery", "display"]),
                "shift": shift,
                "units_checked": checked,
                "defect_count": defects,
                "defect_type": random.choice(["scratch", "alignment", "power", "packaging", "none"]),
                "downtime_minutes": max(0, int(random.gauss(22, 26) + defects * 0.8)),
            })

        elif spec.dataset_id == "supply_chain_shipments":
            carrier = random.choice(["swiftline", "northstar", "oceanic", "airbridge"])
            promised = random.choice([2, 3, 5, 7, 10])
            actual = max(1, int(random.gauss(promised + (1 if carrier == "oceanic" else 0), 1.7)))
            weight = round(random.uniform(2, 880), 1)
            rows.append({
                "shipment_id": f"SHP-{i + 1:06d}",
                "ship_date": rand_date(start, 420),
                "origin": random.choice(["LA", "Dallas", "Chicago", "Shanghai", "Rotterdam"]),
                "destination_region": random.choice(["west", "central", "east", "emea", "apac"]),
                "carrier": carrier,
                "promised_days": promised,
                "actual_days": actual,
                "weight_kg": weight,
                "shipping_cost": round(18 + weight * random.uniform(0.28, 1.65) + actual * 7, 2),
                "late_flag": int(actual > promised),
                "damage_claim": int(random.random() < (0.015 + weight / 50_000 + (0.015 if carrier == "oceanic" else 0))),
            })

        elif spec.dataset_id == "finance_expense_claims":
            category = random.choice(["travel", "meals", "software", "office", "training", "client_event"])
            amount = round(max(8, random.lognormvariate(4.1, 0.75)), 2)
            violation = int(amount > 750 or (category == "meals" and amount > 180) or random.random() < 0.025)
            rows.append({
                "claim_id": f"EXP-{i + 1:06d}",
                "submitted_date": rand_date(start, 420),
                "department": random.choice(["sales", "engineering", "marketing", "finance", "operations"]),
                "employee_level": random.choice(["IC", "manager", "director", "vp"]),
                "category": category,
                "amount": amount,
                "policy_violation": violation,
                "reimbursed_days": max(1, int(random.gauss(7 + violation * 5, 3))),
                "vendor": random.choice(["airline", "hotel", "saas", "restaurant", "conference", "office_depot"]),
            })

        elif spec.dataset_id == "accounts_receivable_aging":
            amount = round(random.lognormvariate(8.1, 0.8), 2)
            past_due = max(0, int(random.gauss(28, 35)))
            dispute = int(random.random() < (0.06 + min(past_due, 120) / 800))
            rows.append({
                "invoice_id": f"INV-{i + 1:06d}",
                "invoice_date": rand_date(date(2024, 8, 1), 520),
                "customer_segment": random.choice(["SMB", "mid_market", "enterprise", "public_sector"]),
                "region": random.choice(["NA", "EMEA", "APAC", "LATAM"]),
                "invoice_amount": amount,
                "days_past_due": past_due,
                "payment_status": "paid" if past_due < 7 and random.random() < 0.7 else random.choice(["open", "partial", "overdue"]),
                "collector": random.choice(["chen", "rivera", "patel", "kim", "nguyen"]),
                "dispute_flag": dispute,
            })

        elif spec.dataset_id == "hr_recruiting_pipeline":
            stage = random.choice(["applied", "screen", "onsite", "offer", "hired", "rejected"])
            score = round(clamp(random.gauss(3.6, 0.9), 1, 5), 1)
            offer = int(stage in ["offer", "hired"] and score > 3.2 and random.random() < 0.74)
            rows.append({
                "candidate_id": f"CAND-{i + 1:06d}",
                "applied_date": rand_date(start, 420),
                "role_family": random.choice(["engineering", "sales", "data", "product", "operations"]),
                "source": random.choice(["referral", "job_board", "agency", "linkedin", "campus"]),
                "stage": stage,
                "days_in_stage": max(1, int(random.gauss(8, 5) + (6 if stage == "onsite" else 0))),
                "interview_score": maybe_blank(score, 0.04),
                "offer_extended": offer,
                "offer_accepted": int(offer and random.random() < 0.68),
                "salary_expectation": int(random.gauss(135000, 42000)),
            })

        elif spec.dataset_id == "employee_attrition_pulse":
            engagement = round(clamp(random.gauss(72, 14), 15, 100), 1)
            overtime = max(0, round(random.gauss(9, 8), 1))
            risk = clamp((100 - engagement) / 100 + overtime / 70 + random.gauss(0, 0.08), 0, 1)
            rows.append({
                "employee_id": f"EMP-{random.randrange(10000, 99999)}",
                "survey_month": rand_month(date(2024, 1, 1), 24),
                "department": random.choice(["sales", "engineering", "support", "finance", "people"]),
                "tenure_months": max(1, int(random.gauss(38, 28))),
                "manager_rating": random.choice([1, 2, 3, 4, 5]),
                "engagement_score": maybe_blank(engagement, 0.05),
                "overtime_hours": overtime,
                "remote_days": random.randrange(0, 6),
                "attrition_risk": round(risk, 3),
                "left_company": int(random.random() < risk * 0.16),
            })

        elif spec.dataset_id == "product_usage_events":
            feature = random.choice(["dashboard", "export", "automation", "forecast", "alerts", "api"])
            events = max(0, int(random.gauss(42, 30) * (1.4 if feature in ["automation", "api"] else 1)))
            errors = max(0, int(random.gauss(1.2, 2.4) + (2 if feature == "api" else 0)))
            rows.append({
                "event_id": f"EVT-{i + 1:07d}",
                "event_date": rand_date(start, 420),
                "account_id": f"ACCT-{random.randrange(10000, 99999)}",
                "plan": random.choice(["free", "team", "business", "enterprise"]),
                "feature": feature,
                "cohort_month": rand_month(date(2024, 1, 1), 18),
                "events": events,
                "active_minutes": max(0, round(events * random.uniform(0.8, 4.2) - errors * 2, 1)),
                "errors": errors,
                "converted": int(random.random() < clamp(0.04 + events / 900 - errors / 200, 0.01, 0.42)),
            })

        elif spec.dataset_id == "mobile_app_ab_test":
            variant = random.choice(["control", "variant_a", "variant_b"])
            sessions = max(0, int(random.gauss(5.5, 3.2) + (0.8 if variant == "variant_b" else 0)))
            retention = int(random.random() < clamp(0.31 + sessions / 80 + (0.035 if variant == "variant_b" else 0), 0.05, 0.8))
            purchase_count = max(0, int(random.gauss(0.22 + sessions / 30, 0.55)))
            rows.append({
                "user_id": f"USR-{i + 1:07d}",
                "assign_date": rand_date(start, 300),
                "variant": variant,
                "country": random.choice(["US", "BR", "DE", "IN", "JP", "UK"]),
                "device": random.choice(["ios", "android"]),
                "sessions": sessions,
                "purchase_count": purchase_count,
                "revenue": round(purchase_count * random.uniform(6, 48), 2),
                "retention_d7": retention,
                "crash_count": max(0, int(random.gauss(0.22, 0.8) + (0.25 if variant == "variant_a" else 0))),
            })

        elif spec.dataset_id == "warehouse_inventory_movements":
            movement_type = random.choice(["inbound", "outbound", "adjustment", "return"])
            qty = int(random.gauss(220, 130))
            if movement_type == "outbound":
                qty = -abs(qty)
            rows.append({
                "movement_id": f"MOV-{i + 1:06d}",
                "date": rand_date(start, 420),
                "warehouse": random.choice(["W1", "W2", "W3", "W4", "W5"]),
                "sku_category": random.choice(["electronics", "apparel", "grocery", "spares", "home"]),
                "movement_type": movement_type,
                "quantity": qty,
                "unit_cost": round(random.uniform(4, 280), 2),
                "stockout_flag": int(movement_type == "outbound" and random.random() < 0.06),
                "shrinkage_flag": int(movement_type == "adjustment" and random.random() < 0.18),
            })

        elif spec.dataset_id == "energy_consumption_sites":
            reading_date = rand_date(start, 420)
            temp = round(random.gauss(18, 9), 1)
            occupancy = max(0, int(random.gauss(180, 90)))
            kwh = round(250 + occupancy * random.uniform(1.5, 4.5) + abs(temp - 20) * random.uniform(18, 42), 1)
            rows.append({
                "site_id": f"SITE-{random.randrange(100, 180)}",
                "reading_date": reading_date,
                "region": random.choice(["north", "south", "east", "west"]),
                "building_type": random.choice(["office", "warehouse", "retail", "clinic"]),
                "kwh": kwh,
                "occupancy": occupancy,
                "temperature_c": temp,
                "peak_demand_kw": round(kwh / random.uniform(8, 14), 1),
                "maintenance_flag": int(random.random() < 0.04),
            })

        elif spec.dataset_id == "banking_transactions_risk":
            channel = random.choice(["card_present", "card_not_present", "mobile", "wire", "atm"])
            amount = round(random.lognormvariate(4.4, 1.05), 2)
            risk = clamp(random.gauss(0.22, 0.18) + (0.2 if channel in ["wire", "card_not_present"] else 0) + amount / 12000, 0, 1)
            fraud = int(random.random() < risk * 0.08)
            rows.append({
                "transaction_id": f"TXN-{i + 1:08d}",
                "transaction_date": rand_date(start, 420),
                "customer_segment": random.choice(["mass", "affluent", "small_business", "private"]),
                "channel": channel,
                "transaction_amount": amount,
                "merchant_category": random.choice(["travel", "electronics", "grocery", "cash", "crypto", "services"]),
                "risk_score": round(risk, 3),
                "fraud_flag": fraud,
                "chargeback_flag": int(fraud or random.random() < risk * 0.04),
            })

        elif spec.dataset_id == "insurance_claims":
            policy = random.choice(["auto", "home", "health", "travel"])
            claim = round(random.lognormvariate(7.2, 0.95) * (1.6 if policy == "home" else 1), 2)
            suspect = int(random.random() < (0.03 + min(claim, 50_000) / 700_000))
            approved = round(claim * random.uniform(0.52, 0.98) * (0.75 if suspect else 1), 2)
            rows.append({
                "claim_id": f"CLM-{i + 1:06d}",
                "claim_date": rand_date(start, 420),
                "policy_type": policy,
                "region": random.choice(["north", "south", "east", "west"]),
                "claim_amount": claim,
                "approved_amount": approved,
                "days_to_close": max(1, int(random.gauss(18, 11) + suspect * 12)),
                "adjuster": random.choice(["austin", "blake", "casey", "drew", "ellis"]),
                "suspected_fraud": suspect,
                "customer_satisfaction": maybe_blank(round(clamp(random.gauss(4.0, 0.8), 1, 5), 1), 0.05),
            })

        elif spec.dataset_id == "healthcare_appointments":
            wait = max(0, int(random.gauss(9, 7)))
            no_show = int(random.random() < clamp(0.08 + wait / 140, 0.02, 0.34))
            rows.append({
                "appointment_id": f"APT-{i + 1:06d}",
                "appointment_date": rand_date(start, 420),
                "clinic": random.choice(["central", "northside", "lakeside", "west_end"]),
                "specialty": random.choice(["primary", "cardiology", "orthopedics", "dermatology", "pediatrics"]),
                "wait_days": wait,
                "no_show": no_show,
                "appointment_duration_min": max(5, int(random.gauss(28, 12))),
                "patient_age_band": random.choice(["0-17", "18-34", "35-49", "50-64", "65+"]),
                "payer_type": random.choice(["commercial", "medicare", "medicaid", "self_pay"]),
                "followup_required": int(random.random() < 0.32),
            })

        elif spec.dataset_id == "education_course_engagement":
            watch = max(0, int(random.gauss(110, 70)))
            lessons = max(0, int(watch / random.uniform(35, 60) + random.gauss(1, 1.5)))
            quiz = round(clamp(random.gauss(72, 15) + lessons * 1.5, 0, 100), 1)
            earned = int(lessons >= 6 and quiz >= 70 and random.random() < 0.58)
            rows.append({
                "learner_id": f"LRN-{random.randrange(100000, 999999)}",
                "week_start": rand_date(start, 420),
                "course": random.choice(["sql_basics", "python_analytics", "growth_marketing", "finance_101"]),
                "cohort": random.choice(["2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1"]),
                "lessons_completed": lessons,
                "quiz_score": maybe_blank(quiz, 0.04),
                "watch_minutes": watch,
                "forum_posts": max(0, int(random.gauss(1.3, 1.6))),
                "certificate_earned": earned,
                "churn_risk": round(clamp(0.55 - watch / 500 - lessons / 20 + random.gauss(0, 0.08), 0, 1), 3),
            })

        elif spec.dataset_id == "real_estate_leads":
            budget = int(random.gauss(720000, 260000))
            visits = max(0, int(random.gauss(1.7, 1.4) + budget / 900000))
            offer = int(visits > 0 and random.random() < clamp(0.16 + visits / 12, 0.03, 0.7))
            rows.append({
                "lead_id": f"LED-{i + 1:06d}",
                "created_date": rand_date(start, 420),
                "city": random.choice(["Austin", "Seattle", "Denver", "Miami", "Boston"]),
                "channel": random.choice(["portal", "referral", "paid_search", "walk_in", "agent_network"]),
                "property_type": random.choice(["condo", "single_family", "townhouse", "multi_family"]),
                "budget": max(120000, budget),
                "visits_scheduled": visits,
                "offer_made": offer,
                "closed_won": int(offer and random.random() < 0.38),
                "days_to_close": max(1, int(random.gauss(34, 18))) if offer else "",
            })

        elif spec.dataset_id == "restaurant_operations":
            daypart = random.choice(["breakfast", "lunch", "dinner", "late_night"])
            items = max(1, int(random.gauss(3.2, 1.4)))
            prep = max(2, round(random.gauss(12, 5) + (5 if daypart == "dinner" else 0), 1))
            rating = round(clamp(random.gauss(4.2, 0.6) - prep / 70, 1, 5), 1)
            rows.append({
                "order_id": f"RST-{i + 1:07d}",
                "order_date": rand_date(start, 420),
                "store": random.choice(["S01", "S02", "S03", "S04", "S05", "S06"]),
                "daypart": daypart,
                "channel": random.choice(["dine_in", "takeaway", "delivery", "kiosk"]),
                "items": items,
                "ticket_size": round(items * random.uniform(7.5, 18.5), 2),
                "prep_minutes": prep,
                "refund_flag": int(random.random() < clamp(0.02 + prep / 500, 0.01, 0.16)),
                "rating": maybe_blank(rating, 0.04),
                "labor_hours": round(random.uniform(4.5, 13.5), 1),
            })

        else:
            raise ValueError(f"Unknown dataset_id: {spec.dataset_id}")

    return rows


def task_for(spec: DatasetSpec, index: int) -> dict[str, Any]:
    dataset_path = f"{AGENT_RELATIVE_DATA_DIR}/{spec.file_name}"
    dim1 = spec.dimensions[0]
    dim2 = spec.dimensions[1] if len(spec.dimensions) > 1 else spec.dimensions[0]
    metric1 = spec.metrics[0]
    metric2 = spec.metrics[1] if len(spec.metrics) > 1 else spec.metrics[0]

    templates = [
        {
            "analysis_type": "data_quality_profile",
            "difficulty": "easy",
            "expected_tools": ["read_file", "python_analysis"],
            "user_request": (
                f"请先检查 {dataset_path} 的字段、行数、缺失值、异常值和可用于分析的关键指标，"
                f"判断它是否适合做{spec.description}分析。"
            ),
            "expected_workflow": [
                "read_file preview first rows",
                "python_analysis compute shape, dtypes, missing values, numeric summary",
                "summarize data readiness and caveats",
            ],
            "oracle_spec": {
                "checks": ["row_count", "columns", "missing_values", "numeric_summary"],
                "acceptance": "Report concrete row count, key columns, missingness, and at least two caveats.",
            },
        },
        {
            "analysis_type": "grouped_kpi",
            "difficulty": "medium",
            "expected_tools": ["read_file", "nl_query", "python_analysis"],
            "user_request": (
                f"基于 {dataset_path}，按 {dim1} 汇总 {metric1} 和 {metric2}，"
                f"找出表现最好和最差的分组，并给出业务解释。"
            ),
            "expected_workflow": [
                "read_file inspect schema",
                "nl_query draft grouped aggregation",
                "python_analysis execute robust aggregation and ranking",
                "compare top and bottom groups with business interpretation",
            ],
            "oracle_spec": {
                "group_by": [dim1],
                "metrics": [metric1, metric2],
                "acceptance": "Return ranked groups with exact aggregated values and interpretation.",
            },
        },
        {
            "analysis_type": "time_trend",
            "difficulty": "medium",
            "expected_tools": ["read_file", "python_analysis", "visualization"],
            "user_request": (
                f"分析 {dataset_path} 中 {metric1} 随 {spec.date_column} 的变化趋势，"
                f"判断最近阶段是否变好或变差，并指出可能的季节性或异常月份。"
            ),
            "expected_workflow": [
                "read_file inspect date and metric columns",
                "python_analysis parse dates and aggregate by month or week",
                "visualization generate line chart code",
                "python_analysis optionally execute chart code and report trend",
            ],
            "oracle_spec": {
                "date_column": spec.date_column,
                "metric": metric1,
                "acceptance": "Mention trend direction, latest period comparison, and any visible anomaly.",
            },
        },
        {
            "analysis_type": "risk_or_anomaly",
            "difficulty": "hard",
            "expected_tools": ["read_file", "python_analysis"],
            "user_request": (
                f"在 {dataset_path} 中找出 {metric1} 或 {metric2} 的异常样本/高风险分组，"
                f"请说明筛选规则、异常集中在哪里，以及下一步应追查什么。"
            ),
            "expected_workflow": [
                "read_file inspect available columns",
                "python_analysis compute quantiles or rates by dimension",
                "identify outlier rows and concentrated groups",
                "explain rule limitations and next investigation steps",
            ],
            "oracle_spec": {
                "metrics": [metric1, metric2],
                "dimensions": [dim1, dim2],
                "acceptance": "Use an explicit threshold or quantile rule and return concrete suspicious groups or rows.",
            },
        },
        {
            "analysis_type": "business_recommendation",
            "difficulty": "hard",
            "expected_tools": ["read_file", "python_analysis", "visualization"],
            "user_request": (
                f"请把 {dataset_path} 当成一次真实业务复盘材料：围绕“{spec.business_questions[index % len(spec.business_questions)]}”"
                f"做分析，输出一个可执行建议，并生成适合复盘会展示的图表方案。"
            ),
            "expected_workflow": [
                "read_file inspect business fields",
                "python_analysis compute supporting evidence",
                "visualization create chart code for the main comparison",
                "final answer with recommendation, confidence, and limitations",
            ],
            "oracle_spec": {
                "business_question": spec.business_questions[index % len(spec.business_questions)],
                "acceptance": "Tie recommendation to computed evidence and include a chart choice with x/y fields.",
            },
        },
    ]
    task = templates[index]
    return {
        "task_id": f"W1-{spec.dataset_id}-{index + 1:02d}",
        "dataset_id": spec.dataset_id,
        "dataset_path": dataset_path,
        "path_policy": {
            "dataset_path_base": "DataAnalysisAgent project root",
            "read_file": "Use dataset_path directly when the agent process runs from the project root.",
            "python_analysis": (
                "Resolve dataset_path to an allowed absolute path before emitting pd.read_csv code, "
                "because PythonAnalysisTool executes code from an isolated temporary cwd."
            ),
        },
        "domain": spec.domain,
        "business_context": spec.description,
        "seed_source": "synthetic_week1_business_seed",
        "requires_tool_calling": True,
        "target_agent": "DataAnalysisAgent",
        "tool_contract": "Anthropic Messages API compatible tool-use trace",
        "data_quality_tags": spec.quality_issues,
        **task,
    }


def build_manifest() -> list[dict[str, Any]]:
    manifest = []
    for spec in SPECS:
        rows = generate_rows(spec)
        write_csv(DATA_DIR / spec.file_name, rows)
        manifest.append({
            "dataset_id": spec.dataset_id,
            "file_name": spec.file_name,
            "relative_path": f"data/{spec.file_name}",
            "agent_relative_path": f"{AGENT_RELATIVE_DATA_DIR}/{spec.file_name}",
            "domain": spec.domain,
            "description": spec.description,
            "row_count": spec.row_count,
            "columns": list(rows[0].keys()),
            "date_column": spec.date_column,
            "dimensions": spec.dimensions,
            "metrics": spec.metrics,
            "quality_issues": spec.quality_issues,
            "business_questions": spec.business_questions,
            "synthetic": True,
            "contains_personal_data": False,
        })
    return manifest


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_seed_tasks(path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for spec in SPECS:
        for index in range(5):
            tasks.append(task_for(spec, index))
    with path.open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
    return tasks


def write_readme(manifest: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> None:
    domains = sorted({item["domain"] for item in manifest})
    lines = [
        "# Week 1 Seed Assets",
        "",
        "This directory contains the first-week seed assets for training a small",
        "tool-using data-analysis model that works with `DataAnalysisAgent`.",
        "",
        "## Contents",
        "",
        f"- `data/`: {len(manifest)} executable synthetic CSV datasets.",
        "- `dataset_manifest.json`: schemas, domains, metrics, and quality notes.",
        f"- `seed_tasks.jsonl`: {len(tasks)} business-analysis seed tasks, 5 per dataset.",
        "- `scripts/generate_assets.py`: deterministic asset generator.",
        "- `scripts/validate_assets.py`: lightweight validation checks.",
        "- `scripts/smoke_tool_execution.py`: executes one dataset with real tools.",
        "",
        "## Design Notes",
        "",
        "- The seed unit is a business analysis task, not a plain Q&A prompt.",
        "- Each task names expected DataAnalysisAgent tools and an oracle-style acceptance rule.",
        "- `dataset_path` is relative to the `DataAnalysisAgent` project root. For",
        "  `python_analysis`, resolve it to an allowed absolute path before emitting",
        "  `pd.read_csv(...)` code because the tool executes from an isolated temp cwd.",
        "- The CSVs intentionally include realistic analysis issues: missing values, outliers,",
        "  skewed metrics, seasonality, flags, and segment effects.",
        "- The assets are synthetic and contain no real personal data.",
        "",
        "## Coverage",
        "",
        f"- Domains: {', '.join(domains)}.",
        "- Task types: data quality profile, grouped KPI, time trend, anomaly/risk,",
        "  and business recommendation with visualization.",
        "",
        "## Regenerate",
        "",
        "```bash",
        "cd DataAnalysisAgent",
        "python examples/training_data/week1_seed_assets/scripts/generate_assets.py",
        "python examples/training_data/week1_seed_assets/scripts/validate_assets.py",
        "python examples/training_data/week1_seed_assets/scripts/smoke_tool_execution.py",
        "```",
        "",
    ]
    (ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest()
    tasks = write_seed_tasks(ROOT / "seed_tasks.jsonl")
    write_json(ROOT / "dataset_manifest.json", manifest)
    write_readme(manifest, tasks)
    print(
        json.dumps(
            {
                "datasets": len(manifest),
                "seed_tasks": len(tasks),
                "output_dir": str(ROOT),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
