from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from .models import Quote

LABELS = {"product": "采购品名", "quantity": "采购数量", "budget": "含运费总预算", "deadline": "最晚到货日"}


class InputError(ValueError):
    pass


def merge_needs(previous, patch):
    """只合并本轮明确给出的条件，统一校验模型和规则的输出。"""
    if not isinstance(patch, dict) or set(patch) - LABELS.keys():
        raise InputError("采购条件格式有误，请重新描述。")
    needs = dict(previous)
    for key, value in patch.items():
        if value is None:
            continue
        try:
            if key == "product":
                value = str(value).strip()
                if not 0 < len(value) <= 80:
                    raise ValueError()
            elif key == "quantity":
                if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 1000000:
                    raise ValueError()
                value = int(value)
            elif key == "budget":
                amount = Decimal(str(value))
                if not amount.is_finite() or not 0 < amount <= Decimal("999999999999") or amount != amount.quantize(Decimal(".01")):
                    raise ValueError()
                value = format(amount, ".2f")
            elif key == "deadline":
                due = date.fromisoformat(str(value))
                if due < timezone.localdate():
                    raise ValueError()
                value = due.isoformat()
        except (ValueError, InvalidOperation, OverflowError):
            raise InputError(f"请检查{LABELS[key]}，金额和数量须为正数，到货日须为今天或之后。")
        needs[key] = value
    return needs


def find_quotes(product):
    """仅查询有效的同品名报价，避免将不同规格商品混算。"""
    return list(Quote.objects.filter(active=True, product__iexact=product))


def quote_data(quote, today):
    return {"code": quote.code, "supplier": quote.supplier, "product": quote.product,
            "unit_price": str(quote.unit_price), "freight": str(quote.freight),
            "stock": quote.stock, "lead_days": quote.lead_days,
            "arrival": (today + timedelta(days=quote.lead_days)).isoformat()}


def evaluate_quote(quote, needs, today):
    """整单运费只计一次，库存、交期、含运总价分别核算，边界值可行。"""
    row = quote_data(quote, today)
    total = quote.unit_price * needs["quantity"] + quote.freight
    reasons = []
    if quote.stock < needs["quantity"]:
        reasons.append(f"可供 {quote.stock} 台，缺 {needs['quantity'] - quote.stock} 台")
    if row["arrival"] > needs["deadline"]:
        reasons.append(f"预计 {row['arrival']} 到货，超过交期")
    if total > Decimal(needs["budget"]):
        reasons.append(f"超预算 ¥{total - Decimal(needs['budget']):,.2f}")
    return {**row, "total": str(total), "reasons": reasons, "feasible": not reasons}


def result_for(needs, rows):
    """按可行报价的含运费总价排序，同价时以报价编号稳定选取。"""
    missing = [label for key, label in LABELS.items() if key not in needs]
    recommendation = None
    if missing:
        message = "请补充" + "、".join(missing) + "，我会沿用已提供的采购条件。"
    elif not rows:
        message = f"报价库暂无「{needs['product']}」的有效报价。请检查品名，或在管理后台补充报价。"
    else:
        valid = sorted((r for r in rows if r["feasible"]), key=lambda r: (Decimal(r["total"]), r["code"]))
        if valid:
            best = valid[0]
            recommendation = best["code"]
            message = f"推荐{best['supplier']}（{best['code']}）。可按期整单交付，含运费总价 ¥{Decimal(best['total']):,.2f}，是可行方案中总价最低的报价。"
        else:
            message = "当前没有同时满足数量、预算和交期的报价。愿意调整预算、采购数量或最晚到货日，再比较一次吗？"
    return {"message": message, "rows": rows, "recommended": recommendation}
