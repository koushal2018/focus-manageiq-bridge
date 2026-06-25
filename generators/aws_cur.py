"""Generate a synthetic AWS Cost & Usage Report (CUR2-style) CSV.

Column names mirror the real CUR2 schema's slash-namespaced layout
(`lineItem/UsageAccountId`, `product/ProductName`, etc.) so the normalizer
exercises the same parsing the ENBD team will face on a real CUR export.

Messiness injected per SPEC s3.1:
  - A row with **blank ProductName** (forces ServiceCategory mapping to handle
    the null --- GOTCHA F-2 territory).
  - **Duplicate** rows (same lineItem/LineItemId emitted twice).
  - **Late-arriving** rows: a few rows have a timestamp earlier than rows
    emitted before them in the file.
  - **Bedrock per-model line items** with input/output token splits (covers
    requirement #1 from SPEC).
  - **Mixed currencies**: BillingCurrencyCode=AED with PricingCurrencyCode=USD.

Output file: out/generators/aws_cur.csv
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import sys

# Allow running as a script from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common

# CUR2-style columns. Not the full ~100-column real CUR --- a sufficient
# subset to exercise the FOCUS mapping.
CUR_COLUMNS = [
    "identity/LineItemId",
    "identity/TimeInterval",
    "bill/PayerAccountId",
    "bill/BillingPeriodStartDate",
    "bill/BillingPeriodEndDate",
    "lineItem/UsageAccountId",
    "lineItem/LineItemType",
    "lineItem/UsageStartDate",
    "lineItem/UsageEndDate",
    "lineItem/ProductCode",
    "lineItem/UsageType",
    "lineItem/Operation",
    "lineItem/AvailabilityZone",
    "lineItem/ResourceId",
    "lineItem/UsageAmount",
    "lineItem/UnblendedCost",
    "lineItem/CurrencyCode",
    "product/ProductName",
    "product/region",
    "product/instanceType",
    "pricing/unit",
    "pricing/publicOnDemandCost",
    "resourceTags/user:app",
    "resourceTags/user:env",
    "resourceTags/user:cost-center",
]


def _row(
    line_item_id: str,
    interval_start: dt.datetime,
    interval_end: dt.datetime,
    *,
    product_code: str,
    product_name: str,
    usage_type: str,
    operation: str,
    region: str,
    az: str = "",
    instance_type: str = "",
    resource_id: str = "",
    usage_amount: float,
    unblended_cost_usd: float,
    pricing_unit: str,
    tags: dict[str, str] | None = None,
    line_item_type: str = "Usage",
) -> dict[str, str]:
    bill_start = dt.datetime(interval_start.year, interval_start.month, 1, tzinfo=dt.timezone.utc)
    # billing period end: first of next month
    nm = bill_start.month % 12 + 1
    ny = bill_start.year + (1 if bill_start.month == 12 else 0)
    bill_end = dt.datetime(ny, nm, 1, tzinfo=dt.timezone.utc)
    tags = tags or {}
    return {
        "identity/LineItemId": line_item_id,
        "identity/TimeInterval": f"{interval_start.isoformat()}/{interval_end.isoformat()}",
        "bill/PayerAccountId": common.FAKE_AWS_ACCOUNT_ID,
        "bill/BillingPeriodStartDate": bill_start.isoformat(),
        "bill/BillingPeriodEndDate": bill_end.isoformat(),
        "lineItem/UsageAccountId": common.FAKE_AWS_ACCOUNT_ID,
        "lineItem/LineItemType": line_item_type,
        "lineItem/UsageStartDate": interval_start.isoformat(),
        "lineItem/UsageEndDate": interval_end.isoformat(),
        "lineItem/ProductCode": product_code,
        "lineItem/UsageType": usage_type,
        "lineItem/Operation": operation,
        "lineItem/AvailabilityZone": az,
        "lineItem/ResourceId": resource_id,
        "lineItem/UsageAmount": f"{usage_amount:.6f}",
        "lineItem/UnblendedCost": f"{unblended_cost_usd:.6f}",
        # NB: AWS itself bills the account in a single configured currency.
        # We declare USD on the line item; the FOCUS normalizer later converts
        # to BillingCurrency=AED. Mismatch is the SPEC s3.1 mixed-currency
        # messiness recipe.
        "lineItem/CurrencyCode": "USD",
        "product/ProductName": product_name,
        "product/region": region,
        "product/instanceType": instance_type,
        "pricing/unit": pricing_unit,
        "pricing/publicOnDemandCost": f"{unblended_cost_usd:.6f}",
        "resourceTags/user:app": tags.get("app", ""),
        "resourceTags/user:env": tags.get("env", ""),
        "resourceTags/user:cost-center": tags.get("cost-center", ""),
    }


def generate(days: int = 3) -> list[dict[str, str]]:
    """Build the row list. Pure function --- writes nothing."""
    rng = common.make_rng()
    rows: list[dict[str, str]] = []
    line_no = 0

    def next_id() -> str:
        nonlocal line_no
        line_no += 1
        return f"li-demo-{line_no:08d}"

    aws_workloads = [w for w in common.WORKLOADS if w.aws_instance_id]

    # --- EC2 usage hourly for each AWS workload ---
    for start, end in common.hourly_periods(days):
        for wl in aws_workloads:
            region = common.AWS_REGIONS[0]  # me-central-1
            az = f"{region}a"
            # cost per hour, vaguely instance-size-shaped, with jitter
            base_hourly = 0.05 * wl.cpu_cores + 0.001 * (wl.memory_mb / 1024)
            jitter = rng.uniform(-0.005, 0.005)
            cost = max(0.0, base_hourly + jitter)
            rows.append(
                _row(
                    next_id(),
                    start,
                    end,
                    product_code="AmazonEC2",
                    product_name="Amazon Elastic Compute Cloud",
                    usage_type=f"BoxUsage:demo.{wl.cpu_cores}xlarge",
                    operation="RunInstances",
                    region=region,
                    az=az,
                    instance_type=f"demo.{wl.cpu_cores}xlarge",
                    resource_id=wl.aws_instance_id or "",
                    usage_amount=1.0,
                    unblended_cost_usd=cost,
                    pricing_unit="Hrs",
                    tags=wl.tags,
                )
            )

    # --- Bedrock per-model line items (SPEC s3.1 requirement #1) ---
    # Two days of "AI traffic" --- one row per model per day, split into
    # input/output usage types.
    bedrock_start = dt.datetime(2026, 6, 1, 0, 0, tzinfo=dt.timezone.utc)
    for d in range(min(days, 2)):
        day_start = bedrock_start + dt.timedelta(days=d)
        day_end = day_start + dt.timedelta(days=1)
        for model_id, in_price, out_price in common.BEDROCK_MODELS:
            input_tokens = rng.randint(50_000, 200_000)
            output_tokens = rng.randint(10_000, 50_000)
            # Input tokens row
            rows.append(
                _row(
                    next_id(),
                    day_start,
                    day_end,
                    product_code="AmazonBedrock",
                    product_name="Amazon Bedrock",
                    usage_type=f"InputTokens:{model_id}",
                    operation="InvokeModel",
                    region="us-east-1",  # gotcha: Bedrock for me-central-1 customers
                                          # often routes via Global inference profile;
                                          # CUR records the inference region.
                    resource_id=f"arn:aws:bedrock:us-east-1::foundation-model/{model_id}",
                    usage_amount=float(input_tokens),
                    unblended_cost_usd=(input_tokens / 1000.0) * in_price,
                    pricing_unit="Tokens",
                    tags={"app": "ai-assist", "env": "prod"},
                )
            )
            # Output tokens row
            rows.append(
                _row(
                    next_id(),
                    day_start,
                    day_end,
                    product_code="AmazonBedrock",
                    product_name="Amazon Bedrock",
                    usage_type=f"OutputTokens:{model_id}",
                    operation="InvokeModel",
                    region="us-east-1",
                    resource_id=f"arn:aws:bedrock:us-east-1::foundation-model/{model_id}",
                    usage_amount=float(output_tokens),
                    unblended_cost_usd=(output_tokens / 1000.0) * out_price,
                    pricing_unit="Tokens",
                    tags={"app": "ai-assist", "env": "prod"},
                )
            )

    # --- Messiness injections (each one tied to a SPEC s3.1 recipe) ---

    # 1) ProductName blank --- the normalizer must NOT silently emit this row
    #    as ServiceCategory="" --- it must catch and log it. (GOTCHA F-2.)
    blanky_start = dt.datetime(2026, 6, 1, 5, 0, tzinfo=dt.timezone.utc)
    rows.append(
        _row(
            next_id(),
            blanky_start,
            blanky_start + dt.timedelta(hours=1),
            product_code="UNKNOWN_PRODUCT",
            product_name="",  # <-- blank on purpose
            usage_type="Mystery:Charge",
            operation="",
            region=common.AWS_REGIONS[0],
            resource_id="arn:aws:demo:???",
            usage_amount=1.0,
            unblended_cost_usd=0.42,
            pricing_unit="Each",
        )
    )

    # 2) Duplicate row (same line_item_id used twice) --- forces idempotent load.
    if rows:
        dup_template = rows[0]
        rows.append(dict(dup_template))

    # 3) Late-arriving rows: a row dated yesterday, emitted at the end of the
    #    file. Real CUR can do this when AWS reissues a period; the loader
    #    must handle out-of-order arrival.
    late_start = dt.datetime(2026, 5, 31, 22, 0, tzinfo=dt.timezone.utc)
    rows.append(
        _row(
            next_id(),
            late_start,
            late_start + dt.timedelta(hours=1),
            product_code="AmazonS3",
            product_name="Amazon Simple Storage Service",
            usage_type="StorageObjectCount",
            operation="StandardStorage",
            region=common.AWS_REGIONS[0],
            resource_id=f"arn:aws:s3:::{common.DEMO_PREFIX.lower()}retention-bucket",
            usage_amount=1024.0,
            unblended_cost_usd=0.024,
            pricing_unit="GB-Mo",
            line_item_type="Usage",
        )
    )

    return rows


def write_csv(path: str | None = None) -> str:
    rows = generate(days=3)
    out = path or os.path.join(common.out_dir(), "aws_cur.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CUR_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


if __name__ == "__main__":
    p = write_csv()
    print(f"wrote {p}")
